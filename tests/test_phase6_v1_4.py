"""V1.0 phase 6.4 verification: recall_value (4.2.4).

V1.0 phase 6.4 spec:
  - jieba.cut(query) -> keyword list
  - For each token, fts5_client.search_values(token, top_k=30)
  - Aggregate hits with the same column_id (dedupe by (value, column_id))
  - Write state.retrieved_values (SRS canonical: list[{value, column_id}])
"""
from __future__ import annotations
import pytest
from langchain_core.runnables import RunnableConfig

from app.agent.nodes.recall_value import (
    TOPK_PER_TOKEN,
    _tokenize,
    _aggregate_by_column_id,
    _search_one_token,
    recall_value,
)


# ---------- 6.4.1 constants ----------

def test_topk_per_token_is_30():
    assert TOPK_PER_TOKEN == 30


# ---------- 6.4.2 tokenisation (jieba.cut + STOP_WORDS) ----------

def test_tokenize_strips_stop_words():
    toks = _tokenize("的 帮我 请问 查询 GMV 华东")
    assert "的" not in toks
    assert "帮我" not in toks
    assert "请问" not in toks
    assert "查询" not in toks
    assert "GMV" in toks or "gmv" in [t.lower() for t in toks]
    assert "华东" in toks


def test_tokenize_handles_empty_string():
    assert _tokenize("") == []
    assert _tokenize("   ") == []


def test_tokenize_dedupes_repeated_tokens():
    toks = _tokenize("华东 华东 GMV GMV")
    assert toks == ["华东", "GMV"] or len(toks) == len(set(toks))


def test_tokenize_chinese_uses_jieba_cut():
    """jieba should split Chinese strings; str.split() would not."""
    toks = _tokenize("上个月华东地区的GMV")
    # jieba splits this into multiple meaningful tokens
    assert len(toks) >= 3
    assert any("\u4e1c" in t or "\u534e" in t for t in toks)


def test_tokenize_falls_back_to_whitespace_when_jieba_unavailable(monkeypatch):
    """If jieba import raises (e.g. during a cold-start transient), we still
    return whitespace tokens rather than crashing the node."""
    import app.agent.nodes.recall_value as mod

    def boom(_):
        raise RuntimeError("jieba cold-start fail")

    monkeypatch.setattr(mod, "jieba", type("J", (), {"cut": staticmethod(boom)})())
    toks = _tokenize("foo bar baz")
    assert toks == ["foo", "bar", "baz"]


# ---------- 6.4.3 per-token FTS5 search ----------

class _StubFTS5:
    """FTS5 stub that returns canned hits keyed by token.

    Tokens match the actual jieba.lcut output for the test queries:
      jieba.lcut("上个月华东地区的GMV") -> ['上个月', '华东地区', '的', 'GMV']
      jieba.lcut("华东") -> ['华东']
      jieba.lcut("华东 手机") -> ['华东', ' ', '手机']
    """

    def __init__(self):
        self.calls = []  # list of (token, top_k)

    def search(self, query, top_k=None):
        self.calls.append((query, top_k or 30))
        if query == "\u534e\u4e1c" or query == "\u534e\u4e1c\u5730\u533a":  # 华东 or 华东地区
            return [
                {"value": "\u534e\u4e1c", "column_id": "dim_region.region_name"},
                {"value": "\u534e\u5317", "column_id": "dim_region.region_name"},
            ]
        if query == "GMV":
            return []
        if query == "\u624b\u673a":  # 手机
            return [{"value": "\u624b\u673a", "column_id": "dim_product.category"}]
        return []


def test_search_one_token_returns_canned_hits():
    rt = type("R", (), {"fts5": _StubFTS5()})()
    hits = _search_one_token(rt, "\u534e\u4e1c", top_k=30)
    assert len(hits) == 2
    assert hits[0]["column_id"] == "dim_region.region_name"


def test_search_one_token_returns_empty_when_no_fts5():
    rt = type("R", (), {"fts5": None})()
    assert _search_one_token(rt, "\u534e\u4e1c", top_k=30) == []


