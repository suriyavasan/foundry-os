"""Company toolset (COMPANY FORK, not in upstream Hermes).

Tools that let an AI employee act inside Company OS by calling the control plane's agent API
(``docs/specs/m2-work-engine.md`` section 3, ``docs/specs/m4-skills-and-hr.md`` section 3) with its
scoped run token.

Work (every employee):
    company_get_context, company_create_task, company_update_task, company_complete_task,
    company_review_task, company_submit_artifact, company_report_progress,
    company_record_decision, company_request_approval, company_decide_approval
Colleagues and hiring (M4):
    company_find_colleagues, company_request_hire
    HR-only on the server (a non-HR token gets 403, whatever the model tries):
    company_search_skills, company_install_skill, company_author_skill, company_assign_skills,
    company_list_role_templates, company_create_role_template, company_hire_agent

Which of these a given employee sees is decided by the control plane (spec ``company_tools``
subset, derived from the role template); the server remains the authority on every call.

The connection (URL + token) is installed by the launcher via
``company_agent.context.configure``; the tools are hidden (``check_fn``) until then. Every
handler returns a JSON string; HTTP failures become ``{"error": <code>, "message": ...}`` so
the model can read and react to them. The server enforces authorization (an agent can never
decide its own approvals, change policy, etc.) - nothing here is a security boundary.
"""

from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import re
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Optional

import httpx

from company_agent import context as ctx
from tools.registry import no_cache_check_fn, registry

MAX_ARTIFACT_BYTES = 25 * 1024 * 1024  # matches the backend's default max upload
MAX_TOOL_RESULT_CHARS = 30_000

ARTIFACT_KINDS = ["document", "code", "design", "report", "data", "link", "other"]
TASK_KINDS = ["work", "review", "planning", "hiring", "routine"]
PRIORITIES = ["low", "normal", "high", "urgent"]
SKILL_SOURCES = ["all", "library", "hermes", "github"]
APPROVAL_KINDS = [
    "external_purchase", "budget_increase", "external_communication",
    "production_deploy", "sensitive_action", "hire", "other",
]


# --------------------------------------------------------------------------- helpers


def _scrub(text: str) -> str:
    for secret in ctx.secrets():
        if secret and secret in text:
            text = text.replace(secret, "[REDACTED]")
    return text


def _out(payload: Any) -> str:
    return _scrub(json.dumps(payload, ensure_ascii=False, default=str))


def _err(code: str, message: str, **extra: Any) -> str:
    return _out({"error": code, "message": message, **extra})


def _usd_to_micros(value: Any, field_name: str) -> int:
    try:
        dec = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError(f"{field_name} must be a number of US dollars") from None
    if dec < 0:
        raise ValueError(f"{field_name} must not be negative")
    return int((dec * 1_000_000).to_integral_value())


def _response_to_result(resp: httpx.Response) -> str:
    try:
        body = resp.json()
    except ValueError:
        body = None
    if resp.is_success:
        return _out(body if body is not None else {"ok": True, "status": resp.status_code})
    envelope = body.get("error") if isinstance(body, dict) else None
    if isinstance(envelope, dict):
        extra: dict[str, Any] = {"status": resp.status_code}
        if envelope.get("details"):
            extra["details"] = envelope["details"]
        return _err(str(envelope.get("code") or f"http_{resp.status_code}"),
                    str(envelope.get("message") or resp.reason_phrase), **extra)
    return _err(f"http_{resp.status_code}", (resp.text or resp.reason_phrase)[:500], status=resp.status_code)


def _call(method: str, path: str, **kwargs: Any) -> str:
    """One agent-API request -> JSON string (never raises)."""
    try:
        resp = ctx.client().request(method, ctx.api_url(path), **kwargs)
    except RuntimeError as exc:  # not configured
        return _err("not_configured", str(exc))
    except httpx.TimeoutException:
        return _err("agent_api_timeout", "The Company OS API did not answer in time.")
    except httpx.HTTPError as exc:
        return _err("agent_api_unreachable", f"Could not reach the Company OS API: {type(exc).__name__}")
    return _response_to_result(resp)


