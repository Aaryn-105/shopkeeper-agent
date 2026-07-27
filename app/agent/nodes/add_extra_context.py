"""Node: add_extra_context (4.2.8 / V1.0 phase 6.8).

V1.0 phase 6.8 spec:
  state.extra_context = {
    "current_time":   datetime.now().isoformat(),
    "db_type":        "MySQL",
    "db_version":     "8.0.x..." (probed at runtime),
    "today_weekday":  "星期一" / "Monday" / ...
  }

The legacy ``now`` key is preserved so the existing phase-4 test and any
downstream consumer keep working alongside the SRS-canonical ``current_time``.
"""
from __future__ import annotations
import pymysql
from datetime import datetime, timezone
from typing import Any

from langchain_core.runnables import RunnableConfig

from app.agent.nodes._helpers import get_runtime, history_append, log_node, now_ms
from app.agent.state import AgentState
from app.core.config import cfg


# V1.0 phase 6.8: SRS canonical labels (Chinese weekdays per spec example)
WEEKDAY_CN: list[str] = ["星期一", "星期二", "星期三", "星期四",
                         "星期五", "星期六", "星期日"]
WEEKDAY_EN: list[str] = ["Monday", "Tuesday", "Wednesday", "Thursday",
                         "Friday", "Saturday", "Sunday"]
DEFAULT_DB_VERSION: str = "unknown"


def _today_weekday(now: datetime | None = None) -> tuple[str, str]:
    """Return (chinese_weekday, english_weekday) for ``now`` (default: now()).

    ``now`` may be a naive or tz-aware datetime; both work because we only
    read its weekday().
    """
    dt = now or datetime.now()
    idx = dt.weekday()  # Monday == 0, Sunday == 6
    return WEEKDAY_CN[idx], WEEKDAY_EN[idx]


def _probe_db_version(timeout_sec: int = 3) -> str:
    """Run ``SELECT VERSION()`` on the MySQL RO account; returns the version
    string or an ``"error: ..."`` sentinel on any failure.
    """
    try:
        conn = pymysql.connect(
            host=cfg.mysql.host, port=int(cfg.mysql.port),
            user=cfg.mysql.ro_user, password=cfg.mysql.ro_password,
            connect_timeout=timeout_sec,
        )
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT VERSION()")
                row = cur.fetchone()
                return str(row[0]) if row and row[0] is not None else DEFAULT_DB_VERSION
        finally:
            conn.close()
    except Exception as e:
        return f"error: {type(e).__name__}"


def build_extra_context(
    now: datetime | None = None,
    db_version: str | None = None,
) -> dict[str, Any]:
    """Pure helper used by both the node and unit tests.

    - When ``db_version`` is None, probe MySQL via ``_probe_db_version``.
    - When ``db_version`` is provided, use it verbatim (handy for tests).
    """
    dt = now or datetime.now(timezone.utc)
    weekday_cn, weekday_en = _today_weekday(dt.replace(tzinfo=None))
    if db_version is None:
        db_version = _probe_db_version()
    return {
        # SRS canonical keys
        "current_time": dt.isoformat(),
        "db_type": "MySQL",
        "db_version": db_version,
        "today_weekday": weekday_cn,
        # convenience aliases
        "today_weekday_en": weekday_en,
        # legacy alias (kept for the phase-4 test + any downstream consumer)
        "now": dt.isoformat(),
    }


def add_extra_context(state: AgentState, config: RunnableConfig | None = None) -> dict:
    t0 = now_ms()
    runtime = get_runtime(config)
    request_id = state.get("request_id", "-")
    ctx = build_extra_context()

    elapsed = now_ms() - t0
    if runtime is not None:
        runtime.metrics.record_node_latency("add_extra_context", elapsed)
        runtime.nodes_called += 1
    log_node(
        "add_extra_context", request_id, "ok",
        db_type=ctx["db_type"],
        db_version=ctx["db_version"],
        weekday=ctx["today_weekday"],
    )
    return {
        "extra_context": ctx,
        "node_history": history_append(
            state, "add_extra_context", "ok", elapsed,
            extra={
                "db_type": ctx["db_type"],
                "db_version": ctx["db_version"],
                "today_weekday": ctx["today_weekday"],
            },
        ),
    }