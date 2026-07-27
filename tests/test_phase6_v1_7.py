"""V1.0 phase 6.7 verification: filter_metric (4.2.7).

V1.0 phase 6.7 spec:
  - Load filter_metric_info.prompt.
  - LLM returns {"keep_metric_ids": [...]}.
  - Filter metric_infos accordingly; fallback to full list when no guidance.
  - Write state.filtered_metric_infos.
"""
from __future__ import annotations
import asyncio
import json
import pytest
from langchain_core.runnables import RunnableConfig

from app.agent.nodes.filter_metric import (
    _load_prompt_template,
    _serialize_metric_infos,
    _parse_keep_response,
    _match_metric_by_id,
    filter_metric,
)


# ---------- 6.7.1 helpers ----------

@pytest.mark.parametrize("text,expected", [
    ('{"keep_metric_ids": ["GMV", "ORDER_CNT"]}', ["GMV", "ORDER_CNT"]),
    ('{"keep_metrics": ["GMV"]}', ["GMV"]),
    ('["GMV", "AOV"]', ["GMV", "AOV"]),
    ('GMV, AOV', ["GMV", "AOV"]),
    ('GMV AOV', ["GMV", "AOV"]),
    ('', []),
    ('garbage', ["garbage"]),
])
def test_parse_keep_response_handles_canonical_shapes(text, expected):
    assert _parse_keep_response(text) == expected


def test_serialize_metric_infos_returns_string():
    out = _serialize_metric_infos([{"id": "GMV", "name": "GMV"}])
    assert isinstance(out, str)
    assert "GMV" in out


def test_prompt_template_loads():
    tpl = _load_prompt_template()
    assert tpl
    assert "{query}" in tpl
    assert "{metric_infos}" in tpl


def test_prompt_template_fallback_when_missing(tmp_path, monkeypatch):
    import app.agent.nodes.filter_metric as mod
    orig = mod._PROMPT_PATH
    try:
        mod._PROMPT_PATH = tmp_path / "missing.prompt"
        tpl = mod._load_prompt_template()
        assert tpl
        assert "{query}" in tpl
    finally:
        mod._PROMPT_PATH = orig


def test_prompt_template_escapes_braces_in_examples():
    """JSON literal in the prompt must use {{ }} so .format() does not crash."""
    tpl = _load_prompt_template()
    assert "{{" in tpl and "}}" in tpl


def test_match_metric_by_id_matches_id():
    assert _match_metric_by_id({"id": "GMV", "name": "GMV"}, "GMV")


def test_match_metric_by_id_matches_alias():
    assert _match_metric_by_id({"id": "GMV", "alias": ["\u9500\u552e\u989d"]}, "\u9500\u552e\u989d")


def test_match_metric_by_id_returns_false_when_no_match():
    assert not _match_metric_by_id({"id": "GMV", "alias": []}, "ORDER_CNT")


def test_match_metric_by_id_handles_missing_alias_key():
    assert not _match_metric_by_id({"id": "GMV"}, "ORDER_CNT")


# ---------- 6.7.2 runtime stubs ----------

class _StubMetrics:
    def __init__(self):
        self.latencies = []
        self.llm_calls = []

    def record_node_latency(self, node, ms):
        self.latencies.append((node, ms))

    def record_llm_call(self, stat):
        self.llm_calls.append(stat)


