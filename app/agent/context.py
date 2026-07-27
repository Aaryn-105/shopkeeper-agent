"""Runtime context passed through the graph nodes.

A single Runtime carries shared resources (LLM, embedding, vector store, full-text
store, MySQL dw client, cache, metrics, logger). It is constructed once per
request and exposed via LangGraph''s configurable={"runtime": ...} so each node
can read it from the runtime parameter.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Optional

from app.core.metrics import Metrics


@dataclass
class AgentRuntime:
    """Shared dependencies injected into every node invocation."""
    request_id: str
    metrics: Metrics
    # clients (lazy-initialized when present)
    llm: Any = None
    embedding: Any = None
    faiss: Any = None
    fts5: Any = None
    mysql_dw: Any = None
    cache: Any = None

    # diagnostics
    nodes_called: int = 0