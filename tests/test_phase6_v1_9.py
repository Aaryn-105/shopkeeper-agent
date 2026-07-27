"""V1.0 phase 6.9 verification: generate_sql (4.2.9).

V1.0 phase 6.9 spec:
  - cache_key = sha256(f"{query}|{fingerprint(filtered_table_infos + filtered_metric_infos)}")
  - On cache hit  -> state.sql = cached.sql_text, state.cache_hit_sql = True
  - On cache miss -> LLM call, parse JSON / fallback to SQL text, store in cache
  - stream_writer({"type":"sql_generated","sql": ..., "request_id": ..., ...})
"""
from __future__ import annotations
import asyncio
import hashlib
import json
import pytest
from langchain_core.runnables import RunnableConfig

from app.agent.nodes.generate_sql import (
    SQL_CACHE_TTL_SECONDS,
    _load_prompt_template,
    fingerprint_table_infos,
    fingerprint_metric_infos,
    make_cache_key,
    parse_sql_response,
    generate_sql,
)


# ---------- 6.9.1 constants ----------

def test_sql_cache_ttl_is_one_hour():
    assert SQL_CACHE_TTL_SECONDS == 3600


# ---------- 6.9.2 fingerprint + cache_key ----------

def test_fingerprint_table_infos_orders_keys():
    a = fingerprint_table_infos({"x": {"columns": [{"id": "x.a"}]}, "y": {"columns": []}})
    b = fingerprint_table_infos({"y": {"columns": []}, "x": {"columns": [{"id": "x.a"}]}})
    assert a == b


def test_fingerprint_table_infos_empty():
    assert fingerprint_table_infos({}) == ""


def test_fingerprint_table_infos_uses_column_ids():
    cols = [{"id": "x.b"}, {"id": "x.a"}, {"name": "x.c"}]
    out = fingerprint_table_infos({"x": {"columns": cols}})
    assert "x.a" in out and "x.b" in out and "x.c" in out


def test_fingerprint_metric_infos_orders_by_id():
    a = fingerprint_metric_infos([{"id": "GMV"}, {"id": "ORDER_CNT"}])
    b = fingerprint_metric_infos([{"id": "ORDER_CNT"}, {"id": "GMV"}])
    assert a == b
    assert a == "GMV,ORDER_CNT"


def test_fingerprint_metric_infos_empty():
    assert fingerprint_metric_infos([]) == ""


def test_make_cache_key_is_sha256_hex():
    key = make_cache_key("hello", {"x": {"columns": []}}, [])
    assert len(key) == 64
    int(key, 16)  # raises if not hex


def test_make_cache_key_changes_with_query():
    k1 = make_cache_key("q1", {}, [])
    k2 = make_cache_key("q2", {}, [])
    assert k1 != k2


def test_make_cache_key_changes_with_table_infos():
    k1 = make_cache_key("q", {"x": {"columns": [{"id": "x.a"}]}}, [])
    k2 = make_cache_key("q", {"x": {"columns": [{"id": "x.b"}]}}, [])
    assert k1 != k2


def test_make_cache_key_changes_with_metric_infos():
    k1 = make_cache_key("q", {}, [{"id": "GMV"}])
    k2 = make_cache_key("q", {}, [{"id": "ORDER_CNT"}])
    assert k1 != k2


def test_make_cache_key_is_order_independent():
    k1 = make_cache_key("q", {"a": {"columns": []}, "b": {"columns": []}}, [])
    k2 = make_cache_key("q", {"b": {"columns": []}, "a": {"columns": []}}, [])
    assert k1 == k2


def test_make_cache_key_matches_manual_sha256():
    from app.agent.nodes.generate_sql import (
        fingerprint_table_infos, fingerprint_metric_infos,
    )
    salt = (fingerprint_table_infos({"x": {"columns": [{"id": "x.a"}]}})
            + "|" + fingerprint_metric_infos([]))
    expected = hashlib.sha256(f"q|{salt}".encode("utf-8")).hexdigest()
    actual = make_cache_key("q", {"x": {"columns": [{"id": "x.a"}]}}, [])
    assert actual == expected


