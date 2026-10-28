"""Radar briefs: one per kind per day, the artifact body, cited items becoming used, scoring."""

from __future__ import annotations

from sqlalchemy import func, select

from app.models.artifact import Artifact
from app.models.radar import RadarBrief, RadarItem
from tests.helpers import api, new_agent
from tests.radar.wp2_helpers import (
    founder_session,
    radar_world,
    researcher_session,
    set_radar_policy,
    start_run,
)

BASE = "/radar"


def brief_body(items, **extra):
    return {
        "kind": "daily",
        "summary": "People find setup slow and say competitors are faster.",
        "themes": [
            {
                "title": "Setup is slow",
                "kind": "critique",
                "item_ids": items[:2],
                "why": "two threads",
            },
            {"title": "Faster rivals", "kind": "competitor", "item_ids": items[2:3]},
        ],
        "relevance": [
            {"item_id": items[0], "relevance": 90},
            {"item_id": items[1], "relevance": 40},
        ],
        "body": "# Daily radar\n\nSetup is slow.",
        **extra,
    }


def test_a_brief_stores_themes_scores_and_a_report_artifact(app, client, company):
    cid, _, items = radar_world(app, client, company)
    agent, sess = researcher_session(app, client, cid)
    r = sess.post(BASE + "/briefs", json=brief_body(items))
    assert r.status_code == 201, r.text
    out = r.json()
    assert out["kind"] == "daily" and out["item_count"] == 3 and out["items_marked_used"] == 3
    with app.state.session_factory() as db:
        artifact = db.get(Artifact, out["artifact_id"])
        assert artifact.kind == "report" and artifact.task_id == sess.task_id
        rows = {i.id: i for i in db.scalars(select(RadarItem).where(RadarItem.company_id == cid))}
        assert {i.status for i in rows.values()} == {"used"}
        assert rows[items[0]].relevance == 90 and rows[items[1]].relevance == 40
        brief = db.get(RadarBrief, out["id"])
        assert brief.author_agent_id == agent["id"] and brief.period_day is not None
        assert [t["title"] for t in brief.themes] == ["Setup is slow", "Faster rivals"]
    listed = client.get(api(cid, "/radar/briefs")).json()["items"]
    assert [b["id"] for b in listed] == [out["id"]] and listed[0]["author_name"] == agent["name"]
    detail = client.get(api(cid, f"/radar/briefs/{out['id']}")).json()
    assert {i["id"] for i in detail["items"]} == set(items)
    assert detail["themes"][0]["item_ids"] == items[:2]


def test_one_brief_per_kind_per_day_and_the_second_changes_nothing(app, client, company):
    cid, _, items = radar_world(app, client, company)
    _, sess = researcher_session(app, client, cid)
    first = sess.post(BASE + "/briefs", json=brief_body(items))
    assert first.status_code == 201
    again = sess.post(BASE + "/briefs", json=brief_body(items, summary="Different words"))
    assert again.status_code == 409 and again.json()["error"]["code"] == "brief_exists"
    assert again.json()["error"]["details"]["existing_brief_id"] == first.json()["id"]
    with app.state.session_factory() as db:
        assert db.scalar(select(func.count(RadarBrief.id))) == 1
        assert db.scalar(select(func.count(Artifact.id)).where(Artifact.company_id == cid)) == 1
    other_kind = sess.post(
        BASE + "/briefs", json=brief_body(items, kind="ui_inspiration", body="# Board")
    )
    assert other_kind.status_code == 201
    assert (
        client.get(api(cid, "/radar/briefs"), params={"kind": "ui_inspiration"}).json()["items"][0][
            "kind"
        ]
        == "ui_inspiration"
    )


def test_brief_validation_and_foreign_ids(app, client, company):
    cid, _, items = radar_world(app, client, company)
    _, sess = researcher_session(app, client, cid)
    unknown = sess.post(
        BASE + "/briefs", json=brief_body(["no-such-item", "no-such-2", "no-such-3"])
    )
    assert unknown.status_code == 422
    both = sess.post(BASE + "/briefs", json=brief_body(items, artifact_id="abc"))
    assert both.status_code == 422
    foreign = sess.post(BASE + "/briefs", json=brief_body(items, body=None, artifact_id="not-mine"))
    assert foreign.status_code == 422
    assert sess.post(BASE + "/briefs", json={"summary": ""}).status_code == 422
    assert (
        sess.post(
            BASE + "/briefs", json=brief_body(items, themes=[{"title": "t", "kind": "gossip"}])
        ).status_code
        == 422
    )
    other = client.post("/api/companies", json={"name": "Other Co", "mission": "m"}).json()
    _, _, other_items = radar_world(app, client, other)
    assert (
        sess.post(BASE + "/briefs", json=brief_body(other_items)).status_code == 422
    )  # another company's items


def test_a_brief_can_point_at_an_artifact_the_agent_submitted(app, client, company):
    cid, _, items = radar_world(app, client, company)
    _, sess = researcher_session(app, client, cid)
    artifact = sess.post(
        "/artifacts", json={"title": "Radar report", "kind": "report", "content": "# R"}
    ).json()
    r = sess.post(BASE + "/briefs", json=brief_body(items, body=None, artifact_id=artifact["id"]))
    assert r.status_code == 201 and r.json()["artifact_id"] == artifact["id"]


def test_who_may_write_briefs_and_when(app, client, company):
    cid, _, items = radar_world(app, client, company)
    plain = new_agent(client, cid, name="Pat")
    assert (
        start_run(app, client, cid, plain["id"])
        .post(BASE + "/briefs", json=brief_body(items))
        .status_code
        == 403
    )
    founder = founder_session(app, client, cid)
    assert (
        founder.post(BASE + "/briefs", json=brief_body(items, kind="self_critique")).status_code
        == 201
    )
    set_radar_policy(app, cid, self_critique_enabled=False)
    other = client.post("/api/companies", json={"name": "Other Co", "mission": "m"}).json()
    ocid, _, oitems = radar_world(app, client, other)
    set_radar_policy(app, ocid, self_critique_enabled=False)
    off = founder_session(app, client, ocid).post(
        BASE + "/briefs", json=brief_body(oitems, kind="self_critique")
    )
    assert off.status_code == 409
    set_radar_policy(app, ocid, radar_enabled=False)
    assert (
        founder_session(app, client, ocid)
        .post(BASE + "/briefs", json=brief_body(oitems))
        .status_code
        == 409
    )


def test_ideas_proposed_in_the_same_run_are_counted_on_the_brief(app, client, company):
    cid, _, items = radar_world(app, client, company)
    _, sess = researcher_session(app, client, cid)
    brief = sess.post(BASE + "/briefs", json=brief_body(items)).json()
    idea = sess.post(
        BASE + "/ideas",
        json={
            "title": "Guided setup wizard",
            "problem": "Setup is slow",
            "proposal": "A wizard",
            "item_ids": items[:1],
        },
    )
    assert idea.status_code == 201
    detail = client.get(api(cid, f"/radar/briefs/{brief['id']}")).json()
    assert detail["ideas_created"] == 1 and [i["id"] for i in detail["ideas"]] == [
        idea.json()["id"]
    ]
