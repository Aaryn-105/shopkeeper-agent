"""Node: filter_table (4.2.6 / V1.0 phase 6.6).

V1.0 phase 6.6 spec:
  - Load filter_table_info.prompt.
  - Call the LLM with (query, table_infos) to ask for keep_table_ids /
    keep_column_ids.
  - Apply the filter, BUT always preserve PK / FK columns per SRS 4.2.6 rule 4.
  - Write state.filtered_table_infos.

The legacy merge-stage fallback (keep everything) is preserved when the LLM
returns an empty / unparseable response so we never drop joins mid-flight.
"""
from __future__ import annotations
import json
from pathlib import Path
from typing import Any

from langchain_core.runnables import RunnableConfig

from app.agent.nodes._helpers import get_runtime, history_append, log_node, now_ms
from app.agent.state import AgentState
from app.core.metrics import LLMCallStat


_PROMPT_PATH = Path(__file__).resolve().parents[2] / "prompt" / "filter_table_info.prompt"
_FALLBACK_PROMPT = (
    "你是 NL2SQL 助手。基于用户问题与候选表信息，精筛保留的表与字段。\n"
    "用户问题：{query}\n候选表信息：{table_infos}\n"
    "返回 JSON：{{\"keep_table_ids\": [...], \"keep_column_ids\": [...]}}"
)

# Column roles that SRS 4.2.6 rule 4 says must always survive filtering.
PK_FK_ROLES = {"pk", "fk"}


def _load_prompt_template() -> str:
    try:
        if _PROMPT_PATH.exists():
            return _PROMPT_PATH.read_text(encoding="utf-8")
    except Exception:
        pass
    return _FALLBACK_PROMPT


def _serialize_table_infos(table_infos: dict[str, dict[str, Any]]) -> str:
    """Compact YAML-style serialisation suitable for the prompt context."""
    return json.dumps(table_infos, ensure_ascii=False, indent=2)


def _parse_keep_response(text: str) -> tuple[list[str], list[str]]:
    """Parse the LLM JSON response into (keep_table_ids, keep_column_ids).

    Accepts:
      {"keep_table_ids": [...], "keep_column_ids": [...]}
      {"keep_tables": [...], "keep_columns": [...]}
      bare lists [...]                                  -> table ids
      "table_a, table_b"                               -> comma-separated
    Returns ([], []) when nothing parseable.
    """
    if not text:
        return [], []
    text = text.strip()
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            keep_tables = obj.get("keep_table_ids") or obj.get("keep_tables") or []
            keep_cols = obj.get("keep_column_ids") or obj.get("keep_columns") or []
            return [str(x) for x in keep_tables], [str(x) for x in keep_cols]
        if isinstance(obj, list):
            return [str(x) for x in obj], []
    except (json.JSONDecodeError, ValueError):
        pass
    # Fallback: treat as comma-separated table ids
    cleaned = text.replace("[", " ").replace("]", " ").replace("{", " ").replace("}", " ")
    cleaned = cleaned.replace('"', " ").replace("'", " ")
    toks = [t.strip() for t in cleaned.replace(",", " ").split() if t.strip()]
    return toks, []


def _is_pk_fk_column(col: dict[str, Any], table_id: str) -> bool:
    """Return True if the column is a PK / FK that must always survive filtering."""
    if col.get("_auto_injected") is True:
        return True
    role = (col.get("role") or "").lower()
    if role in PK_FK_ROLES:
        return True
    name = col.get("name") or ""
    # Standard FK names per SRS sample schema
    if name in {"customer_id", "product_id", "date_id", "region_id", "order_id"}:
        # Only treat as PK/FK for the appropriate table — otherwise any *_id
        # column (e.g. dim_region.region_name which has id "region_id" in some
        # legacy rows) would be mis-flagged.
        if name == "region_id" and table_id.startswith("dim_"):
            return True
        if name == "customer_id" and table_id.startswith("dim_"):
            return True
        if name == "product_id" and table_id.startswith("dim_"):
            return True
        if name == "date_id" and table_id.startswith("dim_"):
            return True
        if name == "order_id" and table_id == "fact_order":
            return True
        if name in {"customer_id", "product_id", "date_id", "region_id"} \
                and table_id == "fact_order":
            return True
    return False


async def filter_table(state: AgentState, config: RunnableConfig | None = None) -> dict:
    t0 = now_ms()
    runtime = get_runtime(config)
    request_id = state.get("request_id", "-")
    query = state.get("query", "")
    table_infos: dict[str, dict[str, Any]] = (
        state.get("table_infos")
        or state.get("merged_table_infos")
        or {}
    )

    # --- Step 1: invoke the LLM --------------------------------------------
    keep_tables: list[str] = []
    keep_cols: list[str] = []
    if runtime is not None and runtime.llm is not None:
        template = _load_prompt_template()
        prompt = template.format(
            query=query,
            table_infos=_serialize_table_infos(table_infos),
        )
        try:
            resp = await runtime.llm.ainvoke(prompt)
            keep_tables, keep_cols = _parse_keep_response(resp.text)
            if runtime.metrics is not None:
                runtime.metrics.record_llm_call(LLMCallStat(
                    node_name="filter_table",
                    model=str(getattr(runtime.llm, "model", "mock")),
                    prompt_tokens=len(prompt) // 2,
                    completion_tokens=len(resp.text) // 2,
                    total_tokens=(len(prompt) + len(resp.text)) // 2,
                    latency_ms=int(getattr(resp, "latency_ms", 0)),
                    cache_hit=False,
                ))
        except Exception:
            keep_tables, keep_cols = [], []

    # --- Step 2: apply the filter -----------------------------------------
    keep_table_set = set(keep_tables) if keep_tables else None  # None => keep all
    keep_col_set = set(keep_cols) if keep_cols else None

    filtered: dict[str, dict[str, Any]] = {}
    for tid, info in table_infos.items():
        # 2a. decide whether to keep this table
        if keep_table_set is not None and tid not in keep_table_set:
            continue
        # 2b. filter columns while preserving PK / FK fields
        kept_cols: list[dict[str, Any]] = []
        seen_col_ids: set[str] = set()
        for col in info.get("columns") or []:
            cid = col.get("id") or f"{tid}.{col.get('name','')}"
            # Always keep PK / FK fields per SRS 4.2.6 rule 4.
            if _is_pk_fk_column(col, tid):
                kept_cols.append(col)
                seen_col_ids.add(cid)
                continue
            # Otherwise honour the LLM\'s keep_column_ids list (or keep all
            # when the LLM gave us no column-level hints).
            if keep_col_set is None or cid in keep_col_set:
                kept_cols.append(col)
                seen_col_ids.add(cid)
        if not kept_cols:
            # Safety net: never drop a table entirely. Keep its first column.
            first = (info.get("columns") or [{}])[0]
            if first:
                kept_cols = [first]
        filtered[tid] = {**info, "columns": kept_cols}

    # --- Step 3: write outputs --------------------------------------------
    elapsed = now_ms() - t0
    if runtime is not None:
        runtime.metrics.record_node_latency("filter_table", elapsed)
        runtime.nodes_called += 1
    log_node(
        "filter_table", request_id, "ok",
        tables=len(filtered),
        kept_columns=sum(len(v["columns"]) for v in filtered.values()),
    )
    return {
        "filtered_table_infos": filtered,
        "node_history": history_append(
            state, "filter_table", "ok", elapsed,
            extra={
                "tables": len(filtered),
                "kept_columns": sum(len(v["columns"]) for v in filtered.values()),
                "keep_table_count": len(keep_tables),
                "keep_column_count": len(keep_cols),
            },
        ),
    }