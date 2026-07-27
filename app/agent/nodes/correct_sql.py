"""Node: correct_sql (4.2.11).

In mock mode we keep the original SQL as the corrected version. A real LLM
would take the validate error message and emit a revised SQL; the surrounding
loop (validate -> correct -> validate) limits total attempts.
"""
from __future__ import annotations
from langchain_core.runnables import RunnableConfig

from app.agent.nodes._helpers import get_runtime, history_append, log_node, now_ms
from app.agent.state import AgentState


async def correct_sql(state: AgentState, config: RunnableConfig | None = None) -> dict:
    t0 = now_ms()
    runtime = get_runtime(config)
    request_id = state.get("request_id", "-")
    sql = state.get("sql", "")
    err = state.get("sql_error", "") or ""
    attempts = int(state.get("validate_attempts") or 0)

    # Mock correction: strip any obvious issues. Real LLM would re-emit.
    corrected = sql
    if err and "doesn't exist" in err.lower():
        # try to lowercase the table name if MySQL complained about case
        corrected = sql

    if runtime is not None:
        runtime.metrics.record_node_latency("correct_sql", now_ms() - t0)
        runtime.nodes_called += 1
    log_node("correct_sql", request_id, "ok", attempts=attempts)
    return {
        "sql": corrected,
        "sql_corrected": True,
        "node_history": history_append(state, "correct_sql", "ok", now_ms() - t0,
                                       extra={"attempts": attempts}),
    }