"""FastAPI lifespan: startup/shutdown hooks for services and logger."""
from __future__ import annotations
from contextlib import asynccontextmanager
from fastapi import FastAPI
from app.core.config import cfg
from app.core.logger import setup_logger, logger


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logger(
        log_dir=cfg.logging.dir,
        level=cfg.logging.level,
        retention_days=int(cfg.logging.retention_days),
    )
    logger.info("startup: env={} name={} version={}", cfg.app.env, cfg.app.name, cfg.app.version)
    yield
    logger.info("shutdown: connections closed")


def install_request_id_middleware(app: FastAPI) -> None:
    """Attach request_id middleware (X-Request-ID header in/out)."""
    from starlette.middleware.base import BaseHTTPMiddleware
    from app.core.request_context import set_request_id, get_request_id

    class RequestIdMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            incoming = request.headers.get(cfg.request.id_header.lower(), "")
            set_request_id(incoming or "")
            response = await call_next(request)
            response.headers[cfg.request.id_header] = get_request_id()
            return response

    app.add_middleware(RequestIdMiddleware)