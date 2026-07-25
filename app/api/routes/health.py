"""GET /api/health — minimal service status probe."""
from __future__ import annotations
from datetime import datetime, timezone
from fastapi import APIRouter


router = APIRouter(prefix="/api", tags=["health"])


@router.get("/health")
async def health():
    return {
        "status": "healthy",
        "services": {"mysql": "pending", "embedding": "pending", "llm": "pending"},
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }