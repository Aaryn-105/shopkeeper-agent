"""Node: filter_table (4.2.6).

LLM-based filter of merged_table_infos. In mock mode we keep all tables/columns
because the merge step already did a coarse recall. When a real LLM is wired
in the same prompt shape would apply.
"""
from __future__ import annotations
from langchain_core.runnables import RunnableConfig

from app.agent.nodes._helpers import get_runtime, history_append, log_node, now_ms
from app.agent.state import AgentState


async def filter_table(state: AgentState, config: RunnableConfig | None = None) -> dict:
    t0 = now_ms()
    runtime = get_runtime(config)
    request_id = state.get("request_id", "-")
    merged = state.get("merged_table_infos") or {}
    query = state.get("query", "")

    # In mock mode we keep every merged table/column; the merge step is already
    # a coarse recall so filtering here only removes obvious non-matches.
    tokens = [t for t in query.replace(",", " ").split() if t]
    keep: dict[str, dict] = {}
    for tid, info in merged.items():
        cols = info.get("columns", [])
        matched = []
        for c in cols:
            blob = " ".join(str(v) for v in c.values() if v).lower()
            if any(t.lower() in blob for t in tokens) or tid in query:
                matched.append(c)
        if not matched:
            matched = cols  # fallback: keep everything from the merge step
        keep[tid] = {**info, "columns": matched}

    if runtime is not None and runtime.llm is not None and not runtime.llm.is_mock:
        # real LLM path could update `keep` based on its decision; left as TODO
        pass

    if runtime is not None:
        runtime.metrics.record_node_latency("filter_table", now_ms() - t0)
        runtime.nodes_called += 1
    log_node("filter_table", request_id, "ok", tables=len(keep))
    return {
        "filtered_table_infos": keep,
        "node_history": history_append(state, "filter_table", "ok", now_ms() - t0,
                                       extra={"tables": len(keep)}),
    }