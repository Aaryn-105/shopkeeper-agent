"""Glossary injection for generate_sql.

Loads conf/glossary.yaml and renders only the entries that match a user query,
so the prompt stays compact and high-signal. Three categories:

  - date_expressions  - "上个月" / "最近7天" / "上周" / ...
  - regions           - "华东" / "华北" / ...
  - metric_formulas   - "GMV" / "ORDER_CNT" / "AOV" / ...

The renderer is rule-based (substring match on key + aliases) - no LLM call -
so it is deterministic, cheap (<1ms), and easy to test.

Usage from generate_sql:

    from app.prompt.glossary_injection import render_glossary_for_query
    block = render_glossary_for_query(query)
    prompt = template.format(..., glossary_block=block)
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_GLOSSARY_PATH = _PROJECT_ROOT / "conf" / "glossary.yaml"


# ---------- loader ----------


@lru_cache(maxsize=1)
def load_glossary() -> dict[str, dict[str, Any]]:
    """Load conf/glossary.yaml once; return empty dicts on any failure."""
    if not _GLOSSARY_PATH.exists():
        return {"date_expressions": {}, "regions": {}, "metric_formulas": {}}
    with _GLOSSARY_PATH.open(encoding="utf-8-sig") as f:
        raw = yaml.safe_load(f) or {}
    raw.setdefault("date_expressions", {})
    raw.setdefault("regions", {})
    raw.setdefault("metric_formulas", {})
    return raw


def reload_glossary() -> dict[str, dict[str, Any]]:
    """Force re-read from disk; useful after the user edits conf/glossary.yaml."""
    load_glossary.cache_clear()
    return load_glossary()


# ---------- matching ----------


def _match_entries(query: str, entries: dict[str, Any]) -> dict[str, Any]:
    """Return entries whose key OR any alias appears as substring in query.

    Case-sensitive on purpose: Chinese characters do not need lowercasing, and
    mixing English aliases (e.g. YTD, AOV) with case-folding risks missing the
    canonical "GMV" / "QTY" forms.
    """
    matched: dict[str, Any] = {}
    for key, info in entries.items():
        candidates: list[str] = [str(key)]
        aliases = info.get("aliases") if isinstance(info, dict) else None
        if isinstance(aliases, list):
            candidates.extend(str(a) for a in aliases if a)
        for cand in candidates:
            if cand and cand in query:
                matched[key] = info
                break
    return matched


# ---------- rendering ----------


def _fmt_date(name: str, info: dict[str, Any]) -> str:
    sql = (info.get("expression") or "").strip().replace("\n", " ")
    note = (info.get("note") or "").strip()
    line = f"- **{name}** -> `{sql}`"
    if note:
        line += f"  ({note})"
    return line


def _fmt_region(name: str, info: dict[str, Any]) -> str:
    rn = info.get("region_name") or name
    provs = info.get("provinces") or []
    line = f"- **{name}** ({rn}) -> provinces: {', '.join(provs) if provs else 'N/A'}"
    return line


def _fmt_metric(name: str, info: dict[str, Any]) -> str:
    sql = (info.get("sql") or "").strip()
    desc = (info.get("description") or "").strip()
    line = f"- **{name}** -> `{sql}`"
    if desc:
        line += f"  ({desc})"
    return line


def render_glossary_for_query(query: str) -> str:
    """Return a markdown block of relevant glossary entries, or "" if no match.

    The block is intentionally compact: only entries whose key/alias appears in
    `query` are rendered, and only the categories that have at least one hit
    get a section header.
    """
    g = load_glossary()
    date_m = _match_entries(query, g["date_expressions"])
    region_m = _match_entries(query, g["regions"])
    metric_m = _match_entries(query, g["metric_formulas"])

    sections: list[str] = []
    if date_m:
        sections.append(
            "### 日期表达\n" + "\n".join(_fmt_date(k, v) for k, v in date_m.items())
        )
    if region_m:
        sections.append(
            "### 区域层级\n" + "\n".join(_fmt_region(k, v) for k, v in region_m.items())
        )
    if metric_m:
        sections.append(
            "### 指标公式\n" + "\n".join(_fmt_metric(k, v) for k, v in metric_m.items())
        )

    return "\n\n".join(sections)


def glossary_summary() -> dict[str, int]:
    """Counts of entries per category (used by /stats page + tests)."""
    g = load_glossary()
    return {
        "date_expressions": len(g.get("date_expressions", {})),
        "regions": len(g.get("regions", {})),
        "metric_formulas": len(g.get("metric_formulas", {})),
        "total": (
            len(g.get("date_expressions", {}))
            + len(g.get("regions", {}))
            + len(g.get("metric_formulas", {}))
        ),
    }


def matched_categories(query: str) -> list[str]:
    """Return the list of categories that have at least one match in query.

    Returned in fixed order: ["date", "region", "metric"]. Useful for
    metrics / debug logging.
    """
    g = load_glossary()
    hits: list[str] = []
    if _match_entries(query, g["date_expressions"]):
        hits.append("date")
    if _match_entries(query, g["regions"]):
        hits.append("region")
    if _match_entries(query, g["metric_formulas"]):
        hits.append("metric")
    return hits
