"""Populate MySQL with sample DW + meta data (idempotent).

Inserts:
  - dw.fact_order     : 1000 synthetic orders across 6 months
  - dw.dim_customer   : 50 customers across 4 member levels
  - dw.dim_product    : 50 products across 4 categories / 5 brands
  - dw.dim_region     : 12 regions (4 provinces per region x 3 macro regions)
  - dw.dim_date       : 1095 dates spanning 2025-01-01 .. 2027-12-31

Also seeds meta metadata so the agent has something to recall:
  - meta.table_info : 5 tables (fact_order + 4 dims)
  - meta.column_info: full column lists keyed by "table.column"
  - meta.metric_info: 3 example metrics (GMV, order count, AOV)
  - meta.column_metric: links columns to metrics

Usage:
  uv run python scripts/init_dw_sample_data.py
"""
from __future__ import annotations
import json
import random
import sys
from datetime import date, timedelta
from pathlib import Path

import pymysql

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.config import cfg  # noqa: E402
from app.core.logger import setup_logger, logger  # noqa: E402


# --------------------- DW seeds ---------------------

REGIONS = [
    ("R001", "北京", "华北", "中国"),
    ("R002", "天津", "华北", "中国"),
    ("R003", "河北", "华北", "中国"),
    ("R004", "上海", "华东", "中国"),
    ("R005", "江苏", "华东", "中国"),
    ("R006", "浙江", "华东", "中国"),
    ("R007", "广东", "华南", "中国"),
    ("R008", "广西", "华南", "中国"),
    ("R009", "福建", "华南", "中国"),
    ("R010", "湖北", "华中", "中国"),
    ("R011", "湖南", "华中", "中国"),
    ("R012", "河南", "华中", "中国"),
]

CATEGORIES = ["手机数码", "电脑办公", "家用电器", "服饰鞋包"]
BRANDS = ["华为", "苹果", "小米", "戴尔", "海尔"]
PRODUCTS = [
    (f"P{i:04d}", f"商品{i:04d}", random.choice(CATEGORIES), random.choice(BRANDS))
    for i in range(1, 51)
]

CUSTOMERS = [
    (
        f"C{i:04d}",
        f"客户{i:04d}",
        random.choice(["M", "F"]),
        random.choice(["普通会员", "银卡会员", "金卡会员", "钻石会员"]),
    )
    for i in range(1, 51)
]


def _date_id(d: date) -> int:
    return d.year * 10000 + d.month * 100 + d.day


def _build_dim_date_rows(start_year: int = 2025, days: int = 1095) -> list[tuple]:
    rows = []
    start = date(start_year, 1, 1)
    for offset in range(days):
        d = start + timedelta(days=offset)
        quarter = f"Q{(d.month - 1) // 3 + 1}"
        rows.append((_date_id(d), d.year, quarter, d.month, d.day))
    return rows


def _build_fact_order_rows(rng: random.Random) -> list[tuple]:
    rows = []
    start = date(2025, 1, 1)
    for i in range(1, 1001):
        # Spread dates across ~30 months so "上个月" / "最近30天" / "YTD"
        # always return rows regardless of when the demo is run.
        offset = rng.randint(0, 900)
        d = start + timedelta(days=offset)
        customer = rng.choice(CUSTOMERS)
        product = rng.choice(PRODUCTS)
        region = rng.choice(REGIONS)
        qty = rng.randint(1, 5)
        amount = round(qty * rng.uniform(99, 4999), 2)
        rows.append((
            f"O{i:06d}",
            customer[0],
            product[0],
            _date_id(d),
            region[0],
            qty,
            amount,
        ))
    return rows


# --------------------- meta seeds ---------------------

TABLE_INFO_ROWS = [
    # (id, name, role, description)
    ("fact_order",   "订单事实表", "fact",     "记录每一笔订单的下单数量和金额，是核心分析的事实表"),
    ("dim_customer", "客户维度表", "dimension", "客户的基本信息，包括姓名、性别、会员等级"),
    ("dim_product",  "商品维度表", "dimension", "商品的基本信息，包括名称、品类、品牌"),
    ("dim_region",   "地区维度表", "dimension", "地区的基本信息，包括省份、大区、国家"),
    ("dim_date",     "日期维度表", "dimension", "日期的基本信息，包括年、季度、月、日"),
]

