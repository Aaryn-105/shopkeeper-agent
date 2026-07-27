"""Node: generate_sql (4.2.9)."""
from __future__ import annotations
from langchain_core.runnables import RunnableConfig

from app.agent.nodes._helpers import get_runtime, history_append, log_node, now_ms
from app.agent.state import AgentState
from app.core.metrics import LLMCallStat


async def generate_sql(state: AgentState, config: RunnableConfig | None = None) -> dict:
    t0 = now_ms()
    runtime = get_runtime(config)
    request_id = state.get("request_id", "-")
    query = state.get("query", "")
    tables = state.get("filtered_table_infos") or state.get("merged_table_infos") or {}

    sql = ""
    if runtime is not None and runtime.llm is not None and not runtime.llm.is_mock:
        # Real LLM path: prompt assembly lives in app.prompt. Left as TODO until
        # a real backend is wired up; falls through to mock below.
        pass

    if not sql:
        from app.clients.llm_client import _mock_generate
        prompt = (
            "Generate SQL for the following request. "
            "SELECT/WITH/EXPLAIN only.\n"
            f"table_ids: {' '.join(tables.keys())}\n"
            f"query: {query}\n"
        )
        sql = _mock_generate(prompt).strip()

    if not sql:
        sql = "SELECT COUNT(*) FROM fact_order"

    elapsed = now_ms() - t0
    if runtime is not None:
        runtime.metrics.record_node_latency("generate_sql", elapsed)
        runtime.metrics.record_sql_generated()
        runtime.nodes_called += 1
        runtime.metrics.record_llm_call(LLMCallStat(
            node_name="generate_sql",
            model=str(getattr(runtime.llm, "model", "mock")),
            prompt_tokens=len(query) // 2,
            completion_tokens=len(sql) // 2,
            total_tokens=(len(query) + len(sql)) // 2,
            latency_ms=int(elapsed),
            cache_hit=False,
        ))
    log_node("generate_sql", request_id, "ok", sql_len=len(sql))
    return {
        "sql": sql,
        "sql_corrected": False,
        "node_history": history_append(state, "generate_sql", "ok", elapsed,
                                       extra={"sql_len": len(sql)}),
    }