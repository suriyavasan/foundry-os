from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app
from app.runtimes.base import (
    CancelToken,
    Capability,
    CostKind,
    HealthStatus,
    RuntimeAdapter,
)
from app.runtimes.hermes import HermesAdapter
from app.runtimes.registry import RuntimeRegistry, build_default_registry

SECRET = "super-secret-hermes-key-123456"
URL = "http://hermes.test:8642"


def _client_with(make_settings, transport=None, key=SECRET, url=URL):
    settings = make_settings(HERMES_API_URL=url, HERMES_API_KEY=key)
    adapter = HermesAdapter.from_settings(settings, transport=transport)
    return TestClient(create_app(settings, runtime_registry=RuntimeRegistry([adapter])))


def _runtime(client):
    resp = client.get("/api/runtimes")
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 1
    return resp, items[0]


def test_not_configured_when_url_unset(client, make_settings):
    app = create_app(make_settings(), runtime_registry=build_default_registry(make_settings()))
    with TestClient(app) as c:
        _, hermes = _runtime(c)
    assert hermes["id"] == "hermes"
    assert hermes["display_name"] == "Hermes Agent"
    assert hermes["kind"] == "agent_runtime"
    assert hermes["health"]["status"] == "not_configured"
    assert hermes["health"]["checked_at"].endswith("Z")
    assert "HERMES_API_URL" in hermes["config_hint"]
    assert set(hermes["capabilities"]) <= {c.value for c in Capability}
    assert "idempotent_start" in hermes["capabilities"]


def test_ok_with_mock_transport_sends_bearer_and_hides_key(make_settings):
    seen: list[tuple[str, str | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.path, request.headers.get("authorization")))
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok", "platform": "hermes-agent"})
        return httpx.Response(200, json={"status": "ready"})

    with _client_with(make_settings, httpx.MockTransport(handler)) as c:
        resp, hermes = _runtime(c)
    assert hermes["health"]["status"] == "ok"
    assert ("/health", f"Bearer {SECRET}") in seen
    assert SECRET not in resp.text
    assert SECRET not in c.get("/api/openapi.json").text


def test_degraded_when_key_rejected(make_settings):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        return httpx.Response(401, json={"error": "bad key"})

    with _client_with(make_settings, httpx.MockTransport(handler)) as c:
        resp, hermes = _runtime(c)
    assert hermes["health"]["status"] == "degraded"
    assert SECRET not in resp.text


@pytest.mark.parametrize(
    "handler",
    [
        lambda r: httpx.Response(500, text=f"boom {SECRET}"),
        lambda r: httpx.Response(404),
        lambda r: (_ for _ in ()).throw(httpx.ConnectError(f"refused {URL} {SECRET}")),
        lambda r: (_ for _ in ()).throw(httpx.ReadTimeout("slow")),
    ],
    ids=["http500", "http404", "connect_error", "timeout"],
)
def test_unavailable_cases_are_sanitised(make_settings, handler):
    with _client_with(make_settings, httpx.MockTransport(handler)) as c:
        resp, hermes = _runtime(c)
    assert hermes["health"]["status"] == "unavailable"
    assert SECRET not in resp.text
    assert URL not in hermes["health"]["detail"]


def test_invalid_url_is_not_configured(make_settings):
    with _client_with(make_settings, url="ftp://nope") as c:
        _, hermes = _runtime(c)
    assert hermes["health"]["status"] == "not_configured"


def test_missing_key_is_degraded_not_ok(make_settings):
    handler = lambda r: httpx.Response(200, json={"status": "ok"})  # noqa: E731
    settings = make_settings(HERMES_API_URL=URL)
    adapter = HermesAdapter.from_settings(settings, transport=httpx.MockTransport(handler))
    assert adapter.check_health().status == HealthStatus.DEGRADED
    assert [i.field for i in adapter.validate_config()] == ["HERMES_API_KEY"]


def test_health_timeout_is_two_seconds():
    captured = {}

    class Spy(httpx.BaseTransport):
        def handle_request(self, request):
            captured["timeout"] = request.extensions.get("timeout")
            return httpx.Response(200, json={})

    adapter = HermesAdapter(URL, SECRET, transport=Spy())
    adapter.check_health()
    assert captured["timeout"]["connect"] == 2.0 and captured["timeout"]["read"] == 2.0


def test_key_never_in_repr_or_settings_dump():
    adapter = HermesAdapter(URL, SECRET)
    assert SECRET not in repr(adapter)
    settings = Settings(_env_file=None, HERMES_API_URL=URL, HERMES_API_KEY=SECRET)
    assert SECRET not in repr(settings) and SECRET not in str(settings.model_dump())


def test_hermes_cannot_execute_yet_and_is_never_dispatched():
    adapter = HermesAdapter(URL, SECRET)
    assert adapter.supports_execution is False
    with pytest.raises(NotImplementedError, match="not implemented"):
        adapter.execute(None, None, CancelToken())  # type: ignore[arg-type]


def test_registry_and_interface():
    assert issubclass(HermesAdapter, RuntimeAdapter)
    reg = RuntimeRegistry([HermesAdapter(None, None)])
    assert reg.get("hermes") is not None and reg.get("nope") is None
    with pytest.raises(ValueError):
        reg.register(HermesAdapter(None, None))
    assert {k.value for k in CostKind} == {"verified", "estimated", "unknown"}