COLUMN_INFO_ROWS = [
    # (id, name, type, role, description, examples, alias, table_id)
    # fact_order
    ("fact_order.order_id",       "order_id",       "varchar(64)",   "primary_key", "订单 ID，主键", "O000001", "订单号,订单编号", "fact_order"),
    ("fact_order.customer_id",    "customer_id",    "varchar(64)",   "foreign_key", "客户 ID，关联 dim_customer", "C0001", "客户编号", "fact_order"),
    ("fact_order.product_id",     "product_id",     "varchar(64)",   "foreign_key", "商品 ID，关联 dim_product", "P0001", "商品编号", "fact_order"),
    ("fact_order.date_id",        "date_id",        "int",           "foreign_key", "下单日期 ID，关联 dim_date", "20250115", "日期编号", "fact_order"),
    ("fact_order.region_id",      "region_id",      "varchar(64)",   "foreign_key", "下单地区 ID，关联 dim_region", "R001", "地区编号", "fact_order"),
    ("fact_order.order_quantity", "order_quantity", "int",           "measure",     "下单数量", "1,2,5", "购买数量,件数", "fact_order"),
    ("fact_order.order_amount",   "order_amount",   "decimal(10,2)", "measure",     "下单金额，单位元", "299.00,5999.00", "订单金额,销售额", "fact_order"),
    # dim_customer
    ("dim_customer.customer_id",   "customer_id",   "varchar(64)",  "primary_key", "客户 ID，主键", "C0001", "客户编号", "dim_customer"),
    ("dim_customer.customer_name", "customer_name", "varchar(128)", "dimension",   "客户姓名", "客户0001", "姓名", "dim_customer"),
    ("dim_customer.gender",        "gender",        "varchar(16)",  "dimension",   "性别", "M,F", "性别", "dim_customer"),
    ("dim_customer.member_level",  "member_level",  "varchar(32)",  "dimension",   "会员等级", "普通会员,银卡会员,金卡会员,钻石会员", "会员等级", "dim_customer"),
    # dim_product
    ("dim_product.product_id",   "product_id",   "varchar(64)",  "primary_key", "商品 ID，主键", "P0001", "商品编号", "dim_product"),
    ("dim_product.product_name", "product_name", "varchar(128)", "dimension",   "商品名称", "商品0001", "商品名", "dim_product"),
    ("dim_product.category",     "category",     "varchar(64)",  "dimension",   "商品品类", "手机数码,电脑办公", "品类,分类", "dim_product"),
    ("dim_product.brand",        "brand",        "varchar(64)",  "dimension",   "品牌", "华为,苹果,小米", "品牌", "dim_product"),
    # dim_region
    ("dim_region.region_id",   "region_id",   "varchar(64)", "primary_key", "地区 ID，主键", "R001", "地区编号", "dim_region"),
    ("dim_region.province",    "province",    "varchar(64)", "dimension",   "省份", "北京,上海,广东", "省份", "dim_region"),
    ("dim_region.region_name", "region_name", "varchar(64)", "dimension",   "大区名称", "华北,华东,华南", "大区", "dim_region"),
    ("dim_region.country",     "country",     "varchar(64)", "dimension",   "国家", "中国", "国家", "dim_region"),
    # dim_date
    ("dim_date.date_id", "date_id", "int",        "primary_key", "日期 ID，格式 yyyyMMdd", "20250101", "日期编号", "dim_date"),
    ("dim_date.year",    "year",    "int",        "dimension",   "年份", "2025", "年", "dim_date"),
    ("dim_date.quarter", "quarter", "varchar(8)", "dimension",   "季度", "Q1,Q2", "季度", "dim_date"),
    ("dim_date.month",   "month",   "int",        "dimension",   "月份", "1..12", "月", "dim_date"),
    ("dim_date.day",     "day",     "int",        "dimension",   "日", "1..31", "日", "dim_date"),
]

METRIC_INFO_ROWS = [
    # (id, name, description, related_columns_json, alias_json)
    ("GMV",        "成交总额", "所有订单的下单金额合计，反映总销售额",
        json.dumps(["fact_order.order_amount"], ensure_ascii=False),
        json.dumps(["销售额", "成交金额", "销售总额"], ensure_ascii=False)),
    ("ORDER_CNT",  "订单数量", "订单总数",
        json.dumps(["fact_order.order_id"], ensure_ascii=False),
        json.dumps(["订单数", "订单总量"], ensure_ascii=False)),
    ("AOV",        "客单价", "平均每笔订单金额，等于 GMV / 订单数",
        json.dumps(["fact_order.order_amount", "fact_order.order_id"], ensure_ascii=False),
        json.dumps(["平均订单金额", "单均价"], ensure_ascii=False)),
]

COLUMN_METRIC_ROWS = [
    ("fact_order.order_amount", "GMV"),
    ("fact_order.order_amount", "AOV"),
    ("fact_order.order_id",     "ORDER_CNT"),
    ("fact_order.order_id",     "AOV"),
]


# --------------------- DML ---------------------