def _json_of(result: str) -> Any:
    try:
        return json.loads(result)
    except ValueError:
        return None


def _is_error(parsed: Any) -> bool:
    """True for a tool-level error body (``{"error": "<code>", "message": ...}``).

    NOT ``"error" in parsed``: successful API bodies carry their own ``error`` field (a Task has
    ``error: null``, or the last failed attempt's ``{code, message, run_id}`` object). Found by the
    first live run against the real backend, where every successful complete_task looked like a
    failure and the agent never stopped."""
    return isinstance(parsed, dict) and isinstance(parsed.get("error"), str)


def _clean(d: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in d.items() if v is not None}


def _task_id(args: dict[str, Any], key: str = "task_id") -> str:
    return str(args.get(key) or ctx.config().task_id or "").strip()


def _str_list(value: Any) -> Optional[list[str]]:
    if value is None:
        return None
    if isinstance(value, str) and value.strip()[:1] in "[{":
        # Hermes' argument coercion stringifies a list/dict the model sent for an array parameter.
        import ast

        text = value.strip()
        for parse in (json.loads, ast.literal_eval):
            try:
                value = parse(text)
                break
            except (ValueError, SyntaxError):
                continue
    if isinstance(value, dict):  # {"art-1": "title"} -> ids
        value = list(value.keys())
    if isinstance(value, str):
        value = [v for v in re.split(r"[,\n]", value)]
    return [str(v).strip() for v in value if str(v).strip()]


def _need(args: dict[str, Any], *names: str) -> Optional[str]:
    missing = [n for n in names if not str(args.get(n) or "").strip()]
    return _err("invalid_arguments", f"Missing required argument(s): {', '.join(missing)}") if missing else None


# --------------------------------------------------------------------------- handlers


def company_get_context(args: dict[str, Any], **_: Any) -> str:
    return _call("GET", "/context")


def company_create_task(args: dict[str, Any], **_: Any) -> str:
    if (bad := _need(args, "title")):
        return bad
    title = str(args["title"]).strip()
    normalized = re.sub(r"\s+", " ", title.lower())
    # Stable per (run, title): a retried/duplicated call returns the existing task, not a second one.
    idem = f"{ctx.config().run_id}:{hashlib.sha256(normalized.encode()).hexdigest()[:24]}"
    try:
        budget = _usd_to_micros(args["budget_usd"], "budget_usd") if args.get("budget_usd") is not None else None
    except ValueError as exc:
        return _err("invalid_arguments", str(exc))
    body = _clean({
        "title": title,
        "description": args.get("description"),
        "acceptance_criteria": args.get("acceptance_criteria"),
        "kind": args.get("kind"),
        "priority": args.get("priority"),
        "assignee_agent_id": args.get("assignee_agent_id"),
        "reviewer_agent_id": args.get("reviewer_agent_id"),
        "parent_task_id": args.get("parent_task_id"),
        "depends_on": _str_list(args.get("depends_on")),
        "required_skills": _str_list(args.get("required_skills")),
        "budget_micros": budget,
        "idempotency_key": idem,
    })
    return _call("POST", "/tasks", json=body)


def company_update_task(args: dict[str, Any], **_: Any) -> str:
    task_id = _task_id(args)
    action = str(args.get("action") or "").strip().lower()
    if not task_id:
        return _err("invalid_arguments", "task_id is required")
    body: dict[str, Any]
    if action == "block":
        if not str(args.get("reason") or "").strip():
            return _err("invalid_arguments", "A reason is required when blocking a task.")
        body = {"status": "blocked", "reason": str(args["reason"])}
    elif action == "unblock":
        body = _clean({"status": "unblocked", "reason": args.get("reason")})
    elif action == "reassign":
        if not args.get("assignee_agent_id"):
            return _err("invalid_arguments", "assignee_agent_id is required to reassign a task.")
        body = {"assignee_agent_id": args["assignee_agent_id"]}
    else:
        return _err("invalid_arguments", "action must be one of: block, unblock, reassign")
    result = _call("PATCH", f"/tasks/{task_id}", json=body)
    parsed = _json_of(result)
    if action == "block" and isinstance(parsed, dict) and not _is_error(parsed) and task_id == ctx.config().task_id:
        ctx.mark_task_blocked(task_id)
    return result


