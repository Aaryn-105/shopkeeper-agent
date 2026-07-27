"""LLM client abstraction with OpenAI-compatible backend and a deterministic mock.

When cfg.llm.api_key is empty we operate in mock mode: a tiny rule-based generator
returns plausible keyword expansions, table/metric filters, and SQL drafts. This
lets the workflow run end-to-end without an external API and keeps tests fast and
offline.
"""
from __future__ import annotations
import json
import re
from dataclasses import dataclass
from typing import Any, Optional

from app.core.config import cfg


@dataclass
class LLMResponse:
    text: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: int = 0
    cache_hit: bool = False


class LLMClient:
    """Thin wrapper around ChatOpenAI; falls back to a deterministic mock."""

    def __init__(self, model: Optional[str] = None,
                 api_base: Optional[str] = None,
                 api_key: Optional[str] = None,
                 temperature: Optional[float] = None,
                 max_tokens: Optional[int] = None) -> None:
        self.model = model or cfg.llm.model or "mock-llm"
        self.api_base = api_base or cfg.llm.api_base
        self.api_key = api_key or cfg.llm.api_key
        self.temperature = (
            float(temperature) if temperature is not None
            else float(cfg.llm.temperature)
        )
        self.max_tokens = (
            int(max_tokens) if max_tokens is not None
            else int(cfg.llm.max_tokens)
        )
        self._real = None
        if self.api_key:
            try:
                from langchain_openai import ChatOpenAI
                self._real = ChatOpenAI(
                    model=self.model,
                    api_key=self.api_key,
                    base_url=self.api_base or None,
                    temperature=self.temperature,
                    max_tokens=self.max_tokens,
                )
            except Exception:
                self._real = None

    @property
    def is_mock(self) -> bool:
        return self._real is None

    async def ainvoke(self, prompt: str, system: Optional[str] = None,
                      response_format: Optional[str] = None) -> LLMResponse:
        """Return an LLMResponse; routes to mock if no real backend configured."""
        if self._real is None:
            text = _mock_generate(prompt)
            return LLMResponse(
                text=text,
                prompt_tokens=len(prompt) // 2,
                completion_tokens=len(text) // 2,
                latency_ms=0,
                cache_hit=False,
            )
        # Real path: minimal - we only need the text back.
        from langchain_core.messages import SystemMessage, HumanMessage
        msgs = []
        if system:
            msgs.append(SystemMessage(content=system))
        msgs.append(HumanMessage(content=prompt))
        result = await self._real.ainvoke(msgs)
        text = getattr(result, "content", str(result))
        return LLMResponse(
            text=text,
            prompt_tokens=0,
            completion_tokens=len(text) // 2,
            latency_ms=0,
            cache_hit=False,
        )


# ---------- mock generator (deterministic, rule-based) ----------

# Tiny dictionary mapping common Chinese / English phrasings to logical fields.
_PHRASES = {
    # region
    "华北": ("dim_region", "region_name", ["R001", "R002", "R003"]),
    "华东": ("dim_region", "region_name", ["R004", "R005", "R006"]),
    "华南": ("dim_region", "region_name", ["R007", "R008", "R009"]),
    "华中": ("dim_region", "region_name", ["R010", "R011", "R012"]),
    # member level
    "钻石": ("dim_customer", "member_level", ["钻石会员"]),
    "金卡": ("dim_customer", "member_level", ["金卡会员"]),
    "银卡": ("dim_customer", "member_level", ["银卡会员"]),
    "普通": ("dim_customer", "member_level", ["普通会员"]),
    # product
    "手机": ("dim_product", "category", ["手机数码"]),
    "电脑": ("dim_product", "category", ["电脑办公"]),
    "家电": ("dim_product", "category", ["家用电器"]),
    "服饰": ("dim_product", "category", ["服饰鞋包"]),
    # metric
    "销售总额": ("GMV", "order_amount", ["SUM"]),
    "总销售额": ("GMV", "order_amount", ["SUM"]),
    "成交金额": ("GMV", "order_amount", ["SUM"]),
    "GMV": ("GMV", "order_amount", ["SUM"]),
    "订单数": ("ORDER_CNT", "order_id", ["COUNT"]),
    "订单量": ("ORDER_CNT", "order_id", ["COUNT"]),
    "客单价": ("AOV", "order_amount", ["AVG"]),
}


