"""Shared helpers for the M22 tests: a company with people of every role and agents with run tokens."""

from __future__ import annotations

import pytest

from tests.accounts_helpers import create_company, do_setup, invite, join, member_ref
from tests.helpers import agent_of_family, api, new_agent, new_task
from tests.test_agent_api import start_run


@pytest.fixture
def world(app):
    owner = do_setup(app)
    cid = create_company(owner)["id"]
    people = {"owner": owner}
    for role in ("admin", "member", "viewer"):
        people[role] = join(app, invite(owner, cid, f"{role}@example.com", role), role)
    people["member2"] = join(app, invite(owner, cid, "member2@example.com", "member"), "member2")
    return people, cid


def refs(people, cid) -> dict[str, str]:
    return {k: member_ref(b, cid) for k, b in people.items()}


def member_id(people, cid, who: str) -> str:
    return member_ref(people[who], cid).removeprefix("member:")


def session(app, owner, cid, agent_id, anon, **task_fields):
    """The agent mid-run on a fresh task, speaking with its run token only."""
    s = start_run(app, owner, cid, agent_id, **task_fields)
    s.client = anon
    return s


def hr_agent(owner, cid) -> dict:
    return agent_of_family(owner, cid, "hr")


def specialist(owner, cid, name="Sam") -> dict:
    return new_agent(owner, cid, name=name, script={})


__all__ = ["api", "new_task", "world", "refs", "member_id", "session", "hr_agent", "specialist"]
