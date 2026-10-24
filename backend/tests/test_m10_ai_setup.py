"""M10 section 1: the AI readiness gate, the extended provider test, the model list."""

from __future__ import annotations

import httpx
import pytest
from sqlalchemy import select

from app.models.work import Task
from tests.helpers import api, new_agent, new_task, settle
from tests.m10_helpers import make_ready
from tests.test_model_providers import (
    MODELS,
    TOOL_CALL_REPLY,
    FakeOllama,
    provider_body,
    use_ollama,
)

pytestmark = pytest.mark.ai_gate


def status(client, cid) -> dict:
    r = client.get(api(cid, "/ai-setup"))
    assert r.status_code == 200, r.text
    return r.json()


# ------------------------------------------------------------------ the gate


def test_a_new_company_is_not_ready(client, company):
    s = status(client, company["id"])
    assert s["ready"] is False and s["blocking_reason"] == "no_tested_provider"
    assert s["management"]["provider_id"] is None and s["management"]["last_test"] is None
    assert s["work_mode"] == "api" and s["work_runtime"] is None
    assert {c["id"] for c in s["clis"]} == {"claude_code", "codex", "opencode"}


def test_creation_is_blocked_until_ready_but_settings_stay_available(app, client, company):
    cid = company["id"]
    for path, body in (
        ("/notes", {"body": "hello"}),
        ("/command", {"input": "/help"}),
        ("/command", {"input": "a thought"}),
        ("/tasks", {"title": "T"}),
        ("/agents", {"name": "Zed", "role_title": "Engineer"}),
    ):
        r = client.post(api(cid, path), json=body)
        assert r.status_code == 409, (path, r.text)
        assert r.json()["error"]["code"] == "ai_setup_required"
        assert r.json()["error"]["details"]["reason"] == "no_tested_provider"
    # owner settings, providers, credentials, policy, ai-setup, team and reads stay available
    assert client.get(api(cid, "/overview")).status_code == 200
    assert client.get(api(cid, "/notes")).status_code == 200
    assert client.patch(api(cid, "/policy"), json={"max_headcount": 10}).status_code == 200
    assert (
        client.post(api(cid, "/credentials"), json={"name": "k", "value": "x" * 12}).status_code
        == 201
    )
    use_ollama(app, FakeOllama())
    assert client.post(api(cid, "/model-providers"), json=provider_body()).status_code == 201
    assert client.patch(api(cid), json={"mission": "m"}).status_code == 200
    make_ready(app, client, cid, name="Another")
    assert client.post(api(cid, "/notes"), json={"body": "hello"}).status_code == 201
    assert client.post(api(cid, "/tasks"), json={"title": "T"}).status_code == 201


def test_reasons_follow_the_state_of_the_providers(app, client, company):
    cid = company["id"]
    use_ollama(app, FakeOllama(served=32768))
    p = client.post(api(cid, "/model-providers"), json=provider_body()).json()
    # the first provider became the company default, but nobody tested it
    assert client.get(api(cid, f"/model-providers/{p['id']}")).json()["is_company_default"] is True
    assert status(client, cid)["blocking_reason"] == "no_tested_provider"
    client.post(api(cid, f"/model-providers/{p['id']}/test"))
    # tested and passing, but the default model name was never confirmed through AI setup... the
    # default model of the provider is used until then, so the company is ready
    assert status(client, cid)["ready"] is True
    # clear the default: a tested provider exists but none is selected
    client.patch(
        api(cid, "/policy"), json={"default_model_provider_id": None, "default_model_name": None}
    )
    s = status(client, cid)
    assert s["ready"] is False and s["blocking_reason"] == "no_default_model"


def test_a_failed_test_blocks_with_provider_test_failed(app, client, company):
    cid = company["id"]
    use_ollama(app, FakeOllama(served=4096))  # 4K served: the context check fails
    p = client.post(api(cid, "/model-providers"), json=provider_body()).json()
    client.post(api(cid, f"/model-providers/{p['id']}/test"))
    s = status(client, cid)
    assert s["ready"] is False and s["blocking_reason"] == "provider_test_failed"
    assert s["management"]["last_test"]["ok"] is False
    # the owner can not select it either
    r = client.put(
        api(cid, "/ai-setup"),
        json={"management_provider_id": p["id"], "management_model": "qwen2.5:7b"},
    )
    assert r.status_code == 409 and r.json()["error"]["code"] == "ai_not_ready"
    assert r.json()["error"]["details"]["reason"] == "provider_test_failed"
    # fix the server and re-test: ready
    use_ollama(app, FakeOllama(served=32768))
    client.post(api(cid, f"/model-providers/{p['id']}/test"))
    r = client.put(
        api(cid, "/ai-setup"),
        json={"management_provider_id": p["id"], "management_model": "qwen2.5:7b"},
    )
    assert r.status_code == 200 and r.json()["ready"] is True
    m = r.json()["management"]
    assert (m["provider_name"], m["kind"], m["model"]) == ("Local Ollama", "ollama", "qwen2.5:7b")
    assert m["last_test"]["ok"] is True


