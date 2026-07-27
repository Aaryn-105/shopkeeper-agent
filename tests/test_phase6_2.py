"""Phase 6.2 verification: GET /api/config endpoint (SRS 4.3.4).

The /api/config endpoint exposes:
- app: {name, version, env}
- ui:  {welcome_message, usage_tips}
- samples: [{id, category, question, description}]

All content comes from conf/default.yaml so editing the YAML is enough to
change welcome / tips / samples (no code change required, per SRS 4.3.4 BR 4).
"""
from __future__ import annotations
import pytest
from fastapi.testclient import TestClient

from main import app


# ---------- shape ----------

def test_config_returns_200_and_top_level_keys():
    with TestClient(app) as client:
        r = client.get("/api/config")
    assert r.status_code == 200
    body = r.json()
    assert {"app", "ui", "samples"} <= set(body.keys())


def test_config_app_block_has_name_version_env():
    with TestClient(app) as client:
        r = client.get("/api/config")
    app_block = r.json()["app"]
    assert {"name", "version", "env"} <= set(app_block.keys())
    assert app_block["name"]  # non-empty
    assert app_block["version"]  # non-empty
    assert app_block["env"] in {"local", "dev", "prod"}


def test_config_ui_block_has_welcome_and_tips():
    with TestClient(app) as client:
        r = client.get("/api/config")
    ui = r.json()["ui"]
    assert "welcome_message" in ui
    assert "usage_tips" in ui
    assert isinstance(ui["welcome_message"], str)
    assert len(ui["welcome_message"]) > 0
    assert isinstance(ui["usage_tips"], list)
    assert len(ui["usage_tips"]) >= 1
    for tip in ui["usage_tips"]:
        assert isinstance(tip, str) and tip  # each tip non-empty


# ---------- samples (SRS BR 3: at least 3-5, different scenarios) ----------

def test_config_samples_at_least_three():
    """SRS 4.3.4 BR 3: samples list must contain at least 3-5 questions."""
    with TestClient(app) as client:
        r = client.get("/api/config")
    samples = r.json()["samples"]
    assert isinstance(samples, list)
    assert len(samples) >= 3
    assert len(samples) <= 10  # sanity upper bound


def test_config_samples_shape():
    """Each sample must expose id, category, question, description."""
    with TestClient(app) as client:
        r = client.get("/api/config")
    samples = r.json()["samples"]
    for s in samples:
        assert {"id", "category", "question", "description"} <= set(s.keys())
        assert s["id"]
        assert s["category"]
        assert s["question"]
        # description may be empty but the key must exist


def test_config_samples_unique_ids():
    """Sample ids must be unique so the frontend can use them as keys."""
    with TestClient(app) as client:
        r = client.get("/api/config")
    samples = r.json()["samples"]
    ids = [s["id"] for s in samples]
    assert len(ids) == len(set(ids)), f"duplicate sample ids: {ids}"


def test_config_samples_cover_multiple_scenarios():
    """SRS 4.3.4 BR 3: samples cover different analysis scenarios.

    Default seed ships >=3 distinct categories; if a future config drops
    below that, this test will catch it.
    """
    with TestClient(app) as client:
        r = client.get("/api/config")
    samples = r.json()["samples"]
    categories = {s["category"] for s in samples}
    assert len(categories) >= 3, f"need >=3 distinct categories, got {categories}"


# ---------- backwards compatibility ----------

def test_existing_routes_still_work():
    """Adding config router must not break /api/health, /api/metadata/* or /api/ask."""
    with TestClient(app) as client:
        r1 = client.get("/api/health")
        assert r1.status_code == 200
        assert r1.json()["status"] in {"healthy", "degraded"}

        r2 = client.get("/api/metadata/tables")
        assert r2.status_code == 200
        assert r2.json()["count"] >= 5

        r3 = client.post("/api/ask", json={"query": ""})
        # empty query is rejected by the existing validator
        assert r3.status_code == 400


def test_config_endpoint_is_idempotent():
    """Two consecutive GETs must return the same payload (no randomness)."""
    with TestClient(app) as client:
        a = client.get("/api/config").json()
        b = client.get("/api/config").json()
    assert a == b


def test_config_response_request_id_header_round_trip():
    """X-Request-ID sent by client should round-trip; body uses request.state."""
    with TestClient(app) as client:
        r = client.get("/api/config", headers={"X-Request-ID": "test-rid-6-2"})
    assert r.status_code == 200
    assert r.headers.get("x-request-id") == "test-rid-6-2"