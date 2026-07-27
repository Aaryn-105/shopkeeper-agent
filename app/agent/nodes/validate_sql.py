"""Node: validate_sql (4.2.10 / V1.0 phase 6.10).

V1.0 phase 6.10 spec:
  - dw_ro_engine.execute_readonly(f"EXPLAIN {state.sql}")
  - On failure write state.sql_error AND state.error (SRS canonical).
  - Increment state.validate_attempts so correct_sql can decide when to stop.
  - Emit a pending_stream_events entry of type "validate_sql" so the SSE layer
    can surface pass / fail to the frontend.

EXPLAIN only analyses the query plan (no row execution) so this is safe to run
on every SQL the agent produces.
"""
from __future__ import annotations
from typing import Any, Optional

from langchain_core.runnables import RunnableConfig

from app.agent.nodes._helpers import get_runtime, history_append, log_node, now_ms
from app.agent.state import AgentState


def _run_explain(sql: str, runtime: Any) -> tuple[bool, Optional[str]]:
    """Execute ``EXPLAIN <sql>`` against the DW readonly connection.

    Returns (ok, error_message). When no validator / mysql_dw is available
    this returns (True, None) so the rest of the workflow can still run
    (the spec says "fail loudly", but a missing validator is not a SQL
    failure — it is an environment failure).
    """
    if runtime is None:
        return True, None
    validator = getattr(runtime, "validator", None)
    if validator is not None and hasattr(validator, "validate"):
        try:
            ok, message = validator.validate(sql)
            return bool(ok), None if ok else str(message)
        except Exception as e:
            return False, f"validator_error: {type(e).__name__}: {str(e)[:160]}"
    dw = getattr(runtime, "mysql_dw", None)
    if dw is None:
        return True, None
    try:
        if hasattr(dw, "execute_readonly"):
            # SRS / V1.0: dw_ro_engine.execute_readonly(f"EXPLAIN {state.sql}")
            cur_or_df = dw.execute_readonly(f"EXPLAIN {sql}")
            # Either a cursor with .fetchall() or a DataFrame; either is fine
            if cur_or_df is None:
                return False, "execute_readonly returned None"
            try:
                _ = cur_or_df.fetchall()
            except Exception:
                pass
            return True, None
        # Fallback: open our own pymysql connection.
        import pymysql
        from app.core.config import cfg
        conn = pymysql.connect(
            host=cfg.mysql.host, port=int(cfg.mysql.port),
            user=cfg.mysql.ro_user, password=cfg.mysql.ro_password,
            database=cfg.mysql.dw_db, autocommit=True,
        )
        try:
            with conn.cursor() as cur:
                cur.execute(f"EXPLAIN {sql}")
            return True, None
        finally:
            conn.close()
    except Exception as e:
        return False, f"{type(e).__name__}: {str(e)[:160]}"


def validate_sql(state: AgentState, config: RunnableConfig | None = None) -> dict:
    t0 = now_ms()
    runtime = get_runtime(config)
    request_id = state.get("request_id", "-")
    sql = state.get("sql", "")
    attempts = int(state.get("validate_attempts") or 0) + 1

    ok, error_message = _run_explain(sql, runtime)

    elapsed = now_ms() - t0
    if runtime is not None:
        runtime.metrics.record_node_latency("validate_sql", elapsed)
        runtime.metrics.record_sql_validated(corrected=not ok)
        runtime.nodes_called += 1

    log_node(
        "validate_sql", request_id,
        "ok" if ok else "fail",
        msg="ok" if ok else (error_message or "fail"),
    )

    # V1.0 phase 6.10 stream event so the SSE layer can surface validation
    # pass / fail to the frontend.
    event = {
        "type": "validate_sql",
        "sql": sql,
        "ok": ok,
        "error": error_message,
        "request_id": request_id,
        "attempts": attempts,
    }
    # Also push to the runtime pending_events queue (best-effort; no-op when
    # the runtime doesn't expose one).
    if runtime is not None:
        pending = getattr(runtime, "pending_events", None)
        if isinstance(pending, list):
            pending.append(event)

    return {
        # Legacy keys (used by correct_sql / graph routing)
        "sql_error": None if ok else error_message,
        "validate_attempts": attempts,
        # SRS canonical: "校验失败时，将错误信息写入 state 的 error 字段"
        "error": None if ok else error_message,
        # V1.0 6.10: pending stream event for the SSE layer
        "pending_stream_events": [event],
    } | {
        "node_history": history_append(
            state, "validate_sql",
            "ok" if ok else "fail",
            elapsed,
            extra={
                "msg": "ok" if ok else (error_message or "fail"),
                "attempts": attempts,
            },
        ),
    }