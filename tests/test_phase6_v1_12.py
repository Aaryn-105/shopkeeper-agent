"""V1.0 phase 6.12 verification: run_sql (4.2.12).

V1.0 phase 6.12 spec:
  - result_cache_key = sha256(state.sql)
  - Cache hit -> state.execution_result = cached.result_json, cache_hit_result=True
  - On miss -> execute and write to query_cache (TTL=3600)
  - stream_writer({"type":"result","columns":[...],"rows":[...],"row_count":N,
                    "request_id":...})
  - Generate state.explanation (no cache) and stream_writer({"type":"done",
    "request_id":..., "duration_ms":..., "explanation":...})
"""
from __future__ import annotations
import asyncio
import hashlib
import json
import pytest
from langchain_core.runnables import RunnableConfig

from app.agent.nodes.run_sql import (
    RESULT_CACHE_TTL_SECONDS,
    _build_explanation,
    _format_result_preview,
    _load_prompt_template,
    make_result_cache_key,
    run_sql,
)


# ---------- 6.12.1 constants & helpers ----------

def test_result_cache_ttl_is_one_hour():
    assert RESULT_CACHE_TTL_SECONDS == 3600


def test_make_result_cache_key_is_sha256_hex():
    key = make_result_cache_key("SELECT 1")
    assert len(key) == 64
    int(key, 16)  # raises if not hex


def test_make_result_cache_key_changes_with_sql():
    k1 = make_result_cache_key("SELECT 1")
    k2 = make_result_cache_key("SELECT 2")
    assert k1 != k2


def test_make_result_cache_key_matches_manual_sha256():
    sql = "SELECT * FROM fact_order"
    expected = hashlib.sha256(sql.encode("utf-8")).hexdigest()
    assert make_result_cache_key(sql) == expected


def test_make_result_cache_key_empty_sql():
    key = make_result_cache_key("")
    assert len(key) == 64


def test_format_result_preview_handles_empty():
    assert _format_result_preview({"columns": [], "rows": []}) == "(empty)"


def test_format_result_preview_caps_to_5_rows():
    cols = ["a"]
    rows = [[i] for i in range(20)]
    out = _format_result_preview({"columns": cols, "rows": rows})
    parsed = json.loads(out)
    assert len(parsed["rows"]) == 5


def test_build_explanation_for_empty_result():
    text = _build_explanation("last month GMV", "SELECT 1",
                              {"columns": ["v"], "rows": [], "row_count": 0})
    assert "无返回结果" in text or "无" in text


def test_build_explanation_for_single_value():
    text = _build_explanation("total", "SELECT SUM(x) AS v FROM t",
                              {"columns": ["v"], "rows": [[1234]], "row_count": 1})
    assert "1234" in text


def test_build_explanation_for_multi_rows():
    text = _build_explanation("GMV by region", "SELECT r, SUM(v) FROM t",
                              {"columns": ["region", "v"], "rows": [["A", 1], ["B", 2]],
                               "row_count": 2})
    assert "2" in text
    assert "region" in text


def test_load_prompt_template_has_placeholders():
    tpl = _load_prompt_template()
    assert tpl
    for k in ("{query}", "{sql}", "{result_preview}"):
        assert k in tpl, f"missing placeholder {k}"


# ---------- 6.12.2 runtime stubs ----------

class _FakeDW:
    """Mimics mysql_dw.execute_readonly returning a dict result."""

    def __init__(self, result=None, raise_exc: Exception | None = None):
        self.result = result or {
            "columns": ["v"], "rows": [[42]], "row_count": 1, "truncated": False,
        }
        self.raise_exc = raise_exc
        self.calls: list[str] = []

    async def execute_readonly(self, sql):
        self.calls.append(sql)
        if self.raise_exc is not None:
            raise self.raise_exc
        return self.result


class _StubLLM:
    def __init__(self, response: str = "\u67e5\u8be2\u8fd4\u56de 1 \u884c\u7ed3\u679c\u3002"):
        self.response = response
        self.calls: list[str] = []

    async def ainvoke(self, prompt, system=None, response_format=None):
        self.calls.append(prompt)
        class _R:
            def __init__(self, text):
                self.text = text
                self.latency_ms = 0
        return _R(self.response)


