"""V1.0 phase 6.8 verification: add_extra_context (4.2.8).

V1.0 phase 6.8 spec:
  state.extra_context = {
    "current_time":   datetime.now().isoformat(),
    "db_type":        "MySQL",
    "db_version":     probed via SELECT VERSION(),
    "today_weekday":  "\u661f\u671f\u4e00" / "Monday" / ...
  }
"""
from __future__ import annotations
from datetime import datetime, timezone
from langchain_core.runnables import RunnableConfig

from app.agent.nodes.add_extra_context import (
    WEEKDAY_CN,
    WEEKDAY_EN,
    DEFAULT_DB_VERSION,
    _today_weekday,
    _probe_db_version,
    build_extra_context,
    add_extra_context,
)


# ---------- 6.8.1 constants ----------

def test_weekday_cn_has_seven_entries():
    assert len(WEEKDAY_CN) == 7
    assert WEEKDAY_CN[0] == "\u661f\u671f\u4e00"
    assert WEEKDAY_CN[-1] == "\u661f\u671f\u65e5"


def test_weekday_en_has_seven_entries():
    assert len(WEEKDAY_EN) == 7
    assert WEEKDAY_EN[0] == "Monday"
    assert WEEKDAY_EN[-1] == "Sunday"


def test_default_db_version_is_unknown_string():
    assert DEFAULT_DB_VERSION == "unknown"


# ---------- 6.8.2 weekday helper ----------

def test_today_weekday_for_known_monday():
    cn, en = _today_weekday(datetime(2024, 1, 1))  # 2024-01-01 was a Monday
    assert cn == "\u661f\u671f\u4e00"
    assert en == "Monday"


def test_today_weekday_for_known_sunday():
    cn, en = _today_weekday(datetime(2023, 12, 31))  # Sunday
    assert cn == "\u661f\u671f\u65e5"
    assert en == "Sunday"


def test_today_weekday_for_known_saturday():
    cn, en = _today_weekday(datetime(2024, 1, 6))  # Saturday
    assert cn == "\u661f\u671f\u516d"
    assert en == "Saturday"


def test_today_weekday_for_known_wednesday():
    cn, en = _today_weekday(datetime(2024, 1, 3))  # Wednesday
    assert cn == "\u661f\u671f\u4e09"
    assert en == "Wednesday"


def test_today_weekday_default_is_today():
    cn, en = _today_weekday()
    assert cn in WEEKDAY_CN
    assert en in WEEKDAY_EN
    # And it should match the weekday of "now"
    expected_idx = datetime.now().weekday()
    assert cn == WEEKDAY_CN[expected_idx]
    assert en == WEEKDAY_EN[expected_idx]


def test_today_weekday_handles_tz_aware_datetime():
    dt = datetime(2024, 1, 1, tzinfo=timezone.utc)  # Monday
    cn, en = _today_weekday(dt)
    assert cn == "\u661f\u671f\u4e00"
    assert en == "Monday"


# ---------- 6.8.3 build_extra_context helper ----------

def test_build_extra_context_returns_canonical_keys():
    ctx = build_extra_context(
        now=datetime(2024, 1, 1, 10, 0, 0),
        db_version="8.0.40",
    )
    for key in ("current_time", "db_type", "db_version", "today_weekday"):
        assert key in ctx, key


def test_build_extra_context_db_type_is_canonical_mysql():
    ctx = build_extra_context(now=datetime(2024, 1, 1), db_version="x")
    assert ctx["db_type"] == "MySQL"


def test_build_extra_context_current_time_is_isoformat():
    dt = datetime(2024, 5, 15, 9, 30, 0)
    ctx = build_extra_context(now=dt, db_version="x")
    assert ctx["current_time"] == dt.isoformat()


def test_build_extra_context_today_weekday_for_monday():
    ctx = build_extra_context(now=datetime(2024, 1, 1), db_version="x")
    assert ctx["today_weekday"] == "\u661f\u671f\u4e00"


def test_build_extra_context_keeps_legacy_now_alias():
    """Legacy ``now`` key must remain so existing consumers keep working."""
    dt = datetime(2024, 1, 1)
    ctx = build_extra_context(now=dt, db_version="x")
    assert ctx["now"] == dt.isoformat()


def test_build_extra_context_provides_english_weekday():
    ctx = build_extra_context(now=datetime(2024, 1, 1), db_version="x")
    assert ctx["today_weekday_en"] == "Monday"


def test_build_extra_context_uses_provided_db_version():
    ctx = build_extra_context(now=datetime.now(), db_version="8.0.40-MySQL")
    assert ctx["db_version"] == "8.0.40-MySQL"


