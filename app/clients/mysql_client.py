"""Async MySQL client for the data warehouse (dw) and metadata (meta).

Uses SQLAlchemy 2.x async + asyncmy for dw queries (SELECT-only via readonly
account) and pymysql sync for validation (EXPLAIN) since EXPLAIN runs once per
request and sync keeps validation logic simple.

Important: every execute_readonly call must use the readonly credentials, never
admin. This is enforced by always reading cfg.mysql.ro_user / ro_password.
"""
from __future__ import annotations
import re
import threading
from typing import Any, Optional

import pymysql
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.core.config import cfg


_ALLOWED_PREFIX = re.compile(r"^\s*(select|with|explain)\b", re.IGNORECASE)


def _ensure_select_only(sql: str) -> str:
    cleaned = sql.strip().rstrip(";").strip()
    if not _ALLOWED_PREFIX.match(cleaned):
        raise PermissionError(
            f"only SELECT/WITH/EXPLAIN allowed for readonly execution: {cleaned[:60]}"
        )
    if ";" in cleaned:
        raise PermissionError("multiple statements not allowed in readonly execution")
    return cleaned


class MySQLClient:
    """Async engine against the dw database."""

    def __init__(self) -> None:
        self._engine: Optional[AsyncEngine] = None
        self._lock = threading.Lock()

    def _build_url(self, database: str) -> str:
        return (
            f"mysql+asyncmy://{cfg.mysql.ro_user}:{cfg.mysql.ro_password}"
            f"@{cfg.mysql.host}:{int(cfg.mysql.port)}/{database}"
        )

    def _ensure_engine(self) -> AsyncEngine:
        with self._lock:
            if self._engine is None:
                self._engine = create_async_engine(
                    self._build_url(cfg.mysql.dw_db),
                    pool_size=int(cfg.mysql.pool_size),
                    pool_recycle=int(cfg.mysql.pool_recycle),
                    pool_pre_ping=True,
                    echo=False,
                )
            return self._engine

    async def execute_readonly(self, sql: str,
                               max_rows: int = 1000) -> dict[str, Any]:
        cleaned = _ensure_select_only(sql)
        engine = self._ensure_engine()
        async with engine.connect() as conn:
            from sqlalchemy import text
            result = await conn.execute(text(cleaned))
            rows = result.fetchall()
            truncated = len(rows) > max_rows
            if truncated:
                rows = rows[:max_rows]
            columns = list(result.keys())
            return {
                "columns": columns,
                "rows": [list(r) for r in rows],
                "row_count": len(rows),
                "truncated": truncated,
            }

    async def aclose(self) -> None:
        if self._engine is not None:
            await self._engine.dispose()
            self._engine = None


class MySQLValidator:
    """Sync EXPLAIN-based SQL validator. Uses readonly account for safety."""

    def __init__(self) -> None:
        self._lock = threading.Lock()

    def validate(self, sql: str) -> tuple[bool, str]:
        """Returns (ok, message). On error, message contains the reason."""
        try:
            cleaned = _ensure_select_only(sql)
        except PermissionError as e:
            return False, f"PermissionError: {e}"
        with self._lock:
            try:
                conn = pymysql.connect(
                    host=cfg.mysql.host,
                    port=int(cfg.mysql.port),
                    user=cfg.mysql.ro_user,
                    password=cfg.mysql.ro_password,
                    database=cfg.mysql.dw_db,
                    charset="utf8mb4",
                    autocommit=True,
                    connect_timeout=5,
                )
                try:
                    with conn.cursor() as cur:
                        cur.execute(f"EXPLAIN {cleaned}")
                        cur.fetchall()
                    return True, "ok"
                finally:
                    conn.close()
            except Exception as e:
                return False, f"{type(e).__name__}: {str(e)[:200]}"


class MetadataClient:
    """Sync reader against the meta DB (uses readonly account, SELECT only)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()

    def _connect(self):
        return pymysql.connect(
            host=cfg.mysql.host,
            port=int(cfg.mysql.port),
            user=cfg.mysql.ro_user,
            password=cfg.mysql.ro_password,
            database=cfg.mysql.meta_db,
            charset="utf8mb4",
            autocommit=True,
            connect_timeout=5,
            cursorclass=pymysql.cursors.DictCursor,
        )

    def list_tables(self) -> list[dict[str, Any]]:
        with self._lock, self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id, name, role, description FROM table_info ORDER BY id")
                return list(cur.fetchall())

    def list_columns(self, table_id: Optional[str] = None) -> list[dict[str, Any]]:
        with self._lock, self._connect() as conn:
            with conn.cursor() as cur:
                if table_id:
                    cur.execute(
                        "SELECT id, name, type, role, description, examples, alias, table_id "
                        "FROM column_info WHERE table_id=%s ORDER BY id",
                        (table_id,),
                    )
                else:
                    cur.execute(
                        "SELECT id, name, type, role, description, examples, alias, table_id "
                        "FROM column_info ORDER BY table_id, id"
                    )
                return list(cur.fetchall())

    def list_metrics(self) -> list[dict[str, Any]]:
        with self._lock, self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, name, description, related_columns, alias FROM metric_info ORDER BY id"
                )
                return list(cur.fetchall())