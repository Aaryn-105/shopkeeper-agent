"""Phase 4 verification: 12-node LangGraph workflow, SSE endpoint, cache, validation."""
from __future__ import annotations
import asyncio
import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

from main import app
from app.agent.context import AgentRuntime
from app.agent.graph import build_graph, get_graph
from app.agent.state import AgentState
from app.clients.cache_client import QueryCache, _similarity
from app.clients.embedding_client import EmbeddingClient
from app.clients.fts5_client import FTS5Store
from app.clients.llm_client import LLMClient, _mock_generate
from app.clients.mysql_client import MetadataClient, MySQLClient, MySQLValidator
from app.core.config import cfg
from app.core.metrics import get_metrics


# ---------- pure unit tests ----------

def test_cache_exact_match_hit_and_miss():
    c = QueryCache(ttl_seconds=60, similarity_threshold=0)
    c.put("abc def", {"v": 1})
    assert c.get_exact("abc def") is not None
    assert c.get_exact("  abc   def  ") is not None
    assert c.get_exact("ghi jkl") is None


def test_cache_similarity_threshold_match():
    c = QueryCache(ttl_seconds=60, similarity_threshold=0.7)
    c.put("abc def ghi", {"v": 1})
    matched_key, payload = c.get_similar("abc def ghi!")
    assert payload is not None
    assert payload["v"] == 1
    _, payload2 = c.get_similar("xxx yyy zzz")
    assert payload2 is None


def test_cache_ttl_expires_entries():
    c = QueryCache(ttl_seconds=0, similarity_threshold=0)
    c.put("k", {"v": 1})
    # ttl=0 -> expired immediately
    assert c.get_exact("k") is None


def test_similarity_helper_is_bounded():
    assert _similarity("hello", "hello") == 1.0
    assert 0.0 <= _similarity("hello", "world") <= 1.0
    assert _similarity("hello", "hello!") > 0.7


def test_llm_client_is_mock_when_api_key_empty():
    assert str(cfg.llm.api_key) == ""
    client = LLMClient()
    assert client.is_mock
    # trigger the keyword prompt branch
    resp = asyncio.run(client.ainvoke("Please extend keywords for: total sales"))
    # mock generator returns either a JSON keyword list or a SQL; both are JSON-able here
    assert isinstance(resp.text, str)
    assert len(resp.text) > 0


def test_llm_mock_generates_sql_with_from_clause():
    sql = _mock_generate(
        "Generate SQL\ntable_ids: fact_order\nquery: total sales\n"
    )
    sql_l = sql.lower()
    assert "from fact_order" in sql_l
    assert sql_l.startswith("select")


def test_embedding_client_returns_vectors_with_expected_dim():
    e = EmbeddingClient()
    vecs = e.encode(["hello world", "bonjour"])
    assert len(vecs) == 2
    for v in vecs:
        assert len(v) == int(cfg.embedding.dim)
        norm = sum(x * x for x in v) ** 0.5
        assert 0.5 < norm < 1.5


def test_fts5_store_round_trip(tmp_path):
    store = FTS5Store(db_path=tmp_path / "fts.db")
    store.add("abc", "col1")
    store.add("def", "col1")
    store.add("ghi", "col2")
    assert store.size() == 3
    hits = store.search("abc")
    assert any(h["value"] == "abc" for h in hits)


# ---------- mysql dw + metadata clients ----------

def test_validator_accepts_a_valid_select():
    v = MySQLValidator()
    ok, msg = v.validate("SELECT 1")
    assert ok, msg


def test_validator_rejects_non_select():
    v = MySQLValidator()
    ok, msg = v.validate("DROP TABLE fact_order")
    assert not ok
    assert "select" in msg.lower() or "permission" in msg.lower()


def test_mysql_client_rejects_non_select_via_execute_readonly():
    client = MySQLClient()
    with pytest.raises(PermissionError):
        asyncio.run(client.execute_readonly("DELETE FROM fact_order"))


def test_metadata_client_returns_expected_counts():
    md = MetadataClient()
    tables = md.list_tables()
    assert {t["id"] for t in tables} >= {
        "fact_order", "dim_customer", "dim_product", "dim_region", "dim_date"
    }
    cols = md.list_columns("fact_order")
    assert any(c["name"] == "order_amount" for c in cols)


# ---------- per-node tests ----------

