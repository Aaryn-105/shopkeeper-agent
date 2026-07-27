"""Node: merge_retrieved_info (4.2.5).

Aggregates retrieved_columns / retrieved_metrics / retrieved_values into a
hierarchical table_infos dict keyed by table_id.
"""
from __future__ import annotations
import json
from langchain_core.runnables import RunnableConfig

from app.agent.nodes._helpers import get_runtime, history_append, log_node, now_ms
from app.agent.state import AgentState


def _parse_alias(value) -> list[str]:
    if not value:
        return []
    if isinstance(value, list):
        return [str(v) for v in value]
    try:
        v = json.loads(value)
        return [str(x) for x in v] if isinstance(v, list) else []
    except Exception:
        return [t.strip() for t in str(value).replace(",", " ").split() if t.strip()]


def merge_retrieved_info(state: AgentState, config: RunnableConfig | None = None) -> dict:
    t0 = now_ms()
    runtime = get_runtime(config)
    request_id = state.get("request_id", "-")
    cols = state.get("retrieved_columns") or []
    metrics = state.get("retrieved_metrics") or []
    values = state.get("retrieved_values") or []

    table_infos: dict[str, dict] = {}

    # build per-table buckets from columns
    for col in cols:
        tid = col.get("table_id") or col.get("id", "").split(".", 1)[0]
        if not tid:
            continue
        bucket = table_infos.setdefault(tid, {"table_id": tid, "columns": []})
        # enrich examples from values when available
        examples = col.get("examples")
        if not examples and values:
            ex = [v["value"] for v in values if v.get("column_id", "").startswith(tid + ".")][:5]
            if ex:
                col = {**col, "examples": ", ".join(str(x) for x in ex)}
        bucket["columns"].append(col)

    # attach metrics by joining through column_metric if available
    if runtime is not None and getattr(runtime, "metadata", None) is not None:
        try:
            column_metric = runtime.metadata.list_columns()  # we just use list to hint
            # simple heuristic: link metric to any table whose columns mention it
            for m in metrics:
                mid = m.get("id", "")
                for tid in list(table_infos.keys()):
                    rel = m.get("related_columns")
                    rel_list = _parse_alias(rel) if rel else []
                    if any(tid in r or r.startswith(tid + ".") for r in rel_list):
                        table_infos[tid].setdefault("metrics", []).append(mid)
        except Exception:
            pass

    # ensure primary_key / foreign_key fields are present even if not recalled
    runtime_needed_keys = {
        "fact_order": ["customer_id", "product_id", "date_id", "region_id"],
        "dim_customer": ["customer_id"],
        "dim_product": ["product_id"],
        "dim_region": ["region_id"],
        "dim_date": ["date_id"],
    }
    if runtime is not None and getattr(runtime, "metadata", None) is not None:
        for tid in list(table_infos.keys()):
            needed = runtime_needed_keys.get(tid, [])
            if not needed:
                continue
            existing_cols = {c.get("name") for c in table_infos[tid]["columns"]}
            for col in runtime.metadata.list_columns(tid):
                if col.get("name") in needed and col.get("name") not in existing_cols:
                    table_infos[tid]["columns"].append(col)

    if runtime is not None:
        runtime.metrics.record_node_latency("merge_retrieved_info", now_ms() - t0)
        runtime.nodes_called += 1
    log_node("merge_retrieved_info", request_id, "ok", tables=len(table_infos))
    return {
        "merged_table_infos": table_infos,
        "node_history": history_append(state, "merge_retrieved_info", "ok", now_ms() - t0,
                                       extra={"tables": len(table_infos)}),
    }