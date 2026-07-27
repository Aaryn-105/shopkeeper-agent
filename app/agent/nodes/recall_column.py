"""Node: recall_column (4.2.2).

Calls FAISSStore.recall_column(query). When the FAISS index has vectors (built
by scripts/build_knowledge_index.py), this returns vector-search hits.
Otherwise falls back to text-recall and finally to meta.column_info.
"""
from __future__ import annotations
from langchain_core.runnables import RunnableConfig

from app.agent.nodes._helpers import get_runtime, history_append, log_node, now_ms
from app.agent.state import AgentState
from app.core.config import cfg


def _vector_or_text_search(runtime, query: str, top_k: int):
    """Try vector search first if FAISS has vectors; else fall back to text recall."""
    faiss = runtime.faiss
    try:
        coll = faiss.column_info
        if coll.is_indexed and runtime.embedding is not None:
            vec = runtime.embedding.encode([query])[0]
            return coll.search(vec, top_k)
    except Exception:
        pass
    return faiss.recall_column(query, top_k)


def recall_column(state: AgentState, config: RunnableConfig | None = None) -> dict:
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
                int(cfg.recall.column_top_k),
            ):
                if h.get("id") in seen:
                    continue
                seen.add(h.get("id"))
                hits.append(h)
    # Fallback: pull directly from meta.column_info via metadata client
    if not hits and runtime is not None and getattr(runtime, "metadata", None) is not None:
        cols = runtime.metadata.list_columns()
        for c in cols:
            blob = " ".join(str(v) for v in c.values() if v).lower()
            if any(k.lower() in blob for k in keywords) or query.strip() and query in str(c.get("description", "")):
                hits.append(c)

    if runtime is not None:
        runtime.metrics.record_node_latency("recall_column", now_ms() - t0)
        runtime.nodes_called += 1
    log_node("recall_column", request_id, "ok", hits=len(hits))
    return {
        "retrieved_columns": hits,
        "node_history": history_append(state, "recall_column", "ok", now_ms() - t0,
                                       extra={"hits": len(hits)}),
    }