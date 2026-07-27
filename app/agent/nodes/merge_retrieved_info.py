"""Node: merge_retrieved_info (4.2.5 / V1.0 phase 6.5).

V1.0 phase 6.5 spec:
  - Group retrieved_columns by column_info.table_id.
  - Call meta_repo to fetch table metadata + auto-fill PK/FK columns
    (customer_id / product_id / date_id / region_id) even when not recalled.
  - Attach retrieved_values to their column_id (per 6.4 deduped output).
  - Group retrieved_metrics by table via metric.related_columns.
  - Output (SRS 4.2.5):
      * state.merged_table_infos   -- legacy dict used by filter_table
      * state.table_infos          -- SRS canonical hierarchical dict
      * state.metric_infos         -- SRS canonical metrics list

The output is YAML-friendly: each table is a dict with table_id / name / role /
description / columns (list of column dicts) / metrics (list of metric ids).
"""
from __future__ import annotations
import json
from typing import Any

from langchain_core.runnables import RunnableConfig

from app.agent.nodes._helpers import get_runtime, history_append, log_node, now_ms
from app.agent.state import AgentState
from app.repositories.meta_repo import MetaRepo, _parse_alias


# V1.0 phase 6.5: cap values attached to each column\'s ``examples`` so the
# merged context stays small enough for downstream LLM prompts.
MAX_EXAMPLES_PER_COLUMN: int = 8


def _table_id_of(col: dict[str, Any]) -> str:
    """Extract the table_id from a recalled column dict."""
    tid = col.get("table_id")
    if tid:
        return str(tid)
    cid = col.get("id") or ""
    if "." in cid:
        return cid.split(".", 1)[0]
    return ""


def _attach_value_examples(
    column: dict[str, Any],
    values_by_column_id: dict[str, list[str]],
) -> dict[str, Any]:
    """Return a copy of ``column`` with examples enriched from recall_value."""
    cid = column.get("id") or f"{column.get('table_id','')}.{column.get('name','')}"
    examples = values_by_column_id.get(cid) or []
    if not examples:
        return column
    # Keep the LLM prompt compact; cap per SRS prompt budget.
    capped = examples[:MAX_EXAMPLES_PER_COLUMN]
    existing = column.get("examples")
    out = dict(column)
    if existing:
        # Merge, dedupe, preserve order.
        seen = set()
        merged: list[str] = []
        for piece in str(existing).replace(",", " ").split():
            if piece and piece not in seen:
                seen.add(piece)
                merged.append(piece)
        for v in capped:
            sv = str(v)
            if sv not in seen:
                seen.add(sv)
                merged.append(sv)
        out["examples"] = ", ".join(merged)
    else:
        out["examples"] = ", ".join(str(v) for v in capped)
    out["_value_tokens"] = list(capped)[:MAX_EXAMPLES_PER_COLUMN]
    return out


