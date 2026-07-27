"""Node: extract_keywords (4.2.1 / V1.0 phase 6.1).

V1.0 phase 6.1 spec:
  - jieba.analyse.extract_tags(query, topK=8, withWeight=False)
  - Filter stop words: STOP_WORDS = {"\u7684","\u4e86","\u4e00\u4e0b","\u5e2e\u6211","\u8bf7\u95ee","\u67e5\u8be2"}
  - Output state.keywords; preserve state.query
  - Performance: pure local compute, target << 500ms

Note: prior implementation used jieba.analyse.textrank(topK=10); per V1.0
phase 6.1 we switch to extract_tags + stop-word filter.
"""
from __future__ import annotations
from langchain_core.runnables import RunnableConfig

from app.agent.nodes._helpers import get_runtime, history_append, log_node, now_ms
from app.agent.state import AgentState


# Stop words per V1.0 phase 6.1. frozenset gives O(1) lookup and dedups the
# accidental "\u5e2e\u6211" duplicate that appears in the spec list.
STOP_WORDS: frozenset[str] = frozenset({
    "\u7684", "\u4e86", "\u4e00\u4e0b", "\u5e2e\u6211", "\u8bf7\u95ee", "\u67e5\u8be2",
})

# Per V1.0 phase 6.1: topK=8, withWeight=False
TOPK: int = 8


def _extract(query: str) -> list[str]:
    """Run jieba extract_tags + stop-word filter. Falls back to split() on error."""
    try:
        import jieba.analyse
        # V1.0 6.1: jieba.analyse.extract_tags(query, topK=8, withWeight=False)
        candidates = jieba.analyse.extract_tags(query, topK=TOPK, withWeight=False)
        keywords = [w for w in candidates if w and w not in STOP_WORDS]
        if keywords:
            return keywords
    except Exception:
        pass
    # Fallback: split on whitespace/punctuation, also filter stop words.
    # Keeps behaviour stable when jieba is unavailable or returns nothing.
    fallback = [w for w in query.replace(",", " ").split() if w and w not in STOP_WORDS]
    return fallback


def extract_keywords(state: AgentState, config: RunnableConfig | None = None) -> dict:
    t0 = now_ms()
    runtime = get_runtime(config)
    query = state.get("query", "")
    request_id = state.get("request_id", "-")

    keywords = _extract(query)

    if runtime is not None:
        runtime.metrics.record_node_latency("extract_keywords", now_ms() - t0)
        runtime.nodes_called += 1

    log_node("extract_keywords", request_id, "ok", keywords=len(keywords))
    return {
        # preserve state.query unchanged; only emit keywords
        "keywords": keywords,
        "node_history": history_append(state, "extract_keywords", "ok", now_ms() - t0),
    }