class _StubCache:
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
        self.executed_ok = 0
        self.executed_fail = 0

    def record_node_latency(self, node, ms):
        self.latencies.append((node, ms))

    def record_llm_call(self, stat):
        self.llm_calls.append(stat)

    def record_sql_executed(self, success: bool):
        if success:
            self.executed_ok += 1
        else:
            self.executed_fail += 1


class _StubRuntime:
    def __init__(self, dw=None, llm=None, cache=None, metrics=None):
        self.mysql_dw = dw
        self.llm = llm
        self.cache = cache
        self.metrics = metrics if metrics is not None else _StubMetrics()
        self.pending_events: list[dict] = []
        self.nodes_called = 0


def _cfg_with(runtime):
    cfg = RunnableConfig()
    cfg["configurable"] = {"runtime": runtime}
    return cfg


def _state(sql="SELECT 1", request_id="rid-6-12", query="\u67e5\u8be2"):
    return {
        "query": query,
        "request_id": request_id,
        "node_history": [],
        "sql": sql,
        "result": None,
        "error": None,
    }


# ---------- 6.12.3 cache miss path ----------

def test_run_sql_cache_miss_executes_against_dw():
    dw = _FakeDW(result={"columns": ["v"], "rows": [[100]], "row_count": 1, "truncated": False})
    rt = _StubRuntime(dw=dw)
    out = asyncio.run(run_sql(_state(sql="SELECT 100"), _cfg_with(rt)))
    assert dw.calls == ["SELECT 100"]
    assert out["result"]["rows"] == [[100]]
    assert out["cache_hit_result"] is False
    assert out["error"] is None


def test_run_sql_writes_to_cache_on_miss():
    cache = _StubCache()
    dw = _FakeDW(result={"columns": ["v"], "rows": [[42]], "row_count": 1, "truncated": False})
    rt = _StubRuntime(dw=dw, cache=cache)
    asyncio.run(run_sql(_state(sql="SELECT 42"), _cfg_with(rt)))
    expected_key = make_result_cache_key("SELECT 42")
    assert cache.put_calls and cache.put_calls[0][0] == expected_key
    assert "result" in cache.put_calls[0][1]


def test_run_sql_records_sql_executed_success():
    dw = _FakeDW()
    metrics = _StubMetrics()
    rt = _StubRuntime(dw=dw, metrics=metrics)
    asyncio.run(run_sql(_state(), _cfg_with(rt)))
    assert metrics.executed_ok == 1
    assert metrics.executed_fail == 0


def test_run_sql_records_sql_executed_failure():
    dw = _FakeDW(raise_exc=RuntimeError("dw down"))
    metrics = _StubMetrics()
    rt = _StubRuntime(dw=dw, metrics=metrics)
    asyncio.run(run_sql(_state(), _cfg_with(rt)))
    assert metrics.executed_ok == 0
    assert metrics.executed_fail == 1


# ---------- 6.12.4 cache hit path ----------

def test_run_sql_cache_hit_skips_dw():
    cache = _StubCache()
    sql = "SELECT cached"
    cached_result = {"columns": ["v"], "rows": [[999]], "row_count": 1, "truncated": False}
    cache.put(make_result_cache_key(sql), {"result": cached_result, "sql": sql})
    dw = _FakeDW(result={"columns": ["v"], "rows": [[1]], "row_count": 1, "truncated": False})
    rt = _StubRuntime(dw=dw, cache=cache)
    out = asyncio.run(run_sql(_state(sql=sql), _cfg_with(rt)))
    assert dw.calls == []  # NOT executed
    assert out["result"] == cached_result
    assert out["cache_hit_result"] is True
    assert out["execution_result"] == cached_result


def test_run_sql_cache_hit_does_not_write_back_to_cache():
    cache = _StubCache()
    sql = "SELECT x"
    cache.put(make_result_cache_key(sql), {"result": {"columns": ["v"], "rows": [[1]], "row_count": 1, "truncated": False}})
    initial_put_count = len(cache.put_calls)
    rt = _StubRuntime(dw=_FakeDW(), cache=cache)
    asyncio.run(run_sql(_state(sql=sql), _cfg_with(rt)))
    assert len(cache.put_calls) == initial_put_count