def company_complete_task(args: dict[str, Any], **_: Any) -> str:
    task_id = _task_id(args)
    if not task_id:
        return _err("invalid_arguments", "task_id is required")
    if (bad := _need(args, "result_summary")):
        return bad
    refs: list[str] = []
    for key in ("output_artifact_ids", "artifact_ids", "output_refs", "artifacts"):  # models vary the name
        refs = _str_list(args.get(key)) or []
        if refs:
            break
    refs = [r for r in refs if r]
    # What this run actually submitted is the truth; fill it in when the model forgot or mis-named it.
    if not refs and task_id == ctx.config().task_id:
        refs = ctx.submitted_artifact_ids()
    body = {"result_summary": str(args["result_summary"]).strip(), "output_refs": refs}
    # Client-side friction, once: small models like to "finish" research tasks without saving the
    # deliverable. The server stays the authority; a second call goes through unchanged.
    if not body["output_refs"] and task_id == ctx.config().task_id and ctx.should_warn_no_deliverable():
        return _err(
            "no_deliverable",
            "You have not submitted any artifact in this run, so there is nothing to hand over. Call "
            "company_submit_artifact now with a title and the full text in content, then call "
            "company_complete_task again. If this task truly has no deliverable, call "
            "company_complete_task again and say why in result_summary.")
    result = _call("POST", f"/tasks/{task_id}/complete", json=body)
    parsed = _json_of(result)
    if isinstance(parsed, dict) and not _is_error(parsed):
        ctx.mark_task_completed(task_id)
    return result


def company_review_task(args: dict[str, Any], **_: Any) -> str:
    if (bad := _need(args, "task_id", "decision")):
        return bad
    decision = str(args["decision"]).strip()
    if decision not in ("accept", "request_revision"):
        return _err("invalid_arguments", "decision must be 'accept' or 'request_revision'")
    return _call("POST", f"/tasks/{str(args['task_id']).strip()}/review",
                 json=_clean({"decision": decision, "note": args.get("note")}))


def _workspace_file(path: str) -> tuple[Optional[str], Optional[str]]:
    """(real path, error). The file must be a regular file inside the run workspace."""
    workspace = ctx.config().workspace_dir
    if not workspace:
        return None, "File uploads are unavailable: no workspace is configured. Use inline content."
    root = os.path.realpath(workspace)
    candidate = path if os.path.isabs(path) else os.path.join(root, path)
    real = os.path.realpath(os.path.expanduser(candidate))
    if real != root and not real.startswith(root + os.sep):
        return None, f"path must be inside your workspace ({root})."
    if not os.path.isfile(real):
        return None, f"File not found in workspace: {path}"
    if os.path.getsize(real) > MAX_ARTIFACT_BYTES:
        return None, f"File is larger than the {MAX_ARTIFACT_BYTES // (1024 * 1024)} MB artifact limit."
    return real, None


