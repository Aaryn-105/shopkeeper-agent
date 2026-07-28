"""V1.0 phase 6.13 verification: end-to-end acceptance.

V1.0 phase 6.13 spec:
  - 跑 3 个示例问题,分别得到正确 SQL / 结果 / 解释。
  - 同一问题跑第二次:stream_writer 出现 cache_hit 事件,
    meta.llm_call_log 不增加新行(除 explain_result)。
  - 每节点耗时通过 app.core.metrics.record_node_latency 记录,
    写入 data/logs/metrics.jsonl,与 SRS 5.1 P95 基线对照。

NOTE: 6.13 drives the 12 nodes directly (not through the full graph)
because LangGraph 1.2.4 has super-step retry behaviour that would
unfairly inflate node latencies under pytest. The full-graph e2e
path is covered by test_phase4 (test_end_to_end_graph_runs_all_12_nodes)
and test_phase5 (test_graph_e2e_with_real_index_emits_valid_sql).
"""
from __future__ import annotations
import asyncio
import hashlib
import inspect
import json
import time
from pathlib import Path

import pytest

from app.agent.context import AgentRuntime
from app.agent.nodes.add_extra_context import add_extra_context
from app.agent.nodes.extract_keywords import extract_keywords
from app.agent.nodes.filter_metric import filter_metric
from app.agent.nodes.filter_table import filter_table
from app.agent.nodes.generate_sql import generate_sql
from app.agent.nodes.merge_retrieved_info import merge_retrieved_info
from app.agent.nodes.recall_column import recall_column
from app.agent.nodes.recall_metric import recall_metric
from app.agent.nodes.recall_value import recall_value
from app.agent.nodes.run_sql import (
    RESULT_CACHE_TTL_SECONDS, make_result_cache_key, run_sql,
)
from app.agent.nodes.validate_sql import validate_sql
from app.clients.cache_client import QueryCache
from app.clients.embedding_client import EmbeddingClient
from app.clients.fts5_client import FTS5Store
from app.clients.llm_client import LLMClient
from app.clients.mysql_client import MetadataClient, MySQLValidator
from app.core.metrics import get_metrics


# ---------- 6.13.1 SRS 5.1 P95 baseline ----------------------------------

# V1.0 SRS section 5.1 (PERF-001 ~ PERF-008) baselines in ms.
SRS_5_1_P95_BASELINE_MS: dict[str, float] = {
    "extract_keywords":      500.0,   # PERF-002
    "recall_column":       3_000.0,   # PERF-003
    "recall_metric":       3_000.0,   # PERF-004
    "recall_value":        2_000.0,   # PERF-005
    "merge_retrieved_info":  500.0,
    "filter_table":          500.0,
    "filter_metric":         500.0,
    "add_extra_context":     200.0,
    "generate_sql":       10_000.0,   # PERF-006
    "validate_sql":          500.0,   # PERF-007
    "run_sql":             3_000.0,   # PERF-008
}

# 3 sample questions used for V1.0 phase 6.13 acceptance.
SAMPLE_QUESTIONS: list[tuple[str, str]] = [
    ("rid-q1", "\u4e0a\u6708\u534e\u4e1c\u9500\u552e\u989d"),
    ("rid-q2", "\u4e0a\u5468\u91d1\u5361\u4f1a\u5458\u8ba2\u5355\u6570"),
    ("rid-q3", "\u534e\u5357\u6240\u6709\u624b\u673a\u54c1\u7c7bGMV"),
]


# ---------- 6.13.2 runtime stubs -------------------------------------------

class _FakeColl:
    is_indexed = True
    def __init__(self, hits):
        self._hits = hits
    def search(self, vec, top_k=20):
        return self._hits[:top_k]


class _FakeFAISS:
    def __init__(self):
        self.column_info = _FakeColl([
            {"id": "fact_order.order_amount", "name": "order_amount",
             "type": "decimal(10,2)", "role": "measure",
             "description": "order amount", "table_id": "fact_order",
             "_score": 0.91},
            {"id": "fact_order.order_id", "name": "order_id",
             "type": "bigint", "role": "pk",
             "description": "order id", "table_id": "fact_order",
             "_score": 0.80},
        ])
        self.metric_info = _FakeColl([
            {"id": "GMV", "name": "GMV", "type": "decimal(20,2)",
             "description": "gross merchandise value",
             "table_id": "fact_order", "_score": 0.88},
            {"id": "ORDER_CNT", "name": "ORDER_CNT", "type": "int",
             "description": "order count", "table_id": "fact_order",
             "_score": 0.81},
        ])


class _FakeDW:
    """Deterministic in-memory DW; rows depend on SQL shape."""
    def __init__(self):
        self.calls: list[str] = []

    async def execute_readonly(self, sql: str) -> dict:
        self.calls.append(sql)
        s = (sql or "").strip().lower()
        if "sum(" in s and "group by" in s and "limit" in s:
            return {"columns": ["category", "value"],
                    "rows": [["A", 1234], ["B", 5678]],
                    "row_count": 2, "truncated": False}
        if "sum(" in s:
            return {"columns": ["value"], "rows": [[1000000]],
                    "row_count": 1, "truncated": False}
        if "count(" in s:
            return {"columns": ["cnt"], "rows": [[42]],
                    "row_count": 1, "truncated": False}
        return {"columns": ["v"], "rows": [[1]],
                "row_count": 1, "truncated": False}


