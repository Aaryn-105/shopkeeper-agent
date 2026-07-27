"""FastAPI lifespan: service probes, connection pool, periodic metrics flush.

Startup order:
  1. configure loguru (logger.py)
  2. probe mysql admin + readonly connections
  3. probe faiss index dir
  4. probe embedding model path (lazy load deferred to first request)
  5. probe ES or FTS5 backend
  6. probe LLM config presence
  7. install metrics on app.state and start periodic flush task

Shutdown: cancel flush task, run final flush, log summary.
"""
from __future__ import annotations
import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI
from app.core.config import cfg
from app.core.logger import setup_logger, logger
from app.core.metrics import get_metrics, metrics_periodic_flush


def _probe_mysql(role: str, user: str, password: str) -> str:
    """Try to connect to MySQL using SQLAlchemy. Returns 'ok' or 'error: ...'."""
    try:
        from sqlalchemy import create_engine, text
        url = f"mysql+pymysql://{user}:{password}@{cfg.mysql.host}:{cfg.mysql.port}"
        engine = create_engine(url, pool_pre_ping=True, pool_recycle=int(cfg.mysql.pool_recycle))
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        engine.dispose()
        return "ok"
    except Exception as e:
        return f"error: {type(e).__name__}: {str(e)[:120]}"


def _probe_faiss() -> str:
    try:
        p = Path(cfg.faiss.index_dir)
        p.parent.mkdir(parents=True, exist_ok=True)
        return "ok" if p.exists() else f"error: dir not creatable ({p})"
    except Exception as e:
        return f"error: {type(e).__name__}: {e}"


def _probe_embedding() -> str:
    """Defer actual model load to first request; here just verify the path."""
    p = Path(str(cfg.embedding.model_path))
    return "ok" if p.exists() else f"error: model path missing ({p})"


def _probe_fts5_or_es() -> str:
    """Return a single status string: 'ok(es)', 'ok(fts5)', or 'error: ...'."""
    try:
        import httpx
        r = httpx.get(f"{cfg.es.url}/_cluster/health", timeout=2.0)
        if r.status_code == 200:
            return "ok(es)"
    except Exception:
        pass
    try:
        p = Path(str(cfg.fts5.db_path))
        p.parent.mkdir(parents=True, exist_ok=True)
        import sqlite3
        conn = sqlite3.connect(str(p))
        conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS _probe USING fts5(v)")
        conn.execute("DROP TABLE _probe")
        conn.close()
        return "ok(fts5)"
    except Exception as e:
        return f"error: fts5 {type(e).__name__}: {e}"


def _probe_llm() -> str:
    if cfg.llm.api_base and cfg.llm.api_key and cfg.llm.model:
        return "ok"
    return "pending: LLM_API_BASE/LLM_API_KEY/LLM_MODEL not configured"


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 1. logger
    setup_logger(
        log_dir=cfg.logging.dir,
        level=cfg.logging.level,
        retention_days=int(cfg.logging.retention_days),
    )
    logger.info(
        "startup: env={} name={} version={}",
        cfg.app.env, cfg.app.name, cfg.app.version,
    )

    # 2-6. service probes
    app.state.probes = {
        "mysql_admin": _probe_mysql(
            "admin",
            str(cfg.mysql.admin_user),
            str(cfg.mysql.admin_password),
        ),
        "mysql_ro": _probe_mysql(
            "readonly",
            str(cfg.mysql.ro_user),
            str(cfg.mysql.ro_password),
        ),
        "faiss": _probe_faiss(),
        "embedding": _probe_embedding(),
        "fts5_or_es": _probe_fts5_or_es(),
        "llm": _probe_llm(),
    }
    for name, status in app.state.probes.items():
        logger.info("probe {}: {}", name, status)

    # 7. metrics + periodic flush
    app.state.metrics = get_metrics()
    metrics_path = Path(cfg.logging.dir) / "metrics.jsonl"
    flush_task = asyncio.create_task(
        metrics_periodic_flush(metrics_path, interval_seconds=30.0),
        name="metrics-flush",
    )
    logger.info("metrics flush task scheduled -> {}", metrics_path)

    try:
        yield
    finally:
        flush_task.cancel()
        try:
            await flush_task
        except asyncio.CancelledError:
            pass
        get_metrics().dump_jsonl(metrics_path)
        s = get_metrics().summary()
        logger.info(
            "shutdown: requests={} llm_calls={} cache_hit_rate={}",
            s["requests_total"], s["llm"]["calls"], s["cache"]["hit_rate"],
        )


def install_request_id_middleware(app: FastAPI) -> None:
    """Attach X-Request-ID middleware and record per-request duration."""
    from starlette.middleware.base import BaseHTTPMiddleware
    from app.core.request_context import set_request_id, get_request_id
    import time

    class RequestIdMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            incoming = request.headers.get(cfg.request.id_header.lower(), "")
            set_request_id(incoming or "")
            t0 = time.perf_counter()
            response = await call_next(request)
            duration_ms = (time.perf_counter() - t0) * 1000.0
            metrics = getattr(app.state, "metrics", None)
            if metrics is not None:
                metrics.record_request()
                metrics.record_node_latency(f"http:{request.url.path}", duration_ms)
            response.headers[cfg.request.id_header] = get_request_id()
            return response

    app.add_middleware(RequestIdMiddleware)