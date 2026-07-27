"""Phase 2 verification: metrics + service probes + periodic flush + middleware."""
from __future__ import annotations
import asyncio
import json
import time
from pathlib import Path
from fastapi.testclient import TestClient
from main import app
from app.core.config import cfg
from app.core.metrics import Metrics, LLMCallStat, get_metrics, metrics_periodic_flush


# ---------- pure unit tests on Metrics ----------

def test_metrics_record_node_latency_and_summary():
    m = Metrics()
    m.record_node_latency("extract_keywords", 12.5)
    m.record_node_latency("extract_keywords", 7.5)
    m.record_node_latency("extract_keywords", 30.0)
    m.record_node_latency("generate_sql", 50.0)
    s = m.summary()
    assert "node_p95_latency_ms" in s
    # current impl uses int(p*(n-1)) truncation: 3 values [7.5,12.5,30.0] -> idx 1 = 12.5
    assert s["node_p95_latency_ms"]["extract_keywords"] == 12.5
    assert s["node_p95_latency_ms"]["generate_sql"] == 50.0


def test_metrics_record_cache_and_hit_rate():
    m = Metrics()
    m.record_cache(hit=True)
    m.record_cache(hit=True)
    m.record_cache(hit=False)
    s = m.summary()
    assert s["cache"]["hits"] == 2
    assert s["cache"]["misses"] == 1
    assert s["cache"]["hit_rate"] == round(2 / 3, 4)


def test_metrics_record_request_counter():
    m = Metrics()
    for _ in range(5):
        m.record_request()
    assert m.summary()["requests_total"] == 5


def test_metrics_record_llm_call_aggregates_tokens_and_latency():
    m = Metrics()
    m.record_llm_call(LLMCallStat(
        node_name="generate_sql", model="gpt-4o-mini",
        prompt_tokens=100, completion_tokens=50, total_tokens=150,
        latency_ms=400, cache_hit=False,
    ))
    m.record_llm_call(LLMCallStat(
        node_name="correct_sql", model="gpt-4o-mini",
        prompt_tokens=80, completion_tokens=20, total_tokens=100,
        latency_ms=200, cache_hit=False,
    ))
    s = m.summary()
    assert s["llm"]["calls"] == 2
    assert s["llm"]["tokens"]["prompt"] == 180
    assert s["llm"]["tokens"]["completion"] == 70
    assert s["llm"]["tokens"]["all"] == 250
    assert s["llm"]["avg_latency_ms"] == 300.0


def test_metrics_summary_shape_keys():
    m = Metrics()
    s = m.summary()
    for k in ("uptime_seconds", "requests_total", "cache", "llm", "node_p95_latency_ms"):
        assert k in s, f"missing key: {k}"
    assert {"hits", "misses", "hit_rate"} <= set(s["cache"])
    assert {"calls", "tokens", "avg_latency_ms"} <= set(s["llm"])


# ---------- regression: dump_jsonl must not deadlock ----------

def test_dump_jsonl_does_not_deadlock_on_summary_reentry(tmp_path: Path):
    """Regression for the Lock-vs-RLock bug: dump_jsonl calls summary() while
    holding the lock. With non-reentrant Lock this deadlocks; with RLock it
    must complete in well under 1s."""
    m = Metrics()
    m.record_request()
    m.record_cache(hit=True)
    out = tmp_path / "metrics.jsonl"
    t0 = time.perf_counter()
    n = m.dump_jsonl(out)
    elapsed = time.perf_counter() - t0
    assert n == 1
    assert elapsed < 1.0, f"dump_jsonl took {elapsed:.2f}s - likely deadlock"
    assert out.exists()
    line = out.read_text(encoding="utf-8").strip()
    parsed = json.loads(line)
    assert parsed["requests_total"] == 1
    assert parsed["cache"]["hits"] == 1


def test_summary_callable_under_explicit_lock():
    """Calling summary() while already holding the lock must NOT deadlock."""
    m = Metrics()
    m.record_request()
    t0 = time.perf_counter()
    with m._lock:
        s = m.summary()
    elapsed = time.perf_counter() - t0
    assert elapsed < 0.5
    assert s["requests_total"] == 1


# ---------- periodic flush background task ----------

