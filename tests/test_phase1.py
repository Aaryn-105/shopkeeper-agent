"""Phase 1 verification: config + logger + request_id + FastAPI lifespan."""
from __future__ import annotations
from fastapi.testclient import TestClient
from main import app
from app.core.config import cfg
from app.core.request_context import REQUEST_ID


def test_config_defaults():
    assert cfg.app.name == "shopkeeper-agent"
    assert cfg.app.env in {"local", "prod"}
    assert int(cfg.embedding.dim) == 512
    assert cfg.mysql.host == "127.0.0.1"
    assert int(cfg.cache.ttl_seconds) > 0


def test_request_id_default_and_set():
    assert REQUEST_ID.get() == "-"
    REQUEST_ID.set("rid-test")
    assert REQUEST_ID.get() == "rid-test"
    REQUEST_ID.set("-")


def test_root_endpoint():
    with TestClient(app) as client:
        r = client.get("/")
        assert r.status_code == 200
        assert r.json()["name"] == "shopkeeper-agent"


def test_health_endpoint():
    with TestClient(app) as client:
        r = client.get("/api/health")
        assert r.status_code == 200
        assert r.json()["status"] == "healthy"
        assert "timestamp" in r.json()


def test_request_id_roundtrip():
    with TestClient(app) as client:
        r = client.get("/api/health", headers={"X-Request-ID": "trace-abc-123"})
        assert r.headers.get("X-Request-ID") == "trace-abc-123"


def test_request_id_autogen():
    with TestClient(app) as client:
        r = client.get("/api/health")
        rid = r.headers.get("X-Request-ID")
        assert rid and len(rid) >= 8


def test_cors_preflight():
    with TestClient(app) as client:
        r = client.options(
            "/api/health",
            headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "GET"},
        )
        assert r.headers.get("access-control-allow-origin") == "*"