"""GET /api/health — surfaces lifespan probes + metrics summary."""
from __future__ import annotations
from datetime import datetime, timezone
from fastapi import APIRouter, Request


router = APIRouter(prefix="/api", tags=["health"])


@router.get("/health")
async def health(request: Request):
    probes = getattr(request.app.state, "probes", {}) or {}
    services = {
        "mysql": _join_pair(probes.get("mysql_admin"), probes.get("mysql_ro")),
        "faiss": probes.get("faiss", "unknown"),
        "embedding": probes.get("embedding", "unknown"),
        "fts5_or_es": _join_pair(probes.get("fts5_or_es")),
        "llm": probes.get("llm", "unknown"),
    }
    metrics = getattr(request.app.state, "metrics", None)
    summary = metrics.summary() if metrics is not None else {}
    healthy = all(_is_ok(v) for v in services.values())
    return {
        "status": "healthy" if healthy else "degraded",
        "services": services,
        "metrics": summary,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def _join_pair(*values: str) -> str:
    """Combine two probe results (admin + ro) into one string."""
    values = [v for v in values if v]
    if not values:
        return "unknown"
    if len(values) == 1:
        return values[0]
    return " | ".join(values)


def _is_ok(status: str) -> bool:
    """A service is considered ok if status starts with 'ok' or 'pending'."""
    if not status:
        return False
    s = status.lower()
    return s.startswith("ok") or s.startswith("pending")