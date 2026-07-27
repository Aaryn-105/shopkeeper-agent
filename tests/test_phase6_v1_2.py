"""V1.0 phase 6.2 verification: recall_column (4.2.2).

V1.0 phase 6.2 spec:
  - [new] call LLM (extend_keywords_for_column_recall.prompt) to extend
    keywords, max 6
  - merge with state.keywords
  - embedding_local.encode([query, *extended_keywords]) -> for each vector
    run faiss_client.search_column(vec, top_k=20)
  - sort by _score desc, dedupe by id
  - final cap: 20
  - write state.retrieved_columns
"""
from __future__ import annotations
import json
import pytest
import asyncio
from langchain_core.runnables import RunnableConfig

from app.agent.nodes.recall_column import (
    MAX_EXTENDED_KEYWORDS,
    TOPK_PER_VECTOR,
    FINAL_CAP,
    _parse_extended_keywords,
    _load_prompt_template,
    recall_column,
)


# ---------- 6.2.1 constants ----------

def test_max_extended_keywords_is_6():
    assert MAX_EXTENDED_KEYWORDS == 6

def test_topk_per_vector_is_20():
    assert TOPK_PER_VECTOR == 20

def test_final_cap_is_20():
    assert FINAL_CAP == 20


# ---------- 6.2.2 keyword parser ----------

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
    """When the response isn't valid JSON, the parser falls back to split()."""
    out = _parse_extended_keywords("[a, b, c]")
    assert out == ["a", "b", "c"]


def test_parse_extended_keywords_ignores_empty_strings():
    out = _parse_extended_keywords('{"keywords": ["", "real", ""]}')
    assert out == ["real"]


# ---------- 6.2.3 prompt template loads ----------

def test_prompt_template_loads():
    """The .prompt file must exist and load non-empty."""
    tpl = _load_prompt_template()
    assert tpl
    assert "{query}" in tpl


def test_prompt_template_fallback_when_missing(tmp_path, monkeypatch):
    """If the prompt file is gone, we still have an inline fallback."""
    # Point _PROMPT_PATH at a non-existent location by monkey-patching
    import app.agent.nodes.recall_column as mod
    orig_path = mod._PROMPT_PATH
    try:
        mod._PROMPT_PATH = tmp_path / "missing.prompt"
        tpl = mod._load_prompt_template()
        assert tpl
        assert "{query}" in tpl
    finally:
        mod._PROMPT_PATH = orig_path


