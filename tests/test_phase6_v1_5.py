"""V1.0 phase 6.5 verification: merge_retrieved_info (4.2.5).

V1.0 phase 6.5 spec:
  - Group retrieved_columns by column_info.table_id.
  - meta_repo auto-fills PK/FK columns (customer_id / product_id / date_id /
    region_id) even when not recalled.
  - Attach retrieved_values per column_id (from 6.4 deduped output).
  - Group retrieved_metrics by table via metric.related_columns.
  - Output:
      state.merged_table_infos  (legacy, used by filter_table)
      state.table_infos         (SRS canonical)
      state.metric_infos        (SRS canonical metrics list)
"""
from __future__ import annotations
import pytest
from langchain_core.runnables import RunnableConfig

from app.agent.nodes.merge_retrieved_info import (
    MAX_EXAMPLES_PER_COLUMN,
    _table_id_of,
    _attach_value_examples,
    _values_by_column_id,
    _auto_fill_pk_fk,
    _attach_table_meta,
    _metrics_by_table,
    merge_retrieved_info,
    to_yaml_style,
)
from app.repositories.meta_repo import MetaRepo


# ---------- 6.5.1 constants & small helpers ----------

def test_max_examples_constant_is_reasonable():
    assert MAX_EXAMPLES_PER_COLUMN >= 1
    assert MAX_EXAMPLES_PER_COLUMN <= 16


def test_table_id_of_uses_explicit_field_first():
    assert _table_id_of({"table_id": "fact_order", "id": "x.y"}) == "fact_order"


def test_table_id_of_falls_back_to_id_prefix():
    assert _table_id_of({"id": "dim_region.region_name"}) == "dim_region"


def test_table_id_of_returns_empty_when_missing():
    assert _table_id_of({"name": "x"}) == ""


def test_values_by_column_id_groups():
    vals = [
        {"value": "\u534e\u4e1c", "column_id": "dim_region.region_name"},
        {"value": "\u534e\u5317", "column_id": "dim_region.region_name"},
        {"value": "\u624b\u673a", "column_id": "dim_product.category"},
        {"value": "noise", "column_id": None},
    ]
    out = _values_by_column_id(vals)
    assert out["dim_region.region_name"] == ["\u534e\u4e1c", "\u534e\u5317"]
    assert out["dim_product.category"] == ["\u624b\u673a"]


def test_attach_value_examples_adds_examples_field():
    col = {"id": "dim_region.region_name", "name": "region_name"}
    enriched = _attach_value_examples(col, {"dim_region.region_name": ["\u534e\u4e1c"]})
    assert enriched.get("examples") == "\u534e\u4e1c"
    assert enriched["_value_tokens"] == ["\u534e\u4e1c"]


def test_attach_value_examples_merges_with_existing():
    col = {"id": "x.y", "examples": "a, b"}
    enriched = _attach_value_examples(col, {"x.y": ["c"]})
    # deduplicated, comma-joined
    assert "a" in enriched["examples"]
    assert "b" in enriched["examples"]
    assert "c" in enriched["examples"]


def test_attach_value_examples_caps_at_max():
    col = {"id": "x.y"}
    vals = [f"v{i}" for i in range(MAX_EXAMPLES_PER_COLUMN + 5)]
    enriched = _attach_value_examples(col, {"x.y": vals})
    tokens = enriched["_value_tokens"]
    assert len(tokens) == MAX_EXAMPLES_PER_COLUMN


def test_attach_value_examples_no_match_returns_original():
    col = {"id": "x.y", "name": "y"}
    enriched = _attach_value_examples(col, {"other.col": ["z"]})
    assert "_value_tokens" not in enriched
    assert enriched["id"] == "x.y"


# ---------- 6.5.2 meta_repo ----------

class _StubMetadata:
    """In-memory replacement for MetadataClient."""

    def __init__(self, tables=None, columns=None, metrics=None):
        self._tables = tables or []
        self._columns = columns or {}
        self._metrics = metrics or []

    def list_tables(self):
        return list(self._tables)

    def list_columns(self, table_id=None):
        if table_id is None:
            out = []
            for tid, cols in self._columns.items():
                out.extend(cols)
            return out
        return list(self._columns.get(table_id, []))

    def list_metrics(self):
        return list(self._metrics)


