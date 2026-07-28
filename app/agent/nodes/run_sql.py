"""Node: run_sql (4.2.12 / V1.0 phase 6.12).

V1.0 phase 6.12 spec:
  - result_cache_key = sha256(state.sql)
  - 命中 -> state.execution_result = cached.result_json, cache_hit_result=True
  - 未命中执行并写入 query_cache (TTL=3600)
  - stream_writer({"type":"result","columns":[...],"rows":[...],"row_count":N,"request_id":...})
  - 生成 state.explanation(**不走缓存**)并 stream_writer({"type":"done","request_id":..., "duration_ms":...})

V1.0 6.12 cache key intentionally differs from generate_sql cache key so the two
caches are independent: SQL 文本相同 -> 复用结果; 同一 (query,table,metric) -> 复用 SQL。
"""
from __future__ import annotations
import hashlib
import json
import time
from typing import Any, Optional

from langchain_core.runnables import RunnableConfig

from app.agent.nodes._helpers import get_runtime, history_append, log_node, now_ms
from app.agent.state import AgentState
from app.core.metrics import LLMCallStat


# V1.0 phase 6.12: result cache TTL (same as SQL cache).
RESULT_CACHE_TTL_SECONDS: int = 3600


def _stream_writer(runtime, event: dict[str, Any]) -> None:
    """Push an event into runtime.pending_events (best-effort, no-op for bare-stub runtime)."""
    if runtime is None:
        return
    pending = getattr(runtime, "pending_events", None)
    if isinstance(pending, list):
        pending.append(event)


def make_result_cache_key(sql: str) -> str:
    """V1.0 phase 6.12: sha256(state.sql)."""
    return hashlib.sha256((sql or "").encode("utf-8")).hexdigest()


async def _execute_sql(sql: str, runtime: Any) -> tuple[dict[str, Any], Optional[str]]:
    """Run SQL against the readonly DW connection; return (result, error)."""
    if runtime is None or getattr(runtime, "mysql_dw", None) is None:
        # No DB connection: synthesise a tiny placeholder result so the rest of
        # the workflow can complete (explanation + done event still emit).
        return (
            {"columns": ["placeholder"], "rows": [["no_mysql_dw_attached"]],
             "row_count": 1, "truncated": False, "note": "synthetic"},
            None,
        )
    try:
        result = await runtime.mysql_dw.execute_readonly(sql)
        # Normalise to dict shape (mysql_dw may return dict or DataFrame).
        if not isinstance(result, dict):
            result = {"columns": [], "rows": [], "row_count": 0,
                      "truncated": False, "raw": result}
        result.setdefault("columns", [])
        result.setdefault("rows", [])
        result.setdefault("row_count", len(result["rows"]))
        result.setdefault("truncated", False)
        return result, None
    except Exception as e:  # noqa: BLE001
        return ({"columns": [], "rows": [], "row_count": 0, "truncated": False},
                f"{type(e).__name__}: {str(e)[:200]}")


def _load_prompt_template() -> str:
    """Return the explanation prompt; fall back to inline template on failure."""
    from pathlib import Path
    path = Path(__file__).resolve().parents[2] / "prompt" / "explain_result.prompt"
    if path.exists():
        try:
            return path.read_text(encoding="utf-8")
        except Exception:
            pass
    return (
        "Based on the question, SQL and result below, write a one-sentence "
        "explanation in natural Chinese.\n"
        "Question: {query}\n"
        "SQL: {sql}\n"
        "Result preview: {result_preview}\n"
    )


def _format_result_preview(result: dict[str, Any]) -> str:
    cols = result.get("columns") or []
    rows = result.get("rows") or []
    if not cols:
        return "(empty)"
    head = rows[:5]
    return json.dumps({"columns": cols, "rows": head}, ensure_ascii=False, default=str)


def _build_explanation(query: str, sql: str, result: dict[str, Any]) -> str:
    """Generate a one-sentence Chinese explanation of the result (no cache)."""
    if not query:
        return "已执行查询并返回结果。"
    rows = result.get("rows") or []
    rc = result.get("row_count", len(rows))
    cols = result.get("columns") or []
    if rc == 0:
        return f"针对「{query}」,查询无返回结果。"
    if rc == 1 and len(cols) == 1:
        return f"针对「{query}」,查询返回单个数值: {rows[0][0] if rows else ''}。"
    return f"针对「{query}」,查询返回 {rc} 行结果(列: {', '.join(cols[:6])})。"


