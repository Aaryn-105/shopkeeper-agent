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
from app.agent.graph import get_graph
from app.agent.state import AgentState
from app.clients.cache_client import QueryCache
from app.clients.embedding_client import EmbeddingClient
from app.clients.faiss_client import FAISSStore
from app.clients.fts5_client import FTS5Store
from app.clients.llm_client import LLMClient
from app.clients.mysql_client import MetadataClient, MySQLClient, MySQLValidator
from app.core.config import cfg
from app.core.logger import logger
from app.core.metrics import get_metrics
from app.core.request_context import get_request_id, new_request_id


router = APIRouter(prefix="/api", tags=["ask"])


class AskRequest(BaseModel):
    query: str = Field(..., description="Natural-language question, in Chinese or English")
    session_id: str | None = Field(default=None, description="Optional client session id")


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
    return {"event": event_type, "data": json.dumps(data, ensure_ascii=False)}


@router.post("/ask")
async def ask(req: AskRequest):
    query = _validate_query(req.query)
    request_id = (
        get_request_id()
        if get_request_id() not in {"", "-"}
        else new_request_id()
    )
    metrics = get_metrics()

    async def event_gen():
        # exact-match cache lookup
        cached = _shared_cache.get_exact(query)
        if cached is None:
            _, cached = _shared_cache.get_similar(query)
        if cached is not None:
            metrics.record_cache(hit=True)
            yield _sse("progress", {"node": "cache", "status": "ok", "message": "命中缓存"})
            yield _sse("result", cached["result"])
            yield _sse("done", {
                "request_id": request_id,
                "duration_ms": 0,
                "cache_hit": True,
            })
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

        graph = get_graph()
        initial: AgentState = {
            "query": query,
            "request_id": request_id,
            "node_history": [],
            "validate_attempts": 0,
            "started_at": time.time(),
        }

        yield _sse("progress", {"node": "start", "status": "running", "message": "开始问数"})

        final_state: dict[str, Any] = dict(initial)
        sql_emitted = False
        corrected_emitted = False
        t_total = time.perf_counter()
        try:
            async for event in graph.astream(
                initial, config={"configurable": {"runtime": runtime}}
            ):
                for node_name, partial in event.items():
                    if not isinstance(partial, dict):
                        continue
                    final_state.update(partial)
                    yield _sse("progress", {
                        "node": node_name,
                        "status": "running",
                        "message": f"执行节点 {node_name}",
                    })
                    if node_name == "generate_sql" and not sql_emitted and partial.get("sql"):
                        yield _sse("sql_generated", {"sql": partial["sql"]})
                        sql_emitted = True
                    if node_name == "correct_sql" and not corrected_emitted and partial.get("sql"):
                        yield _sse("sql_corrected", {"sql": partial["sql"]})
                        corrected_emitted = True
                    if node_name == "run_sql":
                        result = partial.get("result") or {}
                        err = partial.get("error")
                        if err:
                            yield _sse("error", {"code": "E001", "message": err})
                        else:
                            yield _sse("result", {
                                "columns": result.get("columns", []),
                                "rows": result.get("rows", []),
                                "row_count": result.get("row_count", 0),
                                "truncated": result.get("truncated", False),
                            })
        except asyncio.CancelledError:
            yield _sse("error", {"code": "E002", "message": "client disconnected"})
            raise
        except Exception as e:
            logger.bind(request_id=request_id).exception("graph failed")
            yield _sse("error", {"code": "E001", "message": f"{type(e).__name__}: {e}"})
        finally:
            await mysql_dw.aclose()

        if final_state.get("result") and not final_state.get("error"):
            _shared_cache.put(query, {"result": final_state["result"]})

        duration_ms = int((time.perf_counter() - t_total) * 1000)
        yield _sse("done", {
            "request_id": request_id,
            "duration_ms": duration_ms,
            "cache_hit": False,
        })

    return EventSourceResponse(event_gen(), ping=15)