"""Shared plumbing for provider adapters.

Every adapter is a small client class over an injectable ``httpx.Client`` (tests hand it an
``httpx.MockTransport`` fed with recorded fixtures; nothing in the test suite touches the network).
Secrets are passed to a client's constructor for the lifetime of one operation and are never put
into error messages: ``ApiClient._fail`` builds the message from the provider's own error text
after masking every secret the client holds.
"""

from __future__ import annotations

import threading
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

import httpx

from app.core.errors import AppError
from app.services.secrets_scan import scrub

DEFAULT_TIMEOUT = 20.0
MAX_ERROR_CHARS = 300
USER_AGENT = "company-os/0.1 (+https://github.com/company-os)"


class ProviderError(AppError):
    """A provider answered with an error, or could not be reached (502)."""

    status_code = 502
    code = "provider_error"
    default_message = "The provider returned an error"

    def __init__(self, message: str | None = None, details: Any = None) -> None:
        super().__init__(message, details)


class ProviderAuthError(ProviderError):
    """The provider rejected the credential (revoked, wrong scope, expired)."""

    code = "provider_auth_failed"
    default_message = "The provider rejected the credential"


class ProviderNotConfigured(ProviderError):
    """A prerequisite the Founder must provide is missing (an account id, a binary ...)."""

    status_code = 409
    code = "provider_not_configured"
    default_message = "The provider is not fully configured"


class IntegrationHttp:
    """Builds ``httpx.Client`` objects. ``transport`` is set by tests (``httpx.MockTransport``)."""

    def __init__(self, transport: httpx.BaseTransport | None = None) -> None:
        self.transport = transport
        self._lock = threading.Lock()

    def client(self, *, timeout: float = DEFAULT_TIMEOUT, follow_redirects: bool = False):
        return httpx.Client(
            transport=self.transport,
            timeout=timeout,
            follow_redirects=follow_redirects,
            headers={"User-Agent": USER_AGENT},
        )


class ApiClient:
    """JSON-over-HTTP base. Subclasses set ``provider`` and ``base_url``."""

    provider = "provider"
    base_url = ""

    def __init__(self, http: httpx.Client, *secrets: str | None) -> None:
        self.http = http
        self._secrets = tuple(s for s in secrets if s)

    # -- hooks
    def auth_headers(self) -> dict[str, str]:
        return {}

    # -- plumbing
    def _mask(self, text: str) -> str:
        for secret in self._secrets:
            if secret and secret in text:
                text = text.replace(secret, "[redacted]")
        return scrub(text) or ""

    def _fail(self, status: int | None, text: str, what: str) -> ProviderError:
        message = f"{self.provider}: {what} failed"
        if status is not None:
            message += f" (HTTP {status})"
        detail = self._mask(text.strip().replace("\n", " "))[:MAX_ERROR_CHARS]
        if detail:
            message += f": {detail}"
        details = {"provider": self.provider, "status": status}
        if status in (401, 403):
            return ProviderAuthError(message, details)
        return ProviderError(message, details)

    def request(
        self,
        method: str,
        path: str,
        *,
        what: str,
        json: Any = None,
        params: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        absolute: bool = False,
    ) -> httpx.Response:
        url = path if absolute else self.base_url.rstrip("/") + "/" + path.lstrip("/")
        merged = {**self.auth_headers(), **(headers or {})}
        try:
            resp = self.http.request(
                method, url, json=json, params=params, data=data, headers=merged
            )
        except httpx.HTTPError as exc:
            raise self._fail(None, type(exc).__name__ + ": " + str(exc), what) from exc
        if resp.status_code >= 400:
            raise self._fail(resp.status_code, self.error_text(resp), what)
        return resp

    def error_text(self, resp: httpx.Response) -> str:
        """The provider's own error message when the body is JSON, else a short slice of text."""
        try:
            body = resp.json()
        except ValueError:
            return resp.text[:MAX_ERROR_CHARS]
        if isinstance(body, dict):
            errors = body.get("errors")
            if isinstance(errors, list) and errors:
                return "; ".join(
                    str(e.get("message", e)) if isinstance(e, dict) else str(e) for e in errors[:3]
                )
            for key in ("message", "error", "detail", "name"):
                value = body.get(key)
                if isinstance(value, str):
                    return value
                if isinstance(value, dict) and isinstance(value.get("message"), str):
                    return value["message"]
        return resp.text[:MAX_ERROR_CHARS]

    def json(self, method: str, path: str, *, what: str, **kwargs: Any) -> Any:
        resp = self.request(method, path, what=what, **kwargs)
        if not resp.content:
            return {}
        try:
            return resp.json()
        except ValueError as exc:
            raise self._fail(resp.status_code, "response was not JSON", what) from exc


def usd_to_micros(value: Any) -> int | None:
    """``"9.73"`` -> 9_730_000. None for anything that is not a non-negative number."""
    try:
        amount = Decimal(str(value).strip().lstrip("$"))
    except Exception:
        return None
    if amount < 0:
        return None
    return int((amount * 1_000_000).to_integral_value(rounding=ROUND_HALF_UP))


def micros_to_cents(micros: int) -> int:
    """Whole cents; a price that is not a whole number of cents is rounded up (never under-pay)."""
    return -(-micros // 10_000)