def _extract_keywords_from_prompt(prompt: str) -> list[str]:
    """For the extract_keywords prompt, the mock just returns the original query
    back as a single keyword."""
    m = re.search(r"用户问题[：:]\s*(.+)", prompt)
    if m:
        text = m.group(1).strip()
        # split on whitespace / commas
        return [t for t in re.split(r"[\s,，]+", text) if t]
    return []


def _mock_generate(prompt: str) -> str:
    """Best-effort mock that recognizes a few canonical prompt prefixes."""
    p = prompt.strip()
    head = p[:80]

    # 1) extract_keywords expansion
    if "关键词" in head and "扩展" in head:
        kws = _extract_keywords_from_prompt(prompt)
        return json.dumps({"keywords": kws}, ensure_ascii=False)

    # 2) filter_table / filter_metric - return IDs as-is
    if "filter_table" in p or "table_info" in head:
        m = re.search(r"table_ids?\s*[:：]\s*([^\n]+)", p)
        ids = re.findall(r"[a-z_]+", m.group(1)) if m else []
        return json.dumps({"keep_table_ids": ids}, ensure_ascii=False)
    if "filter_metric" in p or "metric_ids" in head:
        m = re.search(r"metric_ids?\s*[:：]\s*([^\n]+)", p)
        ids = re.findall(r"[A-Z_]+", m.group(1)) if m else []
        return json.dumps({"keep_metric_ids": ids}, ensure_ascii=False)

    # 3) generate_sql / correct_sql - build a simple SQL using detected phrases
    if "SQL" in head or "select" in head.lower() or "SELECT" in p:
        text = p
        table_id = "fact_order"
        joins: list[str] = []
        group_cols: list[str] = []
        measure_col = None
        measure_fn = "SUM"
        wheres: list[str] = []
        # detect region filter
        for phrase, (tbl, col, vals) in _PHRASES.items():
            if phrase in text and tbl == "dim_region":
                joins.append(f"JOIN dim_region r ON r.region_id = f.region_id")
                wheres.append(f"r.{col} IN ({', '.join(repr(v) for v in vals)})")
            if phrase in text and tbl == "dim_product":
                joins.append(f"JOIN dim_product p ON p.product_id = f.product_id")
                wheres.append(f"p.{col} IN ({', '.join(repr(v) for v in vals)})")
            if phrase in text and tbl == "dim_customer":
                joins.append(f"JOIN dim_customer c ON c.customer_id = f.customer_id")
                wheres.append(f"c.{col} IN ({', '.join(repr(v) for v in vals)})")
            if phrase in text and tbl == "GMV":
                measure_col = "f.order_amount"
                measure_fn = vals[0] if vals else "SUM"
            if phrase in text and tbl == "ORDER_CNT":
                measure_col = "f.order_id"
                measure_fn = vals[0] if vals else "COUNT"
            if phrase in text and tbl == "AOV":
                measure_col = "f.order_amount"
                measure_fn = vals[0] if vals else "AVG"

        if measure_col is None:
            # default: aggregate order_amount if any "金额" / "总额" in text
            if "金额" in text or "总额" in text or "多少" in text:
                measure_col = "f.order_amount"
                measure_fn = "SUM"

        # group-by hint: if any "各" / "按" / "by"
        if "各" in text or "按" in text or "GROUP" in text.upper():
            for kw, hint in (("地区", "r.region_name"), ("品类", "p.category"),
                             ("品牌", "p.brand"), ("会员", "c.member_level")):
                if kw in text:
                    group_cols.append(f"{hint} AS {kw}")

        select_expr = f"{measure_fn}({measure_col}) AS value" if measure_col else "COUNT(*) AS value"
        select_cols = ([select_expr] if measure_col else ["COUNT(*) AS cnt"])
        select_cols.extend(group_cols)

        sql_parts = [f"SELECT {', '.join(select_cols)}"]
        sql_parts.append(f"FROM {table_id} f")
        for j in joins:
            sql_parts.append(j)
        if wheres:
            sql_parts.append("WHERE " + " AND ".join(wheres))
        if group_cols:
            sql_parts.append("GROUP BY " + ", ".join(g.split(' AS ')[0] for g in group_cols))
        sql_parts.append("LIMIT 100")
        return "\n".join(sql_parts)

    # 4) fallback: return the prompt head so the caller can see something useful
    return f"[mock-llm] {head}"