def test_search_one_token_returns_empty_when_fts5_raises():
    class _BoomFTS5:
        def search(self, *a, **k):
            raise RuntimeError("fts5 down")
    rt = type("R", (), {"fts5": _BoomFTS5()})()
    assert _search_one_token(rt, "\u534e\u4e1c", top_k=30) == []


# ---------- 6.4.4 aggregation by (value, column_id) ----------

def test_aggregate_by_column_id_dedupes_identical_pairs():
    """Three hits for the same (value, column_id) collapse into one entry."""
    raw = [
        {"value": "\u534e\u4e1c", "column_id": "dim_region.region_name", "_token": "\u534e\u4e1c"},
        {"value": "\u534e\u4e1c", "column_id": "dim_region.region_name", "_token": "\u534e\u4e1c"},
        {"value": "\u534e\u4e1c", "column_id": "dim_region.region_name", "_token": "east"},
    ]
    out = _aggregate_by_column_id(raw)
    assert len(out) == 1
    entry = out[0]
    assert entry["value"] == "\u534e\u4e1c"
    assert entry["column_id"] == "dim_region.region_name"
    # _tokens should accumulate unique tokens
    assert set(entry["_tokens"]) == {"\u534e\u4e1c", "east"}


def test_aggregate_by_column_id_keeps_distinct_pairs():
    raw = [
        {"value": "\u534e\u4e1c", "column_id": "dim_region.region_name", "_token": "t1"},
        {"value": "\u534e\u5317", "column_id": "dim_region.region_name", "_token": "t1"},
        {"value": "\u624b\u673a", "column_id": "dim_product.category", "_token": "t2"},
    ]
    out = _aggregate_by_column_id(raw)
    assert len(out) == 3
    keys = {(e["value"], e["column_id"]) for e in out}
    assert ("\u534e\u4e1c", "dim_region.region_name") in keys
    assert ("\u534e\u5317", "dim_region.region_name") in keys
    assert ("\u624b\u673a", "dim_product.category") in keys


def test_aggregate_skips_entries_with_missing_value_or_column_id():
    raw = [
        {"value": None, "column_id": "x.y"},
        {"value": "v", "column_id": None},
        {"value": "ok", "column_id": "x.y", "_token": "t"},
    ]
    out = _aggregate_by_column_id(raw)
    assert len(out) == 1
    assert out[0]["value"] == "ok"


def test_aggregate_preserves_insertion_order():
    raw = [
        {"value": "b", "column_id": "x.y", "_token": "t"},
        {"value": "a", "column_id": "x.y", "_token": "t"},
    ]
    out = _aggregate_by_column_id(raw)
    assert [e["value"] for e in out] == ["b", "a"]


# ---------- 6.4.5 node end-to-end with stub FTS5 ----------

class _StubMetrics:
    def __init__(self):
        self.latencies = []
        self.llm_calls = []

    def record_node_latency(self, node, ms):
        self.latencies.append((node, ms))

    def record_llm_call(self, stat):
        self.llm_calls.append(stat)


class _StubRuntime:
    def __init__(self, fts5=None, allow_mysql_fallback=False):
        self.metrics = _StubMetrics()
        self.fts5 = fts5
        self.nodes_called = 0
        # Unit tests default to False so the live DW MySQL is never touched.
        self.allow_mysql_fallback = allow_mysql_fallback


def _cfg_with(runtime):
    cfg = RunnableConfig()
    cfg["configurable"] = {"runtime": runtime}
    return cfg


def _state(query="x", keywords=None):
    return {
        "query": query,
        "request_id": "rid-6-4",
        "node_history": [],
        "validate_attempts": 0,
        "keywords": keywords or [],
    }


def test_recall_value_writes_state_retrieved_values():
    rt = _StubRuntime(fts5=_StubFTS5())
    out = recall_value(_state(query="\u4e0a\u4e2a\u6708\u534e\u4e1c\u7684GMV"), _cfg_with(rt))
    assert "retrieved_values" in out
    assert isinstance(out["retrieved_values"], list)


def test_recall_value_finds_region_hits():
    rt = _StubRuntime(fts5=_StubFTS5())
    out = recall_value(_state(query="\u534e\u4e1c"), _cfg_with(rt))
    vals = out["retrieved_values"]
    assert len(vals) >= 1
    assert any(v["value"] == "\u534e\u4e1c" for v in vals)
    assert all("column_id" in v for v in vals)


