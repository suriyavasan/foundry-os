"""M17 "One AI stack" (docs/specs/m17-ai-stack.md): connections, order, readiness, summary, the legacy
bootstrap, per-connection tests and plan sign-in (fake ``claude`` / ``agent`` / ``codex`` binaries: no real
sign-in and no real model run ever happens here), tenancy, roles, audit and the migration."""

from __future__ import annotations

import json
import time
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import select

from app.core.time import utcnow
from app.models.ai_stack import AiConnection
from app.models.audit import AuditEvent
from app.models.policy import CompanyPolicy
from app.runtimes.cli import store
from app.services import ai_stack, plan_login
from tests.conftest import human
from tests.helpers import api, new_agent
from tests.m10_helpers import (
    CODEX_OK,
    CURSOR_OK,
    auth_log,
    install_fake_cli,
    make_ready,
    record_of,
    register_cli_adapters,
)
from tests.test_model_providers import FakeOllama, provider_body, use_ollama

pytestmark = pytest.mark.ai_gate

KEY = "sk-test-key-0123456789abcdef"
KEY2 = "sk-test-key-FEDCBA9876543210"
TOKEN = "sk-ant-oat01-SECRET-TOKEN-0123456789abcdefghij"
OK = [{"type": "result", "subtype": "success", "is_error": False, "result": "OK", "usage": {}}]


@pytest.fixture
def bin_dir(app, tmp_path):
    d = tmp_path / "bin"
    d.mkdir()
    app.state.cli_env.extra_dirs.append(d)
    register_cli_adapters(app)
    return d


@pytest.fixture
def data_dir(app) -> Path:
    return app.state.settings.resolved_data_dir


def url(cid, suffix=""):
    return api(cid, "/ai-stack" + suffix)


def stack(client, cid) -> dict:
    r = client.get(url(cid))
    assert r.status_code == 200, r.text
    return r.json()


def add(client, cid, option, expect=201, **body):
    r = client.post(url(cid), json={"option": option, **body})
    assert r.status_code == expect, r.text
    return r.json()


def provider(client, cid, name="Local Ollama", test=True, **kw) -> dict:
    use_ollama(client.app, FakeOllama(served=32768))
    r = client.post(api(cid, "/model-providers"), json=provider_body(name=name, **kw))
    assert r.status_code == 201, r.text
    p = r.json()
    if test:
        t = client.post(api(cid, f"/model-providers/{p['id']}/test"))
        assert t.json()["last_test"]["ok"] is True, t.text
    return p


def api_conn(client, cid, name="Local Ollama", **body) -> dict:
    p = provider(client, cid, name=name)
    return add(client, cid, "ollama", model_provider_id=p["id"], model=p["default_model"], **body)


def credential(client, cid, name="k", value=KEY) -> dict:
    r = client.post(api(cid, "/credentials"), json={"name": name, "value": value})
    assert r.status_code == 201, r.text
    return r.json()


def audit_actions(app) -> list[str]:
    with app.state.session_factory() as db:
        return [a.action for a in db.scalars(select(AuditEvent).order_by(AuditEvent.created_at))]


def everything_written(app) -> str:
    with app.state.session_factory() as db:
        rows = db.scalars(select(AuditEvent)).all()
        return "\n".join(f"{r.action} {r.summary} {json.dumps(r.data)}" for r in rows)


def set_policy_rows(app, cid, **fields):
    with app.state.session_factory() as db:
        row = db.scalar(select(CompanyPolicy).where(CompanyPolicy.company_id == cid))
        for k, v in fields.items():
            setattr(row, k, v)
        db.commit()


def connections(app, cid) -> list[AiConnection]:
    with app.state.session_factory() as db:
        return list(
            db.scalars(
                select(AiConnection)
                .where(AiConnection.company_id == cid)
                .order_by(AiConnection.position)
            )
        )


def pass_test(app, conn_id):
    with app.state.session_factory() as db:
        row = db.get(AiConnection, conn_id)
        row.last_test = {"ok": True, "detail": "ok", "tested_at": "2026-10-28T00:00:00Z"}
        db.commit()


# ------------------------------------------------------------------ empty stack and the catalog


def test_an_empty_stack_is_not_ready_and_lists_the_options(client, company):
    s = stack(client, company["id"])
    assert s["items"] == [] and s["ready"] is False and s["blocking_reason"] == "no_ai"
    assert s["summary"] == {"heavy": [], "everyday": []}
    options = {o["option"]: o for o in s["options"]}
    assert set(options) == {
        "claude_plan", "anthropic_api", "openai_api", "openrouter", "openai_compatible",
        "ollama", "cursor_plan", "cursor_api", "codex_api", "opencode", "antigravity_api",
    }  # fmt: skip
    assert options["ollama"]["available"] is False  # not detected
    assert options["claude_plan"]["group"] == "plan" and options["claude_plan"]["warning"]
    assert options["claude_plan"]["needs_install"] is True  # no claude on this PATH
    assert (
        options["claude_plan"]["install"]["command"] == "npm install -g @anthropic-ai/claude-code"
    )
    assert options["cursor_plan"]["install"]["available"] is False  # vendor shell installer


def test_the_catalog_reflects_installs_and_existing_plan_connections(client, company, bin_dir):
    cid = company["id"]
    install_fake_cli(bin_dir, "claude", OK)
    options = {o["option"]: o for o in stack(client, cid)["options"]}
    assert (
        options["claude_plan"]["needs_install"] is False
        and options["claude_plan"]["install"] is None
    )
    add(client, cid, "claude_plan")
    options = {o["option"]: o for o in stack(client, cid)["options"]}
    assert options["claude_plan"]["available"] is False
    assert options["claude_plan"]["reason"] == "plan_already_connected"


# ------------------------------------------------------------------ CRUD, order, audit