def company_submit_artifact(args: dict[str, Any], **_: Any) -> str:
    path = str(args.get("path") or "").strip()
    content = args.get("content")
    has_content = content is not None and str(content) != ""
    if path and has_content and _workspace_file(path)[0] is None:
        path = ""  # models often send both and invent the path: inline content wins when the file is not real
    if bool(path) == has_content:
        return _err("invalid_arguments", "Provide exactly one of: path (a file in your workspace) or content (inline text).")
    kind = str(args.get("kind") or "document").strip()
    if kind not in ARTIFACT_KINDS:
        return _err("invalid_arguments", f"kind must be one of: {', '.join(ARTIFACT_KINDS)}")
    title = str(args.get("title") or "").strip() or None
    if title is None and not args.get("artifact_id"):
        return _err("invalid_arguments", "title is required for a new artifact.")
    task_id = args.get("task_id") or ctx.config().task_id or None
    fields = _clean({
        "title": title, "kind": kind, "task_id": task_id, "artifact_id": args.get("artifact_id"),
        "note": args.get("note"), "mime_type": args.get("mime_type"),
    })
    if path:
        real, problem = _workspace_file(path)
        if problem:
            return _err("invalid_arguments", problem)
        mime = fields.get("mime_type") or mimetypes.guess_type(real)[0] or "application/octet-stream"
        fields.setdefault("title", os.path.basename(real))
        try:
            with open(real, "rb") as fh:
                result = _call("POST", "/artifacts", data={k: str(v) for k, v in fields.items()},
                               files={"file": (os.path.basename(real), fh, mime)})
        except OSError as exc:
            return _err("invalid_arguments", f"Could not read {path}: {exc.strerror or exc}")
    else:
        result = _call("POST", "/artifacts", json={**fields, "content": str(content)})
    parsed = _json_of(result)
    if isinstance(parsed, dict) and parsed.get("id") and not _is_error(parsed):
        ctx.note_artifact_submitted(str(parsed["id"]))
        cb = ctx.config().on_artifact
        if cb is not None:
            try:
                cb(str(parsed["id"]), str(parsed.get("title") or title or ""))
            except Exception:  # an event-writer problem must not fail the tool call
                pass
    return result


def company_report_progress(args: dict[str, Any], **_: Any) -> str:
    if (bad := _need(args, "message")):
        return bad
    return _call("POST", "/progress", json={"message": str(args["message"]).strip()[:2000]})


def company_record_decision(args: dict[str, Any], **_: Any) -> str:
    if (bad := _need(args, "title")):
        return bad
    context_obj = args.get("context")
    body = _clean({
        "title": str(args["title"]).strip(),
        "rationale": args.get("rationale"),
        "related_task_id": args.get("related_task_id") or ctx.config().task_id or None,
        "related_project_id": args.get("related_project_id"),
        "context": context_obj if isinstance(context_obj, dict) else None,
    })
    return _call("POST", "/decisions", json=body)


def company_request_approval(args: dict[str, Any], **_: Any) -> str:
    if (bad := _need(args, "kind", "title")):
        return bad
    kind = str(args["kind"]).strip()
    if kind not in APPROVAL_KINDS:
        return _err("invalid_arguments", f"kind must be one of: {', '.join(APPROVAL_KINDS)}")
    try:
        amount = _usd_to_micros(args["amount_usd"], "amount_usd") if args.get("amount_usd") is not None else None
    except ValueError as exc:
        return _err("invalid_arguments", str(exc))
    body = _clean({
        "kind": kind,
        "title": str(args["title"]).strip(),
        "reason": args.get("reason"),
        "amount_micros": amount,
        "vendor": args.get("vendor"),
        "recurrence": args.get("recurrence"),
        "risk": args.get("risk"),
        "scope": args.get("scope") if isinstance(args.get("scope"), dict) else None,
        "evidence": args.get("evidence") if isinstance(args.get("evidence"), dict) else None,
        "task_id": args.get("task_id") or ctx.config().task_id or None,
    })
    return _call("POST", "/approvals", json=body)


def company_decide_approval(args: dict[str, Any], **_: Any) -> str:
    if (bad := _need(args, "approval_id", "decision")):
        return bad
    decision = str(args["decision"]).strip()
    if decision not in ("approve", "reject", "request_changes"):
        return _err("invalid_arguments", "decision must be 'approve', 'reject' or 'request_changes'")
    return _call("POST", f"/approvals/{str(args['approval_id']).strip()}/decide",
                 json=_clean({"decision": decision, "note": args.get("note")}))


# ------------------------------------------------------------------ M4: colleagues + hiring


