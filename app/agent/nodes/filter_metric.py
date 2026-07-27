"""Node: filter_metric (4.2.7)."""
from __future__ import annotations
from langchain_core.runnables import RunnableConfig

from app.agent.nodes._helpers import get_runtime, history_append, log_node, now_ms
from app.agent.state import AgentState


async def filter_metric(state: AgentState, config: RunnableConfig | None = None) -> dict:
    t0 = now_ms()
    runtime = get_runtime(config)
    request_id = state.get("request_id", "-")
    metrics = state.get("retrieved_metrics") or []
    query = state.get("query", "")

    tokens = [t for t in query.replace(",", " ").split() if t]
    kept: list[dict] = []
    for m in metrics:
        blob = " ".join(str(v) for v in m.values() if v).lower()
        if any(t.lower() in blob for t in tokens):
            kept.append(m)
    if not kept:
        kept = metrics  # don't over-filter when recall was already coarse

    if runtime is not None:
        runtime.metrics.record_node_latency("filter_metric", now_ms() - t0)
        runtime.nodes_called += 1
    log_node("filter_metric", request_id, "ok", metrics=len(kept))
    return {
        "filtered_metric_infos": kept,
        "node_history": history_append(state, "filter_metric", "ok", now_ms() - t0,
                                       extra={"metrics": len(kept)}),
    }