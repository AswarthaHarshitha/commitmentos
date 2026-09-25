"""Domain errors -> uniform JSON: {"detail": "<message>", "code": "<MACHINE_CODE>"}."""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


class DomainError(Exception):
    status_code = 400
    code = "BAD_REQUEST"

    def __init__(self, message: str, *, code: str | None = None, extra: dict | None = None) -> None:
        super().__init__(message)
        self.message = message
        if code:
            self.code = code
        self.extra = extra or {}


class UnauthorizedError(DomainError):
    status_code = 401
    code = "UNAUTHORIZED"


class ForbiddenError(DomainError):
    status_code = 403
    code = "FORBIDDEN"


class NotFoundError(DomainError):
    status_code = 404
    code = "NOT_FOUND"


class ConflictError(DomainError):
    status_code = 409
    code = "CONFLICT"


class InvalidTransition(ConflictError):
    code = "INVALID_TRANSITION"


class ValidationFailed(DomainError):
    status_code = 422
    code = "VALIDATION_FAILED"


class RateLimited(DomainError):
    status_code = 429
    code = "RATE_LIMITED"


class ServiceUnavailable(DomainError):
    status_code = 503
    code = "SERVICE_UNAVAILABLE"


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(DomainError)
    async def _domain(_: Request, exc: DomainError) -> JSONResponse:
        headers = {"Retry-After": str(exc.extra["retry_after"])} if "retry_after" in exc.extra else None
        body = {"detail": exc.message, "code": exc.code, **{k: v for k, v in exc.extra.items() if k != "retry_after"}}
        return JSONResponse(status_code=exc.status_code, content=body, headers=headers)
