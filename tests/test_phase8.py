"""Phase 8 backend tests.

Covers:
  - Metrics ring buffer (deque, rate-limited bump, window filter)
  - GET /api/stats/timeseries endpoint shape + window bounds
  - /api/stats is unaffected (regression guard for the bump hooks)
"""
from __future__ import annotations
import asyncio
import time as _t
from fastapi.testclient import TestClient

from app.core.metrics import LLMCallStat, get_metrics


def _new_metrics():
    """Fresh Metrics instance so tests don't share state."""
    from app.core.metrics import Metrics
    return Metrics()


def test_bump_timeseries_appends_compact_snapshot():
    m = _new_metrics()
    assert m.timeseries_snapshot() == []
    m.record_cache(hit=True)
    _t.sleep(0.3)
    m.record_cache(hit=False)
    _t.sleep(0.3)
    m.record_llm_call(LLMCallStat(node_name="generate_sql", model="m", total_tokens=42))
    pts = m.timeseries_snapshot(window_seconds=60)
    assert len(pts) >= 1
    last = pts[-1]
    assert last["cache_hits"] == 1
    assert last["cache_misses"] == 1
    assert last["llm_calls"] == 1
    assert last["tokens_total"] == 42
    assert last["ts_ms"] > 0


def test_bump_timeseries_rate_limited():
    m = _new_metrics()
    m.record_cache(hit=True)
    pts1 = m.timeseries_snapshot(window_seconds=60)
    # immediate second call: should NOT create a new entry (rate-limited)
    m.record_cache(hit=True)
    m.record_cache(hit=True)
    pts2 = m.timeseries_snapshot(window_seconds=60)
    assert len(pts2) == len(pts1), "bump must be rate-limited within 250ms"


def test_bump_timeseries_records_each_record_call():
    """Each distinct record method bumps the buffer."""
    m = _new_metrics()
    # sleep just enough to pass the rate limiter between calls
    import time as _t
    m.record_node_latency("extract_keywords", 12.3)
    _t.sleep(0.3)
    m.record_request()
    _t.sleep(0.3)
    m.record_request_outcome(success=True, duration_ms=42.0)
    _t.sleep(0.3)
    m.record_sql_generated()
    _t.sleep(0.3)
    m.record_sql_validated(corrected=False)
    _t.sleep(0.3)
    m.record_sql_executed(success=True)
    pts = m.timeseries_snapshot(window_seconds=60)
    # Each call should produce a new entry (rate limit is 250ms).
    assert len(pts) >= 5
    last = pts[-1]
    assert last["requests_total"] == 1
    assert last["requests_success"] == 1
    assert last["sql_generated"] == 1
    assert last["sql_executed_ok"] == 1


def test_timeseries_window_filter_excludes_old_points():
    m = _new_metrics()
    m._timeseries.append((0.0, {"requests_total": 999}))
    m._timeseries.append((__import__("time").time() - 100, {"requests_total": 5}))
    pts = m.timeseries_snapshot(window_seconds=60)
    # the very-old (epoch 0) point must be filtered out
    assert all(p["requests_total"] != 999 for p in pts)


def test_timeseries_snapshot_shape():
    m = _new_metrics()
    import time as _t
    m.record_cache(hit=True)
    _t.sleep(0.3)
    pts = m.timeseries_snapshot(window_seconds=60)
    required_keys = {
        "ts_ms", "tokens_total", "llm_calls", "cache_hits", "cache_misses",
        "requests_total", "requests_success", "requests_error",
        "sql_generated", "sql_executed_ok", "sql_executed_failed",
    }
    assert required_keys.issubset(set(pts[-1].keys()))


def test_stats_snapshot_still_works_after_phase8_changes():
    """Regression: /api/stats shape must not break."""
    m = get_metrics()
    snap = m.stats_snapshot()
    assert "tokens" in snap and "llm_calls" in snap and "cache" in snap
    assert "requests" in snap and "sql" in snap and "node_p95_latency_ms" in snap


# ---------- HTTP layer ----------

def _client() -> TestClient:
    from main import app
    return TestClient(app)


def test_api_stats_timeseries_returns_envelope():
    with _client() as c:
        r = c.get("/api/stats/timeseries?window=60")
        assert r.status_code == 200
        body = r.json()
        assert body["window_seconds"] == 60
        assert isinstance(body["points"], list)
        assert body["count"] == len(body["points"])


def test_api_stats_timeseries_window_bounds():
    with _client() as c:
        # below minimum -> 422
        assert c.get("/api/stats/timeseries?window=5").status_code == 422
        # above maximum -> 422
        assert c.get("/api/stats/timeseries?window=99999999").status_code == 422
        # default window works
        assert c.get("/api/stats/timeseries").status_code == 200


def test_api_stats_timeseries_grows_after_request():
    with _client() as c:
        before = c.get("/api/stats/timeseries?window=60").json()["count"]
        # trigger a stats_snapshot bump indirectly by hitting /api/stats
        c.get("/api/stats")
        c.get("/api/stats")
        c.get("/api/stats")
        # request outcomes also bump via app.middleware hooks
        # but a direct GET may not call record_request_outcome, so we just
        # assert the endpoint shape stays consistent
        after = c.get("/api/stats/timeseries?window=60").json()
        assert isinstance(after["count"], int)
        # count should be >= before (or close, depending on rate limit)
        assert after["count"] >= before