"""Phase 6.4 verification: GET /api/history endpoints.

The /api/history endpoints expose:
  GET /api/history?session_id=&limit=&offset=  -> list page + total + pagination
  GET /api/history/{id}                        -> single record or 404

Writes happen automatically inside the /api/ask SSE flow (ok / error /
cache_hit outcomes). For tests we exercise the HistoryWriter directly to
keep the assertions deterministic and avoid driving the full LangGraph
graph.
"""
from __future__ import annotations
import pytest
from fastapi.testclient import TestClient

from main import app
from app.clients.history_client import HistoryReader, HistoryWriter


# Each test gets a unique session_id so they don't interfere with each other
# or with leftover records from other tests / smoke runs.
def _session_id() -> str:
    import uuid
    return f"test-{uuid.uuid4().hex[:12]}"


# ---------- A. shape ----------

def test_history_list_returns_expected_top_level_keys():
    with TestClient(app) as client:
        r = client.get("/api/history")
    assert r.status_code == 200
    body = r.json()
    expected = {"count", "total", "limit", "offset", "session_id", "items"}
    assert expected <= set(body.keys())
    assert isinstance(body["items"], list)
    assert isinstance(body["total"], int)
    assert isinstance(body["count"], int)


def test_history_list_item_shape():
    """Each item must expose the documented fields."""
    sid = _session_id()
    HistoryWriter.record(
        request_id="shape-test",
        session_id=sid,
        query="SELECT example query",
        sql_text="SELECT 1",
        status="ok",
        duration_ms=100,
        row_count=1,
    )
    with TestClient(app) as client:
        body = client.get("/api/history", params={"session_id": sid}).json()
    assert body["count"] >= 1
    item = body["items"][0]
    expected = {
        "id", "request_id", "session_id", "query", "sql_text",
        "sql_corrected", "status", "error_message",
        "duration_ms", "row_count", "created_at",
    }
    assert expected <= set(item.keys())
    assert isinstance(item["id"], int)
    assert isinstance(item["sql_corrected"], bool)
    assert isinstance(item["duration_ms"], int)
    assert isinstance(item["row_count"], int)
    # created_at must be ISO 8601 string
    assert isinstance(item["created_at"], str)
    assert "T" in item["created_at"]  # naive ISO check


# ---------- B. session_id filter ----------

def test_history_session_id_filter_isolates_records():
    sid_a, sid_b = _session_id(), _session_id()
    HistoryWriter.record(request_id="a1", session_id=sid_a, query="qA1",
                         sql_text=None, status="ok", duration_ms=10)
    HistoryWriter.record(request_id="a2", session_id=sid_a, query="qA2",
                         sql_text=None, status="ok", duration_ms=20)
    HistoryWriter.record(request_id="b1", session_id=sid_b, query="qB1",
                         sql_text=None, status="error", duration_ms=5,
                         error_message="boom")
    with TestClient(app) as client:
        ra = client.get("/api/history", params={"session_id": sid_a}).json()
        rb = client.get("/api/history", params={"session_id": sid_b}).json()
    assert ra["count"] == 2
    assert rb["count"] == 1
    assert all(item["session_id"] == sid_a for item in ra["items"])
    assert all(item["session_id"] == sid_b for item in rb["items"])


def test_history_session_id_filter_returns_empty_for_unknown():
    with TestClient(app) as client:
        r = client.get("/api/history", params={"session_id": "no-such-session-ever"})
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 0
    assert body["total"] == 0
    assert body["items"] == []


# ---------- C. pagination ----------

def test_history_pagination_limit_and_offset():
    sid = _session_id()
    # insert 5 records with the same session
    for i in range(5):
        HistoryWriter.record(
            request_id=f"page-{i}", session_id=sid,
            query=f"q{i}", sql_text=None, status="ok", duration_ms=1,
        )

    with TestClient(app) as client:
        r1 = client.get("/api/history", params={"session_id": sid, "limit": 2, "offset": 0}).json()
        r2 = client.get("/api/history", params={"session_id": sid, "limit": 2, "offset": 2}).json()
        r3 = client.get("/api/history", params={"session_id": sid, "limit": 2, "offset": 4}).json()
    assert r1["count"] == 2
    assert r2["count"] == 2
    assert r3["count"] == 1
    # ids must not overlap across pages
    ids = {it["id"] for it in r1["items"]} | {it["id"] for it in r2["items"]} | {it["id"] for it in r3["items"]}
    assert len(ids) == 5


def test_history_limit_is_capped_at_100():
    with TestClient(app) as client:
        # 200 should be clamped to 100 by FastAPI's Query validation
        r = client.get("/api/history", params={"limit": 200})
    # FastAPI returns 422 for out-of-range limit
    assert r.status_code == 422


def test_history_limit_min_is_1():
    with TestClient(app) as client:
        r = client.get("/api/history", params={"limit": 0})
    assert r.status_code == 422


def test_history_offset_must_be_non_negative():
    with TestClient(app) as client:
        r = client.get("/api/history", params={"offset": -1})
    assert r.status_code == 422


# ---------- D. ordering ----------

