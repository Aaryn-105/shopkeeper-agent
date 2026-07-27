"""V1.0 phase 6.11 verification: correct_sql (4.2.11).

V1.0 phase 6.11 spec:
  - LLM 一次性重写 SQL;**不走缓存**。
  - state.sql = corrected_sql, state.sql_corrected = True。
  - 解析 LLM 输出复用 parse_sql_response(JSON / markdown / 纯 SQL 三态)。
  - record_llm_call(node_name="correct_sql")。
  - 推送事件:
        {"type":"sql_corrected","original_sql":..., "corrected_sql":...,
         "error": state.sql_error, "request_id": state.request_id}
    写入 state.pending_stream_events 与 runtime.pending_events。
"""
from __future__ import annotations
import asyncio
import json
import pytest
from langchain_core.runnables import RunnableConfig

from app.agent.nodes.correct_sql import (
    _FALLBACK_PROMPT,
    _load_prompt_template,
    correct_sql,
)


# ---------- 6.11.1 prompt template ----------

def test_prompt_template_loads_from_file():
    tpl = _load_prompt_template()
    assert tpl
    # All V1.0 placeholders present
    for k in ("{query}", "{current_time}", "{filtered_table_infos}",
             "{filtered_metric_infos}", "{original_sql}", "{error}"):
        assert k in tpl, f"missing placeholder {k}"


def test_prompt_template_is_chinese_safety_text():
    tpl = _load_prompt_template()
    # should contain role / safety language markers (one of them)
    assert "角色" in tpl or "NL2SQL" in tpl
    # The 7 error categories mentioned in the spec
    assert "JOIN" in tpl
    assert "GROUP BY" in tpl


def test_prompt_template_fallback_when_file_missing(tmp_path, monkeypatch):
    import app.agent.nodes.correct_sql as mod
    orig = mod._PROMPT_PATH
    try:
        mod._PROMPT_PATH = tmp_path / "no_such_file.prompt"
        tpl = mod._load_prompt_template()
        assert tpl == _FALLBACK_PROMPT
    finally:
        mod._PROMPT_PATH = orig


# ---------- 6.11.2 runtime stubs ----------

class _StubLLM:
    """Records ainvoke() calls and returns canned text."""

    def __init__(self, response: str, raise_exc: Exception | None = None,
                 model: str = "mock"):
        self.response = response
        self.raise_exc = raise_exc
        self.model = model
        self.calls: list[str] = []

    async def ainvoke(self, prompt, system=None, response_format=None):
        self.calls.append(prompt)
        if self.raise_exc is not None:
            raise self.raise_exc

        class _R:
            def __init__(self, text):
                self.text = text
                self.latency_ms = 0

        return _R(self.response)


class _StubCache:
    """Strict cache stub that records every get/put call so we can assert no-cache."""

    def __init__(self):
        self.get_calls: list[str] = []
        self.put_calls: list[tuple] = []
        self._data: dict[str, dict] = {}

    def get_exact(self, key):
        self.get_calls.append(key)
        return self._data.get(key)

    def put(self, key, value):
        self.put_calls.append((key, value))
        self._data[key] = value


class _StubMetrics:
    def __init__(self):
        self.latencies = []
        self.llm_calls: list = []

    def record_node_latency(self, node, ms):
        self.latencies.append((node, ms))

    def record_llm_call(self, stat):
        self.llm_calls.append(stat)


class _StubRuntime:
    def __init__(self, llm=None, cache=None, metrics=None):
        self.llm = llm
        self.cache = cache
        self.metrics = metrics if metrics is not None else _StubMetrics()
        self.pending_events: list[dict] = []
        self.nodes_called = 0


def _cfg_with(runtime):
    cfg = RunnableConfig()
    cfg["configurable"] = {"runtime": runtime}
    return cfg


def _state(sql="SELECT bad FROM unknown", error="Unknown table 'unknown'",
           attempts=1, query="test", request_id="rid-6-11",
           table_infos=None, metric_infos=None, extra=None):
    return {
        "query": query,
        "request_id": request_id,
        "node_history": [],
        "sql": sql,
        "sql_error": error,
        "validate_attempts": attempts,
        "filtered_table_infos": table_infos or {
            "fact_order": {"columns": [{"id": "order_id"}, {"id": "order_amount"}]},
        },
        "filtered_metric_infos": metric_infos or [{"id": "GMV"}],
        "extra_context": extra or {"current_time": "2024-01-01T00:00:00",
                                   "db_type": "MySQL", "db_version": "8.0"},
    }