# ---------- 6.9.3 parse_sql_response ----------

@pytest.mark.parametrize("text,expected", [
    ('{"sql": "SELECT 1"}', "SELECT 1"),
    ('{"sql_text": "SELECT 2"}', "SELECT 2"),
    ('{"query": "SELECT 3"}', "SELECT 3"),
    ('''```sql
SELECT 4
```''', "SELECT 4"),
    ('SELECT 5', "SELECT 5"),
    ('WITH x AS (SELECT 1) SELECT * FROM x', "WITH x AS (SELECT 1) SELECT * FROM x"),
    ('EXPLAIN SELECT * FROM t', "EXPLAIN SELECT * FROM t"),
    ('', ""),
    ('garbage with no sql', ""),
])
def test_parse_sql_response_handles_canonical_shapes(text, expected):
    assert parse_sql_response(text) == expected


def test_parse_sql_response_picks_sql_keyword_when_mixed_with_text():
    """When the LLM adds preamble, we still find the SQL statement."""
    text = "Sure! Here is the SQL:\nSELECT * FROM fact_order LIMIT 10"
    out = parse_sql_response(text)
    assert out.startswith("SELECT")
    assert "fact_order" in out


# ---------- 6.9.4 prompt ----------

def test_prompt_template_loads():
    tpl = _load_prompt_template()
    assert tpl
    # All placeholders present
    for k in ("{query}", "{filtered_table_infos}", "{filtered_metric_infos}",
             "{current_time}", "{db_type}", "{db_version}", "{retrieved_values}"):
        assert k in tpl


def test_prompt_template_fallback_when_missing(tmp_path, monkeypatch):
    import app.agent.nodes.generate_sql as mod
    orig = mod._PROMPT_PATH
    try:
        mod._PROMPT_PATH = tmp_path / "missing.prompt"
        tpl = mod._load_prompt_template()
        assert tpl
        assert "{query}" in tpl
    finally:
        mod._PROMPT_PATH = orig


# ---------- 6.9.5 runtime stubs ----------

class _StubCache:
    """In-memory fake matching the QueryCache.get_exact / put interface."""

    def __init__(self):
        self._store: dict[str, dict] = {}
        self.get_calls = 0
        self.put_calls = 0

    def get_exact(self, key):
        self.get_calls += 1
        return self._store.get(key)

    def put(self, key, payload):
        self.put_calls += 1
        self._store[key] = payload


class _StubMetrics:
    def __init__(self):
        self.latencies = []
        self.llm_calls = []
        self.sql_generated_count = 0

    def record_node_latency(self, node, ms):
        self.latencies.append((node, ms))

    def record_llm_call(self, stat):
        self.llm_calls.append(stat)

    def record_sql_generated(self):
        self.sql_generated_count += 1


