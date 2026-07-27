# -*- coding: utf-8 -*-
"""Build the three knowledge indexes from MySQL metadata.

Step 5 in the V1.0 plan:
  1. meta.column_info  -> FAISS column_info collection (vector + payload)
  2. meta.metric_info  -> FAISS metric_info collection (vector + payload)
  3. dw dim_* distinct values -> FTS5 value_info (id, value, column_id)

Each collection resets before write, so the script is fully idempotent. Failures
return a non-zero exit code after logging the cause - the existing indexes stay
on disk so previous good builds are not lost on a partial failure.

Usage:
    uv run python scripts/build_knowledge_index.py
    uv run python scripts/build_knowledge_index.py --top-n-values 200
    uv run python -c "from scripts.build_knowledge_index import build; \
        from app.clients.mysql_client import MetadataClient; \
        from app.clients.embedding_client import EmbeddingClient; \
        from app.clients.faiss_client import FAISSStore; \
        from app.clients.fts5_client import FTS5Store; \
        print(build(MetadataClient(), EmbeddingClient(), FAISSStore(), FTS5Store()))"
"""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path
from typing import Any

# project root -> sys.path so `uv run python scripts/...` works
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.clients.embedding_client import EmbeddingClient
from app.clients.faiss_client import FAISSStore
from app.clients.fts5_client import FTS5Store
from app.clients.mysql_client import MetadataClient
from app.core.config import cfg


def _col_text(c: dict[str, Any]) -> str:
    """Concatenate the fields that give the most semantic signal for embedding."""
    parts = [
        str(c.get("name") or ""),
        str(c.get("description") or ""),
        str(c.get("table_id") or ""),
        " ".join(c.get("alias") or []) if isinstance(c.get("alias"), list) else str(c.get("alias") or ""),
        " ".join(str(x) for x in (c.get("examples") or []))
            if isinstance(c.get("examples"), list) else str(c.get("examples") or ""),
    ]
    return " | ".join(p for p in parts if p)


def _metric_text(m: dict[str, Any]) -> str:
    parts = [
        str(m.get("name") or ""),
        str(m.get("description") or ""),
        " ".join(m.get("alias") or []) if isinstance(m.get("alias"), list) else str(m.get("alias") or ""),
        " ".join(m.get("related_columns") or [])
            if isinstance(m.get("related_columns"), list)
            else str(m.get("related_columns") or ""),
    ]
    return " | ".join(p for p in parts if p)


def _scan_dim_values(top_n: int) -> list[tuple[str, str]]:
    """Top-N distinct values per (table, column) on dw dim_* tables."""
    import pymysql
    out: list[tuple[str, str]] = []
    conn = pymysql.connect(
        host=cfg.mysql.host, port=int(cfg.mysql.port),
        user=cfg.mysql.ro_user, password=cfg.mysql.ro_password,
        database=cfg.mysql.dw_db, charset="utf8mb4", autocommit=True,
        connect_timeout=5,
    )
    try:
        with conn.cursor() as cur:
            for tbl, col in (
                ("dim_region", "region_name"),
                ("dim_customer", "member_level"),
                ("dim_product", "category"),
                ("dim_product", "brand"),
            ):
                cur.execute(
                    f"SELECT DISTINCT `{col}` FROM `{tbl}` ORDER BY `{col}` LIMIT %s",
                    (top_n,),
                )
                for (v,) in cur.fetchall():
                    if v is None or not str(v).strip():
                        continue
                    out.append((str(v), f"{tbl}.{col}"))
    finally:
        conn.close()
    return out


def build(meta: MetadataClient, embedding: EmbeddingClient,
          faiss: FAISSStore, fts5: FTS5Store,
          top_n_values: int = 100) -> dict[str, Any]:
    """Build all three indexes. Returns summary dict with counts + errors."""
    summary: dict[str, Any] = {"columns": 0, "metrics": 0, "values": 0, "errors": []}

    # ----- column_info -----
    try:
        faiss.column_info.reset()
        cols = meta.list_columns()
        if cols:
            texts = [_col_text(c) for c in cols]
            vecs = embedding.encode(texts)
            faiss.column_info.add(vecs, cols)
            summary["columns"] = len(cols)
    except Exception as e:
        summary["errors"].append(f"column_info: {type(e).__name__}: {e}")

    # ----- metric_info -----
    try:
        faiss.metric_info.reset()
        metrics = meta.list_metrics()
        if metrics:
            texts = [_metric_text(m) for m in metrics]
            vecs = embedding.encode(texts)
            faiss.metric_info.add(vecs, metrics)
            summary["metrics"] = len(metrics)
    except Exception as e:
        summary["errors"].append(f"metric_info: {type(e).__name__}: {e}")

    # ----- value_info (FTS5) -----
    try:
        fts5.reset()
        pairs = _scan_dim_values(top_n_values)
        if pairs:
            fts5.add_many(pairs)
        summary["values"] = len(pairs)
    except Exception as e:
        summary["errors"].append(f"value_info: {type(e).__name__}: {e}")

    return summary


def main() -> int:
    ap = argparse.ArgumentParser(description="Build FAISS + FTS5 knowledge indexes.")
    ap.add_argument("--top-n-values", type=int, default=100,
                    help="Per-dim-column distinct-value cap when populating FTS5")
    args = ap.parse_args()

    meta = MetadataClient()
    embedding = EmbeddingClient()
    faiss = FAISSStore()
    fts5 = FTS5Store()

    summary = build(meta, embedding, faiss, fts5, top_n_values=args.top_n_values)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 1 if summary["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())