def _trim(result: str, keep: tuple[str, ...], list_key: str = "items", clip: dict[str, int] | None = None) -> str:
    """Shrink a list response to the fields a small model needs (long fields blow its context)."""
    parsed = _json_of(result)
    if not isinstance(parsed, dict) or _is_error(parsed) or not isinstance(parsed.get(list_key), list):
        return result
    rows = []
    for row in parsed[list_key]:
        if not isinstance(row, dict):
            continue
        item = {k: row[k] for k in keep if k in row}
        for k, limit in (clip or {}).items():
            if isinstance(item.get(k), str) and len(item[k]) > limit:
                item[k] = item[k][:limit] + "..."
        rows.append(item)
    out = {list_key: rows}
    if parsed.get("next_cursor"):
        out["next_cursor"] = parsed["next_cursor"]
    return _out(out)


def company_find_colleagues(args: dict[str, Any], **_: Any) -> str:
    tags = _str_list(args.get("skill_tags")) or []
    params = _clean({
        "skill_tags": ",".join(t.lower() for t in tags) or None,
        "family": args.get("family"),
        "status": args.get("status"),
    })
    return _call("GET", "/agents", params=params)


def company_request_hire(args: dict[str, Any], **_: Any) -> str:
    skills = _str_list(args.get("required_skills")) or []
    if not skills:
        return _err("invalid_arguments", "required_skills must list at least one skill or capability tag.")
    if (bad := _need(args, "reason")):
        return bad
    task_id = _task_id(args)
    if not task_id:
        return _err("invalid_arguments", "task_id is required")
    return _call("POST", "/hiring-requests", json=_clean({
        "required_skills": skills,
        "role_hint": args.get("role_hint"),
        "reason": str(args["reason"]).strip(),
        "task_id": task_id,
    }))


def company_search_skills(args: dict[str, Any], **_: Any) -> str:
    source = str(args.get("source") or "").strip() or None
    if source is not None and source not in SKILL_SOURCES:
        return _err("invalid_arguments", f"source must be one of: {', '.join(SKILL_SOURCES)}")
    limit = args.get("limit")
    try:
        limit = max(1, min(int(limit), 50)) if limit is not None else 20
    except (TypeError, ValueError):
        return _err("invalid_arguments", "limit must be a number")
    result = _call("GET", "/skills/catalog", params=_clean({"q": args.get("query"), "source": source, "limit": limit}))
    return _trim(result, ("slug", "name", "description", "tags", "license", "source", "source_ref",
                          "item_type", "installed", "status"), clip={"description": 240})


def company_install_skill(args: dict[str, Any], **_: Any) -> str:
    if (bad := _need(args, "source_ref")):
        return bad
    return _call("POST", "/skills/install", json=_clean({
        "source_ref": str(args["source_ref"]).strip(), "slug": args.get("slug")}))


def company_author_skill(args: dict[str, Any], **_: Any) -> str:
    if (bad := _need(args, "slug", "name", "description", "body_markdown")):
        return bad
    files = args.get("files")
    if isinstance(files, str):
        files = _json_of(files)
    body = _clean({
        "slug": str(args["slug"]).strip(),
        "name": str(args["name"]).strip(),
        "description": str(args["description"]).strip(),
        "tags": _str_list(args.get("tags")) or [],
        "body_markdown": str(args["body_markdown"]),
        "files": files if isinstance(files, list) else None,
    })
    return _call("POST", "/skills/author", json=body)


def company_assign_skills(args: dict[str, Any], **_: Any) -> str:
    if (bad := _need(args, "agent_id")):
        return bad
    slugs = _str_list(args.get("skill_slugs")) or []
    if not slugs:
        return _err("invalid_arguments", "skill_slugs must list at least one skill slug.")
    return _call("POST", f"/agents/{str(args['agent_id']).strip()}/skills", json={"slugs": slugs})


def company_list_role_templates(args: dict[str, Any], **_: Any) -> str:
    result = _call("GET", "/role-templates")
    return _trim(result, ("slug", "family", "title", "description", "default_kind", "default_department",
                          "default_toolsets", "default_skill_slugs", "required_skill_tags", "builtin"),
                 clip={"description": 240})