def test_add_patch_reorder_delete_with_audit(app, client, company):
    cid = company["id"]
    first = api_conn(client, cid, share=4)
    assert first["rank_label"] == "Primary" and first["position"] == 0
    assert first["option"] == "ollama" and first["provider_kind"] == "ollama"
    assert first["label"] == "Local Ollama" and first["status"]["state"] == "ready"
    assert first["use_for"] == "everything" and first["share"] == 4 and first["runs_24h"] == 0
    second = api_conn(client, cid, name="Second Ollama", use_for="everyday", share=0)
    assert second["rank_label"] == "Secondary" and second["position"] == 1
    s = stack(client, cid)
    assert s["ready"] is True and s["blocking_reason"] is None
    assert [i["id"] for i in s["items"]] == [first["id"], second["id"]]

    r = client.patch(
        url(cid, f"/{second['id']}"), json={"label": "Backup", "share": 1, "enabled": False}
    )
    assert (
        r.status_code == 200
        and r.json()["label"] == "Backup"
        and r.json()["status"]["state"] == "disabled"
    )
    assert client.patch(url(cid, f"/{second['id']}"), json={"share": 9}).status_code == 422
    assert client.patch(url(cid, f"/{second['id']}"), json={"bogus": 1}).status_code == 422

    # changing the model resets the test (it described another model)
    r = client.patch(url(cid, f"/{first['id']}"), json={"model": "other:7b"})
    assert r.json()["model"] == "other:7b" and r.json()["status"]["state"] == "needs_test"
    assert stack(client, cid)["ready"] is False

    bad = [first["id"]]
    assert client.put(url(cid, "/order"), json={"ids": bad}).status_code == 422  # missing one
    assert (
        client.put(url(cid, "/order"), json={"ids": [first["id"], first["id"]]}).status_code == 422
    )
    assert client.put(url(cid, "/order"), json={"ids": [first["id"], "nope"]}).status_code == 422
    r = client.put(url(cid, "/order"), json={"ids": [second["id"], first["id"]]})
    assert r.status_code == 200
    assert [i["id"] for i in r.json()["items"]] == [second["id"], first["id"]]
    assert r.json()["items"][0]["rank_label"] == "Primary"

    assert client.delete(url(cid, f"/{second['id']}")).status_code == 204
    left = stack(client, cid)["items"]
    assert [i["id"] for i in left] == [first["id"]] and left[0]["position"] == 0
    assert client.delete(url(cid, f"/{second['id']}")).status_code == 404
    actions = audit_actions(app)
    for a in ("ai_connection.added", "ai_connection.updated", "ai_connection.reordered",
              "ai_connection.removed"):  # fmt: skip
        assert a in actions


def test_deleting_a_connection_keeps_its_provider_and_a_used_provider_cannot_be_deleted(
    client, company
):
    cid = company["id"]
    c = api_conn(client, cid)
    assert client.delete(api(cid, f"/model-providers/{c['model_provider_id']}")).status_code == 409
    assert client.delete(url(cid, f"/{c['id']}")).status_code == 204
    assert client.get(api(cid, f"/model-providers/{c['model_provider_id']}")).status_code == 200


def test_validation_of_new_connections(client, company, make_user, client_as):
    cid = company["id"]
    p = provider(client, cid)
    # an API option needs a provider of its kind
    assert add(client, cid, "ollama", expect=422)  # no provider
    assert add(client, cid, "openrouter", expect=422, model_provider_id=p["id"])  # wrong kind
    assert add(client, cid, "ollama", expect=404, model_provider_id="no-such-provider")
    assert add(client, cid, "ollama", expect=422, model_provider_id=p["id"], credential_id="x")
    # a CLI key option needs a credential; a plan option takes none
    assert add(client, cid, "codex_api", expect=422)
    assert add(client, cid, "cursor_plan", expect=422, credential_id="x")
    assert add(client, cid, "codex_api", expect=422, model_provider_id=p["id"])
    assert add(client, cid, "opencode", expect=422)
    # other companies' credentials and providers are rejected
    other = client_as(human(make_user("o2@x.test")))
    oc = other.post("/api/companies", json={"name": "Other"}).json()
    foreign_cred = credential(other, oc["id"], "fk")
    assert add(client, cid, "codex_api", expect=404, credential_id=foreign_cred["id"])
    foreign_provider = provider(other, oc["id"], name="Theirs")
    assert add(client, cid, "ollama", expect=404, model_provider_id=foreign_provider["id"])
    assert [i["label"] for i in client.get(url(cid)).json()["items"]] == [
        "Local Ollama"
    ]  # bootstrap only


