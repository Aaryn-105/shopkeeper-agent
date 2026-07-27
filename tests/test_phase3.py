"""Phase 3 verification: MySQL schema, readonly user, sample data, privilege enforcement."""
from __future__ import annotations
import subprocess
import sys
from pathlib import Path

import pymysql
import pytest

from app.core.config import cfg


# ---------- helpers ----------

ROOT = Path(__file__).resolve().parent.parent


def _admin_conn() -> pymysql.Connection:
    return pymysql.connect(
        host=cfg.mysql.host,
        port=int(cfg.mysql.port),
        user=cfg.mysql.admin_user,
        password=cfg.mysql.admin_password,
        charset="utf8mb4",
        autocommit=True,
    )


def _readonly_conn() -> pymysql.Connection:
    return pymysql.connect(
        host=cfg.mysql.host,
        port=int(cfg.mysql.port),
        user=cfg.mysql.ro_user,
        password=cfg.mysql.ro_password,
        charset="utf8mb4",
        autocommit=True,
    )


def _run_script(name: str) -> subprocess.CompletedProcess:
    """Run scripts/<name>.py via the same Python we are running on."""
    script = ROOT / "scripts" / name
    return subprocess.run(
        [sys.executable, str(script)],
        capture_output=True,
        text=True,
        timeout=60,
    )


def _list_tables(cur, db: str) -> list[str]:
    cur.execute(f"USE `{db}`")
    cur.execute("SHOW TABLES")
    return [row[0] for row in cur.fetchall()]


def _describe(cur, db: str, table: str) -> dict[str, dict]:
    cur.execute(f"USE `{db}`")
    cur.execute(f"DESCRIBE `{table}`")
    out: dict[str, dict] = {}
    for col, type_, null, key, default, extra in cur.fetchall():
        out[col] = {"type": type_, "null": null, "key": key, "default": default, "extra": extra}
    return out


# ---------- meta schema (SRS 6.2.2) ----------

META_EXPECTED_TABLES = {"table_info", "column_info", "metric_info", "column_metric", "llm_call_log"}
DW_EXPECTED_TABLES = {"fact_order", "dim_customer", "dim_product", "dim_region", "dim_date"}


@pytest.fixture(scope="module", autouse=True)
def _bootstrap_schema():
    """Ensure both init scripts have run; idempotent so safe to call here."""
    r1 = _run_script("init_meta_mysql.py")
    r2 = _run_script("init_dw_sample_data.py")
    assert r1.returncode == 0, f"init_meta_mysql failed: {r1.stdout}\n{r1.stderr}"
    assert r2.returncode == 0, f"init_dw_sample_data failed: {r2.stdout}\n{r2.stderr}"
    yield


def test_meta_and_dw_databases_exist():
    conn = _admin_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SHOW DATABASES")
            dbs = {row[0] for row in cur.fetchall()}
            assert cfg.mysql.meta_db in dbs
            assert cfg.mysql.dw_db in dbs
    finally:
        conn.close()


def test_meta_has_four_core_tables_plus_llm_call_log():
    conn = _admin_conn()
    try:
        with conn.cursor() as cur:
            tables = set(_list_tables(cur, cfg.mysql.meta_db))
            assert META_EXPECTED_TABLES <= tables, (
                f"missing meta tables: {META_EXPECTED_TABLES - tables}"
            )
    finally:
        conn.close()


def test_dw_has_one_fact_and_four_dims():
    conn = _admin_conn()
    try:
        with conn.cursor() as cur:
            tables = set(_list_tables(cur, cfg.mysql.dw_db))
            assert DW_EXPECTED_TABLES <= tables, (
                f"missing dw tables: {DW_EXPECTED_TABLES - tables}"
            )
    finally:
        conn.close()


def test_column_info_columns_match_srs_schema():
    conn = _admin_conn()
    try:
        with conn.cursor() as cur:
            cols = _describe(cur, cfg.mysql.meta_db, "column_info")
        # columns explicitly required by SRS
        for required in ("id", "name", "type", "role", "description", "examples", "alias", "table_id"):
            assert required in cols, f"column_info missing required column: {required}"
        # id format per SRS: "table_name.column_name"
        assert "varchar(256)" in cols["id"]["type"].lower()
        # role enum-ish but stored as varchar
        assert "varchar" in cols["role"]["type"].lower()
    finally:
        conn.close()


