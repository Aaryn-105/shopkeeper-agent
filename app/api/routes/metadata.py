"""GET /api/metadata/* - metadata management endpoints (SRS 4.3.2).

Per the spec this iteration implements query-only endpoints. CRUD is deferred
to a later version ("本期先提供查询接口，增删改接口可后续扩展").

Endpoints:
  GET /api/metadata/tables                   -> list all tables
  GET /api/metadata/tables/{table_id}        -> single table + its columns
  GET /api/metadata/tables/{table_id}/columns -> columns under a table
  GET /api/metadata/columns?table_id=...     -> all columns (optional filter)
  GET /api/metadata/columns/{column_id}      -> single column detail
  GET /api/metadata/metrics                  -> list all metrics
  GET /api/metadata/metrics/{metric_id}      -> single metric detail

Detail endpoints return 404 when the requested id does not exist.
"""
from __future__ import annotations
import json
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.clients.mysql_client import MetadataClient
from app.core.logger import logger


router = APIRouter(prefix="/api/metadata", tags=["metadata"])


def _coerce_json(value: Any) -> Any:
    """MySQL JSON columns may come back as str or already-decoded.

    Examples column for column_info is stored as a single value (not an array),
    so we only attempt to parse when the value clearly looks like JSON.
    """
    if value is None or isinstance(value, (list, dict, int, float, bool)):
        return value
    if isinstance(value, str):
        s = value.strip()
        if s.startswith("[") or s.startswith("{"):
            try:
                return json.loads(s)
            except (json.JSONDecodeError, ValueError):
                return value
        return value
    return value


def _serialize_table(t: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": t.get("id"),
        "name": t.get("name"),
        "role": t.get("role"),
        "description": t.get("description"),
    }


def _serialize_column(c: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": c.get("id"),
        "name": c.get("name"),
        "type": c.get("type"),
        "role": c.get("role"),
        "description": c.get("description"),
        "examples": _coerce_json(c.get("examples")),
        "alias": _coerce_json(c.get("alias")),
        "table_id": c.get("table_id"),
    }


def _serialize_metric(m: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": m.get("id"),
        "name": m.get("name"),
        "description": m.get("description"),
        "related_columns": _coerce_json(m.get("related_columns")),
        "alias": _coerce_json(m.get("alias")),
    }


def _client() -> MetadataClient:
    return MetadataClient()


# ---------- tables ----------

@router.get("/tables")
async def list_tables() -> dict[str, Any]:
    client = _client()
    rows = [_serialize_table(t) for t in client.list_tables()]
    return {"count": len(rows), "items": rows}


@router.get("/tables/{table_id}")
async def get_table(table_id: str) -> dict[str, Any]:
    client = _client()
    rows = [t for t in client.list_tables() if t["id"] == table_id]
    if not rows:
        raise HTTPException(status_code=404, detail=f"table not found: {table_id}")
    cols = [_serialize_column(c) for c in client.list_columns(table_id)]
    return {"table": _serialize_table(rows[0]), "columns": cols}


@router.get("/tables/{table_id}/columns")
async def get_table_columns(table_id: str) -> dict[str, Any]:
    client = _client()
    cols = client.list_columns(table_id)
    if not cols:
        # disambiguate empty (table exists but has no columns) vs missing table
        if not any(t["id"] == table_id for t in client.list_tables()):
            raise HTTPException(status_code=404, detail=f"table not found: {table_id}")
    items = [_serialize_column(c) for c in cols]
    return {"table_id": table_id, "count": len(items), "items": items}


# ---------- columns ----------

@router.get("/columns")
async def list_columns(
    table_id: str | None = Query(default=None, description="Filter by table id"),
) -> dict[str, Any]:
    client = _client()
    rows = [_serialize_column(c) for c in client.list_columns(table_id)]
    return {"table_id": table_id, "count": len(rows), "items": rows}


@router.get("/columns/{column_id:path}")
async def get_column(column_id: str) -> dict[str, Any]:
    client = _client()
    rows = [c for c in client.list_columns() if c["id"] == column_id]
    if not rows:
        raise HTTPException(status_code=404, detail=f"column not found: {column_id}")
    return {"column": _serialize_column(rows[0])}


# ---------- metrics ----------

@router.get("/metrics")
async def list_metrics() -> dict[str, Any]:
    client = _client()
    rows = [_serialize_metric(m) for m in client.list_metrics()]
    return {"count": len(rows), "items": rows}


@router.get("/metrics/{metric_id}")
async def get_metric(metric_id: str) -> dict[str, Any]:
    client = _client()
    rows = [m for m in client.list_metrics() if m["id"] == metric_id]
    if not rows:
        raise HTTPException(status_code=404, detail=f"metric not found: {metric_id}")
    return {"metric": _serialize_metric(rows[0])}