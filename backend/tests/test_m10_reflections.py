"""M10 section 4: reflections, the Founder's reactions and company standards."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models.autonomy import WakeRequest
from app.models.reflections import CompanyStandard
from tests.conftest import human
from tests.guardrail_helpers import set_family
from tests.helpers import agent_of_family, api, new_agent, new_task
from tests.test_agent_api import start_run

BASE = "/api/agent/v1"
RULE = "Every launch gets a second review before it ships."


@pytest.fixture
def cof(app, client, company):
    """The AI Co-founder mid-run on a task (its run token)."""
    founder = agent_of_family(client, company["id"], "founder")
    return start_run(app, client, company["id"], founder["id"], task_title="Reflect")


def post(cof, **overrides):
    body = {
        "kind": "observation",
        "title": "Reviews catch bugs",
        "body": "Three revisions this week.",
    }
    return cof.post("/reflections", json={**body, **overrides})


def proposal(cof, **overrides):
    return post(
        cof,
        kind="standard_proposal",
        title="Second review before shipping",
        body="Two launches slipped for lack of a second pair of eyes.",
        proposed_standard=RULE,
        **overrides,
    )


def respond(client, cid, rid, reaction="endorse", body=None):
    return client.post(
        api(cid, f"/reflections/{rid}/responses"), json={"reaction": reaction, "body": body}
    )


def wakes(app) -> list[WakeRequest]:
    with app.state.session_factory() as db:
        return list(db.scalars(select(WakeRequest)))


# ------------------------------------------------------------------ posting


def test_the_cofounder_posts_and_the_founder_reads(client, company, cof):
    cid = company["id"]
    task = new_task(client, cid, title="Ship v1")
    r = post(
        cof,
        kind="retro",
        evidence=[{"type": "task", "id": task["id"]}, {"type": "task", "id": task["id"]}],
    )
    assert r.status_code == 201, r.text
    ref = r.json()
    assert ref["status"] == "open" and ref["kind"] == "retro" and ref["author_name"]
    assert ref["evidence"] == [
        {"type": "task", "id": task["id"], "label": "Ship v1"}
    ]  # deduped, labelled
    assert ref["response_count"] == 0 and ref["last_response"] is None
    listed = client.get(api(cid, "/reflections")).json()
    assert [i["id"] for i in listed["items"]] == [ref["id"]] and listed["next_cursor"] is None
    detail = client.get(api(cid, f"/reflections/{ref['id']}")).json()
    assert detail["responses"] == [] and detail["body"] == "Three revisions this week."
    assert client.get(api(cid, "/reflections"), params={"status": "adopted"}).json()["items"] == []
    # a duplicate title within a week returns the existing reflection
    again = post(cof, kind="retro")
    assert again.status_code == 200 and again.json()["id"] == ref["id"]
    events = client.get(api(cid, "/activity")).text
    assert "reflection.created" in events


def test_validation_limits_and_secrets(client, company, cof):
    assert post(cof, kind="gossip").status_code == 422
    assert post(cof, kind="standard_proposal").status_code == 422  # needs proposed_standard
    assert post(cof, proposed_standard="a rule").status_code == 422  # only for proposals
    assert post(cof, proposed_standard="x" * 501, kind="standard_proposal").status_code == 422
    assert post(cof, body="x" * 6200).status_code == 422
    assert post(cof, evidence=[{"type": "task", "id": "nope"}]).status_code == 422
    r = post(cof, body="our key is sk-ant-api03-abcdefghijklmnopqrstuvwxyz0123456789")
    assert r.status_code == 409 and "secrets" in r.json()["error"]["message"]
    for i in range(5):
        assert post(cof, title=f"Observation {i}").status_code == 201
    r = post(cof, title="One too many")
    assert r.status_code == 409 and r.json()["error"]["code"] == "limit_exceeded"


def test_evidence_of_another_company_is_rejected(app, client, company, cof, make_user, client_as):
    other = client_as(human(make_user("o@x.test")))
    oc = other.post("/api/companies", json={"name": "Other"}).json()
    foreign = other.post(api(oc["id"], "/tasks"), json={"title": "Secret plan"})
    # the other company has no AI gate in this test module (ENFORCED is off), so the task exists
    assert foreign.status_code == 201
    r = post(cof, evidence=[{"type": "task", "id": foreign.json()["id"]}])
    assert r.status_code == 422


def test_only_the_cofounder_writes_reflections(app, client, company, cof):
    cid = company["id"]
    worker = new_agent(client, cid, name="Wes")
    session = start_run(app, client, cid, worker["id"])
    for path, body in (
        ("/reflections", {"kind": "observation", "title": "t", "body": "b"}),
        ("/reflections/x/replies", {"body": "b"}),
        ("/reflections/x/revise", {"body": "b"}),
    ):
        r = session.post(path, json=body)
        assert r.status_code == 403, (path, r.text)
    # humans cannot use the agent API
    assert client.post(
        BASE + "/reflections", json={"kind": "risk", "title": "t", "body": "b"}
    ).status_code in (401, 403)


# ------------------------------------------------------------------ the Founder reacts


def test_reactions_set_the_status_and_wake_the_cofounder(app, client, company, cof):
    cid = company["id"]
    ref = post(cof).json()
    r = respond(client, cid, ref["id"], "challenge", "Is that really three?")
    assert r.status_code == 201, r.text
    resp = r.json()
    assert (
        resp["reaction"] == "challenge"
        and resp["reflection_status"] == "open"
        and resp["standard_id"] is None
    )
    assert resp["author_type"] == "human" and resp["author_name"]
    [wake] = wakes(app)
    assert wake.trigger == "reflection" and "challenged" in wake.reasons[0]["text"]
    assert (
        "Is that really three?" in wake.reasons[0]["text"] and ref["id"] in wake.reasons[0]["text"]
    )
    # the wake is fast: 5 seconds, not the note debounce
    from datetime import timedelta

    from app.core.time import utcnow

    assert wake.not_before <= utcnow() + timedelta(seconds=8)
    # endorse a plain observation -> endorsed, no standard
    r = respond(client, cid, ref["id"], "endorse")
    assert r.json()["reflection_status"] == "endorsed" and r.json()["standard_id"] is None
    detail = client.get(api(cid, f"/reflections/{ref['id']}")).json()
    assert [x["reaction"] for x in detail["responses"]] == ["challenge", "endorse"]
    assert detail["response_count"] == 2 and detail["last_response"]["reaction"] == "endorse"
    # challenge, refine and comment need text
    assert respond(client, cid, ref["id"], "challenge").status_code == 422
    assert respond(client, cid, ref["id"], "refine", "  ").status_code == 422


def test_endorsing_a_standard_proposal_adopts_it_and_injects_it_into_every_agents_context(
    app, client, company, cof
):
    cid = company["id"]
    ref = proposal(cof).json()
    assert client.get(api(cid, "/standards")).json()["items"] == []  # a proposal is not a rule
    r = respond(client, cid, ref["id"], "endorse")
    assert r.json()["reflection_status"] == "adopted" and r.json()["standard_id"]
    [std] = client.get(api(cid, "/standards")).json()["items"]
    assert std["id"] == r.json()["standard_id"] and std["status"] == "active"
    assert std["statement"] == RULE and std["source_reflection_id"] == ref["id"]
    assert std["adopted_by_user_id"] and std["title"] == "Second review before shipping"
    assert client.get(api(cid, f"/reflections/{ref['id']}")).json()["standard_id"] == std["id"]
    # ... and every agent sees it: its context, its state
    worker = new_agent(client, cid, name="Wes")
    session = start_run(app, client, cid, worker["id"])
    ctx = session.get("/context").json()
    assert [s["statement"] for s in ctx["standards"]] == [RULE]
    assert [s["statement"] for s in session.get("/state").json()["standards"]] == [RULE]
    assert [s["statement"] for s in cof.get("/context").json()["standards"]] == [RULE]


def test_a_revision_must_be_endorsed_again_and_the_standard_is_the_revised_text(
    app, client, company, cof
):
    cid = company["id"]
    ref = proposal(cof).json()
    respond(client, cid, ref["id"], "refine", "Shorter, please.")
    r = cof.post(
        f"/reflections/{ref['id']}/revise",
        json={"proposed_standard": "Launches get a second review.", "note": "Shortened."},
    )
    assert r.status_code == 200 and r.json()["status"] == "revised"
    assert r.json()["proposed_standard"] == "Launches get a second review."
    assert client.get(api(cid, "/standards")).json()["items"] == []  # revising adopts nothing
    r = respond(client, cid, ref["id"], "endorse")
    assert r.json()["reflection_status"] == "adopted"
    [std] = client.get(api(cid, "/standards")).json()["items"]
    assert std["statement"] == "Launches get a second review."
    # an adopted standard is changed by people only
    r = cof.post(f"/reflections/{ref['id']}/revise", json={"proposed_standard": "Never review."})
    assert r.status_code == 409


def test_the_cofounder_can_never_adopt_retire_or_edit_a_standard(app, client, company, cof):
    cid = company["id"]
    ref = proposal(cof).json()
    respond(client, cid, ref["id"], "endorse")
    [std] = client.get(api(cid, "/standards")).json()["items"]
    # no agent-API route touches standards
    assert cof.post("/standards", json={"title": "t", "statement": "s"}).status_code in (404, 405)
    assert cof.patch(f"/standards/{std['id']}", json={"status": "retired"}).status_code in (
        404,
        405,
    )
    # the owner API refuses an agent's run token
    for method, path, body in (
        ("post", api(cid, "/standards"), {"title": "t", "statement": "s"}),
        ("patch", api(cid, f"/standards/{std['id']}"), {"status": "retired"}),
        ("post", api(cid, f"/reflections/{ref['id']}/responses"), {"reaction": "endorse"}),
        ("post", api(cid, f"/reflections/{ref['id']}/archive"), None),
        ("get", api(cid, "/standards"), None),
        ("get", api(cid, "/reflections"), None),
    ):
        r = getattr(client, method)(path, headers=cof.headers, **({"json": body} if body else {}))
        assert r.status_code == 403, (path, r.text)
    # a reply by the Co-founder does not change status
    r = cof.post(f"/reflections/{ref['id']}/replies", json={"body": "Thanks!"})
    assert r.status_code == 201 and r.json()["reaction"] == "comment"
    assert client.get(api(cid, f"/reflections/{ref['id']}")).json()["status"] == "adopted"
    # nor can it endorse its own proposal by any other means
    other = post(
        cof, kind="standard_proposal", title="Another", proposed_standard="Another rule."
    ).json()
    cof.post(f"/reflections/{other['id']}/replies", json={"body": "I endorse this."})
    assert len(client.get(api(cid, "/standards")).json()["items"]) == 1


def test_standards_are_human_authored_editable_and_retirable(app, client, company, cof):
    cid = company["id"]
    r = client.post(
        api(cid, "/standards"),
        json={
            "title": "Plain language",
            "statement": "Write plainly.",
            "applies_to": ["marketing"],
        },
    )
    assert r.status_code == 201, r.text
    std = r.json()
    assert std["source_reflection_id"] is None and std["applies_to"] == ["marketing"]
    assert (
        client.patch(
            api(cid, f"/standards/{std['id']}"), json={"statement": "Write very plainly."}
        ).json()["statement"]
        == "Write very plainly."
    )
    writer = new_agent(client, cid, name="Wes")
    eng = new_agent(client, cid, name="Eve")
    set_family(app, writer["id"], "marketing")
    set_family(app, eng["id"], "engineering")
    seen = lambda agent: [  # noqa: E731
        s["title"]
        for s in start_run(app, client, cid, agent["id"]).get("/context").json()["standards"]
    ]
    assert seen(writer) == ["Plain language"] and seen(eng) == []  # scoped by role family
    r = client.patch(api(cid, f"/standards/{std['id']}"), json={"status": "retired"})
    assert r.json()["status"] == "retired"
    assert seen(writer) == []
    assert client.get(api(cid, "/standards"), params={"status": "active"}).json()["items"] == []
    assert (
        len(client.get(api(cid, "/standards"), params={"status": "retired"}).json()["items"]) == 1
    )
    assert client.patch(api(cid, f"/standards/{std['id']}"), json={}).status_code == 409
    assert client.patch(api(cid, "/standards/nope"), json={"status": "active"}).status_code == 404
    assert (
        client.post(api(cid, "/standards"), json={"title": "t", "statement": "x" * 501}).status_code
        == 422
    )


def test_archive_and_closed_reflections(client, company, cof):
    cid = company["id"]
    ref = post(cof).json()
    r = client.post(api(cid, f"/reflections/{ref['id']}/archive"))
    assert r.status_code == 200 and r.json()["status"] == "archived"
    assert client.post(api(cid, f"/reflections/{ref['id']}/archive")).status_code == 409
    assert respond(client, cid, ref["id"], "endorse").status_code == 409
    assert cof.post(f"/reflections/{ref['id']}/replies", json={"body": "x"}).status_code == 409


def test_what_the_cofounder_still_has_to_answer_is_in_its_state(client, company, cof):
    cid = company["id"]
    ref = proposal(cof).json()
    assert cof.get("/state").json()["reflections_to_answer"] == []
    respond(client, cid, ref["id"], "challenge", "Why two reviewers?")
    todo = cof.get("/state").json()["reflections_to_answer"]
    assert [t["id"] for t in todo] == [ref["id"]]
    assert (
        todo[0]["latest_reaction"] == "challenge"
        and todo[0]["latest_response"] == "Why two reviewers?"
    )
    cof.post(f"/reflections/{ref['id']}/replies", json={"body": "One is enough, you are right."})
    assert cof.get("/state").json()["reflections_to_answer"] == []


# ------------------------------------------------------------------ roles & tenancy


def test_roles_and_tenancy(app, client, company, cof, make_user, add_member, client_as):
    cid = company["id"]
    ref = proposal(cof).json()
    users = {}
    for role in ("admin", "member", "viewer"):
        u = make_user(f"{role}@x.test")
        add_member(cid, u.id, role)
        users[role] = client_as(human(u))
    stranger = client_as(human(make_user("s@x.test")))
    for role, c in users.items():  # everyone in the company reads
        assert c.get(api(cid, "/reflections")).status_code == 200, role
        assert c.get(api(cid, "/standards")).status_code == 200, role
    assert respond(users["admin"], cid, ref["id"], "comment", "ok").status_code == 201
    for role in ("member", "viewer"):  # only owners and admins react or set standards
        assert respond(users[role], cid, ref["id"], "endorse").status_code == 403, role
        assert (
            users[role]
            .post(api(cid, "/standards"), json={"title": "t", "statement": "s"})
            .status_code
            == 403
        )
        assert users[role].post(api(cid, f"/reflections/{ref['id']}/archive")).status_code == 403
    for path in ("/reflections", f"/reflections/{ref['id']}", "/standards"):
        assert stranger.get(api(cid, path)).status_code == 404
    assert respond(stranger, cid, ref["id"], "endorse").status_code == 404
    assert client.get(api(cid, "/reflections/nope")).status_code == 404


def test_standards_and_reflections_are_scoped_to_their_company(
    app, client, company, cof, make_user, client_as
):
    cid = company["id"]
    ref = proposal(cof).json()
    respond(client, cid, ref["id"], "endorse")
    owner2 = client_as(human(make_user("o2@x.test")))
    c2 = owner2.post("/api/companies", json={"name": "Second"}).json()["id"]
    assert owner2.get(api(c2, "/reflections")).json()["items"] == []
    assert owner2.get(api(c2, "/standards")).json()["items"] == []
    assert owner2.get(api(c2, f"/reflections/{ref['id']}")).status_code == 404
    founder2 = agent_of_family(owner2, c2, "founder")
    s2 = start_run(app, owner2, c2, founder2["id"])
    assert s2.get("/context").json()["standards"] == []
    with app.state.session_factory() as db:
        assert {r.company_id for r in db.scalars(select(CompanyStandard))} == {cid}


def test_new_routes_and_tools_are_classified_in_the_guardrail_registry():
    from app.guardrails import registry

    for method, path, action in (
        ("POST", "/reflections", "reflection.post"),
        ("POST", "/reflections/{reflection_id}/replies", "reflection.reply"),
        ("POST", "/reflections/{reflection_id}/revise", "reflection.revise"),
    ):
        spec = registry.route_spec(method, path)
        assert spec is not None and spec.action == action
        assert registry.get_action(action).tier == "internal_write"
    for tool in (
        "company_post_reflection",
        "company_reply_reflection",
        "company_revise_reflection",
    ):
        assert registry.tier_of_tool(tool) == "internal_write"
    human_only = registry.HUMAN_ONLY["cli_runtime.manage"]
    assert human_only.human_only and human_only.tier == "identity"
    from app.services import agent_toolkit

    assert {
        "company_post_reflection",
        "company_reply_reflection",
        "company_revise_reflection",
    } <= set(agent_toolkit.FOUNDER_TOOLS)
    # nobody else gets the reflection tools
    others = {
        *agent_toolkit.WORKER_TOOLS,
        *agent_toolkit.MANAGER_EXTRA,
        *agent_toolkit.LEAD_TOOLS,
        *agent_toolkit.HR_TOOLS,
    }
    assert not {t for t in others if "reflection" in t}
