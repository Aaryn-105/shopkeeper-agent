"""Node: generate_sql (4.2.9 / V1.0 phase 6.9).

V1.0 phase 6.9 spec:
  - cache_key = sha256(f"{query}|{fingerprint(filtered_table_infos + filtered_metric_infos)}")
  - On cache hit  -> state.sql = cached.sql_text, state.cache_hit_sql = True
  - On cache miss -> LLM call, parse JSON / fallback to SQL text, store in cache
  - stream_writer({"type":"sql_generated","sql": state.sql,"request_id": state.request_id})
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from langchain_core.runnables import RunnableConfig

from app.agent.nodes._helpers import get_runtime, history_append, log_node, now_ms
from app.agent.state import AgentState
from app.core.metrics import LLMCallStat

_PROMPT_PATH = Path(__file__).resolve().parents[2] / "prompt" / "generate_sql.prompt"
_FALLBACK_PROMPT = (
    "Generate MySQL SQL for: {query}\n"
    "Table info: {filtered_table_infos}\n"
    "Metric info: {filtered_metric_infos}\n"
    "Current time: {current_time}\n"
)


# V1.0 phase 6.9 cache TTL
SQL_CACHE_TTL_SECONDS: int = 3600


def _load_prompt_template() -> str:
    try:
        if _PROMPT_PATH.exists():
            return _PROMPT_PATH.read_text(encoding="utf-8")
    except Exception:
        pass
    return _FALLBACK_PROMPT


def fingerprint_table_infos(table_infos: dict[str, Any]) -> str:
    """Stable, short signature of the filtered_table_infos dict.

    Used as the cache-key salt so the cache only reuses an SQL when the
    underlying table / column set is the same.
    """
    if not table_infos:
        return ""
    parts: list[str] = []
    for tid in sorted(table_infos.keys()):
        info = table_infos[tid] or {}
        cols = info.get("columns") or []
        col_ids = sorted({c.get("id") or c.get("name") or "" for c in cols})
        parts.append(f"{tid}|{','.join(col_ids)}")
    return ";".join(parts)


def fingerprint_metric_infos(metric_infos: list[Any]) -> str:
    if not metric_infos:
        return ""
    mids = sorted({m.get("id") or m.get("name") or "" for m in metric_infos})
    return ",".join(mids)


def make_cache_key(
    query: str, table_infos: dict[str, Any], metric_infos: list[Any]
) -> str:
    """V1.0 phase 6.9: sha256(f"{query}|{fp(table)+fp(metric)}")."""
    salt = (
        fingerprint_table_infos(table_infos)
        + "|"
        + fingerprint_metric_infos(metric_infos)
    )
    raw = f"{query.strip()}|{salt}".encode()
    return hashlib.sha256(raw).hexdigest()


def parse_sql_response(text: str) -> str:
    """Parse LLM output into a SQL string.

    Accepted shapes:
      {"sql": "..."}                           -- canonical JSON
      {"sql_text": "..."}                      -- alias
      ```sql ... ``` code-fenced markdown
      plain text starting with SELECT/WITH/EXPLAIN/SHOW/INSERT/UPDATE/DELETE
      plain text containing a SQL keyword
    Returns "" when nothing parseable.
    """
    if not text:
        return ""
    text = text.strip()

    # 1. JSON object form
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            for k in ("sql", "sql_text", "query"):
                v = obj.get(k)
                if isinstance(v, str) and v.strip():
                    return v.strip()
        if isinstance(obj, list) and obj:
            first = obj[0]
            if isinstance(first, dict):
                v = first.get("sql") or first.get("sql_text")
                if isinstance(v, str):
                    return v.strip()
    except (json.JSONDecodeError, ValueError):
        pass

    # 2. Markdown fenced code block
    if text.startswith("```"):
        lines = text.splitlines()
        body = [ln for ln in lines[1:] if not ln.startswith("```")]
        if body:
            return "\n".join(body).strip()

    # 3. Plain SQL (detect by leading keyword)
    upper = text.upper()
    if any(
        upper.startswith(kw)
        for kw in (
            "SELECT",
            "WITH",
            "EXPLAIN",
            "SHOW",
            "INSERT",
            "UPDATE",
            "DELETE",
            "CREATE",
        )
    ):
        return text

    # 4. Heuristic: a SELECT/EXPLAIN line anywhere in the text. Require
    #    start-of-line (not just whitespace) so phrases like "garbage with no
    #    sql" do not accidentally match.
    import re

    for ln in text.splitlines():
        stripped = ln.strip()
        if stripped and re.match(r"^(SELECT|WITH|EXPLAIN)\b", stripped.upper()):
            return stripped
    return ""


def _stream_writer(runtime, event: dict[str, Any]) -> None:
    """Push an event into the runtime\'s pending_events queue (best-effort).

    The SSE route layer drains this queue to emit events. When no queue is
    available (unit tests with bare-stub runtime), this is a no-op so direct
    node calls don\'t crash.
    """
    if runtime is None:
        return
    pending = getattr(runtime, "pending_events", None)
    if isinstance(pending, list):
        pending.append(event)


async def generate_sql(state: AgentState, config: RunnableConfig | None = None) -> dict:
    t0 = now_ms()
    runtime = get_runtime(config)
    request_id = state.get("request_id", "-")
    query = state.get("query", "")
    table_infos = (
        state.get("filtered_table_infos") or state.get("merged_table_infos") or {}
    )
    metric_infos = state.get("filtered_metric_infos") or []
    extra = state.get("extra_context") or {}

    cache_key = make_cache_key(query, table_infos, metric_infos)

    # --- Step 1: cache lookup ------------------------------------------------
    cache_hit_sql = False
    sql_text = ""
    cache_store = getattr(runtime, "cache", None) if runtime is not None else None
    if cache_store is not None and hasattr(cache_store, "get_exact"):
        try:
            cached = cache_store.get_exact(cache_key)
        except Exception:
            cached = None
        if isinstance(cached, dict) and cached.get("sql_text"):
            sql_text = str(cached["sql_text"]).strip()
            cache_hit_sql = True

    # --- Step 2: LLM call on cache miss ------------------------------------
    if not cache_hit_sql:
        if runtime is not None and runtime.llm is not None:
            template = _load_prompt_template()
            prompt = template.format(
                query=query,
                current_time=extra.get("current_time", ""),
                db_type=extra.get("db_type", ""),
                db_version=extra.get("db_version", ""),
                filtered_table_infos=json.dumps(
                    table_infos, ensure_ascii=False, indent=2
                ),
                filtered_metric_infos=json.dumps(
                    metric_infos, ensure_ascii=False, indent=2
                ),
                retrieved_values=json.dumps(
                    state.get("retrieved_values") or [],
                    ensure_ascii=False,
                    indent=2,
                ),
            )
            try:
                resp = await runtime.llm.ainvoke(prompt)
                sql_text = parse_sql_response(resp.text).strip()
                if runtime.metrics is not None:
                    runtime.metrics.record_llm_call(
                        LLMCallStat(
                            node_name="generate_sql",
                            model=str(getattr(runtime.llm, "model", "mock")),
                            prompt_tokens=len(prompt) // 2,
                            completion_tokens=len(resp.text) // 2,
                            total_tokens=(len(prompt) + len(resp.text)) // 2,
                            latency_ms=int(getattr(resp, "latency_ms", 0)),
                            cache_hit=False,
                        )
                    )
            except Exception as exc:
                log_node(
                    "generate_sql",
                    request_id,
                    "llm_error",
                    error=f"{type(exc).__name__}: {str(exc)[:160]}",
                )
                if not getattr(runtime.llm, "is_mock", True):
                    raise RuntimeError("LLM SQL generation failed") from exc
                sql_text = ""

        if (
            not sql_text
            and runtime is not None
            and runtime.llm is not None
            and not getattr(runtime.llm, "is_mock", True)
        ):
            raise RuntimeError("LLM response did not contain valid SQL")

        if not sql_text:
            # Safety net: the original node guaranteed a non-empty SQL. Keep
            # that behaviour so validate_sql has something to work on.
            sql_text = "SELECT COUNT(*) FROM fact_order"

        # --- Step 3: store in cache (TTL=3600) -----------------------------
        if cache_store is not None and hasattr(cache_store, "put"):
            try:
                # Respect the QueryCache TTL by setting ttl_seconds if we can.
                if hasattr(cache_store, "_ttl") and SQL_CACHE_TTL_SECONDS:
                    try:
                        cache_store._ttl = SQL_CACHE_TTL_SECONDS
                    except Exception:
                        pass
                cache_store.put(
                    cache_key,
                    {
                        "sql_text": sql_text,
                        "query": query,
                        "stored_at": now_ms(),
                    },
                )
            except Exception:
                pass

    # --- Step 4: metrics + stream event ------------------------------------
    elapsed = now_ms() - t0
    if runtime is not None:
        runtime.metrics.record_node_latency("generate_sql", elapsed)
        if not cache_hit_sql:
            runtime.metrics.record_sql_generated()
        runtime.nodes_called += 1

    _stream_writer(
        runtime,
        {
            "type": "sql_generated",
            "sql": sql_text,
            "request_id": request_id,
            "cache_hit": cache_hit_sql,
            "cache_key": cache_key,
        },
    )

    log_node(
        "generate_sql",
        request_id,
        "cache_hit" if cache_hit_sql else "ok",
        sql_len=len(sql_text),
        cache_hit=cache_hit_sql,
    )

    return {
        "sql": sql_text,
        "sql_corrected": False,
        "cache_hit_sql": cache_hit_sql,
        # Keep the legacy cache_hit flag in sync so existing consumers still work.
        "cache_hit": cache_hit_sql,
        "pending_stream_events": [
            {
                "type": "sql_generated",
                "sql": sql_text,
                "request_id": request_id,
                "cache_hit": cache_hit_sql,
            }
        ],
        "node_history": history_append(
            state,
            "generate_sql",
            "cache_hit" if cache_hit_sql else "ok",
            elapsed,
            extra={
                "sql_len": len(sql_text),
                "cache_hit": cache_hit_sql,
                "cache_key": cache_key[:12],
            },
        ),
    }