def test_history_orders_by_created_at_desc():
    sid = _session_id()
    # insert in this order; list should reverse
    for i in range(3):
        HistoryWriter.record(
            request_id=f"ord-{i}", session_id=sid, query=f"q{i}",
            sql_text=None, status="ok", duration_ms=1,
        )
    with TestClient(app) as client:
        body = client.get("/api/history", params={"session_id": sid}).json()
    # Most recent first
    ids = [it["id"] for it in body["items"]]
    assert ids == sorted(ids, reverse=True)


# ---------- E. detail endpoint ----------

def test_history_detail_returns_record():
    sid = _session_id()
    HistoryWriter.record(
        request_id="det", session_id=sid, query="detail test",
        sql_text="SELECT 42", status="ok", duration_ms=99, row_count=1,
    )
    with TestClient(app) as client:
        listing = client.get("/api/history", params={"session_id": sid}).json()
        hid = listing["items"][0]["id"]
        rd = client.get(f"/api/history/{hid}")
    assert rd.status_code == 200
    body = rd.json()
    assert "history" in body
    assert body["history"]["id"] == hid
    assert body["history"]["query"] == "detail test"


def test_history_detail_404_for_missing_id():
    with TestClient(app) as client:
        r = client.get("/api/history/999999999")
    assert r.status_code == 404
    assert "999999999" in r.text


# ---------- F. write side covers all status values ----------

def test_history_records_all_status_values():
    sid = _session_id()
    HistoryWriter.record(
        request_id="ok-1", session_id=sid, query="q", sql_text="SELECT 1",
        status="ok", duration_ms=10,
    )
    HistoryWriter.record(
        request_id="err-1", session_id=sid, query="q", sql_text=None,
        status="error", error_message="simulated failure", duration_ms=10,
    )
    HistoryWriter.record(
        request_id="ch-1", session_id=sid, query="q", sql_text=None,
        status="cache_hit", duration_ms=0, row_count=3,
    )
    with TestClient(app) as client:
        body = client.get("/api/history", params={"session_id": sid}).json()
    statuses = {it["status"] for it in body["items"]}
    assert {"ok", "error", "cache_hit"} <= statuses


def test_history_error_record_preserves_error_message():
    sid = _session_id()
    HistoryWriter.record(
        request_id="err", session_id=sid, query="q", sql_text=None,
        status="error", error_message="OperationalError: connection lost",
        duration_ms=10,
    )
    with TestClient(app) as client:
        body = client.get("/api/history", params={"session_id": sid}).json()
    assert body["count"] == 1
    assert body["items"][0]["error_message"] == "OperationalError: connection lost"


def test_history_sql_corrected_flag_round_trips():
    sid = _session_id()
    HistoryWriter.record(
        request_id="corr", session_id=sid, query="q",
        sql_text="SELECT 1 FROM t", status="ok",
        duration_ms=10, sql_corrected=True,
    )
    with TestClient(app) as client:
        body = client.get("/api/history", params={"session_id": sid}).json()
    item = body["items"][0]
    assert item["sql_corrected"] is True


# ---------- G. ask.py writes a record end-to-end ----------

def test_ask_endpoint_writes_history_on_empty_query_is_400():
    """Empty query is rejected before the graph runs and writes no record.

    This is a regression guard: history write must happen only AFTER the
    SSE flow runs (i.e. for valid queries), not for rejected ones.
    """
    sid = _session_id()
    with TestClient(app) as client:
        r = client.post("/api/ask", json={"query": "", "session_id": sid})
    assert r.status_code == 400
    # No record should have been written for the rejected request
    listing = client.get("/api/history", params={"session_id": sid}).json()
    assert listing["count"] == 0


def test_ask_endpoint_writes_history_for_valid_query():
    """A valid /api/ask call must produce exactly one history record.

    The graph may still error on the mock LLM, but a record must exist.
    """
    sid = _session_id()
    with TestClient(app) as client:
        # Use streaming so the SSE flow completes; consume events until done
        with client.stream(
            "POST", "/api/ask",
            json={"query": "上个月华东地区GMV", "session_id": sid},
        ) as resp:
            for _ in resp.iter_lines():
                pass
    listing = client.get("/api/history", params={"session_id": sid}).json()
    assert listing["count"] >= 1
    item = listing["items"][0]
    assert item["session_id"] == sid
    assert item["query"] == "上个月华东地区GMV"
    assert item["status"] in {"ok", "error", "cache_hit"}


# ---------- H. backwards compat ----------

def test_existing_routes_still_work():
    with TestClient(app) as client:
        r1 = client.get("/api/health")
        assert r1.status_code == 200

        r2 = client.get("/api/metadata/tables")
        assert r2.status_code == 200

        r3 = client.get("/api/config")
        assert r3.status_code == 200

        r4 = client.get("/api/stats")
        assert r4.status_code == 200

        r5 = client.post("/api/ask", json={"query": ""})
        assert r5.status_code == 400


def test_history_request_id_round_trip():
    with TestClient(app) as client:
        r = client.get("/api/history", headers={"X-Request-ID": "rid-6-4"})
    assert r.status_code == 200
    assert r.headers.get("x-request-id") == "rid-6-4"