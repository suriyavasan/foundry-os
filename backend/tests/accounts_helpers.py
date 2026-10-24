"""Helpers for the M7 account tests: a browser-like client (cookies + CSRF) and account builders."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.services.auth import CSRF_COOKIE, CSRF_HEADER

PASSWORD = "correct horse battery staple"
PASSWORD2 = "another long passphrase 2026"
UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}


class Browser(TestClient):
    """A TestClient that behaves like the web app: keeps cookies and echoes ``cos_csrf`` into
    ``X-CSRF-Token`` on state-changing requests. ``auto_csrf=False`` is a script that forgets it."""

    auto_csrf = True

    def request(self, method, url, **kwargs):  # type: ignore[override]
        if self.auto_csrf and method.upper() in UNSAFE:
            token = self.cookies.get(CSRF_COOKIE)
            headers = dict(kwargs.pop("headers", None) or {})
            if token and CSRF_HEADER not in {k.lower() for k in headers}:
                headers[CSRF_HEADER] = token
            kwargs["headers"] = headers
        return super().request(method, url, **kwargs)


OPEN: list[TestClient] = []


def _open(c: TestClient) -> TestClient:
    c.__enter__()  # runs the app's startup (the placeholder owner, the owner token), like uvicorn
    OPEN.append(c)
    return c


def close_all() -> None:
    while OPEN:
        OPEN.pop().__exit__(None, None, None)


def browser(app: FastAPI, *, loopback: bool = False, **kwargs) -> Browser:
    """A fresh anonymous browser. Default: some other machine on the LAN (the test peer is not
    loopback); ``loopback=True``: opened on the server's own machine."""
    if loopback:
        kwargs.setdefault("base_url", "http://localhost:8700")
        kwargs.setdefault("client", ("127.0.0.1", 50123))
    else:
        kwargs.setdefault("client", ("192.168.1.50", 50123))
    return _open(Browser(app, **kwargs))  # type: ignore[return-value]


def setup_body(app: FastAPI, **overrides) -> dict:
    body = {
        "username": "alice",
        "display_name": "Alice Founder",
        "password": PASSWORD,
        "setup_token": app.state.owner_token.get(),
    }
    body.update(overrides)
    return body


def do_setup(app: FastAPI, **overrides) -> Browser:
    """First-run setup through the real endpoint; returns the signed-in browser."""
    b = browser(app)
    r = b.post("/api/auth/setup", json=setup_body(app, **overrides))
    assert r.status_code == 200, r.text
    return b


def login(app: FastAPI, username: str, password: str = PASSWORD, **extra) -> Browser:
    b = browser(app)
    r = b.post("/api/auth/login", json={"username": username, "password": password, **extra})
    assert r.status_code == 200, r.text
    assert r.json()["state"] == "authenticated", r.text
    return b


def create_company(b: TestClient, name: str = "Acme Labs") -> dict:
    r = b.post("/api/companies", json={"name": name, "mission": "Ship"})
    assert r.status_code == 201, r.text
    return r.json()


def invite(b: TestClient, cid: str, email: str, role: str = "member", **extra) -> dict:
    r = b.post(f"/api/companies/{cid}/invitations", json={"email": email, "role": role, **extra})
    assert r.status_code == 201, r.text
    return r.json()


def join(app: FastAPI, created: dict, username: str, password: str = PASSWORD) -> Browser:
    """Accept an invitation by creating a new account; returns the new member's browser."""
    b = browser(app)
    r = b.post(
        f"/api/invitations/{created['token']}/accept",
        json={"username": username, "display_name": username.title(), "password": password},
    )
    assert r.status_code == 200, r.text
    return b


def member_ref(b: TestClient, cid: str) -> str:
    me = b.get("/api/me").json()
    mid = next(m["member_id"] for m in me["memberships"] if m["company_id"] == cid)
    return f"member:{mid}"