def test_run_sql_cache_hit_emits_cache_hit_true_in_result_event():
    cache = _StubCache()
    sql = "SELECT 1"
    cache.put(make_result_cache_key(sql), {"result": {"columns": ["v"], "rows": [[1]], "row_count": 1, "truncated": False}})
    rt = _StubRuntime(cache=cache)
    out = asyncio.run(run_sql(_state(sql=sql), _cfg_with(rt)))
    result_evt = out["pending_stream_events"][0]
    assert result_evt["cache_hit"] is True


# ---------- 6.12.5 result event ----------

def test_run_sql_emits_result_event_with_columns_rows_row_count():
    dw = _FakeDW(result={"columns": ["c1", "c2"], "rows": [["a", "b"], ["c", "d"]], "row_count": 2, "truncated": False})
    rt = _StubRuntime(dw=dw)
    out = asyncio.run(run_sql(_state(request_id="rid-result"), _cfg_with(rt)))
    ev = out["pending_stream_events"][0]
    assert ev["type"] == "result"
    assert ev["columns"] == ["c1", "c2"]
    assert ev["rows"] == [["a", "b"], ["c", "d"]]
    assert ev["row_count"] == 2
    assert ev["request_id"] == "rid-result"
    assert ev["truncated"] is False


def test_run_sql_result_event_also_pushed_to_runtime_queue():
    dw = _FakeDW()
    rt = _StubRuntime(dw=dw)
    asyncio.run(run_sql(_state(), _cfg_with(rt)))
    assert any(e["type"] == "result" for e in rt.pending_events)


# ---------- 6.12.6 explanation (never cached) ----------

def test_run_sql_generates_explanation_via_llm():
    dw = _FakeDW(result={"columns": ["v"], "rows": [[100]], "row_count": 1, "truncated": False})
    llm = _StubLLM(response="\u67e5\u8be2\u8fd4\u56de 100\u3002")
    rt = _StubRuntime(dw=dw, llm=llm)
    out = asyncio.run(run_sql(_state(query="\u603b\u9500\u552e\u989d"), _cfg_with(rt)))
    assert "\u67e5\u8be2\u8fd4\u56de 100" in out["explanation"]
    assert len(llm.calls) == 1
    assert "\u603b\u9500\u552e\u989d" in llm.calls[0]


def test_run_sql_records_llm_call_with_node_explain_result():
    dw = _FakeDW()
    metrics = _StubMetrics()
    rt = _StubRuntime(dw=dw, llm=_StubLLM(), metrics=metrics)
    asyncio.run(run_sql(_state(), _cfg_with(rt)))
    assert any(c.node_name == "explain_result" for c in metrics.llm_calls)


def test_run_sql_falls_back_to_template_explanation_when_no_llm():
    dw = _FakeDW(result={"columns": ["v"], "rows": [[42]], "row_count": 1, "truncated": False})
    rt = _StubRuntime(dw=dw, llm=None)
    out = asyncio.run(run_sql(_state(query="\u67e5\u8be2"), _cfg_with(rt)))
    assert "\u67e5\u8be2" in out["explanation"]
    assert "42" in out["explanation"]


def test_run_sql_falls_back_to_template_explanation_when_llm_raises():
    class _BoomLLM:
        async def ainvoke(self, *a, **kw):
            raise RuntimeError("network down")
    dw = _FakeDW(result={"columns": ["v"], "rows": [[1]], "row_count": 1, "truncated": False})
    rt = _StubRuntime(dw=dw, llm=_BoomLLM())
    out = asyncio.run(run_sql(_state(query="\u67e5\u8be2"), _cfg_with(rt)))
    assert out["explanation"]


def test_run_sql_explanation_is_not_cached():
    """V1.0 spec: explanation never cached -> state has no explanation_cache field."""
    dw = _FakeDW()
    cache = _StubCache()
    rt = _StubRuntime(dw=dw, llm=_StubLLM(), cache=cache)
    out = asyncio.run(run_sql(_state(), _cfg_with(rt)))
    # result cache only contains the result, not explanation
    for _, value in cache.put_calls:
        assert "explanation" not in value
    assert "explanation_cache" not in out


