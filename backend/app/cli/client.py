"""A small HTTP client for the Company OS API: error mapping, paging, server-sent events."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from typing import Any

import httpx

# Exit codes (documented in docs/deploy.md): 0 ok, 1 API/other error, 2 usage (Typer),
# 3 not signed in, 4 forbidden, 5 not found, 6 conflict, 7 server unreachable.
EXIT_ERROR = 1
EXIT_USAGE = 2
EXIT_AUTH = 3
EXIT_FORBIDDEN = 4
EXIT_NOT_FOUND = 5
EXIT_CONFLICT = 6
EXIT_UNREACHABLE = 7


class CliError(Exception):
    def __init__(
        self, message: str, *, exit_code: int = EXIT_ERROR, code: str = "error", details: Any = None
    ) -> None:
        super().__init__(message)
        self.message = message
        self.exit_code = EXIT_USAGE if code == "usage" else exit_code
        self.code = code
        self.details = details


_STATUS_EXIT = {401: EXIT_AUTH, 403: EXIT_FORBIDDEN, 404: EXIT_NOT_FOUND, 409: EXIT_CONFLICT}

HttpFactory = Callable[[str], httpx.Client]


def default_http_factory(api_url: str) -> httpx.Client:
    return httpx.Client(base_url=api_url, timeout=httpx.Timeout(30.0, connect=5.0))


def error_from_response(resp: httpx.Response) -> CliError:
    code, message, details = "error", f"HTTP {resp.status_code}", None
    try:
        body = resp.json()
        err = body.get("error") if isinstance(body, dict) else None
        if isinstance(err, dict):
            code = str(err.get("code") or code)
            message = str(err.get("message") or message)
            details = err.get("details")
    except ValueError:
        pass
    if resp.status_code == 401:
        message += " (run `companyos login`)"
    return CliError(
        message,
        exit_code=_STATUS_EXIT.get(resp.status_code, EXIT_ERROR),
        code=code,
        details=details,
    )


def _parse_json(text: str) -> dict | None:
    try:
        value = json.loads(text)
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


class ApiClient:
    def __init__(self, http: httpx.Client, token: str | None) -> None:
        self.http = http
        self.token = token

    @property
    def base_url(self) -> str:
        return str(self.http.base_url).rstrip("/")

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"} if self.token else {}

    def request(
        self,
        method: str,
        path: str,
        *,
        json_body: Any = None,
        params: dict | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        kwargs: dict[str, Any] = {"headers": {**self._headers(), **(headers or {})}}
        if params:
            kwargs["params"] = {k: v for k, v in params.items() if v is not None}
        if json_body is not None:
            kwargs["json"] = json_body
        try:
            resp = self.http.request(method, "/api" + path, **kwargs)
        except httpx.ConnectError as exc:
            raise CliError(
                f"Cannot reach Company OS at {self.base_url}. Is it running? (`companyos up`)",
                exit_code=EXIT_UNREACHABLE,
                code="unreachable",
            ) from exc
        except httpx.TimeoutException as exc:
            raise CliError(
                f"Timed out talking to {self.base_url}", exit_code=EXIT_UNREACHABLE, code="timeout"
            ) from exc
        if resp.status_code >= 400:
            raise error_from_response(resp)
        if resp.status_code == 204 or not resp.content:
            return None
        try:
            return resp.json()
        except ValueError as exc:
            raise CliError(f"Unexpected non-JSON answer from {path}") from exc

    def get(self, path: str, **params: Any) -> Any:
        return self.request("GET", path, params=params)

    def post(self, path: str, body: Any | None = None) -> Any:
        return self.request("POST", path, json_body={} if body is None else body)

    def patch(self, path: str, body: Any) -> Any:
        return self.request("PATCH", path, json_body=body)

    def delete(self, path: str) -> Any:
        return self.request("DELETE", path)

    def paged(self, path: str, *, limit: int = 200, max_items: int = 1000, **params: Any) -> list:
        """Follow ``next_cursor`` (lists are ``{items, next_cursor}``)."""
        items: list = []
        cursor: str | None = None
        while len(items) < max_items:
            page = self.get(path, limit=limit, cursor=cursor, **params)
            if isinstance(page, list):
                return page
            items.extend(page.get("items", []))
            cursor = page.get("next_cursor")
            if not cursor:
                break
        return items

    def sse(self, path: str, **params: Any) -> Iterator[tuple[str, dict]]:
        """Yield ``(event, data)`` from a text/event-stream endpoint until the connection ends."""
        query = {k: v for k, v in params.items() if v is not None}
        try:
            with self.http.stream(
                "GET",
                "/api" + path,
                params=query,
                headers={**self._headers(), "Accept": "text/event-stream"},
                timeout=httpx.Timeout(None, connect=5.0),
            ) as resp:
                if resp.status_code >= 400:
                    resp.read()
                    raise error_from_response(resp)
                event, data = "message", []
                for line in resp.iter_lines():
                    if line == "":
                        parsed = _parse_json("\n".join(data)) if data else None
                        if parsed is not None:
                            yield event, parsed
                        event, data = "message", []
                    elif line.startswith("event:"):
                        event = line[6:].strip()
                    elif line.startswith("data:"):
                        data.append(line[5:].lstrip())
        except httpx.ConnectError as exc:
            raise CliError(
                f"Cannot reach Company OS at {self.base_url}",
                exit_code=EXIT_UNREACHABLE,
                code="unreachable",
            ) from exc
