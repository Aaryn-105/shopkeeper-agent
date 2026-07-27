"""History persistence client for /api/ask question/answer records.

Writes use the admin MySQL account (we need INSERT permission and the readonly
account is SELECT-only by design). Reads use the readonly account, matching
how MetadataClient and the /api/metadata endpoints operate.

Schema lives in scripts/init_meta_mysql.py (META_DDL['ask_history']). The
table is created automatically by re-running the init script.

Status values written by ask.py:
  - 'ok':         graph completed, result returned
  - 'error':      any node raised an unrecoverable error
  - 'cache_hit':  served from QueryCache before the graph ran
"""
from __future__ import annotations
import threading
from typing import Any, Optional

import pymysql

from app.core.config import cfg


def _connect_admin():
    return pymysql.connect(
        host=cfg.mysql.host,
        port=int(cfg.mysql.port),
        user=cfg.mysql.admin_user,
        password=cfg.mysql.admin_password,
        database=cfg.mysql.meta_db,
        charset="utf8mb4",
        autocommit=True,
    )


def _connect_ro():
    return pymysql.connect(
        host=cfg.mysql.host,
        port=int(cfg.mysql.port),
        user=cfg.mysql.ro_user,
        password=cfg.mysql.ro_password,
        database=cfg.mysql.meta_db,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
    )


class HistoryWriter:
    """Thread-safe writer for ask_history. Uses admin credentials."""

    _write_lock = threading.Lock()

    @classmethod
    def record(
        cls,
        *,
        request_id: str,
        session_id: Optional[str],
        query: str,
        sql_text: Optional[str],
        status: str,
        error_message: Optional[str] = None,
        duration_ms: int = 0,
        row_count: int = 0,
        sql_corrected: bool = False,
    ) -> int:
        """Insert a history record. Returns the new auto-increment id.

        Returns 0 if the insert failed silently (caller can ignore; the
        endpoint is best-effort by design and must not block the ask flow).
        """
        with cls._write_lock:
            conn = _connect_admin()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO ask_history
                            (request_id, session_id, query, sql_text, status,
                             error_message, duration_ms, row_count, sql_corrected)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            request_id,
                            session_id,
                            query,
                            sql_text,
                            status,
                            error_message,
                            int(duration_ms),
                            int(row_count),
                            1 if sql_corrected else 0,
                        ),
                    )
                    return int(cur.lastrowid or 0)
            except Exception:
                # history is best-effort; never fail the ask flow on it
                return 0
            finally:
                conn.close()


class HistoryReader:
    """Thread-safe reader for ask_history. Uses readonly credentials.

    Returns pymysql DictCursor rows so each item is already a dict.
    """

    @classmethod
    def list_recent(
        cls,
        session_id: Optional[str] = None,
        limit: int = 20,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 100))
        offset = max(0, int(offset))
        conn = _connect_ro()
        try:
            with conn.cursor() as cur:
                if session_id:
                    cur.execute(
                        """
                        SELECT id, request_id, session_id, query, sql_text,
                               sql_corrected, status, error_message,
                               duration_ms, row_count, created_at
                        FROM ask_history
                        WHERE session_id = %s
                        ORDER BY created_at DESC, id DESC
                        LIMIT %s OFFSET %s
                        """,
                        (session_id, limit, offset),
                    )
                else:
                    cur.execute(
                        """
                        SELECT id, request_id, session_id, query, sql_text,
                               sql_corrected, status, error_message,
                               duration_ms, row_count, created_at
                        FROM ask_history
                        ORDER BY created_at DESC, id DESC
                        LIMIT %s OFFSET %s
                        """,
                        (limit, offset),
                    )
                rows = cur.fetchall()
                return [_serialize(r) for r in rows]
        finally:
            conn.close()

    @classmethod
    def count(cls, session_id: Optional[str] = None) -> int:
        conn = _connect_ro()
        try:
            with conn.cursor() as cur:
                if session_id:
                    cur.execute(
                        "SELECT COUNT(*) AS n FROM ask_history WHERE session_id = %s",
                        (session_id,),
                    )
                else:
                    cur.execute("SELECT COUNT(*) AS n FROM ask_history")
                row = cur.fetchone()
                return int(row["n"]) if row else 0
        finally:
            conn.close()

    @classmethod
    def get_by_id(cls, history_id: int) -> Optional[dict[str, Any]]:
        conn = _connect_ro()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, request_id, session_id, query, sql_text,
                           sql_corrected, status, error_message,
                           duration_ms, row_count, created_at
                    FROM ask_history WHERE id = %s
                    """,
                    (int(history_id),),
                )
                row = cur.fetchone()
                return _serialize(row) if row else None
        finally:
            conn.close()


def _serialize(row: dict[str, Any]) -> dict[str, Any]:
    """Convert a DB row into a JSON-safe dict.

    - sql_corrected -> bool
    - created_at (datetime) -> ISO 8601 string
    """
    if row is None:
        return row
    out = dict(row)
    out["sql_corrected"] = bool(out.get("sql_corrected", 0))
    created_at = out.get("created_at")
    if created_at is not None and hasattr(created_at, "isoformat"):
        out["created_at"] = created_at.isoformat()
    return out