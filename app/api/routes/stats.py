"""GET /api/stats - \u7cfb\u7edf\u8fd0\u8425\u6307\u6807\u67e5\u8be2\u63a5\u53e3\uff08\u7528\u6237\u989d\u5916\u8981\u6c42 + OPS-009 / OPS-010\uff09\u3002

\u8fd4\u56de\u9762\u677f\u6240\u9700\u7684\u8fd0\u8425\u6307\u6807\uff0c\u9762\u5411\u524d\u7aef\u7684 `/stats` \u9875\u9762\uff1a
- tokens:           token \u6d88\u8017\uff08prompt / completion / total\uff09
- llm_calls:        \u5927\u6a21\u578b\u8c03\u7528\u6b21\u6570 + \u5e73\u5747\u8017\u65f6
- cache:            \u7f13\u5b58\u547d\u4e2d\u7387\uff08hits / misses / total / hit_rate\uff09
- requests:         OPS-009 \u95ee\u6570\u8bf7\u6c42\u91cf\u3001\u6210\u529f\u7387\u3001\u5e73\u5747\u8017\u65f6\u3001P95 \u8017\u65f6
- sql:              OPS-010 SQL \u751f\u6210\u7edf\u8ba1\uff08first_pass_rate / correction_rate / execution_success_rate\uff09
- node_p95_latency_ms: \u6bcf\u4e2a\u8282\u70b9\u7684 P95 \u8017\u65f6\uff08phase 2 \u5df2\u6709\uff09
- uptime_seconds:   \u8fdb\u7a0b\u8d77\u52a8\u65f6\u95f4

\u6240\u6709\u6570\u636e\u6765\u81ea app.state.metrics\uff0c\u8bfb\u53d6\u8fc7\u7a0b\u52a0\u9501\u3002
\u4e0d\u4f1a\u4fee\u6539\u8ba1\u6570\u5668\u4e5f\u4e0d\u4f1a\u89e6\u53d1\u4efb\u4f55\u4fa7\u6548\u5e94\uff08\u5e42\u7b49\u8bfb\u53d6\uff09\u3002
"""
from __future__ import annotations
from typing import Any

from fastapi import APIRouter, Query, Request

from app.core.metrics import get_metrics


router = APIRouter(prefix="/api", tags=["stats"])


@router.get("/stats")
async def get_stats(request: Request) -> dict[str, Any]:
    """Return a snapshot of the in-process metrics for the /stats dashboard.

    The snapshot is computed by Metrics.stats_snapshot() and includes the
    user-required counters (token usage, LLM call count, cache hit rate)
    plus the OPS-009 / OPS-010 observability data.
    """
    payload = get_metrics().stats_snapshot()
    rid = getattr(request.state, "request_id", None)
    if rid:
        payload["request_id"] = rid
    return payload

@router.get("/stats/timeseries")
async def get_stats_timeseries(
    request: Request,
    window: int = Query(default=600, ge=10, le=86400,
                        description="Window in seconds (10s - 24h)"),
) -> dict[str, Any]:
    """Return time-series points for the /stats dashboard charts.

    Phase 8 — used by the SVG Sparkline / Bar / Gauge charts on /stats.
    Backs onto Metrics.timeseries_snapshot which reads from the in-memory
    ring buffer (maxlen=2000, rate-limited bump on every record_* call).
    """
    points = get_metrics().timeseries_snapshot(window_seconds=window)
    payload: dict[str, Any] = {
        "window_seconds": window,
        "count": len(points),
        "points": points,
    }
    rid = getattr(request.state, "request_id", None)
    if rid:
        payload["request_id"] = rid
    return payload