def _connect() -> pymysql.Connection:
    return pymysql.connect(
        host=cfg.mysql.host, port=int(cfg.mysql.port),
        user=cfg.mysql.admin_user, password=cfg.mysql.admin_password,
        charset="utf8mb4", autocommit=False, connect_timeout=10,
    )


def _ensure_dw(cur) -> dict[str, int]:
    counts: dict[str, int] = {}
    cur.execute(f"USE `{cfg.mysql.dw_db}`")

    cur.executemany(
        "INSERT IGNORE INTO dim_region (region_id, province, region_name, country) "
        "VALUES (%s,%s,%s,%s)",
        REGIONS,
    )
    counts["dim_region"] = len(REGIONS)

    cur.executemany(
        "INSERT IGNORE INTO dim_product (product_id, product_name, category, brand) "
        "VALUES (%s,%s,%s,%s)",
        PRODUCTS,
    )
    counts["dim_product"] = len(PRODUCTS)

    cur.executemany(
        "INSERT IGNORE INTO dim_customer (customer_id, customer_name, gender, member_level) "
        "VALUES (%s,%s,%s,%s)",
        CUSTOMERS,
    )
    counts["dim_customer"] = len(CUSTOMERS)

    # dim_date: insert-ignore so reruns are safe and additive.
    date_rows = _build_dim_date_rows(start_year=2025, days=1095)
    cur.executemany(
        "INSERT IGNORE INTO dim_date (date_id, year, quarter, month, day) "
        "VALUES (%s,%s,%s,%s,%s)",
        date_rows,
    )
    counts["dim_date"] = len(date_rows)

    # fact_order: truncate first so the new date range actually applies.
    cur.execute("DELETE FROM fact_order")
    counts["fact_order_deleted"] = cur.rowcount

    rng = random.Random(20251225)
    order_rows = _build_fact_order_rows(rng)
    cur.executemany(
        "INSERT INTO fact_order "  # not IGNORE: we just truncated
        "(order_id, customer_id, product_id, date_id, region_id, order_quantity, order_amount) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s)",
        order_rows,
    )
    counts["fact_order"] = len(order_rows)
    return counts


def _ensure_meta(cur) -> dict[str, int]:
    counts: dict[str, int] = {}
    cur.execute(f"USE `{cfg.mysql.meta_db}`")

    cur.executemany(
        "INSERT IGNORE INTO table_info (id, name, role, description) "
        "VALUES (%s,%s,%s,%s)",
        TABLE_INFO_ROWS,
    )
    counts["table_info"] = len(TABLE_INFO_ROWS)

    cur.executemany(
        "INSERT IGNORE INTO column_info "
        "(id, name, type, role, description, examples, alias, table_id) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
        COLUMN_INFO_ROWS,
    )
    counts["column_info"] = len(COLUMN_INFO_ROWS)

    cur.executemany(
        "INSERT IGNORE INTO metric_info (id, name, description, related_columns, alias) "
        "VALUES (%s,%s,%s,%s,%s)",
        METRIC_INFO_ROWS,
    )
    counts["metric_info"] = len(METRIC_INFO_ROWS)

    cur.executemany(
        "INSERT IGNORE INTO column_metric (column_id, metric_id) VALUES (%s,%s)",
        COLUMN_METRIC_ROWS,
    )
    counts["column_metric"] = len(COLUMN_METRIC_ROWS)
    return counts


def _count(cur, db: str, table: str) -> int:
    cur.execute(f"SELECT COUNT(*) FROM `{db}`.`{table}`")
    return int(cur.fetchone()[0])


def main() -> int:
    setup_logger(log_dir=cfg.logging.dir, level="INFO",
                 retention_days=int(cfg.logging.retention_days))
    logger.info("init_dw_sample_data start")

    conn = _connect()
    try:
        with conn.cursor() as cur:
            dw_counts = _ensure_dw(cur)
            meta_counts = _ensure_meta(cur)
        conn.commit()
    except Exception:
        conn.rollback()
        logger.exception("init failed, rolled back")
        raise
    finally:
        conn.close()

    # verify with a fresh connection
    conn = _connect()
    try:
        with conn.cursor() as cur:
            actual_dw = {t: _count(cur, cfg.mysql.dw_db, t)
                         for t in ("fact_order", "dim_customer", "dim_product", "dim_region", "dim_date")}
            actual_meta = {t: _count(cur, cfg.mysql.meta_db, t)
                           for t in ("table_info", "column_info", "metric_info", "column_metric")}
    finally:
        conn.close()

    logger.info("dw counts: requested={} actual={}", dw_counts, actual_dw)
    logger.info("meta counts: requested={} actual={}", meta_counts, actual_meta)
    print(f"OK dw={actual_dw} meta={actual_meta}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())