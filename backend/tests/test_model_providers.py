"""Model providers: CRUD, company default + default runtime, Ollama probing (served context!),
detect/register, tenancy and authorization. Network is mocked with httpx.MockTransport."""

from __future__ import annotations

import json

import httpx
import pytest
from sqlalchemy import select

from app.models.agent import Agent
from tests.conftest import human
from tests.helpers import api

OLLAMA = "http://127.0.0.1:11434"

MODELS = {
    "qwen2.5:7b": {"caps": ["completion", "tools"], "size": "7.6B", "ctx": 32768},
    "deepseek-r1:14b": {
        "caps": ["tools", "thinking", "completion"],
        "size": "14.8B",
        "ctx": 131072,
    },
    "olmo2:13b": {"caps": ["completion"], "size": "13.7B", "ctx": 4096},
    "nomic-embed-text": {"caps": ["embedding"], "size": "137M", "ctx": 8192},
}


class FakeOllama:
    def __init__(self, models=None, served=32768, up=True, chat_status=200, loaded=True):
        self.models = MODELS if models is None else models
        self.served = served
        self.up = up
        self.chat_status = chat_status
        self.loaded = loaded
        self.requests: list[tuple[str, str]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append((request.method, request.url.path))
        if not self.up:
            raise httpx.ConnectError("refused")
        path = request.url.path
        if path == "/api/tags":
            return httpx.Response(
                200,
                json={
                    "models": [
                        {
                            "name": n,
                            "details": {
                                "parameter_size": m["size"],
                                "quantization_level": "Q4_K_M",
                            },
                        }
                        for n, m in self.models.items()
                    ]
                },
            )
        if path == "/api/show":
            name = json.loads(request.content)["model"]
            m = self.models[name]
            return httpx.Response(
                200, json={"capabilities": m["caps"], "model_info": {"x.context_length": m["ctx"]}}
            )
        if path == "/v1/chat/completions":
            assert request.headers["authorization"] == "Bearer ollama"
            return httpx.Response(self.chat_status, json={"choices": []})
        if path == "/api/ps":
            if not self.loaded or self.served is None:
                return httpx.Response(200, json={"models": []})
            return httpx.Response(
                200, json={"models": [{"name": "qwen2.5:7b", "context_length": self.served}]}
            )
        return httpx.Response(404)


def use_ollama(app, fake: FakeOllama) -> FakeOllama:
    app.state.provider_transport = httpx.MockTransport(fake)
    return fake


def provider_body(**kw):
    return {"name": "Local Ollama", "kind": "ollama", "default_model": "qwen2.5:7b", **kw}


def agents_by_name(client, cid):
    return {a["name"]: a for a in client.get(api(cid, "/agents")).json()["items"]}


# ------------------------------------------------------------------ create + defaults


def test_ollama_defaults_and_url_normalization(client, company):
    r = client.post(
        api(company["id"], "/model-providers"),
        json=provider_body(base_url="http://localhost:11434/v1/"),
    )
    assert r.status_code == 201
    p = r.json()
    assert p["base_url"] == "http://localhost:11434"  # the server root, never /v1
    assert p["is_local"] is True and p["enabled"] is True and p["context_length"] == 32768
    assert p["credential_id"] is None and p["last_test"] is None
    assert p["input_price_per_mtok_micros"] is None  # unknown stays unknown, not 0
    r = client.post(api(company["id"], "/model-providers"), json=provider_body(name="Default URL"))
    assert r.json()["base_url"] == OLLAMA


def test_first_provider_becomes_the_company_default_and_equips_founder_and_hr(client, company):
    cid = company["id"]
    before = agents_by_name(client, cid)
    assert {a["runtime"] for a in before.values()} == {"unassigned"}
    p = client.post(api(cid, "/model-providers"), json=provider_body()).json()
    assert p["is_company_default"] is True
    policy = client.get(api(cid, "/policy")).json()
    assert (policy["default_model_provider_id"], policy["default_model_name"]) == (
        p["id"],
        "qwen2.5:7b",
    )
    after = agents_by_name(client, cid)
    for a in after.values():
        assert a["runtime"] == "company_agent"
        assert (a["model_provider"], a["model_name"]) == (p["id"], "qwen2.5:7b")
    audit = [e["action"] for e in client.get(api(cid, "/activity")).json()["items"]]
    assert "model_provider.created" in audit


def test_existing_runtime_and_model_choices_are_left_alone(client, company):
    cid = company["id"]
    founder = next(a for a in agents_by_name(client, cid).values() if a["kind"] == "founder")
    client.patch(
        api(cid, f"/agents/{founder['id']}"), json={"runtime": "hermes", "model_provider": "mine"}
    )
    client.post(api(cid, "/model-providers"), json=provider_body())
    after = client.get(api(cid, f"/agents/{founder['id']}")).json()
    assert (after["runtime"], after["model_provider"]) == ("hermes", "mine")


def test_a_second_provider_does_not_steal_the_default_unless_asked(client, company):
    cid = company["id"]
    first = client.post(api(cid, "/model-providers"), json=provider_body()).json()
    second = client.post(
        api(cid, "/model-providers"),
        json=provider_body(name="Other", base_url="http://127.0.0.1:11435"),
    ).json()
    assert client.get(api(cid, "/policy")).json()["default_model_provider_id"] == first["id"]
    third = client.post(
        api(cid, "/model-providers"),
        json=provider_body(name="Third", base_url="http://127.0.0.1:11436", make_default=True),
    ).json()
    assert client.get(api(cid, "/policy")).json()["default_model_provider_id"] == third["id"]
    flags = {
        p["id"]: p["is_company_default"]
        for p in client.get(api(cid, "/model-providers")).json()["items"]
    }
    assert flags == {first["id"]: False, second["id"]: False, third["id"]: True}


def test_new_agents_run_on_the_company_agent_runtime_when_a_default_model_exists(client, company):
    cid = company["id"]
    plain = client.post(api(cid, "/agents"), json={"name": "Before", "role_title": "X"}).json()
    assert plain["runtime"] == "unassigned"  # no provider yet: nothing pretends to be runnable
    p = client.post(api(cid, "/model-providers"), json=provider_body()).json()
    new = client.post(api(cid, "/agents"), json={"name": "After", "role_title": "X"}).json()
    assert (new["runtime"], new["model_provider"], new["model_name"]) == (
        "company_agent",
        p["id"],
        "qwen2.5:7b",
    )
    explicit = client.post(
        api(cid, "/agents"), json={"name": "Explicit", "role_title": "X", "runtime": "hermes"}
    ).json()
    assert explicit["runtime"] == "hermes" and explicit["model_provider"] is None


def test_policy_default_must_be_an_enabled_provider_of_this_company(client, company):
    cid = company["id"]
    other = client.post("/api/companies", json={"name": "Other"}).json()
    foreign = client.post(api(other["id"], "/model-providers"), json=provider_body()).json()
    r = client.patch(api(cid, "/policy"), json={"default_model_provider_id": foreign["id"]})
    assert r.status_code == 404
    mine = client.post(api(cid, "/model-providers"), json=provider_body()).json()
    client.patch(api(cid, f"/model-providers/{mine['id']}"), json={"enabled": False})
    assert client.get(api(cid, "/policy")).json()["default_model_provider_id"] is None  # cleared
    r = client.patch(api(cid, "/policy"), json={"default_model_provider_id": mine["id"]})
    assert r.status_code == 404  # disabled
    r = client.patch(api(cid, "/policy"), json={"sandbox_backend": "docker"})
    assert r.json()["sandbox_backend"] == "docker"
    assert client.patch(api(cid, "/policy"), json={"sandbox_backend": "vm"}).status_code == 422


# ------------------------------------------------------------------ validation


def test_cloud_kinds_need_a_credential_and_compatible_needs_a_url(client, company):
    cid = company["id"]
    for kind in ("openrouter", "anthropic", "openai"):
        r = client.post(
            api(cid, "/model-providers"), json={"name": kind, "kind": kind, "default_model": "m"}
        )
        assert r.status_code == 422, kind
    r = client.post(
        api(cid, "/model-providers"),
        json={"name": "c", "kind": "openai_compatible", "default_model": "m"},
    )
    assert r.status_code == 422


@pytest.mark.parametrize(
    "url", ["ftp://x", "not a url", "http://user:hunter2@host/v1", "file:///etc/passwd"]
)
def test_bad_or_credentialed_urls_are_rejected_and_not_echoed(client, company, url):
    r = client.post(api(company["id"], "/model-providers"), json=provider_body(base_url=url))
    assert r.status_code == 422 and "hunter2" not in r.text


def test_names_are_unique_and_unknown_fields_rejected(client, company):
    cid = company["id"]
    assert client.post(api(cid, "/model-providers"), json=provider_body()).status_code == 201
    assert client.post(api(cid, "/model-providers"), json=provider_body()).status_code == 409
    assert (
        client.post(
            api(cid, "/model-providers"), json=provider_body(name="x", api_key="k")
        ).status_code
        == 422
    )
    assert (
        client.post(
            api(cid, "/model-providers"), json=provider_body(name="y", context_length=100)
        ).status_code
        == 422
    )


def test_update_and_delete_rules(client, company):
    cid = company["id"]
    p = client.post(api(cid, "/model-providers"), json=provider_body()).json()
    r = client.patch(
        api(cid, f"/model-providers/{p['id']}"),
        json={"context_length": 16384, "default_model": "qwen3:8b"},
    )
    assert (
        r.status_code == 200
        and r.json()["context_length"] == 16384
        and r.json()["last_test"] is None
    )
    assert (
        client.patch(
            api(cid, f"/model-providers/{p['id']}"), json={"kind": "anthropic"}
        ).status_code
        == 422
    )
    # agents (founder/HR) use it: cannot delete
    assert client.delete(api(cid, f"/model-providers/{p['id']}")).status_code == 409
    for a in agents_by_name(client, cid).values():
        client.patch(api(cid, f"/agents/{a['id']}"), json={"model_provider": None})
    assert client.delete(api(cid, f"/model-providers/{p['id']}")).status_code == 204
    assert client.get(api(cid, f"/model-providers/{p['id']}")).status_code == 404
    assert client.get(api(cid, "/policy")).json()["default_model_provider_id"] is None


def test_agent_created_while_the_founder_has_no_model_still_gets_the_company_default(
    app, client, company
):
    cid = company["id"]
    p = client.post(api(cid, "/model-providers"), json=provider_body()).json()
    with app.state.session_factory() as db:
        founder = db.scalar(select(Agent).where(Agent.company_id == cid, Agent.kind == "founder"))
        founder.runtime, founder.model_provider, founder.model_name = "unassigned", None, None
        db.commit()
    # an owner-created agent without explicit runtime inherits the default
    a = client.post(api(cid, "/agents"), json={"name": "N", "role_title": "X"}).json()
    assert a["model_provider"] == p["id"]


# ------------------------------------------------------------------ tenancy + authorization


def test_providers_are_tenant_scoped(client, company):
    other = client.post("/api/companies", json={"name": "Other"}).json()
    mine = client.post(api(company["id"], "/model-providers"), json=provider_body()).json()
    assert client.get(api(other["id"], f"/model-providers/{mine['id']}")).status_code == 404
    assert (
        client.patch(
            api(other["id"], f"/model-providers/{mine['id']}"), json={"enabled": False}
        ).status_code
        == 404
    )
    assert client.delete(api(other["id"], f"/model-providers/{mine['id']}")).status_code == 404
    assert client.post(api(other["id"], f"/model-providers/{mine['id']}/test")).status_code == 404
    assert client.get(api(other["id"], "/model-providers")).json()["items"] == []
    # and the same name is fine in another company
    assert (
        client.post(api(other["id"], "/model-providers"), json=provider_body()).status_code == 201
    )


def test_non_members_and_viewers(client, client_as, make_user, add_member, company):
    cid = company["id"]
    p = client.post(api(cid, "/model-providers"), json=provider_body()).json()
    stranger = client_as(human(make_user("s@example.com")))
    assert stranger.get(api(cid, "/model-providers")).status_code == 404
    viewer_user = make_user("v@example.com")
    add_member(cid, viewer_user.id, "viewer")
    viewer = client_as(human(viewer_user))
    assert viewer.get(api(cid, "/model-providers")).status_code == 200
    assert viewer.get(api(cid, f"/model-providers/{p['id']}")).status_code == 200
    assert (
        viewer.post(api(cid, "/model-providers"), json=provider_body(name="v")).status_code == 403
    )
    assert (
        viewer.patch(api(cid, f"/model-providers/{p['id']}"), json={"enabled": False}).status_code
        == 403
    )
    assert viewer.delete(api(cid, f"/model-providers/{p['id']}")).status_code == 403
    assert viewer.post(api(cid, f"/model-providers/{p['id']}/test")).status_code == 403
    assert viewer.post(api(cid, "/model-providers/detect"), json={}).status_code == 403


# ------------------------------------------------------------------ the provider test (served context!)


def test_ok_when_ollama_serves_the_configured_context(app, client, company):
    cid = company["id"]
    fake = use_ollama(app, FakeOllama(served=32768))
    p = client.post(api(cid, "/model-providers"), json=provider_body()).json()
    r = client.post(api(cid, f"/model-providers/{p['id']}/test"))
    assert r.status_code == 200
    test = r.json()["last_test"]
    assert test["status"] == "ok" and test["warnings"] == []
    assert (test["context_length_configured"], test["context_length_served"]) == (32768, 32768)
    assert test["model_available"] is True and test["tool_calling"] is True
    assert test["checked_at"].endswith("Z") and test["latency_ms"] is not None
    # it really loaded the model through the OpenAI-compatible endpoint the runtime uses
    assert ("POST", "/v1/chat/completions") in fake.requests
    assert client.get(api(cid, f"/model-providers/{p['id']}")).json()["last_test"]["status"] == "ok"


def test_warns_with_the_fix_when_ollama_serves_a_smaller_context(app, client, company):
    cid = company["id"]
    use_ollama(app, FakeOllama(served=4096))
    p = client.post(api(cid, "/model-providers"), json=provider_body()).json()
    test = client.post(api(cid, f"/model-providers/{p['id']}/test")).json()["last_test"]
    assert test["status"] == "warning" and test["context_length_served"] == 4096
    (warning,) = test["warnings"]
    assert "4096" in warning and "32768" in warning
    assert "OLLAMA_CONTEXT_LENGTH=32768" in warning
    assert "truncated" in warning


def test_missing_model_no_tools_and_unreachable_are_errors(app, client, company):
    cid = company["id"]
    p = client.post(
        api(cid, "/model-providers"), json=provider_body(default_model="ghost:1b")
    ).json()
    use_ollama(app, FakeOllama())
    t = client.post(api(cid, f"/model-providers/{p['id']}/test")).json()["last_test"]
    assert (
        t["status"] == "error"
        and t["model_available"] is False
        and "ollama pull ghost:1b" in t["message"]
    )
    client.patch(api(cid, f"/model-providers/{p['id']}"), json={"default_model": "olmo2:13b"})
    t = client.post(api(cid, f"/model-providers/{p['id']}/test")).json()["last_test"]
    assert t["status"] == "error" and t["tool_calling"] is False and "tool calling" in t["message"]
    use_ollama(app, FakeOllama(up=False))
    t = client.post(api(cid, f"/model-providers/{p['id']}/test")).json()["last_test"]
    assert t["status"] == "error" and "Cannot reach Ollama" in t["message"]


def test_unreadable_served_context_is_reported_not_assumed(app, client, company):
    cid = company["id"]
    use_ollama(app, FakeOllama(loaded=False))
    p = client.post(api(cid, "/model-providers"), json=provider_body()).json()
    t = client.post(api(cid, f"/model-providers/{p['id']}/test")).json()["last_test"]
    assert t["status"] == "warning" and t["context_length_served"] is None
    assert "/api/ps" in t["warnings"][0]


def test_http_provider_test_uses_the_credential_and_never_echoes_it(app, client, company):
    cid = company["id"]
    key = "sk-test-cloud-key-0123456789abcdef"
    cred = client.post(api(cid, "/credentials"), json={"name": "k", "value": key}).json()
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"], seen["auth"] = str(request.url), request.headers.get("authorization")
        return httpx.Response(200, json={"data": [{"id": "big-model"}]})

    app.state.provider_transport = httpx.MockTransport(handler)
    p = client.post(
        api(cid, "/model-providers"),
        json={
            "name": "Cloud",
            "kind": "openai_compatible",
            "base_url": "https://llm.example.com/v1",
            "credential_id": cred["id"],
            "default_model": "big-model",
        },
    ).json()
    r = client.post(api(cid, f"/model-providers/{p['id']}/test"))
    t = r.json()["last_test"]
    assert t["status"] == "ok" and seen["url"] == "https://llm.example.com/v1/models"
    assert seen["auth"] == f"Bearer {key}"
    assert key not in r.text and "not probed" in t["message"]  # honest: tool calling not verified
    # rejected key
    app.state.provider_transport = httpx.MockTransport(
        lambda req: httpx.Response(401, text=f"bad {key}")
    )
    r = client.post(api(cid, f"/model-providers/{p['id']}/test"))
    assert r.json()["last_test"]["status"] == "error" and key not in r.text


def test_anthropic_probe_uses_its_own_headers(app, client, company):
    cid = company["id"]
    cred = client.post(
        api(cid, "/credentials"), json={"name": "a", "value": "sk-ant-0123456789abcdef"}
    ).json()
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(
            url=str(request.url),
            key=request.headers.get("x-api-key"),
            ver=request.headers.get("anthropic-version"),
        )
        return httpx.Response(200, json={"data": []})

    app.state.provider_transport = httpx.MockTransport(handler)
    p = client.post(
        api(cid, "/model-providers"),
        json={
            "name": "A",
            "kind": "anthropic",
            "credential_id": cred["id"],
            "default_model": "claude-x",
        },
    ).json()
    client.post(api(cid, f"/model-providers/{p['id']}/test"))
    assert (
        seen["url"] == "https://api.anthropic.com/v1/models"
        and seen["key"] == "sk-ant-0123456789abcdef"
    )
    assert seen["ver"]


# ------------------------------------------------------------------ detect + one-call register


def test_detect_recommends_a_tool_capable_non_reasoning_model(app, client, company):
    use_ollama(app, FakeOllama(served=None))
    r = client.get(api(company["id"], "/model-providers/detect"))
    assert r.status_code == 200
    body = r.json()
    assert body["reachable"] is True and body["base_url"] == OLLAMA
    assert (
        body["recommended_model"] == "qwen2.5:7b"
    )  # not deepseek-r1 (reasoning), not olmo2 (no tools)
    by_name = {m["name"]: m for m in body["models"]}
    assert by_name["qwen2.5:7b"]["tool_calling"] is True and by_name["qwen2.5:7b"]["recommended"]
    assert by_name["olmo2:13b"]["tool_calling"] is False
    assert any("context window is unknown" in w for w in body["warnings"])
    assert body["registered"] is None
    # read-only: nothing was registered
    assert client.get(api(company["id"], "/model-providers")).json()["items"] == []


def test_detect_reports_an_unreachable_ollama(app, client, company):
    use_ollama(app, FakeOllama(up=False))
    body = client.get(api(company["id"], "/model-providers/detect")).json()
    assert body["reachable"] is False and body["models"] == [] and body["recommended_model"] is None
    assert any("Cannot reach Ollama" in w for w in body["warnings"])
    r = client.post(api(company["id"], "/model-providers/detect"), json={"register": True})
    assert r.status_code == 409


def test_register_in_one_call_creates_tests_and_makes_it_the_default(app, client, company):
    cid = company["id"]
    use_ollama(app, FakeOllama(served=4096))
    r = client.post(api(cid, "/model-providers/detect"), json={"register": True})
    assert r.status_code == 200, r.text
    body = r.json()
    reg = body["registered"]
    assert (
        reg["name"] == "Local Ollama" and reg["default_model"] == "qwen2.5:7b" and reg["is_local"]
    )
    assert reg["is_company_default"] is True and reg["context_length"] == 32768
    assert reg["last_test"]["status"] == "warning"  # the served-context trap surfaces immediately
    assert "OLLAMA_CONTEXT_LENGTH=32768" in reg["last_test"]["warnings"][0]
    assert body["already_registered_provider_id"] == reg["id"]
    assert {a["runtime"] for a in agents_by_name(client, cid).values()} == {"company_agent"}


def test_register_is_idempotent_and_can_choose_a_model(app, client, company):
    cid = company["id"]
    use_ollama(app, FakeOllama())
    first = client.post(api(cid, "/model-providers/detect"), json={"register": True}).json()
    again = client.post(api(cid, "/model-providers/detect"), json={"register": True}).json()
    assert again["registered"]["id"] == first["registered"]["id"]
    assert len(client.get(api(cid, "/model-providers")).json()["items"]) == 1
    # a different server URL is a different provider; a chosen model must be usable
    other = client.post(
        api(cid, "/model-providers/detect"),
        json={
            "register": True,
            "base_url": "http://127.0.0.1:11435",
            "model": "deepseek-r1:14b",
            "make_default": False,
        },
    ).json()
    assert (
        other["registered"]["default_model"] == "deepseek-r1:14b"
        and other["registered"]["is_company_default"] is False
    )
    assert (
        client.post(
            api(cid, "/model-providers/detect"),
            json={"register": True, "base_url": "http://127.0.0.1:11436", "model": "olmo2:13b"},
        ).status_code
        == 409
    )
    assert (
        client.post(
            api(cid, "/model-providers/detect"),
            json={"register": True, "base_url": "http://127.0.0.1:11437", "model": "nope"},
        ).status_code
        == 404
    )


def test_register_needs_a_tool_capable_model(app, client, company):
    use_ollama(
        app,
        FakeOllama(
            models={
                "olmo2:13b": MODELS["olmo2:13b"],
                "nomic-embed-text": MODELS["nomic-embed-text"],
            }
        ),
    )
    body = client.get(api(company["id"], "/model-providers/detect")).json()
    assert body["recommended_model"] is None and any("ollama pull" in w for w in body["warnings"])
    r = client.post(api(company["id"], "/model-providers/detect"), json={"register": True})
    assert r.status_code == 409 and "ollama pull" in r.json()["error"]["message"]


def test_detect_is_owner_admin_only_and_tenant_scoped(app, client, client_as, make_user, company):
    use_ollama(app, FakeOllama())
    stranger = client_as(human(make_user("s@example.com")))
    assert (
        stranger.post(
            api(company["id"], "/model-providers/detect"), json={"register": True}
        ).status_code
        == 404
    )


def test_detect_post_works_without_a_body(app, client, company):
    use_ollama(app, FakeOllama(served=None))
    r = client.post(api(company["id"], "/model-providers/detect"))
    assert r.status_code == 200 and r.json()["registered"] is None
    assert client.get(api(company["id"], "/model-providers")).json()["items"] == []
