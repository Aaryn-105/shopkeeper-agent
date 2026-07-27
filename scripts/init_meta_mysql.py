"""Initialize MySQL schema for shopkeeper-agent (idempotent).

Per SRS 6.2:
  - meta DB: table_info, column_info, metric_info, column_metric + llm_call_log
  - dw   DB: fact_order + dim_customer, dim_product, dim_region, dim_date

Reads credentials from app.core.config (OmegaConf + .env overlay):
  - cfg.mysql.admin_user / admin_password for DDL
  - cfg.mysql.ro_user / ro_password (default "readonly" / "readonly123") for SELECT

Usage:
  uv run python scripts/init_meta_mysql.py
"""
from __future__ import annotations
import sys
from pathlib import Path

import pymysql

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.config import cfg  # noqa: E402
from app.core.logger import setup_logger, logger  # noqa: E402


META_DDL: list[str] = [
    """
    CREATE TABLE IF NOT EXISTS table_info (
        id          VARCHAR(128) NOT NULL,
        name        VARCHAR(128) NOT NULL,
        role        VARCHAR(32)  NOT NULL,
        description TEXT,
        created_at  TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at  TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP
                                ON UPDATE CURRENT_TIMESTAMP,
        PRIMARY KEY (id),
        KEY idx_table_info_role (role)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS column_info (
        id          VARCHAR(256) NOT NULL,
        name        VARCHAR(128) NOT NULL,
        type        VARCHAR(64)  NOT NULL,
        role        VARCHAR(32)  NOT NULL,
        description TEXT,
        examples    TEXT,
        alias       TEXT,
        table_id    VARCHAR(128) NOT NULL,
        created_at  TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at  TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP
                                ON UPDATE CURRENT_TIMESTAMP,
        PRIMARY KEY (id),
        KEY idx_column_info_table (table_id),
        KEY idx_column_info_role  (role)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS metric_info (
        id              VARCHAR(64)  NOT NULL,
        name            VARCHAR(128) NOT NULL,
        description     TEXT,
        related_columns TEXT,
        alias           TEXT,
        created_at      TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at      TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP
                                    ON UPDATE CURRENT_TIMESTAMP,
        PRIMARY KEY (id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS column_metric (
        column_id  VARCHAR(256) NOT NULL,
        metric_id  VARCHAR(64)  NOT NULL,
        PRIMARY KEY (column_id, metric_id),
        KEY idx_column_metric_col (column_id),
        KEY idx_column_metric_met (metric_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS llm_call_log (
        id                BIGINT       NOT NULL AUTO_INCREMENT,
        request_id        VARCHAR(64)  NOT NULL,
        node_name         VARCHAR(64)  NOT NULL,
        model             VARCHAR(128) NOT NULL,
        prompt_tokens     INT          NOT NULL DEFAULT 0,
        completion_tokens INT          NOT NULL DEFAULT 0,
        total_tokens      INT          NOT NULL DEFAULT 0,
        latency_ms        INT          NOT NULL DEFAULT 0,
        cache_hit         TINYINT(1)   NOT NULL DEFAULT 0,
        created_at        TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (id),
        KEY idx_llm_request (request_id),
        KEY idx_llm_node    (node_name),
        KEY idx_llm_created (created_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
]

DW_DDL: list[str] = [
    """
    CREATE TABLE IF NOT EXISTS fact_order (
        order_id       VARCHAR(64)    NOT NULL,
        customer_id    VARCHAR(64)    NOT NULL,
        product_id     VARCHAR(64)    NOT NULL,
        date_id        INT            NOT NULL,
        region_id      VARCHAR(64)    NOT NULL,
        order_quantity INT            NOT NULL DEFAULT 0,
        order_amount   DECIMAL(10,2)  NOT NULL DEFAULT 0,
        PRIMARY KEY (order_id),
        KEY idx_fact_order_customer (customer_id),
        KEY idx_fact_order_product  (product_id),
        KEY idx_fact_order_date     (date_id),
        KEY idx_fact_order_region   (region_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS dim_customer (
        customer_id   VARCHAR(64)  NOT NULL,
        customer_name VARCHAR(128) NOT NULL,
        gender        VARCHAR(16),
        member_level  VARCHAR(32),
        PRIMARY KEY (customer_id),
        KEY idx_dim_customer_level (member_level)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS dim_product (
        product_id   VARCHAR(64)  NOT NULL,
        product_name VARCHAR(128) NOT NULL,
        category     VARCHAR(64),
        brand        VARCHAR(64),
        PRIMARY KEY (product_id),
        KEY idx_dim_product_cat (category),
        KEY idx_dim_product_br  (brand)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS dim_region (
        region_id   VARCHAR(64) NOT NULL,
        province    VARCHAR(64),
        region_name VARCHAR(64),
        country     VARCHAR(64),
        PRIMARY KEY (region_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS dim_date (
        date_id INT         NOT NULL,
        year    INT         NOT NULL,
        quarter VARCHAR(8)  NOT NULL,
        month   INT         NOT NULL,
        day     INT         NOT NULL,
        PRIMARY KEY (date_id),
        KEY idx_dim_date_year  (year),
        KEY idx_dim_date_month (month)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
]


def _connect(**overrides) -> pymysql.Connection:
    """Connect using admin creds (or overrides)."""
    return pymysql.connect(
        host=cfg.mysql.host,
        port=int(cfg.mysql.port),
        user=overrides.get("user", cfg.mysql.admin_user),
        password=overrides.get("password", cfg.mysql.admin_password),
        charset="utf8mb4",
        autocommit=False,
        connect_timeout=10,
    )


def _ensure_databases(cur) -> list[str]:
    created: list[str] = []
    for db in (cfg.mysql.meta_db, cfg.mysql.dw_db):
        cur.execute(
            f"CREATE DATABASE IF NOT EXISTS `{db}` "
            f"DEFAULT CHARACTER SET utf8mb4 DEFAULT COLLATE utf8mb4_unicode_ci"
        )
        created.append(db)
    return created


def _exec_ddl(cur, db: str, stmts: list[str]) -> list[str]:
    cur.execute(f"USE `{db}`")
    names: list[str] = []
    for stmt in stmts:
        cur.execute(stmt)
        head = stmt.split("EXISTS", 1)[1].strip().split("(", 1)[0].strip().strip("`")
        names.append(head)
    return names


def _ensure_readonly_user(cur) -> dict[str, str]:
    """Create the readonly user if missing and grant SELECT on dw + meta metadata tables."""
    ro_user = str(cfg.mysql.ro_user or "readonly")
    ro_password = str(cfg.mysql.ro_password or "readonly123")

    cur.execute(
        f"CREATE USER IF NOT EXISTS '{ro_user}'@'%' "
        f"IDENTIFIED BY '{ro_password}'"
    )

    meta_db = cfg.mysql.meta_db
    dw_db = cfg.mysql.dw_db

    # GRANT is idempotent: re-issuing the same grant is a no-op.
    cur.execute(f"GRANT SELECT ON `{dw_db}`.* TO '{ro_user}'@'%'")
    for tbl in ("table_info", "column_info", "metric_info", "column_metric"):
        cur.execute(
            f"GRANT SELECT ON `{meta_db}`.`{tbl}` TO '{ro_user}'@'%'"
        )
    # llm_call_log intentionally NOT granted - admin-only audit log

    cur.execute("FLUSH PRIVILEGES")
    return {"user": ro_user, "password": ro_password}


def _verify_readonly_can_connect(creds: dict[str, str]) -> str:
    try:
        conn = pymysql.connect(
            host=cfg.mysql.host,
            port=int(cfg.mysql.port),
            user=creds["user"],
            password=creds["password"],
            charset="utf8mb4",
            connect_timeout=5,
        )
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
            cur.fetchone()
        conn.close()
        return "ok"
    except Exception as e:
        return f"error: {type(e).__name__}: {e}"[:120]


def main() -> int:
    setup_logger(log_dir=cfg.logging.dir, level="INFO",
                 retention_days=int(cfg.logging.retention_days))
    logger.info(
        "init_meta_mysql start host={} port={} meta_db={} dw_db={}",
        cfg.mysql.host, cfg.mysql.port, cfg.mysql.meta_db, cfg.mysql.dw_db,
    )

    conn = _connect()
    try:
        with conn.cursor() as cur:
            dbs = _ensure_databases(cur)
            logger.info("databases ensured: {}", dbs)

            meta_tables = _exec_ddl(cur, cfg.mysql.meta_db, META_DDL)
            logger.info("meta tables ensured: {}", meta_tables)

            dw_tables = _exec_ddl(cur, cfg.mysql.dw_db, DW_DDL)
            logger.info("dw tables ensured: {}", dw_tables)

            ro_creds = _ensure_readonly_user(cur)
            logger.info("readonly user ensured: user={}", ro_creds["user"])

        conn.commit()
    except Exception:
        conn.rollback()
        logger.exception("init failed, rolled back")
        raise
    finally:
        conn.close()

    ro_status = _verify_readonly_can_connect(ro_creds)
    logger.info("readonly connectivity check: {}", ro_status)
    print(
        f"OK meta_ddl={len(META_DDL)} dw_ddl={len(DW_DDL)} "
        f"ro_user={ro_creds['user']} ro_connect={ro_status}"
    )
    return 0 if ro_status == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())