def company_create_role_template(args: dict[str, Any], **_: Any) -> str:
    if (bad := _need(args, "slug", "title")):
        return bad
    body = _clean({
        "slug": str(args["slug"]).strip(),
        "title": str(args["title"]).strip(),
        "family": args.get("family"),
        "description": args.get("description"),
        "default_kind": args.get("default_kind"),
        "default_department": args.get("default_department"),
        "instructions": args.get("instructions"),
        "default_toolsets": _str_list(args.get("default_toolsets")),
        "default_skill_slugs": _str_list(args.get("default_skill_slugs")),
        "required_skill_tags": _str_list(args.get("required_skill_tags")),
    })
    return _call("POST", "/role-templates", json=body)


def company_hire_agent(args: dict[str, Any], **_: Any) -> str:
    if (bad := _need(args, "role_template", "name", "manager_agent_id")):
        return bad
    task_id = _task_id(args)
    if not task_id:
        return _err("invalid_arguments", "task_id (the hiring task) is required")
    try:
        budget = (_usd_to_micros(args["monthly_budget_usd"], "monthly_budget_usd")
                  if args.get("monthly_budget_usd") is not None else None)
    except ValueError as exc:
        return _err("invalid_arguments", str(exc))
    body = _clean({
        "role_template": str(args["role_template"]).strip(),
        "name": str(args["name"]).strip(),
        "role_title": args.get("role_title"),
        "manager_agent_id": str(args["manager_agent_id"]).strip(),
        "department": args.get("department"),
        "skill_slugs": _str_list(args.get("skill_slugs")) or [],
        "model_provider": args.get("model_provider"),
        "model_name": args.get("model_name"),
        "monthly_budget_micros": budget,
        "reason": str(args.get("reason") or "").strip() or f"Hire requested by task {task_id}",
        "task_id": task_id,
    })
    return _call("POST", "/hires", json=body)


# --------------------------------------------------------------------------- schemas
# Kept deliberately terse: small local models degrade with long tool descriptions.