def test_put_validations(app, client, company):
    cid = company["id"]
    p = make_ready(app, client, cid)
    put = lambda **kw: client.put(  # noqa: E731
        api(cid, "/ai-setup"),
        json={"management_provider_id": p["id"], "management_model": "qwen2.5:7b", **kw},
    )
    # untested model of a tested provider
    r = client.put(
        api(cid, "/ai-setup"),
        json={"management_provider_id": p["id"], "management_model": "other:1b"},
    )
    assert r.status_code == 409 and r.json()["error"]["details"]["reason"] == "provider_test_failed"
    assert put(work_mode="hybrid").status_code == 422  # needs a work_runtime
    assert put(work_mode="hybrid", work_runtime="company_agent").status_code == 422
    r = put(work_mode="hybrid", work_runtime="cli:opencode")
    assert r.status_code == 409 and r.json()["error"]["details"]["reason"] == "cli_not_installed"
    assert put(work_mode="bogus").status_code == 422
    assert (
        client.put(
            api(cid, "/ai-setup"),
            json={"management_provider_id": "nope", "management_model": "m"},
        ).status_code
        == 404
    )
    assert status(client, cid)["ready"] is True  # the failed attempts changed nothing


def test_re_testing_a_provider_with_a_new_failure_closes_the_gate_again(app, client, company):
    cid = company["id"]
    p = make_ready(app, client, cid)
    use_ollama(app, FakeOllama(up=False))
    client.post(api(cid, f"/model-providers/{p['id']}/test"))
    s = status(client, cid)
    assert s["ready"] is False and s["blocking_reason"] == "provider_test_failed"
    assert client.post(api(cid, "/tasks"), json={"title": "x"}).status_code == 409


def test_changing_the_provider_invalidates_the_test(app, client, company):
    cid = company["id"]
    p = make_ready(app, client, cid)
    client.patch(api(cid, f"/model-providers/{p['id']}"), json={"context_length": 65536})
    assert status(client, cid)["ready"] is False  # last_test was cleared by the edit


def test_existing_company_with_a_pre_m10_test_is_not_ready(app, client, company):
    """A provider tested before M10 has no `checks`: it never counts (the demo company's case)."""
    from app.models.provider import ModelProvider

    cid = company["id"]
    p = make_ready(app, client, cid)
    with app.state.session_factory() as db:
        row = db.get(ModelProvider, p["id"])
        row.last_test = {
            "status": "ok",
            "message": "old",
            "checked_at": "2026-10-01T00:00:00Z",
            "tool_calling": True,
            "warnings": [],
        }
        db.commit()
    s = status(client, cid)
    assert s["ready"] is False and s["blocking_reason"] == "provider_test_failed"
    t = client.get(api(cid, f"/model-providers/{p['id']}")).json()["last_test"]
    assert t["ok"] is False and t["checks"] == []  # legacy tests are served, never as passing


# ------------------------------------------------------------------ tasks, wakes, dispatch


def test_tasks_wait_with_a_reason_and_nothing_is_dispatched_or_woken(
    app, client, company, scheduler, worker, scripted
):
    cid = company["id"]
    p = make_ready(app, client, cid)
    agent = new_agent(client, cid, script={"summary": "ok"})
    task = new_task(client, cid, assignee_agent_id=agent["id"])
    assert task["status"] == "ready"
    client.post(api(cid, "/notes"), json={"body": "a thought"})
    # the AI breaks (provider disabled -> company default cleared)
    client.patch(api(cid, f"/model-providers/{p['id']}"), json={"enabled": False})
    assert status(client, cid)["ready"] is False
    settle(scheduler, worker)
    with app.state.session_factory() as db:
        row = db.get(Task, task["id"])
        assert row.status == "backlog" and row.blocked_reason == "ai_setup_required"
        from app.models.autonomy import WakeRequest
        from app.models.run import TaskRun

        assert db.scalars(select(TaskRun)).all() == []  # nothing was dispatched
        assert len(db.scalars(select(WakeRequest)).all()) == 1  # the wake waits
    # the AI works again: the same task is picked up with no human step
    client.patch(api(cid, f"/model-providers/{p['id']}"), json={"enabled": True})
    client.post(api(cid, f"/model-providers/{p['id']}/test"))
    r = client.put(
        api(cid, "/ai-setup"),
        json={"management_provider_id": p["id"], "management_model": "qwen2.5:7b"},
    )
    assert r.status_code == 200
    settle(scheduler, worker)
    with app.state.session_factory() as db:
        row = db.get(Task, task["id"])
        assert row.status == "done" and row.blocked_reason is None


