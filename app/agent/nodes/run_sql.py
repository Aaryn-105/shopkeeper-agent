"""Node: run_sql (4.2.12)."""
from __future__ import annotations
from langchain_core.runnables import RunnableConfig

from app.agent.nodes._helpers import get_runtime, history_append, log_node, now_ms
from app.agent.state import AgentState


async def run_sql(state: AgentState, config: RunnableConfig | None = None) -> dict:
    t0 = now_ms()
    runtime = get_runtime(config)
    request_id = state.get("request_id", "-")
    sql = state.get("sql", "")

    result: dict = {"columns": [], "rows": [], "row_count": 0, "truncated": False}
    error: str | None = None
    if runtime is not None and runtime.mysql_dw is not None:
        try:
            result = await runtime.mysql_dw.execute_readonly(sql)
        except Exception as e:
            error = f"{type(e).__name__}: {str(e)[:200]}"

    if runtime is not None:
        runtime.metrics.record_node_latency("run_sql", now_ms() - t0)
        runtime.metrics.record_sql_executed(success=error is None)
        runtime.nodes_called += 1
    log_node("run_sql", request_id, "ok" if error is None else "fail",
             rows=result.get("row_count", 0), error=error)
    return {
        "result": result,
        "error": error,
        "node_history": history_append(
            state, "run_sql", "ok" if error is None else "fail",
            now_ms() - t0,
            extra={"rows": result.get("row_count", 0), "error": error or ""},
        ),
    }