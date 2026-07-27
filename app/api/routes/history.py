"""GET /api/history - \u5386\u53f2\u95ee\u6570\u8bb0\u5f55\u67e5\u8be2\u63a5\u53e3\u3002

\u67e5\u8be2\u8bed\u4e49\uff1a
  GET /api/history?session_id=<id>&limit=<n>&offset=<n>

\u8fd4\u56de\u9879\u76ee\u6309 created_at DESC, id DESC \u6392\u5e8f\u3002

\u4e0e SRS 4.4.1 "\u5bf9\u8bdd\u533a\u57df\uff0c\u5c55\u793a\u5386\u53f2\u95ee\u7b54\u8bb0\u5f55" \u5bf9\u9f50\u3002
\u6301\u4e45\u5316\u5230 meta.ask_history\uff08\u9700\u8981\u91cd\u8dd1 scripts/init_meta_mysql.py \u521b\u5efa\u8868\uff09\u3002
"""
from __future__ import annotations
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from app.clients.history_client import HistoryReader


router = APIRouter(prefix="/api/history", tags=["history"])


@router.get("")
@router.get("/")
async def list_history(
    request: Request,
    session_id: str | None = Query(default=None, description="Filter by session id"),
    limit: int = Query(default=20, ge=1, le=100, description="Page size (1-100)"),
    offset: int = Query(default=0, ge=0, description="Offset for pagination"),
) -> dict[str, Any]:
    """Return a page of historical ask records."""
    items = HistoryReader.list_recent(session_id=session_id, limit=limit, offset=offset)
    total = HistoryReader.count(session_id=session_id)
    payload: dict[str, Any] = {
        "count": len(items),
        "total": total,
        "limit": limit,
        "offset": offset,
        "session_id": session_id,
        "items": items,
    }
    rid = getattr(request.state, "request_id", None)
    if rid:
        payload["request_id"] = rid
    return payload


@router.get("/{history_id}")
async def get_history(request: Request, history_id: int) -> dict[str, Any]:
    """Return a single history record by id, or 404 if missing."""
    row = HistoryReader.get_by_id(history_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"history not found: {history_id}")
    rid = getattr(request.state, "request_id", None)
    out = {"history": row}
    if rid:
        out["request_id"] = rid
    return out