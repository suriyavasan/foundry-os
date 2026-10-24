"""Shared helpers for the Milestone 6 tests: a small team, a connected test provider and the
agent-API calls of the maker-checker flow."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import update

from app.guardrails.providers import FunctionExecutor, ProviderCall
from app.guardrails.registry import PROVIDER_ACTIONS
from app.models.agent import Agent
from tests.helpers import agent_of_family, api, new_agent
from tests.test_agent_api import AgentSession, start_run

SECRET = "smtp-secret-0123456789"


@dataclass
class Team:
    cid: str
    founder: dict
    manager: dict
    worker: dict


def make_team(client, cid: str) -> Team:
    founder = agent_of_family(client, cid, "founder")
    manager = new_agent(
        client,
        cid,
        name="Mia",
        kind="lead",
        role_title="Marketing Lead",
        manager_agent_id=founder["id"],
    )
    worker = new_agent(client, cid, name="Wes", role_title="Writer", manager_agent_id=manager["id"])
    return Team(cid, founder, manager, worker)


def set_family(app, agent_id: str, family: str) -> None:
    with app.state.session_factory() as db:
        db.execute(update(Agent).where(Agent.id == agent_id).values(role_family=family))
        db.commit()


@dataclass
class Mail:
    """A connected test provider: records every call it executes."""

    credential: dict
    calls: list[ProviderCall] = field(default_factory=list)

    @property
    def id(self) -> str:
        return self.credential["id"]


def connect_mail(
    app,
    client,
    cid: str,
    *,
    actions: tuple[str, ...] = ("email.*", "social.*"),
    families: tuple[str, ...] = (),
    provider: str = "email",
    register: bool = True,
) -> Mail:
    mail = Mail(credential={})

    def run(call: ProviderCall) -> dict[str, Any]:
        mail.calls.append(call)
        # a careless provider that echoes the secret back must not leak it to the agent
        return {"message_id": "m-1", "debug": f"auth={call.secret}", "action": call.action}

    if register:
        app.state.provider_executors.register(
            FunctionExecutor(provider, set(PROVIDER_ACTIONS), run)
        )
    r = client.post(
        api(cid, "/credentials"),
        json={
            "name": f"mail-{provider}",
            "value": SECRET,
            "kind": "token",
            "provider": provider,
            "allowed_actions": list(actions),
            "allowed_families": list(families),
        },
    )
    assert r.status_code == 201, r.text
    mail.credential = r.json()
    return mail


def send(
    session: AgentSession,
    mail: Mail,
    *,
    action: str = "email.send",
    to: str = "jane@customer.example",
    subject: str = "Hello",
    body: str = "Hi Jane, thanks for your interest.",
    **extra: Any,
):
    return session.post(
        "/provider-actions",
        json={
            "credential_id": mail.id,
            "action": action,
            "params": {"to": to, "subject": subject, "body": body, **extra},
        },
    )


def run_as(app, client, cid: str, agent: dict, title: str = "work") -> AgentSession:
    return start_run(app, client, cid, agent["id"], task_title=title)


def add_rule(client, cid: str, **body) -> dict:
    payload = {"scope_type": "all", "conditions": {}, **body}
    r = client.post(api(cid, "/guardrails/rules"), json=payload)
    assert r.status_code == 201, r.text
    return r.json()


def events(client, cid: str, **params) -> list[dict]:
    r = client.get(api(cid, "/guardrails/events"), params={"limit": 200, **params})
    assert r.status_code == 200, r.text
    return r.json()["items"]
