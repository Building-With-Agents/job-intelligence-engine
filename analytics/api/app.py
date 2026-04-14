"""FastAPI application for Week 8 analytics Q&A and triggers."""

from __future__ import annotations

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from analytics.api.routes import router as analytics_router

log = structlog.get_logger()


def create_app() -> FastAPI:
    app = FastAPI(
        title="Job Intelligence — Analytics API",
        version="0.1.0",
        description="Workforce Q&A (POST /analytics/query) and on-demand triggers with SQL guardrails.",
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

    app.include_router(analytics_router)
    return app


app = create_app()