# ---------- 6.11.3 state writes ----------

def test_correct_sql_overrides_state_sql_with_corrected():
    rt = _StubRuntime(llm=_StubLLM(response="SELECT 1 FROM fact_order"))
    out = asyncio.run(correct_sql(
        _state(sql="SELECT bad_col FROM fact_order"),
        _cfg_with(rt),
    ))
    assert out["sql"] == "SELECT 1 FROM fact_order"


def test_correct_sql_sets_sql_corrected_flag_true():
    rt = _StubRuntime(llm=_StubLLM(response="SELECT 1"))
    out = asyncio.run(correct_sql(_state(), _cfg_with(rt)))
    assert out["sql_corrected"] is True


def test_correct_sql_returns_state_sql_even_when_llm_returns_empty():
    rt = _StubRuntime(llm=_StubLLM(response=""))
    out = asyncio.run(correct_sql(_state(sql="SELECT 1"), _cfg_with(rt)))
    # Empty LLM output -> keep original SQL, still mark corrected
    assert out["sql"] == "SELECT 1"
    assert out["sql_corrected"] is True


def test_correct_sql_returns_state_sql_when_llm_returns_garbage():
    rt = _StubRuntime(llm=_StubLLM(response="not a sql at all, no keywords"))
    out = asyncio.run(correct_sql(_state(sql="SELECT 2"), _cfg_with(rt)))
    assert out["sql"] == "SELECT 2"
    assert out["sql_corrected"] is True


def test_correct_sql_keeps_original_sql_when_no_llm():
    """Defensive: no runtime.llm -> keep original SQL, do not crash."""
    rt = _StubRuntime(llm=None)
    out = asyncio.run(correct_sql(_state(sql="SELECT 1"), _cfg_with(rt)))
    assert out["sql"] == "SELECT 1"
    assert out["sql_corrected"] is True


def test_correct_sql_no_runtime_returns_state_with_corrected_flag():
    out = asyncio.run(correct_sql(_state(sql="SELECT 1"), None))
    assert out["sql"] == "SELECT 1"
    assert out["sql_corrected"] is True


# ---------- 6.11.4 NO CACHE (V1.0 spec) ----------

def test_correct_sql_does_not_read_from_cache():
    """V1.0 phase 6.11: 校正不走缓存 -> cache.get_exact 永不被调用。"""
    cache = _StubCache()
    rt = _StubRuntime(llm=_StubLLM(response="SELECT 1"), cache=cache)
    asyncio.run(correct_sql(_state(), _cfg_with(rt)))
    assert cache.get_calls == []


def test_correct_sql_does_not_write_to_cache():
    """V1.0 phase 6.11: 校正不走缓存 -> cache.put 永不被调用。"""
    cache = _StubCache()
    rt = _StubRuntime(llm=_StubLLM(response="SELECT 1"), cache=cache)
    asyncio.run(correct_sql(_state(), _cfg_with(rt)))
    assert cache.put_calls == []


def test_correct_sql_no_cache_access_even_with_existing_hit():
    """Even if a cache is wired in, correct_sql must ignore it entirely."""
    cache = _StubCache()
    # Pre-seed a cache that would otherwise have produced a hit
    cache._data["some_key"] = {"sql_text": "SELECT cached_value"}
    rt = _StubRuntime(llm=_StubLLM(response="SELECT corrected"), cache=cache)
    out = asyncio.run(correct_sql(_state(), _cfg_with(rt)))
    assert out["sql"] == "SELECT corrected"
    assert cache.get_calls == []
    assert cache.put_calls == []


# ---------- 6.11.5 LLM invocation ----------

def test_correct_sql_invokes_llm_with_prompt():
    llm = _StubLLM(response="SELECT 1")
    rt = _StubRuntime(llm=llm)
    asyncio.run(correct_sql(
        _state(query="\u4e0a\u6708GMV",
               error="Unknown column x",
               sql="SELECT bad FROM fact_order"),
        _cfg_with(rt),
    ))
    assert len(llm.calls) == 1
    prompt = llm.calls[0]
    assert "\u4e0a\u6708GMV" in prompt
    assert "SELECT bad FROM fact_order" in prompt
    assert "Unknown column x" in prompt
    assert "fact_order" in prompt  # table info


def test_correct_sql_records_llm_call_with_node_name_correct_sql():
    llm = _StubLLM(response="SELECT 1")
    metrics = _StubMetrics()
    rt = _StubRuntime(llm=llm, metrics=metrics)
    asyncio.run(correct_sql(_state(), _cfg_with(rt)))
    assert len(metrics.llm_calls) == 1
    stat = metrics.llm_calls[0]
    assert stat.node_name == "correct_sql"
    assert stat.cache_hit is False


