"""Phase 5 verification: build_knowledge_index idempotency + real-vector recall.

Covers what handover document 3.1 promised:
  - column_info vector hits when FAISS index built
  - metric_info vector hits when FAISS index built
  - value_info FTS5 hits when synced
  - end-to-end graph uses real indexes (no fallback path)
"""
from __future__ import annotations
import asyncio
import json
import subprocess
import sys
from pathlib import Path

from app.agent.context import AgentRuntime
from app.agent.graph import build_graph
from app.agent.state import AgentState
from app.clients.embedding_client import EmbeddingClient
from app.clients.faiss_client import FAISSStore
from app.clients.fts5_client import FTS5Store
from app.clients.llm_client import LLMClient
from app.clients.mysql_client import (
    MetadataClient, MySQLClient, MySQLValidator,
)
from app.core.config import cfg
from app.core.metrics import get_metrics


PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# ---------- pure unit / smoke ----------

def test_build_script_runs_and_returns_summary():
    """Direct Python API call; mirrors what scripts/build_knowledge_index.py does."""
    from scripts.build_knowledge_index import build
    summary = build(
        MetadataClient(),
        EmbeddingClient(),
        FAISSStore(),
        FTS5Store(),
    )
    assert summary["errors"] == []
    assert summary["columns"] >= 1
    assert summary["metrics"] >= 1
    assert summary["values"] >= 1


def test_build_script_cli_smoke():
    """Subprocess invocation of scripts/build_knowledge_index.py."""
    script = SCRIPTS_DIR / "build_knowledge_index.py"
    assert script.exists(), script
    out = subprocess.run(
        [sys.executable, str(script), "--top-n-values", "50"],
        capture_output=True, text=True, timeout=120,
        cwd=str(PROJECT_ROOT),
    )
    assert out.returncode == 0, f"stderr={out.stderr}"
    payload = json.loads(out.stdout.strip())
    assert payload["errors"] == []
    assert payload["columns"] >= 1 and payload["metrics"] >= 1 and payload["values"] >= 1


# ---------- vector / FTS5 recall tests ----------

def test_recall_column_returns_vector_hits_when_index_built():
    from scripts.build_knowledge_index import build
    embedding = EmbeddingClient()
    faiss = FAISSStore()
    fts5 = FTS5Store()
    summary = build(MetadataClient(), embedding, faiss, fts5)
    assert summary["errors"] == []
    assert summary["columns"] >= 1

    vec = embedding.encode(["订单金额"])[0]
    hits = faiss.column_info.search(vec, top_k=10)
    assert hits, "vector search returned no hits for 订单金额"
    ids = [h.get("id") for h in hits]
    assert any("order_amount" in (i or "") for i in ids), f"hits={ids}"


def test_recall_metric_returns_vector_hits_when_index_built():
    from scripts.build_knowledge_index import build
    embedding = EmbeddingClient()
    faiss = FAISSStore()
    fts5 = FTS5Store()
    summary = build(MetadataClient(), embedding, faiss, fts5)
    assert summary["errors"] == []
    assert summary["metrics"] >= 1

    vec = embedding.encode(["GMV 销售总额"])[0]
    hits = faiss.metric_info.search(vec, top_k=10)
    assert hits, "vector search returned no hits for GMV 销售总额"
    names = [(h.get("id") or "") for h in hits]
    assert any("GMV" in n for n in names), f"hits={names}"


def test_recall_value_returns_fts5_hits_when_synced():
    from scripts.build_knowledge_index import build
    faiss = FAISSStore()
    fts5 = FTS5Store()
    summary = build(MetadataClient(), EmbeddingClient(), faiss, fts5)
    assert summary["errors"] == []
    assert summary["values"] >= 1

    hits = fts5.search("华北")
    assert hits, "no FTS hits for 华北"
    assert any("华北" in h["value"] for h in hits)
    assert any(h["column_id"].startswith("dim_") for h in hits)


# ---------- per-node direct recall with real index ----------

def test_recall_column_node_uses_real_index():
    """The node should call vector search when FAISS is populated, returning hits."""
    from scripts.build_knowledge_index import build
    from app.agent.nodes.recall_column import recall_column

    embedding = EmbeddingClient()
    faiss = FAISSStore()
    fts5 = FTS5Store()
    summary = build(MetadataClient(), embedding, faiss, fts5)
    assert summary["errors"] == []

    runtime = AgentRuntime(
        request_id="rc-direct",
        metrics=get_metrics(),
        llm=LLMClient(),
        embedding=embedding,
        faiss=faiss,
        fts5=fts5,
        mysql_dw=MySQLClient(),
        cache=None,
    )
    runtime.validator = MySQLValidator()
    runtime.metadata = MetadataClient()

    state: AgentState = {
        "query": "订单金额",
        "request_id": "rc-direct",
        "keywords": ["订单", "金额"],
    }
    cfg_obj = {"configurable": {"runtime": runtime}}
    out = asyncio.run(recall_column(state, cfg_obj))
    cols = out.get("retrieved_columns") or []
    assert len(cols) > 0, "recall_column returned no hits against real index"
    assert any("order_amount" in (c.get("id") or "") for c in cols), f"hits={cols}"


