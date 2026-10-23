"""Application errors and their JSON envelope: ``{"error": {code, message, details}}``."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger("company_os.errors")


class AppError(Exception):
    status_code = 500
    code = "internal_error"
    default_message = "Internal server error"

    def __init__(self, message: str | None = None, details: Any = None) -> None:
        self.message = message or self.default_message
        self.details = details
        super().__init__(self.message)


class BadRequest(AppError):
    status_code = 400
    code = "bad_request"
    default_message = "Bad request"


class Unauthenticated(AppError):
    status_code = 401
    code = "unauthenticated"
    default_message = "Authentication required"


class Forbidden(AppError):
    status_code = 403
    code = "forbidden"
    default_message = "You do not have permission to do this"


class NotFound(AppError):
    status_code = 404
    code = "not_found"
    default_message = "Not found"


class Conflict(AppError):
    status_code = 409
    code = "conflict"
    default_message = "Conflict"


class InvalidTransition(Conflict):
    code = "invalid_transition"
    default_message = "Invalid state transition"


class LimitExceeded(Conflict):
    """A configured hard limit (delegation depth, tasks per cycle, ...) would be exceeded."""

    code = "limit_exceeded"
    default_message = "A configured limit would be exceeded"


class BudgetExceeded(Conflict):
    code = "budget_exceeded"
    default_message = "The budget does not allow this"


class PayloadTooLarge(AppError):
    status_code = 413
    code = "payload_too_large"
    default_message = "Payload too large"


class ValidationFailed(AppError):
    """Business-rule validation failure raised from services (422)."""

    status_code = 422
    code = "validation_error"
    default_message = "Validation failed"


class LicenseRefused(AppError):
    """A skill's license is missing or not on the allowlist."""

    status_code = 422
    code = "license_not_allowed"
    default_message = "The skill's license is not allowed"


class ScannerUnavailable(AppError):
    """Non-builtin skills cannot be imported without the runtime's security scanner."""

    status_code = 503
    code = "scanner_unavailable"
    default_message = "The skill security scanner is not available"


class UpstreamError(AppError):
    """An external source (GitHub) failed."""

    status_code = 502
    code = "upstream_error"
    default_message = "The upstream source failed"


def envelope(code: str, message: str, details: Any = None) -> dict[str, Any]:
    return {"error": {"code": code, "message": message, "details": details}}


def _error_response(status: int, code: str, message: str, details: Any = None) -> JSONResponse:
    return JSONResponse(status_code=status, content=envelope(code, message, details))


_HTTP_CODES = {
    400: "bad_request",
    401: "unauthenticated",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    413: "payload_too_large",
}


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError) -> JSONResponse:
        return _error_response(exc.status_code, exc.code, exc.message, exc.details)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        # Only loc + msg: pydantic's `input`/`ctx` could echo submitted values back.
        details = [
            {"loc": list(e.get("loc", ())), "msg": str(e.get("msg", ""))} for e in exc.errors()
        ]
        return _error_response(422, "validation_error", "Request validation failed", details)

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        fallback = "bad_request" if exc.status_code < 500 else "internal_error"
        code = _HTTP_CODES.get(exc.status_code, fallback)
        message = "Not found" if exc.status_code == 404 else str(exc.detail)
        return _error_response(exc.status_code, code, message)

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled error on %s %s", request.method, request.url.path)
        return _error_response(500, "internal_error", "Internal server error")
