"""V1.0 phase 6.6 verification: filter_table (4.2.6).

V1.0 phase 6.6 spec:
  - Load filter_table_info.prompt.
  - LLM returns {"keep_table_ids": [...], "keep_column_ids": [...]}.
  - Filter tables / columns accordingly.
  - MUST preserve PK / FK columns per SRS 4.2.6 rule 4.
  - Write state.filtered_table_infos.
"""
from __future__ import annotations
import asyncio
import json
import pytest
from langchain_core.runnables import RunnableConfig

from app.agent.nodes.filter_table import (
    PK_FK_ROLES,
    _load_prompt_template,
    _serialize_table_infos,
    _parse_keep_response,
    _is_pk_fk_column,
    filter_table,
)


# ---------- 6.6.1 constants & small helpers ----------

def test_pk_fk_roles_constant():
    assert "pk" in PK_FK_ROLES
    assert "fk" in PK_FK_ROLES


def test_serialize_table_infos_returns_string():
    out = _serialize_table_infos({"fact_order": {"table_id": "fact_order"}})
    assert isinstance(out, str)
    assert "fact_order" in out


@pytest.mark.parametrize("text,expected", [
    ('{"keep_table_ids": ["a", "b"], "keep_column_ids": ["a.col1"]}',
     (["a", "b"], ["a.col1"])),
    ('{"keep_tables": ["x"], "keep_columns": ["x.y"]}', (["x"], ["x.y"])),
    ('["a", "b"]', (["a", "b"], [])),
    ('a, b, c', (["a", "b", "c"], [])),
    ('a b c', (["a", "b", "c"], [])),
    ('', ([], [])),
    ('garbage', (['garbage'], [])),  # whitespace-split fallback
])
def test_parse_keep_response_handles_canonical_shapes(text, expected):
    assert _parse_keep_response(text) == expected


def test_prompt_template_loads():
    tpl = _load_prompt_template()
    assert tpl
    assert "{query}" in tpl
    assert "{table_infos}" in tpl


def test_prompt_template_fallback_when_missing(tmp_path, monkeypatch):
    import app.agent.nodes.filter_table as mod
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
    # The literal JSON should have doubled braces
    assert "{{" in tpl and "}}" in tpl


def test_is_pk_fk_column_recognises_auto_injected():
    assert _is_pk_fk_column({"name": "customer_id", "_auto_injected": True}, "fact_order")


def test_is_pk_fk_column_recognises_role_pk():
    assert _is_pk_fk_column({"name": "x", "role": "pk"}, "fact_order")


def test_is_pk_fk_column_recognises_role_fk():
    assert _is_pk_fk_column({"name": "x", "role": "fk"}, "fact_order")


def test_is_pk_fk_column_recognises_fact_order_standard_fks():
    for name in ("customer_id", "product_id", "date_id", "region_id", "order_id"):
        assert _is_pk_fk_column({"name": name}, "fact_order"), name


def test_is_pk_fk_column_recognises_dim_pk():
    assert _is_pk_fk_column({"name": "region_id"}, "dim_region")
    assert _is_pk_fk_column({"name": "customer_id"}, "dim_customer")


def test_is_pk_fk_column_rejects_non_pk():
    assert not _is_pk_fk_column({"name": "order_amount"}, "fact_order")
    assert not _is_pk_fk_column({"name": "region_name"}, "dim_region")


# ---------- 6.6.2 runtime stubs ----------

class _StubMetrics:
    def __init__(self):
        self.latencies = []
        self.llm_calls = []

    def record_node_latency(self, node, ms):
        self.latencies.append((node, ms))

    def record_llm_call(self, stat):
        self.llm_calls.append(stat)


class _StubLLM:
    """LLM that returns a canned JSON response."""

    is_mock = True
    model = "mock"

    def __init__(self, response: str = '{"keep_table_ids": [], "keep_column_ids": []}'):
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


def _state(query="x", table_infos=None, merged_table_infos=None):
    return {
        "query": query,
        "request_id": "rid-6-6",
        "node_history": [],
        "validate_attempts": 0,
        "table_infos": table_infos or {},
        "merged_table_infos": merged_table_infos or {},
    }


def _fact_order_table():
    return {
        "table_id": "fact_order",
        "name": "fact_order",
        "role": "fact",
        "description": "order fact",
        "columns": [
            {"id": "fact_order.order_id", "name": "order_id", "role": "pk"},
            {"id": "fact_order.customer_id", "name": "customer_id", "role": "fk",
             "_auto_injected": True},
            {"id": "fact_order.product_id", "name": "product_id", "role": "fk",
             "_auto_injected": True},
            {"id": "fact_order.date_id", "name": "date_id", "role": "fk",
             "_auto_injected": True},
            {"id": "fact_order.region_id", "name": "region_id", "role": "fk",
             "_auto_injected": True},
            {"id": "fact_order.order_amount", "name": "order_amount", "role": "measure",
             "description": "order amount"},
        ],
    }