def test_table_info_columns_match_srs_schema():
    conn = _admin_conn()
    try:
        with conn.cursor() as cur:
            cols = _describe(cur, cfg.mysql.meta_db, "table_info")
        for required in ("id", "name", "role", "description"):
            assert required in cols
        assert "varchar(128)" in cols["id"]["type"].lower()
    finally:
        conn.close()


def test_metric_info_columns_match_srs_schema():
    conn = _admin_conn()
    try:
        with conn.cursor() as cur:
            cols = _describe(cur, cfg.mysql.meta_db, "metric_info")
        for required in ("id", "name", "description", "related_columns", "alias"):
            assert required in cols
    finally:
        conn.close()


def test_column_metric_is_a_bridge_table():
    conn = _admin_conn()
    try:
        with conn.cursor() as cur:
            cols = _describe(cur, cfg.mysql.meta_db, "column_metric")
            keys = {c: cols[c]["key"] for c in cols}
            assert keys.get("column_id", "").upper() == "PRI"
            assert keys.get("metric_id", "").upper() == "PRI"
    finally:
        conn.close()


def test_fact_order_schema_matches_srs():
    conn = _admin_conn()
    try:
        with conn.cursor() as cur:
            cols = _describe(cur, cfg.mysql.dw_db, "fact_order")
        expected = {"order_id", "customer_id", "product_id", "date_id", "region_id",
                    "order_quantity", "order_amount"}
        assert expected <= set(cols)
        assert "decimal" in cols["order_amount"]["type"].lower()
    finally:
        conn.close()


def test_dim_date_schema_matches_srs():
    conn = _admin_conn()
    try:
        with conn.cursor() as cur:
            cols = _describe(cur, cfg.mysql.dw_db, "dim_date")
        expected = {"date_id", "year", "quarter", "month", "day"}
        assert expected <= set(cols)
    finally:
        conn.close()


# ---------- readonly account + privilege enforcement ----------

def test_readonly_user_exists_with_mysql_native_password():
    conn = _admin_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT User, Host, plugin FROM mysql.user "
                "WHERE User=%s",
                (cfg.mysql.ro_user,),
            )
            row = cur.fetchone()
            assert row is not None, f"readonly user '{cfg.mysql.ro_user}' does not exist"
            user, host, plugin = row
            assert user == cfg.mysql.ro_user
            assert plugin in {"mysql_native_password", "caching_sha2_password"}
    finally:
        conn.close()


def test_readonly_can_select_from_dw_and_meta_metadata_tables():
    """SRS SEC-002: read-only target is dw + meta metadata; llm_call_log excluded."""
    conn = _readonly_conn()
    try:
        with conn.cursor() as cur:
            # dw read
            for tbl in ("fact_order", "dim_customer", "dim_product", "dim_region", "dim_date"):
                cur.execute(f"SELECT COUNT(*) FROM `{cfg.mysql.dw_db}`.`{tbl}`")
                cur.fetchone()
            # meta metadata read
            for tbl in ("table_info", "column_info", "metric_info", "column_metric"):
                cur.execute(f"SELECT COUNT(*) FROM `{cfg.mysql.meta_db}`.`{tbl}`")
                cur.fetchone()
    finally:
        conn.close()


def test_readonly_cannot_insert_into_dw():
    conn = _readonly_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"INSERT INTO `{cfg.mysql.dw_db}`.`dim_region` "
                "(region_id, province, region_name, country) "
                "VALUES ('R999', 'TEST', 'TEST', 'TEST')"
            )
        pytest.fail("readonly user should not be allowed to INSERT into dw.dim_region")
    except pymysql.err.OperationalError as e:
        assert e.args[0] in {1142, 1144}, f"unexpected error: {e}"  # 1142 SELECT denied, 1144 INSERT denied
    finally:
        conn.close()


