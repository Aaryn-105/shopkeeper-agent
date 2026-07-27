"""V1.0 phase 6.10 verification: validate_sql (4.2.10).

V1.0 phase 6.10 spec:
  - dw_ro_engine.execute_readonly(f"EXPLAIN {state.sql}")
  - Failure -> state.sql_error (legacy) AND state.error (SRS canonical).
  - Increments validate_attempts so correct_sql can decide when to stop.
  - Emits pending_stream_events of type "validate_sql" with pass/fail info.
"""
from __future__ import annotations
import pytest
from langchain_core.runnables import RunnableConfig

from app.agent.nodes.validate_sql import (
    _run_explain,
    validate_sql,
)


# ---------- 6.10.1 _run_explain helper ----------

class _FakeValidator:
    """Returns (ok, message) tuple."""

    def __init__(self, ok: bool = True, msg: str = "ok"):
        self._ok = ok
        self._msg = msg
        self.calls = []

    def validate(self, sql):
        self.calls.append(sql)
        return self._ok, self._msg


class _StubRuntime:
    def __init__(self, validator=None, mysql_dw=None):
        self.validator = validator
        self.mysql_dw = mysql_dw


def test_run_explain_uses_validator_when_present():
    v = _FakeValidator(ok=True)
    rt = _StubRuntime(validator=v)
    ok, err = _run_explain("SELECT 1", rt)
    assert ok is True
    assert err is None
    assert v.calls == ["SELECT 1"]


def test_run_explain_returns_error_message_when_validator_says_no():
    v = _FakeValidator(ok=False, msg="bad sql")
    rt = _StubRuntime(validator=v)
    ok, err = _run_explain("SELECT bad_col FROM t", rt)
    assert ok is False
    assert err == "bad sql"


def test_run_explain_handles_validator_exception():
    class _BoomValidator:
        def validate(self, sql):
            raise RuntimeError("validator down")
    rt = _StubRuntime(validator=_BoomValidator())
    ok, err = _run_explain("SELECT 1", rt)
    assert ok is False
    assert err and "validator_error" in err


def test_run_explain_passes_when_no_validator_no_dw():
    rt = _StubRuntime()
    ok, err = _run_explain("SELECT 1", rt)
    # No validator / no mysql_dw -> we treat the env as unavailable, not the
    # SQL as broken. ok=True, err=None keeps the rest of the workflow moving.
    assert ok is True
    assert err is None


# ---------- 6.10.2 runtime stubs ----------

class _StubMetrics:
    def __init__(self):
        self.latencies = []
        self.validated_total = 0
        self.validated_corrected = 0

    def record_node_latency(self, node, ms):
        self.latencies.append((node, ms))

    def record_sql_validated(self, corrected: bool = False):
        self.validated_total += 1
        if corrected:
            self.validated_corrected += 1


class _StubRuntimeFull:
    def __init__(self, validator=None, mysql_dw=None):
        self.metrics = _StubMetrics()
        self.validator = validator
        self.mysql_dw = mysql_dw
        self.pending_events = []
        self.nodes_called = 0


def _cfg_with(runtime):
    cfg = RunnableConfig()
    cfg["configurable"] = {"runtime": runtime}
    return cfg


def _state(sql="SELECT 1"):
    return {
        "query": "x",
        "request_id": "rid-6-10",
        "node_history": [],
        "validate_attempts": 0,
        "sql": sql,
    }


# ---------- 6.10.3 node behaviour ----------

def test_validate_sql_returns_legacy_sql_error_when_ok():
    rt = _StubRuntimeFull(validator=_FakeValidator(ok=True))
    out = validate_sql(_state(), _cfg_with(rt))
    assert out["sql_error"] is None


def test_validate_sql_writes_srs_canonical_error_when_ok():
    rt = _StubRuntimeFull(validator=_FakeValidator(ok=True))
    out = validate_sql(_state(), _cfg_with(rt))
    assert out["error"] is None


def test_validate_sql_writes_legacy_sql_error_when_fail():
    rt = _StubRuntimeFull(validator=_FakeValidator(ok=False, msg="bad sql"))
    out = validate_sql(_state(), _cfg_with(rt))
    assert out["sql_error"] == "bad sql"


def test_validate_sql_writes_srs_canonical_error_when_fail():
    rt = _StubRuntimeFull(validator=_FakeValidator(ok=False, msg="bad sql"))
    out = validate_sql(_state(), _cfg_with(rt))
    assert out["error"] == "bad sql"


def test_validate_sql_increments_validate_attempts():
    rt = _StubRuntimeFull(validator=_FakeValidator(ok=True))
    out = validate_sql(_state(), _cfg_with(rt))
    assert out["validate_attempts"] == 1


def test_validate_sql_increments_attempts_from_existing_state():
    rt = _StubRuntimeFull(validator=_FakeValidator(ok=True))
    state = _state()
    state["validate_attempts"] = 2
    out = validate_sql(state, _cfg_with(rt))
    assert out["validate_attempts"] == 3


def test_validate_sql_emits_pending_stream_event_on_pass():
    rt = _StubRuntimeFull(validator=_FakeValidator(ok=True))
    out = validate_sql(_state(sql="SELECT 1 FROM fact_order"), _cfg_with(rt))
    assert "pending_stream_events" in out
    assert len(out["pending_stream_events"]) == 1
    ev = out["pending_stream_events"][0]
    assert ev["type"] == "validate_sql"
    assert ev["ok"] is True
    assert ev["error"] is None
    assert ev["sql"] == "SELECT 1 FROM fact_order"
    assert ev["request_id"] == "rid-6-10"
    assert ev["attempts"] == 1


