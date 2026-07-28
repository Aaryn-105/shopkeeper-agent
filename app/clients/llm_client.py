"""LLM client abstraction with OpenAI-compatible backend and a deterministic mock.

When cfg.llm.api_key is empty we operate in mock mode: a tiny rule-based
generator returns plausible keyword expansions, table/metric filters, and SQL
drafts. Phase 9 upgrade: the mock extracts the user's actual query from the
prompt before scanning for phrase matches, so dictionary phrases like
"\u534e\u4e1c" only fire when the user actually asked about "\u534e\u4e1c"
rather than when an alias appears inside the filtered_table_infos JSON dump.
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
        if self._real is None:
            text = _mock_generate(prompt)
            return LLMResponse(
                text=text,
                prompt_tokens=len(prompt) // 2,
                completion_tokens=len(text) // 2,
                latency_ms=0,
                cache_hit=False,
            )
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

# Each entry maps a surface form -> (table, column, [values]).
# table doubles as dimension key and metric flag (GMV/ORDER_CNT/AOV).
_PHRASES: dict[str, tuple[str, str, list[str]]] = {
    # ---- region. Values are PROVINCE names because dim_region.province
    # holds the province (\u4e0a\u6d77) and dim_region.region_name holds the
    # da-qu (\u534e\u4e1c). Questions like "\u534e\u4e1c GMV" map to all
    # provinces in the \u534e\u4e1c da-qu.
    "华东": ("dim_region", "province", ["\u4e0a\u6d77", "\u6c5f\u82cf", "\u6d59\u6c5f"]),
    "华北": ("dim_region", "province", ["\u5317\u4eac", "\u5929\u6d25", "\u6cb3\u5317"]),
    "华南": ("dim_region", "province", ["\u5e7f\u4e1c", "\u5e7f\u897f", "\u6d77\u5357"]),
    "华中": ("dim_region", "province", ["\u6e56\u5317", "\u6e56\u5357", "\u6cb3\u5357"]),
    "西南": ("dim_region", "province", ["\u56db\u5ddd", "\u91cd\u5e86", "\u4e91\u5357"]),
    "西北": ("dim_region", "province", ["\u9655\u897f", "\u7518\u8083", "\u65b0\u7586"]),
    "东北": ("dim_region", "province", ["\u8fbd\u5b81", "\u5409\u6797", "\u9ed1\u9f99\u6c5f"]),

    # ---- member_level (real values: \u666e\u901a\u4f1a\u5458 / \u94f6\u5361\u4f1a\u5458 /
    # \u9ec4\u91d1\u4f1a\u5458 / \u94b6\u77f3\u4f1a\u5458)
    "钻石": ("dim_customer", "member_level", ["\u94b6\u77f3\u4f1a\u5458"]),
    "黄金": ("dim_customer", "member_level", ["\u9ec4\u91d1\u4f1a\u5458"]),
    "铂金": ("dim_customer", "member_level", ["\u94c2\u91d1\u4f1a\u5458"]),
    "银卡": ("dim_customer", "member_level", ["\u94f6\u5361\u4f1a\u5458"]),
    "白银": ("dim_customer", "member_level", ["\u94f6\u5361\u4f1a\u5458"]),
    "普通": ("dim_customer", "member_level", ["\u666e\u901a\u4f1a\u5458"]),

    # ---- product category (\u5bb6\u7528\u7535\u5668 / \u624b\u673a\u6570\u7801 / \u670d\u9970\u978b\u5305 / \u7535\u8111\u529e\u516c)
    "手机": ("dim_product", "category", ["\u624b\u673a\u6570\u7801"]),
    "电脑": ("dim_product", "category", ["\u7535\u8111\u529e\u516c"]),
    "家电": ("dim_product", "category", ["\u5bb6\u7528\u7535\u5668"]),
    "家用电器": ("dim_product", "category", ["\u5bb6\u7528\u7535\u5668"]),
    "手机数码": ("dim_product", "category", ["\u624b\u673a\u6570\u7801"]),
    "电脑办公": ("dim_product", "category", ["\u7535\u8111\u529e\u516c"]),
    "服饰鞋包": ("dim_product", "category", ["\u670d\u9970\u978b\u5305"]),
    "服饰": ("dim_product", "category", ["\u670d\u9970\u978b\u5305"]),
    "鞋包": ("dim_product", "category", ["\u670d\u9970\u978b\u5305"]),

    # ---- product brand (\u534e\u4e3a / \u5c0f\u7c73 / \u8054\u60f3 / \u4e09\u661f / \u82f9\u679c)
    "华为": ("dim_product", "brand", ["\u534e\u4e3a"]),
    "小米": ("dim_product", "brand", ["\u5c0f\u7c73"]),
    "联想": ("dim_product", "brand", ["\u8054\u60f3"]),
    "三星": ("dim_product", "brand", ["\u4e09\u661f"]),
    "苹果": ("dim_product", "brand", ["\u82f9\u679c"]),
    "苹果手机": ("dim_product", "brand", ["\u82f9\u679c"]),

    # ---- gender (M / F)
    "男": ("dim_customer", "gender", ["M"]),
    "女": ("dim_customer", "gender", ["F"]),
    "男性": ("dim_customer", "gender", ["M"]),
    "女性": ("dim_customer", "gender", ["F"]),

    # ---- metric hints
    "GMV": ("GMV", "order_amount", ["SUM"]),
    "AOV": ("AOV", "order_amount", ["AVG"]),
    "销售额": ("GMV", "order_amount", ["SUM"]),
    "总销售额": ("GMV", "order_amount", ["SUM"]),
    "成交金额": ("GMV", "order_amount", ["SUM"]),
    "订单数": ("ORDER_CNT", "order_id", ["COUNT"]),
    "客单价": ("AOV", "order_amount", ["AVG"]),
    "总营收": ("GMV", "order_amount", ["SUM"]),
    "总销售": ("GMV", "order_amount", ["SUM"]),
    "总数量": ("QTY", "order_quantity", ["SUM"]),
    "总下单数量": ("QTY", "order_quantity", ["SUM"]),
    "数量": ("QTY", "order_quantity", ["SUM"]),
    "销量": ("QTY", "order_quantity", ["SUM"]),
    "\u591a少订单": ("ORDER_CNT", "order_id", ["COUNT"]),
    "\u603b订单": ("ORDER_CNT", "order_id", ["COUNT"]),
    "\u8ba2单总量": ("ORDER_CNT", "order_id", ["COUNT"]),
    "\u9500售总额": ("GMV", "order_amount", ["SUM"]),
    "\u603b额": ("GMV", "order_amount", ["SUM"]),
    "均价": ("AOV", "order_amount", ["AVG"]),
    "下单数": ("ORDER_CNT", "order_id", ["COUNT"]),
    "下单量": ("QTY", "order_quantity", ["SUM"]),
    "平均每笔": ("QTY", "order_quantity", ["AVG"]),
    "平均单笔": ("AOV", "order_amount", ["AVG"]),
    "平均数量": ("QTY", "order_quantity", ["AVG"]),
}

# Time-window phrases: each maps to a WHERE-clause fragment against dim_date.
_TIME_PHRASES: list[tuple[list[str], str]] = [
    (["\u4e0a\u6708", "\u4e0a\u4e2a\u6708", "\u4e0a\u4e00\u4e2a\u6708", "\u4e0a\u4e2a\u6708\u4efd"],
     "MONTH(d.date_id_str) = MONTH(CURDATE() - INTERVAL 1 MONTH) AND YEAR(d.date_id_str) = YEAR(CURDATE() - INTERVAL 1 MONTH)"),
    (["\u4e0a\u5b63\u5ea6", "\u4e0a\u4e00\u5b63\u5ea6", "\u4e0a\u4e2a\u5b63\u5ea6"],
     "d.quarter = CONCAT('Q', QUARTER(CURDATE() - INTERVAL 3 MONTH))"),
    (["\u672c\u5e74", "\u4eca\u5e74"],
     "YEAR(d.date_id_str) = YEAR(CURDATE())"),
    (["\u53bb\u5e74", "\u4e0a\u5e74"],
     "YEAR(d.date_id_str) = YEAR(CURDATE()) - 1"),
    (["\u6700\u8fd130\u5929", "\u8fd130\u5929", "\u4ec530\u5929", "\u8fc7\u53bb30\u5929"],
     "d.date_id_str >= CURDATE() - INTERVAL 30 DAY"),
    (["\u6700\u8fd17\u5929", "\u8fd17\u5929", "\u4ec57\u5929"],
     "d.date_id_str >= CURDATE() - INTERVAL 7 DAY"),
    (["\u7b2c\u4e00\u5b63\u5ea6"],
     "d.quarter = 'Q1'"),
    (["\u7b2c\u4e8c\u5b63\u5ea6"],
     "d.quarter = 'Q2'"),
    (["\u7b2c\u4e09\u5b63\u5ea6"],
     "d.quarter = 'Q3'"),
    (["\u7b2c\u56db\u5b63\u5ea6"],
     "d.quarter = 'Q4'"),
    (["Q1"],
     "d.quarter = 'Q1'"),
    (["Q2"],
     "d.quarter = 'Q2'"),
    (["Q3"],
     "d.quarter = 'Q3'"),
    (["Q4"],
     "d.quarter = 'Q4'"),
]


# ---------- prompt introspection ----------

def _extract_user_query(prompt: str) -> str:
    """Pull the user question out of a generate_sql / correct_sql prompt.

    The prompt template repeats the question in the input section and again
    at the bottom. We grab the LAST non-empty line which is the second
    copy, avoiding the JSON dump of filtered_table_infos above it.
    """
    lines = [ln.strip() for ln in prompt.splitlines() if ln.strip()]
    if not lines:
        return ""
    last = lines[-1]
    last = re.sub(r"^[#\s]*\u7528\u6237\u95ee\u9898[\u3001:\uff1a]\s*", "", last)
    return last.strip()


def _extract_keywords_from_prompt(prompt: str) -> list[str]:
    q = _extract_user_query(prompt)
    if not q:
        return []
    return [t for t in re.split(r"[\s,\u3002\uff0c\uff1b\uff1a\u3001]+", q) if t]


# ---------- SQL helpers ----------

_METRIC_PHRASES = {"GMV", "ORDER_CNT", "AOV", "QTY"}


def _detect_measure(query: str) -> tuple[Optional[str], str, str]:
    """Return (metric, fn, measure_col) for the user question."""
    # Sort by phrase length DESC so longer/specific phrases (\u5e73\u5747\u6bcf\u7b14) win over
    # shorter generic ones (\u6570\u91cf / \u4e0b\u5355\u6570).
    candidates = [
        (phrase, tbl, col, vals)
        for phrase, (tbl, col, vals) in _PHRASES.items()
        if tbl in _METRIC_PHRASES and phrase in query
    ]
    candidates.sort(key=lambda x: len(x[0]), reverse=True)
    if candidates:
        phrase, tbl, col, vals = candidates[0]
        return tbl, (vals[0] if vals else "SUM"), f"f.{col}"
    return None, "SUM", "f.order_amount"


def _detect_filters(query: str) -> tuple[list[str], list[str]]:
    """Return (joins, wheres) from phrase matching against the user query."""
    joins: list[str] = []
    wheres: list[str] = []
    seen: set[str] = set()
    for phrase, (tbl, col, vals) in _PHRASES.items():
        if tbl in _METRIC_PHRASES:
            continue
        if tbl in seen:
            continue
        if phrase in query:
            seen.add(tbl)
            items = ", ".join(repr(v) for v in vals)
            if tbl == "dim_region":
                joins.append("JOIN dim_region r ON r.region_id = f.region_id")
                wheres.append(f"r.{col} IN ({items})")
            elif tbl == "dim_product":
                joins.append("JOIN dim_product p ON p.product_id = f.product_id")
                wheres.append(f"p.{col} IN ({items})")
            elif tbl == "dim_customer":
                joins.append("JOIN dim_customer c ON c.customer_id = f.customer_id")
                wheres.append(f"c.{col} IN ({items})")
    return joins, wheres


def _detect_time_filter(query: str) -> Optional[str]:
    # Year detection: explicit 4-digit year
    m = re.search(r"(\d{4})\s*\u5e74", query)
    if m:
        return f"d.year = {m.group(1)}"
    for phrases, fragment in _TIME_PHRASES:
        for ph in phrases:
            if ph in query:
                return fragment
    return None


def _detect_group_by(query: str):
    """Return list of (alias, sql_expression) for GROUP BY columns."""
    out = []
    # Triggers: explicit aggregation / partition keywords
    triggers = ["\u6309", "GROUP", "group", "\u5404", "\u5206\u7ec4", "\u5404\u4e2a", "\u6bd4\u4f8b", "\u5360\u6bd4", "\u5404\u4e2a\u6708", "\u6708\u4efd", "\u5b63\u5ea6", "\u5e74\u4efd", "\u6bcf\u4e2a\u6708", "\u6bcf\u6708", "\u6bcf\u5b63\u5ea6", "\u6bcf\u5e74", "\u6bcf", "\u5404\u54c1\u7c7b", "\u5404\u5927\u533a", "\u8d8b\u52bf", "\u5bf9\u6bd4", "\u6027\u522b", "\u591a\u5c11", "\u603b\u91cf", "\u9500\u91cf", "\u6708", "\u6700\u9ad8", "\u6700\u591a"]
    needs_group = any(kw in query for kw in triggers)

    # Rules: keyword -> SQL expression to GROUP BY
    rules = [
        ("\u5730\u533a", "r.region_name"),
        ("\u533a\u57df", "r.region_name"),
        ("\u5927\u533a", "r.region_name"),
        ("\u54c1\u7c7b", "p.category"),
        ("\u7c7b\u522b", "p.category"),
        ("\u54c1\u724c", "p.brand"),
        ("\u4f1a\u5458", "c.member_level"),
        ("\u7b49\u7ea7", "c.member_level"),
        ("\u6027\u522b", "c.gender"),
        ("\u6708", "d.month"),
        ("\u6708\u4efd", "d.month"),
        ("\u5b63\u5ea6", "d.quarter"),
        ("\u5e74\u4efd", "d.year"),
        ("\u5e74", "d.year"),
    ]

    if needs_group:
        for kw, expr in rules:
            if kw in query:
                out.append((kw, expr))
    # Implicit group-by when ORDER BY DESC / Top / \u6392\u540d + a dimension hint
    if not out:
        order_tokens = ["\u6392\u540d", "\u6392\u884c", "Top", "top", "TOP"]
        if any(t in query for t in order_tokens):
            for kw, expr in rules:
                if kw in query:
                    out.append((kw, expr))
    return out


def _detect_order_limit(query: str) -> tuple[Optional[str], Optional[int]]:
    # Top N / first N / front N / highest N / lowest N
    m = re.search(r"(?:Top|top|TOP|\u524d)\s*(\d+)", query)
    if not m:
        m = re.search(r"\u6700\u9ad8[\u7684]*?\s*(\d+)", query)
    if not m:
        m = re.search(r"\u6700\u591a[\u7684]*?\s*(\d+)", query)
    if not m:
        m = re.search(r"\u6700\u4f4e[\u7684]*?\s*(\d+)", query)
    if not m:
        m = re.search(r"\u6700\u5c11[\u7684]*?\s*(\d+)", query)
    limit = int(m.group(1)) if m else None
    if "\u6392\u540d" in query or "\u6392\u884c" in query:
        return "DESC", limit or 100
    if "\u6700\u9ad8" in query or "\u6700\u591a" in query:
        return "DESC", limit or 100
    if "\u6700\u4f4e" in query or "\u6700\u5c11" in query:
        return "ASC", limit or 100
    if limit:
        return "DESC", limit
    return None, None

def _wants_aggregate(query: str) -> bool:
    if any(kw in query for kw in ["\u591a\u5c11", "\u603b", "\u5e73\u5747", "\u5360\u6bd4", "\u6bd4\u4f8b", "\u7387", "\u989d", "\u6570", "\u9500\u552e\u989d", "\u8ba2\u5355\u6570", "\u5ba2\u5355\u4ef7"]):
        return True
    for phrase, (tbl, _, _) in _PHRASES.items():
        if tbl in _METRIC_PHRASES and phrase in query:
            return True
    return False



def _build_ratio_sql(query: str, joins: list[str], wheres: list[str]) -> Optional[str]:
    """Build a ratio SQL (\u5360\u6bd4/\u6bd4\u4f8b/\u603b\u5360) when the user asks for percentage."""
    ratio_kw = [chr(0x5360) + chr(0x6bd4), chr(0x6bd4) + chr(0x4f8b), chr(0x603b) + chr(0x5360)]
    if not any(kw in query for kw in ratio_kw):
        return None
    # CASE WHEN for filter column, denominator = full sum
    case_clauses = []
    for j in joins[:]:
        if "dim_region" in j and any(w.startswith("r.province") for w in wheres):
            case_clauses.append(
                f"SUM(CASE WHEN {wheres[0]} THEN f.order_amount ELSE 0 END) / NULLIF(SUM(f.order_amount), 0) AS ratio"
            )
            break
        if "dim_product" in j and any(w.startswith("p.category") for w in wheres):
            case_clauses.append(
                f"SUM(CASE WHEN {wheres[0]} THEN 1 ELSE 0 END) * 1.0 / NULLIF(COUNT(*), 0) AS ratio"
            )
            break
        if "dim_customer" in j and any(w.startswith("c.member_level") for w in wheres):
            case_clauses.append(
                f"SUM(CASE WHEN {wheres[0]} THEN 1 ELSE 0 END) * 1.0 / NULLIF(COUNT(*), 0) AS ratio"
            )
            break
    if not case_clauses:
        return None
    sql = ["SELECT " + case_clauses[0], "FROM fact_order f"]
    for j in joins:
        sql.append(j)
    return chr(0x0a).join(sql)


# ---------- main mock entrypoint ----------

def _mock_generate(prompt: str) -> str:
    p = prompt.strip()
    head = p[:80]
    query = _extract_user_query(prompt)

    if "\u5173\u952e\u8bcd" in head and ("\u6269\u5c55" in head or "\u63d0\u53d6" in head):
        kws = _extract_keywords_from_prompt(prompt)
        return json.dumps({"keywords": kws}, ensure_ascii=False)

    if "filter_table" in p or "table_info" in head:
        m = re.search(r"table_ids?\s*[:\uff1a]\s*([^\n]+)", p)
        ids = re.findall(r"[a-z_]+", m.group(1)) if m else []
        return json.dumps({"keep_table_ids": ids}, ensure_ascii=False)
    if "filter_metric" in p or "metric_ids" in head:
        m = re.search(r"metric_ids?\s*[:\uff1a]\s*([^\n]+)", p)
        ids = re.findall(r"[A-Z_]+", m.group(1)) if m else []
        return json.dumps({"keep_metric_ids": ids}, ensure_ascii=False)

    if "SQL" in head or "select" in head.lower() or "SELECT" in p:
        joins, wheres = _detect_filters(query)
        time_clause = _detect_time_filter(query)
        if time_clause:
            joins.append("JOIN dim_date d ON d.date_id = f.date_id")
            wheres.append(time_clause)

        group_cols = _detect_group_by(query)
        # Auto-add the JOINs for any dim table that group_cols references
        # but isn't yet joined. This makes questions like "各品类的销售额"
        # or "各大区的GMV" (which mention the dimension but no specific
        # value) still produce the right JOIN + GROUP BY.
        joined_tables = {j.split()[1] for j in joins if j.startswith("JOIN ")}
        for _, expr in group_cols:
            if expr.startswith("p.") and "dim_product" not in joined_tables:
                joins.append("JOIN dim_product p ON p.product_id = f.product_id")
                joined_tables.add("dim_product")
            elif expr.startswith("r.") and "dim_region" not in joined_tables:
                joins.append("JOIN dim_region r ON r.region_id = f.region_id")
                joined_tables.add("dim_region")
            elif expr.startswith("c.") and "dim_customer" not in joined_tables:
                joins.append("JOIN dim_customer c ON c.customer_id = f.customer_id")
                joined_tables.add("dim_customer")
            elif expr.startswith("d.") and "dim_date" not in joined_tables:
                joins.append("JOIN dim_date d ON d.date_id = f.date_id")
                joined_tables.add("dim_date")

        order_dir, limit = _detect_order_limit(query)

        metric, measure_fn, measure_col = _detect_measure(query)

        # \u5ba2\u5355\u4ef7 / \u5747\u4ef7 short-circuit
        if metric is None and any(kw in query for kw in ["\u5ba2\u5355\u4ef7", "\u5747\u4ef7"]):
            metric = "AOV"
            measure_fn = "AVG"
            measure_col = "f.order_amount"

        if not _wants_aggregate(query) and not group_cols:
            select_parts = ["COUNT(*) AS cnt"]
        else:
            metric_alias = {"GMV": "gmv", "ORDER_CNT": "cnt", "AOV": "aov", "QTY": ("avg_qty" if measure_fn == "AVG" else "total_qty")}.get(metric, "value")
            select_parts = [f"{measure_fn}({measure_col}) AS {metric_alias}"]
            for alias, expr in group_cols:
                select_parts.append(f"{expr} AS {alias}")

        # Try ratio/percentage shortcut first
        ratio_sql = _build_ratio_sql(query, joins, wheres)
        if ratio_sql:
            return ratio_sql
        sql_parts = ["SELECT " + ", ".join(select_parts)]
        sql_parts.append("FROM fact_order f")
        for j in joins:
            sql_parts.append(j)
        if wheres:
            sql_parts.append("WHERE " + " AND ".join(wheres))
        if group_cols:
            sql_parts.append("GROUP BY " + ", ".join(expr for _, expr in group_cols))
        if order_dir and (metric is not None or group_cols):
            order_alias = {"GMV": "gmv", "ORDER_CNT": "cnt", "AOV": "aov", "QTY": "total_qty"}.get(metric, "value")
            sql_parts.append(f"ORDER BY {order_alias} {order_dir}")
        if limit and limit <= 1000:
            sql_parts.append(f"LIMIT {limit}")
        return "\n".join(sql_parts)

    if "explanation" in p.lower() or "\u8bf4\u660e" in head or "\u89e3\u91ca" in head:
        m = re.search(r"([\d,]+\.\d+|[\d,]+)", prompt)
        value = m.group(1) if m else ""
        if value:
            return f"\u9488\u5bf9\u60a8\u7684\u95ee\u9898\uff0c\u67e5\u8be2\u8fd4\u56de\u6570\u503c {value}\u3002\u6570\u636e\u5df2\u6309\u6700\u65b0\u4e8b\u5b9e\u8868\u7edf\u8ba1\u3002"
        return "\u9488\u5bf9\u60a8\u7684\u95ee\u9898\uff0c\u67e5\u8be2\u5df2\u8fd4\u56de\u6700\u65b0\u7ed3\u679c\u3002"

    return f"[mock-llm] {head}"