def test_correct_sql_does_not_record_llm_call_when_llm_raises():
    """An LLM error must not poison the metrics.llm_calls list."""
    llm = _StubLLM(response="", raise_exc=RuntimeError("network down"))
    metrics = _StubMetrics()
    rt = _StubRuntime(llm=llm, metrics=metrics)
    asyncio.run(correct_sql(_state(), _cfg_with(rt)))
    # The except branch in the node silently keeps the original SQL and
    # does NOT call record_llm_call (no successful ainvoke).
    assert metrics.llm_calls == []


def test_correct_sql_preserves_sql_on_llm_error():
    llm = _StubLLM(response="", raise_exc=RuntimeError("network down"))
    rt = _StubRuntime(llm=llm)
    out = asyncio.run(correct_sql(_state(sql="SELECT 1"), _cfg_with(rt)))
    assert out["sql"] == "SELECT 1"
    assert out["sql_corrected"] is True


# ---------- 6.11.6 parse_sql_response integration ----------

@pytest.mark.parametrize("llm_text,expected", [
    ('{"sql": "SELECT * FROM fact_order"}', "SELECT * FROM fact_order"),
    ('{"sql_text": "SELECT 1"}', "SELECT 1"),
    ('```sql\nSELECT 2\n```', "SELECT 2"),
    ('SELECT 3', "SELECT 3"),
    ('WITH x AS (SELECT 1) SELECT * FROM x', "WITH x AS (SELECT 1) SELECT * FROM x"),
])
def test_correct_sql_parses_all_supported_llm_shapes(llm_text, expected):
    rt = _StubRuntime(llm=_StubLLM(response=llm_text))
    out = asyncio.run(correct_sql(_state(), _cfg_with(rt)))
    assert out["sql"] == expected


# ---------- 6.11.7 stream event ----------

def test_correct_sql_emits_pending_stream_event():
    rt = _StubRuntime(llm=_StubLLM(response="SELECT corrected"))
    out = asyncio.run(correct_sql(
        _state(sql="SELECT original",
               error="Unknown column",
               request_id="rid-stream"),
        _cfg_with(rt),
    ))
    assert "pending_stream_events" in out
    assert len(out["pending_stream_events"]) == 1
    ev = out["pending_stream_events"][0]
    assert ev["type"] == "sql_corrected"
    assert ev["original_sql"] == "SELECT original"
    assert ev["corrected_sql"] == "SELECT corrected"
    assert ev["error"] == "Unknown column"
    assert ev["request_id"] == "rid-stream"


def test_correct_sql_stream_event_includes_attempts():
    rt = _StubRuntime(llm=_StubLLM(response="SELECT 1"))
    out = asyncio.run(correct_sql(_state(attempts=3), _cfg_with(rt)))
    ev = out["pending_stream_events"][0]
    assert ev["attempts"] == 3


def test_correct_sql_event_also_pushed_to_runtime_queue():
    rt = _StubRuntime(llm=_StubLLM(response="SELECT 1"))
    asyncio.run(correct_sql(_state(), _cfg_with(rt)))
    assert len(rt.pending_events) == 1
    assert rt.pending_events[0]["type"] == "sql_corrected"


def test_correct_sql_event_emitted_even_on_llm_error():
    """A failed LLM call must still emit the sql_corrected event (with the
    original SQL as corrected_sql) so the SSE layer can surface the failure."""
    rt = _StubRuntime(llm=_StubLLM(response="", raise_exc=RuntimeError("boom")))
    out = asyncio.run(correct_sql(_state(sql="SELECT 1"), _cfg_with(rt)))
    assert len(out["pending_stream_events"]) == 1
    ev = out["pending_stream_events"][0]
    assert ev["type"] == "sql_corrected"
    assert ev["original_sql"] == "SELECT 1"
    assert ev["corrected_sql"] == "SELECT 1"


def test_correct_sql_event_carries_empty_error_string_when_no_error():
    rt = _StubRuntime(llm=_StubLLM(response="SELECT 1"))
    state = _state()
    state["sql_error"] = ""
    out = asyncio.run(correct_sql(state, _cfg_with(rt)))
    ev = out["pending_stream_events"][0]
    assert ev["error"] == ""


# ---------- 6.11.8 metrics + node counters ----------

