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
    headers: dict[str, str] | None = None  # extra response headers (e.g. Retry-After)

    def __init__(self, message: str | None = None, details: Any = None) -> None:
        self.message = message or self.default_message
        self.details = details
        super().__init__(self.message)

    def body(self) -> dict[str, Any]:
        """JSON body of the response (the standard error envelope unless overridden)."""
        return envelope(self.code, self.message, self.details)


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


class Gone(AppError):
    status_code = 410
    code = "gone"
    default_message = "This endpoint has been removed"


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


class GuardrailDenied(Forbidden):
    """A guardrail rule (or the fail-closed default) forbids this action (403)."""

    code = "guardrail_denied"
    default_message = "A guardrail forbids this action"


class RateLimited(AppError):
    status_code = 429
    code = "rate_limited"
    default_message = "Rate limit exceeded"

    def __init__(self, message: str | None = None, details: Any = None) -> None:
        super().__init__(message, details)
        wait = details.get("retry_after_seconds") if isinstance(details, dict) else None
        if isinstance(wait, int | float) and wait > 0:
            self.headers = {"Retry-After": str(int(wait))}


# ---- Milestone 7: accounts
class InvalidCredentials(Unauthenticated):
    """Wrong username/password/code. Always the same message: never says which part was wrong."""

    code = "invalid_credentials"
    default_message = "Incorrect username or password"


class CsrfFailed(Forbidden):
    code = "csrf_failed"
    default_message = "The CSRF token is missing or does not match; reload the page and retry"


class SetupComplete(Conflict):
    code = "setup_complete"
    default_message = "Setup is already complete"


class SetupTokenRequired(Forbidden):
    code = "setup_token_required"
    default_message = (
        "Enter the setup token printed in the terminal where the backend runs "
        "(it is also in data/owner.token)"
    )


class InvalidSetupToken(Forbidden):
    code = "invalid_setup_token"
    default_message = "That setup token is not valid"


class EmergencyStopped(AppError):
    """The instance-wide emergency stop is active: nothing runs until the owner resumes."""

    status_code = 503
    code = "emergency_stop"
    default_message = "The emergency stop is active; nothing runs until the owner resumes"


class NotAvailable(AppError):
    """A subsystem that is not built / not connected yet (never faked)."""

    status_code = 501
    code = "not_available"
    default_message = "Not available yet"

    def __init__(self, message: str | None = None, *, milestone: str, details: Any = None) -> None:
        super().__init__(message, {"available": False, "milestone": milestone, **(details or {})})


class ApprovalPending(AppError):
    """The action waits for an approval or a peer review (HTTP 202, not an error envelope): the
    agent should finish what it can and repeat the identical action once it is woken."""

    status_code = 202
    code = "pending_approval"
    default_message = "The action waits for a review"

    def __init__(self, message: str | None = None, **fields: Any) -> None:
        super().__init__(message, fields)
        self.fields = fields

    def body(self) -> dict[str, Any]:
        return {"status": "pending_approval", "message": self.message, **self.fields}


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
        return JSONResponse(status_code=exc.status_code, content=exc.body(), headers=exc.headers)

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


# ---- Milestone 10: AI setup
class AiSetupRequired(Conflict):
    """The company has no working AI yet: nothing that needs an AI may be created or run."""

    code = "ai_setup_required"
    default_message = (
        "Connect a working AI model first: Foundry AI OS cannot run without one (Settings > AI)"
    )


class AiNotReady(Conflict):
    """The chosen AI configuration did not pass its checks (``details.reason`` says why)."""

    code = "ai_not_ready"
    default_message = "That AI configuration is not ready"