@pytest.fixture
def stub_meta():
    return _StubMetadata(
        tables=[
            {"id": "fact_order", "name": "fact_order", "role": "fact",
             "description": "order fact table"},
            {"id": "dim_region", "name": "dim_region", "role": "dim",
             "description": "region dimension"},
            {"id": "dim_product", "name": "dim_product", "role": "dim",
             "description": "product dimension"},
        ],
        columns={
            "fact_order": [
                {"id": "fact_order.order_id", "name": "order_id",
                 "type": "bigint", "role": "pk"},
                {"id": "fact_order.customer_id", "name": "customer_id",
                 "type": "bigint", "role": "fk"},
                {"id": "fact_order.product_id", "name": "product_id",
                 "type": "bigint", "role": "fk"},
                {"id": "fact_order.date_id", "name": "date_id",
                 "type": "int", "role": "fk"},
                {"id": "fact_order.region_id", "name": "region_id",
                 "type": "int", "role": "fk"},
                {"id": "fact_order.order_amount", "name": "order_amount",
                 "type": "decimal(10,2)", "role": "measure"},
            ],
            "dim_region": [
                {"id": "dim_region.region_id", "name": "region_id",
                 "type": "int", "role": "pk"},
                {"id": "dim_region.region_name", "name": "region_name",
                 "type": "varchar(32)", "role": "attribute"},
            ],
            "dim_product": [
                {"id": "dim_product.product_id", "name": "product_id",
                 "type": "bigint", "role": "pk"},
                {"id": "dim_product.category", "name": "category",
                 "type": "varchar(32)", "role": "attribute"},
            ],
        },
        metrics=[
            {"id": "GMV", "name": "GMV", "description": "gross merchandise value",
             "related_columns": "fact_order.order_amount", "alias": ["\u9500\u552e\u989d"]},
            {"id": "ORDER_CNT", "name": "ORDER_CNT",
             "description": "order count",
             "related_columns": "fact_order.order_id", "alias": []},
        ],
    )


def test_meta_repo_get_table_returns_match(stub_meta):
    repo = MetaRepo(stub_meta)
    t = repo.get_table("fact_order")
    assert t and t["id"] == "fact_order"


def test_meta_repo_get_table_returns_none_when_missing(stub_meta):
    repo = MetaRepo(stub_meta)
    assert repo.get_table("dim_unknown") is None


def test_meta_repo_get_columns_filters_by_name(stub_meta):
    repo = MetaRepo(stub_meta)
    cols = repo.get_columns("fact_order", names=["order_amount", "customer_id"])
    names = {c["name"] for c in cols}
    assert names == {"order_amount", "customer_id"}


def test_meta_repo_get_pk_fk_columns_returns_expected(stub_meta):
    repo = MetaRepo(stub_meta)
    pk_fk = repo.get_pk_fk_columns("fact_order")
    names = {c["name"] for c in pk_fk}
    assert {"customer_id", "product_id", "date_id", "region_id"}.issubset(names)


def test_meta_repo_get_pk_fk_columns_empty_for_unknown_table(stub_meta):
    repo = MetaRepo(stub_meta)
    assert repo.get_pk_fk_columns("dim_unknown") == []


def test_meta_repo_list_metrics_returns_all(stub_meta):
    repo = MetaRepo(stub_meta)
    mids = {m["id"] for m in repo.list_metrics()}
    assert {"GMV", "ORDER_CNT"}.issubset(mids)


def test_meta_repo_get_metric_related_columns_parses(stub_meta):
    repo = MetaRepo(stub_meta)
    rels = repo.get_metric_related_columns("GMV")
    assert "fact_order.order_amount" in rels


def test_meta_repo_get_metric_related_columns_handles_list(stub_meta):
    stub_meta._metrics.append(
        {"id": "M3", "name": "M3", "description": "",
         "related_columns": ["fact_order.order_id", "fact_order.order_amount"], "alias": []}
    )
    repo = MetaRepo(stub_meta)
    rels = repo.get_metric_related_columns("M3")
    assert "fact_order.order_id" in rels
    assert "fact_order.order_amount" in rels


def test_meta_repo_get_metric_returns_none_for_unknown(stub_meta):
    repo = MetaRepo(stub_meta)
    assert repo.get_metric("UNKNOWN") is None


def test_auto_fill_pk_fk_adds_missing_columns(stub_meta):
    repo = MetaRepo(stub_meta)
    existing = [{"name": "order_amount"}]
    out = _auto_fill_pk_fk("fact_order", existing, repo)
    names = {c["name"] for c in out}
    assert {"order_amount", "customer_id", "product_id", "date_id", "region_id"}.issubset(names)
    # New entries should be flagged
    auto = [c for c in out if c.get("_auto_injected")]
    assert {c["name"] for c in auto} == {"customer_id", "product_id", "date_id", "region_id"}


def test_auto_fill_pk_fk_noop_when_already_present(stub_meta):
    """If every PK/FK column is already present, the helper is a no-op."""
    repo = MetaRepo(stub_meta)
    existing = [
        {"name": "customer_id"},
        {"name": "product_id"},
        {"name": "date_id"},
        {"name": "region_id"},
    ]
    out = _auto_fill_pk_fk("fact_order", existing, repo)
    assert len(out) == 4
    assert all(not c.get("_auto_injected") for c in out)



