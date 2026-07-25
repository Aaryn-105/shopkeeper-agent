"""Loguru-based logger with request_id propagation."""
from __future__ import annotations
import sys
from pathlib import Path
from loguru import logger as _logger
from app.core.request_context import REQUEST_ID


def _patch(record):
    record["extra"]["request_id"] = REQUEST_ID.get()
    return record


def setup_logger(log_dir: str, level: str = "INFO", retention_days: int = 30) -> None:
    """Configure loguru: console + rotating daily file."""
    _logger.remove()
    _logger.configure(patcher=_patch)
    _logger.add(
        sys.stderr,
        level=level,
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level:<7}</level> | <cyan>{extra[request_id]}</cyan> | <magenta>{name}:{function}:{line}</magenta> - <level>{message}</level>",
    )
    Path(log_dir).mkdir(parents=True, exist_ok=True)
    _logger.add(
        f"{log_dir}/app_{{time:YYYY-MM-DD}}.log",
        level="DEBUG",
        rotation="00:00",
        retention=f"{retention_days} days",
        enqueue=True,
        encoding="utf-8",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level:<7} | {extra[request_id]} | {name}:{function}:{line} - {message}",
    )


logger = _logger