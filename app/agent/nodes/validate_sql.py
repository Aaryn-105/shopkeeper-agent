"""Node: validate_sql (4.2.10)."""
from __future__ import annotations
from langchain_core.runnables import RunnableConfig

from app.agent.nodes._helpers import get_runtime, history_append, log_node, now_ms
from app.agent.state import AgentState


def validate_sql(state: AgentState, config: RunnableConfig | None = None) -> dict:
    t0 = now_ms()
    runtime = get_runtime(config)
    request_id = state.get("request_id", "-")
    sql = state.get("sql", "")
    attempts = int(state.get("validate_attempts") or 0) + 1

    ok, message = True, "ok"
    validator = getattr(runtime, "validator", None) if runtime is not None else None
    if validator is None and runtime is not None and getattr(runtime, "mysql_dw", None) is not None:
        # fall back to ad-hoc EXPLAIN via dw client connection if validator absent
        try:
            import pymysql
            from app.core.config import cfg
            conn = pymysql.connect(
                host=cfg.mysql.host, port=int(cfg.mysql.port),
                user=cfg.mysql.ro_user, password=cfg.mysql.ro_password,
                database=cfg.mysql.dw_db, autocommit=True,
            )
            with conn.cursor() as cur:
                cur.execute(f"EXPLAIN {sql}")
            conn.close()
            ok, message = True, "ok"
        except Exception as e:
            ok, message = False, f"{type(e).__name__}: {str(e)[:160]}"
    elif validator is not None:
        ok, message = validator.validate(sql)

    if runtime is not None:
        runtime.metrics.record_node_latency("validate_sql", now_ms() - t0)
        runtime.metrics.record_sql_validated(corrected=not ok)
        runtime.nodes_called += 1
    log_node("validate_sql", request_id, "ok" if ok else "fail", msg=message)
    return {
        "sql_error": None if ok else message,
        "validate_attempts": attempts,
        "node_history": history_append(state, "validate_sql", "ok" if ok else "fail",
                                       now_ms() - t0, extra={"msg": message}),
    }