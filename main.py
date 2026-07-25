"""Shopkeeper Agent — FastAPI entrypoint (Phase 1 skeleton)."""
from __future__ import annotations
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.core.config import cfg
from app.core.lifespan import lifespan, install_request_id_middleware
from app.api.routes.health import router as health_router


app = FastAPI(
    title=cfg.app.name,
    version=cfg.app.version,
    lifespan=lifespan,
)

install_request_id_middleware(app)

app.add_middleware(
    CORSMiddleware,
    allow_origins=cfg.cors.allow_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health_router)


@app.get("/")
async def root():
    return {"name": cfg.app.name, "version": cfg.app.version, "env": cfg.app.env}