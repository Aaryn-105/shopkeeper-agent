"""Phase 6.3 verification: GET /api/stats endpoint (user-added requirement + OPS-009 / OPS-010).

The /api/stats endpoint surfaces:
- tokens:           prompt / completion / total token usage across LLM calls
- llm_calls:        total LLM invocations + average latency
- cache:            hits / misses / total / hit_rate
- requests:         total / success / error / success_rate / avg_duration_ms / p95_duration_ms
- sql:              generated / validated_first_pass / corrected / executed_ok / executed_failed /
                    first_pass_rate / correction_rate / execution_success_rate
- node_p95_latency_ms: per-node P95 (already in v1.0)
- uptime_seconds:   process uptime

Tests cover:
  A. Endpoint shape (200, all expected keys present)
  B. User-required fields (tokens, llm_calls.total, cache.hit_rate)
  C. Counters react to events (cache hit/miss, sql generated/validated/executed,
     request success/error)
  D. Backwards compat with health.py flat-key summary + existing tests
"""
from __future__ import annotations
import pytest
from fastapi.testclient import TestClient

from main import app
from app.core.metrics import Metrics, LLMCallStat, get_metrics


# ---------- A. shape ----------

def test_stats_returns_200_and_top_level_keys():
    with TestClient(app) as client:
        r = client.get("/api/stats")
    assert r.status_code == 200
    body = r.json()
    expected = {
        "uptime_seconds",
        "tokens",
        "llm_calls",
        "cache",
        "requests",
        "sql",
        "node_p95_latency_ms",
    }
    assert expected <= set(body.keys())


def test_stats_user_required_fields_present():
    """User explicitly required: token consumption, LLM call count, cache hit rate."""
    with TestClient(app) as client:
        body = client.get("/api/stats").json()
    # tokens
    assert {"prompt", "completion", "total"} <= set(body["tokens"].keys())
    assert all(isinstance(body["tokens"][k], int) for k in ("prompt", "completion", "total"))
    # llm_calls
    assert {"total", "avg_latency_ms"} <= set(body["llm_calls"].keys())
    assert isinstance(body["llm_calls"]["total"], int)
    assert isinstance(body["llm_calls"]["avg_latency_ms"], (int, float))
    # cache.hit_rate
    assert {"hits", "misses", "total", "hit_rate"} <= set(body["cache"].keys())
    assert isinstance(body["cache"]["hit_rate"], (int, float))
    assert 0.0 <= body["cache"]["hit_rate"] <= 1.0


def test_stats_requests_block_shape():
    """OPS-009: 请求量、成功率、平均耗时、P95 耗时."""
    with TestClient(app) as client:
        body = client.get("/api/stats").json()
    keys = set(body["requests"].keys())
    assert {"total", "success", "error", "success_rate", "avg_duration_ms", "p95_duration_ms"} <= keys
    assert isinstance(body["requests"]["total"], int)
    assert isinstance(body["requests"]["success"], int)
    assert isinstance(body["requests"]["error"], int)
    assert 0.0 <= body["requests"]["success_rate"] <= 1.0


def test_stats_sql_block_shape():
    """OPS-010: SQL 生成准确率、首次校验通过率、校正率."""
    with TestClient(app) as client:
        body = client.get("/api/stats").json()
    keys = set(body["sql"].keys())
    assert {
        "generated",
        "validated_first_pass",
        "corrected",
        "executed_ok",
        "executed_failed",
        "executed_total",
        "first_pass_rate",
        "correction_rate",
        "execution_success_rate",
    } <= keys
    for k in ("generated", "validated_first_pass", "corrected", "executed_ok", "executed_failed"):
        assert isinstance(body["sql"][k], int)


def test_stats_rates_are_well_formed():
    """All rate fields must be in [0, 1]."""
    with TestClient(app) as client:
        body = client.get("/api/stats").json()
    for k in ("hit_rate",):
        assert 0.0 <= body["cache"][k] <= 1.0
    for k in ("success_rate",):
        assert 0.0 <= body["requests"][k] <= 1.0
    for k in ("first_pass_rate", "correction_rate", "execution_success_rate"):
        assert 0.0 <= body["sql"][k] <= 1.0


# ---------- B. counters react to events ----------

def test_cache_hit_and_miss_increment_counters():
    """Cache hit/miss events must bump the cache counters and hit_rate."""
    metrics = get_metrics()
    pre = metrics.stats_snapshot()["cache"]
    metrics.record_cache(hit=True)
    metrics.record_cache(hit=True)
    metrics.record_cache(hit=False)
    post = metrics.stats_snapshot()["cache"]
    assert post["hits"] - pre["hits"] == 2
    assert post["misses"] - pre["misses"] == 1
    assert post["total"] - pre["total"] == 3
    # recomputed hit_rate within the new totals
    assert 0.0 <= post["hit_rate"] <= 1.0


def test_request_outcome_records_success_and_error():
    """record_request_outcome must split success/error and accumulate duration."""
    metrics = get_metrics()
    pre = metrics.stats_snapshot()["requests"]
    metrics.record_request_outcome(success=True, duration_ms=12.0)
    metrics.record_request_outcome(success=False, duration_ms=7.0)
    post = metrics.stats_snapshot()["requests"]
    assert post["success"] - pre["success"] == 1
    assert post["error"] - pre["error"] == 1
    assert post["avg_duration_ms"] >= 0.0
    assert post["p95_duration_ms"] >= 0.0