def test_readonly_cannot_select_llm_call_log():
    """llm_call_log is admin-only audit data."""
    conn = _readonly_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT COUNT(*) FROM `{cfg.mysql.meta_db}`.`llm_call_log`"
            )
        pytest.fail("readonly user should not be allowed to SELECT llm_call_log")
    except pymysql.err.OperationalError as e:
        assert e.args[0] in {1142, 1144}, f"unexpected error: {e}"
    finally:
        conn.close()


# ---------- sample data sanity ----------

def test_dw_sample_row_counts_are_nonzero_and_sensible():
    conn = _admin_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) FROM `{cfg.mysql.dw_db}`.`fact_order`")
            orders = int(cur.fetchone()[0])
            cur.execute(f"SELECT COUNT(*) FROM `{cfg.mysql.dw_db}`.`dim_customer`")
            customers = int(cur.fetchone()[0])
            cur.execute(f"SELECT COUNT(*) FROM `{cfg.mysql.dw_db}`.`dim_product`")
            products = int(cur.fetchone()[0])
            cur.execute(f"SELECT COUNT(*) FROM `{cfg.mysql.dw_db}`.`dim_region`")
            regions = int(cur.fetchone()[0])
            cur.execute(f"SELECT COUNT(*) FROM `{cfg.mysql.dw_db}`.`dim_date`")
            dates = int(cur.fetchone()[0])
    finally:
        conn.close()
    assert orders >= 100, f"fact_order too small: {orders}"
    assert customers >= 10
    assert products >= 10
    assert regions >= 5
    # dim_date should cover a full year
    assert dates >= 365


def test_meta_metadata_describes_dw_tables():
    """Every dw table must appear in meta.table_info, and column_info rows must use
    the SRS-prescribed 'table_name.column_name' id format."""
    conn = _admin_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT id FROM `{cfg.mysql.meta_db}`.`table_info`"
            )
            table_ids = {row[0] for row in cur.fetchall()}
            # every dw table should be in table_info
            for t in ("fact_order", "dim_customer", "dim_product", "dim_region", "dim_date"):
                assert t in table_ids, f"{t} missing from meta.table_info"

            cur.execute(
                f"SELECT id, table_id FROM `{cfg.mysql.meta_db}`.`column_info` LIMIT 5"
            )
            sample = cur.fetchall()
            for col_id, table_id in sample:
                # id format must be table_name.column_name
                assert col_id.startswith(table_id + "."), (
                    f"column_info.id {col_id!r} does not start with table_id {table_id!r}."
                )
    finally:
        conn.close()


def test_metric_info_has_at_least_one_metric_and_aliases():
    conn = _admin_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT id, alias FROM `{cfg.mysql.meta_db}`.`metric_info`"
            )
            rows = cur.fetchall()
            assert len(rows) >= 1
            for metric_id, alias in rows:
                assert metric_id
                # alias is JSON-encoded list, must contain at least one entry
                if alias:
                    import json
                    parsed = json.loads(alias)
                    assert isinstance(parsed, list) and len(parsed) >= 1
    finally:
        conn.close()


# ---------- scripts are idempotent ----------

def test_init_scripts_are_idempotent():
    """Run both scripts a second time and confirm no error and no row growth."""
    conn = _admin_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) FROM `{cfg.mysql.dw_db}`.`fact_order`")
            before_orders = int(cur.fetchone()[0])
            cur.execute(f"SELECT COUNT(*) FROM `{cfg.mysql.meta_db}`.`column_info`")
            before_cols = int(cur.fetchone()[0])
    finally:
        conn.close()

    r1 = _run_script("init_meta_mysql.py")
    r2 = _run_script("init_dw_sample_data.py")
    assert r1.returncode == 0, r1.stderr
    assert r2.returncode == 0, r2.stderr

    conn = _admin_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) FROM `{cfg.mysql.dw_db}`.`fact_order`")
            after_orders = int(cur.fetchone()[0])
            cur.execute(f"SELECT COUNT(*) FROM `{cfg.mysql.meta_db}`.`column_info`")
            after_cols = int(cur.fetchone()[0])
    finally:
        conn.close()
    assert after_orders == before_orders
    assert after_cols == before_cols