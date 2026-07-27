"""Node: recall_column (4.2.2 / V1.0 phase 6.2).

V1.0 phase 6.2 spec:
  - [\u65b0\u589e] \u5148\u8c03 LLM\uff08extend_keywords_for_column_recall.prompt\uff09\u6269\u5c55\u5173\u952e\u8bcd\uff08\u9650 \u22646 \u4e2a\uff09
  - \u5408\u5e76 state.keywords
  - embedding_local.encode([query, *extended_keywords]) -> \u5bf9\u6bcf\u6761\u5411\u91cf
    faiss_client.search_column(vec, top_k=20)
  - \u6309 score \u6392\u5e8f\u53bb\u91cd\uff0c\u6700\u7ec8\u4fdd\u7559 \u226420
  - \u5199 state.retrieved_columns\uff08\u5b57\u6bb5\u540d\u9075\u5f02 AgentState\uff09

Note: prior implementation called FAISS.text_recall directly without LLM
extension. V1.0 phase 6.2 introduces a keyword-expansion step before the
vector search so the recall sees richer semantic context.
"""
from __future__ import annotations
import json
from pathlib import Path
from typing import Any

from langchain_core.runnables import RunnableConfig

from app.agent.nodes._helpers import get_runtime, history_append, log_node, now_ms
from app.agent.state import AgentState
from app.core.config import cfg
from app.core.metrics import LLMCallStat


# V1.0 phase 6.2: extend_keywords_for_column_recall returns \u22646 keywords
MAX_EXTENDED_KEYWORDS: int = 6
# V1.0 phase 6.2: top_k=20 per vector; final cap = 20
TOPK_PER_VECTOR: int = 20
FINAL_CAP: int = 20

_PROMPT_PATH = Path(__file__).resolve().parents[2] / "prompt" / "extend_keywords_for_column_recall.prompt"
_FALLBACK_PROMPT = (
    "\u6269\u5c55\u6700\u591a 6 \u4e2a\u7528\u4e8e\u6570\u636e\u5e93\u5b57\u6bb5\u53ec\u56de\u7684\u5173\u952e\u8bcd\u3002\n"
    "\u7528\u6237\u95ee\u9898\uff1a{query}\n"
    "\u8fd4\u56de JSON \u5bf9\u8c61\uff1a"
)


def _load_prompt_template() -> str:
    """Load the keyword-extension prompt. Falls back to inline if the file is gone."""
    try:
        if _PROMPT_PATH.exists():
            return _PROMPT_PATH.read_text(encoding="utf-8")
    except Exception:
        pass
    return _FALLBACK_PROMPT


def _parse_extended_keywords(text: str) -> list[str]:
    """Parse the LLM response into a clean list[str] of keywords.

    Accepted shapes (in priority order):
      {"keywords": [...]}     -- canonical, recognised by the mock generator
      ["kw1", "kw2"]         -- bare JSON array
      "kw1, kw2, kw3"        -- plain comma-separated
      "kw1 kw2 kw3"          -- whitespace-separated
    """
    if not text:
        return []
    text = text.strip()
    # 1. JSON object or array
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            for key in ("keywords", "extended", "words", "expanded"):
                v = obj.get(key)
                if isinstance(v, list):
                    return [str(k).strip() for k in v if k]
        if isinstance(obj, list):
            return [str(k).strip() for k in obj if k]
    except (json.JSONDecodeError, ValueError):
        pass
    # 2. Strip JSON-ish brackets and quotes, then split on commas / whitespace
    cleaned = text.replace("[", " ").replace("]", " ").replace("{", " ").replace("}", " ")
    cleaned = cleaned.replace('"', " ").replace("'", " ")
    return [t.strip() for t in cleaned.replace(",", " ").split() if t.strip()]