def test_recall_value_tokenizes_query_via_jieba():
    """jieba tokenisation means the \u534e\u4e1c token triggers the FTS5 stub.

    jieba.lcut("\u534e\u4e1c\u624b\u673aGMV") -> [\u534e\u4e1c, \u624b\u673a, "GMV"]
    """
    rt = _StubRuntime(fts5=_StubFTS5())
    recall_value(_state(query="\u534e\u4e1c\u624b\u673aGMV"), _cfg_with(rt))
    tokens_used = [c[0] for c in rt.fts5.calls]
    assert "\u534e\u4e1c" in tokens_used
    assert "\u624b\u673a" in tokens_used


def test_recall_value_passes_topk_30_per_token():
    rt = _StubRuntime(fts5=_StubFTS5())
    recall_value(_state(query="\u534e\u4e1c \u624b\u673a"), _cfg_with(rt))
    for _tok, top_k in rt.fts5.calls:
        assert top_k == 30


def test_recall_value_records_node_latency_and_counter():
    rt = _StubRuntime(fts5=_StubFTS5())
    recall_value(_state(query="\u534e\u4e1c"), _cfg_with(rt))
    assert rt.nodes_called == 1
    nodes = [n for n, _ in rt.metrics.latencies]
    assert "recall_value" in nodes


def test_recall_value_node_history_entry_records_token_count():
    rt = _StubRuntime(fts5=_StubFTS5())
    out = recall_value(_state(query="\u4e0a\u4e2a\u6708\u534e\u4e1c\u7684GMV"), _cfg_with(rt))
    nh = out["node_history"][-1]
    assert nh["node"] == "recall_value"
    assert nh["status"] == "ok"
    assert nh["tokens"] >= 1
    # history_append stores latency as `ms`
    assert "ms" in nh
    assert nh["ms"] >= 0


def test_recall_value_no_fts5_returns_empty_list():
    rt = _StubRuntime(fts5=None)
    out = recall_value(_state(query="\u534e\u4e1c"), _cfg_with(rt))
    assert out["retrieved_values"] == []


def test_recall_value_no_query_returns_empty_list():
    rt = _StubRuntime(fts5=_StubFTS5())
    out = recall_value(_state(query=""), _cfg_with(rt))
    assert out["retrieved_values"] == []


def test_recall_value_query_with_only_stop_words_returns_empty():
    rt = _StubRuntime(fts5=_StubFTS5())
    out = recall_value(_state(query="  的 帮我 查询 "), _cfg_with(rt))
    assert out["retrieved_values"] == []


def test_recall_value_aggregates_hits_from_multiple_tokens():
    """Two tokens, each producing hits, get merged and deduped."""
    rt = _StubRuntime(fts5=_StubFTS5())
    out = recall_value(_state(query="\u534e\u4e1c \u624b\u673a"), _cfg_with(rt))
    cols = {v["column_id"] for v in out["retrieved_values"]}
    assert "dim_region.region_name" in cols
    assert "dim_product.category" in cols


def test_recall_value_returns_canonical_state_shape():
    rt = _StubRuntime(fts5=_StubFTS5())
    out = recall_value(_state(query="\u534e\u4e1c"), _cfg_with(rt))
    assert set(out.keys()) == {"retrieved_values", "node_history"}
    assert isinstance(out["node_history"], list)


def test_recall_value_each_entry_has_value_and_column_id_keys():
    """SRS canonical shape requires both keys."""
    rt = _StubRuntime(fts5=_StubFTS5())
    out = recall_value(_state(query="\u534e\u4e1c"), _cfg_with(rt))
    for entry in out["retrieved_values"]:
        assert "value" in entry
        assert "column_id" in entry


def test_recall_value_handles_fts5_exception():
    class _BoomFTS5:
        def search(self, *a, **k):
            raise RuntimeError("fts5 down")

    rt = _StubRuntime(fts5=_BoomFTS5())
    # Should not raise
    out = recall_value(_state(query="\u534e\u4e1c"), _cfg_with(rt))
    # empty since both FTS5 failed AND MySQL fallback also failed (no MySQL in unit test)
    assert out["retrieved_values"] == []