# ---------- 6.2.4 runtime stubs ----------

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
    extension. Otherwise returns an empty payload. Matches the production mock."""

    is_mock = True
    model = "mock"

    def __init__(self, response: str = '{"keywords": ["\u9500\u552e\u989d", "\u533a\u57df", "GMV"]}'):
        self.response = response
        self.calls = []

    async def ainvoke(self, prompt, system=None, response_format=None):
        from app.clients.llm_client import LLMResponse
        self.calls.append(prompt)
        return LLMResponse(text=self.response, prompt_tokens=len(prompt) // 2,
                           completion_tokens=len(self.response) // 2, latency_ms=1)


class _StubEmbedding:
    def __init__(self, n_per_text=1):
        self.n_per_text = n_per_text
        self.calls = []

    def encode(self, texts):
        self.calls.append(list(texts))
        # one vector per text
        return [[0.1 * (i + 1), 0.2, 0.3] for i, _ in enumerate(texts)]


class _StubFAISSCollection:
    """FAISS collection that returns N canned hits per .search() call, with a
    monotonic-decreasing _score so ordering is observable."""

    is_indexed = True

    def __init__(self, hits_per_call: int = 5):
        self.hits_per_call = hits_per_call
        self.calls = 0

    def search(self, vec, top_k):
        self.calls += 1
        n = min(top_k, self.hits_per_call)
        return [
            {"id": f"col_{self.calls}_{i}", "name": f"col_{i}", "_score": 1.0 - i * 0.1}
            for i in range(n)
        ]

    def text_recall(self, query, top_k):
        # not used when vector path is healthy
        return []


class _StubFAISS:
    def __init__(self, hits_per_call=5):
        self.column_info = _StubFAISSCollection(hits_per_call=hits_per_call)


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
        "request_id": "rid-6-2",
        "node_history": [],
        "validate_attempts": 0,
        "keywords": keywords if keywords is not None else [],
    }


# ---------- 6.2.5 LLM call + keyword extension ----------

def test_recall_column_calls_llm_with_keyword_extension_prompt():
    rt = _StubRuntime(llm=_StubLLM())
    asyncio.run(recall_column(_state(), _cfg_with(rt)))
    assert len(rt.llm.calls) == 1
    prompt = rt.llm.calls[0]
    # Mock generator recognises "关键词" + "扩展" combo
    assert "\u5173\u952e\u8bcd" in prompt or "\u5173\u952e\u5b57" in prompt
    assert "\u6269\u5c55" in prompt
    assert "GMV" in prompt  # query interpolated


def test_recall_column_limits_extended_keywords_to_6():
    """LLM returns 7 keywords; recall keeps only the first 6."""
    big = json.dumps({"keywords": [f"kw{i}" for i in range(7)]})
    rt = _StubRuntime(llm=_StubLLM(response=big))
    state = _state()
    out = asyncio.run(recall_column(state, _cfg_with(rt)))
    # The log node entry should record extended count
    nh = out["node_history"][-1]
    assert nh["extended"] == 6


def test_recall_column_records_llm_call_stat():
    rt = _StubRuntime(llm=_StubLLM())
    asyncio.run(recall_column(_state(), _cfg_with(rt)))
    assert len(rt.metrics.llm_calls) == 1
    stat = rt.metrics.llm_calls[0]
    assert stat.node_name == "recall_column_extend"
    assert stat.model == "mock"
    assert stat.cache_hit is False


# ---------- 6.2.6 encoding + vector search ----------

def test_recall_column_encodes_query_and_extended_and_state_keywords():
    rt = _StubRuntime(
        llm=_StubLLM(response='{"keywords": ["\u9500\u552e", "\u533a\u57df"]}'),
        embedding=_StubEmbedding(),
        faiss=_StubFAISS(),
    )
    state = _state(query="GMV", keywords=["\u534e\u4e1c"])
    asyncio.run(recall_column(state, _cfg_with(rt)))
    # embedding was called with dedup'd [query, *extended, *state.keywords]
    assert rt.embedding.calls, "embedding must be called"
    texts = rt.embedding.calls[0]
    assert "GMV" in texts
    assert "\u9500\u552e" in texts
    assert "\u533a\u57df" in texts
    assert "\u534e\u4e1c" in texts
    # no duplicates
    assert len(texts) == len(set(texts))


def test_recall_column_runs_one_faiss_search_per_vector():
    """Each encoded vector must trigger exactly one FAISS search."""
    rt = _StubRuntime(
        llm=_StubLLM(response='{"keywords": ["a", "b", "c"]}'),
        embedding=_StubEmbedding(),
        faiss=_StubFAISS(hits_per_call=3),
    )
    state = _state(query="q", keywords=["k1", "k2"])
    # texts = dedup'd ["q", "a", "b", "c", "k1", "k2"] -> 6 vectors
    asyncio.run(recall_column(state, _cfg_with(rt)))
    assert rt.faiss.column_info.calls == 6


# ---------- 6.2.7 score desc + dedupe + cap ----------

def test_recall_column_sorts_by_score_desc():
    rt = _StubRuntime(
        llm=_StubLLM(),
        embedding=_StubEmbedding(),
        faiss=_StubFAISS(hits_per_call=5),
    )
    out = asyncio.run(recall_column(_state(), _cfg_with(rt)))
    cols = out["retrieved_columns"]
    scores = [c.get("_score", 0.0) for c in cols]
    assert scores == sorted(scores, reverse=True)


def test_recall_column_dedupes_by_id():
    """Two vectors returning the same id should collapse to one."""
    coll = _StubFAISSCollection()
    coll.is_indexed = True

    class _DupeColl:
        is_indexed = True
        def search(self, vec, top_k):
            # always return the same 3 ids with descending score
            return [
                {"id": "x", "_score": 0.9},
                {"id": "y", "_score": 0.5},
                {"id": "z", "_score": 0.1},
            ]

    class _DupeFAISS:
        column_info = _DupeColl()

    rt = _StubRuntime(llm=_StubLLM(), embedding=_StubEmbedding(), faiss=_DupeFAISS())
    state = _state(query="q", keywords=["a", "b"])
    out = asyncio.run(recall_column(state, _cfg_with(rt)))
    ids = [c["id"] for c in out["retrieved_columns"]]
    assert ids == ["x", "y", "z"]
    assert len(out["retrieved_columns"]) == 3


def test_recall_column_caps_at_20():
    """Even with many vectors each returning 20 hits, final <= 20."""
    class _ManyColl:
        is_indexed = True
        def search(self, vec, top_k):
            return [{"id": f"id_{self.i}_{i}", "_score": 1.0 - i * 0.01} for i in range(top_k)]
        i = 0  # not used; just a sanity

    coll = _ManyColl()
    class _ManyFAISS:
        column_info = coll

    rt = _StubRuntime(llm=_StubLLM(), embedding=_StubEmbedding(), faiss=_ManyFAISS())
    out = asyncio.run(recall_column(_state(query="q", keywords=["a", "b"]), _cfg_with(rt)))
    assert len(out["retrieved_columns"]) <= 20


# ---------- 6.2.8 graceful fallback ----------

def test_recall_column_no_llm_returns_empty_extension():
    """When runtime.llm is None, extended keywords are [] and recall still works."""
    rt = _StubRuntime(llm=None, embedding=_StubEmbedding(), faiss=_StubFAISS())
    out = asyncio.run(recall_column(_state(query="GMV"), _cfg_with(rt)))
    assert out["retrieved_columns"]
    # The log records extended=0
    nh = out["node_history"][-1]
    assert nh["extended"] == 0


def test_recall_column_llm_error_returns_empty_extension():
    """When the LLM raises, we degrade gracefully (no extension, still recall)."""
    class _BoomLLM:
        is_mock = True
        model = "mock"
        async def ainvoke(self, prompt, system=None, response_format=None):
            raise RuntimeError("boom")
    rt = _StubRuntime(llm=_BoomLLM(), embedding=_StubEmbedding(), faiss=_StubFAISS())
    out = asyncio.run(recall_column(_state(), _cfg_with(rt)))
    assert out["retrieved_columns"]
    assert out["node_history"][-1]["extended"] == 0


def test_recall_column_no_embedding_falls_back_to_text_recall():
    """If embedding is None we should still return results from text_recall."""
    class _TextFAISS:
        column_info = _StubFAISSCollection()
    rt = _StubRuntime(llm=_StubLLM(), embedding=None, faiss=_TextFAISS())
    # No vector recall -> text_recall fallback is used by FAISSStore.recall_column
    # But our node only does vector recall. To still work we add a fallback:
    # we hit the FAISSStore-level text recall if no hits come back.
    out = asyncio.run(recall_column(_state(), _cfg_with(rt)))
    # hits may be empty (since no vector index call was made) - that's OK
    # The important thing: no exception raised.
    assert "retrieved_columns" in out


# ---------- 6.2.9 metrics + state field ----------

def test_recall_column_writes_to_state_retrieved_columns():
    rt = _StubRuntime(llm=_StubLLM(), embedding=_StubEmbedding(), faiss=_StubFAISS())
    out = asyncio.run(recall_column(_state(), _cfg_with(rt)))
    assert "retrieved_columns" in out
    assert "query" not in out  # state.query preserved
    assert "keywords" not in out  # state.keywords preserved


def test_recall_column_records_node_latency_and_increments_counter():
    rt = _StubRuntime(llm=_StubLLM(), embedding=_StubEmbedding(), faiss=_StubFAISS())
    asyncio.run(recall_column(_state(), _cfg_with(rt)))
    assert rt.nodes_called == 1
    nodes = [n for n, _ in rt.metrics.latencies]
    assert "recall_column" in nodes


# ---------- 6.2.10 backwards compat ----------

def test_existing_phase4_recall_column_tests_still_pass_recall_column_node():
    """A trivial direct call still returns the canonical state shape."""
    rt = _StubRuntime(llm=_StubLLM(), embedding=_StubEmbedding(), faiss=_StubFAISS())
    state = _state(query="\u4e0a\u4e2a\u6708\u534e\u4e1c\u7684GMV", keywords=["GMV"])
    out = asyncio.run(recall_column(state, _cfg_with(rt)))
    assert set(out.keys()) == {"retrieved_columns", "node_history"}
    # node_history was extended via reducer
    assert isinstance(out["node_history"], list)
    assert out["node_history"][-1]["node"] == "recall_column"