def test_one_plan_connection_per_cli(client, company):
    cid = company["id"]
    add(client, cid, "claude_plan")
    r = client.post(url(cid), json={"option": "claude_plan"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "plan_already_connected"
    add(client, cid, "cursor_plan")
    assert client.post(url(cid), json={"option": "cursor_plan"}).status_code == 409
    # but API keys of any provider, several times, are fine
    api_conn(client, cid)
    api_conn(client, cid, name="Another Ollama")


def test_tenancy_roles_and_agents(app, client, company, make_user, client_as, add_member):
    from tests.test_agent_api import start_run

    cid = company["id"]
    c = api_conn(client, cid)
    # a stranger gets 404 for everything, including another company's connection id
    stranger = client_as(human(make_user("s@x.test")))
    assert stranger.get(url(cid)).status_code == 404
    assert stranger.post(url(cid), json={"option": "claude_plan"}).status_code == 404
    other_company = stranger.post("/api/companies", json={"name": "Other"}).json()
    ocid = other_company["id"]
    assert stranger.patch(url(ocid, f"/{c['id']}"), json={"label": "x"}).status_code == 404
    assert stranger.delete(url(ocid, f"/{c['id']}")).status_code == 404
    assert stranger.post(url(ocid, f"/{c['id']}/test")).status_code == 404
    assert stranger.get(url(ocid, f"/{c['id']}/login")).status_code == 404
    assert [i["id"] for i in stack(client, cid)["items"]] == [c["id"]]  # untouched
    # viewers and members read but never write
    viewer = make_user("v@x.test")
    add_member(cid, viewer.id, "viewer")
    member = make_user("m@x.test")
    add_member(cid, member.id, "member")
    admin = make_user("a@x.test")
    add_member(cid, admin.id, "admin")
    for who in (viewer, member):
        cl = client_as(human(who))
        assert cl.get(url(cid)).status_code == 200
        assert cl.post(url(cid), json={"option": "claude_plan"}).status_code == 403
        assert cl.patch(url(cid, f"/{c['id']}"), json={"share": 1}).status_code == 403
        assert cl.put(url(cid, "/order"), json={"ids": [c["id"]]}).status_code == 403
        assert cl.delete(url(cid, f"/{c['id']}")).status_code == 403
        assert cl.post(url(cid, f"/{c['id']}/test")).status_code == 403
    assert (
        client_as(human(admin)).patch(url(cid, f"/{c['id']}"), json={"share": 1}).status_code == 200
    )
    # an agent can neither read nor change its own AI
    agent = new_agent(client, cid)
    session = start_run(app, client, cid, agent["id"])
    h = session.headers
    assert client.get(url(cid), headers=h).status_code == 403
    assert client.post(url(cid), json={"option": "claude_plan"}, headers=h).status_code == 403
    assert client.patch(url(cid, f"/{c['id']}"), json={"share": 0}, headers=h).status_code == 403
    assert client.put(url(cid, "/order"), json={"ids": [c["id"]]}, headers=h).status_code == 403
    assert client.delete(url(cid, f"/{c['id']}"), headers=h).status_code == 403
    assert client.put(api(cid, "/ai-setup"), json={}, headers=h).status_code in (403, 410)


# ------------------------------------------------------------------ readiness and summary


def test_readiness_blocking_reasons(app, client, company):
    cid = company["id"]
    assert stack(client, cid)["blocking_reason"] == "no_ai"
    p = provider(client, cid, test=False)
    c = add(client, cid, "ollama", model_provider_id=p["id"], model="qwen2.5:7b", use_for="heavy")
    # the provider default was never tested: the connection is not tested either
    s = stack(client, cid)
    assert s["ready"] is False and s["blocking_reason"] == "no_tested_ai"
    assert c["status"]["state"] == "needs_test"
    t = client.post(url(cid, f"/{c['id']}/test"))
    assert t.status_code == 200 and t.json()["status"]["state"] == "ready"
    assert t.json()["status"]["last_test"]["ok"] is True
    s = stack(client, cid)
    assert s["ready"] is False and s["blocking_reason"] == "no_everyday_ai"
    d = api_conn(client, cid, name="Day", use_for="everyday")
    assert stack(client, cid)["ready"] is True
    client.patch(url(cid, f"/{c['id']}"), json={"use_for": "everyday"})
    assert stack(client, cid)["blocking_reason"] == "no_heavy_ai"
    client.patch(url(cid, f"/{d['id']}"), json={"enabled": False})
    assert (
        stack(client, cid)["blocking_reason"] == "no_ai"
        or stack(client, cid)["blocking_reason"] == "no_heavy_ai"
    )
    client.patch(url(cid, f"/{c['id']}"), json={"enabled": False})
    assert stack(client, cid)["blocking_reason"] == "no_ai"
    # the gate follows: /ai-setup is derived from the stack
    gate = client.get(api(cid, "/ai-setup")).json()
    assert (
        gate["ready"] is False and gate["blocking_reason"] == "no_ai" and gate["stack_count"] == 2
    )


def test_a_failed_provider_test_marks_the_connection_test_failed(app, client, company):
    cid = company["id"]
    p = provider(client, cid, test=False)
    c = add(client, cid, "ollama", model_provider_id=p["id"], model="missing-model:1b")
    r = client.post(url(cid, f"/{c['id']}/test"))
    assert r.status_code == 200
    assert r.json()["status"]["state"] == "test_failed" and r.json()["status"]["message"]
    assert stack(client, cid)["ready"] is False


def test_summary_percentages_and_backups(client, company):
    cid = company["id"]
    a = api_conn(client, cid, name="A", share=3)  # everything
    b = api_conn(client, cid, name="B", share=1, use_for="heavy")
    c = api_conn(client, cid, name="C", share=2, use_for="everyday")
    d = api_conn(client, cid, name="D", share=0)  # backup of everything
    s = stack(client, cid)["summary"]
    heavy = {e["connection_id"]: e for e in s["heavy"]}
    assert heavy[a["id"]]["percent"] == 75 and heavy[b["id"]]["percent"] == 25
    assert heavy[d["id"]] == {"connection_id": d["id"], "label": "D", "percent": 0, "backup": True}
    assert c["id"] not in heavy
    everyday = {e["connection_id"]: e for e in s["everyday"]}
    assert everyday[a["id"]]["percent"] == 60 and everyday[c["id"]]["percent"] == 40
    assert everyday[d["id"]]["backup"] is True
    assert sum(e["percent"] for e in s["heavy"]) == 100
    # a disabled connection drops out of the split
    client.patch(url(cid, f"/{b['id']}"), json={"enabled": False})
    heavy = {e["connection_id"]: e for e in stack(client, cid)["summary"]["heavy"]}
    assert heavy[a["id"]]["percent"] == 100 and b["id"] not in heavy
    # thirds: percentages still add up to 100
    for n in ("E", "F"):
        api_conn(client, cid, name=n, share=3)
    s = stack(client, cid)["summary"]["everyday"]
    assert sum(e["percent"] for e in s if not e.get("backup")) == 100


# ------------------------------------------------------------------ bootstrap from the legacy settings


def legacy(app, cid, **fields):
    set_policy_rows(app, cid, **fields)


def test_bootstrap_from_an_api_only_company(app, client, company):
    cid = company["id"]
    p = provider(client, cid)  # first provider becomes the company default
    s = stack(client, cid)
    assert [i["label"] for i in s["items"]] == ["Local Ollama"]
    i = s["items"][0]
    assert i["use_for"] == "everything" and i["share"] == 2 and i["status"]["state"] == "ready"
    assert i["model_provider_id"] == p["id"] and s["ready"] is True
    assert audit_actions(app).count("ai_stack.bootstrapped") == 1
    # idempotent: reading again changes nothing
    assert [x["id"] for x in stack(client, cid)["items"]] == [i["id"]]
    assert audit_actions(app).count("ai_stack.bootstrapped") == 1
    # never again after deletes
    client.delete(url(cid, f"/{i['id']}"))
    assert stack(client, cid)["items"] == []
    assert audit_actions(app).count("ai_stack.bootstrapped") == 1
    with app.state.session_factory() as db:
        flag = db.scalar(
            select(CompanyPolicy.ai_stack_bootstrapped).where(CompanyPolicy.company_id == cid)
        )
    assert flag is True


def test_bootstrap_with_a_separate_work_model_makes_heavy_and_everyday(app, client, company):
    cid = company["id"]
    provider(client, cid)
    w = provider(client, cid, name="Work Ollama")
    legacy(app, cid, work_model_provider_id=w["id"], work_model_name="qwen2.5:7b")
    items = stack(client, cid)["items"]
    assert [(i["label"], i["use_for"]) for i in items] == [
        ("Local Ollama", "heavy"),
        ("Work Ollama (everyday work)", "everyday"),
    ]
    assert stack(client, cid)["ready"] is True


def test_bootstrap_of_a_hybrid_cli_with_its_own_login_and_with_an_api_key(app, client, company):
    cid = company["id"]
    provider(client, cid)
    cred = credential(client, cid)
    with app.state.session_factory() as db:
        store.save(
            db, "codex", auth_mode="api_key", credential_id=cred["id"],
            last_test={"ok": True, "detail": "ok", "tested_at": "2026-10-28T00:00:00Z"},
        )  # fmt: skip
        db.commit()
    legacy(app, cid, work_mode="hybrid", work_runtime="cli:codex")
    items = stack(client, cid)["items"]
    assert [(i["option"], i["use_for"], i["auth_mode"]) for i in items] == [
        ("ollama", "heavy", None),
        ("codex_api", "everyday", "api_key"),
    ]
    assert items[1]["credential_id"] == cred["id"] and items[1]["status"]["state"] != "needs_test"
    assert stack(client, cid)["ready"] is True


def test_bootstrap_of_a_hybrid_claude_own_login_and_of_the_claude_plan_engine(
    app, client, company, data_dir, make_user, client_as
):
    cid = company["id"]
    provider(client, cid)
    with app.state.session_factory() as db:
        store.save(db, "claude_code", auth_mode="own_login",
                   last_test={"ok": True, "detail": "ok", "tested_at": "2026-10-28T00:00:00Z"})  # fmt: skip
        db.commit()
    legacy(app, cid, work_mode="hybrid", work_runtime="cli:claude_code")
    items = stack(client, cid)["items"]
    assert [(i["option"], i["use_for"]) for i in items] == [
        ("ollama", "heavy"),
        ("claude_plan", "everyday"),
    ]
    assert items[1]["home_dir"] == "claude-code"  # the B9 directory is reused
    # a second company on the claude_plan engine
    other = client_as(human(make_user("o3@x.test")))
    oc = other.post("/api/companies", json={"name": "Plan Co"}).json()
    set_policy_rows(app, oc["id"], ai_engine="claude_plan")
    got = other.get(url(oc["id"])).json()["items"]
    assert [(i["option"], i["use_for"], i["home_dir"]) for i in got] == [
        ("claude_plan", "everything", "claude-code")
    ]
    assert got[0]["status"]["state"] in ("needs_test", "not_installed", "needs_sign_in")


def test_adding_the_provider_a_bootstrap_just_adopted_does_not_duplicate_it(client, company):
    cid = company["id"]
    p = provider(client, cid)  # default + tested: a bootstrap would adopt it
    c = add(client, cid, "ollama", model_provider_id=p["id"], model=p["default_model"], share=4)
    items = stack(client, cid)["items"]
    assert len(items) == 1 and items[0]["id"] == c["id"] and items[0]["share"] == 4


# ------------------------------------------------------------------ PUT /ai-setup is gone


def test_ai_setup_is_derived_and_put_is_gone(client, company):
    cid = company["id"]
    g = client.get(api(cid, "/ai-setup")).json()
    assert g["ready"] is False and g["blocking_reason"] == "no_ai" and g["stack_count"] == 0
    assert "clis" in g
    make_ready(client.app, client, cid)
    g = client.get(api(cid, "/ai-setup")).json()
    assert g["ready"] is True and g["blocking_reason"] is None and g["stack_count"] == 1
    r = client.put(api(cid, "/ai-setup"), json={"management_provider_id": "x"})
    assert r.status_code == 410 and r.json()["error"]["code"] == "gone"


def test_the_b9_login_routes_are_gone(client, company, bin_dir):
    install_fake_cli(bin_dir, "claude", OK)
    assert client.get("/api/cli-runtimes/claude_code/login").status_code in (404, 405)
    assert client.post("/api/cli-runtimes/claude_code/login", json={}).status_code in (404, 405)
    assert client.delete("/api/cli-runtimes/claude_code/login").status_code in (404, 405)
    r = client.get("/api/cli-runtimes/claude_code")  # installation-level status stays
    assert r.status_code == 200 and r.json()["login"] is None


# ------------------------------------------------------------------ per-connection tests use the connection's auth


def test_cli_connections_test_with_their_own_key(app, client, company, bin_dir):
    cid = company["id"]
    install_fake_cli(bin_dir, "codex", CODEX_OK)
    k1, k2 = credential(client, cid, "k1", KEY), credential(client, cid, "k2", KEY2)
    c1 = add(client, cid, "codex_api", credential_id=k1["id"], label="Codex one")
    c2 = add(client, cid, "codex_api", credential_id=k2["id"], label="Codex two", model="gpt-x")
    assert c1["status"]["state"] == "needs_test" and c1["credential_id"] == k1["id"]
    for conn, key in ((c1, KEY), (c2, KEY2)):
        r = client.post(url(cid, f"/{conn['id']}/test"))
        assert r.status_code == 200, r.text
        assert r.json()["status"]["state"] == "ready", r.json()
        env = record_of(bin_dir, "codex")["env"]
        assert env["CODEX_API_KEY"] == key
        assert env["HOME"].endswith("cli-home") or "test-codex" in env["HOME"]
    assert "gpt-x" in " ".join(record_of(bin_dir, "codex")["argv"])
    assert KEY not in client.get(url(cid)).text + everything_written(app)
    assert KEY2 not in client.get(url(cid)).text + everything_written(app)
    assert stack(client, cid)["ready"] is True  # codex covers everything


def test_a_cli_test_needs_the_binary_and_reports_failures(client, company, bin_dir):
    cid = company["id"]
    cred = credential(client, cid)
    c = add(client, cid, "codex_api", credential_id=cred["id"])
    assert c["status"]["state"] == "not_installed"
    assert client.post(url(cid, f"/{c['id']}/test")).status_code == 409
    install_fake_cli(bin_dir, "codex", [], exit=2, stderr="login required")
    r = client.post(url(cid, f"/{c['id']}/test"))
    assert r.status_code == 200 and r.json()["status"]["state"] == "test_failed"
    assert "login required" in r.json()["status"]["last_test"]["detail"]


def test_claude_plan_test_runs_in_the_connections_own_config_dir(
    client, company, bin_dir, data_dir
):
    cid = company["id"]
    install_fake_cli(bin_dir, "claude", OK)
    c = add(client, cid, "claude_plan")
    assert c["home_dir"] == f"claude-code/{c['id']}"
    home = data_dir / c["home_dir"]
    assert home.is_dir() and (home.stat().st_mode & 0o777) == 0o700
    r = client.post(url(cid, f"/{c['id']}/test"))
    assert r.json()["status"]["state"] == "ready", r.json()
    env = record_of(bin_dir, "claude")["env"]
    assert env["CLAUDE_CONFIG_DIR"] == str(home)
    assert "ANTHROPIC_API_KEY" not in env


def test_cursor_plan_runs_in_an_isolated_home_without_an_api_key(
    client, company, bin_dir, data_dir
):
    cid = company["id"]
    install_fake_cli(bin_dir, "agent", CURSOR_OK)
    c = add(client, cid, "cursor_plan")
    assert c["home_dir"] == f"cursor-home/{c['id']}" and c["max_concurrency"] == 1
    r = client.post(url(cid, f"/{c['id']}/test"))
    assert r.json()["status"]["last_test"]["ok"] is True, r.json()
    rec = record_of(bin_dir, "agent")
    assert rec["env"]["HOME"] == str(data_dir / c["home_dir"])
    assert "CURSOR_API_KEY" not in rec["env"]
    assert "--force" not in rec["argv"]  # the test never gets to change anything


# ------------------------------------------------------------------ plan sign-in per connection


def login_url(cid, conn_id):
    return url(cid, f"/{conn_id}/login")


def wait_login(client, cid, conn_id, state="succeeded", timeout=15.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        body = client.get(login_url(cid, conn_id)).json()
        if body["login"]["state"] == state:
            return body
        time.sleep(0.05)
    raise AssertionError(f"sign-in never reached {state}: {body}")


def test_claude_login_status_login_logout_per_connection(app, client, company, bin_dir, data_dir):
    cid = company["id"]
    install_fake_cli(bin_dir, "claude", OK, auth={"signed_in": False, "login_lines": [
        "Opening browser...", f"token {TOKEN}", "visit https://claude.ai/oauth/authorize?code_challenge=abc&state=xyz",
        "also https://evil.example/steal?x=1"]})  # fmt: skip
    c = add(client, cid, "claude_plan")
    home = data_dir / c["home_dir"]
    s = client.get(login_url(cid, c["id"])).json()
    assert s["signed_in"] is False and s["login"]["state"] == "idle" and s["config_dir_ok"] is True
    assert stack(client, cid)["items"][0]["status"]["state"] == "needs_sign_in"
    r = client.post(login_url(cid, c["id"]), json={"method": "console"})
    assert r.status_code == 202, r.text
    done = wait_login(client, cid, c["id"])
    assert done["login"]["url"].startswith("https://claude.ai/oauth/authorize?")
    assert "evil.example" not in json.dumps(done)
    assert TOKEN not in json.dumps(done)
    argv = [e for e in auth_log(bin_dir) if e["argv"][1] == "login"][0]
    assert argv["argv"] == ["auth", "login", "--console"]
    assert argv["env"]["CLAUDE_CONFIG_DIR"] == str(home) and argv["cwd"]
    assert "COMPANY_OS_SECRET_KEY" not in argv["env"]
    s = client.get(login_url(cid, c["id"]) + "?refresh=1").json()
    assert s["signed_in"] is True and s["email"] == "founder@example.com"
    assert stack(client, cid)["items"][0]["status"]["account"] == "founder@example.com"
    # nothing but the URL is kept anywhere
    with app.state.session_factory() as db:
        dump = json.dumps(
            [r.last_test for r in db.scalars(select(AiConnection))]
        ) + everything_written(app)
    assert TOKEN not in dump and "ai_connection.login_started" in dump
    assert "ai_connection.login_finished" in audit_actions(app)
    # logout runs the CLI's own logout in this home and resets the test
    pass_test(app, c["id"])
    r = client.delete(login_url(cid, c["id"]))
    assert r.status_code == 200 and r.json()["signed_in"] is False
    assert ["auth", "logout"] in [e["argv"] for e in auth_log(bin_dir)]
    assert stack(client, cid)["items"][0]["status"]["state"] == "needs_sign_in"
    assert "ai_connection.logout" in audit_actions(app)
    assert TOKEN not in client.get(url(cid)).text


def test_login_is_single_flight_per_connection_and_needs_the_binary(client, company, bin_dir):
    cid = company["id"]
    c = add(client, cid, "claude_plan")
    assert client.get(login_url(cid, c["id"])).status_code == 409  # not installed
    assert client.post(login_url(cid, c["id"]), json={}).status_code == 409
    install_fake_cli(bin_dir, "claude", OK, auth={"signed_in": False, "login_sleep": 1.5})
    assert client.post(login_url(cid, c["id"]), json={}).status_code == 202
    r = client.post(login_url(cid, c["id"]), json={})
    assert r.status_code == 409 and r.json()["error"]["code"] == "login_in_progress"
    assert client.delete(login_url(cid, c["id"])).status_code == 409
    assert client.post(login_url(cid, c["id"]), json={"method": "nope"}).status_code in (409, 422)
    wait_login(client, cid, c["id"])
    # a failing login ends as failed with a message, no output kept
    install_fake_cli(bin_dir, "claude", OK, auth={"signed_in": False, "login_exit": 3,
                                                   "login_lines": [f"secret {TOKEN}"]})  # fmt: skip
    client.post(login_url(cid, c["id"]), json={})
    body = wait_login(client, cid, c["id"], state="failed")
    assert "exit code 3" in body["login"]["message"] and TOKEN not in json.dumps(body)


def test_only_plan_connections_have_a_login(client, company):
    cid = company["id"]
    c = api_conn(client, cid)
    for method in ("get", "post", "delete"):
        r = getattr(client, method)(login_url(cid, c["id"]))
        assert r.status_code == 404, (method, r.text)


def test_cursor_login_uses_an_isolated_home_and_never_opens_a_browser(
    app, client, company, bin_dir, data_dir
):
    cid = company["id"]
    install_fake_cli(bin_dir, "agent", CURSOR_OK)
    c = add(client, cid, "cursor_plan")
    home = data_dir / c["home_dir"]
    assert client.get(login_url(cid, c["id"])).json()["signed_in"] is False  # `agent status` exit 1
    assert client.post(login_url(cid, c["id"]), json={"method": "console"}).status_code == 202
    done = wait_login(client, cid, c["id"])
    assert done["login"]["url"] == "https://cursor.com/loginDeepControl?challenge=abc&uuid=xyz"
    assert "crsr_FAKE_TOKEN" not in json.dumps(done)
    calls = auth_log(bin_dir, "agent")
    login = next(e for e in calls if e["argv"] == ["login"])
    assert login["env"]["HOME"] == str(home) and login["env"]["NO_OPEN_BROWSER"] == "1"
    s = client.get(login_url(cid, c["id"]) + "?refresh=1").json()
    assert s["signed_in"] is True and s["email"] == "founder@example.com"
    assert client.delete(login_url(cid, c["id"])).json()["signed_in"] is False
    assert any(
        e["argv"] == ["logout"] and e["env"]["HOME"] == str(home)
        for e in auth_log(bin_dir, "agent")
    )
    assert "crsr_FAKE_TOKEN" not in everything_written(app) + client.get(url(cid)).text


def test_two_homes_never_share_a_sign_in(client, company, bin_dir, data_dir, make_user, client_as):
    install_fake_cli(bin_dir, "agent", CURSOR_OK)
    cid = company["id"]
    a = add(client, cid, "cursor_plan")
    other = client_as(human(make_user("o4@x.test")))
    other.app.state.cli_env.extra_dirs.append(bin_dir)
    other.app.state.cli_env.use_system_path = False
    oc = other.post("/api/companies", json={"name": "Other"}).json()["id"]
    b = other.post(url(oc), json={"option": "cursor_plan"}).json()
    assert a["home_dir"] != b["home_dir"]
    client.post(login_url(cid, a["id"]), json={})
    wait_login(client, cid, a["id"])
    assert client.get(login_url(cid, a["id"]) + "?refresh=1").json()["signed_in"] is True
    assert other.get(login_url(oc, b["id"]) + "?refresh=1").json()["signed_in"] is False


def test_deleting_a_plan_connection_signs_out_and_removes_its_home(
    app, client, company, bin_dir, data_dir
):
    cid = company["id"]
    install_fake_cli(bin_dir, "claude", OK)
    c = add(client, cid, "claude_plan")
    home = data_dir / c["home_dir"]
    (home / "marker").write_text("x")
    assert client.delete(url(cid, f"/{c['id']}")).status_code == 204
    assert not home.exists()
    assert ["auth", "logout"] in [e["argv"] for e in auth_log(bin_dir)]
    with app.state.session_factory() as db:
        rows = db.scalars(
            select(AuditEvent).where(AuditEvent.action == "ai_connection.removed")
        ).all()
        assert rows and rows[0].data["removed_home"] is True


def test_removing_a_connection_never_deletes_outside_the_plan_directories(data_dir):
    outside = data_dir / "precious"
    outside.mkdir(parents=True)
    assert plan_login.remove_home(data_dir, "precious") is False
    assert plan_login.remove_home(data_dir, "claude-code/../precious") is False
    assert outside.exists()


# ------------------------------------------------------------------ what the routing step calls


def test_run_auth_and_usable(app, client, company, bin_dir, data_dir):
    cid = company["id"]
    install_fake_cli(bin_dir, "agent", CURSOR_OK)
    cred = credential(client, cid)
    plan = add(client, cid, "cursor_plan")
    key = add(client, cid, "cursor_api", credential_id=cred["id"], model="auto")
    api_c = api_conn(client, cid)
    with app.state.session_factory() as db:
        p, k, a = (db.get(AiConnection, i["id"]) for i in (plan, key, api_c))
        pa = ai_stack.run_auth(db, p, data_dir=data_dir)
        assert pa.auth_mode == "own_login" and pa.plan_home == str(data_dir / plan["home_dir"])
        assert pa.max_concurrency == 1 and pa.credential_id is None and pa.cli_id == "cursor"
        ka = ai_stack.run_auth(db, k, data_dir=data_dir)
        assert ka.auth_mode == "api_key" and ka.credential_id == cred["id"] and ka.plan_home is None
        assert ka.max_concurrency is None and ka.model == "auto"
        assert ka.as_state()["connection_id"] == k.id
        with pytest.raises(ValueError):
            ai_stack.run_auth(db, a, data_dir=data_dir)
        # usable: needs a passing test, not paused, not signed out
        ok, why = ai_stack.usable(k, db=db, cli_env=app.state.cli_env)
        assert ok is False and "test" in why
        k.last_test = {"ok": True, "detail": "ok", "tested_at": "2026-10-28T00:00:00Z"}
        assert ai_stack.usable(k, db=db, cli_env=app.state.cli_env) == (True, None)
        until = utcnow() + timedelta(hours=2)
        ai_stack.record_plan_event(db, k.id, "usage_limit", until=until, message="limit")
        ok, why = ai_stack.usable(k)
        assert ok is False and "paused until" in why
        assert ai_stack.usable(k, now=until + timedelta(minutes=1)) == (True, None)
        ai_stack.record_plan_event(db, k.id, "login_expired")
        ok, why = ai_stack.usable(k)
        assert ok is False and "signed in again" in why
        ai_stack.clear_plan_event(db, k.id)
        assert ai_stack.usable(k) == (True, None)
        k.enabled = False
        assert ai_stack.usable(k)[0] is False
        db.commit()
    s = {i["id"]: i for i in stack(client, cid)["items"]}
    assert s[key["id"]]["status"]["state"] == "disabled"
    assert {"ai_connection.paused", "ai_connection.resumed"} <= set(audit_actions(app))


def test_a_paused_plan_shows_paused_but_still_counts_for_readiness(app, client, company, bin_dir):
    cid = company["id"]
    install_fake_cli(bin_dir, "claude", OK, auth={"signed_in": True})
    c = add(client, cid, "claude_plan")
    pass_test(app, c["id"])
    with app.state.session_factory() as db:
        ai_stack.record_plan_event(
            db, c["id"], "usage_limit", until=utcnow() + timedelta(hours=1), message="limit"
        )
        db.commit()
    s = stack(client, cid)
    assert s["items"][0]["status"]["state"] == "paused" and s["items"][0]["status"]["paused_until"]
    assert s["ready"] is True
    assert s["summary"]["heavy"] == []  # but it takes no share while paused


def test_runs_24h_counts_runs_on_the_connection(app, client, company):
    from app.models.run import TaskRun
    from tests.helpers import new_task

    cid = company["id"]
    c = api_conn(client, cid)
    agent = new_agent(client, cid)
    task = new_task(client, cid, title="t", assignee_agent_id=agent["id"])
    with app.state.session_factory() as db:
        for n, age in enumerate((1, 30)):
            db.add(TaskRun(company_id=cid, task_id=task["id"], agent_id=agent["id"], attempt_number=n + 1,
                           status="succeeded", runtime="scripted", idempotency_key=f"k{n}",
                           queued_at=utcnow(), started_at=utcnow() - timedelta(hours=age),
                           ai_connection_id=c["id"]))  # fmt: skip
        db.commit()
    assert stack(client, cid)["items"][0]["runs_24h"] == 1


# ------------------------------------------------------------------ the gate


def test_the_gate_opens_when_the_stack_becomes_ready(app, client, company):
    cid = company["id"]
    r = client.post(api(cid, "/notes"), json={"body": "hello"})
    assert r.status_code == 409 and r.json()["error"]["details"]["reason"] == "no_ai"
    api_conn(client, cid)
    assert client.post(api(cid, "/notes"), json={"body": "hello"}).status_code == 201


# ------------------------------------------------------------------ migration


def test_migration_creates_the_table_and_columns_and_downgrades(tmp_path):
    import sqlalchemy as sa

    from alembic import command
    from tests.conftest import alembic_config

    url_ = f"sqlite:///{tmp_path / 'm.db'}"
    cfg = alembic_config(url_)
    command.upgrade(cfg, "0016")
    engine = sa.create_engine(url_)
    assert "ai_connections" not in sa.inspect(engine).get_table_names()
    command.upgrade(cfg, "0017")
    insp = sa.inspect(engine)
    cols = {c["name"] for c in insp.get_columns("ai_connections")}
    assert {"company_id", "position", "engine", "model_provider_id", "cli_id", "auth_mode",
            "credential_id", "cli_provider_id", "use_for", "share", "enabled", "home_dir",
            "last_test", "paused_until", "pause_reason"} <= cols  # fmt: skip
    assert "ai_connection_id" in {c["name"] for c in insp.get_columns("task_runs")}
    flag = {c["name"]: c for c in insp.get_columns("company_policies")}["ai_stack_bootstrapped"]
    assert flag["nullable"] is False
    command.downgrade(cfg, "0016")
    insp = sa.inspect(engine)
    assert "ai_connections" not in insp.get_table_names()
    assert "ai_connection_id" not in {c["name"] for c in insp.get_columns("task_runs")}
    assert "ai_stack_bootstrapped" not in {c["name"] for c in insp.get_columns("company_policies")}
    command.upgrade(cfg, "head")


# ------------------------------------------------------------------ sign-in state parsing and caching


def test_parse_status_claude_and_cursor():
    p = plan_login.parse_status
    out = p("claude_code", '{"loggedIn": true, "authMethod": "claude.ai", "email": "a@b.test"}', 0)
    assert out == {"signed_in": True, "auth_method": "claude.ai", "email": "a@b.test"}
    assert p("claude_code", '{"loggedIn": false, "authMethod": "none"}', 1)["signed_in"] is False
    assert p("claude_code", "garbage", 0)["signed_in"] is False  # fails closed
    assert p("claude_code", '{"loggedIn": true}', 1)["signed_in"] is False  # exit code wins
    # Cursor: the output format is undocumented, so parse leniently on exit code and wording
    assert p("cursor", "Logged in as dev@example.com", 0) == {
        "signed_in": True, "auth_method": None, "email": "dev@example.com"}  # fmt: skip
    assert p("cursor", "Not logged in", 0)["signed_in"] is False
    assert p("cursor", "Not authenticated. Run `agent login`.", 1)["signed_in"] is False
    assert p("cursor", "", 0)["signed_in"] is False
    assert p("cursor", "Logged in", 1)["signed_in"] is False


def test_status_is_cached_for_30_seconds_and_the_env_is_minimal(
    app, client, company, bin_dir, data_dir, monkeypatch
):
    cid = company["id"]
    monkeypatch.setenv("COMPANY_OS_SECRET_KEY", "must-not-leak")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "must-not-leak")
    install_fake_cli(bin_dir, "claude", OK, auth={"signed_in": True})
    c = add(client, cid, "claude_plan")
    client.get(login_url(cid, c["id"]))
    client.get(login_url(cid, c["id"]))
    status_calls = [e for e in auth_log(bin_dir) if e["argv"] == ["auth", "status"]]
    assert len(status_calls) == 1
    client.get(login_url(cid, c["id"]) + "?refresh=1")
    assert len([e for e in auth_log(bin_dir) if e["argv"] == ["auth", "status"]]) == 2
    env = status_calls[0]["env"]
    assert env["CLAUDE_CONFIG_DIR"] == str(data_dir / c["home_dir"])
    assert "COMPANY_OS_SECRET_KEY" not in env and "ANTHROPIC_API_KEY" not in env


# ------------------------------------------------------------------ runs take their auth from the connection


def _prep_for(app, client, cid, conn_id, tmp_path):
    from tests.helpers import new_task
    from tests.test_b9_claude_plan_routing import request_for

    agent = new_agent(client, cid, name=f"Sam{conn_id[:4]}", runtime="company_agent")
    task = new_task(client, cid, title="work", kind="work", assignee_agent_id=agent["id"])
    request = request_for(app, cid, agent["id"], task["id"], tmp_path)
    object.__setattr__(request, "ai_connection_id", conn_id)
    adapter_id = {"claude_code": "cli:claude_code", "cursor": "cli:cursor", "codex": "cli:codex"}
    return request, adapter_id


def test_runs_use_the_connections_plan_home_key_and_concurrency(
    app, client, company, bin_dir, tmp_path, data_dir
):
    cid = company["id"]
    for binary, lines in (("claude", OK), ("agent", CURSOR_OK), ("codex", CODEX_OK)):
        install_fake_cli(bin_dir, binary, lines)
    api_conn(client, cid)  # a ready AI, so agents can be created
    claude = add(client, cid, "claude_plan")
    cursor = add(client, cid, "cursor_plan")
    k1, k2 = credential(client, cid, "k1", KEY), credential(client, cid, "k2", KEY2)
    codex1 = add(client, cid, "codex_api", credential_id=k1["id"])
    codex2 = add(client, cid, "codex_api", credential_id=k2["id"])

    def prep_of(conn, adapter_name):
        request, _ = _prep_for(app, client, cid, conn["id"], tmp_path)
        adapter = app.state.runtimes.get(adapter_name)
        prep = adapter._prepare(request)
        return adapter, prep, adapter.build(prep)

    adapter, prep, inv = prep_of(claude, "cli:claude_code")
    assert prep.auth_mode == "own_login" and prep.ai_connection_id == claude["id"]
    assert prep.config_dir == data_dir / claude["home_dir"]
    assert inv.env["CLAUDE_CONFIG_DIR"] == str(data_dir / claude["home_dir"])
    assert "ANTHROPIC_API_KEY" not in inv.env
    adapter, prep, inv = prep_of(cursor, "cli:cursor")
    assert prep.auth_mode == "own_login" and prep.home == data_dir / cursor["home_dir"]
    assert inv.env["HOME"] == str(data_dir / cursor["home_dir"]) and "CURSOR_API_KEY" not in inv.env
    assert "--force" in inv.argv  # the run itself keeps the documented deny rules
    for conn, key in ((codex1, KEY), (codex2, KEY2)):
        adapter, prep, inv = prep_of(conn, "cli:codex")
        assert prep.auth_mode == "api_key" and prep.secret == key
        assert inv.env["CODEX_API_KEY"] == key
    # a connection of another CLI (or company) is refused
    from app.runtimes.base import RuntimeAdapterError

    request, _ = _prep_for(app, client, cid, claude["id"], tmp_path)
    with pytest.raises(RuntimeAdapterError):
        app.state.runtimes.get("cli:codex")._prepare(request)
