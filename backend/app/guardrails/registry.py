"""The action registry: every agent-API endpoint, runtime tool and control-plane provider action
with its risk tier (docs/specs/m6 section 1).

Fail closed: an action that is not registered here has no tier and is denied. A test enumerates
every ``/api/agent/v1`` route of the application and asserts it is registered, so adding an
endpoint without classifying it fails CI before it can ship.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.models.enums import ActionTier as T

TOOLS_FILE = Path(__file__).with_name("tools.json")
AGENT_API_PREFIXES = ("/api/agent/v1", "/agent/v1")
AGENT_API_PREFIX = AGENT_API_PREFIXES[0]

# Rate-limit keys (docs/specs/m6 section 2).
API_CALLS = "api_calls_per_min"
TASKS_CREATED = "tasks_created_per_hour"
EXTERNAL_COMMS = "external_communications_per_day"
HIRES = "hires_per_day"
SKILL_INSTALLS = "skill_installs_per_day"
WEB_FETCHES = "web_fetches_per_hour"
SYSTEM_EMAILS = "system_emails_per_day"  # M8: invitations / notification e-mails


@dataclass(frozen=True)
class ActionSpec:
    name: str
    tier: str
    description: str
    kind: str = "api"  # api | tool | provider | internal
    rate_keys: tuple[str, ...] = ()
    # Identity actions that touch domain / DNS ownership default to a human, not the Co-founder.
    human_default: bool = False
    # M8: a floor no rule can lower. The decision is at least ``human_approval`` (a human may still
    # write a ``deny`` rule). Used for real-money and domain-ownership actions.
    human_required: bool = False
    # M7: account management. No agent may ever do these and no rule can allow it: the engine
    # denies them before it looks at any rule.
    human_only: bool = False


@dataclass(frozen=True)
class RouteSpec:
    method: str
    path: str  # relative to /api/agent/v1, e.g. "/tasks/{task_id}"
    action: str  # action name, or "" when the action comes from the body (``dynamic``)
    dynamic: bool = False  # the body's ``action`` field names a registered provider action


def _a(name: str, tier: T, description: str, **kw: Any) -> ActionSpec:
    return ActionSpec(name, tier.value, description, **kw)


_API_ACTIONS = [
    _a("context.read", T.READ, "Read the task, colleagues and policy limits"),
    _a("state.read", T.READ, "Read the live company state"),
    _a("colleagues.search", T.READ, "Search colleagues"),
    _a("members.read", T.READ, "List the human colleagues (names, titles, capacity)"),
    _a("skills.read", T.READ, "List an agent's skills"),
    _a("role_templates.read", T.READ, "List role templates"),
    _a("approval.read", T.READ, "Read an approval"),
    _a("notes.read", T.READ, "Read the Founder's notes"),
    _a("guardrail.tool_check", T.READ, "Ask whether a runtime tool may run"),
    _a("guardrail.flag", T.READ, "Report a flagged tool result"),
    _a("task.create", T.INTERNAL_WRITE, "Create a task", rate_keys=(TASKS_CREATED,)),
    _a("task.update", T.INTERNAL_WRITE, "Block, unblock or reassign a task"),
    _a("task.complete", T.INTERNAL_WRITE, "Complete a task"),
    _a("task.review", T.INTERNAL_WRITE, "Accept or send back a delegated task"),
    _a("task.retry", T.INTERNAL_WRITE, "Retry a failed task"),
    _a("task.cancel", T.INTERNAL_WRITE, "Cancel a task"),
    _a("artifact.submit", T.INTERNAL_WRITE, "Submit an artifact"),
    _a("progress.report", T.INTERNAL_WRITE, "Post a progress update"),
    _a("decision.record", T.INTERNAL_WRITE, "Record a decision"),
    _a("approval.request", T.INTERNAL_WRITE, "Request an approval"),
    _a("approval.decide", T.INTERNAL_WRITE, "Decide an approval (Co-founder only)"),
    _a("peer_review.decide", T.INTERNAL_WRITE, "Decide a peer review assigned to you"),
    _a("escalation.create", T.INTERNAL_WRITE, "Escalate to the AI Co-founder"),
    _a("help.request", T.INTERNAL_WRITE, "Ask your lead for help"),
    _a("note.triage", T.INTERNAL_WRITE, "Triage a Founder's note"),
    _a("note.reply", T.INTERNAL_WRITE, "Reply to a Founder's note"),
    _a("memory.propose", T.INTERNAL_WRITE, "Propose a memory entry"),
    _a("memory.verify", T.INTERNAL_WRITE, "Verify a memory proposal"),
    _a("memory.reject", T.INTERNAL_WRITE, "Reject a memory proposal"),
    _a("hire.request", T.ORG_CHANGE, "Ask HR for a hire"),
    _a("hire.create", T.ORG_CHANGE, "Hire an employee", rate_keys=(HIRES,)),
    _a("skills.catalog", T.EXTERNAL_READ, "Search the skill catalog (incl. GitHub)"),
    _a("skills.install", T.ORG_CHANGE, "Install a skill", rate_keys=(SKILL_INSTALLS,)),
    _a("skills.author", T.ORG_CHANGE, "Author a skill"),
    _a("skills.assign", T.ORG_CHANGE, "Assign skills to an agent"),
    _a("skills.unassign", T.ORG_CHANGE, "Remove a skill from an agent"),
    _a("role_templates.create", T.ORG_CHANGE, "Create a role template"),
    _a("goal.create", T.ORG_CHANGE, "Create a goal"),
    _a("goal.update", T.ORG_CHANGE, "Update a goal"),
    _a("project.create", T.ORG_CHANGE, "Create a project"),
    _a("project.update", T.ORG_CHANGE, "Update a project"),
    _a("schedule.create", T.ORG_CHANGE, "Create a schedule"),
    _a("schedule.update", T.ORG_CHANGE, "Update a schedule"),
    # The route of every provider action; the real action (and tier) comes from the body.
    _a("provider.action", T.EXTERNAL_COMMUNICATION, "Run a provider action (see PROVIDER_ACTIONS)"),
]

# Actions performed by the control plane with a stored credential (framework in M6, real providers
# in M8). Agents never see the credential; the control plane makes the call.
_PROVIDER_ACTIONS = [
    _a("email.read", T.EXTERNAL_READ, "Read the company mailbox", kind="provider"),
    _a("github.read", T.EXTERNAL_READ, "Read a GitHub repository", kind="provider"),
    _a(
        "email.send",
        T.EXTERNAL_COMMUNICATION,
        "Send an email",
        kind="provider",
        rate_keys=(EXTERNAL_COMMS,),
    ),
    _a(
        "social.post",
        T.EXTERNAL_COMMUNICATION,
        "Post publicly",
        kind="provider",
        rate_keys=(EXTERNAL_COMMS,),
    ),
    _a(
        "github.pr.open",
        T.EXTERNAL_COMMUNICATION,
        "Open a pull request on an external repository",
        kind="provider",
        rate_keys=(EXTERNAL_COMMS,),
    ),
    _a(
        "github.issue.comment",
        T.EXTERNAL_COMMUNICATION,
        "Comment on an external issue",
        kind="provider",
        rate_keys=(EXTERNAL_COMMS,),
    ),
    _a(
        "invite.send",
        T.EXTERNAL_COMMUNICATION,
        "Invite a human",
        kind="provider",
        rate_keys=(EXTERNAL_COMMS,),
    ),
    _a("terms.accept", T.EXTERNAL_COMMITMENT, "Accept terms of service", kind="provider"),
    _a("account.register", T.EXTERNAL_COMMITMENT, "Register an account", kind="provider"),
    _a(
        "domain.register",
        T.EXTERNAL_COMMITMENT,
        "Register a domain",
        kind="provider",
        human_default=True,
        human_required=True,
    ),
    _a("purchase.make", T.SPEND, "Make a purchase", kind="provider"),
    _a("subscription.start", T.SPEND, "Start a subscription", kind="provider"),
    _a("mailbox.create", T.IDENTITY, "Create a mailbox", kind="provider"),
    _a("dns.change", T.IDENTITY, "Change DNS records", kind="provider", human_default=True),
    _a("credential.rotate", T.IDENTITY, "Rotate a credential", kind="provider"),
    _a("resource.delete", T.DESTRUCTIVE, "Delete an external resource", kind="provider"),
    # --- Milestone 8: reachability & identity (executors in app/integrations) ---
    _a("dns.read", T.EXTERNAL_READ, "List the DNS records of a zone", kind="provider"),
    _a("domain.search", T.EXTERNAL_READ, "Search domain availability and price", kind="provider"),
    _a(
        "domain.preview",
        T.EXTERNAL_READ,
        "Prepare the exact registration file and pull request of a free-subdomain registry",
        kind="provider",
    ),
    _a(
        "domain.register_pr",
        T.EXTERNAL_COMMITMENT,
        "Open the pull request that registers a free subdomain (published under the Founder's "
        "GitHub identity)",
        kind="provider",
        human_default=True,
        human_required=True,
    ),
    _a(
        "domain.purchase",
        T.SPEND,
        "Buy a domain from a registrar (real money, bound to a price ceiling)",
        kind="provider",
        human_default=True,
        human_required=True,
    ),
    _a("hosted_ui.deploy", T.IDENTITY, "Deploy the control UI to Vercel", kind="provider"),
    _a(
        "hosted_ui.status",
        T.EXTERNAL_READ,
        "Read the hosted control UI deployment",
        kind="provider",
    ),
    _a("tunnel.status", T.EXTERNAL_READ, "Read the public tunnel status", kind="provider"),
    _a("tunnel.start", T.IDENTITY, "Start the public tunnel", kind="provider"),
    _a("tunnel.stop", T.IDENTITY, "Stop the public tunnel", kind="provider"),
]

# M7 accounts & team. Tier `identity`, but unlike the provider actions above they cannot be
# approved or allowed by any rule: users, invitations, memberships, tokens and sessions are created
# and changed by humans signed in to the control plane, never by an agent (run tokens included).
_HUMAN_ONLY_ACTIONS = [
    _a("account.manage", T.IDENTITY, "Manage an account", kind="human_only", human_only=True),
    _a("user.create", T.IDENTITY, "Create a user account", kind="human_only", human_only=True),
    _a("session.create", T.IDENTITY, "Open a login session", kind="human_only", human_only=True),
    _a("session.revoke", T.IDENTITY, "Revoke sessions", kind="human_only", human_only=True),
    _a("token.create", T.IDENTITY, "Create an API token", kind="human_only", human_only=True),
    _a("token.revoke", T.IDENTITY, "Revoke an API token", kind="human_only", human_only=True),
    _a("password.change", T.IDENTITY, "Change a password", kind="human_only", human_only=True),
    _a("twofa.manage", T.IDENTITY, "Manage two-factor login", kind="human_only", human_only=True),
    _a("invitation.create", T.IDENTITY, "Invite a person", kind="human_only", human_only=True),
    _a("invitation.revoke", T.IDENTITY, "Revoke an invitation", kind="human_only", human_only=True),
    _a("invitation.accept", T.IDENTITY, "Accept an invitation", kind="human_only", human_only=True),
    _a("member.update", T.IDENTITY, "Change a member", kind="human_only", human_only=True),
    _a("member.remove", T.IDENTITY, "Remove a member", kind="human_only", human_only=True),
    # M8: third-party accounts and how the device is reached are the Founder's, never an agent's
    # (agents use the guarded provider actions instead).
    _a(
        "connected_account.manage",
        T.IDENTITY,
        "Connect, test or revoke third-party accounts",
        kind="human_only",
        human_only=True,
    ),
    _a(
        "reachability.manage",
        T.IDENTITY,
        "Change how this device is reached (network bind, tunnel, binaries)",
        kind="human_only",
        human_only=True,
    ),
]
HUMAN_ONLY: dict[str, ActionSpec] = {s.name: s for s in _HUMAN_ONLY_ACTIONS}

# Actions the platform itself takes on an agent's behalf (not reachable from the agent API).
_INTERNAL_ACTIONS = [
    _a(
        "model.paid_fallback",
        T.SPEND,
        "Run on a paid model because no free model is available",
        kind="internal",
    ),
]

_ROUTES = [
    RouteSpec("GET", "/context", "context.read"),
    RouteSpec("GET", "/state", "state.read"),
    RouteSpec("POST", "/tasks", "task.create"),
    RouteSpec("PATCH", "/tasks/{task_id}", "task.update"),
    RouteSpec("POST", "/tasks/{task_id}/complete", "task.complete"),
    RouteSpec("POST", "/tasks/{task_id}/review", "task.review"),
    RouteSpec("POST", "/tasks/{task_id}/retry", "task.retry"),
    RouteSpec("POST", "/tasks/{task_id}/cancel", "task.cancel"),
    RouteSpec("POST", "/artifacts", "artifact.submit"),
    RouteSpec("POST", "/progress", "progress.report"),
    RouteSpec("POST", "/decisions", "decision.record"),
    RouteSpec("POST", "/approvals", "approval.request"),
    RouteSpec("GET", "/approvals/{approval_id}", "approval.read"),
    RouteSpec("POST", "/approvals/{approval_id}/decide", "approval.decide"),
    RouteSpec("GET", "/agents", "colleagues.search"),
    RouteSpec("GET", "/members", "members.read"),
    RouteSpec("GET", "/agents/{agent_id}/skills", "skills.read"),
    RouteSpec("POST", "/agents/{agent_id}/skills", "skills.assign"),
    RouteSpec("DELETE", "/agents/{agent_id}/skills/{skill_ref}", "skills.unassign"),
    RouteSpec("GET", "/skills/catalog", "skills.catalog"),
    RouteSpec("POST", "/skills/install", "skills.install"),
    RouteSpec("POST", "/skills/author", "skills.author"),
    RouteSpec("GET", "/role-templates", "role_templates.read"),
    RouteSpec("POST", "/role-templates", "role_templates.create"),
    RouteSpec("POST", "/hires", "hire.create"),
    RouteSpec("POST", "/hiring-requests", "hire.request"),
    RouteSpec("POST", "/escalations", "escalation.create"),
    RouteSpec("POST", "/help-requests", "help.request"),
    RouteSpec("POST", "/goals", "goal.create"),
    RouteSpec("PATCH", "/goals/{goal_id}", "goal.update"),
    RouteSpec("POST", "/projects", "project.create"),
    RouteSpec("PATCH", "/projects/{project_id}", "project.update"),
    RouteSpec("POST", "/schedules", "schedule.create"),
    RouteSpec("PATCH", "/schedules/{schedule_id}", "schedule.update"),
    RouteSpec("GET", "/notes", "notes.read"),
    RouteSpec("POST", "/notes/{note_id}/triage", "note.triage"),
    RouteSpec("POST", "/notes/{note_id}/replies", "note.reply"),
    RouteSpec("POST", "/memory", "memory.propose"),
    RouteSpec("POST", "/memory/{entry_id}/verify", "memory.verify"),
    RouteSpec("POST", "/memory/{entry_id}/reject", "memory.reject"),
    # Milestone 6
    RouteSpec("POST", "/guardrails/check-tool", "guardrail.tool_check"),
    RouteSpec("POST", "/guardrails/flags", "guardrail.flag"),
    RouteSpec("POST", "/peer-reviews/{approval_id}/decide", "peer_review.decide"),
    RouteSpec("POST", "/provider-actions", "", dynamic=True),
]


@lru_cache(maxsize=1)
def tool_registry() -> dict[str, Any]:
    return json.loads(TOOLS_FILE.read_text(encoding="utf-8"))


def _tool_actions() -> list[ActionSpec]:
    reg = tool_registry()
    return [
        ActionSpec(
            f"tool.{name}",
            info["tier"],
            f"Runtime tool {name}",
            kind="tool",
            rate_keys=(WEB_FETCHES,) if name in ("web_search", "web_extract") else (),
        )
        for name, info in reg["tools"].items()
    ]


ACTIONS: dict[str, ActionSpec] = {
    spec.name: spec
    for spec in (
        *_API_ACTIONS,
        *_PROVIDER_ACTIONS,
        *_INTERNAL_ACTIONS,
        *_HUMAN_ONLY_ACTIONS,
        *_tool_actions(),
    )
}
PROVIDER_ACTIONS: dict[str, ActionSpec] = {s.name: s for s in _PROVIDER_ACTIONS}
ROUTES: dict[tuple[str, str], RouteSpec] = {(r.method, r.path): r for r in _ROUTES}

TIERS: tuple[str, ...] = tuple(t.value for t in T)


def get_action(name: str) -> ActionSpec | None:
    return ACTIONS.get(name)


def route_spec(method: str, path: str) -> RouteSpec | None:
    """``path`` is the route template relative to ``/api/agent/v1``."""
    return ROUTES.get((method.upper(), path))


def relative_route(template: str) -> str:
    for prefix in AGENT_API_PREFIXES:
        if template.startswith(prefix):
            return template[len(prefix) :]
    return template


def tool_action(tool_name: str) -> str:
    return f"tool.{tool_name}"


def company_tool_action(tool_name: str) -> str | None:
    info = tool_registry()["company_tools"].get(tool_name)
    return info["action"] if info else None


def tier_of_tool(tool_name: str) -> str | None:
    """Tier of a runtime tool (Hermes-native or company), None when unregistered."""
    reg = tool_registry()
    if tool_name in reg["tools"]:
        return reg["tools"][tool_name]["tier"]
    action = company_tool_action(tool_name)
    spec = ACTIONS.get(action) if action else None
    return spec.tier if spec else None


def hermes_tools_for(toolsets: list[str]) -> list[str]:
    """Hermes-native tools a run with these (control-plane) toolsets can call."""
    reg = tool_registry()
    names: list[str] = []
    for ts in toolsets:
        names.extend(reg["toolsets"].get(ts, []))
    names.extend(reg["always"])
    return list(dict.fromkeys(names))


def untrusted_sources() -> dict[str, str]:
    """tool name -> source label for tools whose results are untrusted data."""
    reg = tool_registry()
    out = {n: i["untrusted"] for n, i in reg["tools"].items() if i.get("untrusted")}
    out.update({n: i["untrusted"] for n, i in reg["company_tools"].items() if i.get("untrusted")})
    return out


def runtime_spec(toolsets: list[str], company_tools: list[str]) -> dict[str, Any]:
    """The ``guardrails`` block of a run spec for the tools this run can reach."""
    tiers: dict[str, str] = {}
    for name in hermes_tools_for(toolsets):
        tier = tier_of_tool(name)
        if tier is not None:
            tiers[name] = tier
    for name in company_tools:
        tier = tier_of_tool(name)
        if tier is not None:
            tiers[name] = tier
    wanted = untrusted_sources()
    return {
        "tool_tiers": tiers,
        "server_checked_tiers": [
            t.value for t in T if t not in (T.READ, T.INTERNAL_WRITE, T.ORG_CHANGE)
        ],
        "untrusted_tools": {n: s for n, s in wanted.items() if n in tiers},
        "flag_injection": True,
    }
