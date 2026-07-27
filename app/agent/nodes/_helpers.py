"""Shared helpers used by every LangGraph node."""
from __future__ import annotations
import time
from typing import Any

from langchain_core.runnables import RunnableConfig

from app.agent.state import AgentState
from app.core.logger import logger


def now_ms() -> float:
    return time.perf_counter() * 1000.0


def history_append(state: AgentState, node: str, status: str, ms: float,
                   extra: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    h = list(state.get("node_history") or [])
    entry: dict[str, Any] = {"node": node, "status": status, "ms": round(ms, 2)}
    if extra:
        entry.update(extra)
    h.append(entry)
    return h


def get_runtime(config: RunnableConfig | None):
    """Pull the AgentRuntime out of configurable."""
    if config is None:
        return None
    return (config.get("configurable") or {}).get("runtime")


def log_node(node: str, request_id: str, status: str, **fields: Any) -> None:
    logger.bind(request_id=request_id, node=node).info(
        f"node {node} {status}: " + " ".join(f"{k}={v}" for k, v in fields.items())
    )