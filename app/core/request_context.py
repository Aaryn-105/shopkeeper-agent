"""Request-scoped context variables (request_id)."""
from __future__ import annotations
from contextvars import ContextVar
from uuid import uuid4


REQUEST_ID: ContextVar[str] = ContextVar("request_id", default="-")


def new_request_id() -> str:
    return uuid4().hex


def set_request_id(value: str) -> None:
    REQUEST_ID.set(value or new_request_id())


def get_request_id() -> str:
    return REQUEST_ID.get()