async def run_sql(state: AgentState, config: RunnableConfig | None = None) -> dict:
    t0 = now_ms()
    runtime = get_runtime(config)
    request_id = state.get("request_id", "-")
    sql = state.get("sql", "") or ""
    query = state.get("query", "")

    cache_hit_result = False
    explanation = ""
    cache_key = make_result_cache_key(sql)
    result: dict[str, Any] = {"columns": [], "rows": [], "row_count": 0, "truncated": False}
    error: Optional[str] = None

    # --- Step 1: cache lookup -----------------------------------------------
    cache_store = getattr(runtime, "cache", None) if runtime is not None else None
    if cache_store is not None and hasattr(cache_store, "get_exact"):
        try:
            cached = cache_store.get_exact(cache_key)
        except Exception:
            cached = None
        if isinstance(cached, dict) and (cached.get("result") or cached.get("result_json")):
            blob = cached.get("result") or cached.get("result_json")
            if isinstance(blob, str):
                try:
                    result = json.loads(blob)
                except Exception:
                    result = {"columns": [], "rows": [], "row_count": 0, "truncated": False}
            elif isinstance(blob, dict):
                result = blob
            cache_hit_result = True

    # --- Step 2: on miss, run against the DW --------------------------------
    if not cache_hit_result:
        result, error = await _execute_sql(sql, runtime)

        # --- Step 3: write to cache (TTL=3600) -----------------------------
        if error is None and cache_store is not None and hasattr(cache_store, "put"):
            try:
                if hasattr(cache_store, "_ttl") and RESULT_CACHE_TTL_SECONDS:
                    try:
                        cache_store._ttl = RESULT_CACHE_TTL_SECONDS
                    except Exception:
                        pass
                cache_store.put(
                    cache_key,
                    {
                        "result": result,
                        "sql": sql,
                        "stored_at": now_ms(),
                    },
                )
            except Exception:
                pass

    # --- Step 4: emit the result event -------------------------------------
    result_event = {
        "type": "result",
        "columns": result.get("columns", []),
        "rows": result.get("rows", []),
        "row_count": int(result.get("row_count", 0) or 0),
        "truncated": bool(result.get("truncated", False)),
        "request_id": request_id,
        "cache_hit": cache_hit_result,
        "error": error,
    }
    _stream_writer(runtime, result_event)

    # --- Step 5: generate explanation (never cached) -----------------------
    if error is None:
        if runtime is not None and getattr(runtime, "llm", None) is not None:
            template = _load_prompt_template()
            prompt = template.format(
                query=query,
                sql=sql,
                result_preview=_format_result_preview(result),
            )
            try:
                resp = await runtime.llm.ainvoke(prompt)
                txt = (resp.text or "").strip()
                # LLM mocks may return a stringified JSON; strip a single layer.
                if txt.startswith('"') and txt.endswith('"'):
                    try:
                        txt = json.loads(txt)
                    except Exception:
                        pass
                explanation = txt or _build_explanation(query, sql, result)
                if runtime.metrics is not None:
                    runtime.metrics.record_llm_call(LLMCallStat(
                        node_name="explain_result",
                        model=str(getattr(runtime.llm, "model", "mock")),
                        prompt_tokens=len(prompt) // 2,
                        completion_tokens=len(resp.text) // 2,
                        total_tokens=(len(prompt) + len(resp.text)) // 2,
                        latency_ms=int(getattr(resp, "latency_ms", 0)),
                        cache_hit=False,
                    ))
            except Exception:
                explanation = _build_explanation(query, sql, result)
        else:
            explanation = _build_explanation(query, sql, result)
    else:
        explanation = f"SQL 执行失败: {error}"

    # --- Step 6: emit the done event ---------------------------------------
    duration_ms = round((time.perf_counter() - (state.get("started_at") or time.perf_counter())) * 1000.0, 2) \
        if state.get("started_at") else round(now_ms() - t0, 2)
    done_event = {
        "type": "done",
        "request_id": request_id,
        "duration_ms": duration_ms,
        "sql": sql,
        "row_count": int(result.get("row_count", 0) or 0),
        "cache_hit": cache_hit_result,
        "explanation": explanation,
    }
    _stream_writer(runtime, done_event)

    # --- Step 7: metrics + node counters -----------------------------------
    elapsed = now_ms() - t0
    if runtime is not None:
        runtime.metrics.record_node_latency("run_sql", elapsed)
        runtime.metrics.record_sql_executed(success=error is None)
        runtime.nodes_called += 1

    log_node(
        "run_sql", request_id,
        "cache_hit" if cache_hit_result else ("ok" if error is None else "fail"),
        rows=result.get("row_count", 0), cache_hit=cache_hit_result,
        error=error or "",
    )

    return {
        "result": result,
        "explanation": explanation,
        "error": error,
        "execution_result": result,
        "cache_hit_result": cache_hit_result,
        "pending_stream_events": [result_event, done_event],
        "node_history": history_append(
            state, "run_sql",
            "cache_hit" if cache_hit_result else ("ok" if error is None else "fail"),
            elapsed,
            extra={
                "rows": int(result.get("row_count", 0) or 0),
                "cache_hit": cache_hit_result,
                "cache_key": cache_key[:12],
                "error": error or "",
            },
        ),
    }