class _StubLLM:
    is_mock = True
    model = "mock"

    def __init__(self, response: str = "SELECT 1"):
        self.response = response
        self.calls = []

    async def ainvoke(self, prompt, system=None, response_format=None):
        from app.clients.llm_client import LLMResponse
        self.calls.append(prompt)
        return LLMResponse(text=self.response, prompt_tokens=len(prompt) // 2,
                           completion_tokens=len(self.response) // 2, latency_ms=1)


class _StubRuntime:
    def __init__(self, llm=None, cache=None):
        self.metrics = _StubMetrics()
        self.llm = llm
        self.cache = cache
        self.pending_events = []
        self.nodes_called = 0


def _cfg_with(runtime):
    cfg = RunnableConfig()
    cfg["configurable"] = {"runtime": runtime}
    return cfg


def _state(query="q", table_infos=None, metric_infos=None, extra=None):
    return {
        "query": query,
        "request_id": "rid-6-9",
        "node_history": [],
        "validate_attempts": 0,
        "filtered_table_infos": table_infos or {},
        "filtered_metric_infos": metric_infos or [],
        "merged_table_infos": {},
        "extra_context": extra or {},
    }


def _fact_order_table():
    return {
        "table_id": "fact_order",
        "columns": [
            {"id": "fact_order.order_amount", "name": "order_amount"},
            {"id": "fact_order.region_id", "name": "region_id"},
        ],
    }


# ---------- 6.9.6 node behaviour ----------

def test_generate_sql_writes_sql_field():
    rt = _StubRuntime(llm=_StubLLM(response="SELECT 1"))
    out = asyncio.run(generate_sql(_state(), _cfg_with(rt)))
    assert "sql" in out
    assert out["sql"] == "SELECT 1"


def test_generate_sql_writes_cache_hit_sql_false_on_miss():
    rt = _StubRuntime(llm=_StubLLM(response="SELECT 1"))
    out = asyncio.run(generate_sql(_state(), _cfg_with(rt)))
    assert out["cache_hit_sql"] is False


def test_generate_sql_writes_cache_hit_sql_true_on_hit():
    cache = _StubCache()
    table_infos = {"fact_order": _fact_order_table()}
    key = make_cache_key("q", table_infos, [])
    cache.put(key, {"sql_text": "SELECT 42", "query": "q"})
    rt = _StubRuntime(llm=_StubLLM(response="SELECT 1"), cache=cache)
    out = asyncio.run(generate_sql(
        _state(query="q", table_infos=table_infos),
        _cfg_with(rt),
    ))
    assert out["cache_hit_sql"] is True
    assert out["sql"] == "SELECT 42"
    assert rt.metrics.sql_generated_count == 0


def test_generate_sql_skips_llm_call_on_cache_hit():
    cache = _StubCache()
    table_infos = {"fact_order": _fact_order_table()}
    key = make_cache_key("q", table_infos, [])
    cache.put(key, {"sql_text": "SELECT 42", "query": "q"})
    llm = _StubLLM(response="SELECT 1")
    rt = _StubRuntime(llm=llm, cache=cache)
    asyncio.run(generate_sql(
        _state(query="q", table_infos=table_infos),
        _cfg_with(rt),
    ))
    # LLM was never called when the cache hit
    assert len(llm.calls) == 0


def test_generate_sql_writes_cache_on_miss():
    cache = _StubCache()
    rt = _StubRuntime(llm=_StubLLM(response="SELECT 99"), cache=cache)
    asyncio.run(generate_sql(_state(query="unique-q"), _cfg_with(rt)))
    assert cache.put_calls == 1


def test_generate_sql_second_call_with_same_input_hits_cache():
    cache = _StubCache()
    llm = _StubLLM(response="SELECT 100")
    rt = _StubRuntime(llm=llm, cache=cache)
    state = _state(query="repeat-q", table_infos={"fact_order": _fact_order_table()})
    asyncio.run(generate_sql(state, _cfg_with(rt)))
    # Second call must hit cache
    out2 = asyncio.run(generate_sql(state, _cfg_with(rt)))
    assert out2["cache_hit_sql"] is True
    assert out2["sql"] == "SELECT 100"
    # LLM called only once
    assert len(llm.calls) == 1


def test_generate_sql_no_cache_always_calls_llm():
    rt = _StubRuntime(llm=_StubLLM(response="SELECT 1"), cache=None)
    state = _state(query="q-no-cache")
    asyncio.run(generate_sql(state, _cfg_with(rt)))
    out2 = asyncio.run(generate_sql(state, _cfg_with(rt)))
    assert out2["cache_hit_sql"] is False


def test_generate_sql_handles_json_llm_response():
    rt = _StubRuntime(llm=_StubLLM(response='{"sql": "SELECT 7 FROM t"}'))
    out = asyncio.run(generate_sql(_state(), _cfg_with(rt)))
    assert out["sql"] == "SELECT 7 FROM t"


def test_generate_sql_handles_markdown_codeblock():
    rt = _StubRuntime(llm=_StubLLM(response="```sql\nSELECT * FROM fact_order\n```"))
    out = asyncio.run(generate_sql(_state(), _cfg_with(rt)))
    assert "SELECT * FROM fact_order" in out["sql"]


def test_generate_sql_handles_plain_sql_response():
    rt = _StubRuntime(llm=_StubLLM(response="SELECT amount FROM fact_order"))
    out = asyncio.run(generate_sql(_state(), _cfg_with(rt)))
    assert out["sql"] == "SELECT amount FROM fact_order"


def test_generate_sql_falls_back_to_count_when_llm_returns_nothing():
    """Safety net: non-empty SQL guaranteed even when LLM returns garbage."""
    rt = _StubRuntime(llm=_StubLLM(response=""))
    out = asyncio.run(generate_sql(_state(), _cfg_with(rt)))
    assert out["sql"]
    assert "fact_order" in out["sql"].lower()


def test_generate_sql_falls_back_when_llm_raises():
    class _BoomLLM:
        is_mock = True
        model = "mock"
        async def ainvoke(self, prompt, system=None, response_format=None):
            raise RuntimeError("llm down")
    rt = _StubRuntime(llm=_BoomLLM())
    out = asyncio.run(generate_sql(_state(), _cfg_with(rt)))
    assert out["sql"]
    assert "fact_order" in out["sql"].lower()


def test_generate_sql_no_llm_uses_fallback_sql():
    rt = _StubRuntime(llm=None)
    out = asyncio.run(generate_sql(_state(), _cfg_with(rt)))
    assert out["sql"]
    assert "fact_order" in out["sql"].lower()


def test_generate_sql_records_node_latency_and_counter():
    rt = _StubRuntime(llm=_StubLLM(response="SELECT 1"))
    asyncio.run(generate_sql(_state(), _cfg_with(rt)))
    assert rt.nodes_called == 1
    nodes = [n for n, _ in rt.metrics.latencies]
    assert "generate_sql" in nodes


def test_generate_sql_records_llm_call_stat_on_miss():
    rt = _StubRuntime(llm=_StubLLM(response="SELECT 1"))
    asyncio.run(generate_sql(_state(), _cfg_with(rt)))
    assert len(rt.metrics.llm_calls) == 1
    stat = rt.metrics.llm_calls[0]
    assert stat.node_name == "generate_sql"
    assert stat.model == "mock"


def test_generate_sql_records_sql_generated_metric_on_miss():
    rt = _StubRuntime(llm=_StubLLM(response="SELECT 1"))
    asyncio.run(generate_sql(_state(), _cfg_with(rt)))
    assert rt.metrics.sql_generated_count == 1


def test_generate_sql_does_not_record_sql_generated_on_cache_hit():
    cache = _StubCache()
    table_infos = {"fact_order": _fact_order_table()}
    cache.put(make_cache_key("q", table_infos, []), {"sql_text": "SELECT 42"})
    rt = _StubRuntime(llm=_StubLLM(response="SELECT 1"), cache=cache)
    asyncio.run(generate_sql(_state(query="q", table_infos=table_infos), _cfg_with(rt)))
    assert rt.metrics.sql_generated_count == 0


def test_generate_sql_emits_pending_stream_event_on_miss():
    rt = _StubRuntime(llm=_StubLLM(response="SELECT 1"))
    out = asyncio.run(generate_sql(_state(query="rid-q"), _cfg_with(rt)))
    assert "pending_stream_events" in out
    assert len(out["pending_stream_events"]) == 1
    ev = out["pending_stream_events"][0]
    assert ev["type"] == "sql_generated"
    assert ev["sql"] == "SELECT 1"
    assert ev["request_id"] == "rid-6-9"
    assert ev["cache_hit"] is False


def test_generate_sql_emits_pending_stream_event_on_hit():
    cache = _StubCache()
    table_infos = {"fact_order": _fact_order_table()}
    cache.put(make_cache_key("q", table_infos, []), {"sql_text": "SELECT 42"})
    rt = _StubRuntime(llm=_StubLLM(response="SELECT 1"), cache=cache)
    out = asyncio.run(generate_sql(
        _state(query="q", table_infos=table_infos),
        _cfg_with(rt),
    ))
    ev = out["pending_stream_events"][0]
    assert ev["type"] == "sql_generated"
    assert ev["cache_hit"] is True
    assert ev["sql"] == "SELECT 42"


def test_generate_sql_pending_event_also_pushed_to_runtime_queue():
    rt = _StubRuntime(llm=_StubLLM(response="SELECT 1"))
    asyncio.run(generate_sql(_state(query="rid-q"), _cfg_with(rt)))
    assert len(rt.pending_events) == 1
    assert rt.pending_events[0]["type"] == "sql_generated"


def test_generate_sql_keeps_legacy_cache_hit_in_sync():
    """state.cache_hit (legacy) and state.cache_hit_sql (V1.0) must agree."""
    rt = _StubRuntime(llm=_StubLLM(response="SELECT 1"))
    out = asyncio.run(generate_sql(_state(), _cfg_with(rt)))
    assert out["cache_hit"] == out["cache_hit_sql"]


def test_generate_sql_node_history_records_cache_state():
    rt = _StubRuntime(llm=_StubLLM(response="SELECT 1"))
    out = asyncio.run(generate_sql(_state(), _cfg_with(rt)))
    nh = out["node_history"][-1]
    assert nh["node"] == "generate_sql"
    assert nh["status"] == "ok"
    assert nh["cache_hit"] is False
    assert nh["sql_len"] == len("SELECT 1")
    assert "cache_key" in nh


def test_generate_sql_node_history_records_cache_hit_status():
    cache = _StubCache()
    table_infos = {"fact_order": _fact_order_table()}
    cache.put(make_cache_key("q", table_infos, []), {"sql_text": "SELECT 42"})
    rt = _StubRuntime(llm=_StubLLM(response="SELECT 1"), cache=cache)
    out = asyncio.run(generate_sql(
        _state(query="q", table_infos=table_infos),
        _cfg_with(rt),
    ))
    nh = out["node_history"][-1]
    assert nh["status"] == "cache_hit"
    assert nh["cache_hit"] is True


def test_generate_sql_prompt_contains_query_table_and_metric():
    rt = _StubRuntime(llm=_StubLLM(response="SELECT 1"))
    asyncio.run(generate_sql(
        _state(query="\u534e\u4e1c\u9500\u552e\u989d",
               table_infos={"fact_order": _fact_order_table()},
               metric_infos=[{"id": "GMV"}],
               extra={"current_time": "2024-01-01T00:00:00",
                      "db_type": "MySQL", "db_version": "8.0.40"}),
        _cfg_with(rt),
    ))
    prompt = rt.llm.calls[0]
    assert "\u534e\u4e1c\u9500\u552e\u989d" in prompt
    assert "fact_order" in prompt
    assert "GMV" in prompt
    assert "MySQL" in prompt
    assert "8.0.40" in prompt


def test_generate_sql_no_runtime_still_writes_sql():
    """Defensive: the node must not crash when runtime is None."""
    out = asyncio.run(generate_sql(_state(), None))
    assert out["sql"]


def test_generate_sql_sql_corrected_flag_initially_false():
    rt = _StubRuntime(llm=_StubLLM(response="SELECT 1"))
    out = asyncio.run(generate_sql(_state(), _cfg_with(rt)))
    assert out["sql_corrected"] is False