"""FastAPI application for Week 8 analytics Q&A and triggers."""

from __future__ import annotations

import contextlib
import logging

import structlog
import structlog.contextvars as scv
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from analytics.api.routes import router as analytics_router

log = structlog.get_logger()

_STRUCTLOG_ANALYTICS_API_CONFIGURED = False


def _configure_structlog_for_analytics_api() -> None:
    """Bind ``merge_contextvars`` so ``X-Request-Id`` and LaborPulse headers flow into logs (JIE #222)."""
    global _STRUCTLOG_ANALYTICS_API_CONFIGURED
    if _STRUCTLOG_ANALYTICS_API_CONFIGURED:
        return
    with contextlib.suppress(Exception):
        structlog.configure(
            processors=[
                structlog.contextvars.merge_contextvars,
                structlog.processors.add_log_level,
                structlog.processors.TimeStamper(fmt="iso", utc=True),
                structlog.dev.ConsoleRenderer(colors=False),
            ],
            wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
            cache_logger_on_first_use=True,
        )
    _STRUCTLOG_ANALYTICS_API_CONFIGURED = True


def create_app() -> FastAPI:
    _configure_structlog_for_analytics_api()
    app = FastAPI(
        title="Job Intelligence — Analytics API",
        version="0.1.0",
        description="Workforce Q&A (POST /analytics/query) and on-demand triggers with SQL guardrails.",
        docs_url="/docs",
        redoc_url="/redoc",
    )

    @app.exception_handler(StarletteHTTPException)
    async def http_exc_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

    @app.exception_handler(RequestValidationError)
    async def validation_exc_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": exc.errors()})

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        log.exception("analytics_api_unhandled", path=str(request.url))
        return JSONResponse(status_code=500, content={"detail": "Internal server error"})

    @app.middleware("http")
    async def clear_request_contextvars(request: Request, call_next):
        scv.clear_contextvars()
        try:
            return await call_next(request)
        finally:
            scv.clear_contextvars()

    app.include_router(analytics_router)
    return app


app = create_app()