# ------------------------------------------------------------------ provider test checks


def cloud_provider(app, client, cid, handler, *, kind="openai_compatible", **extra):
    app.state.provider_transport = httpx.MockTransport(handler)
    cred = client.post(
        api(cid, "/credentials"), json={"name": f"k-{kind}", "value": "sk-cloud-0123456789abcdef"}
    )
    body = {
        "name": f"cloud-{kind}",
        "kind": kind,
        "credential_id": cred.json()["id"],
        "default_model": "big-model",
        "base_url": "https://llm.example.com/v1" if kind == "openai_compatible" else None,
        **extra,
    }
    return client.post(
        api(cid, "/model-providers"), json={k: v for k, v in body.items() if v}
    ).json()


def names(test) -> dict[str, bool]:
    return {c["name"]: c["ok"] for c in test["checks"]}


def test_cloud_checks_all_pass(app, client, company):
    cid = company["id"]
    p = cloud_provider(
        app,
        client,
        cid,
        lambda req: (
            httpx.Response(200, json=TOOL_CALL_REPLY)
            if req.method == "POST"
            else httpx.Response(200, json={"data": [{"id": "big-model"}]})
        ),
    )
    t = client.post(api(cid, f"/model-providers/{p['id']}/test")).json()["last_test"]
    assert t["ok"] is True and t["status"] == "ok"
    assert names(t) == {
        "reachable": True,
        "auth": True,
        "tool_calling": True,
        "context": True,
        "model_exists": True,
    }
    assert t["served_context"] == 32768 and t["tested_at"] and t["model"] == "big-model"
    assert "sk-cloud-0123456789abcdef" not in str(t)


def test_cloud_bad_key_unreachable_no_tools_small_context_unknown_model(app, client, company):
    cid = company["id"]
    ok_list = httpx.Response(200, json={"data": [{"id": "big-model"}]})

    def bad_key(req):
        return httpx.Response(401, text="nope sk-cloud-0123456789abcdef")

    p = cloud_provider(app, client, cid, bad_key)
    t = client.post(api(cid, f"/model-providers/{p['id']}/test")).json()["last_test"]
    assert t["ok"] is False and names(t)["reachable"] is True and names(t)["auth"] is False
    assert "sk-cloud" not in str(t)

    def down(req):
        raise httpx.ConnectError("refused")

    app.state.provider_transport = httpx.MockTransport(down)
    t = client.post(api(cid, f"/model-providers/{p['id']}/test")).json()["last_test"]
    assert names(t)["reachable"] is False and t["ok"] is False

    def no_tools(req):
        if req.method == "POST":
            return httpx.Response(
                200, json={"choices": [{"message": {"role": "assistant", "content": "ok"}}]}
            )
        return ok_list

    app.state.provider_transport = httpx.MockTransport(no_tools)
    t = client.post(api(cid, f"/model-providers/{p['id']}/test")).json()["last_test"]
    assert names(t)["tool_calling"] is False and t["ok"] is False and t["tool_calling"] is False

    def tools_ok(req):
        return httpx.Response(200, json=TOOL_CALL_REPLY) if req.method == "POST" else ok_list

    app.state.provider_transport = httpx.MockTransport(tools_ok)
    client.patch(api(cid, f"/model-providers/{p['id']}"), json={"context_length": 8192})
    t = client.post(api(cid, f"/model-providers/{p['id']}/test")).json()["last_test"]
    assert names(t)["context"] is False and t["ok"] is False  # declared 8K < 32K

    client.patch(
        api(cid, f"/model-providers/{p['id']}"),
        json={"context_length": 32768, "default_model": "ghost"},
    )

    def ghost(req):
        if req.method == "POST":
            return httpx.Response(404, json={"error": "no such model"})
        return ok_list

    app.state.provider_transport = httpx.MockTransport(ghost)
    t = client.post(api(cid, f"/model-providers/{p['id']}/test")).json()["last_test"]
    assert names(t)["model_exists"] is False and t["ok"] is False