def test_sql_counters_track_lifecycle():
    """record_sql_generated/validated/executed must update OPS-010 counters."""
    metrics = get_metrics()
    pre = metrics.stats_snapshot()["sql"]
    metrics.record_sql_generated()
    metrics.record_sql_validated(corrected=False)   # first pass
    metrics.record_sql_executed(success=True)
    mid = metrics.stats_snapshot()["sql"]
    assert mid["generated"] - pre["generated"] == 1
    assert mid["validated_first_pass"] - pre["validated_first_pass"] == 1
    assert mid["executed_ok"] - pre["executed_ok"] == 1

    metrics.record_sql_generated()
    metrics.record_sql_validated(corrected=True)    # needs correction
    metrics.record_sql_generated()                  # retry
    metrics.record_sql_validated(corrected=False)   # now passes
    metrics.record_sql_executed(success=False)      # but execution fails
    post = metrics.stats_snapshot()["sql"]
    assert post["generated"] - pre["generated"] == 3
    assert post["corrected"] - pre["corrected"] == 1
    assert post["executed_failed"] - pre["executed_failed"] == 1


def test_token_usage_aggregated_from_llm_calls():
    """LLMCallStat token counts must roll up into tokens.{prompt, completion, total}."""
    metrics = Metrics()  # use a fresh instance to avoid global noise
    metrics.record_llm_call(LLMCallStat(
        node_name="generate_sql", model="mock",
        prompt_tokens=100, completion_tokens=50, total_tokens=150, latency_ms=10,
    ))
    metrics.record_llm_call(LLMCallStat(
        node_name="generate_sql", model="mock",
        prompt_tokens=200, completion_tokens=80, total_tokens=280, latency_ms=20,
    ))
    snap = metrics.stats_snapshot()
    assert snap["tokens"]["prompt"] == 300
    assert snap["tokens"]["completion"] == 130
    assert snap["tokens"]["total"] == 430
    assert snap["llm_calls"]["total"] == 2
    assert snap["llm_calls"]["avg_latency_ms"] == 15.0


# ---------- C. integration via HTTP ----------

def test_stats_reflects_real_traffic():
    """Hitting /api/config + /api/health + /api/ask with empty query must update counters."""
    metrics = get_metrics()
    pre = metrics.stats_snapshot()

    with TestClient(app) as client:
        client.get("/api/health")
        client.get("/api/config")
        # 400 should bump error count
        client.post("/api/ask", json={"query": ""})

    post = metrics.stats_snapshot()
    # 3 new HTTP requests → at least 3 more on requests.total
    assert post["requests"]["total"] - pre["requests"]["total"] >= 3
    # empty query went through ask validator → 400 → error count bumped
    assert post["requests"]["error"] - pre["requests"]["error"] >= 1


def test_stats_is_idempotent_shape():
    """Two consecutive GETs must produce the same structural shape.

    Counters themselves change because each /api/stats call goes through
    the request middleware (we cannot avoid that), so we assert the
    sections and their keys are stable rather than the values.
    """
    with TestClient(app) as client:
        a = client.get("/api/stats").json()
        b = client.get("/api/stats").json()
    assert set(a.keys()) == set(b.keys()), "top-level keys differ"
    for section in ("tokens", "llm_calls", "cache", "requests", "sql"):
        assert set(a[section].keys()) == set(b[section].keys()), \
            f"{section} keys differ: {set(a[section].keys())} vs {set(b[section].keys())}"
    assert set(a["node_p95_latency_ms"].keys()) == set(b["node_p95_latency_ms"].keys())


def test_stats_request_id_round_trip():
    """X-Request-ID header must round-trip through /api/stats."""
    with TestClient(app) as client:
        r = client.get("/api/stats", headers={"X-Request-ID": "rid-6-3"})
    assert r.status_code == 200
    assert r.headers.get("x-request-id") == "rid-6-3"


# ---------- D. backwards compatibility ----------

def test_existing_routes_still_work():
    """Adding stats router must not break /api/health, /api/metadata/*, /api/config, /api/ask."""
    with TestClient(app) as client:
        r1 = client.get("/api/health")
        assert r1.status_code == 200
        assert r1.json()["status"] in {"healthy", "degraded"}

        r2 = client.get("/api/metadata/tables")
        assert r2.status_code == 200
        assert r2.json()["count"] >= 5

        r3 = client.get("/api/config")
        assert r3.status_code == 200
        assert "app" in r3.json()

        r4 = client.post("/api/ask", json={"query": ""})
        assert r4.status_code == 400


def test_flat_summary_still_works_for_health():
    """The v1.0 flat summary() output (used by health.py and test_phase2.py) must
    still expose requests_total / cache / llm at the top level."""
    from app.core.metrics import get_metrics
    s = get_metrics().summary()
    assert "requests_total" in s
    assert {"hits", "misses", "hit_rate"} <= set(s["cache"].keys())
    assert {"calls", "tokens", "avg_latency_ms"} <= set(s["llm"].keys())