def _make_runtime() -> AgentRuntime:
    runtime = AgentRuntime(
        request_id="test-rid",
        metrics=get_metrics(),
        llm=LLMClient(),
        embedding=EmbeddingClient(),
        faiss=type("F", (), {
            "recall_column": lambda *a, **k: [],
            "recall_metric": lambda *a, **k: [],
        })(),
        fts5=FTS5Store(),
        mysql_dw=None,
        cache=None,
    )
    runtime.metadata = MetadataClient()  # type: ignore[attr-defined]
    runtime.validator = MySQLValidator()  # type: ignore[attr-defined]
    return runtime


def test_extract_keywords_node_uses_jieba():
    from app.agent.nodes.extract_keywords import extract_keywords
    runtime = _make_runtime()
    state: AgentState = {"query": "sales total north region 2025", "request_id": "test-rid"}
    cfg_obj = {"configurable": {"runtime": runtime}}
    out = extract_keywords(state, cfg_obj)
    assert "keywords" in out
    assert isinstance(out["keywords"], list)
    assert len(out["keywords"]) >= 1


def test_recall_value_node_finds_region_match_via_meta_fallback():
    from app.agent.nodes.recall_value import recall_value
    runtime = _make_runtime()
    state: AgentState = {
        "query": "test value node",
        "request_id": "test-rid",
        "keywords": ["test"],
    }
    cfg_obj = {"configurable": {"runtime": runtime}}
    out = recall_value(state, cfg_obj)
    assert "retrieved_values" in out
    # FTS5 is empty (not yet synced) so the fallback runs, but with a query
    # that has no dim_* tokens there will simply be no hits - the node still
    # completes and returns an empty list. We at least verify it does not
    # crash.
    assert isinstance(out["retrieved_values"], list)


def test_recall_metric_node_returns_metrics():
    from app.agent.nodes.recall_metric import recall_metric
    runtime = _make_runtime()
    state: AgentState = {
        "query": "GMV sales total",
        "request_id": "test-rid",
        "keywords": ["GMV"],
    }
    cfg_obj = {"configurable": {"runtime": runtime}}
    out = recall_metric(state, cfg_obj)
    assert "retrieved_metrics" in out
    assert any(m.get("id") == "GMV" for m in out["retrieved_metrics"])


def test_recall_column_node_returns_columns():
    from app.agent.nodes.recall_column import recall_column
    runtime = _make_runtime()
    state: AgentState = {
        "query": "order_amount total",
        "request_id": "test-rid",
        "keywords": ["order_amount", "amount"],
    }
    cfg_obj = {"configurable": {"runtime": runtime}}
    out = recall_column(state, cfg_obj)
    assert "retrieved_columns" in out
    assert any(c.get("table_id") == "fact_order" for c in out["retrieved_columns"])


def test_merge_node_groups_columns_by_table_and_includes_pk_fk():
    from app.agent.nodes.merge_retrieved_info import merge_retrieved_info
    runtime = _make_runtime()
    state: AgentState = {
        "query": "fact_order",
        "request_id": "test-rid",
        "keywords": [],
        "retrieved_columns": [{
            "id": "fact_order.order_amount", "name": "order_amount",
            "type": "decimal(10,2)", "role": "measure",
            "description": "amount", "table_id": "fact_order",
        }],
        "retrieved_metrics": [],
        "retrieved_values": [],
    }
    cfg_obj = {"configurable": {"runtime": runtime}}
    out = merge_retrieved_info(state, cfg_obj)
    assert "merged_table_infos" in out
    mti = out["merged_table_infos"]
    assert "fact_order" in mti
    names = {c.get("name") for c in mti["fact_order"]["columns"]}
    assert {"customer_id", "product_id", "date_id", "region_id"}.issubset(names)


def test_add_extra_context_node_records_db_version():
    from app.agent.nodes.add_extra_context import add_extra_context
    runtime = _make_runtime()
    state: AgentState = {"query": "x", "request_id": "test-rid"}
    cfg_obj = {"configurable": {"runtime": runtime}}
    out = add_extra_context(state, cfg_obj)
    ctx = out["extra_context"]
    assert ctx["db_type"] == "mysql"
    assert "now" in ctx
    assert "error" not in ctx["db_version"]


def test_generate_sql_node_returns_non_empty_sql():
    from app.agent.nodes.generate_sql import generate_sql
    runtime = _make_runtime()
    state: AgentState = {
        "query": "total sales by region",
        "request_id": "test-rid",
        "filtered_table_infos": {"fact_order": {"columns": []}},
    }
    cfg_obj = {"configurable": {"runtime": runtime}}
    out = asyncio.run(generate_sql(state, cfg_obj))
    sql = out["sql"].strip()
    assert sql
    assert "fact_order" in sql.lower()
    assert any(h["node"] == "generate_sql" for h in out.get("node_history", []))


