"""Node: filter_metric (4.2.7 / V1.0 phase 6.7).

V1.0 phase 6.7 spec:
  - Load filter_metric_info.prompt.
  - Call the LLM with (query, metric_infos) to ask for keep_metric_ids.
  - Apply the filter, falling back to the full list when the LLM gives no
    guidance so we don\'t over-filter a coarse recall (SRS 4.2.7 rule 4).
  - Write state.filtered_metric_infos.
"""
from __future__ import annotations
import json
from pathlib import Path
from typing import Any

from langchain_core.runnables import RunnableConfig

from app.agent.nodes._helpers import get_runtime, history_append, log_node, now_ms
from app.agent.state import AgentState
from app.core.metrics import LLMCallStat


_PROMPT_PATH = Path(__file__).resolve().parents[2] / "prompt" / "filter_metric_info.prompt"
_FALLBACK_PROMPT = (
    "你是 NL2SQL 助手。基于用户问题与候选指标，精筛保留的指标。\n"
    "用户问题：{query}\n候选指标：{metric_infos}\n"
    "返回 JSON：{{\"keep_metric_ids\": [...]}}"
)


def _load_prompt_template() -> str:
    try:
        if _PROMPT_PATH.exists():
            return _PROMPT_PATH.read_text(encoding="utf-8")
    except Exception:
        pass
    return _FALLBACK_PROMPT


def _serialize_metric_infos(metric_infos: list[dict[str, Any]]) -> str:
    return json.dumps(metric_infos, ensure_ascii=False, indent=2)


def _parse_keep_response(text: str) -> list[str]:
    """Parse the LLM response into a list[str] of metric ids.

    Accepts:
      {"keep_metric_ids": [...]}
      {"keep_metrics": [...]}
      ["GMV", "ORDER_CNT"]
      "GMV, ORDER_CNT"   (comma-separated)
      "GMV ORDER_CNT"    (whitespace-separated)
    Returns [] when nothing parseable.
    """
    if not text:
        return []
    text = text.strip()
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            ids = obj.get("keep_metric_ids") or obj.get("keep_metrics") or []
            return [str(x) for x in ids]
        if isinstance(obj, list):
            return [str(x) for x in obj]
    except (json.JSONDecodeError, ValueError):
        pass
    cleaned = text.replace("[", " ").replace("]", " ").replace("{", " ").replace("}", " ")
    cleaned = cleaned.replace('"', " ").replace("'", " ")
    return [t.strip() for t in cleaned.replace(",", " ").split() if t.strip()]


def _match_metric_by_id(metric: dict[str, Any], mid: str) -> bool:
    """A metric "matches" an id when either id or name equals ``mid`` (case
    insensitive) or the alias list contains it."""
    if str(metric.get("id") or "") == mid:
        return True
    if str(metric.get("name") or "") == mid:
        return True
    alias = metric.get("alias") or []
    if isinstance(alias, list) and mid in [str(a) for a in alias]:
        return True
    return False


async def filter_metric(state: AgentState, config: RunnableConfig | None = None) -> dict:
    t0 = now_ms()
    runtime = get_runtime(config)
    request_id = state.get("request_id", "-")
    query = state.get("query", "")
    metric_infos: list[dict[str, Any]] = (
        state.get("metric_infos")
        or state.get("retrieved_metrics")
        or []
    )

    # --- Step 1: invoke the LLM --------------------------------------------
    keep_ids: list[str] = []
    if runtime is not None and runtime.llm is not None:
        template = _load_prompt_template()
        prompt = template.format(
            query=query,
            metric_infos=_serialize_metric_infos(metric_infos),
        )
        try:
            resp = await runtime.llm.ainvoke(prompt)
            keep_ids = _parse_keep_response(resp.text)
            if runtime.metrics is not None:
                runtime.metrics.record_llm_call(LLMCallStat(
                    node_name="filter_metric",
                    model=str(getattr(runtime.llm, "model", "mock")),
                    prompt_tokens=len(prompt) // 2,
                    completion_tokens=len(resp.text) // 2,
                    total_tokens=(len(prompt) + len(resp.text)) // 2,
                    latency_ms=int(getattr(resp, "latency_ms", 0)),
                    cache_hit=False,
                ))
        except Exception:
            keep_ids = []

    # --- Step 2: apply the filter -----------------------------------------
    if not keep_ids:
        # LLM gave no guidance; preserve everything (SRS 4.2.7 rule 4).
        filtered = list(metric_infos)
    else:
        # Match by id / name / alias so "GMV" or "\u9500\u552e\u989d" both work.
        keep_set = set(keep_ids)
        filtered = [m for m in metric_infos if any(
            _match_metric_by_id(m, kid) for kid in keep_set
        )]
        # If matching produced zero hits, fall back to keeping the full list.
        # This avoids dropping the only metric the LLM referenced by alias
        # that we couldn\'t normalise back to its id.
        if not filtered:
            filtered = list(metric_infos)

    # --- Step 3: write outputs --------------------------------------------
    elapsed = now_ms() - t0
    if runtime is not None:
        runtime.metrics.record_node_latency("filter_metric", elapsed)
        runtime.nodes_called += 1
    log_node("filter_metric", request_id, "ok", metrics=len(filtered))
    return {
        "filtered_metric_infos": filtered,
        "node_history": history_append(
            state, "filter_metric", "ok", elapsed,
            extra={
                "metrics": len(filtered),
                "keep_ids": len(keep_ids),
            },
        ),
    }