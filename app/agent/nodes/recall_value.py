"""Node: recall_value (4.2.4 / V1.0 phase 6.4).

V1.0 phase 6.4 spec:
  - jieba.cut(query) -> keyword list
  - For each token, fts5_client.search_values(token, top_k=30)
  - Aggregate hits with the same column_id (dedupe by (value, column_id))
  - Write state.retrieved_values (SRS canonical shape: list[{value, column_id}])

Note: prior implementation used `str.split()` and ran a single FTS5 query,
which lost Chinese tokenisation quality. V1.0 phase 6.4 introduces jieba
tokenisation and per-token recall.
"""
from __future__ import annotations
import jieba  # module-level so unit tests can monkeypatch
from typing import Any

from langchain_core.runnables import RunnableConfig

from app.agent.nodes._helpers import get_runtime, history_append, log_node, now_ms
from app.agent.state import AgentState
from app.core.config import cfg
from app.agent.nodes.extract_keywords import STOP_WORDS


# V1.0 phase 6.4: per-token FTS5 top_k = 30
TOPK_PER_TOKEN: int = 30

# Reuse the stop-word set defined for extract_keywords so the two recall paths
# agree on what to ignore.
_VALUE_STOP_WORDS = STOP_WORDS


def _tokenize(query: str) -> list[str]:
    """Split the query with jieba; fall back to whitespace when jieba is cold.

    Returns a deduplicated list of non-empty tokens not in STOP_WORDS, in
    document order.
    """
    text = (query or "").strip()
    if not text:
        return []
    try:
        raw = [t.strip() for t in jieba.cut(text) if t and t.strip()]
    except Exception:
        raw = text.replace(",", " ").split()
    out: list[str] = []
    seen: set[str] = set()
    for tok in raw:
        if not tok or tok in _VALUE_STOP_WORDS:
            continue
        if tok in seen:
            continue
        seen.add(tok)
        out.append(tok)
    return out


def _search_one_token(runtime, token: str, top_k: int) -> list[dict[str, Any]]:
    """Run a single FTS5 search; returns [] on any failure."""
    if runtime is None or runtime.fts5 is None:
        return []
    try:
        hits = runtime.fts5.search(token, top_k=top_k)
        return hits or []
    except Exception:
        return []


def _aggregate_by_column_id(hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Dedupe hits by (value, column_id) preserving score-desc insertion order.

    Output is SRS-canonical: list[{value, column_id, _tokens}] where
    _tokens records which token(s) caused this hit (useful for debugging
    and merge node 6.5).
    """
    seen: set[tuple[str, str]] = set()
    out: list[dict[str, Any]] = []
    for h in hits:
        v = h.get("value")
        c = h.get("column_id")
        if v is None or c is None:
            continue
        key = (str(v), str(c))
        if key in seen:
            # augment _tokens on the existing entry
            existing = next(o for o in out if (o["value"], o["column_id"]) == key)
            tok = h.get("_token")
            if tok and tok not in existing["_tokens"]:
                existing["_tokens"].append(tok)
            continue
        seen.add(key)
        entry: dict[str, Any] = {
            "value": str(v),
            "column_id": str(c),
            "_tokens": [h["_token"]] if h.get("_token") else [],
        }
        out.append(entry)
    return out


def _scan_dim_values_fallback(query: str) -> list[dict[str, Any]]:
    """Direct MySQL fallback when the FTS5 index is empty / unavailable.

    Mirrors scripts/build_knowledge_index._scan_dim_values; we duplicate it
    here to keep the recall node self-contained when running unit tests
    against a fresh FTS5 index.
    """
    import pymysql
    tokens = [t for t in _tokenize(query) if t and t not in _VALUE_STOP_WORDS]
    if not tokens:
        return []
    out: list[dict[str, Any]] = []
    try:
        conn = pymysql.connect(
            host=cfg.mysql.host, port=int(cfg.mysql.port),
            user=cfg.mysql.ro_user, password=cfg.mysql.ro_password,
            database=cfg.mysql.dw_db, charset="utf8mb4", autocommit=True,
            connect_timeout=5,
        )
        with conn.cursor() as cur:
            for tbl, col in (
                ("dim_region", "region_name"),
                ("dim_customer", "member_level"),
                ("dim_product", "category"),
                ("dim_product", "brand"),
            ):
                where = " OR ".join([f"`{col}` LIKE %s"] * len(tokens))
                params = [f"%{t}%" for t in tokens]
                cur.execute(
                    f"SELECT DISTINCT `{col}` FROM `{tbl}` WHERE {where} LIMIT 20",
                    params,
                )
                for (v,) in cur.fetchall():
                    if v is None:
                        continue
                    out.append({"value": v, "column_id": f"{tbl}.{col}"})
        conn.close()
    except Exception:
        pass
    return out


def recall_value(state: AgentState, config: RunnableConfig | None = None) -> dict:
    t0 = now_ms()
    runtime = get_runtime(config)
    request_id = state.get("request_id", "-")
    query = state.get("query", "")

    # --- Step 1: jieba.cut -> tokens ----------------------------------------
    tokens = _tokenize(query)

    # --- Step 2: per-token FTS5 search (top_k=30) ---------------------------
    raw_hits: list[dict[str, Any]] = []
    for tok in tokens:
        for h in _search_one_token(runtime, tok, top_k=TOPK_PER_TOKEN):
            raw_hits.append({**h, "_token": tok})

    # --- Step 3: aggregate by (value, column_id) ----------------------------
    out = _aggregate_by_column_id(raw_hits)

    # --- Step 4: MySQL fallback when FTS5 returned nothing ------------------
    # Opt-in via runtime.allow_mysql_fallback (default True for prod, False in
    # unit tests so we never hit the live DW from `recall_value`).
    if not out and tokens and getattr(runtime, "allow_mysql_fallback", True):
        fb = _scan_dim_values_fallback(query)
        # stamp _tokens so downstream tests stay uniform
        for entry in fb:
            entry["_tokens"] = list(tokens)
        out = _aggregate_by_column_id(fb)

    elapsed = now_ms() - t0
    if runtime is not None:
        runtime.metrics.record_node_latency("recall_value", elapsed)
        runtime.nodes_called += 1
    log_node(
        "recall_value", request_id, "ok",
        hits=len(out), tokens=len(tokens),
    )
    return {
        "retrieved_values": out,
        "node_history": history_append(
            state, "recall_value", "ok", elapsed,
            extra={"hits": len(out), "tokens": len(tokens)},
        ),
    }