def test_openrouter_proves_the_key_on_its_key_endpoint_and_uses_declared_context(
    app, client, company
):
    cid = company["id"]
    seen = []

    def handler(req):
        seen.append((req.method, req.url.path))
        if req.url.path.endswith("/auth/key"):
            return httpx.Response(401, json={"error": "bad key"})
        if req.method == "POST":
            return httpx.Response(200, json=TOOL_CALL_REPLY)
        return httpx.Response(200, json={"data": [{"id": "big-model", "context_length": 200000}]})

    p = cloud_provider(app, client, cid, handler, kind="openrouter")
    t = client.post(api(cid, f"/model-providers/{p['id']}/test")).json()["last_test"]
    assert names(t)["auth"] is False and t["ok"] is False  # the public model list proves nothing
    assert ("POST", "/api/v1/chat/completions") not in seen  # no tokens spent on a bad key


def test_anthropic_probe_uses_tool_use(app, client, company):
    cid = company["id"]
    bodies = []

    def handler(req):
        if req.method == "POST":
            bodies.append(req.content)
            return httpx.Response(
                200,
                json={
                    "content": [
                        {"type": "tool_use", "name": "report_status", "input": {"status": "ok"}}
                    ]
                },
            )
        return httpx.Response(200, json={"data": [{"id": "big-model"}]})

    p = cloud_provider(app, client, cid, handler, kind="anthropic")
    t = client.post(api(cid, f"/model-providers/{p['id']}/test")).json()["last_test"]
    assert t["ok"] is True and b"tool_choice" in bodies[0] and b"report_status" in bodies[0]


def test_testing_another_model_and_the_model_list(app, client, company):
    cid = company["id"]
    use_ollama(app, FakeOllama(served=32768))
    p = client.post(api(cid, "/model-providers"), json=provider_body()).json()
    r = client.post(api(cid, f"/model-providers/{p['id']}/test"), json={"model": "olmo2:13b"})
    t = r.json()["last_test"]
    assert t["model"] == "olmo2:13b" and t["ok"] is False and names(t)["tool_calling"] is False
    listing = client.get(api(cid, f"/model-providers/{p['id']}/models")).json()
    assert listing["available"] is True and listing["next_cursor"] is None
    by_id = {m["id"]: m for m in listing["items"]}
    assert set(by_id) == set(MODELS) and by_id["qwen2.5:7b"]["tool_calling"] is True

    cred = client.post(
        api(cid, "/credentials"), json={"name": "kk", "value": "sk-cloud-0123456789abcdef"}
    ).json()
    app.state.provider_transport = httpx.MockTransport(
        lambda req: httpx.Response(
            200, json={"data": [{"id": "b", "context_length": 64000}, {"id": "a"}]}
        )
    )
    c = client.post(
        api(cid, "/model-providers"),
        json={
            "name": "cl",
            "kind": "openrouter",
            "credential_id": cred["id"],
            "default_model": "a",
        },
    ).json()
    listing = client.get(api(cid, f"/model-providers/{c['id']}/models")).json()
    assert [m["id"] for m in listing["items"]] == ["a", "b"] and listing["items"][1][
        "context_length"
    ] == 64000
    app.state.provider_transport = httpx.MockTransport(lambda req: httpx.Response(404))
    listing = client.get(api(cid, f"/model-providers/{c['id']}/models")).json()
    assert listing["available"] is False and listing["items"] == [] and listing["detail"]


def test_ai_setup_is_tenant_scoped_and_admin_only(
    app, client, company, make_user, add_member, client_as
):
    from tests.conftest import human

    cid = company["id"]
    p = make_ready(app, client, cid)
    stranger = client_as(human(make_user("s@x.test")))
    assert stranger.get(api(cid, "/ai-setup")).status_code == 404
    viewer_user = make_user("v@x.test")
    add_member(cid, viewer_user.id, "viewer")
    viewer = client_as(human(viewer_user))
    assert viewer.get(api(cid, "/ai-setup")).status_code == 200  # read-only is fine
    r = viewer.put(
        api(cid, "/ai-setup"),
        json={"management_provider_id": p["id"], "management_model": "qwen2.5:7b"},
    )
    assert r.status_code == 403


def test_schedules_do_not_fire_until_the_ai_works(app, client, company, scheduler, clock):
    from app.models.schedule import Schedule

    cid = company["id"]
    clock.advance(24 * 3600 + 120)
    scheduler.tick()
    with app.state.session_factory() as db:
        assert all(s.last_fired_at is None for s in db.scalars(select(Schedule)))  # all waited
    make_ready(app, client, cid)
    scheduler.tick()
    with app.state.session_factory() as db:
        assert any(
            s.last_fired_at is not None for s in db.scalars(select(Schedule))
        )  # now they fire
