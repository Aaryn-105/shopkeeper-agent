"""Node: correct_sql (4.2.11 / V1.0 phase 6.11).

V1.0 phase 6.11 spec:
  - LLM一次性重写 SQL;**不走缓存**。
  - `state.sql = corrected_sql`、`state.sql_corrected = True`。
  - 解析 LLM 输出(JSON / markdown / 纯 SQL 三态)复用 `parse_sql_response`。
  - 记录 LLM 调用埋点 `record_llm_call(node_name="correct_sql")`。
  - 推送事件:
        {"type":"sql_corrected","original_sql":..., "corrected_sql":...,
         "error": state.sql_error, "request_id": state.request_id}
    写入 state.pending_stream_events 与 runtime.pending_events。
  - 节点耗时记录到 metrics(node_name="correct_sql")。

容错:LLM 抛错 / 输出空 -> 保留原 SQL(不让 workflow 卡死),事件仍发。
"""
from __future__ import annotations
import json
from pathlib import Path
from typing import Any

from langchain_core.runnables import RunnableConfig

from app.agent.nodes._helpers import get_runtime, history_append, log_node, now_ms
from app.agent.nodes.generate_sql import parse_sql_response
from app.agent.state import AgentState
from app.core.metrics import LLMCallStat


_PROMPT_PATH = Path(__file__).resolve().parents[2] / "prompt" / "correct_sql.prompt"
_FALLBACK_PROMPT = (
    "Correct the following MySQL SQL.\n"
    "Question: {query}\n"
    "Current time: {current_time}\n"
    "Table info: {filtered_table_infos}\n"
    "Metric info: {filtered_metric_infos}\n"
    "Original SQL: {original_sql}\n"
    "Error: {error}\n"
    "Return only the corrected SQL statement."
)


def _load_prompt_template() -> str:
    """Load the correction prompt; fall back to an inline template on failure."""
    try:
        if _PROMPT_PATH.exists():
            return _PROMPT_PATH.read_text(encoding="utf-8")
    except Exception:
        pass
    return _FALLBACK_PROMPT


def _stream_writer(runtime, event: dict[str, Any]) -> None:
    """Push an event into runtime.pending_events (best-effort, no-op for bare-stub runtime)."""
    if runtime is None:
        return
    pending = getattr(runtime, "pending_events", None)
    if isinstance(pending, list):
        pending.append(event)


async def correct_sql(state: AgentState, config: RunnableConfig | None = None) -> dict:
    t0 = now_ms()
    runtime = get_runtime(config)
    request_id = state.get("request_id", "-")
    original_sql = state.get("sql", "") or ""
    err = state.get("sql_error", "") or ""
    attempts = int(state.get("validate_attempts") or 0)

    table_infos = (
        state.get("filtered_table_infos")
        or state.get("merged_table_infos")
        or state.get("table_infos")
        or {}
    )
    metric_infos = state.get("filtered_metric_infos") or state.get("metric_infos") or []
    extra = state.get("extra_context") or {}

    query = state.get("query", "")
    current_time = extra.get("current_time", "")

    # --- Step 1: call LLM (no cache lookup / no cache write) ---------------
    corrected_sql = original_sql
    llm_invoked = False
    if runtime is not None and getattr(runtime, "llm", None) is not None:
        template = _load_prompt_template()
        prompt = template.format(
            query=query,
            current_time=current_time,
            filtered_table_infos=json.dumps(table_infos, ensure_ascii=False, indent=2),
            filtered_metric_infos=json.dumps(metric_infos, ensure_ascii=False, indent=2),
            original_sql=original_sql,
            error=err,
        )
        try:
            resp = await runtime.llm.ainvoke(prompt)
            parsed = parse_sql_response(resp.text).strip()
            if parsed:
                corrected_sql = parsed
            if runtime.metrics is not None:
                runtime.metrics.record_llm_call(LLMCallStat(
                    node_name="correct_sql",
                    model=str(getattr(runtime.llm, "model", "mock")),
                    prompt_tokens=len(prompt) // 2,
                    completion_tokens=len(resp.text) // 2,
                    total_tokens=(len(prompt) + len(resp.text)) // 2,
                    latency_ms=int(getattr(resp, "latency_ms", 0)),
                    cache_hit=False,
                ))
            llm_invoked = True
        except Exception as e:  # noqa: BLE001 - keep workflow moving
            log_node("correct_sql", request_id, "llm_error",
                     error=f"{type(e).__name__}: {str(e)[:80]}")
            corrected_sql = original_sql

    # --- Step 2: build the sql_corrected event -----------------------------
    event = {
        "type": "sql_corrected",
        "original_sql": original_sql,
        "corrected_sql": corrected_sql,
        "error": err,
        "request_id": request_id,
        "attempts": attempts,
    }
    _stream_writer(runtime, event)

    # --- Step 3: metrics + node counters ----------------------------------
    elapsed = now_ms() - t0
    if runtime is not None:
        runtime.metrics.record_node_latency("correct_sql", elapsed)
        runtime.nodes_called += 1

    log_node(
        "correct_sql", request_id,
        "ok" if corrected_sql else "no_correction",
        attempts=attempts,
        llm_invoked=llm_invoked,
        sql_len=len(corrected_sql),
    )

    return {
        "sql": corrected_sql,
        "sql_corrected": True,
        "pending_stream_events": [event],
        "node_history": history_append(
            state, "correct_sql",
            "ok" if corrected_sql else "no_correction",
            elapsed,
            extra={
                "attempts": attempts,
                "llm_invoked": llm_invoked,
                "sql_len": len(corrected_sql),
            },
        ),
    }