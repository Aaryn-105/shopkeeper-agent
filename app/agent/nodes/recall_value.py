"""Node: recall_value (4.2.4).

Hits FTS5 for matching field values. Falls back to scanning dim_* distinct
values via direct MySQL when the FTS index is empty.
"""
from __future__ import annotations
import pymysql
from langchain_core.runnables import RunnableConfig

from app.agent.nodes._helpers import get_runtime, history_append, log_node, now_ms
from app.agent.state import AgentState
from app.core.config import cfg


def _scan_dim_values(request_id: str, query: str) -> list[dict]:
    """Scan dim_* for rows whose text columns contain any query token.

    Development fallback: once the FTS5 index is built by the sync script this
    is bypassed and the primary path is FTS5.
    """
    tokens = [t for t in query.replace(",", " ").split() if t]
    if not tokens:
        return []
    out: list[dict] = []
    try:
        conn = pymysql.connect(
            host=cfg.mysql.host, port=int(cfg.mysql.port),
            user=cfg.mysql.ro_user, password=cfg.mysql.ro_password,
            database=cfg.mysql.dw_db, charset="utf8mb4", autocommit=True,
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
    keywords = state.get("keywords") or []

    hits: list[dict] = []
    if runtime is not None and runtime.fts5 is not None:
        try:
            for text in [query, " ".join(keywords)]:
                if not text.strip():
                    continue
                for h in runtime.fts5.search(text):
                    if h not in hits:
                        hits.append(h)
        except Exception:
            hits = []

    if not hits:
        hits = _scan_dim_values(request_id, query)

    elapsed = now_ms() - t0
    if runtime is not None:
        runtime.metrics.record_node_latency("recall_value", elapsed)
        runtime.nodes_called += 1
    log_node("recall_value", request_id, "ok", hits=len(hits))
    return {
        "retrieved_values": hits,
        "node_history": history_append(state, "recall_value", "ok", elapsed,
                                       extra={"hits": len(hits)}),
    }