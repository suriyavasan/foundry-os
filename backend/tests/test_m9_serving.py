"""M9: the API port also serves the built web UI (SPA fallback, caching, CSP, security headers)."""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.applications import Starlette
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.routing import Route

from app.core.config import REPO_DIR, Settings
from app.main import create_app
from app.runtimes.registry import RuntimeRegistry
from app.web import API_CSP, CONTENT_CSP, SPA_CSP, SecurityHeadersMiddleware, WebUiMiddleware

INDEX = (
    '<!doctype html><html><head><script src="/theme-init.js"></script>'
    '<script type="module" src="/assets/app-abc123.js"></script></head>'
    '<body><div id="root"></div></body></html>'
)


@pytest.fixture
def dist(tmp_path: Path) -> Path:
    d = tmp_path / "dist"
    (d / "assets").mkdir(parents=True)
    (d / "index.html").write_text(INDEX)
    (d / "assets" / "app-abc123.js").write_text("console.log('hi')")
    (d / "assets" / "app-abc123.css").write_text("body{}")
    (d / "theme-init.js").write_text("/* theme */")
    (d / "favicon.svg").write_text("<svg xmlns='http://www.w3.org/2000/svg'/>")
    (tmp_path / "secret.txt").write_text("outside the dist")
    return d


@pytest.fixture
def web(make_settings: Callable[..., Settings], dist: Path):
    app = create_app(make_settings(web_dist_dir=dist), runtime_registry=RuntimeRegistry())
    with TestClient(app) as c:
        yield c


def test_root_serves_index_with_revalidation_and_a_strict_csp(web: TestClient):
    r = web.get("/")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert r.headers["cache-control"] == "no-cache"
    csp = r.headers["content-security-policy"]
    assert csp == SPA_CSP
    script_src = next(d for d in csp.split("; ") if d.startswith("script-src"))
    assert script_src == "script-src 'self'"  # no inline, no eval, no other origin
    for directive in (
        "frame-ancestors 'none'",
        "object-src 'none'",
        "base-uri 'self'",
        "default-src 'self'",
    ):
        assert directive in csp
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["referrer-policy"] == "no-referrer"
    assert r.headers["x-frame-options"] == "DENY"
    assert "camera=()" in r.headers["permissions-policy"]
    assert "strict-transport-security" not in r.headers  # plain http


def test_hashed_assets_are_immutable_everything_else_revalidates(web: TestClient):
    asset = web.get("/assets/app-abc123.js")
    assert asset.status_code == 200
    assert asset.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert "javascript" in asset.headers["content-type"]
    assert asset.headers["etag"]
    assert web.get("/assets/app-abc123.css").headers["cache-control"].startswith("public")
    for path in ("/theme-init.js", "/favicon.svg", "/index.html"):
        r = web.get(path)
        assert r.status_code == 200, path
        assert r.headers["cache-control"] == "no-cache", path
    # a conditional request is answered 304 (the ETag makes revalidation cheap)
    first = web.get("/theme-init.js")
    again = web.get("/theme-init.js", headers={"If-None-Match": first.headers["etag"]})
    assert again.status_code == 304


def test_client_side_routes_fall_back_to_index(web: TestClient):
    for path in (
        "/c/some-company",
        "/c/some-company/anything/deep",
        "/onboarding",
        "/invite/abc_DEF-123",
    ):
        r = web.get(path)
        assert r.status_code == 200 and "root" in r.text, path
        assert r.headers["content-type"].startswith("text/html")


def test_missing_files_with_an_extension_are_real_404s_not_html(web: TestClient):
    r = web.get("/assets/missing-file.js")
    assert r.status_code == 404
    assert "<html" not in r.text
    assert r.json()["error"]["code"] == "not_found"


def test_spa_fallback_never_shadows_the_api(web: TestClient):
    unknown = web.get("/api/this-does-not-exist")
    assert unknown.status_code == 404
    assert unknown.headers["content-type"].startswith("application/json")
    assert unknown.json()["error"]["code"] == "not_found"
    assert "root" not in unknown.text
    assert web.get("/api").status_code == 404 and "root" not in web.get("/api").text
    # writes are never answered by the UI layer
    assert web.post("/c/anything", json={}).status_code in (404, 405)
    health = web.get("/api/health")
    assert health.status_code == 200 and health.json()["status"] == "ok"
    assert web.get("/api/openapi.json").status_code == 200


