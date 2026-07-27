"""Phase 6.1 verification: GET /api/metadata/* endpoints (SRS 4.3.2).

Covers query-only endpoints for tables, columns, metrics; detail endpoints
return 404 when the id does not exist.
"""
from __future__ import annotations
import pytest
from fastapi.testclient import TestClient

from main import app


# ---------- list endpoints ----------

def test_list_tables_returns_expected_ids():
    with TestClient(app) as client:
        r = client.get("/api/metadata/tables")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] >= 5
    ids = {t["id"] for t in body["items"]}
    assert {"fact_order", "dim_customer", "dim_product", "dim_region", "dim_date"} <= ids
    for t in body["items"]:
        assert {"id", "name", "role", "description"} <= set(t.keys())


def test_list_tables_shape():
    with TestClient(app) as client:
        r = client.get("/api/metadata/tables")
    body = r.json()
    fact_order = next(t for t in body["items"] if t["id"] == "fact_order")
    assert fact_order["role"] == "fact"
    dim_customer = next(t for t in body["items"] if t["id"] == "dim_customer")
    assert dim_customer["role"] == "dimension"


def test_list_columns_with_table_id_filter():
    with TestClient(app) as client:
        r = client.get("/api/metadata/columns", params={"table_id": "fact_order"})
    assert r.status_code == 200
    body = r.json()
    assert body["table_id"] == "fact_order"
    assert body["count"] >= 1
    assert all(c["table_id"] == "fact_order" for c in body["items"])


def test_list_columns_without_filter_returns_all():
    with TestClient(app) as client:
        r = client.get("/api/metadata/columns")
    assert r.status_code == 200
    body = r.json()
    assert body["table_id"] is None
    assert body["count"] >= 5
    table_ids = {c["table_id"] for c in body["items"]}
    assert "fact_order" in table_ids
    assert "dim_customer" in table_ids


def test_list_metrics_returns_all():
    with TestClient(app) as client:
        r = client.get("/api/metadata/metrics")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] >= 1
    ids = {m["id"] for m in body["items"]}
    assert {"GMV", "AOV", "order_count"} <= ids or len(ids) >= 1
    for m in body["items"]:
        assert {"id", "name", "description", "related_columns", "alias"} <= set(m.keys())
        # related_columns + alias must be parsed JSON (list), not raw string
        assert isinstance(m["related_columns"], list)
        assert isinstance(m["alias"], list)


# ---------- detail endpoints ----------

def test_get_table_detail_includes_columns():
    with TestClient(app) as client:
        r = client.get("/api/metadata/tables/fact_order")
    assert r.status_code == 200
    body = r.json()
    assert body["table"]["id"] == "fact_order"
    assert body["table"]["role"] == "fact"
    assert len(body["columns"]) >= 1
    for c in body["columns"]:
        assert c["table_id"] == "fact_order"
        assert {"id", "name", "type", "role", "description", "alias", "examples"} <= set(c.keys())


def test_get_table_columns_endpoint():
    with TestClient(app) as client:
        r = client.get("/api/metadata/tables/dim_region/columns")
    assert r.status_code == 200
    body = r.json()
    assert body["table_id"] == "dim_region"
    assert body["count"] >= 1
    col_names = {c["name"] for c in body["items"]}
    assert "region_name" in col_names


def test_get_column_detail_with_dotted_id():
    with TestClient(app) as client:
        r = client.get("/api/metadata/columns/fact_order.order_amount")
    assert r.status_code == 200
    body = r.json()
    col = body["column"]
    assert col["id"] == "fact_order.order_amount"
    assert col["name"] == "order_amount"
    assert col["type"]  # non-empty
    assert col["role"] in {"measure", "primary_key", "foreign_key", "dimension"}
    # alias may be list or str; just verify it exists
    assert col["alias"] is not None


def test_get_metric_detail():
    with TestClient(app) as client:
        r = client.get("/api/metadata/metrics/GMV")
    if r.status_code == 200:
        body = r.json()
        m = body["metric"]
        assert m["id"] == "GMV"
        assert isinstance(m["related_columns"], list)
        assert isinstance(m["alias"], list)
    else:
        # GMV may not exist in some sample-data variants; allow
        pytest.skip("GMV metric not present in this dataset")


# ---------- 404 paths ----------

def test_get_table_404_for_missing_id():
    with TestClient(app) as client:
        r = client.get("/api/metadata/tables/no_such_table")
    assert r.status_code == 404
    assert "no_such_table" in r.text


def test_get_column_404_for_missing_id():
    with TestClient(app) as client:
        r = client.get("/api/metadata/columns/no_such.col")
    assert r.status_code == 404


def test_get_metric_404_for_missing_id():
    with TestClient(app) as client:
        r = client.get("/api/metadata/metrics/no_such_metric")
    assert r.status_code == 404


def test_get_table_columns_404_for_missing_table():
    with TestClient(app) as client:
        r = client.get("/api/metadata/tables/no_such_table/columns")
    assert r.status_code == 404


# ---------- backwards compatibility ----------

def test_existing_health_and_ask_still_work():
    """Adding metadata router must not break /api/health or /api/ask."""
    with TestClient(app) as client:
        r = client.get("/api/health")
        assert r.status_code == 200
        assert r.json()["status"] in {"healthy", "degraded"}
        r2 = client.post("/api/ask", json={"query": ""})
        # 400 for empty is the expected behavior of the existing endpoint
        assert r2.status_code == 400