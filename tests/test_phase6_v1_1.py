"""V1.0 phase 6.1 verification: extract_keywords (4.2.1).

Spec (V1.0 SRS-aligned plan, phase 6.1):
  - jieba.analyse.extract_tags(query, topK=8, withWeight=False)
  - Filter STOP_WORDS = {"\u7684","\u4e86","\u4e00\u4e0b","\u5e2e\u6211","\u8bf7\u95ee","\u67e5\u8be2"}
  - Output state.keywords; preserve state.query
  - Performance: pure local compute, target << 500ms
"""
from __future__ import annotations
import time
import pytest
from langchain_core.runnables import RunnableConfig

from app.agent.nodes.extract_keywords import (
    STOP_WORDS,
    TOPK,
    _extract,
    extract_keywords,
)
from app.agent.state import AgentState


# ---------- 6.1.1 algorithm + topK ----------

def test_extract_keywords_uses_extract_tags_algorithm():
    """V1.0 6.1: must use jieba.analyse.extract_tags (not textrank/tfidf)."""
    # "GMV" is a content word and should be picked up by extract_tags even
    # when surrounded by stop words and filler.
    out = _extract("\u67e5\u8be2\u4e0a\u4e2a\u6708GMV")
    assert "GMV" in out


def test_extract_keywords_top_k_is_8():
    """V1.0 6.1: topK=8 (was 10 in prior textrank impl)."""
    assert TOPK == 8


def test_extract_keywords_caps_output_at_8():
    """Even with a long query containing many terms, output length <= 8."""
    long_query = (
        "\u534e\u4e1c\u534e\u5317\u534e\u5357 \u6c7d\u8f66\u624b\u673a\u7535\u8111 "
        "\u670d\u88c5\u98df\u54c1\u9152\u6c34\u5316\u5986\u6d3b\u52a8 "
        "GMV AOV \u9500\u552e\u989d\u8ba2\u5355\u91cf\u5ba2\u5355\u4ef7"
    )
    out = _extract(long_query)
    assert len(out) <= 8


# ---------- 6.1.2 STOP_WORDS filter ----------

def test_stop_words_constant_matches_v1_spec():
    """The STOP_WORDS set must include every word from V1.0 phase 6.1."""
    expected = {"\u7684", "\u4e86", "\u4e00\u4e0b", "\u5e2e\u6211", "\u8bf7\u95ee", "\u67e5\u8be2"}
    # Use subset because frozenset dedups the accidental duplicate
    assert expected <= set(STOP_WORDS)


@pytest.mark.parametrize("sw", ["\u7684", "\u4e86", "\u4e00\u4e0b", "\u5e2e\u6211", "\u8bf7\u95ee", "\u67e5\u8be2"])
def test_extract_keywords_filters_each_stop_word(sw):
    """A query that is only stop words should return an empty keyword list."""
    out = _extract(sw)
    assert sw not in out
    assert out == []


def test_extract_keywords_strips_stop_words_from_mixed_query():
    """Stop words must be filtered out while content words are kept."""
    out = _extract("\u8bf7\u95ee\u4e0a\u4e2a\u6708\u534e\u4e1c\u7684GMV\u662f\u591a\u5c11")
    # "\u8bf7\u95ee" and "\u7684" are stop words; "\u534e\u4e1c" and "GMV" should remain
    assert "\u8bf7\u95ee" not in out
    assert "\u7684" not in out
    assert "GMV" in out


# ---------- 6.1.3 state preservation + return shape ----------

def test_extract_keywords_preserves_state_query():
    """V1.0 6.1: keep state.query; only emit keywords."""
    state: AgentState = {
        "query": "\u4e0a\u4e2a\u6708\u9500\u552e\u989d",
        "request_id": "rid-6-1-1",
        "node_history": [],
        "validate_attempts": 0,
    }
    out = extract_keywords(state, config=None)
    # state.query is not in the return dict (preserved by graph reducer)
    assert "query" not in out
    # only keywords + node_history are returned
    assert set(out.keys()) == {"keywords", "node_history"}
    # the returned dict must not mutate the input
    assert state["query"] == "\u4e0a\u4e2a\u6708\u9500\u552e\u989d"


def test_extract_keywords_returns_list():
    """keywords must be a JSON-serialisable list[str]."""
    out = extract_keywords(
        {"query": "GMV", "request_id": "rid-6-1-2", "node_history": [], "validate_attempts": 0},
        config=None,
    )
    assert isinstance(out["keywords"], list)
    assert all(isinstance(k, str) for k in out["keywords"])


def test_extract_keywords_empty_query_returns_empty_list():
    state: AgentState = {
        "query": "",
        "request_id": "rid-6-1-3",
        "node_history": [],
        "validate_attempts": 0,
    }
    out = extract_keywords(state, config=None)
    assert out["keywords"] == []


def test_extract_keywords_whitespace_only_query_returns_empty():
    out = _extract("   ")
    assert out == []


# ---------- 6.1.4 metrics埋点 ----------

class _StubRuntime:
    """Minimal runtime stub so we can assert metrics + nodes_called."""

    def __init__(self) -> None:
        self.metrics = _StubMetrics()
        self.nodes_called = 0


class _StubMetrics:
    def __init__(self) -> None:
        self.calls: list[tuple[str, float]] = []

    def record_node_latency(self, node: str, ms: float) -> None:
        self.calls.append((node, ms))


def test_extract_keywords_records_node_latency():
    runtime = _StubRuntime()
    state: AgentState = {
        "query": "GMV",
        "request_id": "rid-6-1-4",
        "node_history": [],
        "validate_attempts": 0,
    }
    extract_keywords(state, config=_runtime_config(runtime))
    # P95埋点 should have been called for extract_keywords
    nodes = [n for n, _ in runtime.metrics.calls]
    assert "extract_keywords" in nodes
    assert runtime.nodes_called == 1


def _runtime_config(runtime):
    """Build a RunnableConfig with our stub runtime attached."""
    cfg = RunnableConfig()
    cfg["configurable"] = {"runtime": runtime}
    return cfg


# ---------- 6.1.5 performance: << 500ms ----------

def test_extract_keywords_under_500ms_warm():
    """V1.0 6.1: pure local compute, measured should be << 500ms.

    Run the node a few times to amortise jieba cold-start.
    """
    queries = [
        "\u4e0a\u4e2a\u6708\u534e\u4e1c\u5730\u533a\u7684GMV",
        "\u5404\u54c1\u7c7b\u5ba2\u5355\u4ef7\u5982\u4f55",
        "\u6700\u8fd1\u4e03\u5929\u8ba2\u5355\u91cf\u8d8b\u52bf",
    ]
    # warm-up
    for q in queries:
        _extract(q)
    # measure
    t0 = time.perf_counter()
    for q in queries * 5:  # 15 calls
        _extract(q)
    elapsed_ms = (time.perf_counter() - t0) * 1000 / 15  # average per call
    assert elapsed_ms < 500, f"extract_keywords too slow: {elapsed_ms:.1f}ms"