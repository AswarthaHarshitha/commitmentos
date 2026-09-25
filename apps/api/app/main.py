"""FastAPI application factory."""

from __future__ import annotations

import logging
import re
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, OperationalError

from app import __version__
from app.clock import clock
from app.config import get_settings
from app.db import get_engine, get_sessionmaker
from app.errors import install_error_handlers
from app.logging_config import configure_logging
from app.routers import actions, approvals, auth, candidates, feeds, internal, messages, obligations, webhooks
from app.services import testclock

log = logging.getLogger("commitmentos")

MAX_BODY_BYTES = 1_000_000  # ingestion payloads are text; nothing legitimate is larger than 1 MB
_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{8,64}$")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    if settings.is_production:
        settings.assert_production_safe()
    log.info("CommitmentOS API starting (env=%s, llm=%s)", settings.app_env, settings.llm_provider)
    if settings.test_clock:
        with get_sessionmaker()() as db:
            testclock.restore_clock(db)
        log.warning("TEST_CLOCK is on (offset %ds): this must never run next to real data", int(clock.offset.total_seconds()))
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level, json_logs=settings.app_env != "test")

    app = FastAPI(
        title="CommitmentOS API",
        version=__version__,
        description=(
            "Commitment detection + obligation lifecycle automation.\n\n"
            "Authenticate with `POST /api/auth/login` (httpOnly cookie) or `POST /api/auth/token` (bearer)."
        ),
        lifespan=lifespan,
        docs_url="/api/docs",
        redoc_url="/api/redoc",
        openapi_url="/api/openapi.json",
    )
    install_error_handlers(app)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "Authorization"],
    )

    @app.middleware("http")
    async def request_context(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        started = time.perf_counter()
        incoming = request.headers.get("x-request-id", "")
        request_id = incoming if _REQUEST_ID.match(incoming) else uuid.uuid4().hex
        length = request.headers.get("content-length")
        if length and length.isdigit() and int(length) > MAX_BODY_BYTES:
            response: Response = JSONResponse(
                status_code=413, content={"detail": "Request body too large", "code": "PAYLOAD_TOO_LARGE"}
            )
        else:
            try:
                response = await call_next(request)
            except Exception:  # noqa: BLE001 - last-resort guard: never leak internals
                log.exception("unhandled error", extra={"request_id": request_id, "path": request.url.path})
                response = JSONResponse(status_code=500, content={"detail": "Internal server error", "code": "INTERNAL_ERROR"})
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        if request.url.path.startswith("/api/") and not request.url.path.startswith("/api/docs"):
            response.headers["Cache-Control"] = "no-store"
        # path only: query strings / bodies can carry tokens or message content
        log.info(
            "request",
            extra={
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code,
                "ms": round((time.perf_counter() - started) * 1000, 1),
            },
        )
        return response

    @app.exception_handler(DBAPIError)
    async def db_error(request: Request, exc: DBAPIError) -> JSONResponse:
        # Log the error *type* only: the statement/params can contain message content.
        log.error("database error on %s: %s", request.url.path, type(exc.orig).__name__ if exc.orig else type(exc).__name__)
        return JSONResponse(
            status_code=503,
            content={"detail": "The database is temporarily unavailable. Please retry shortly.", "code": "DATABASE_UNAVAILABLE"},
            headers={"Retry-After": "5"},
        )

    @app.get("/api/health", tags=["system"])
    def health() -> JSONResponse:
        try:
            with get_engine().connect() as conn:
                conn.execute(text("SELECT 1"))
        except (OperationalError, DBAPIError):
            return JSONResponse(status_code=503, content={"status": "degraded", "db": "down", "version": __version__})
        return JSONResponse(content={"status": "ok", "db": "ok", "version": __version__})

    for module in (auth, obligations, candidates, approvals, messages, feeds, webhooks, internal, actions):
        app.include_router(module.router)
    return app


app = create_app()