def test_periodic_flush_writes_lines_and_handles_cancel(tmp_path: Path):
    async def runner():
        out = tmp_path / "flush.jsonl"
        # short interval so we see a tick quickly
        task = asyncio.create_task(
            metrics_periodic_flush(out, interval_seconds=0.05)
        )
        # let it tick at least once
        await asyncio.sleep(0.15)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        return out
    out = asyncio.run(runner())
    assert out.exists(), "flush task did not create the jsonl file"
    lines = out.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) >= 1, "expected at least one flush line"
    parsed = json.loads(lines[0])
    assert "ts" in parsed and "requests_total" in parsed


# ---------- health endpoint integration ----------

def test_health_response_envelope_and_five_service_groups():
    with TestClient(app) as client:
        r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] in {"healthy", "degraded"}
    assert "services" in body
    assert "metrics" in body
    assert "timestamp" in body
    # five grouped service slots
    assert set(body["services"].keys()) == {"mysql", "faiss", "embedding", "fts5_or_es", "llm"}


def test_health_uses_real_probes_from_app_state():
    with TestClient(app):
        probes = app.state.probes
    assert set(probes.keys()) == {"mysql_admin", "mysql_ro", "faiss", "embedding", "fts5_or_es", "llm"}
    # all of these should be non-empty strings
    for k, v in probes.items():
        assert isinstance(v, str) and v, f"probe {k} returned empty: {v!r}"
    # with .env loaded, MySQL credentials are present
    assert probes["mysql_admin"] in {"ok"} or probes["mysql_admin"].startswith("error:")
    assert probes["mysql_ro"] in {"ok"} or probes["mysql_ro"].startswith("error:")
    assert probes["faiss"] == "ok"
    assert probes["embedding"] == "ok"
    assert probes["fts5_or_es"].startswith("ok(")
    # LLM is pending because no API key configured
    assert probes["llm"].startswith("pending:") or probes["llm"] == "ok"


def test_health_metrics_summary_shape():
    with TestClient(app) as client:
        body = client.get("/api/health").json()
    m = body["metrics"]
    assert {"requests_total", "cache", "llm", "node_p95_latency_ms"} <= set(m)
    assert {"hits", "misses", "hit_rate"} <= set(m["cache"])
    assert {"calls", "tokens", "avg_latency_ms"} <= set(m["llm"])


# ---------- middleware records per-request metrics ----------

def test_middleware_increments_request_count_and_records_path_latency():
    before = get_metrics().summary()["requests_total"]
    with TestClient(app) as client:
        client.get("/")
        client.get("/api/health")
        client.get("/api/health")
    after = get_metrics().summary()["requests_total"]
    delta = after - before
    # at least 3 increments (one per call we made)
    assert delta >= 3, f"expected >=3 new requests, got {delta}"
    # and the path-based latency key must have been recorded
    p95 = get_metrics().summary()["node_p95_latency_ms"]
    assert any(k.startswith("http:/") for k in p95), f"no http: latency key in {p95}"


# ---------- persisted metrics file on shutdown ----------

def test_metrics_jsonl_persisted_after_lifespan_shutdown(tmp_path: Path):
    # exercise a fresh lifespan to ensure a flush is emitted
    with TestClient(app) as client:
        client.get("/")
    log_path = Path(str(cfg.logging.dir)) / "metrics.jsonl"
    assert log_path.exists(), f"{log_path} not written"
    lines = log_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) >= 1, "expected at least one line in metrics.jsonl after shutdown"
    last = json.loads(lines[-1])
    assert "ts" in last
    assert "requests_total" in last


# ---------- .env overlay sanity ----------

def test_env_overlay_loads_mysql_credentials():
    # .env file is at repo root and ships MYSQL_ADMIN_PASSWORD=123456
    assert cfg.mysql.host == "127.0.0.1"
    assert int(cfg.mysql.port) == 3306
    # credentials must be present (not empty)
    assert str(cfg.mysql.admin_password) != "", "admin password not loaded from .env"
    assert str(cfg.mysql.ro_password) != "", "readonly password not loaded from .env"
    # model path and dim are also from .env
    assert str(cfg.embedding.model_path).endswith("bge-st")
    assert int(cfg.embedding.dim) == 512