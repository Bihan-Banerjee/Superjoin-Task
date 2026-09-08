"""Application entry point."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api import cases, documents, evaluation, facts, jobs, relations
from app.config import get_settings
from app.db.engine import init_database

logger = logging.getLogger(__name__)


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    # These are noisy at INFO and say nothing useful about the pipeline.
    for noisy in ("httpx", "httpcore", "urllib3", "onnxruntime"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings.log_level)
    settings.ensure_directories()
    init_database()
    for warning in settings.throttle_warnings():
        logger.warning(warning)
    graph_warning = settings.graph_view_warning()
    if graph_warning:
        logger.warning(graph_warning)
    logger.info("knowledge layer ready at %s", settings.sqlalchemy_url)
    yield


app = FastAPI(
    title="Fact Knowledge Layer",
    version="0.1.0",
    description=(
        "Extracts facts from PDFs, grounds each one in verifiable evidence, and reconciles "
        "them across documents."
    ),
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origin_list,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

for router in (
    documents.router,
    facts.router,
    relations.router,
    cases.router,
    jobs.router,
    evaluation.router,
):
    app.include_router(router)


@app.exception_handler(ValueError)
async def value_error_handler(_request: Request, error: ValueError) -> JSONResponse:
    return JSONResponse(status_code=400, content={"detail": str(error)})


@app.get("/api/health", tags=["meta"])
def health() -> dict[str, Any]:
    """Readiness, plus whether a model is configured.

    The interface uses this to tell the difference between an empty knowledge layer and one
    that cannot ingest because no provider is set up — two situations that otherwise look
    identical from the outside.
    """
    settings = get_settings()
    configured = {
        "gemini": bool(settings.gemini_api_key),
        "openrouter": bool(settings.openrouter_api_key),
        "ollama": True,
        "replay": True,
    }
    provider = settings.llm_provider.lower()
    return {
        "status": "ok",
        "provider": provider,
        "provider_configured": configured.get(provider, False),
        "fallback_provider": settings.llm_fallback_provider or None,
        "vision_enabled": settings.enable_vision,
        "embedding_model": settings.embedding_model,
        "cache_enabled": settings.llm_cache_enabled,
        "concurrency": settings.llm_concurrency,
        "rate_limit_rpm": settings.llm_rate_limit_rpm,
        "throttle_warnings": settings.throttle_warnings(),
        "graph_view_enabled": settings.enable_graph_view,
        "graph_view_warning": settings.graph_view_warning(),
    }