def test_run_sql_explanation_reflects_execution_error():
    dw = _FakeDW(raise_exc=RuntimeError("dw down"))
    rt = _StubRuntime(dw=dw, llm=None)
    out = asyncio.run(run_sql(_state(), _cfg_with(rt)))
    assert "\u6267\u884c\u5931\u8d25" in out["explanation"] or "\u5931\u8d25" in out["explanation"]


# ---------- 6.12.7 done event ----------

def test_run_sql_emits_done_event_with_duration_and_explanation():
    dw = _FakeDW()
    rt = _StubRuntime(dw=dw, llm=_StubLLM(response="\u603b\u8ba1 100\u3002"))
    out = asyncio.run(run_sql(_state(request_id="rid-done"), _cfg_with(rt)))
    evs = out["pending_stream_events"]
    done = next(e for e in evs if e["type"] == "done")
    assert done["type"] == "done"
    assert done["request_id"] == "rid-done"
    assert "duration_ms" in done
    assert done["duration_ms"] >= 0
    assert done["explanation"] == "\u603b\u8ba1 100\u3002"
    assert "sql" in done
    assert "row_count" in done


def test_run_sql_done_event_also_pushed_to_runtime_queue():
    dw = _FakeDW()
    rt = _StubRuntime(dw=dw, llm=_StubLLM())
    asyncio.run(run_sql(_state(), _cfg_with(rt)))
    assert any(e["type"] == "done" for e in rt.pending_events)


def test_run_sql_done_event_duration_uses_started_at_when_present():
    """When state.started_at is set, duration_ms reflects end-to-end latency."""
    import time
    state = _state()
    state["started_at"] = time.perf_counter() - 0.1  # 100ms ago
    dw = _FakeDW()
    rt = _StubRuntime(dw=dw, llm=_StubLLM())
    out = asyncio.run(run_sql(state, _cfg_with(rt)))
    done = next(e for e in out["pending_stream_events"] if e["type"] == "done")
    assert done["duration_ms"] >= 100.0


# ---------- 6.12.8 metrics + counters ----------

def test_run_sql_records_node_latency_and_counter():
    rt = _StubRuntime(dw=_FakeDW(), llm=_StubLLM())
    asyncio.run(run_sql(_state(), _cfg_with(rt)))
    assert rt.nodes_called == 1
    assert any(n == "run_sql" for n, _ in rt.metrics.latencies)


def test_run_sql_node_history_records_cache_hit():
    cache = _StubCache()
    sql = "SELECT 1"
    cache.put(make_result_cache_key(sql), {"result": {"columns": ["v"], "rows": [[1]], "row_count": 1, "truncated": False}})
    rt = _StubRuntime(cache=cache)
    out = asyncio.run(run_sql(_state(sql=sql), _cfg_with(rt)))
    nh = out["node_history"][-1]
    assert nh["node"] == "run_sql"
    assert nh["status"] == "cache_hit"
    assert nh["cache_hit"] is True
    assert nh["rows"] == 1


def test_run_sql_node_history_records_cache_miss():
    dw = _FakeDW()
    rt = _StubRuntime(dw=dw, llm=_StubLLM())
    out = asyncio.run(run_sql(_state(), _cfg_with(rt)))
    nh = out["node_history"][-1]
    assert nh["status"] == "ok"
    assert nh["cache_hit"] is False


def test_run_sql_no_runtime_returns_safe_defaults():
    """Defensive: no runtime -> no crash, no cache, no dw, but still emit done."""
    out = asyncio.run(run_sql(_state(), None))
    assert out["result"]
    assert "explanation" in out
    # Still emits two events even without runtime
    assert len(out["pending_stream_events"]) == 2


def test_run_sql_two_invocations_same_sql_second_hits_cache():
    """End-to-end: first call writes cache, second call reads it."""
    cache = _StubCache()
    dw = _FakeDW(result={"columns": ["v"], "rows": [[7]], "row_count": 1, "truncated": False})
    rt = _StubRuntime(dw=dw, llm=_StubLLM(), cache=cache)
    out1 = asyncio.run(run_sql(_state(sql="SELECT 7"), _cfg_with(rt)))
    out2 = asyncio.run(run_sql(_state(sql="SELECT 7"), _cfg_with(rt)))
    assert out1["cache_hit_result"] is False
    assert out2["cache_hit_result"] is True
    # Second call did NOT hit DW
    assert dw.calls == ["SELECT 7"]  # only first call