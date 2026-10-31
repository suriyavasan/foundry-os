"""M22 + M19: a human-work request reaches the person's linked chat with the questions preference."""

from __future__ import annotations

from app.core.principal import CompanyContext, Principal, PrincipalType, Role
from app.models.chat import ChatLink
from app.models.work import Task
from app.schemas.human_work import HumanWorkRequest
from app.services import human_work
from tests.helpers import new_agent
from tests.m19_helpers import link_owner, url
from tests.test_m19_channels import outbound, rows, w  # noqa: F401  (``w`` is a fixture)


def request_work(w, client, name="Nova"):
    agent = new_agent(client, w.cid, name=name)
    ctx = CompanyContext(
        w.cid, Principal(PrincipalType.AGENT, agent["id"], agent["name"], w.cid), Role.AGENT
    )
    with w.factory() as db:
        task, _, _ = human_work.request(
            db,
            ctx,
            None,
            HumanWorkRequest(
                title="Sign the NDA",
                instructions="Open the e-sign mail and sign page 3.",
                category="signature",
                why_human="A legal signature",
                blocks_my_task=False,
            ),
        )
        return task.id


def test_a_human_work_request_arrives_in_chat_and_follows_the_questions_preference(w, client):
    link_owner(w)
    w.platforms.tg_sent.clear()
    task_id = request_work(w, client)
    w.supervisor.tick()
    text = w.platforms.texts("telegram")[-1]
    assert "Nova needs a person: Sign the NDA" in text and "sign page 3" in text
    assert "Open Foundry" in text and "Due:" in text
    assert [m.ref_id for m in outbound(w, ref_type="human_work")]
    # a finished task is not announced any more (rendered at send time)
    task_id2 = request_work(w, client, "Kim")
    with w.factory() as db:
        db.get(Task, task_id2).status = "cancelled"
        db.commit()
    n = len(w.platforms.tg_sent)
    w.supervisor.tick()
    assert len(w.platforms.tg_sent) == n
    assert any(m.error == "no_longer_open" for m in outbound(w, ref_type="human_work"))
    # switch the questions preference off: nothing more is queued
    link = rows(w, ChatLink)[0]
    r = w.client.patch(url(w, f"/channel-links/{link.id}"), json={"notify": {"questions": False}})
    assert r.status_code == 200
    before = len(outbound(w, ref_type="human_work"))
    request_work(w, client, "Lee")
    w.supervisor.tick()
    assert len(outbound(w, ref_type="human_work")) == before
    assert task_id
