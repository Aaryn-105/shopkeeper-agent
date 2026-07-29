"""POST /api/ask - SSE endpoint driving the 12-node LangGraph workflow."""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from app.agent.context import AgentRuntime
from app.clients.cache_client import QueryCache
from app.clients.embedding_client import EmbeddingClient
from app.clients.faiss_client import FAISSStore
from app.clients.fts5_client import FTS5Store
from app.clients.history_client import HistoryWriter
from app.clients.llm_client import LLMClient
from app.clients.mysql_client import MetadataClient, MySQLClient, MySQLValidator
from app.core.config import cfg
from app.core.logger import logger
from app.core.metrics import get_metrics
from app.core.request_context import get_request_id, new_request_id
from app.services.ask_service import build_default_service

router = APIRouter(prefix="/api", tags=["ask"])


class AskRequest(BaseModel):
    query: str = Field(
        ..., description="Natural-language question, in Chinese or English"
    )
    session_id: str | None = Field(
        default=None, description="Optional client session id"
    )


# Shared cache so warm hits survive across requests.
_shared_cache: QueryCache = QueryCache()


def _validate_query(q: str) -> str:
    q = (q or "").strip()
    if not q:
        raise HTTPException(status_code=400, detail="query must not be empty")
    if len(q) > int(cfg.ask.max_query_length):
        raise HTTPException(
            status_code=400,
            detail=f"query too long: max {cfg.ask.max_query_length} chars",
        )
    return q


def _sse(event_type: str, data: dict[str, Any]) -> dict[str, Any]:
    return {
        "event": event_type,
        "data": json.dumps(data, ensure_ascii=False, default=str),
    }


def _runtime_event_to_sse(event: dict[str, Any]) -> dict[str, Any] | None:
    """Translate direct-driver node events to the public SSE protocol."""
    event_type = str(event.get("type") or "")
    if event_type == "done":
        return None
    if event_type == "validate_sql":
        ok = bool(event.get("ok"))
        return _sse(
            "progress",
            {
                "node": "validate_sql",
                "status": "ok" if ok else "error",
                "message": "SQL 校验通过" if ok else "SQL 校验失败",
                "request_id": event.get("request_id"),
            },
        )
    if event_type not in {
        "progress",
        "sql_generated",
        "sql_corrected",
        "result",
        "error",
    }:
        return None
    return _sse(
        event_type,
        {key: value for key, value in event.items() if key != "type"},
    )


@router.post("/ask")
async def ask(req: AskRequest):
    query = _validate_query(req.query)
    request_id = (
        get_request_id() if get_request_id() not in {"", "-"} else new_request_id()
    )
    metrics = get_metrics()
    session_id = req.session_id

    async def event_gen():
        # exact-match cache lookup
        cached = _shared_cache.get_exact(query)
        if cached is None:
            _, cached = _shared_cache.get_similar(query)
        if cached is not None:
            metrics.record_cache(hit=True)
            yield _sse(
                "progress", {"node": "cache", "status": "ok", "message": "命中缓存"}
            )
            cached_result = dict(cached.get("result") or {})
            cached_result["cache_hit"] = True
            if cached.get("sql"):
                yield _sse("sql_generated", {"sql": cached["sql"], "cache_hit": True})
            yield _sse("result", cached_result)
            HistoryWriter.record(
                request_id=request_id,
                session_id=session_id,
                query=query,
                sql_text=cached.get("sql") or None,
                status="cache_hit",
                error_message=None,
                duration_ms=0,
                row_count=int(cached_result.get("row_count", 0) or 0),
                sql_corrected=False,
            )
            yield _sse(
                "done",
                {
                    "request_id": request_id,
                    "duration_ms": 0,
                    "cache_hit": True,
                    "explanation": cached.get("explanation"),
                },
            )
            return
        metrics.record_cache(hit=False)

        # initialize runtime + clients
        llm = LLMClient()
        embedding = EmbeddingClient()
        faiss = FAISSStore()
        fts5 = FTS5Store()
        mysql_dw = MySQLClient()
        validator = MySQLValidator()

        runtime = AgentRuntime(
            request_id=request_id,
            metrics=metrics,
            llm=llm,
            embedding=embedding,
            faiss=faiss,
            fts5=fts5,
            mysql_dw=mysql_dw,
            cache=_shared_cache,
        )
        runtime.validator = validator  # type: ignore[attr-defined]
        runtime.metadata = MetadataClient()  # type: ignore[attr-defined]

        service = build_default_service()

        yield _sse(
            "progress", {"node": "start", "status": "running", "message": "开始问数"}
        )

        final_state: dict[str, Any] = {}
        pending_index = 0
        workflow_error: str | None = None
        t_total = time.perf_counter()
        workflow_task = asyncio.create_task(service.run_question(query, runtime))
        try:
            while not workflow_task.done():
                while pending_index < len(runtime.pending_events):
                    event = runtime.pending_events[pending_index]
                    pending_index += 1
                    wire_event = _runtime_event_to_sse(event)
                    if wire_event is not None:
                        yield wire_event
                if not workflow_task.done():
                    await asyncio.sleep(0.01)
            final_state = await workflow_task
            while pending_index < len(runtime.pending_events):
                event = runtime.pending_events[pending_index]
                pending_index += 1
                wire_event = _runtime_event_to_sse(event)
                if wire_event is not None:
                    yield wire_event
        except asyncio.CancelledError:
            workflow_task.cancel()
            try:
                await workflow_task
            except asyncio.CancelledError:
                pass
            raise
        except Exception as e:
            workflow_error = f"{type(e).__name__}: {e}"
            final_state["error"] = workflow_error
            logger.bind(request_id=request_id).exception("direct workflow failed")
        finally:
            await mysql_dw.aclose()

        if workflow_error:
            yield _sse(
                "error",
                {
                    "code": "E001",
                    "message": workflow_error,
                },
            )

        if final_state.get("result") and not final_state.get("error"):
            _shared_cache.put(
                query,
                {
                    "result": final_state["result"],
                    "sql": final_state.get("sql"),
                    "explanation": final_state.get("explanation"),
                },
            )

        duration_ms = int((time.perf_counter() - t_total) * 1000)
        final_error = final_state.get("error")
        final_result = final_state.get("result") or {}
        if final_error and not workflow_error:
            yield _sse("error", {"code": "E001", "message": str(final_error)})

        if final_error:
            hist_status = "error"
        elif final_result:
            hist_status = "ok"
        else:
            hist_status = "error"
        HistoryWriter.record(
            request_id=request_id,
            session_id=session_id,
            query=query,
            sql_text=final_state.get("sql") or None,
            status=hist_status,
            error_message=final_error,
            duration_ms=duration_ms,
            row_count=int(final_result.get("row_count", 0) or 0),
            sql_corrected=bool(final_state.get("sql_corrected", False)),
        )
        yield _sse(
            "done",
            {
                "request_id": request_id,
                "duration_ms": duration_ms,
                "cache_hit": False,
                "sql": final_state.get("sql"),
                "explanation": final_state.get("explanation"),
            },
        )

    return EventSourceResponse(event_gen(), ping=15)
