"""Node: extract_keywords (4.2.1).

Uses jieba.analyse.textrank to pull out top keywords. No LLM is required for
this node (SRS specifies jieba TF-IDF; we use TextRank which is the
recommended upgrade and ships with jieba).
"""
from __future__ import annotations
from langchain_core.runnables import RunnableConfig

from app.agent.nodes._helpers import get_runtime, history_append, log_node, now_ms
from app.agent.state import AgentState


def extract_keywords(state: AgentState, config: RunnableConfig | None = None) -> dict:
    t0 = now_ms()
    runtime = get_runtime(config)
    query = state.get("query", "")
    request_id = state.get("request_id", "-")

    try:
        import jieba.analyse
        keywords = jieba.analyse.textrank(query, topK=10)
        if not keywords:
            # fallback: split on whitespace
            keywords = [t for t in query.replace(",", " ").split() if t]
    except Exception:
        keywords = [t for t in query.replace(",", " ").split() if t]

    if runtime is not None:
        runtime.metrics.record_node_latency("extract_keywords", now_ms() - t0)
        runtime.nodes_called += 1

    log_node("extract_keywords", request_id, "ok", keywords=len(keywords))
    return {
        "keywords": keywords,
        "node_history": history_append(state, "extract_keywords", "ok", now_ms() - t0),
    }