def test_correct_sql_records_node_latency():
    rt = _StubRuntime(llm=_StubLLM(response="SELECT 1"))
    asyncio.run(correct_sql(_state(), _cfg_with(rt)))
    assert rt.nodes_called == 1
    nodes = [n for n, _ in rt.metrics.latencies]
    assert "correct_sql" in nodes


def test_correct_sql_node_history_records_attempts():
    rt = _StubRuntime(llm=_StubLLM(response="SELECT 1"))
    out = asyncio.run(correct_sql(_state(attempts=2), _cfg_with(rt)))
    nh = out["node_history"][-1]
    assert nh["node"] == "correct_sql"
    assert nh["status"] == "ok"
    assert nh["attempts"] == 2
    assert nh["llm_invoked"] is True
    assert nh["sql_len"] == len("SELECT 1")


def test_correct_sql_node_history_marks_llm_invoked_false_when_no_llm():
    rt = _StubRuntime(llm=None)
    out = asyncio.run(correct_sql(_state(), _cfg_with(rt)))
    nh = out["node_history"][-1]
    assert nh["llm_invoked"] is False
    assert nh["status"] == "ok"  # SQL preserved -> still ok


def test_correct_sql_node_history_appends_to_existing_history():
    rt = _StubRuntime(llm=_StubLLM(response="SELECT 1"))
    state = _state()
    state["node_history"] = [{"node": "validate_sql", "status": "fail", "ms": 5.0}]
    out = asyncio.run(correct_sql(state, _cfg_with(rt)))
    assert len(out["node_history"]) == 2
    assert out["node_history"][0]["node"] == "validate_sql"
    assert out["node_history"][1]["node"] == "correct_sql"


# ---------- 6.11.9 no extra side-effects ----------

def test_correct_sql_does_not_call_record_sql_generated():
    """correct_sql should only record llm_call + node_latency. The
    `record_sql_generated` counter is owned by generate_sql only."""
    class _M(_StubMetrics):
        def __init__(self):
            super().__init__()
            self.sql_generated_called = 0

        def record_sql_generated(self):
            self.sql_generated_called += 1

    metrics = _M()
    rt = _StubRuntime(llm=_StubLLM(response="SELECT 1"), metrics=metrics)
    asyncio.run(correct_sql(_state(), _cfg_with(rt)))
    assert metrics.sql_generated_called == 0


def test_correct_sql_uses_filtered_or_merged_or_table_infos_in_prompt():
    """Prompt should pull from filtered -> merged -> table_infos, in that order."""
    llm = _StubLLM(response="SELECT 1")
    rt = _StubRuntime(llm=llm)
    state = _state()
    state["filtered_table_infos"] = {
        "fact_order_a": {"columns": [{"id": "a.x"}]},
    }
    state["merged_table_infos"] = {
        "fact_order_b": {"columns": [{"id": "b.x"}]},
    }
    state["table_infos"] = {
        "fact_order_c": {"columns": [{"id": "c.x"}]},
    }
    asyncio.run(correct_sql(state, _cfg_with(rt)))
    # filtered_table_infos wins
    assert "fact_order_a" in llm.calls[0]
    assert "fact_order_b" not in llm.calls[0]
    assert "fact_order_c" not in llm.calls[0]


def test_correct_sql_prompt_contains_metric_infos():
    llm = _StubLLM(response="SELECT 1")
    rt = _StubRuntime(llm=llm)
    asyncio.run(correct_sql(
        _state(metric_infos=[{"id": "GMV"}, {"id": "ORDER_CNT"}]),
        _cfg_with(rt),
    ))
    prompt = llm.calls[0]
    assert "GMV" in prompt
    assert "ORDER_CNT" in prompt


def test_correct_sql_event_uses_state_request_id_not_runtime_default():
    rt = _StubRuntime(llm=_StubLLM(response="SELECT 1"))
    out = asyncio.run(correct_sql(
        _state(request_id="custom-req-id-9"),
        _cfg_with(rt),
    ))
    assert out["pending_stream_events"][0]["request_id"] == "custom-req-id-9"


def test_correct_sql_multiple_invocations_keep_independent_events():
    """Each invocation produces exactly one event; old runtime events are kept."""
    rt = _StubRuntime(llm=_StubLLM(response="SELECT 1"))
    asyncio.run(correct_sql(_state(), _cfg_with(rt)))
    asyncio.run(correct_sql(_state(), _cfg_with(rt)))
    # runtime queue accumulates both events
    assert len(rt.pending_events) == 2
    assert all(ev["type"] == "sql_corrected" for ev in rt.pending_events)
    # but each invocation returns its own single-event list
    assert rt.nodes_called == 2