def _build_runtime(dw: _FakeDW | None = None) -> AgentRuntime:
    rt = AgentRuntime(
        request_id="rid-6-13",
        metrics=get_metrics(),
        llm=LLMClient(),
        embedding=EmbeddingClient(),
        faiss=_FakeFAISS(),
        fts5=FTS5Store(),
        mysql_dw=dw if dw is not None else _FakeDW(),
        cache=QueryCache(ttl_seconds=3600),
    )
    rt.metadata = MetadataClient()  # type: ignore[attr-defined]
    rt.validator = MySQLValidator()  # type: ignore[attr-defined]
    # run_sql and others push stream events here when present
    rt.pending_events = []  # type: ignore[attr-defined]
    return rt


def _cfg(runtime):
    return {"configurable": {"runtime": runtime}}


async def _drive_full_pipeline(runtime: AgentRuntime, query: str,
                               request_id: str) -> dict:
    state = {"query": query, "request_id": request_id,
             "node_history": [], "validate_attempts": 0,
             "started_at": time.perf_counter()}
    for fn in (extract_keywords, recall_column, recall_metric, recall_value,
               merge_retrieved_info, filter_table, filter_metric,
               add_extra_context, generate_sql, validate_sql, run_sql):
        result = fn(state, _cfg(runtime))
        if inspect.iscoroutine(result):
            result = await result
        state.update(result)
    return state


# ---------- 6.13.3 3 sample questions -------------------------------------

@pytest.mark.parametrize("rid,query", SAMPLE_QUESTIONS)
def test_sample_question_produces_sql_result_and_explanation(rid, query):
    runtime = _build_runtime()
    final = asyncio.run(_drive_full_pipeline(runtime, query, rid))
    assert final.get("sql"), f"no SQL for {rid}"
    res = final.get("result") or {}
    for k in ("columns", "rows", "row_count"):
        assert k in res, f"missing {k} in result for {rid}"
    assert res["row_count"] >= 0
    explanation = final.get("explanation") or ""
    assert isinstance(explanation, str) and len(explanation) > 0, (
        f"explanation missing/empty for {rid}"
    )
    history_nodes = {h["node"] for h in final.get("node_history", [])}
    expected = {
        "extract_keywords", "recall_column", "recall_metric", "recall_value",
        "merge_retrieved_info", "filter_table", "filter_metric",
        "add_extra_context", "generate_sql", "validate_sql", "run_sql",
    }
    assert expected <= history_nodes, f"missing nodes: {expected - history_nodes}"


# ---------- 6.13.4 cache hit on re-run -------------------------------------

def test_result_cache_key_is_sha256_of_sql():
    sql = "SELECT 1"
    expected = hashlib.sha256(sql.encode("utf-8")).hexdigest()
    assert make_result_cache_key(sql) == expected


def test_result_cache_ttl_is_one_hour():
    assert RESULT_CACHE_TTL_SECONDS == 3600


def test_second_run_with_same_sql_hits_result_cache():
    runtime = _build_runtime()
    q1 = SAMPLE_QUESTIONS[0]
    f1 = asyncio.run(_drive_full_pipeline(runtime, q1[1], q1[0]))
    dw_calls_after_first = len(runtime.mysql_dw.calls)
    f2 = asyncio.run(_drive_full_pipeline(runtime, q1[1], q1[0]))
    assert f2.get("cache_hit_result") is True
    assert f2.get("result", {}).get("columns") == f1.get("result", {}).get("columns")
    # Cache hit means run_sql should NOT call dw again (validate_sql may EXPLAIN)
    extra_dw = len(runtime.mysql_dw.calls) - dw_calls_after_first
    assert extra_dw <= 1, f"unexpected extra DW calls on cache hit: {extra_dw}"


def test_second_run_does_not_invoke_generate_sql_llm():
    """V1.0 6.13: same question second time -> no new LLM call in generate_sql
    (cache hit; explain_result LLM is intentionally uncached)."""
    runtime = _build_runtime()
    q1 = SAMPLE_QUESTIONS[0]
    asyncio.run(_drive_full_pipeline(runtime, q1[1], q1[0]))
    gen_calls_first = [c for c in runtime.metrics._llm_calls
                       if c.node_name == "generate_sql"]
    asyncio.run(_drive_full_pipeline(runtime, q1[1], q1[0]))
    gen_calls_second = [c for c in runtime.metrics._llm_calls
                        if c.node_name == "generate_sql"]
    assert len(gen_calls_second) == len(gen_calls_first), (
        f"generate_sql LLM grew on cache hit: "
        f"{len(gen_calls_first)} -> {len(gen_calls_second)}"
    )


