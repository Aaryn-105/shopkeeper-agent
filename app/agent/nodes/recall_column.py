"""Node: recall_column (4.2.2).

Calls FAISSStore.recall_column(query). When the FAISS index has payloads from a
prior build_knowledge_index run, this returns scored hits. When payloads are
present but vectors are absent we still get text-recall results (substring
match on name/description/alias).
"""
from __future__ import annotations
from langchain_core.runnables import RunnableConfig

from app.agent.nodes._helpers import get_runtime, history_append, log_node, now_ms
from app.agent.state import AgentState


def recall_column(state: AgentState, config: RunnableConfig | None = None) -> dict:
    t0 = now_ms()
    runtime = get_runtime(config)
    request_id = state.get("request_id", "-")
    query = state.get("query", "")
    keywords = state.get("keywords") or []

    hits: list[dict] = []
    if runtime is not None and runtime.faiss is not None:
        # query + keyword expansion, then take union
        seen = set()
        for text in [query, " ".join(keywords)]:
            if not text.strip():
                continue
            for h in runtime.faiss.recall_column(text):
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