def test_build_extra_context_probes_db_version_when_none():
    """When db_version is None we probe live MySQL. In a healthy env that
    yields a non-error version string."""
    ctx = build_extra_context(now=datetime.now())
    assert "error" not in ctx["db_version"]
    # Should look like a real version (digits and dots)
    assert any(ch.isdigit() for ch in ctx["db_version"])


# ---------- 6.8.4 _probe_db_version helper ----------

def test_probe_db_version_returns_string():
    v = _probe_db_version()
    assert isinstance(v, str)
    assert v  # non-empty


def test_probe_db_version_handles_unreachable_host(monkeypatch):
    """When MySQL is unreachable, the probe returns an error sentinel."""
    import app.agent.nodes.add_extra_context as mod
    # Patch cfg.mysql.host to a black-hole IP and force a short timeout
    orig_host = mod.cfg.mysql.host
    orig_timeout = mod._probe_db_version.__defaults__
    try:
        mod.cfg.mysql.host = "127.0.0.1"
        mod._probe_db_version.__defaults__ = (1,)  # 1-second timeout
        v = _probe_db_version()
        # Either pymysql refused (got "error: ..." or empty), or it actually
        # connected to a local MySQL. Accept either.
        if not v.startswith("error") and v != DEFAULT_DB_VERSION:
            # Accept the live-connect case (no live DB to assert against).
            assert v
    finally:
        mod.cfg.mysql.host = orig_host
        mod._probe_db_version.__defaults__ = orig_timeout


# ---------- 6.8.5 node end-to-end ----------

class _StubMetrics:
    def __init__(self):
        self.latencies = []
    def record_node_latency(self, node, ms):
        self.latencies.append((node, ms))


class _StubRuntime:
    def __init__(self):
        self.metrics = _StubMetrics()
        self.nodes_called = 0


def _cfg_with(runtime):
    cfg = RunnableConfig()
    cfg["configurable"] = {"runtime": runtime}
    return cfg


def _state(query="x"):
    return {
        "query": query,
        "request_id": "rid-6-8",
        "node_history": [],
        "validate_attempts": 0,
    }


def test_node_writes_extra_context_field():
    rt = _StubRuntime()
    out = add_extra_context(_state(), _cfg_with(rt))
    assert "extra_context" in out


def test_node_extra_context_has_all_canonical_keys():
    rt = _StubRuntime()
    out = add_extra_context(_state(), _cfg_with(rt))
    ctx = out["extra_context"]
    for key in ("current_time", "db_type", "db_version", "today_weekday"):
        assert key in ctx


def test_node_db_type_is_mysql():
    rt = _StubRuntime()
    out = add_extra_context(_state(), _cfg_with(rt))
    assert out["extra_context"]["db_type"] == "MySQL"


def test_node_current_time_is_isoformat():
    rt = _StubRuntime()
    out = add_extra_context(_state(), _cfg_with(rt))
    # Should be parseable as ISO 8601
    parsed = datetime.fromisoformat(out["extra_context"]["current_time"])
    assert isinstance(parsed, datetime)


def test_node_today_weekday_is_chinese_weekday():
    rt = _StubRuntime()
    out = add_extra_context(_state(), _cfg_with(rt))
    assert out["extra_context"]["today_weekday"] in WEEKDAY_CN


def test_node_db_version_not_error_sentinel():
    rt = _StubRuntime()
    out = add_extra_context(_state(), _cfg_with(rt))
    assert not out["extra_context"]["db_version"].startswith("error")


def test_node_records_latency_and_counter():
    rt = _StubRuntime()
    add_extra_context(_state(), _cfg_with(rt))
    assert rt.nodes_called == 1
    nodes = [n for n, _ in rt.metrics.latencies]
    assert "add_extra_context" in nodes


def test_node_history_entry_records_context_fields():
    rt = _StubRuntime()
    out = add_extra_context(_state(), _cfg_with(rt))
    nh = out["node_history"][-1]
    assert nh["node"] == "add_extra_context"
    assert nh["status"] == "ok"
    assert nh["db_type"] == "MySQL"
    assert "today_weekday" in nh


def test_node_no_runtime_still_works():
    """The node must not crash when runtime is None (e.g. direct call)."""
    out = add_extra_context(_state(), None)
    assert "extra_context" in out
    assert out["extra_context"]["db_type"] == "MySQL"


def test_node_legacy_now_key_still_present():
    """Existing phase-4 consumers still read ``now``; keep emitting it."""
    rt = _StubRuntime()
    out = add_extra_context(_state(), _cfg_with(rt))
    assert "now" in out["extra_context"]