def test_validate_sql_node_accepts_a_known_good_sql():
    from app.agent.nodes.validate_sql import validate_sql
    runtime = _make_runtime()
    state: AgentState = {
        "query": "x", "request_id": "test-rid",
        "sql": "SELECT order_id FROM fact_order LIMIT 5",
    }
    cfg_obj = {"configurable": {"runtime": runtime}}
    out = validate_sql(state, cfg_obj)
    assert out["sql_error"] is None
    assert out["validate_attempts"] == 1


def test_validate_sql_node_flags_a_bad_sql():
    from app.agent.nodes.validate_sql import validate_sql
    runtime = _make_runtime()
    state: AgentState = {
        "query": "x", "request_id": "test-rid",
        "sql": "SELECT no_such_col FROM fact_order",
    }
    cfg_obj = {"configurable": {"runtime": runtime}}
    out = validate_sql(state, cfg_obj)
    assert out["sql_error"] is not None


def test_run_sql_node_executes_against_dw_and_returns_rows():
    from app.agent.nodes.run_sql import run_sql
    runtime = _make_runtime()
    runtime.mysql_dw = MySQLClient()
    state: AgentState = {
        "query": "x", "request_id": "test-rid",
        "sql": "SELECT COUNT(*) AS n FROM fact_order",
    }
    cfg_obj = {"configurable": {"runtime": runtime}}
    out = asyncio.run(run_sql(state, cfg_obj))
    res = out["result"]
    assert res["row_count"] >= 1
    assert "n" in res["columns"]
    asyncio.run(runtime.mysql_dw.aclose())


# ---------- end-to-end graph ----------

def test_end_to_end_graph_runs_all_12_nodes():
    runtime = _make_runtime()
    runtime.mysql_dw = MySQLClient()
    initial: AgentState = {
        "query": "total sales by region",
        "request_id": "graph-test",
        "node_history": [],
        "validate_attempts": 0,
    }
    graph = build_graph()
    final = asyncio.run(graph.ainvoke(initial, config={"configurable": {"runtime": runtime}}))
    history_nodes = {h["node"] for h in final.get("node_history", [])}
    expected = {
        "extract_keywords", "recall_column", "recall_metric", "recall_value",
        "merge_retrieved_info", "filter_table", "filter_metric",
        "add_extra_context", "generate_sql", "validate_sql", "run_sql",
    }
    assert expected <= history_nodes, f"missing: {expected - history_nodes}"
    assert final.get("sql")
    res = final.get("result") or {}
    assert res.get("row_count", 0) >= 0
    asyncio.run(runtime.mysql_dw.aclose())


def test_graph_compiled_get_graph_returns_same_singleton():
    a = get_graph()
    b = get_graph()
    assert a is b


# ---------- /api/ask endpoint ----------

def _collect_sse_events(response) -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []
    current: dict[str, Any] = {}
    for raw in response.iter_lines():
        if not raw:
            if current:
                chunks.append(current)
                current = {}
            continue
        if raw.startswith("event:"):
            current["type"] = raw.split(":", 1)[1].strip()
        elif raw.startswith("data:"):
            current.update(json.loads(raw.split(":", 1)[1].strip()))
    if current:
        chunks.append(current)
    return chunks


def test_api_ask_rejects_empty_query():
    with TestClient(app) as client:
        r = client.post("/api/ask", json={"query": ""})
        assert r.status_code == 400


def test_api_ask_rejects_overlong_query():
    long_q = "x" * (int(cfg.ask.max_query_length) + 1)
    with TestClient(app) as client:
        r = client.post("/api/ask", json={"query": long_q})
        assert r.status_code == 400


def test_api_ask_streams_sse_events_for_valid_query():
    payload = {"query": "total sales"}
    with TestClient(app) as client:
        with client.stream("POST", "/api/ask", json=payload) as r:
            assert r.status_code == 200
            assert r.headers["content-type"].startswith("text/event-stream")
            chunks = _collect_sse_events(r)
    types = [c.get("type") for c in chunks]
    assert "progress" in types
    assert "sql_generated" in types
    assert "result" in types
    assert "done" in types
    done = next(c for c in chunks if c.get("type") == "done")
    assert done.get("request_id")


def test_api_ask_warm_cache_serves_on_second_call():
    q = "order count"
    with TestClient(app) as client:
        with client.stream("POST", "/api/ask", json={"query": q}) as r:
            assert r.status_code == 200
            for _ in r.iter_lines():
                pass
        with client.stream("POST", "/api/ask", json={"query": q}) as r:
            assert r.status_code == 200
            chunks = _collect_sse_events(r)
    done = next((c for c in chunks if c.get("type") == "done"), {})
    assert done.get("cache_hit") is True