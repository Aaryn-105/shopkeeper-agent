"""SQLite FTS5-backed value search index.

Per SRS 6.2.4 the index is named value_info and holds (id, value, column_id).
We persist to cfg.fts5.db_path so a sync script can populate it from the DW.
"""
from __future__ import annotations
import sqlite3
import threading
from pathlib import Path
from typing import Any, Optional

from app.core.config import cfg


class FTS5Store:
    def __init__(self, db_path: Optional[Path] = None) -> None:
        self.db_path = Path(db_path or cfg.fts5.db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_schema(self) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS value_info USING fts5("
                "value, column_id, tokenize='unicode61 remove_diacritics 2')"
            )
            conn.commit()

    def add(self, value: str, column_id: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO value_info(value, column_id) VALUES (?, ?)",
                (value, column_id),
            )
            conn.commit()

    def add_many(self, items: list[tuple[str, str]]) -> None:
        if not items:
            return
        with self._lock, self._connect() as conn:
            conn.executemany(
                "INSERT INTO value_info(value, column_id) VALUES (?, ?)",
                items,
            )
            conn.commit()

    def reset(self) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM value_info")
            conn.commit()

    def search(self, query: str, top_k: Optional[int] = None) -> list[dict[str, Any]]:
        k = top_k or int(cfg.fts5.top_k_value)
        # Build an FTS5 prefix query from each whitespace token
        tokens = [t for t in query.replace(",", " ").split() if t]
        if not tokens:
            return []
        fts_expr = " OR ".join(f'"{t}"*' for t in tokens)
        with self._lock, self._connect() as conn:
            try:
                cur = conn.execute(
                    "SELECT value, column_id FROM value_info "
                    "WHERE value_info MATCH ? LIMIT ?",
                    (fts_expr, k),
                )
                return [{"value": v, "column_id": c} for v, c in cur.fetchall()]
            except sqlite3.OperationalError:
                # bad fts syntax -> fall back to LIKE
                like = "%".join(tokens)
                cur = conn.execute(
                    "SELECT value, column_id FROM value_info "
                    "WHERE value LIKE ? LIMIT ?",
                    (f"%{like}%", k),
                )
                return [{"value": v, "column_id": c} for v, c in cur.fetchall()]

    def size(self) -> int:
        with self._lock, self._connect() as conn:
            cur = conn.execute("SELECT COUNT(*) FROM value_info")
            return int(cur.fetchone()[0])