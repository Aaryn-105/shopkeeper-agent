"""Node: recall_metric (4.2.3)."""
from __future__ import annotations
from langchain_core.runnables import RunnableConfig

from app.agent.nodes._helpers import get_runtime, history_append, log_node, now_ms
from app.agent.state import AgentState


def recall_metric(state: AgentState, config: RunnableConfig | None = None) -> dict:
    t0 = now_ms()
    runtime = get_runtime(config)
    request_id = state.get("request_id", "-")
    query = state.get("query", "")
    keywords = state.get("keywords") or []

    hits: list[dict] = []
    if runtime is not None and runtime.faiss is not None:
        seen = set()
        for text in [query, " ".join(keywords)]:
            if not text.strip():
                continue
            for h in runtime.faiss.recall_metric(text):
                if h.get("id") in seen:
                    continue
                seen.add(h.get("id"))
                hits.append(h)

    if not hits and runtime is not None and getattr(runtime, "metadata", None) is not None:
        ms = runtime.metadata.list_metrics()
        for m in ms:
            blob = " ".join(str(v) for v in m.values() if v).lower()
            if any(k.lower() in blob for k in keywords):
                hits.append(m)

    if runtime is not None:
        runtime.metrics.record_node_latency("recall_metric", now_ms() - t0)
        runtime.nodes_called += 1
    log_node("recall_metric", request_id, "ok", hits=len(hits))
    return {
        "retrieved_metrics": hits,
        "node_history": history_append(state, "recall_metric", "ok", now_ms() - t0,
                                       extra={"hits": len(hits)}),
    }