async def _extend_keywords_via_llm(runtime, query: str) -> list[str]:
    """Step 1 of V1.0 6.2: ask the LLM to expand keywords for column recall.

    Returns at most MAX_EXTENDED_KEYWORDS strings, or [] when the LLM is
    unavailable / fails. Records an LLMCallStat so the call shows up in /api/stats.
    """
    if runtime is None or runtime.llm is None:
        return []
    template = _load_prompt_template()
    prompt = template.format(query=query, max_k=MAX_EXTENDED_KEYWORDS)
    try:
        resp = await runtime.llm.ainvoke(prompt)
        kws = _parse_extended_keywords(resp.text)[:MAX_EXTENDED_KEYWORDS]
        if runtime.metrics is not None:
            runtime.metrics.record_llm_call(LLMCallStat(
                node_name="recall_column_extend",
                model=str(getattr(runtime.llm, "model", "mock")),
                prompt_tokens=len(prompt) // 2,
                completion_tokens=len(resp.text) // 2,
                total_tokens=(len(prompt) + len(resp.text)) // 2,
                latency_ms=int(getattr(resp, "latency_ms", 0)),
                cache_hit=False,
            ))
        return kws
    except Exception:
        return []


def _search_one_vector(runtime, vec: list[float], top_k: int) -> list[dict[str, Any]]:
    """Run a single FAISS .search(); returns [] on any failure / no index."""
    if runtime is None or runtime.faiss is None:
        return []
    coll = getattr(runtime.faiss, "column_info", None)
    if coll is None:
        return []
    try:
        if coll.is_indexed:
            hits = coll.search(vec, top_k)
            if hits:
                return hits
    except Exception:
        return []
    return []


async def recall_column(state: AgentState, config: RunnableConfig | None = None) -> dict:
    t0 = now_ms()
    runtime = get_runtime(config)
    request_id = state.get("request_id", "-")
    query = state.get("query", "")
    state_keywords = state.get("keywords") or []

    # --- Step 1: LLM-extend keywords (\u22646) ---------------------------------
    extended = await _extend_keywords_via_llm(runtime, query)
    extended = extended[:MAX_EXTENDED_KEYWORDS]

    # --- Step 2: build [query, *extended, *state_keywords] dedup'd -------------
    # V1.0 6.2 says encode [query, *extended_keywords]; we additionally merge
    # state.keywords (the spec uses the word "\u5408\u5e76") before deduping.
    raw_texts: list[str] = [query, *extended, *state_keywords]
    seen: set[str] = set()
    texts: list[str] = []
    for t in raw_texts:
        if t and t not in seen:
            seen.add(t)
            texts.append(t)

    # --- Step 3: encode + per-vector FAISS search (top_k=20) -------------------
    hits: list[dict[str, Any]] = []
    vectors: list[list[float]] = []
    if runtime is not None and runtime.embedding is not None and texts:
        try:
            vectors = runtime.embedding.encode(texts)
        except Exception:
            vectors = []
    for vec in vectors:
        hits.extend(_search_one_vector(runtime, vec, top_k=TOPK_PER_VECTOR))

    # --- Step 4: score desc, dedupe by id, cap at FINAL_CAP --------------------
    hits.sort(key=lambda h: float(h.get("_score", 0.0)), reverse=True)
    seen_ids: set[Any] = set()
    out: list[dict[str, Any]] = []
    for h in hits:
        hid = h.get("id")
        if hid is None or hid in seen_ids:
            continue
        seen_ids.add(hid)
        out.append(h)
        if len(out) >= FINAL_CAP:
            break

    if runtime is not None:
        runtime.metrics.record_node_latency("recall_column", now_ms() - t0)
        runtime.nodes_called += 1
    log_node(
        "recall_column", request_id, "ok",
        hits=len(out), extended=len(extended), texts=len(texts),
    )
    return {
        "retrieved_columns": out,
        "node_history": history_append(
            state, "recall_column", "ok", now_ms() - t0,
            extra={"hits": len(out), "extended": len(extended), "texts": len(texts)},
        ),
    }