def test_validate_sql_emits_pending_stream_event_on_fail():
    rt = _StubRuntimeFull(validator=_FakeValidator(ok=False, msg="column not found"))
    out = validate_sql(_state(sql="SELECT no_such_col"), _cfg_with(rt))
    ev = out["pending_stream_events"][0]
    assert ev["type"] == "validate_sql"
    assert ev["ok"] is False
    assert ev["error"] == "column not found"
    assert ev["sql"] == "SELECT no_such_col"


def test_validate_sql_pushes_event_to_runtime_queue():
    rt = _StubRuntimeFull(validator=_FakeValidator(ok=True))
    validate_sql(_state(), _cfg_with(rt))
    assert len(rt.pending_events) == 1
    assert rt.pending_events[0]["type"] == "validate_sql"


def test_validate_sql_records_node_latency_and_counter():
    rt = _StubRuntimeFull(validator=_FakeValidator(ok=True))
    validate_sql(_state(), _cfg_with(rt))
    assert rt.nodes_called == 1
    nodes = [n for n, _ in rt.metrics.latencies]
    assert "validate_sql" in nodes


def test_validate_sql_records_metric_with_corrected_false_on_pass():
    rt = _StubRuntimeFull(validator=_FakeValidator(ok=True))
    validate_sql(_state(), _cfg_with(rt))
    assert rt.metrics.validated_total == 1
    assert rt.metrics.validated_corrected == 0


def test_validate_sql_records_metric_with_corrected_true_on_fail():
    rt = _StubRuntimeFull(validator=_FakeValidator(ok=False, msg="bad"))
    validate_sql(_state(), _cfg_with(rt))
    assert rt.metrics.validated_total == 1
    assert rt.metrics.validated_corrected == 1


def test_validate_sql_node_history_entry_records_msg():
    rt = _StubRuntimeFull(validator=_FakeValidator(ok=False, msg="boom"))
    out = validate_sql(_state(), _cfg_with(rt))
    nh = out["node_history"][-1]
    assert nh["node"] == "validate_sql"
    assert nh["status"] == "fail"
    assert nh["msg"] == "boom"
    assert nh["attempts"] == 1


def test_validate_sql_node_history_records_ok_status():
    rt = _StubRuntimeFull(validator=_FakeValidator(ok=True))
    out = validate_sql(_state(), _cfg_with(rt))
    nh = out["node_history"][-1]
    assert nh["status"] == "ok"
    assert nh["msg"] == "ok"


def test_validate_sql_empty_sql_still_returns_clean_state():
    """An empty SQL string should not crash; the validator decides."""
    rt = _StubRuntimeFull(validator=_FakeValidator(ok=True))
    out = validate_sql(_state(sql=""), _cfg_with(rt))
    assert out["sql_error"] is None
    assert out["validate_attempts"] == 1


def test_validate_sql_no_runtime_returns_clean_state():
    out = validate_sql(_state(), None)
    assert out["sql_error"] is None
    assert out["error"] is None
    assert out["validate_attempts"] == 1


def test_validate_sql_no_runtime_no_validator_no_dw_marks_ok():
    """With no runtime we don\'t run EXPLAIN, but we also don\'t mark as failed."""
    out = validate_sql(_state(), None)
    assert out["pending_stream_events"][0]["ok"] is True


def test_validate_sql_uses_mysql_dw_execute_readonly_when_no_validator():
    """V1.0 spec: dw_ro_engine.execute_readonly(f"EXPLAIN {state.sql}")."""
    class _FakeDW:
        def __init__(self, ok=True):
            self._ok = ok
            self.calls = []

        def execute_readonly(self, sql):
            self.calls.append(sql)
            if not self._ok:
                raise RuntimeError("dw down")
            return _FakeCursor()

    class _FakeCursor:
        def fetchall(self):
            return [("plan",)]

    rt = _StubRuntimeFull(mysql_dw=_FakeDW())
    out = validate_sql(_state(sql="SELECT 1"), _cfg_with(rt))
    assert out["sql_error"] is None
    assert out["mysql_dw_calls" if False else "error"] is None
    # Verify EXPLAIN was issued
    assert rt.mysql_dw.calls == ["EXPLAIN SELECT 1"]


def test_validate_sql_mysql_dw_exception_writes_sql_error():
    class _BoomDW:
        def execute_readonly(self, sql):
            raise RuntimeError("connection refused")
    rt = _StubRuntimeFull(mysql_dw=_BoomDW())
    out = validate_sql(_state(sql="SELECT 1"), _cfg_with(rt))
    assert out["sql_error"] is not None
    assert "RuntimeError" in out["sql_error"] or "connection" in out["sql_error"]
    assert out["error"] == out["sql_error"]


def test_validate_sql_validator_message_preserved_in_event():
    rt = _StubRuntimeFull(validator=_FakeValidator(ok=False, msg="Unknown column \'x.y\'"))
    out = validate_sql(_state(), _cfg_with(rt))
    ev = out["pending_stream_events"][0]
    assert "Unknown column" in ev["error"]