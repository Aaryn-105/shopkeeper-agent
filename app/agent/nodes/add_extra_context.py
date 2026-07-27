"""Node: add_extra_context (4.2.8)."""
from __future__ import annotations
import pymysql
from datetime import datetime, timezone
from langchain_core.runnables import RunnableConfig

from app.agent.nodes._helpers import get_runtime, history_append, log_node, now_ms
from app.agent.state import AgentState
from app.core.config import cfg


def add_extra_context(state: AgentState, config: RunnableConfig | None = None) -> dict:
    t0 = now_ms()
    runtime = get_runtime(config)
    request_id = state.get("request_id", "-")

    ctx = {
        "now": datetime.now(timezone.utc).isoformat(),
        "db_type": "mysql",
        "db_version": "unknown",
    }
    try:
        conn = pymysql.connect(
            host=cfg.mysql.host, port=int(cfg.mysql.port),
            user=cfg.mysql.ro_user, password=cfg.mysql.ro_password,
            connect_timeout=3,
        )
        with conn.cursor() as cur:
            cur.execute("SELECT VERSION()")
            ctx["db_version"] = str(cur.fetchone()[0])
        conn.close()
    except Exception as e:
        ctx["db_version"] = f"error: {type(e).__name__}"

    if runtime is not None:
        runtime.metrics.record_node_latency("add_extra_context", now_ms() - t0)
        runtime.nodes_called += 1
    log_node("add_extra_context", request_id, "ok", db_version=ctx["db_version"])
    return {
        "extra_context": ctx,
        "node_history": history_append(state, "add_extra_context", "ok", now_ms() - t0),
    }