def test_attach_table_meta_enriches(stub_meta):
    repo = MetaRepo(stub_meta)
    bucket = {"table_id": "fact_order", "columns": []}
    out = _attach_table_meta("fact_order", bucket, repo)
    assert out["role"] == "fact"
    assert "order fact table" in out["description"]


def test_attach_table_meta_unknown_table_passthrough(stub_meta):
    repo = MetaRepo(stub_meta)
    bucket = {"table_id": "unknown", "columns": []}
    out = _attach_table_meta("unknown", bucket, repo)
    assert out["table_id"] == "unknown"
    assert "role" not in out


def test_metrics_by_table_groups_by_related_columns(stub_meta):
    repo = MetaRepo(stub_meta)
    metrics = [{"id": "GMV", "related_columns": "fact_order.order_amount"}]
    out = _metrics_by_table(metrics, repo)
    assert "fact_order" in out
    assert out["fact_order"][0]["id"] == "GMV"


def test_metrics_by_table_empty_when_no_repo():
    out = _metrics_by_table([{"id": "GMV"}], None)
    assert out == {}


# ---------- 6.5.3 node end-to-end ----------

class _StubMetrics:
    def __init__(self):
        self.latencies = []
    def record_node_latency(self, node, ms):
        self.latencies.append((node, ms))


class _StubRuntime:
    def __init__(self, metadata=None):
        self.metrics = _StubMetrics()
        self.metadata = metadata
        self.nodes_called = 0


def _cfg_with(runtime):
    cfg = RunnableConfig()
    cfg["configurable"] = {"runtime": runtime}
    return cfg


def _state(cols=None, metrics=None, values=None):
    return {
        "query": "x",
        "request_id": "rid-6-5",
        "node_history": [],
        "validate_attempts": 0,
        "retrieved_columns": cols or [],
        "retrieved_metrics": metrics or [],
        "retrieved_values": values or [],
    }


def test_merge_writes_merged_table_infos():
    rt = _StubRuntime(metadata=_StubMetadata())
    out = merge_retrieved_info(
        _state(cols=[{"id": "fact_order.order_amount", "name": "order_amount",
                      "table_id": "fact_order"}]),
        _cfg_with(rt),
    )
    assert "merged_table_infos" in out
    assert "fact_order" in out["merged_table_infos"]


def test_merge_writes_table_infos_canonical():
    rt = _StubRuntime(metadata=_StubMetadata())
    out = merge_retrieved_info(
        _state(cols=[{"id": "fact_order.order_amount", "name": "order_amount",
                      "table_id": "fact_order"}]),
        _cfg_with(rt),
    )
    assert "table_infos" in out
    assert "fact_order" in out["table_infos"]


def test_merge_writes_metric_infos_canonical():
    rt = _StubRuntime(metadata=_StubMetadata())
    out = merge_retrieved_info(
        _state(metrics=[{"id": "GMV", "name": "GMV", "description": "...",
                         "related_columns": "fact_order.order_amount",
                         "alias": ["\u9500\u552e\u989d"]}]),
        _cfg_with(rt),
    )
    assert "metric_infos" in out
    assert any(m["id"] == "GMV" for m in out["metric_infos"])


def test_merge_groups_columns_by_table():
    rt = _StubRuntime(metadata=_StubMetadata())
    cols = [
        {"id": "fact_order.order_amount", "name": "order_amount", "table_id": "fact_order"},
        {"id": "dim_region.region_name", "name": "region_name", "table_id": "dim_region"},
    ]
    out = merge_retrieved_info(_state(cols=cols), _cfg_with(rt))
    ti = out["table_infos"]
    assert "fact_order" in ti
    assert "dim_region" in ti
    assert any(c["name"] == "order_amount" for c in ti["fact_order"]["columns"])
    assert any(c["name"] == "region_name" for c in ti["dim_region"]["columns"])


def test_merge_infers_table_id_from_id_prefix_when_missing():
    rt = _StubRuntime(metadata=_StubMetadata())
    cols = [{"id": "fact_order.order_amount", "name": "order_amount"}]
    out = merge_retrieved_info(_state(cols=cols), _cfg_with(rt))
    assert "fact_order" in out["table_infos"]


def test_merge_auto_fills_pk_fk(stub_meta):
    rt = _StubRuntime(metadata=stub_meta)
    cols = [{"id": "fact_order.order_amount", "name": "order_amount",
             "table_id": "fact_order"}]
    out = merge_retrieved_info(_state(cols=cols), _cfg_with(rt))
    names = {c["name"] for c in out["table_infos"]["fact_order"]["columns"]}
    assert {"customer_id", "product_id", "date_id", "region_id"}.issubset(names)