def _values_by_column_id(retrieved_values: list[dict[str, Any]]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for v in retrieved_values or []:
        cid = v.get("column_id")
        val = v.get("value")
        if not cid or val is None:
            continue
        out.setdefault(str(cid), []).append(str(val))
    return out


def _metrics_by_table(
    retrieved_metrics: list[dict[str, Any]],
    repo: MetaRepo | None,
) -> dict[str, list[dict[str, Any]]]:
    """Return ``{table_id: [metric_dict, ...]}`` using each metric\'s related_columns.

    Falls back to an empty mapping when the repository is unreachable or when
    the recall hit doesn\'t carry related_columns AND the repo lookup also fails.
    """
    out: dict[str, list[dict[str, Any]]] = {}
    for m in retrieved_metrics or []:
        mid = m.get("id") or ""
        rels = m.get("related_columns")
        if (rels is None or rels == [] or rels == "") and mid and repo is not None:
            try:
                full = repo.get_metric(mid)
                if full:
                    rels = full.get("related_columns")
            except Exception:
                rels = []
        if not rels:
            continue
        rels_list = _parse_alias(rels)
        tables = {r.split(".", 1)[0] for r in rels_list if "." in r}
        for tid in tables:
            out.setdefault(tid, []).append(m)
    return out


def _auto_fill_pk_fk(
    table_id: str,
    existing: list[dict[str, Any]],
    repo: MetaRepo,
) -> list[dict[str, Any]]:
    """SRS 4.2.5 rule 6: ensure PK / FK columns are present even if not recalled."""
    try:
        pk_fk = repo.get_pk_fk_columns(table_id)
    except Exception:
        return existing
    if not pk_fk:
        return existing
    have = {c.get("name") for c in existing}
    out = list(existing)
    for c in pk_fk:
        if c.get("name") not in have:
            tagged = dict(c)
            tagged["_auto_injected"] = True
            out.append(tagged)
            have.add(c.get("name"))
    return out


def _attach_table_meta(
    table_id: str,
    bucket: dict[str, Any],
    repo: MetaRepo,
) -> dict[str, Any]:
    """Enrich the bucket with name/role/description from meta_repo."""
    try:
        meta = repo.get_table(table_id)
    except Exception:
        meta = None
    out = dict(bucket)
    if meta:
        if meta.get("name"):
            out.setdefault("name", meta.get("name"))
        if meta.get("role"):
            out.setdefault("role", meta.get("role"))
        if meta.get("description"):
            out.setdefault("description", meta.get("description"))
    return out


def merge_retrieved_info(state: AgentState, config: RunnableConfig | None = None) -> dict:
    t0 = now_ms()
    runtime = get_runtime(config)
    request_id = state.get("request_id", "-")
    cols = state.get("retrieved_columns") or []
    metrics = state.get("retrieved_metrics") or []
    values = state.get("retrieved_values") or []

    repo: MetaRepo | None = None
    if runtime is not None and getattr(runtime, "metadata", None) is not None:
        try:
            repo = MetaRepo(runtime.metadata)
        except Exception:
            repo = None

    values_by_cid = _values_by_column_id(values)
    metrics_by_tbl = _metrics_by_table(metrics, repo)

    # --- Step 1: group columns by table_id ----------------------------------
    table_infos: dict[str, dict[str, Any]] = {}
    for col in cols:
        tid = _table_id_of(col)
        if not tid:
            continue
        bucket = table_infos.setdefault(tid, {"table_id": tid, "columns": []})
        bucket["columns"].append(col)

    # --- Step 2: attach metric ids to each table ----------------------------
    for tid, mlist in metrics_by_tbl.items():
        bucket = table_infos.setdefault(tid, {"table_id": tid, "columns": []})
        bucket.setdefault("metrics", [])
        for m in mlist:
            mid = m.get("id") or ""
            if mid and mid not in bucket["metrics"]:
                bucket["metrics"].append(mid)

    # --- Step 3: enrich each table (values, PK/FK, metadata) ----------------
    for tid in list(table_infos.keys()):
        bucket = table_infos[tid]
        bucket["columns"] = [
            _attach_value_examples(c, values_by_cid) for c in bucket["columns"]
        ]
        if repo is not None:
            bucket["columns"] = _auto_fill_pk_fk(tid, bucket["columns"], repo)
            table_infos[tid] = _attach_table_meta(tid, bucket, repo)
        else:
            table_infos[tid] = bucket

    # --- Step 4: produce SRS-canonical metric_infos -------------------------
    metric_infos: list[dict[str, Any]] = []
    for m in metrics or []:
        mid = m.get("id") or ""
        if not mid:
            continue
        entry: dict[str, Any] = {
            "id": mid,
            "name": m.get("name") or mid,
            "description": m.get("description") or "",
            "related_columns": m.get("related_columns") or [],
            "alias": m.get("alias") or [],
        }
        metric_infos.append(entry)

    # --- Step 5: write outputs --------------------------------------------
    elapsed = now_ms() - t0
    if runtime is not None:
        runtime.metrics.record_node_latency("merge_retrieved_info", elapsed)
        runtime.nodes_called += 1
    log_node(
        "merge_retrieved_info", request_id, "ok",
        tables=len(table_infos), metrics=len(metric_infos),
    )
    return {
        # legacy key (used by filter_table / generate_sql)
        "merged_table_infos": table_infos,
        # SRS 4.2.5 canonical keys
        "table_infos": table_infos,
        "metric_infos": metric_infos,
        "node_history": history_append(
            state, "merge_retrieved_info", "ok", elapsed,
            extra={
                "tables": len(table_infos),
                "metrics": len(metric_infos),
                "values": sum(len(v) for v in values_by_cid.values()),
            },
        ),
    }


def to_yaml_style(table_infos: dict[str, Any]) -> str:
    """Helper used by downstream prompt builders (filter_*, generate_sql).

    Not invoked by the node itself, but kept here so callers don\'t reimplement
    the serialisation. Matches the SRS 4.2.5 rule 7 ("YAML-style hierarchical
    structure, easy for the LLM to read").
    """
    return json.dumps(table_infos, ensure_ascii=False, indent=2)