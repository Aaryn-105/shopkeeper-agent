"""V1.0 phase 6.3 verification: recall_metric (4.2.3).

V1.0 phase 6.3 spec:
  - [new] call LLM (extend_keywords_for_metric_recall.prompt) to extend
    keywords, max 6
  - merge with state.keywords
  - embedding_local.encode([query, *extended_keywords]) -> for each vector
    run faiss_client.search_metric(vec, top_k=10)
  - sort by _score desc, dedupe by id
  - final cap: 10
  - write state.retrieved_metrics
"""
from __future__ import annotations
import json
import pytest
import asyncio
from langchain_core.runnables import RunnableConfig

from app.agent.nodes.recall_metric import (
    MAX_EXTENDED_KEYWORDS,
    TOPK_PER_VECTOR,
    FINAL_CAP,
    _parse_extended_keywords,
    _load_prompt_template,
    recall_metric,
)


# ---------- 6.3.1 constants ----------

def test_max_extended_keywords_is_6():
    assert MAX_EXTENDED_KEYWORDS == 6

def test_topk_per_vector_is_10():
    assert TOPK_PER_VECTOR == 10

def test_final_cap_is_10():
    assert FINAL_CAP == 10


# ---------- 6.3.2 keyword parser (mirrors 6.2 behaviour) ----------

@pytest.mark.parametrize("text,expected", [
    ('{"keywords": ["a", "b", "c"]}', ["a", "b", "c"]),
    ('["a", "b"]', ["a", "b"]),
    ('a, b, c', ["a", "b", "c"]),
    ('a b c', ["a", "b", "c"]),
    ('{"extended": ["x", "y"]}', ["x", "y"]),
    ('', []),
])
def test_parse_extended_keywords_handles_canonical_shapes(text, expected):
    assert _parse_extended_keywords(text) == expected


def test_parse_extended_keywords_strips_json_brackets_when_invalid_json():
    out = _parse_extended_keywords("[a, b, c]")
    assert out == ["a", "b", "c"]


def test_parse_extended_keywords_ignores_empty_strings():
    out = _parse_extended_keywords('{"keywords": ["", "real", ""]}')
    assert out == ["real"]


# ---------- 6.3.3 prompt template loads ----------

def test_prompt_template_loads():
    """The .prompt file must exist and load non-empty."""
    tpl = _load_prompt_template()
    assert tpl
    assert "{query}" in tpl


def test_prompt_template_fallback_when_missing(tmp_path, monkeypatch):
    """If the prompt file is gone, we still have an inline fallback."""
    import app.agent.nodes.recall_metric as mod
    orig_path = mod._PROMPT_PATH
    try:
        mod._PROMPT_PATH = tmp_path / "missing.prompt"
        tpl = mod._load_prompt_template()
        assert tpl
        assert "{query}" in tpl
    finally:
        mod._PROMPT_PATH = orig_path


def test_metric_prompt_distinct_from_column_prompt():
    """Metric prompt focuses on aggregate metrics (GMV / 销售额 / AOV) per SRS 4.2.3."""
    tpl = _load_prompt_template()
    assert "\u6307\u6807" in tpl or "metric" in tpl.lower()
    assert "GMV" in tpl or "\u9500\u552e\u989d" in tpl


# ---------- 6.3.4 runtime stubs ----------

class _StubMetrics:
    def __init__(self):
        self.latencies = []
        self.llm_calls = []

    def record_node_latency(self, node, ms):
        self.latencies.append((node, ms))

    def record_llm_call(self, stat):
        self.llm_calls.append(stat)