def _dim_region_table():
    return {
        "table_id": "dim_region",
        "columns": [
            {"id": "dim_region.region_id", "name": "region_id", "role": "pk"},
            {"id": "dim_region.region_name", "name": "region_name", "role": "attribute"},
        ],
    }


# ---------- 6.6.3 node behaviour ----------

def test_filter_table_writes_filtered_table_infos():
    rt = _StubRuntime(llm=_StubLLM())
    out = asyncio.run(filter_table(
        _state(table_infos={"fact_order": _fact_order_table()}),
        _cfg_with(rt),
    ))
    assert "filtered_table_infos" in out
    assert "fact_order" in out["filtered_table_infos"]


def test_filter_table_no_llm_returns_input_intact():
    rt = _StubRuntime(llm=None)
    fact = _fact_order_table()
    out = asyncio.run(filter_table(
        _state(table_infos={"fact_order": fact}),
        _cfg_with(rt),
    ))
    # Without an LLM we keep everything; PK/FK + order_amount all survive.
    cols = out["filtered_table_infos"]["fact_order"]["columns"]
    names = {c["name"] for c in cols}
    assert {"order_id", "customer_id", "product_id", "date_id",
            "region_id", "order_amount"}.issubset(names)


def test_filter_table_keeps_all_tables_when_llm_returns_empty_keep_lists():
    rt = _StubRuntime(llm=_StubLLM(response='{"keep_table_ids": [], "keep_column_ids": []}'))
    fact = _fact_order_table()
    dim = _dim_region_table()
    out = asyncio.run(filter_table(
        _state(table_infos={"fact_order": fact, "dim_region": dim}),
        _cfg_with(rt),
    ))
    assert set(out["filtered_table_infos"].keys()) == {"fact_order", "dim_region"}


def test_filter_table_drops_tables_not_in_keep_table_ids():
    response = json.dumps({"keep_table_ids": ["fact_order"], "keep_column_ids": []})
    rt = _StubRuntime(llm=_StubLLM(response=response))
    fact = _fact_order_table()
    dim = _dim_region_table()
    out = asyncio.run(filter_table(
        _state(table_infos={"fact_order": fact, "dim_region": dim}),
        _cfg_with(rt),
    ))
    assert "fact_order" in out["filtered_table_infos"]
    assert "dim_region" not in out["filtered_table_infos"]


def test_filter_table_keeps_pk_fk_columns_even_when_llm_drops_them():
    """SRS 4.2.6 rule 4: PK/FK fields always survive filtering."""
    response = json.dumps({
        "keep_table_ids": ["fact_order"],
        "keep_column_ids": ["fact_order.order_amount"],
    })
    rt = _StubRuntime(llm=_StubLLM(response=response))
    fact = _fact_order_table()
    out = asyncio.run(filter_table(
        _state(table_infos={"fact_order": fact}),
        _cfg_with(rt),
    ))
    cols = out["filtered_table_infos"]["fact_order"]["columns"]
    names = {c["name"] for c in cols}
    assert "order_amount" in names
    assert "order_id" in names
    assert "customer_id" in names
    assert "product_id" in names
    assert "date_id" in names
    assert "region_id" in names


def test_filter_table_keeps_only_listed_columns_when_llm_provides_them():
    response = json.dumps({
        "keep_table_ids": ["fact_order"],
        "keep_column_ids": ["fact_order.order_amount", "fact_order.region_id"],
    })
    rt = _StubRuntime(llm=_StubLLM(response=response))
    fact = _fact_order_table()
    out = asyncio.run(filter_table(
        _state(table_infos={"fact_order": fact}),
        _cfg_with(rt),
    ))
    cols = out["filtered_table_infos"]["fact_order"]["columns"]
    names = {c["name"] for c in cols}
    # order_amount + all PK/FK fields (the LLM said keep region_id which IS pk/fk)
    assert "order_amount" in names
    assert "order_id" in names
    assert "customer_id" in names
    assert "product_id" in names
    assert "date_id" in names
    assert "region_id" in names


def test_filter_table_drops_unrelated_columns():
    """When keep_column_ids lists ONLY order_amount, only that + the PK/FK
    columns should survive. Any other non-PK/FK column would be a leak."""
    # Inject an extra non-PK/FK column that should be dropped.
    fact = _fact_order_table()
    fact["columns"].append({
        "id": "fact_order.discount_amount", "name": "discount_amount",
        "role": "measure", "description": "promo discount",
    })
    response = json.dumps({
        "keep_table_ids": ["fact_order"],
        "keep_column_ids": ["fact_order.order_amount"],
    })
    rt = _StubRuntime(llm=_StubLLM(response=response))
    out = asyncio.run(filter_table(
        _state(table_infos={"fact_order": fact}),
        _cfg_with(rt),
    ))
    cols = out["filtered_table_infos"]["fact_order"]["columns"]
    names = {c["name"] for c in cols}
    # order_amount survives; discount_amount is dropped.
    assert "order_amount" in names
    assert "discount_amount" not in names
    # PK/FK still present
    assert {"order_id", "customer_id", "product_id", "date_id", "region_id"}.issubset(names)