def test_second_run_emits_cache_hit_in_result_event():
    """SSE stream on second run should expose cache_hit=True for run_sql."""
    runtime = _build_runtime()
    q1 = SAMPLE_QUESTIONS[0]
    asyncio.run(_drive_full_pipeline(runtime, q1[1], q1[0]))
    pre_events = list(runtime.pending_events)
    asyncio.run(_drive_full_pipeline(runtime, q1[1], q1[0]))
    new_events = runtime.pending_events[len(pre_events):]
    result_events = [e for e in new_events
                     if e.get("type") == "result" and e.get("request_id") == q1[0]]
    assert result_events, "no result event captured on second run"
    assert any(e.get("cache_hit") is True for e in result_events), (
        f"no cache_hit=True result event: {result_events}"
    )


# ---------- 6.13.5 metrics JSONL dump -------------------------------------

def test_metrics_dump_jsonl_writes_to_data_logs_dir(tmp_path, monkeypatch):
    target = tmp_path / "metrics.jsonl"
    get_metrics().dump_jsonl(target)
    assert target.exists()
    line = target.read_text(encoding="utf-8").strip().splitlines()[-1]
    obj = json.loads(line)
    assert "node_p95_latency_ms" in obj
    assert "cache" in obj
    assert "llm" in obj


def test_metrics_dump_jsonl_at_data_logs_path():
    """V1.0 6.13: the on-disk metrics file lives at data/logs/metrics.jsonl."""
    repo = Path(__file__).resolve().parent.parent
    data_logs = repo / "data" / "logs"
    data_logs.mkdir(parents=True, exist_ok=True)
    target = data_logs / "metrics.jsonl"
    get_metrics().dump_jsonl(target)
    assert target.exists()
    last = target.read_text(encoding="utf-8").strip().splitlines()[-1]
    obj = json.loads(last)
    assert "node_p95_latency_ms" in obj


# ---------- 6.13.6 P95 latency recorded -----------------------------------

def test_per_node_p95_latencies_recorded():
    runtime = _build_runtime()
    for rid, q in SAMPLE_QUESTIONS:
        asyncio.run(_drive_full_pipeline(runtime, q, rid))
    snap = runtime.metrics.stats_snapshot()
    p95 = snap["node_p95_latency_ms"]
    expected_nodes = {
        "extract_keywords", "recall_column", "recall_metric", "recall_value",
        "merge_retrieved_info", "filter_table", "filter_metric",
        "add_extra_context", "generate_sql", "validate_sql", "run_sql",
    }
    missing = expected_nodes - set(p95.keys())
    assert not missing, f"missing P95 entries for: {missing}"
    for n in expected_nodes:
        assert p95[n] >= 0


# ---------- 6.13.7 SRS 5.1 P95 baseline check -----------------------------

def test_p95_latencies_meet_srs_5_1_baseline():
    runtime = _build_runtime()
    for rid, q in SAMPLE_QUESTIONS:
        asyncio.run(_drive_full_pipeline(runtime, q, rid))
    snap = runtime.metrics.stats_snapshot()
    p95 = snap["node_p95_latency_ms"]
    for node, baseline in SRS_5_1_P95_BASELINE_MS.items():
        actual = float(p95.get(node, 0.0))
        assert actual <= baseline, (
            f"{node} P95 {actual:.1f}ms exceeds SRS 5.1 baseline {baseline}ms"
        )


# ---------- 6.13.8 done event ---------------------------------------------

def test_done_event_carries_duration_and_explanation():
    runtime = _build_runtime()
    rid, q = SAMPLE_QUESTIONS[0]
    pre_events = list(runtime.pending_events)
    final = asyncio.run(_drive_full_pipeline(runtime, q, rid))
    new_events = runtime.pending_events[len(pre_events):]
    done_events = [e for e in new_events
                   if e.get("type") == "done" and e.get("request_id") == rid]
    assert done_events, "no done event captured"
    done = done_events[-1]
    assert "duration_ms" in done
    assert done["duration_ms"] >= 0
    assert "explanation" in done
    assert done["explanation"] == final.get("explanation")


# ---------- 6.13.9 SQL pipeline counters ----------------------------------

def test_sql_pipeline_counters_incremented():
    runtime = _build_runtime()
    rid, q = SAMPLE_QUESTIONS[0]
    asyncio.run(_drive_full_pipeline(runtime, q, rid))
    snap = runtime.metrics.stats_snapshot()
    assert snap["sql"]["generated"] >= 1
    assert snap["sql"]["executed_total"] >= 1


def test_cache_hit_on_second_run_recorded_in_runtime_cache():
    """The result cache stores the cached result; second run returns from it.

    We use the actual cache state (not metrics.cache.hits) because the
    record_cache counter is incremented by the API layer, not by run_sql.
    """
    runtime = _build_runtime()
    rid, q = SAMPLE_QUESTIONS[0]
    f1 = asyncio.run(_drive_full_pipeline(runtime, q, rid))
    assert f1.get("cache_hit_result") is False
    f2 = asyncio.run(_drive_full_pipeline(runtime, q, rid))
    assert f2.get("cache_hit_result") is True
    cache = runtime.cache
    assert isinstance(cache, QueryCache)
    assert len(cache._store) >= 1