def _schema(name: str, description: str, properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {"name": name, "description": description,
            "parameters": {"type": "object", "properties": properties, "required": required}}


_S = {"type": "string"}


def _str(desc: str) -> dict[str, Any]:
    return {"type": "string", "description": desc}


_TOOLS: dict[str, tuple[dict[str, Any], Callable[..., str], str]] = {
    "company_get_context": (
        _schema("company_get_context",
                "Read your company, your task, your manager/reports and policy limits from Company OS.",
                {}, []),
        company_get_context, "🏢"),
    "company_create_task": (
        _schema("company_create_task",
                "Create a task or subtask (delegate work). Assign it to a colleague by agent id.",
                {"title": _str("Short task title"),
                 "description": _str("What needs to be done"),
                 "acceptance_criteria": _str("How the result will be judged"),
                 "assignee_agent_id": _str("Agent id of the colleague who should do it"),
                 "reviewer_agent_id": _str("Agent id of the reviewer (optional)"),
                 "priority": {"type": "string", "enum": PRIORITIES},
                 "kind": {"type": "string", "enum": TASK_KINDS},
                 "depends_on": {"type": "array", "items": _S, "description": "Task ids that must finish first"},
                 "required_skills": {"type": "array", "items": _S},
                 "budget_usd": {"type": "number", "description": "Spending cap for the task in US dollars"}},
                ["title"]),
        company_create_task, "➕"),
    "company_update_task": (
        _schema("company_update_task",
                "Block or unblock a task (with a reason), or reassign one you manage. Defaults to your task.",
                {"action": {"type": "string", "enum": ["block", "unblock", "reassign"]},
                 "task_id": _str("Task id (default: your current task)"),
                 "reason": _str("Why (required for block)"),
                 "assignee_agent_id": _str("New assignee (for reassign)")},
                ["action"]),
        company_update_task, "✏️"),
    "company_complete_task": (
        _schema("company_complete_task",
                "Mark your task finished. Call this once, after the deliverable is submitted.",
                {"result_summary": _str("What was done and where the result is"),
                 "output_artifact_ids": {"type": "array", "items": _S,
                                         "description": "Artifact ids produced for this task"},
                 "task_id": _str("Task id (default: your current task)")},
                ["result_summary"]),
        company_complete_task, "✅"),
    "company_review_task": (
        _schema("company_review_task",
                "Review a delegated task assigned to you for review: accept it or request a revision.",
                {"task_id": _str("Task id under review"),
                 "decision": {"type": "string", "enum": ["accept", "request_revision"]},
                 "note": _str("Feedback for the assignee")},
                ["task_id", "decision"]),
        company_review_task, "🔍"),
    "company_submit_artifact": (
        _schema("company_submit_artifact",
                "Submit a deliverable. Give inline text in 'content', or 'path' of a file in your workspace.",
                {"title": _str("Artifact title"),
                 "content": _str("Inline text content (markdown/plain)"),
                 "path": _str("File path inside your workspace (alternative to content)"),
                 "kind": {"type": "string", "enum": ARTIFACT_KINDS},
                 "artifact_id": _str("Existing artifact id to add a new version"),
                 "note": _str("Short note about this version"),
                 "task_id": _str("Task id (default: your current task)")},
                ["title"]),
        company_submit_artifact, "📎"),
    "company_report_progress": (
        _schema("company_report_progress",
                "Post a one-line progress update visible to the company.",
                {"message": _str("Progress update")}, ["message"]),
        company_report_progress, "📣"),
    "company_record_decision": (
        _schema("company_record_decision",
                "Record a decision you made and why, for the company decision log.",
                {"title": _str("The decision"),
                 "rationale": _str("Why you decided this"),
                 "related_task_id": _str("Related task id (default: your task)")},
                ["title"]),
        company_record_decision, "🧭"),
    "company_request_approval": (
        _schema("company_request_approval",
                "Request approval for a risky or costly action (purchase, hire, deploy...). Returns an approval id.",
                {"kind": {"type": "string", "enum": APPROVAL_KINDS},
                 "title": _str("What you want approved"),
                 "reason": _str("Why it is needed"),
                 "amount_usd": {"type": "number", "description": "Cost in US dollars, if any"},
                 "vendor": _str("Vendor or counterparty"),
                 "recurrence": {"type": "string", "enum": ["one_time", "monthly", "yearly"]},
                 "risk": {"type": "string", "enum": ["low", "medium", "high"]},
                 "scope": {"type": "object", "description": "Exact action payload that will be authorized"}},
                ["kind", "title"]),
        company_request_approval, "🛂"),
    "company_decide_approval": (
        _schema("company_decide_approval",
                "Founder only: decide a pending approval assigned to the founder. Never your own request.",
                {"approval_id": _str("Approval id"),
                 "decision": {"type": "string", "enum": ["approve", "reject", "request_changes"]},
                 "note": _str("Reason for the decision")},
                ["approval_id", "decision"]),
        company_decide_approval, "⚖️"),
    "company_find_colleagues": (
        _schema("company_find_colleagues",
                "Find colleagues by skill tags / role family, with their open-task load. Use before creating or "
                "delegating tasks, or before asking for a hire.",
                {"skill_tags": {"type": "array", "items": _S, "description": "Skill tags, all must match"},
                 "family": _str("Role family, e.g. engineering, design, research"),
                 "status": _str("Agent status (default: active)")},
                []),
        company_find_colleagues, "👥"),
    "company_request_hire": (
        _schema("company_request_hire",
                "Ask HR to fill a capability gap: creates a hiring task for the HR agent. Check "
                "company_find_colleagues first.",
                {"required_skills": {"type": "array", "items": _S, "description": "Skills/tags the new hire needs"},
                 "reason": _str("Why the company needs this hire"),
                 "role_hint": _str("Suggested role, e.g. 'frontend engineer'"),
                 "task_id": _str("The task that needs the hire (default: your task)")},
                ["required_skills", "reason"]),
        company_request_hire, "🙋"),
    "company_search_skills": (
        _schema("company_search_skills",
                "HR: search the skill catalog (company library, Hermes skills, GitHub) for a capability.",
                {"query": _str("Search words, or owner/repo for source=github"),
                 "source": {"type": "string", "enum": SKILL_SOURCES},
                 "limit": {"type": "number", "description": "Max results (default 20)"}},
                []),
        company_search_skills, "🔎"),
    "company_install_skill": (
        _schema("company_install_skill",
                "HR: install a skill into the company library. source_ref is 'hermes:<slug>' or "
                "'github:owner/repo[@ref]:path' (from company_search_skills). Quarantined skills are unusable.",
                {"source_ref": _str("Skill source reference"),
                 "slug": _str("Library slug override (optional)")},
                ["source_ref"]),
        company_install_skill, "📥"),
    "company_author_skill": (
        _schema("company_author_skill",
                "HR: write a new company skill (concise, procedural SKILL.md body) when nothing suitable exists.",
                {"slug": _str("kebab-case id"), "name": _str("Skill name"),
                 "description": _str("One sentence: when to use this skill"),
                 "tags": {"type": "array", "items": _S}, "body_markdown": _str("SKILL.md body (markdown)"),
                 "files": {"type": "array", "items": {"type": "object"},
                           "description": "Extra files: [{path, content}]"}},
                ["slug", "name", "description", "body_markdown"]),
        company_author_skill, "✍️"),
    "company_assign_skills": (
        _schema("company_assign_skills",
                "HR: give installed skills to an employee (never yourself).",
                {"agent_id": _str("Employee agent id"),
                 "skill_slugs": {"type": "array", "items": _S, "description": "Library skill slugs"}},
                ["agent_id", "skill_slugs"]),
        company_assign_skills, "🎓"),
    "company_list_role_templates": (
        _schema("company_list_role_templates",
                "HR/founder: list role templates (title, family, default tools and skills).", {}, []),
        company_list_role_templates, "📋"),
    "company_create_role_template": (
        _schema("company_create_role_template",
                "HR/founder: define a new role template when no existing one fits. Keep toolsets minimal.",
                {"slug": _str("kebab-case id"), "title": _str("Role title"), "family": _str("Role family"),
                 "description": _str("What the role does"),
                 "instructions": _str("Role playbook for the employee"),
                 "default_toolsets": {"type": "array", "items": _S,
                                      "description": "e.g. company, file, web, skills, todo"},
                 "default_skill_slugs": {"type": "array", "items": _S},
                 "required_skill_tags": {"type": "array", "items": _S}},
                ["slug", "title"]),
        company_create_role_template, "🧩"),
    "company_hire_agent": (
        _schema("company_hire_agent",
                "HR: hire an employee from a role template. Activated automatically only if company limits allow; "
                "otherwise an approval is created. Complete the hiring task afterwards.",
                {"role_template": _str("Role template slug"), "name": _str("Distinct first name"),
                 "manager_agent_id": _str("Agent id of the new hire's manager"),
                 "role_title": _str("Precise job title (optional)"),
                 "department": _str("Department name (optional)"),
                 "skill_slugs": {"type": "array", "items": _S, "description": "Extra skills beyond the template"},
                 "monthly_budget_usd": {"type": "number", "description": "Monthly budget cap in US dollars"},
                 "reason": _str("Why this hire"),
                 "task_id": _str("The hiring task id (default: your task)")},
                ["role_template", "name", "manager_agent_id"]),
        company_hire_agent, "🤝"),
}

def _availability(tool_name: str) -> Callable[[], bool]:
    return no_cache_check_fn(lambda: ctx.tool_enabled(tool_name))


for _name, (_schema_def, _handler, _emoji) in _TOOLS.items():
    registry.register(
        name=_name,
        toolset="company",
        schema=_schema_def,
        handler=_handler,
        check_fn=_availability(_name),
        emoji=_emoji,
        max_result_size_chars=MAX_TOOL_RESULT_CHARS,
    )
