"""Service layer wrapping the NL2SQL workflow.

Phase 9.3 / Phase 9.5 deliverable:
  AskService is the single entry point used by /api/ask and the test
  harness. It hides whether execution goes through the LangGraph StateGraph
  or through a direct node-by-node driver. The latter is the path we run in
  Phase 9 because LangGraph 1.2.4 drops our AgentRuntime from configurable
  between super-steps, which broke Phase 9.3 end-to-end.

Two execution modes:
  mode="graph"   -- invokes the compiled LangGraph (forward path if we ever
                    fix the scheduler; keeps the SSE events flowing through
                    LangGraph stream_writer).
  mode="direct"  -- calls each node function directly with the runtime
                    injected via config so the LLM, FAISS, FTS5, MySQL and
                    cache clients are actually used.

Both modes share the same public run_question signature so swapping is a
single argument.
"""
from __future__ import annotations

import asyncio
import inspect
import time
from typing import Any, Callable

from app.agent.context import AgentRuntime
from app.agent.nodes.add_extra_context import add_extra_context
from app.agent.nodes.correct_sql import correct_sql
from app.agent.nodes.extract_keywords import extract_keywords
from app.agent.nodes.filter_metric import filter_metric
from app.agent.nodes.filter_table import filter_table
from app.agent.nodes.generate_sql import generate_sql
from app.agent.nodes.merge_retrieved_info import merge_retrieved_info
from app.agent.nodes.recall_column import recall_column
from app.agent.nodes.recall_metric import recall_metric
from app.agent.nodes.recall_value import recall_value
from app.agent.nodes.run_sql import run_sql
from app.agent.nodes.validate_sql import validate_sql
from app.agent.state import AgentState


# Keep validate -> correct_sql retries bounded so a broken query cannot loop
# forever. Matches graph.MAX_CORRECT_ATTEMPTS = 2 plus one safety pass.
MAX_VALIDATE_LOOP: int = 3


def _config_for(runtime: AgentRuntime) -> dict[str, Any]:
    """Build the RunnableConfig dict the nodes read runtime from."""
    return {"configurable": {"runtime": runtime}}


def _merge_update(state: dict[str, Any], update: dict[str, Any]) -> dict[str, Any]:
    """Apply a node return-dict onto state the same way LangGraph would.

    Policy:
      - List-typed keys that are explicitly annotated with operator.add
        (only `node_history`) are appended via the helpers that already
        return a fresh list including prior entries.
      - dict-typed keys are shallow-merged (e.g. table_infos).
      - All other keys overwrite.
    """
    if not update:
        return state
    for k, v in update.items():
        if isinstance(v, dict) and isinstance(state.get(k), dict):
            state[k] = {**state[k], **v}
        else:
            state[k] = v
    return state


async def _maybe_await(fn: Callable[..., Any], *args, **kwargs) -> Any:
    """Call fn, awaiting the result if fn is coroutine, otherwise returning
    the value synchronously wrapped in an awaitable."""
    result = fn(*args, **kwargs)
    if inspect.iscoroutine(result):
        return await result
    return result


async def _run_parallel(coros):
    """Await all coroutines concurrently."""
    return await asyncio.gather(*coros)


async def _build_coroutine(fn, state, cfg):
    return await _maybe_await(fn, state, cfg)


async def _run_direct(question: str, runtime: AgentRuntime,
                      max_loops: int = MAX_VALIDATE_LOOP) -> dict[str, Any]:
    """Run the workflow by calling each node directly with runtime threaded
    through config. Bypasses LangGraph's super-step scheduler."""
    state: dict[str, Any] = {
        "query": question,
        "request_id": runtime.request_id,
        "node_history": [],
        "started_at": time.perf_counter(),
    }
    cfg = _config_for(runtime)

    # 1) extract_keywords (single)
    update = await _maybe_await(extract_keywords, state, cfg)
    state = _merge_update(state, update)

    # 2) recall_* parallel fan-out
    rc, rm, rv = await _run_parallel([
        _build_coroutine(recall_column, state, cfg),
        _build_coroutine(recall_metric, state, cfg),
        _build_coroutine(recall_value, state, cfg),
    ])
    for u in (rc, rm, rv):
        state = _merge_update(state, u)

    # 3) merge (fan-in)
    update = await _maybe_await(merge_retrieved_info, state, cfg)
    state = _merge_update(state, update)

    # 4) filter_table / filter_metric parallel
    ft, fm = await _run_parallel([
        _build_coroutine(filter_table, state, cfg),
        _build_coroutine(filter_metric, state, cfg),
    ])
    state = _merge_update(state, ft)
    state = _merge_update(state, fm)

    # 5) add_extra_context
    update = await _maybe_await(add_extra_context, state, cfg)
    state = _merge_update(state, update)

    # 6) generate_sql
    update = await _maybe_await(generate_sql, state, cfg)
    state = _merge_update(state, update)

    # 7) validate_sql / correct_sql bounded loop
    for _ in range(max_loops):
        update = await _maybe_await(validate_sql, state, cfg)
        state = _merge_update(state, update)
        if not state.get("sql_error"):
            break
        update = await _maybe_await(correct_sql, state, cfg)
        state = _merge_update(state, update)

    # 8) run_sql
    update = await _maybe_await(run_sql, state, cfg)
    state = _merge_update(state, update)

    # Expose the result under both `result` and `execution_result` so downstream
    # callers (test, SSE) don't have to care which name a node used.
    if state.get("result") is not None and state.get("execution_result") is None:
        state["execution_result"] = state["result"]

    return state


async def _run_graph(question: str, runtime: AgentRuntime) -> dict[str, Any]:
    """Fall-back path that runs through the compiled StateGraph. Kept as a
    safety net in case the direct driver ever diverges from the graph spec."""
    from app.agent.graph import get_graph

    initial: AgentState = {
        "query": question,
        "request_id": runtime.request_id,
        "node_history": [],
        "validate_attempts": 0,
        "started_at": time.perf_counter(),
    }
    final = await get_graph().ainvoke(initial, config=_config_for(runtime))
    if isinstance(final, dict):
        return final
    return dict(final)


class AskService:
    """High-level facade for the NL2SQL workflow.

    Designed for two callers:
      - the FastAPI /api/ask route (uses mode="graph" once the SSE path is
        wired up to LangGraph stream_writer).
      - the phase-9 accuracy harness (uses mode="direct" until the graph
        scheduler stops dropping the runtime).
    """

    def __init__(self, mode: str = "direct") -> None:
        if mode not in ("direct", "graph"):
            raise ValueError(f"unknown AskService mode: {mode!r}")
        self._mode = mode

    @property
    def mode(self) -> str:
        return self._mode

    async def run_question(self, question: str,
                           runtime: AgentRuntime) -> dict[str, Any]:
        if self._mode == "direct":
            return await _run_direct(question, runtime)
        return await _run_graph(question, runtime)


def build_default_service() -> AskService:
    """Default service instance used by application code and tests."""
    return AskService(mode="direct")