def test_merge_attaches_value_examples_to_matching_column(stub_meta):
    rt = _StubRuntime(metadata=stub_meta)
    cols = [{"id": "dim_region.region_name", "name": "region_name",
             "table_id": "dim_region"}]
    values = [
        {"value": "\u534e\u4e1c", "column_id": "dim_region.region_name"},
        {"value": "\u534e\u5317", "column_id": "dim_region.region_name"},
    ]
    out = merge_retrieved_info(_state(cols=cols, values=values), _cfg_with(rt))
    col = next(c for c in out["table_infos"]["dim_region"]["columns"]
               if c["name"] == "region_name")
    assert "\u534e\u4e1c" in col.get("examples", "")
    assert "\u534e\u5317" in col.get("examples", "")


def test_merge_attaches_metric_ids_to_table():
    rt = _StubRuntime(metadata=_StubMetadata())
    metrics = [{"id": "GMV", "related_columns": "fact_order.order_amount"}]
    out = merge_retrieved_info(_state(metrics=metrics), _cfg_with(rt))
    fact = out["table_infos"]["fact_order"]
    assert "metrics" in fact
    assert "GMV" in fact["metrics"]


def test_merge_enriches_table_metadata(stub_meta):
    rt = _StubRuntime(metadata=stub_meta)
    cols = [{"id": "fact_order.order_amount", "name": "order_amount",
             "table_id": "fact_order"}]
    out = merge_retrieved_info(_state(cols=cols), _cfg_with(rt))
    fact = out["table_infos"]["fact_order"]
    assert fact.get("role") == "fact"
    assert "order fact table" in fact.get("description", "")


def test_merge_no_repo_falls_back_gracefully():
    """Without metadata, the node still groups columns and outputs metric_infos."""
    rt = _StubRuntime(metadata=None)
    cols = [{"id": "fact_order.order_amount", "name": "order_amount",
             "table_id": "fact_order"}]
    out = merge_retrieved_info(
        _state(cols=cols, metrics=[{"id": "GMV", "name": "GMV", "description": ""}]),
        _cfg_with(rt),
    )
    assert "fact_order" in out["table_infos"]
    assert out["metric_infos"][0]["id"] == "GMV"
    # No PK/FK auto-fill
    names = {c["name"] for c in out["table_infos"]["fact_order"]["columns"]}
    assert "customer_id" not in names


def test_merge_records_node_latency_and_counter():
    rt = _StubRuntime(metadata=_StubMetadata())
    merge_retrieved_info(_state(cols=[]), _cfg_with(rt))
    assert rt.nodes_called == 1
    nodes = [n for n, _ in rt.metrics.latencies]
    assert "merge_retrieved_info" in nodes


def test_merge_node_history_entry_records_counts():
    rt = _StubRuntime(metadata=_StubMetadata())
    cols = [{"id": "fact_order.order_amount", "name": "order_amount", "table_id": "fact_order"}]
    values = [{"value": "\u534e\u4e1c", "column_id": "dim_region.region_name"}]
    metrics = [{"id": "GMV", "name": "GMV", "description": "",
                "related_columns": "fact_order.order_amount"}]
    out = merge_retrieved_info(_state(cols=cols, metrics=metrics, values=values),
                               _cfg_with(rt))
    nh = out["node_history"][-1]
    assert nh["node"] == "merge_retrieved_info"
    assert nh["status"] == "ok"
    assert nh["tables"] >= 1
    assert nh["metrics"] >= 1


def test_merge_empty_state_produces_empty_outputs():
    rt = _StubRuntime(metadata=_StubMetadata())
    out = merge_retrieved_info(_state(), _cfg_with(rt))
    assert out["table_infos"] == {}
    assert out["metric_infos"] == []
    assert out["merged_table_infos"] == {}


def test_merge_dedupes_metric_ids_per_table(stub_meta):
    rt = _StubRuntime(metadata=stub_meta)
    metrics = [
        {"id": "GMV", "related_columns": "fact_order.order_amount"},
        {"id": "GMV", "related_columns": "fact_order.order_amount"},
    ]
    out = merge_retrieved_info(_state(metrics=metrics), _cfg_with(rt))
    assert out["table_infos"]["fact_order"]["metrics"].count("GMV") == 1


def test_to_yaml_style_helper_returns_string():
    out = to_yaml_style({"fact_order": {"table_id": "fact_order", "columns": []}})
    assert isinstance(out, str)
    assert "fact_order" in out