class _StubLLM:
    """LLM that returns a canned keyword JSON when the prompt mentions keyword
    extension. Matches the production mock."""

    is_mock = True
    model = "mock"

    def __init__(self, response: str = '{"keywords": ["\u9500\u552e\u989d", "GMV", "AOV"]}'):
        self.response = response
        self.calls = []

    async def ainvoke(self, prompt, system=None, response_format=None):
        from app.clients.llm_client import LLMResponse
        self.calls.append(prompt)
        return LLMResponse(text=self.response, prompt_tokens=len(prompt) // 2,
                           completion_tokens=len(self.response) // 2, latency_ms=1)


class _StubEmbedding:
    def __init__(self):
        self.calls = []

    def encode(self, texts):
        self.calls.append(list(texts))
        return [[0.1 * (i + 1), 0.2, 0.3] for i, _ in enumerate(texts)]


class _StubFAISSCollection:
    """FAISS collection that returns N canned hits per .search() call."""

    is_indexed = True

    def __init__(self, hits_per_call: int = 5):
        self.hits_per_call = hits_per_call
        self.calls = 0

    def search(self, vec, top_k):
        self.calls += 1
        n = min(top_k, self.hits_per_call)
        return [
            {"id": f"met_{self.calls}_{i}", "name": f"met_{i}", "_score": 1.0 - i * 0.1}
            for i in range(n)
        ]


class _StubFAISS:
    def __init__(self, hits_per_call=5):
        self.metric_info = _StubFAISSCollection(hits_per_call=hits_per_call)


class _StubRuntime:
    def __init__(self, llm=None, embedding=None, faiss=None):
        self.metrics = _StubMetrics()
        self.llm = llm
        self.embedding = embedding
        self.faiss = faiss
        self.nodes_called = 0


def _cfg_with(runtime):
    cfg = RunnableConfig()
    cfg["configurable"] = {"runtime": runtime}
    return cfg


def _state(query="GMV", keywords=None):
    return {
        "query": query,
        "request_id": "rid-6-3",
        "node_history": [],
        "validate_attempts": 0,
        "keywords": keywords if keywords is not None else [],
    }


# ---------- 6.3.5 LLM call + keyword extension ----------

def test_recall_metric_calls_llm_with_metric_keyword_extension_prompt():
    rt = _StubRuntime(llm=_StubLLM())
    asyncio.run(recall_metric(_state(), _cfg_with(rt)))
    assert len(rt.llm.calls) == 1
    prompt = rt.llm.calls[0]
    assert "\u5173\u952e\u8bcd" in prompt or "\u5173\u952e\u5b57" in prompt
    assert "\u6269\u5c55" in prompt
    assert "GMV" in prompt


def test_recall_metric_limits_extended_keywords_to_6():
    big = json.dumps({"keywords": [f"kw{i}" for i in range(7)]})
    rt = _StubRuntime(llm=_StubLLM(response=big))
    out = asyncio.run(recall_metric(_state(), _cfg_with(rt)))
    nh = out["node_history"][-1]
    assert nh["extended"] == 6


def test_recall_metric_records_llm_call_stat():
    rt = _StubRuntime(llm=_StubLLM())
    asyncio.run(recall_metric(_state(), _cfg_with(rt)))
    assert len(rt.metrics.llm_calls) == 1
    stat = rt.metrics.llm_calls[0]
    assert stat.node_name == "recall_metric_extend"
    assert stat.model == "mock"
    assert stat.cache_hit is False


# ---------- 6.3.6 encoding + vector search ----------

def test_recall_metric_encodes_query_and_extended_and_state_keywords():
    rt = _StubRuntime(
        llm=_StubLLM(response='{"keywords": ["\u9500\u552e", "\u8425\u6536"]}'),
        embedding=_StubEmbedding(),
        faiss=_StubFAISS(),
    )
    state = _state(query="GMV", keywords=["\u534e\u4e1c"])
    asyncio.run(recall_metric(state, _cfg_with(rt)))
    assert rt.embedding.calls, "embedding must be called"
    texts = rt.embedding.calls[0]
    assert "GMV" in texts
    assert "\u9500\u552e" in texts
    assert "\u8425\u6536" in texts
    assert "\u534e\u4e1c" in texts
    assert len(texts) == len(set(texts))


def test_recall_metric_runs_one_faiss_search_per_vector():
    rt = _StubRuntime(
        llm=_StubLLM(response='{"keywords": ["a", "b", "c"]}'),
        embedding=_StubEmbedding(),
        faiss=_StubFAISS(hits_per_call=3),
    )
    state = _state(query="q", keywords=["k1", "k2"])
    asyncio.run(recall_metric(state, _cfg_with(rt)))
    # texts = dedup'd ["q", "a", "b", "c", "k1", "k2"] -> 6 vectors
    assert rt.faiss.metric_info.calls == 6


def test_recall_metric_uses_metric_info_not_column_info():
    """Metric recall must hit metric_info; column_info is for recall_column."""
    rt = _StubRuntime(llm=_StubLLM(), embedding=_StubEmbedding(), faiss=_StubFAISS())
    asyncio.run(recall_metric(_state(), _cfg_with(rt)))
    assert rt.faiss.metric_info.calls >= 1


# ---------- 6.3.7 score desc + dedupe + cap ----------

def test_recall_metric_sorts_by_score_desc():
    rt = _StubRuntime(
        llm=_StubLLM(),
        embedding=_StubEmbedding(),
        faiss=_StubFAISS(hits_per_call=5),
    )
    out = asyncio.run(recall_metric(_state(), _cfg_with(rt)))
    mets = out["retrieved_metrics"]
    scores = [m.get("_score", 0.0) for m in mets]
    assert scores == sorted(scores, reverse=True)


def test_recall_metric_dedupes_by_id():
    class _DupeColl:
        is_indexed = True
        def search(self, vec, top_k):
            return [
                {"id": "GMV", "_score": 0.9},
                {"id": "AOV", "_score": 0.5},
                {"id": "ORDER_CNT", "_score": 0.1},
            ]

    class _DupeFAISS:
        metric_info = _DupeColl()

    rt = _StubRuntime(llm=_StubLLM(), embedding=_StubEmbedding(), faiss=_DupeFAISS())
    state = _state(query="q", keywords=["a", "b"])
    out = asyncio.run(recall_metric(state, _cfg_with(rt)))
    ids = [m["id"] for m in out["retrieved_metrics"]]
    assert ids == ["GMV", "AOV", "ORDER_CNT"]
    assert len(out["retrieved_metrics"]) == 3


def test_recall_metric_caps_at_10():
    """Even with many vectors each returning 10 hits, final <= 10."""
    class _ManyColl:
        is_indexed = True
        def search(self, vec, top_k):
            return [{"id": f"id_{i}", "_score": 1.0 - i * 0.01} for i in range(top_k)]

    class _ManyFAISS:
        metric_info = _ManyColl()

    rt = _StubRuntime(llm=_StubLLM(), embedding=_StubEmbedding(), faiss=_ManyFAISS())
    out = asyncio.run(recall_metric(_state(query="q", keywords=["a", "b"]), _cfg_with(rt)))
    assert len(out["retrieved_metrics"]) <= 10


# ---------- 6.3.8 graceful fallback ----------

def test_recall_metric_no_llm_returns_empty_extension():
    rt = _StubRuntime(llm=None, embedding=_StubEmbedding(), faiss=_StubFAISS())
    out = asyncio.run(recall_metric(_state(query="GMV"), _cfg_with(rt)))
    assert out["retrieved_metrics"]
    nh = out["node_history"][-1]
    assert nh["extended"] == 0


def test_recall_metric_llm_error_returns_empty_extension():
    class _BoomLLM:
        is_mock = True
        model = "mock"
        async def ainvoke(self, prompt, system=None, response_format=None):
            raise RuntimeError("boom")

    rt = _StubRuntime(llm=_BoomLLM(), embedding=_StubEmbedding(), faiss=_StubFAISS())
    out = asyncio.run(recall_metric(_state(), _cfg_with(rt)))
    assert out["retrieved_metrics"]
    assert out["node_history"][-1]["extended"] == 0


def test_recall_metric_no_embedding_returns_empty_hits_without_raising():
    rt = _StubRuntime(llm=_StubLLM(), embedding=None, faiss=_StubFAISS())
    out = asyncio.run(recall_metric(_state(), _cfg_with(rt)))
    assert "retrieved_metrics" in out


def test_recall_metric_no_faiss_returns_empty_hits():
    rt = _StubRuntime(llm=_StubLLM(), embedding=_StubEmbedding(), faiss=None)
    out = asyncio.run(recall_metric(_state(), _cfg_with(rt)))
    assert out["retrieved_metrics"] == []


# ---------- 6.3.9 metrics + state field ----------

def test_recall_metric_writes_to_state_retrieved_metrics():
    rt = _StubRuntime(llm=_StubLLM(), embedding=_StubEmbedding(), faiss=_StubFAISS())
    out = asyncio.run(recall_metric(_state(), _cfg_with(rt)))
    assert "retrieved_metrics" in out
    assert "query" not in out
    assert "keywords" not in out


def test_recall_metric_records_node_latency_and_increments_counter():
    rt = _StubRuntime(llm=_StubLLM(), embedding=_StubEmbedding(), faiss=_StubFAISS())
    asyncio.run(recall_metric(_state(), _cfg_with(rt)))
    assert rt.nodes_called == 1
    nodes = [n for n, _ in rt.metrics.latencies]
    assert "recall_metric" in nodes


# ---------- 6.3.10 backwards compat ----------

def test_recall_metric_returns_canonical_state_shape():
    rt = _StubRuntime(llm=_StubLLM(), embedding=_StubEmbedding(), faiss=_StubFAISS())
    state = _state(query="\u4e0a\u4e2a\u6708\u534e\u4e1c\u7684GMV", keywords=["GMV"])
    out = asyncio.run(recall_metric(state, _cfg_with(rt)))
    assert set(out.keys()) == {"retrieved_metrics", "node_history"}
    assert isinstance(out["node_history"], list)
    assert out["node_history"][-1]["node"] == "recall_metric"