def test_api_answers_get_no_store_and_a_nothing_may_load_policy(web: TestClient):
    r = web.get("/api/health")
    assert r.headers["content-security-policy"] == API_CSP
    assert r.headers["cache-control"] == "no-store"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"
    docs = web.get("/api/docs")
    assert docs.status_code == 200
    assert "content-security-policy" not in docs.headers  # Swagger UI loads its UI from a CDN
    assert docs.headers["x-content-type-options"] == "nosniff"


def test_dotfiles_and_traversal_are_never_served(dist: Path):
    resolve = WebUiMiddleware._resolve
    assert resolve(dist, "/") == dist / "index.html"
    assert resolve(dist, "/theme-init.js") == dist / "theme-init.js"
    for evil in (
        "/../secret.txt",
        "/%2e%2e/secret.txt",
        "/assets/../../secret.txt",
        "/.env",
        "/assets/.hidden.js",
    ):
        assert resolve(dist, evil) is None, evil
    (dist / ".hidden").write_text("x")
    assert resolve(dist, "/.hidden") is None
    # a symlink out of the dist directory must not be followed
    (dist / "link.txt").symlink_to(dist.parent / "secret.txt")
    assert resolve(dist, "/link.txt") is None


def test_head_requests_work(web: TestClient):
    r = web.head("/")
    assert r.status_code == 200 and r.content == b""
    assert r.headers["cache-control"] == "no-cache"


def test_not_served_when_unbuilt_or_disabled(
    make_settings: Callable[..., Settings], dist: Path, tmp_path: Path
):
    unbuilt = create_app(
        make_settings(web_dist_dir=tmp_path / "nope"), runtime_registry=RuntimeRegistry()
    )
    with TestClient(unbuilt) as c:
        r = c.get("/")
        assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"
        assert c.get("/api/health").status_code == 200
    off = create_app(
        make_settings(web_dist_dir=dist, serve_web=False), runtime_registry=RuntimeRegistry()
    )
    with TestClient(off) as c:
        assert c.get("/").status_code == 404
        assert (
            c.get("/api/health").headers["x-content-type-options"] == "nosniff"
        )  # headers stay on


def test_hsts_only_over_https_and_content_endpoints_are_sandboxed():
    async def ok(request):
        return JSONResponse({"ok": True})

    async def text(request):
        return PlainTextResponse("hello")

    inner = Starlette(
        routes=[
            Route("/api/artifacts/1/content", text),
            Route("/api/docs", text),
            Route("/api/thing", ok),
            Route("/page", text),
        ]
    )
    app = SecurityHeadersMiddleware(inner)
    c = TestClient(app)
    plain = c.get("/api/thing")
    assert "strict-transport-security" not in plain.headers
    secure = c.get("/api/thing", headers={"X-Forwarded-Proto": "https"})
    assert secure.headers["strict-transport-security"].startswith("max-age=")
    assert c.get("/api/artifacts/1/content").headers["content-security-policy"] == CONTENT_CSP
    assert "sandbox" in CONTENT_CSP
    assert "content-security-policy" not in c.get("/api/docs").headers
    # headers set by the endpoint itself are respected, not overwritten
    assert c.get("/page").headers["x-content-type-options"] == "nosniff"


def test_existing_route_headers_win():
    async def custom(request):
        return PlainTextResponse(
            "x", headers={"Referrer-Policy": "same-origin", "Cache-Control": "max-age=5"}
        )

    app = SecurityHeadersMiddleware(Starlette(routes=[Route("/api/x", custom)]))
    r = TestClient(app).get("/api/x")
    assert r.headers["referrer-policy"] == "same-origin"
    assert r.headers["cache-control"] == "max-age=5"


BUILT_INDEX = REPO_DIR / "web" / "dist" / "index.html"


@pytest.mark.skipif(not BUILT_INDEX.is_file(), reason="web/dist is not built")
def test_the_real_vite_build_works_under_the_csp():
    """No inline scripts, no handlers, no external origins in what Vite actually produced."""
    html = BUILT_INDEX.read_text()
    scripts = re.findall(r"<script\b([^>]*)>", html)
    assert scripts, "expected script tags"
    for attrs in scripts:
        assert "src=" in attrs, f"inline script would be blocked by the CSP: <script {attrs}>"
    assert not re.search(r"\son[a-z]+\s*=", html), "inline event handler"
    assert not re.search(r"""(?:src|href)=["']https?://""", html), "external origin in index.html"
    assert " style=" not in html
    dist = BUILT_INDEX.parent
    referenced = re.findall(r'(?:src|href)="/([^"]+)"', html)
    assert referenced
    for rel in referenced:
        assert (dist / rel).is_file(), f"index.html references /{rel}, which is not in dist"
