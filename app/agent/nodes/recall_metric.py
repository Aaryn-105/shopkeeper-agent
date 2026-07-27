"""Node: recall_metric (4.2.3)."""
from __future__ import annotations
from langchain_core.runnables import RunnableConfig

from app.agent.nodes._helpers import get_runtime, history_append, log_node, now_ms
from app.agent.state import AgentState
from app.core.config import cfg


def _vector_or_text_search(runtime, query: str, top_k: int):
    """Try vector search first if FAISS has vectors; else fall back to text recall."""
    faiss = runtime.faiss
    try:
        coll = faiss.metric_info
        if coll.is_indexed and runtime.embedding is not None:
            vec = runtime.embedding.encode([query])[0]
            return coll.search(vec, top_k)
    except Exception:
        pass
    return faiss.recall_metric(query, top_k)


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
            for h in _vector_or_text_search(
                runtime, text,
                int(cfg.recall.metric_top_k),
            ):
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