def test_filter_table_falls_back_to_merged_table_infos():
    """If table_infos is empty but merged_table_infos has data, use that."""
    response = json.dumps({"keep_table_ids": ["fact_order"], "keep_column_ids": []})
    rt = _StubRuntime(llm=_StubLLM(response=response))
    out = asyncio.run(filter_table(
        _state(table_infos={},
               merged_table_infos={"fact_order": _fact_order_table()}),
        _cfg_with(rt),
    ))
    assert "fact_order" in out["filtered_table_infos"]


def test_filter_table_does_not_drop_table_completely():
    """If every column would be dropped, keep at least one as a safety net."""
    response = json.dumps({
        "keep_table_ids": ["fact_order"],
        "keep_column_ids": [],
    })
    rt = _StubRuntime(llm=_StubLLM(response=response))
    fact = _fact_order_table()
    out = asyncio.run(filter_table(
        _state(table_infos={"fact_order": fact}),
        _cfg_with(rt),
    ))
    cols = out["filtered_table_infos"]["fact_order"]["columns"]
    # No keep_column_ids + all columns are PK/FK => all PK/FK stay (which is > 0)
    assert len(cols) >= 1


def test_filter_table_handles_garbage_llm_response_gracefully():
    rt = _StubRuntime(llm=_StubLLM(response="totally not json"))
    fact = _fact_order_table()
    out = asyncio.run(filter_table(
        _state(table_infos={"fact_order": fact}),
        _cfg_with(rt),
    ))
    # Garbage -> parser returns (['totally', 'not', 'json'], []).
    # keep_table_ids then become ['totally', 'not', 'json'] which is set.
    # fact_order is not in that set so it gets dropped entirely.
    # Verify it does not raise.
    assert "filtered_table_infos" in out


def test_filter_table_handles_llm_exception():
    class _BoomLLM:
        is_mock = True
        model = "mock"
        async def ainvoke(self, prompt, system=None, response_format=None):
            raise RuntimeError("llm down")

    rt = _StubRuntime(llm=_BoomLLM())
    out = asyncio.run(filter_table(
        _state(table_infos={"fact_order": _fact_order_table()}),
        _cfg_with(rt),
    ))
    # Graceful fallback: keep all tables + all PK/FK fields
    cols = out["filtered_table_infos"]["fact_order"]["columns"]
    names = {c["name"] for c in cols}
    assert {"order_id", "customer_id", "product_id", "date_id",
            "region_id"}.issubset(names)


def test_filter_table_records_node_latency_and_counter():
    rt = _StubRuntime(llm=_StubLLM())
    asyncio.run(filter_table(
        _state(table_infos={"fact_order": _fact_order_table()}),
        _cfg_with(rt),
    ))
    assert rt.nodes_called == 1
    nodes = [n for n, _ in rt.metrics.latencies]
    assert "filter_table" in nodes


def test_filter_table_records_llm_call_stat():
    rt = _StubRuntime(llm=_StubLLM())
    asyncio.run(filter_table(
        _state(table_infos={"fact_order": _fact_order_table()}),
        _cfg_with(rt),
    ))
    assert len(rt.metrics.llm_calls) == 1
    stat = rt.metrics.llm_calls[0]
    assert stat.node_name == "filter_table"
    assert stat.model == "mock"


def test_filter_table_node_history_entry_records_counts():
    rt = _StubRuntime(llm=_StubLLM())
    fact = _fact_order_table()
    out = asyncio.run(filter_table(
        _state(table_infos={"fact_order": fact}),
        _cfg_with(rt),
    ))
    nh = out["node_history"][-1]
    assert nh["node"] == "filter_table"
    assert nh["status"] == "ok"
    assert nh["tables"] >= 1
    assert "kept_columns" in nh
    assert nh["ms"] >= 0


def test_filter_table_empty_input_returns_empty_dict():
    rt = _StubRuntime(llm=_StubLLM())
    out = asyncio.run(filter_table(_state(), _cfg_with(rt)))
    assert out["filtered_table_infos"] == {}


def test_filter_table_prompt_contains_query_and_table_infos():
    rt = _StubRuntime(llm=_StubLLM())
    asyncio.run(filter_table(
        _state(query="\u4e0a\u6708\u534e\u4e1cGMV",
               table_infos={"fact_order": _fact_order_table()}),
        _cfg_with(rt),
    ))
    prompt = rt.llm.calls[0]
    assert "\u4e0a\u6708\u534e\u4e1cGMV" in prompt
    assert "fact_order" in prompt