def test_recall_metric_node_uses_real_index():
    from scripts.build_knowledge_index import build
    from app.agent.nodes.recall_metric import recall_metric

    embedding = EmbeddingClient()
    faiss = FAISSStore()
    fts5 = FTS5Store()
    summary = build(MetadataClient(), embedding, faiss, fts5)
    assert summary["errors"] == []

    runtime = AgentRuntime(
        request_id="rm-direct",
        metrics=get_metrics(),
        llm=LLMClient(),
        embedding=embedding,
        faiss=faiss,
        fts5=fts5,
        mysql_dw=MySQLClient(),
        cache=None,
    )
    runtime.validator = MySQLValidator()
    runtime.metadata = MetadataClient()

    state: AgentState = {
        "query": "销售总额",
        "request_id": "rm-direct",
        "keywords": ["销售", "总额"],
    }
    cfg_obj = {"configurable": {"runtime": runtime}}
    out = asyncio.run(recall_metric(state, cfg_obj))
    metrics = out.get("retrieved_metrics") or []
    assert len(metrics) > 0, "recall_metric returned no hits against real index"
    assert any("GMV" in (m.get("id") or "") for m in metrics), f"hits={metrics}"


def test_recall_value_node_uses_real_index():
    from scripts.build_knowledge_index import build
    from app.agent.nodes.recall_value import recall_value

    faiss = FAISSStore()
    fts5 = FTS5Store()
    summary = build(MetadataClient(), EmbeddingClient(), faiss, fts5)
    assert summary["errors"] == []

    runtime = AgentRuntime(
        request_id="rv-direct",
        metrics=get_metrics(),
        llm=LLMClient(),
        embedding=EmbeddingClient(),
        faiss=faiss,
        fts5=fts5,
        mysql_dw=MySQLClient(),
        cache=None,
    )
    runtime.validator = MySQLValidator()
    runtime.metadata = MetadataClient()

    state: AgentState = {
        "query": "华北",
        "request_id": "rv-direct",
        "keywords": ["华北"],
    }
    cfg_obj = {"configurable": {"runtime": runtime}}
    out = recall_value(state, cfg_obj)
    vals = out.get("retrieved_values") or []
    assert len(vals) > 0, "recall_value returned no hits against real FTS5"
    assert any("华北" in (v.get("value") or "") for v in vals), f"hits={vals}"


# ---------- end-to-end with real indexes ----------

def test_graph_e2e_with_real_index_emits_valid_sql():
    """After build, the graph must produce SQL through the real-index path
    (recall_column/recall_metric/recall_value all see non-empty FAISS/FTS5).

    We assert only the SQL envelope here; per-node hit counts under graph
    execution are covered by the direct node tests above.
    """
    from scripts.build_knowledge_index import build
    embedding = EmbeddingClient()
    faiss = FAISSStore()
    fts5 = FTS5Store()
    summary = build(MetadataClient(), embedding, faiss, fts5)
    assert summary["errors"] == []

    runtime = AgentRuntime(
        request_id="p5-e2e",
        metrics=get_metrics(),
        llm=LLMClient(),
        embedding=embedding,
        faiss=faiss,
        fts5=fts5,
        mysql_dw=MySQLClient(),
        cache=None,
    )
    runtime.validator = MySQLValidator()
    runtime.metadata = MetadataClient()

    initial: AgentState = {
        "query": "华北地区销售总额",
        "request_id": "p5-e2e",
        "node_history": [],
        "validate_attempts": 0,
    }
    graph = build_graph()
    final = asyncio.run(
        graph.ainvoke(initial, config={"configurable": {"runtime": runtime}})
    )

    sql = final.get("sql") or ""
    assert sql.strip().upper().startswith("SELECT")
    # generate_sql + validate_sql + run_sql must all appear in history
    history_nodes = {h["node"] for h in final.get("node_history", [])}
    expected = {
        "extract_keywords", "recall_column", "recall_metric", "recall_value",
        "merge_retrieved_info", "filter_table", "filter_metric",
        "add_extra_context", "generate_sql", "validate_sql", "run_sql",
    }
    assert expected <= history_nodes, f"missing nodes: {expected - history_nodes}"
    assert final.get("result") is not None
    asyncio.run(runtime.mysql_dw.aclose())