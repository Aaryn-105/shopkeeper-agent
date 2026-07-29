"""Glossary injection unit tests.

Covers conf/glossary.yaml loader + matcher + renderer. We intentionally do not
mock the loader so this test also pins the on-disk glossary shape: any rename
or alias removal that breaks a downstream case will surface here first.
"""

from __future__ import annotations

import pytest

from app.prompt.glossary_injection import (
    glossary_summary,
    load_glossary,
    matched_categories,
    render_glossary_for_query,
)

# ---------- summary ----------


def test_glossary_summary_has_three_categories():
    s = glossary_summary()
    assert set(s) == {"date_expressions", "regions", "metric_formulas", "total"}
    assert s["total"] == (s["date_expressions"] + s["regions"] + s["metric_formulas"])


def test_glossary_has_baseline_entries():
    """Regression: the canonical 7大区 / 14日期 / 7指标 must remain loadable."""
    g = load_glossary()
    # regions
    for name in ("华东", "华北", "华南", "华中", "西南", "西北", "东北"):
        assert name in g["regions"], f"region {name} missing"
    # core metrics
    for name in ("GMV", "ORDER_CNT", "AOV", "QTY", "UV", "PAY_CNT", "PAY_RATE"):
        assert name in g["metric_formulas"], f"metric {name} missing"
    # core date expressions
    for name in (
        "上个月",
        "本月",
        "最近7天",
        "最近30天",
        "上季度",
        "年初至今",
        "今天",
        "昨天",
    ):
        assert name in g["date_expressions"], f"date {name} missing"


# ---------- renderer ----------


def test_render_empty_for_unmatched_query():
    assert render_glossary_for_query("total sales") == ""


def test_render_includes_date_section_for_month_query():
    out = render_glossary_for_query("上个月华东的 GMV")
    assert "### 日期表达" in out
    assert "上个月" in out
    assert "DATE_FORMAT" in out


def test_render_includes_region_section_for_region_query():
    out = render_glossary_for_query("华东 GMV")
    assert "### 区域层级" in out
    assert "华东" in out
    assert "上海" in out and "江苏" in out and "浙江" in out


def test_render_includes_metric_section_for_gmv_query():
    out = render_glossary_for_query("GMV")
    assert "### 指标公式" in out
    assert "SUM(f.order_amount)" in out


def test_render_combined_query_returns_three_sections():
    out = render_glossary_for_query("上个月华东地区的 GMV 是多少？")
    assert "### 日期表达" in out
    assert "### 区域层级" in out
    assert "### 指标公式" in out
    assert (
        out.index("### 日期表达")
        < out.index("### 区域层级")
        < out.index("### 指标公式")
    )


def test_render_picks_alias_over_key():
    """近7天 is an alias of 最近7天 - both should resolve to the same entry."""
    out_a = render_glossary_for_query("最近7天订单")
    out_b = render_glossary_for_query("近7天订单")
    assert "DATE_SUB" in out_a and "INTERVAL 7 DAY" in out_a
    assert "DATE_SUB" in out_b and "INTERVAL 7 DAY" in out_b


def test_render_metrics_aliases_hit():
    """Both 销售额 and GMV should hit the GMV metric entry."""
    assert "SUM(f.order_amount)" in render_glossary_for_query("销售额")
    assert "SUM(f.order_amount)" in render_glossary_for_query("AOV")  # alias
    assert "客单价" in render_glossary_for_query(
        "客单价"
    ) or "AOV" in render_glossary_for_query("客单价")
    assert "COUNT(DISTINCT f.customer_id)" in render_glossary_for_query("独立用户")


def test_render_region_aliases_hit():
    """京沪 / 京津冀 / 江浙沪 aliases should all resolve."""
    for q in ("京津冀 GMV", "江浙沪 GMV", "华东地区 GMV"):
        out = render_glossary_for_query(q)
        # 京津冀 / 江浙沪 / 华东地区 alias maps to 华北 / 华东 / 华东
        assert "### 区域层级" in out, q


def test_render_no_metric_when_no_metric_keyword():
    """A region+date query must not include the metric section unless a metric word is present."""
    out = render_glossary_for_query("上个月华东有哪些订单")
    assert "### 日期表达" in out
    assert "### 区域层级" in out
    # 没有 GMV/订单数 等关键词 -> 不应有指标公式段
    assert "### 指标公式" not in out


# ---------- matched_categories ----------


def test_matched_categories_returns_only_hits():
    assert matched_categories("上个月华东 GMV") == ["date", "region", "metric"]
    assert matched_categories("最近30天") == ["date"]
    assert matched_categories("华北") == ["region"]
    assert matched_categories("独立用户") == ["metric"]
    assert matched_categories("hello") == []


# ---------- loader: cache + reload ----------


def test_reload_glossary_returns_fresh_state(tmp_path, monkeypatch):
    """reload_glossary() should re-read from disk; we override the path
    indirectly by writing a tiny YAML elsewhere and patching the module path."""
    # Build a tiny throwaway YAML next to the real one
    import app.prompt.glossary_injection as gi

    alt = tmp_path / "tiny.yaml"
    alt.write_text(
        "date_expressions: {x: {expression: 'NOW', aliases: []}}\n"
        "regions: {}\nmetric_formulas: {}\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(gi, "_GLOSSARY_PATH", alt)
    gi.load_glossary.cache_clear()
    g = gi.reload_glossary()
    assert "x" in g["date_expressions"]
    assert g["regions"] == {}
    # Restore for downstream tests
    monkeypatch.undo()
    gi.load_glossary.cache_clear()


@pytest.mark.parametrize(
    "q,expected_section",
    [
        ("GMV 多少", "### 指标公式"),
        ("订单数", "### 指标公式"),
        ("客单价", "### 指标公式"),
        ("件数", "### 指标公式"),
        ("买家数", "### 指标公式"),
        ("支付率", "### 指标公式"),
        ("上个月 GMV", "### 日期表达"),
        ("最近7天订单", "### 日期表达"),
        ("上周订单数", "### 日期表达"),
        ("年初至今 GMV", "### 日期表达"),
        ("今天 GMV", "### 日期表达"),
        ("昨天 客单价", "### 日期表达"),
        ("华东 GMV", "### 区域层级"),
        ("华北 GMV", "### 区域层级"),
        ("华南 GMV", "### 区域层级"),
    ],
)
def test_param_render_hits_expected_section(q, expected_section):
    assert expected_section in render_glossary_for_query(q), q
