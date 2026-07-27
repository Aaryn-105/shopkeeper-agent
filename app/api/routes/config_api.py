"""\u4f60\u597d\uff0c\u6211\u662f\u7535\u5546\u95ee\u6570\u52a9\u624b\u3002GET /api/config - \u7cfb\u7edf\u914d\u7f6e\u67e5\u8be2\u63a5\u53e3 (SRS 4.3.4)\u3002

\u8fd4\u56de\u524d\u7aef\u521d\u59cb\u5316\u6240\u9700\u7684\u914d\u7f6e\u4fe1\u606f\uff1a
- app: \u7cfb\u7edf\u540d\u79f0\u3001\u7248\u672c\u3001\u73af\u5883
- ui:  \u6b22\u8fce\u8bed\u3001\u4f7f\u7528\u8bf4\u660e
- samples: \u63a8\u8350\u6837\u4f8b\u95ee\u9898\uff08\u81f3\u5c11 3-5 \u4e2a\uff0c\u8986\u76d6\u4e0d\u540c\u573a\u666f\uff09

\u6240\u6709\u5185\u5bb9\u53d6\u81ea conf/*.yaml\uff0c\u4fee\u6539\u914d\u7f6e\u6587\u4ef6\u5373\u53ef\u8c03\u6574\uff0c\u65e0\u9700\u6539\u4ee3\u7801
\uff08\u5bf9\u5e94 SRS 4.3.4 \u4e1a\u52a1\u89c4\u5219 4\uff09\u3002
"""
from __future__ import annotations
from typing import Any

from fastapi import APIRouter, Request

from app.core.config import cfg


router = APIRouter(prefix="/api", tags=["config"])


def _build_config_view() -> dict[str, Any]:
    """Build the JSON payload served by GET /api/config.

    Pulled from cfg directly (no hard-coded strings) so that editing
    conf/default.yaml is the only step needed to update welcome / tips /
    samples. Falls back gracefully if the samples section is absent.
    """
    samples_cfg = getattr(cfg, "samples", None)
    welcome = getattr(samples_cfg, "welcome_message", "") if samples_cfg is not None else ""
    tips = list(getattr(samples_cfg, "usage_tips", []) or []) if samples_cfg is not None else []
    questions = list(getattr(samples_cfg, "sample_questions", []) or []) if samples_cfg is not None else []

    samples_out: list[dict[str, Any]] = []
    for q in questions:
        samples_out.append({
            "id": getattr(q, "id", "") or "",
            "category": getattr(q, "category", "") or "",
            "question": getattr(q, "question", "") or "",
            "description": getattr(q, "description", "") or "",
        })

    return {
        "app": {
            "name": cfg.app.name,
            "version": cfg.app.version,
            "env": cfg.app.env,
        },
        "ui": {
            "welcome_message": welcome,
            "usage_tips": tips,
        },
        "samples": samples_out,
    }


@router.get("/config")
async def get_config(request: Request) -> dict[str, Any]:
    """Return system config (name + version, welcome + tips, sample questions)."""
    payload = _build_config_view()
    rid = getattr(request.state, "request_id", None)
    if rid:
        payload["request_id"] = rid
    return payload