class _StubLLM:
    is_mock = True
    model = "mock"

    def __init__(self, response: str = '{"keep_metric_ids": []}'):
        self.response = response
        self.calls = []

    async def ainvoke(self, prompt, system=None, response_format=None):
        from app.clients.llm_client import LLMResponse
        self.calls.append(prompt)
        return LLMResponse(text=self.response, prompt_tokens=len(prompt) // 2,
                           completion_tokens=len(self.response) // 2, latency_ms=1)


class _StubRuntime:
    def __init__(self, llm=None):
        self.metrics = _StubMetrics()
        self.llm = llm
        self.nodes_called = 0


def _cfg_with(runtime):
    cfg = RunnableConfig()
    cfg["configurable"] = {"runtime": runtime}
    return cfg


def _state(query="x", metric_infos=None, retrieved_metrics=None):
    return {
        "query": query,
        "request_id": "rid-6-7",
        "node_history": [],
        "validate_attempts": 0,
        "metric_infos": metric_infos or [],
        "retrieved_metrics": retrieved_metrics or [],
    }


def _metric(mid, name=None, alias=None):
    return {
        "id": mid,
        "name": name or mid,
        "description": f"desc for {mid}",
        "related_columns": ["fact_order.x"],
        "alias": alias or [],
    }


# ---------- 6.7.3 node behaviour ----------

def test_filter_metric_writes_filtered_metric_infos():
    rt = _StubRuntime(llm=_StubLLM())
    out = asyncio.run(filter_metric(
        _state(metric_infos=[_metric("GMV"), _metric("AOV")]),
        _cfg_with(rt),
    ))
    assert "filtered_metric_infos" in out
    assert isinstance(out["filtered_metric_infos"], list)


def test_filter_metric_no_llm_keeps_everything():
    rt = _StubRuntime(llm=None)
    metrics = [_metric("GMV"), _metric("AOV")]
    out = asyncio.run(filter_metric(
        _state(metric_infos=metrics),
        _cfg_with(rt),
    ))
    assert out["filtered_metric_infos"] == metrics


def test_filter_metric_empty_keep_ids_keeps_everything():
    rt = _StubRuntime(llm=_StubLLM(response='{"keep_metric_ids": []}'))
    metrics = [_metric("GMV"), _metric("AOV")]
    out = asyncio.run(filter_metric(
        _state(metric_infos=metrics),
        _cfg_with(rt),
    ))
    assert out["filtered_metric_infos"] == metrics


def test_filter_metric_keeps_only_listed_metrics():
    response = json.dumps({"keep_metric_ids": ["GMV"]})
    rt = _StubRuntime(llm=_StubLLM(response=response))
    metrics = [_metric("GMV"), _metric("AOV"), _metric("ORDER_CNT")]
    out = asyncio.run(filter_metric(
        _state(metric_infos=metrics),
        _cfg_with(rt),
    ))
    ids = {m["id"] for m in out["filtered_metric_infos"]}
    assert ids == {"GMV"}


def test_filter_metric_supports_multi_metric_keep():
    """SRS 4.2.7 rule 5: a query may need multiple metrics."""
    response = json.dumps({"keep_metric_ids": ["GMV", "ORDER_CNT"]})
    rt = _StubRuntime(llm=_StubLLM(response=response))
    metrics = [_metric("GMV"), _metric("AOV"), _metric("ORDER_CNT")]
    out = asyncio.run(filter_metric(
        _state(metric_infos=metrics),
        _cfg_with(rt),
    ))
    ids = {m["id"] for m in out["filtered_metric_infos"]}
    assert ids == {"GMV", "ORDER_CNT"}


def test_filter_metric_matches_by_alias():
    response = json.dumps({"keep_metric_ids": ["\u9500\u552e\u989d"]})
    rt = _StubRuntime(llm=_StubLLM(response=response))
    metrics = [_metric("GMV", alias=["\u9500\u552e\u989d"]), _metric("AOV")]
    out = asyncio.run(filter_metric(
        _state(metric_infos=metrics),
        _cfg_with(rt),
    ))
    ids = {m["id"] for m in out["filtered_metric_infos"]}
    assert ids == {"GMV"}


def test_filter_metric_matches_by_name():
    response = json.dumps({"keep_metric_ids": ["GMV"]})
    rt = _StubRuntime(llm=_StubLLM(response=response))
    metrics = [{"id": "x", "name": "GMV", "alias": []}, _metric("AOV")]
    out = asyncio.run(filter_metric(
        _state(metric_infos=metrics),
        _cfg_with(rt),
    ))
    ids = {m["id"] for m in out["filtered_metric_infos"]}
    assert ids == {"x"}


def test_filter_metric_falls_back_when_no_match():
    """If keep_ids match nothing, preserve the full list (don\'t lose data)."""
    response = json.dumps({"keep_metric_ids": ["NONEXISTENT"]})
    rt = _StubRuntime(llm=_StubLLM(response=response))
    metrics = [_metric("GMV"), _metric("AOV")]
    out = asyncio.run(filter_metric(
        _state(metric_infos=metrics),
        _cfg_with(rt),
    ))
    ids = {m["id"] for m in out["filtered_metric_infos"]}
    assert ids == {"GMV", "AOV"}


def test_filter_metric_handles_garbage_llm_response():
    """Garbage JSON -> empty keep_ids -> keep everything."""
    rt = _StubRuntime(llm=_StubLLM(response="totally not json"))
    metrics = [_metric("GMV"), _metric("AOV")]
    out = asyncio.run(filter_metric(
        _state(metric_infos=metrics),
        _cfg_with(rt),
    ))
    assert out["filtered_metric_infos"] == metrics


def test_filter_metric_handles_llm_exception():
    class _BoomLLM:
        is_mock = True
        model = "mock"
        async def ainvoke(self, prompt, system=None, response_format=None):
            raise RuntimeError("llm down")

    rt = _StubRuntime(llm=_BoomLLM())
    metrics = [_metric("GMV"), _metric("AOV")]
    out = asyncio.run(filter_metric(
        _state(metric_infos=metrics),
        _cfg_with(rt),
    ))
    assert out["filtered_metric_infos"] == metrics


def test_filter_metric_falls_back_to_retrieved_metrics():
    """When metric_infos is empty, fall back to retrieved_metrics."""
    response = json.dumps({"keep_metric_ids": ["GMV"]})
    rt = _StubRuntime(llm=_StubLLM(response=response))
    retrieved = [_metric("GMV"), _metric("AOV")]
    out = asyncio.run(filter_metric(
        _state(metric_infos=[], retrieved_metrics=retrieved),
        _cfg_with(rt),
    ))
    ids = {m["id"] for m in out["filtered_metric_infos"]}
    assert ids == {"GMV"}


def test_filter_metric_preserves_full_metric_info():
    """SRS 4.2.7 rule 4: keep id / name / description / related_columns / alias."""
    response = json.dumps({"keep_metric_ids": ["GMV"]})
    rt = _StubRuntime(llm=_StubLLM(response=response))
    metrics = [_metric("GMV", name="GMV", alias=["\u9500\u552e\u989d"])]
    out = asyncio.run(filter_metric(
        _state(metric_infos=metrics),
        _cfg_with(rt),
    ))
    assert len(out["filtered_metric_infos"]) == 1
    m = out["filtered_metric_infos"][0]
    assert m["id"] == "GMV"
    assert m["name"] == "GMV"
    assert m["description"]
    assert m["related_columns"]
    assert m["alias"] == ["\u9500\u552e\u989d"]


def test_filter_metric_records_node_latency_and_counter():
    rt = _StubRuntime(llm=_StubLLM())
    asyncio.run(filter_metric(
        _state(metric_infos=[_metric("GMV")]),
        _cfg_with(rt),
    ))
    assert rt.nodes_called == 1
    nodes = [n for n, _ in rt.metrics.latencies]
    assert "filter_metric" in nodes


def test_filter_metric_records_llm_call_stat():
    rt = _StubRuntime(llm=_StubLLM())
    asyncio.run(filter_metric(
        _state(metric_infos=[_metric("GMV")]),
        _cfg_with(rt),
    ))
    assert len(rt.metrics.llm_calls) == 1
    stat = rt.metrics.llm_calls[0]
    assert stat.node_name == "filter_metric"
    assert stat.model == "mock"


def test_filter_metric_node_history_entry_records_counts():
    rt = _StubRuntime(llm=_StubLLM(response='{"keep_metric_ids": ["GMV"]}'))
    metrics = [_metric("GMV"), _metric("AOV")]
    out = asyncio.run(filter_metric(
        _state(metric_infos=metrics),
        _cfg_with(rt),
    ))
    nh = out["node_history"][-1]
    assert nh["node"] == "filter_metric"
    assert nh["status"] == "ok"
    assert nh["metrics"] == 1
    assert nh["keep_ids"] == 1


def test_filter_metric_empty_input_returns_empty_list():
    rt = _StubRuntime(llm=_StubLLM())
    out = asyncio.run(filter_metric(_state(), _cfg_with(rt)))
    assert out["filtered_metric_infos"] == []


def test_filter_metric_prompt_contains_query_and_metric_infos():
    rt = _StubRuntime(llm=_StubLLM())
    metrics = [_metric("GMV"), _metric("AOV")]
    asyncio.run(filter_metric(
        _state(query="\u4e0a\u6708\u9500\u552e\u989d",
               metric_infos=metrics),
        _cfg_with(rt),
    ))
    prompt = rt.llm.calls[0]
    assert "\u4e0a\u6708\u9500\u552e\u989d" in prompt
    assert "GMV" in prompt
    assert "AOV" in prompt