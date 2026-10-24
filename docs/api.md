# Company OS HTTP API

Base path `/api`. JSON only. Conventions in `docs/architecture.md` §8. The live OpenAPI
schema is served at `/api/openapi.json` (docs UI at `/api/docs`). This file is the
human-readable contract; when they disagree, fix one of them.

All timestamps are ISO-8601 UTC strings (`2026-10-23T12:00:00Z`). Money fields end in
`_micros` (integer micro-USD).

## Errors

```json
{ "error": { "code": "not_found", "message": "Agent not found", "details": null } }
```

| HTTP | code |
|---|---|
| 400 | `bad_request` |
| 401 | `unauthenticated` |
| 403 | `forbidden` |
| 404 | `not_found` |
| 405 | `method_not_allowed` |
| 409 | `conflict`, `invalid_transition` |
| 422 | `validation_error` (`details` = list of `{loc, msg}`) |
| 500 | `internal_error` (no stack traces or secrets in body) |

## Pagination

`?limit=50&cursor=<opaque>` (`limit` 1-200, default 50; a malformed cursor is 400 `bad_request`) → `{ "items": [...], "next_cursor": "<opaque>" | null }`.

---

## Milestone 1 resources

### Health & identity

`GET /api/health` → `{ "status": "ok", "database": "ok", "version": "0.1.0", "auth_mode": "local" }`
(if the database check fails: HTTP 503 with `status: "degraded"`, `database: "error"`).

`GET /api/me` →
```json
{
  "user": { "id": "uuid", "email": "owner@localhost", "display_name": "Owner" },
  "memberships": [ { "company_id": "uuid", "company_name": "Acme", "role": "owner" } ]
}
```

### Company

```json
Company = {
  "id": "uuid", "name": "Acme Labs", "slug": "acme-labs",
  "mission": "string|null", "vision": "string|null",
  "strategic_goal": "string|null",
  "constraints": ["string"],
  "timezone": "UTC",
  "status": "active" | "paused",
  "created_at": "ts", "updated_at": "ts"
}
```

- `GET /api/companies` → `{ items: Company[], next_cursor }` (only companies I am a member of)
- Request bodies reject unknown fields (422 `validation_error`). `slug` is generated from the name on create and never changes.
- `POST /api/companies` body:
  ```json
  { "name": "required 1-120", "mission": "?", "vision": "?", "strategic_goal": "?",
    "constraints": ["?"], "timezone": "IANA tz, default UTC",
    "founder": { "name": "default 'Atlas'", "model_provider": "?", "model_name": "?", "runtime": "default 'unassigned'" } }
  ```
  → 201 `Company`. Side effects in one transaction: owner membership for caller, an Agent
  with `kind=founder`, `status=active`, `reports_to_human=true`, `role_title="Founder & CEO"`;
  AuditEvent `company.created`.
- `GET /api/companies/{cid}` → `Company`
- `PATCH /api/companies/{cid}` (owner/admin) body: any of `name, mission, vision, strategic_goal, constraints, timezone, status` → `Company`; AuditEvent `company.updated` with changed field names (not values of large text).

### Overview (Command Center)

`GET /api/companies/{cid}/overview` →
```json
{
  "company": Company,
  "founder": Agent | null,
  "agents": { "total": 3, "by_status": { "proposed": 0, "active": 2, "paused": 1, "retiring": 0, "retired": 0, "rejected": 0 } },
  "departments": { "total": 2 },
  "tasks":     { "available": true, ... },   // real aggregates since Milestone 2: see "Overview aggregates (M2)"
  "spending":  { "available": true, ... },
  "approvals": { "available": true, ... },
  "artifacts": { "available": true, ... },
  "routines":  { "available": true, ... },
  "recent_activity": [AuditEvent]   // newest 10
}
```
(In Milestone 1 these five blocks were `{ "available": false, "milestone": "Mx" }`. The `available`
flag stays in the contract: any future subsystem that is not built yet must report
`available: false` rather than zeros. `founder` is an `Agent` including `live`.)

### Departments

```json
Department = { "id", "company_id", "name", "description": "string|null", "lead_agent_id": "uuid|null", "agent_count": 0, "created_at", "updated_at" }
```
- `agent_count` counts agents assigned to the department whose status is not `retired`/`rejected`. `name` clash → 409 `conflict`. Lists are oldest-first.
- `GET /api/companies/{cid}/departments` → `{ items, next_cursor }`
- `POST /api/companies/{cid}/departments` `{ name (unique per company, 1-80), description?, lead_agent_id? }` → 201
- `PATCH /api/companies/{cid}/departments/{id}` → Department

### Agents (AI employees)

```json
Agent = {
  "id", "company_id",
  "name": "string", "role_title": "string",
  "kind": "founder" | "lead" | "specialist" | "reviewer",
  "status": "proposed" | "active" | "paused" | "retiring" | "retired" | "rejected",
  "department_id": "uuid|null",
  "manager_agent_id": "uuid|null",
  "reports_to_human": false,
  "responsibilities": "string|null",
  "instructions": "string|null",
  "model_provider": "string|null", "model_name": "string|null",
  "runtime": "string",                // e.g. "unassigned", "hermes"
  "runtime_config": {},               // non-secret only; server rejects keys that look like secrets
  "tool_permissions": ["string"],
  "monthly_budget_micros": 0 | null,
  "daily_budget_micros": 0 | null,
  "created_at", "updated_at", "status_changed_at",
  "live": AgentLive | null            // M2: what the agent is doing right now (see Milestone 2)
}
```

- `GET /api/companies/{cid}/agents?status=&department_id=` → `{ items, next_cursor }`
- `POST /api/companies/{cid}/agents` → 201 Agent with `status=proposed`. `kind=founder` is rejected (409 `conflict`) if a live (proposed/active/paused/retiring) founder exists. Also records AuditEvent `agent.created`. `manager_agent_id`/`department_id` must belong to the same company (404 otherwise).
- `GET /api/companies/{cid}/agents/{id}` → Agent
- `PATCH /api/companies/{cid}/agents/{id}` → Agent. Editable: name, role_title, department_id, manager_agent_id, responsibilities, instructions, model_provider, model_name, runtime, runtime_config, tool_permissions, budgets. Not editable: status, kind, company_id. Retired agents are read-only (409). A manager cycle (including self-management) is rejected (409), as is a retired/rejected manager. Unknown/non-editable fields (`status`, `kind`, `company_id`, ...) are 422. Agent principals can never change their own (or anyone's) `tool_permissions`, budgets, `runtime` or `runtime_config`, nor perform lifecycle actions on themselves (403 `forbidden`). `runtime_config` keys (at any depth) matching `/key|secret|token|password|credential/i` are 422 `validation_error` with `details[].loc = ["body","runtime_config",...]`.
- Lifecycle: `POST /api/companies/{cid}/agents/{id}/{action}` with optional `{ "reason": "string" }` → Agent.

  | action | from | to |
  |---|---|---|
  | `activate` | proposed | active |
  | `reject` | proposed | rejected |
  | `pause` | active | paused |
  | `resume` | paused | active |
  | `retire` | active, paused | retired (M1: immediate; M7 adds `retiring` with handover) |

  Invalid → 409 `invalid_transition`. The founder cannot be retired while it is the only founder (409). Every transition writes an AuditEvent named in the past tense: `agent.activated`, `agent.rejected`, `agent.paused`, `agent.resumed`, `agent.retired` (`data` = `{from, to, reason?}`). Retiring also clears `lead_agent_id` on any department the agent led. Since there can be only one live founder, the founder can never be retired in M1 (409 `conflict`); it can be paused.

### Activity

```json
AuditEvent = { "id", "company_id", "actor_type": "human" | "agent" | "system", "actor_id": "uuid|null", "actor_label": "string",
               "action": "agent.paused", "target_type": "agent", "target_id": "uuid|null", "summary": "string", "data": {}, "created_at" }
```
- `GET /api/companies/{cid}/activity?limit=&cursor=&target_type=&target_id=` → `{ items, next_cursor }`, newest first.

### Runtimes

`GET /api/runtimes` →
```json
{ "items": [ {
    "id": "hermes", "display_name": "Hermes Agent", "kind": "agent_runtime",
    "capabilities": ["streaming_events", "cancel", "usage_tokens", "usage_cost", "approvals", "idempotent_start"],
    "health": { "status": "ok" | "degraded" | "unavailable" | "not_configured", "detail": "string", "checked_at": "ts" },
    "config_hint": "Set HERMES_API_URL and HERMES_API_KEY ..."
} ] }
```
Hermes health: `not_configured` if `HERMES_API_URL` is unset (or not an http(s) URL); else
`GET {url}/health` with Bearer key (2s timeout) → `unavailable` on error/non-200. Hermes' `/health`
is unauthenticated, so on 200 we also probe the authenticated `GET /health/detailed`: 401/403 →
`degraded` (key rejected), 200 → `ok`; a missing `HERMES_API_KEY` is also `degraded`. The key is
never echoed back. Other audit actions: `company.created|updated`, `department.created|updated`,
`agent.created|updated`.


---

# Milestone 2: work engine

Everything below is under `/api/companies/{cid}` unless stated. Mutations need a human
`owner`/`admin` (viewers: 403 on every write; agent principals: 403 on the owner API, they
use the agent API). Non-members get 404. Cross-company ids (agent, task, project, ...)
referenced in a body are 404. Lists are `{items, next_cursor}`; unless noted, ordering is
oldest first (goals, projects, schedules) or **newest first** (tasks, task runs, artifacts,
approvals, decisions).

New error codes: `409 invalid_transition` (state machine), `409 limit_exceeded` (policy
limit, `details.limit` names it), `409 budget_exceeded` (only inside `Task.error`), `413
payload_too_large`, `401 unauthenticated` (agent API / run token).

Money is integer micro-USD (`*_micros`). Every cost carries a `cost_kind`:
`verified` (a real charge reported by the runtime/provider), `estimated`, or `unknown`.
An estimate is never presented as a charge.

## Policy

`CompanyPolicy` (1:1 with the company, created with it; existing companies were backfilled).
Only human owners/admins may change it; agents, even with their own run token, get 403.

```json
Policy = {
  "id", "company_id",
  "autonomy_mode": "autonomous" | "supervised",
  "human_approval_required_for": ["external_purchase", "budget_increase"],   // approval kinds
  "daily_budget_micros": null, "monthly_budget_micros": null,                // null = no limit
  "max_concurrent_runs": 2, "max_delegation_depth": 3, "max_tasks_per_planning_cycle": 10,
  "max_attempts_per_task": 3, "max_revisions": 2, "max_run_seconds": 900,
  "max_headcount": 12, "heartbeat_interval_seconds": 900,
  "default_run_reservation_micros": 50000,
  "allowed_model_providers": [],                                              // [] = any
  "created_at", "updated_at"
}
```
- `GET /policy` -> `Policy`; `PATCH /policy` (any subset of the fields above except `id`, `company_id`,
  timestamps; unknown fields 422) -> `Policy`. Audit `policy.updated` (`data.changed` = field names).
  Changing a budget field re-evaluates tasks blocked by budget; changing `allowed_model_providers`
  re-evaluates tasks blocked by `provider_not_allowed`.
- Approval kinds: `external_purchase`, `budget_increase`, `external_communication`,
  `production_deploy`, `sensitive_action`, `hire`, `other`.
- `autonomy_mode: "supervised"` makes **every** approval a human approval.
- Enforced in code today: `max_concurrent_runs`, `max_delegation_depth`, `max_tasks_per_planning_cycle`,
  `max_attempts_per_task`, `max_revisions`, `max_run_seconds`, `max_headcount` (agents proposing hires),
  budgets, `allowed_model_providers` (at dispatch). `heartbeat_interval_seconds` is stored for M5.

## Goals and projects

```json
Goal    = { "id", "company_id", "title", "description": null, "status": "proposed|active|achieved|abandoned",
            "priority": "low|normal|high|urgent", "owner_agent_id": null, "target_date": "2027-06-30" | null,
            "created_by_type": "human|agent|system", "created_by_id", "created_at", "updated_at" }
Project = { "id", "company_id", "goal_id": null, "name", "description": null,
            "status": "planned|active|paused|completed|cancelled", "lead_agent_id": null,
            "budget_micros": null, "created_by_type", "created_by_id", "created_at", "updated_at" }
```
- `GET/POST /goals` (`?status=&owner_agent_id=`), `GET/PATCH /goals/{id}`.
  POST body: `title` (1-200), `description?`, `status?` (default `proposed`), `priority?`, `owner_agent_id?`, `target_date?`.
- `GET/POST /projects` (`?status=&goal_id=`), `GET/PATCH /projects/{id}`.
  POST body: `name` (1-200), `description?`, `goal_id?`, `status?` (default `planned`), `lead_agent_id?`, `budget_micros?`.
- Audit: `goal.created|updated`, `project.created|updated`.

## Tasks

```json
Task = {
  "id", "company_id", "project_id": null, "parent_task_id": null,
  "title", "description": null, "acceptance_criteria": null,
  "kind": "work|review|planning|hiring|routine",
  "status": "backlog|ready|in_progress|in_review|revision_requested|blocked|done|failed|cancelled",
  "priority": "low|normal|high|urgent",
  "assignee_agent_id": null, "reviewer_agent_id": null,
  "required_skills": [], "budget_micros": null,
  "attempt_count": 0, "revision_count": 0, "delegation_depth": 0,
  "due_at": null, "started_at": null, "completed_at": null,
  "result_summary": null,
  "error": null | { "code", "message", "run_id": null, ...extra },   // e.g. budget_exceeded also has "limit", "retry_after"
  "input_refs": [], "output_refs": [],
  "review_note": null,                 // last reviewer feedback (extension)
  "created_by_type", "created_by_id", "created_by_run_id": null,   // run that created it, if an agent did
  "idempotency_key": null,
  "depends_on": ["task id", ...],      // dependencies (extension: embedded for convenience)
  "created_at", "updated_at"
}
```

**State machine** (anything else is `409 invalid_transition`):

```
backlog ──(assignee active + deps done + company active)──▶ ready ──dispatch──▶ in_progress
   ▲ (conditions stop holding)◀──────────────────────────────┘                   │
in_progress ─ success, acceptance_criteria + resolvable reviewer ─▶ in_review ─accept─▶ done
in_progress ─ success otherwise ───────────────────────────────────────────────────▶ done
in_progress ─ failed/timed out, attempts left ─▶ ready (retry)   no attempts ─▶ failed
in_review ─request_revision─▶ revision_requested ─▶ ready (revision_count+1; > max_revisions ─▶ failed)
any open state ◀─block / unblock─▶ blocked        any non-terminal ─cancel─▶ cancelled
failed ─retry (explicit)─▶ backlog         terminal: done, failed, cancelled
```
Readiness is recomputed whenever a task is created/assigned/edited, a dependency finishes or is
added/removed, an agent's lifecycle status changes, or the company is paused/resumed. The reviewer is
`reviewer_agent_id`, else the assignee's manager agent (never the assignee). A revision resets
`attempt_count` to 0. Unblocking returns the task to `backlog` and readiness decides.

- `GET /tasks` `?status=&project_id=&assignee_agent_id=&parent_task_id=&kind=&limit=&cursor=` (newest first).
- `POST /tasks` -> **201** `Task` (or **200** with the existing task when `idempotency_key` was already used
  in this company). Body: `title` (1-300), `description?`, `acceptance_criteria?`, `kind?` (default `work`),
  `priority?` (default `normal`), `project_id?`, `parent_task_id?`, `assignee_agent_id?`, `reviewer_agent_id?`,
  `depends_on?` [task ids], `required_skills?`, `budget_micros?`, `due_at?` (needs a timezone offset),
  `input_refs?`, `idempotency_key?`. `delegation_depth` = parent's + 1; deeper than
  `policy.max_delegation_depth` -> 409 `limit_exceeded`. Assigning a retired/rejected agent -> 409.
- `GET /tasks/{id}`; `PATCH /tasks/{id}` (`title, description, acceptance_criteria, kind, priority, project_id,
  assignee_agent_id, reviewer_agent_id, required_skills, budget_micros, due_at, input_refs`; terminal tasks are
  read-only; a running task cannot be reassigned).
- `POST /tasks/{id}/cancel` (`{reason?}`; also cancels active runs), `POST /tasks/{id}/block` (`{reason?}`; blocks a
  running task and cancels its run), `POST /tasks/{id}/unblock`, `POST /tasks/{id}/retry` (failed only; resets attempts).
- `POST /tasks/{id}/review` `{decision: "accept"|"request_revision", note?}`: the task must be `in_review`;
  humans (owner/admin) or the resolved reviewer agent.
- `POST /tasks/{id}/dependencies` `{depends_on_task_id}` -> 201 `Task`; cycles, self-dependency and duplicates -> 409.
  `DELETE /tasks/{id}/dependencies/{depends_on_task_id}` -> `Task`.
- Tasks blocked by the dispatcher carry `error.code`: `budget_exceeded` (with `limit` = `task_budget|project_budget|
  agent_daily|agent_monthly|company_daily|company_monthly` and `retry_after` for periodic limits; released
  automatically when the period rolls over or a budget is raised) or `provider_not_allowed`.
  Manual blocks use `error.code = "blocked"`; a user-cancelled run blocks its task with `run_cancelled`.

## Runs and run events

```json
TaskRun = {
  "id", "company_id", "task_id", "agent_id",
  "attempt_number",                       // 1-based, counts every run of the task
  "status": "queued|starting|running|succeeded|failed|cancelled|timed_out",
  "runtime", "runtime_execution_id": null,
  "idempotency_key": "{task_id}:{attempt_number}:{revision_count}",
  "queued_at", "started_at": null, "finished_at": null, "heartbeat_at": null, "worker_id": null,
  "cancel_requested": false, "cancel_reason": null,   // cancelled_by_user|company_paused|task_cancelled|task_blocked|worker_shutdown
  "exit_reason": null,                    // completed|timeout|worker_lost|job_dead|runtime_exception|...
  "error": null | { "code", "message" },
  "input_tokens": null, "output_tokens": null,
  "cost_micros": null, "cost_kind": "verified|estimated|unknown",
  "reservation_id": null,
  "activity_summary": null, "activity_at": null,       // latest human-readable line (live)
  "created_at", "updated_at"
}
RunEvent = { "id", "run_id", "seq", "type", "payload": {...}, "occurred_at" }
```
- `GET /tasks/{id}/runs` (newest first), `GET /runs/{id}`.
- `GET /runs/{id}/events?after_seq=0&limit=200` -> `{items: RunEvent[], next_cursor}` ordered by `seq`;
  `next_cursor` is the last `seq` returned (pass it back as `after_seq`) or null. Payloads are redacted:
  secret-looking keys become `"[redacted]"`. Runtime events use the runtime's own `seq` (1, 2, ...);
  events posted through the agent API (`progress`) use seq >= 1,000,000.
- `POST /runs/{id}/cancel` `{reason?}` -> `TaskRun`. Queued runs are cancelled immediately; started ones get
  `cancel_requested` and stop cooperatively. Cancelling a run blocks its task (`run_cancelled`).
  Finished run -> 409.
- Unknown cost: the reservation is settled as an **estimate** (`cost_kind: "estimated"`), never as `verified`.

## Artifacts

```json
Artifact = { "id", "company_id", "project_id": null, "task_id": null, "run_id": null,
             "created_by_type", "created_by_id", "title", "kind": "document|code|design|report|data|link|other",
             "mime_type", "status": "draft|submitted|accepted|rejected", "current_version", "created_at", "updated_at" }
ArtifactDetail = Artifact + { "versions": [ { "id", "artifact_id", "version", "size_bytes", "sha256",
                              "created_by_type", "created_by_id", "note", "created_at" } ] }
```
Content lives in the storage layer (`data/artifacts/{company_id}/{artifact_id}/{version}`), never in the DB.
- `GET /artifacts` `?project_id=&task_id=&kind=&status=&q=` (title search; newest first).
- `POST /artifacts` -> 201 `ArtifactDetail`. Either `multipart/form-data` with a `file` part plus text fields
  `title` (defaults to the file name), `kind`, `task_id`, `project_id`, `note`, `mime_type`; or JSON
  `{title, kind, content, mime_type?, task_id?, project_id?, note?}` for text. Add `artifact_id` to create a new
  version instead (a rejected artifact returns to `draft`). Over `COMPANY_OS_MAX_UPLOAD_BYTES` (25 MB) -> 413.
- `GET /artifacts/{id}` (with versions); `PATCH /artifacts/{id}` `{title?, status?}`
  (`draft -> submitted|accepted|rejected`, `submitted -> accepted|rejected`, `rejected -> draft|submitted`).
- `GET /artifacts/{id}/versions/{v}/content` streams the bytes with the stored mime type, `ETag` = sha256,
  `X-Content-Type-Options: nosniff`, and `Content-Disposition: inline` only for `text/plain`, `text/markdown`,
  `text/csv`, `application/json` (everything else, including html/svg, is `attachment`).

## Approvals and decisions

```json
Approval = { "id", "company_id", "kind", "status": "pending|approved|rejected|changes_requested|expired",
             "approver": "human|founder", "requested_by_agent_id": null, "task_id": null, "title", "reason": null,
             "amount_micros": null, "vendor": null, "recurrence": "one_time|monthly|yearly"|null,
             "risk": "low|medium|high", "evidence": {}, "scope": {}, "scope_hash",
             "decided_by_type": null, "decided_by_id": null, "decided_at": null, "decision_note": null,
             "consumed_at": null, "created_at", "updated_at" }
Decision = { "id", "company_id", "made_by_type", "made_by_id", "title", "rationale": null,
             "related_task_id": null, "related_project_id": null, "context": {}, "created_at" }
```
- `GET /approvals` `?status=&approver=` (newest first), `GET /approvals/{id}`.
- `POST /approvals/{id}/approve|reject|request_changes` `{note?}` -> `Approval` (pending only, else 409).
  Owners/admins may decide any approval; agents are rejected here unless they are the active founder deciding a
  `founder` approval they did not request.
- `approver` is derived when the approval is created: `human` if the kind is in
  `policy.human_approval_required_for` (or the company is `supervised`), else `founder`.
- An approval authorizes **one** action: the internal `approvals.consume(approval_id, scope)` succeeds only for an
  approved, unconsumed approval whose canonical-JSON SHA-256 equals `scope_hash`, then sets `consumed_at`.
- `GET /decisions` `?related_task_id=&related_project_id=` (newest first). Decisions are recorded by agents (agent API).

## Schedules

```json
Schedule = { "id", "company_id", "name", "kind": "heartbeat|routine", "agent_id",
             "interval_seconds": 3600 | null, "cron": "0 9 * * 1-5" | null,   // exactly one is set
             "timezone": "UTC", "task_template": { "title?", "description?", "kind?", "priority?", "acceptance_criteria?" },
             "enabled": true, "next_run_at": null, "last_fired_at": null, "created_at", "updated_at" }
```
- `GET /schedules` `?enabled=&kind=&agent_id=`, `POST /schedules` (`interval_seconds` >= 60 **xor** 5-field `cron`;
  `timezone` defaults to the company's), `GET /schedules/{id}`, `PATCH /schedules/{id}` (changing timing or
  `enabled` recomputes `next_run_at`; disabling clears it).
- Firing creates a task assigned to `agent_id` (default kind: `planning` for heartbeats, `routine` for routines). Each
  (schedule, slot) fires at most once, across schedulers and restarts; after downtime only the latest missed slot
  fires. A heartbeat is skipped while its previous task is still open. Paused companies do not fire.

## Usage

`GET /usage?from=&to=&group_by=agent|project|model|day` (defaults: this month so far, `group_by=agent`).
`from`/`to` are `YYYY-MM-DD` (company timezone; the `to` day is inclusive) or ISO-8601 instants (`to` exclusive).
```json
{ "group_by": "agent", "from": "2026-10-01T00:00:00Z", "to": "2026-10-24T09:00:01Z",
  "items": [ { "key": "agent id | project id | model | YYYY-MM-DD", "label": "Ada" | null,
               "input_tokens": 0, "output_tokens": 0, "cached_tokens": 0, "cost_micros": 0,
               "by_cost_kind": { "verified_micros": 0, "estimated_micros": 0, "unknown_micros": 0 }, "records": 0 } ],
  "totals": { ...same shape, "key": null, "label": "Total" } }
```
Items are sorted by cost (descending), or by day ascending. This is deliberately minimal; M6 expands it.

## Company kill switch

- `POST /pause` -> `Company` (`status: "paused"`): stops dispatch and schedule firing, cancels queued runs on the
  spot and sets `cancel_requested` (`company_paused`) on running ones; their tasks return to the queue with the
  attempt refunded. Already paused -> 409. `PATCH /companies/{cid}` with `status: "paused"` does the same.
- `POST /resume` -> `Company` (`status: "active"`); ready work is dispatched again. Not paused -> 409.

## Live stream (SSE)

`GET /stream` (`text/event-stream`). Frames:
```
retry: 3000

id: 42
event: task.updated
data: {"id":42,"type":"task.updated","company_id":"...","payload":{...},"created_at":"2026-10-24T09:00:00Z"}

: keepalive
```
`id` is the company event id (monotonic per database). Resume with the `Last-Event-ID` header or `?after=<id>`;
with neither the stream starts at "now" (`?after=0` replays the retained history, which is pruned after 7 days).
Heartbeat comment every 15 s; the server polls the outbox every second. Membership is checked like any company route.

Event types (payloads never contain secrets): `activity` (mirrors an audit event: `id, action, actor_type,
actor_label, target_type, target_id, summary`), `task.created`, `task.updated` (`task_id, status, previous_status,
title, assignee_agent_id, project_id, parent_task_id`), `run.queued`, `run.started`, `run.event` (`run_id, task_id,
agent_id, seq, type, summary`), `run.finished` (`run_id, task_id, agent_id, status, exit_reason`),
`run.cancel_requested`, `agent.updated` (`agent_id, status`), `agent.activity` (`agent_id, state, task_id, run_id,
summary`; throttled to ~1 per second per run), `artifact.created|updated`, `approval.created|decided`,
`decision.created`, `goal.created|updated`, `project.created|updated`, `schedule.created|updated|fired`,
`policy.updated`, `company.updated`.

## Agent activity ("live")

`GET /agents/{id}/activity-now` -> `AgentLive`, also embedded as `live` in every `Agent` response (list, detail,
create, update, lifecycle, `overview.founder`):
```json
AgentLive = {
  "state": "idle" | "working" | "blocked" | "paused" | "offline",
  "current_task": { "id", "title" } | null,
  "current_run": { "id", "status", "started_at" } | null,
  "summary": "Working on: ..." | "reading the brief" | null,     // latest line the runtime reported
  "since": "ts" | null
}
```
`working` = a run is queued/starting/running; `blocked` = nothing runnable and at least one of its tasks is blocked;
`paused` = the agent or the whole company is paused; `offline` = proposed/retiring/retired/rejected.

## Overview aggregates (M2)

`GET /overview` blocks (all with `"available": true`):
```json
"tasks":     { "available": true, "total": 5, "open": 3,
               "by_status": { "backlog": 1, "ready": 1, "in_progress": 1, "in_review": 0, "revision_requested": 0,
                              "blocked": 0, "done": 2, "failed": 0, "cancelled": 0 },
               "waiting_for_runtime": 1,        // ready tasks whose assignee's runtime cannot execute (unassigned/unbuilt)
               "blocked_by_budget": 0 },
"spending":  { "available": true, "timezone": "UTC",
               "today": { "spent_micros": 420000, "held_micros": 50000,
                          "by_cost_kind": { "verified_micros": 300000, "estimated_micros": 120000, "unknown_micros": 0 },
                          "budget_micros": 10000000 | null },
               "month": { ...same shape... } },
"approvals": { "available": true, "pending_total": 2, "pending_by_approver": { "human": 1, "founder": 1 } },
"artifacts": { "available": true, "total": 7, "latest": [ Artifact x up to 5, newest first ] },
"routines":  { "available": true, "total": 4, "enabled": 3,
               "upcoming": [ { "id", "name", "kind", "agent_id", "agent_name", "next_run_at" } ] }   // next 5 enabled
```
`spent_micros` comes from usage records; `held_micros` is reserved by runs that have not finished (kept apart so
reservations are never mistaken for spend). Periods use the company timezone.

## Agent API (`/api/agent/v1`)

Used by runtimes during a run. Auth: `Authorization: Bearer <run token>`. The token (`cos_run.<payload>.<sig>`) is
HMAC-SHA256 signed with `COMPANY_OS_SECRET_KEY` (or an auto-generated `data/secret.key`, mode 0600) over
`{run_id, agent_id, company_id, exp}`, and is valid only while the run is `starting`/`running` and unexpired;
any other case is `401 unauthenticated` with the same message. It resolves to an agent principal bound to the run's
company; everything it writes is attributed to that agent and audited. The same token is also accepted on the owner API
(`/api/companies/{cid}/...`), where the caller is that agent: it can read its own company but every mutation that is
human-only (policy, budgets, permissions, lifecycle, pause, task administration) answers 403, and any other
company answers 404. A request with neither a run token nor an owner credential is `401` (Milestone 3b; see "Authentication" below).

| Method | Path | Purpose |
|---|---|---|
| GET | `/context` | company, run, current task (+ parent, dependencies with results, artifacts, `review_note`), own profile, manager, direct reports, active colleagues (name, role, responsibilities; skill tags arrive with M4: `skills_available: false`), relevant policy limits and `tasks_created_this_run` |
| POST | `/tasks` | create a task/subtask (`TaskCreate` body; `parent_task_id` defaults to the current task). 201, or 200 for a repeated `idempotency_key`. Enforces `max_delegation_depth`, `max_tasks_per_planning_cycle` (per run), and `budget_micros` <= the parent's remaining budget (409 `limit_exceeded`) |
| PATCH | `/tasks/{id}` | `{status: "blocked"|"unblocked", reason?, assignee_agent_id?}` on tasks the agent created or manages (or its own current task, for blocking). Any other field is 422 |
| POST | `/tasks/{id}/complete` | `{result_summary, output_refs?}` - assignee only, its current task, during the run -> `done` or `in_review` |
| POST | `/tasks/{id}/review` | reviewer only: `{decision: "accept"|"request_revision", note?}` |
| POST | `/artifacts` | multipart (`file` + `title`, `kind`, `task_id`, ...) or JSON `{title, kind, content}`; `artifact_id` adds a version (only on its own artifacts / current task). `task_id` defaults to the current task |
| POST | `/progress` | `{message}` -> RunEvent `progress` (seq >= 1,000,000) + live activity line |
| POST | `/decisions` | `DecisionCreate` (`related_task_id` defaults to the current task) |
| POST | `/approvals` | `ApprovalCreate` -> `201 {id, status, approver}` |
| GET | `/approvals/{id}` | poll (requester or founder only) |
| POST | `/approvals/{id}/decide` | `{decision: "approve"|"reject"|"request_changes", note?}`: the active founder agent only, for `approver=founder` approvals it did not request |

Agents can also propose colleagues with `POST /api/companies/{cid}/agents` (their token): they may not set
permissions, budgets or runtime, and `policy.max_headcount` caps the number of live agents (409 `limit_exceeded`).

## Processes

- `uv run companyos-worker`: scheduler loop (every 2 s: fire schedules, dispatch, recovery every 30 s) plus the
  worker loop (leases `execute_run` jobs, `COMPANY_OS_WORKER_CONCURRENCY` threads). SIGTERM/SIGINT stop leasing,
  cancel running executions cooperatively (their tasks return to `ready`, attempt refunded) and wait up to 10 s.
- `COMPANY_OS_EMBEDDED_WORKER=1` runs the same loops inside the API process.
- `COMPANY_OS_ENABLE_TEST_RUNTIMES=1` registers the `scripted` runtime (`kind: "test_fixture"` in `GET /api/runtimes`);
  its behaviour comes from `agent.runtime_config.script` (`events`, `delay_seconds`, `block_until_cancel`, `result`,
  `summary`, `error_message`, `fail_first_attempts`, `raise_exception`, `usage`, `artifacts`). It never calls a model.
- New settings: `COMPANY_OS_SECRET_KEY`, `COMPANY_OS_DATA_DIR`, `COMPANY_OS_MAX_UPLOAD_BYTES`,
  `COMPANY_OS_AGENT_API_URL`, `COMPANY_OS_WORKER_CONCURRENCY`, `COMPANY_OS_ENGINE_TICK_SECONDS`,
  `COMPANY_OS_ENGINE_POLL_SECONDS`.

## Known limits (Milestone 2)

- ~~Local auth mode has no authentication~~ Fixed in Milestone 3b: every request needs an owner credential or a run
  token (see "Milestone 3b" > "Authentication").
- Only the `scripted` test runtime can execute tasks; the Hermes adapter reports health but `supports_execution` is
  false, so tasks assigned to it wait ("waiting for runtime"). Nothing here was exercised against a real model.
- The SSE cursor is the outbox row id. On SQLite writers are serialized so ids commit in order; on Postgres a
  slow transaction could in principle commit a lower id after a higher one was already delivered.

---

# Milestone 4: skills & HR

Spec: `docs/specs/m4-skills-and-hr.md`. Migration `0003_skills_and_roles` (existing companies get an HR
agent "Harper" in a "People" department and a queued `seed_skill_library` job; existing agents get
`role_family` = `founder` for founders, else `generic`). Run `uv run alembic upgrade head` before starting
the API: it refuses to start without the `skills` table.

New error codes: `422 license_not_allowed` (GitHub skill with a missing/unlisted license),
`503 scanner_unavailable` (the runtime's security scanner is not installed or failed: only built-in skills
can be imported), `502 upstream_error` (GitHub failed or rate-limited; the token is never echoed).

## Changed resources

- `Agent` gains `role_template: string|null` (slug) and `role_family` (`founder|hr|engineering|design|marketing|
  sales|customer_success|research|qa|operations|finance|generic`, default `generic`; founders are `founder`).
  `PATCH /agents/{id}` still rejects both (unknown field): they are set by templates only.
- `POST /agents` accepts `role_template` (slug) and `skill_slugs`. For every field **not** sent, the template's
  defaults apply (`kind`, department by name - created if missing, `responsibilities`, `instructions`,
  `tool_permissions` = the template's toolsets) and the template's default skills (plus `skill_slugs`) that exist
  and are active in the company library are assigned. Humans only: an agent sending either field gets 403 (agents
  hire through HR). Unknown template: 404.
- Company creation now also creates the active HR agent **Harper** (`hr-partner`, reports to the founder,
  department "People" led by Harper) and queues the `seed_skill_library` job (the worker imports the library).
- `Policy` gains `auto_activate_hires: bool` (default true) and `hire_default_monthly_budget_micros: int|null`.
- `Task` gains `blocked_reason: string|null` (`"waiting_for_hire"` while a backlog task waits for a hire).
- `Approval` gains `subject_type` / `subject_id` (`"agent_hire"` + the proposed agent id for hire approvals; set
  only by the hiring service, never by clients).
- Approving a hire approval activates that agent (and consumes the approval); rejecting it rejects the agent;
  `request_changes` leaves it `proposed`.
- Agent API `GET /context`: every agent brief (self, manager, reports, colleagues) now has `role_family`,
  `skills` (slugs of assigned active skills) and `skill_tags`; `skills_available` is `true`.
- `GET /api/runtimes` and the rest are unchanged.

## Skills (owner API, `/api/companies/{cid}`)

```json
Skill = { "id", "company_id", "slug", "name", "description", "category": null, "tags": ["..."],
          "version": null, "author": null, "license": "MIT"|null,
          "source_kind": "hermes_bundled|hermes_optional|github|company_authored|uploaded",
          "source_ref": "hermes@db1fa153:skills/creative/claude-design | github:owner/repo@<commit sha>:path | company:<slug> | company_os:roles/skills/<slug>",
          "blob_sha256", "trust": "builtin|trusted|community",
          "scan_verdict": "safe|caution|dangerous|unscanned", "status": "active|quarantined|archived",
          "requires_toolsets": [], "required_env": ["NAMES_ONLY"], "created_by_type", "created_by_id",
          "created_at", "updated_at" }
SkillDetail = Skill + { "scan_report": { "verdict", "scanner_version", "summary", "finding_count",
                        "findings": [ { "category", "pattern", "severity", "file", "line", "description" } ] },
                        "frontmatter": {}, "files": [ { "path", "size_bytes" } ],
                        "assigned_agents": [ { "id", "name", "role_title", "status" } ] }
```

| Method & path | Who | Notes |
|---|---|---|
| `GET /skills` | any member | `?status=&source_kind=&tag=&q=&limit=&cursor=` (oldest first) |
| `GET /skills/catalog` | any member | `?q=&source=all\|library\|hermes\|github&limit=&cursor=` -> `{items: CatalogItem[], next_cursor}`; cursor is an offset. `CatalogItem = {slug, name, description, tags, license, source, source_kind, source_ref, item_type: skill\|repository, installed, skill_id, status, stars}`. `source=github`: `q=owner/repo` lists that repository's skills (pinned to its default-branch commit); other `q` runs a repository search (`topic:agent-skills`) and, with a token, a `filename:SKILL.md` code search |
| `POST /skills/install` `{source_ref, slug?}` | owner/admin | -> `{skill, outcome: installed\|updated\|unchanged, quarantined, scan}`; 201 when installed, else 200. Accepts `hermes:<slug or path>`, `hermes@<sha>:<path>`, `github:<owner>/<repo>[@<ref>]:<path>`, or a `https://github.com/<o>/<r>/tree/<ref>/<path>` URL. A different source under an existing slug is 409 (pass `slug`) |
| `POST /skills/seed` | owner/admin | queues the library seeding job -> 202 `{job_id, status: queued\|already_queued}`; `?wait=true` runs it inline and returns `{installed, updated, unchanged, quarantined, missing, agents_equipped}` |
| `GET /skills/{id}` | any member | `SkillDetail` |
| `GET /skills/{id}/files/{path}` | any member | `{path, content, truncated}` text preview (<= 200 KB); binary 409, traversal 400/404 |
| `POST /skills/{id}/archive` | owner/admin | `{note?}`; archived skills are never materialized; seeding does not resurrect them |
| `POST /skills/{id}/unquarantine` | owner/admin | `{note?}`: explicit human override of the scanner, audited with the overridden verdict |
| `GET /agents/{id}/skills` | any member | `{items: [{skill, assigned_by_type, assigned_by_id, assigned_at}]}` |
| `POST /agents/{id}/skills` `{skill_ids?, slugs?}` | owner/admin | only **active** skills (else 409); idempotent; returns the agent's skills |
| `DELETE /agents/{id}/skills/{skill_id_or_slug}` | owner/admin | 204 |
| `GET /role-templates`, `POST /role-templates` | members read / owner-admin create | `RoleTemplate = {slug, family, title, description, default_kind, default_department, instructions, default_toolsets, default_skill_slugs, required_skill_tags, builtin, id}`. Built-in templates (code, `backend/app/roles/builtin`) are visible to every company; custom ones live in the company. Slugs of built-ins are reserved (409) |

Policy for imports (mirrors `runtime/tools/skills_guard.py`):

| trust | who | safe | caution | dangerous |
|---|---|---|---|---|
| `builtin` | Hermes bundled/optional, Company OS seed skills | active | active | active (verdict recorded) |
| `trusted` | GitHub `openai/skills`, `anthropics/skills`, `huggingface/skills`, `NVIDIA/skills` (exact repos) | active | active | quarantined |
| `community` | any other GitHub repo, skills authored by HR | active | quarantined | quarantined |

A quarantined skill is stored (inspect it with `GET /skills/{id}`) but never assigned or materialized. Scanning runs
`<runtime python> -m company_agent.skills_scan <dir>`; if the runtime venv is missing, non-builtin imports are
refused (`503 scanner_unavailable`) and built-in ones are stored as `scan_verdict: "unscanned"`.
GitHub installs resolve the ref to a commit sha, download only that skill directory at the sha (limits are checked
on the tree listing first: <= 100 files, <= 2 MB, no symlinks/submodules/native binaries) and require an allowed
license: skill-level `license` frontmatter, else a `LICENSE` file in the skill, else the repository license; allowed
SPDX ids: MIT, Apache-2.0, BSD-2-Clause, BSD-3-Clause, ISC, CC0-1.0, CC-BY-4.0, Unlicense, MPL-2.0.
A frontmatter license such as "Complete terms in LICENSE.txt" is refused (not an SPDX id). Only public repositories
are supported; `COMPANY_OS_GITHUB_TOKEN` (temporary, until the M3b credential store) raises rate limits and enables
code search, and is sent only to `api.github.com`.

Audit actions: `skill.installed|updated|quarantined|unquarantined|archived`, `skill_library.seeded|seed_requested`,
`agent.skills_changed`, `role_template.created`, `hire.created|requested`, `task.assigned`. Company events:
`skill.installed|updated|quarantined|archived`, `skill_library.seeded`, `agent.skills_changed`,
`role_template.created`, `hire.created|requested`.

## Agent API additions (`/api/agent/v1`, run token)

Roles are enforced in the services, so the same checks apply if an agent token calls the owner API.

| Method & path | Who | Notes |
|---|---|---|
| `GET /agents` | any agent | colleague search: `?skill_tags=a,b&family=&status=` (default `status=active`) -> `{items: [{id, name, role_title, role_family, role_template, kind, status, department_id, skills, skill_tags, open_tasks}], next_cursor: null}` sorted by load. A skill contributes its tags, slug and category to `skill_tags` |
| `GET /agents/{id}/skills` | any agent | as the owner endpoint |
| `GET /skills/catalog` | HR, founder | as the owner endpoint |
| `POST /skills/install` | HR | as the owner endpoint |
| `POST /skills/author` `{slug, name, description, tags, body_markdown, files?: [{path, content}]}` | HR | generates the frontmatter (`name` = slug); source `company_authored`, trust `community`: scanned, quarantined on caution/dangerous. Re-authoring a slug updates it and bumps the patch version. 201 |
| `POST /agents/{id}/skills`, `DELETE /agents/{id}/skills/{ref}` | HR | never to itself (403) |
| `GET /role-templates`, `POST /role-templates` | HR, founder | agent-authored templates cannot include `terminal`/`code_execution` or the `founder`/`hr` families (403) |
| `POST /hires` | HR only | below |
| `POST /hiring-requests` | founder, leads, anyone with reports | below |

`POST /hires` `{role_template, name, role_title?, manager_agent_id, department?, skill_slugs?, model_provider?,
model_name?, monthly_budget_micros?, reason, task_id}` -> 201
`{agent, auto_activated, approval_id, approver, reasons_not_auto_activated, skills, skipped_default_skills}`.
The new agent gets the template's kind, instructions, responsibilities and toolsets (as `tool_permissions`; HR cannot
pass permissions), the template's default skills that exist in the library plus `skill_slugs` (unknown or
non-active explicit skills: 404/409), the founder's `runtime` and (unless given) model, the requested or
`hire_default_monthly_budget_micros` budget, and department by name (created if missing). HR cannot hire into the
`founder`/`hr` templates (403). Refused with `409 limit_exceeded` (`details.limit = max_headcount`) when live
(proposed/active/paused/retiring) headcount already equals `max_headcount`. The hire is **activated immediately** iff
all hold: `autonomy_mode = autonomous`, `auto_activate_hires`, `hire` not in `human_approval_required_for`, and - when
the company has a monthly budget - the budgets of active/paused/retiring agents plus the requested one fit within it.
Otherwise the agent stays `proposed` and an Approval (`kind: hire`, approver derived from policy, `scope:
{action: "activate_agent", agent_id}`, `evidence.not_auto_activated_because` = reason codes
`autonomy_mode_not_autonomous|auto_activate_hires_disabled|hire_requires_human_approval|headcount_limit|
budget_does_not_fit`) is created.

`POST /hiring-requests` `{required_skills[], role_hint?, reason, task_id}` -> `{hiring_task_id, created, status}` (201,
or 200 for the same task + skill set). Creates a `hiring` task assigned to the active HR agent (409 if there is none);
an agent may have at most 5 open hiring requests (409 `limit_exceeded`).

## Gap detection (dispatcher)

On every dispatch tick (and right after a hire is activated or an agent's skills change), for each **backlog** task
with `required_skills`, no assignee and all dependencies done (company active): the best active agent whose skill
capabilities (tags, slugs, categories of its active skills, lowercase) cover all required tags is assigned -
same department as the project lead first, then fewest open tasks, then oldest agent (`task.assigned` audit). If
nobody qualifies, a hiring request for HR is created once per (task, skill set) and the task stays `backlog` with
`blocked_reason: "waiting_for_hire"`. Tasks without `required_skills` are left alone. If the company has no active HR
agent the check is simply retried on later ticks.

## Processes and settings

- `seed_skill_library` jobs (queued at company creation, by the migration backfill, and by `POST /skills/seed`) are
  executed by the worker (`companyos-worker` or the embedded worker); seeding is idempotent and copies skills into the
  content-addressed store `data/skills/blobs/<sha256>/` (shared across companies; hash = sha256 over the sorted
  `path NUL size NUL content` listing).
- Settings: `COMPANY_OS_RUNTIME_DIR` (default `<repo>/runtime`), `COMPANY_OS_RUNTIME_PYTHON` (default
  `<runtime>/.venv/bin/python`), `COMPANY_OS_GITHUB_TOKEN` (temporary), `COMPANY_OS_GITHUB_API_URL`,
  `COMPANY_OS_SKILL_SCAN_TIMEOUT_SECONDS`.
- Materialization (used by the M3b runtime adapter, not an HTTP endpoint):
  `services.skills.materialize_for_agent(db, company_id, agent_id, hermes_home)` makes `<hermes_home>/skills/`
  contain exactly the agent's assigned **active** skills (blob hash re-verified, atomic per-skill replace, everything
  else removed).

## Verified and not verified (Milestone 4)

Verified against real systems: the skills guard wrapper and the full library seed (35 skills, real scanner) against
`runtime/`; GitHub discovery and installs against `anthropics/skills` without a token (trusted repo, caution allowed,
license read from frontmatter; several skills correctly refused for non-SPDX license strings). Not verified: GitHub
code search and authenticated rate limits (needs a token), the `uploaded` source (no endpoint yet), an HR agent
driving the flow on a local model (needs the M3b adapter and the company-toolset additions).


---

# Milestone 3b: company agent runtime, model providers, credentials, owner login

Migration `0004_model_providers_credentials` (run `uv run alembic upgrade head`; the API refuses to start
without the `model_providers` table). New dependency: `cryptography`.

## Authentication (breaking change)

`COMPANY_OS_AUTH_MODE=local` no longer treats unauthenticated requests as the owner. Agent processes run on the same
host, so "comes from localhost" proves nothing.

| Caller | Credential | Principal |
|---|---|---|
| Browser | `cos_owner` cookie (`HttpOnly`, `SameSite=Strict`, 30 days) from `GET /api/auth/local?token=<owner token>` | the local owner |
| Script / CLI | `Authorization: Bearer <contents of data/owner.token>` | the local owner |
| Agent run | `Authorization: Bearer cos_run...` | that agent (owner API rules for agents apply) |
| none / wrong | | `401 unauthenticated`, `details: {"reason": "owner_credential_required", "login_path": "/api/auth/local"}` |

- `data/owner.token` is created at startup (32 random bytes urlsafe, mode 0600, never logged). The login URL is printed to
  the terminal (stderr, only when it is a TTY, or with `COMPANY_OS_PRINT_LOGIN_URL=1`); log files never contain it.
- `GET /api/auth/local?token=<token>`: constant-time compare; `303` to `/` with `Set-Cookie: cos_owner=...; HttpOnly;
  SameSite=Strict; Path=/` (and `Cache-Control: no-store`, `Referrer-Policy: no-referrer`); wrong/missing token: `401`
  (`details.reason = "invalid_login_token"`), no cookie.
- Open without credentials: `GET /api/health`, `/api/openapi.json`, `/api/docs`, `GET /api/auth/local` (the login itself).
- The runtime process never receives the owner token (not in env/argv; the file is outside the file-tool workspace jail).
- CORS now sends `Access-Control-Allow-Credentials` for the configured origins; the dev proxy forwards cookies.

## Credentials (`/api/companies/{cid}/credentials`)

```json
Credential = { "id", "company_id", "name", "kind": "api_key|token|other", "created_by_type", "created_by_id",
               "revoked_at": null, "created_at", "updated_at" }          // metadata only: the value is never returned
```

| Method & path | Who | Notes |
|---|---|---|
| `GET /credentials`, `GET /credentials/{id}` | human members | agents: 403 |
| `POST /credentials` `{name, kind?, value}` | owner/admin | 201 `Credential`; `name` unique among live credentials (409); `value` 1-8192 chars is Fernet-encrypted (key derived from `COMPANY_OS_SECRET_KEY` / `data/secret.key`); audit `credential.created` (name + kind only) |
| `DELETE /credentials/{id}` | owner/admin | revokes (ciphertext wiped, name freed), idempotent; 409 while an enabled provider uses it |

The value is never in a response, log line, audit row, event or RunEvent; validation errors do not echo it. Replacing the
secret key makes stored credentials unreadable (re-enter them).

## Model providers (`/api/companies/{cid}/model-providers`)

```json
ModelProvider = { "id", "company_id", "name", "kind": "ollama|openrouter|anthropic|openai|openai_compatible",
                  "base_url": "http://127.0.0.1:11434", "credential_id": null, "default_model": "qwen2.5:7b",
                  "context_length": 32768, "is_local": true, "enabled": true,
                  "input_price_per_mtok_micros": null, "output_price_per_mtok_micros": null,   // null = unknown price
                  "last_test": ProviderTest | null, "is_company_default": true, "created_at", "updated_at" }
ProviderTest = { "status": "ok|warning|error", "message", "checked_at", "model_available", "tool_calling",
                 "context_length_configured", "context_length_served", "warnings": [], "latency_ms" }
```

| Method & path | Who | Notes |
|---|---|---|
| `GET /model-providers`, `GET /model-providers/{id}` | human members | agents: 403 |
| `POST /model-providers` | owner/admin | `{name, kind, base_url?, credential_id?, default_model, context_length? (8192+, default 32768), is_local?, enabled?, prices?, make_default?}` -> 201. Ollama `base_url` is the server root (a trailing `/v1` is stripped; default `http://127.0.0.1:11434`); URLs with embedded credentials are 422; `openrouter`/`anthropic`/`openai` need a `credential_id` (same company, not revoked, else 404/409); `openai_compatible` needs `base_url`. The first provider (or `make_default`) becomes the company default and equips the founder and HR (see below) |
| `PATCH /model-providers/{id}` | owner/admin | `name, base_url, credential_id, default_model, context_length, is_local, enabled, prices`; `kind` is immutable; clears `last_test`; disabling the default clears it |
| `DELETE /model-providers/{id}` | owner/admin | 204; 409 while a live agent uses it; clears the company default if it was one |
| `POST /model-providers/{id}/test` | owner/admin | returns the provider with `last_test`. **Ollama:** checks the model exists and supports tools (`/api/show` capabilities), sends one tiny chat through `/v1/chat/completions` (what the runtime uses; loads the model) and reads the *served* context from `/api/ps`; when it is smaller than `context_length` the result is `warning` with the fix (`restart Ollama with OLLAMA_CONTEXT_LENGTH=<n>`). **Other kinds:** authenticated model-list call; tool calling is not probed |
| `GET /model-providers/detect` | owner/admin | read-only probe of local Ollama (`?` none; use POST for another URL): `{base_url, reachable, models: [{name, parameter_size, quantization, context_length_native, tool_calling, recommended}], recommended_model, served_context_length, warnings, already_registered_provider_id, registered: null}` |
| `POST /model-providers/detect` `{base_url?, register?: false, model?, context_length?, make_default?: true}` | owner/admin | same probe; with `"register": true` registers Ollama in one call (recommended tool-capable, non-reasoning model unless `model` is given; 409 if none / unreachable, 404 unknown model), runs the provider test and returns it in `registered`. Idempotent per base URL |

Policy gains `default_model_provider_id`, `default_model_name` (must be an enabled provider of the company, else 404) and
`sandbox_backend` (`auto|none|docker`, default `auto`: docker only if the daemon answers). Defaults that follow:
- when a company gets its default provider, its **founder and HR** agents that still have runtime `unassigned` become
  `company_agent` with that provider/model (agents with a runtime or model of their own are left alone);
- `POST /agents` without an explicit `runtime` in a company that has a default model creates `company_agent` agents on it;
  hires (`POST /hires`) inherit the founder's runtime/model, else the company default; `model_provider` on a hire must be
  an enabled provider id of the company (404 otherwise).

## Runtime `company_agent` ("Company Agent (Hermes fork)")

Listed by `GET /api/runtimes`; capabilities `streaming_events, cancel, usage_tokens, usage_cost, artifacts`. Health:
`not_configured` (interpreter/package missing), `unavailable` (`--version` fails or protocol mismatch), else `ok` with the
sandbox state in `detail` (for example `sandbox: none (docker daemon not reachable); terminal and code_execution are never
granted`).

- **Per run:** the worker materializes the agent's assigned active skills into `data/agents/<agent id>/home/skills`, builds the
  run spec (identity, org, task incl. revision feedback, model + credential, toolsets, company tools, limits, run token, workspace
  `data/workspaces/<company>/<run>`), spawns `runtime/.venv/bin/python -m company_agent.run` (own process group, minimal env)
  and writes the spec to its **stdin**. The model key and run token never appear in argv, environment or on disk.
- **Toolsets** = role template defaults  intersect  agent `tool_permissions` (when non-empty)  intersect `runtime_config.toolsets`,
  always plus `company`, minus `terminal`/`code_execution` unless the sandbox is docker. `skills_manage` is never granted
  (HR authors skills through the API). **Company tools** follow the role: workers get
  `get_context, report_progress, submit_artifact, complete_task, update_task, create_task`; founder/leads/reviewers/managers add
  `review_task, record_decision, request_approval, find_colleagues, request_hire` (the founder also `decide_approval`); HR gets the
  HR tools (`search_skills, install_skill, author_skill, assign_skills, list_role_templates, create_role_template, hire_agent`).
  The server enforces the same roles independently.
- `agent.runtime_config` keys: `max_iterations` (1-500, default 30), `toolsets`, `company_tools` (both can only restrict).
- **Events:** the launcher's JSONL events are stored as RunEvents with the launcher's own `seq` (1, 2, ...). Server-written
  `progress` events use seq >= 1,000,000; launcher lines with a duplicate/decreasing seq, a seq >= 1,000,000, an unknown type or
  malformed JSON are dropped (first wins). Secrets (run token, cloud key) are scrubbed from every payload. `usage` and the terminal
  event carry `billing: {cost_micros, cost_kind, note, local}`.
- **Outcome mapping:** `run.completed` -> run `succeeded` (the agent already completed or blocked the task through the API);
  `run.failed` (`max_iterations`, `time_limit`, `task_not_completed`, `model_error`) or a crash -> run `failed` -> the task state
  machine retries (`ready`) until `max_attempts_per_task`, then `failed`; `run.cancelled` -> `cancelled`; a worker timeout ->
  `timed_out`. Configuration problems (no provider, disabled provider, undecryptable credential) fail the run with code `config`.
- **Cancel / timeout:** SIGTERM to the launcher, SIGKILL of its whole process group after `COMPANY_OS_RUNTIME_KILL_GRACE_SECONDS`
  (default 10); stragglers in the group are always killed.
- **Cost:** `ModelProvider.is_local` -> `cost_micros = 0`, `cost_kind = verified` ("no provider charge", stated in the `billing` payload).
  Cloud: provider-reported actual cost -> `verified`; runtime or provider-price-table estimate -> `estimated`; nothing -> `unknown`
  (the ledger then settles the reservation as an estimate). Tokens: `input_tokens` = uncached + cache-read + cache-write prompt tokens.

## Verified and not verified (Milestone 3b)

Verified live (2026-10-24, macOS arm64, Python 3.14 runtime, Ollama 0.40.4 `qwen2.5:7b` on a temporary server with
`OLLAMA_CONTEXT_LENGTH=32768`, backend on a throw-away database/data dir with the embedded worker):
- owner login (`401` without credentials, bearer, cookie, redaction of the login token in uvicorn's access log);
- one-call Ollama registration with the served-context probe (and, against the user's real default Ollama, the 4096-token warning
  with the fix text);
- a hire from the `research-analyst` template and a task executed end to end: ready -> in_progress -> `in_review` (the agent
  completed it through the API; the task has acceptance criteria) -> `done` (owner review), run `succeeded`, 15 stored events
  (seq 1-15), a real artifact, usage 8 722 prompt / 690 output tokens, `cost_kind: verified`, `cost_micros: 0`, 62 s wall time;
- cancel of a running run through the API: SIGTERM, run `cancelled` in 1.0 s, task `blocked` (`run_cancelled`), no leftover process;
- the autonomous hiring chain: a task needing skill `docker` and no assignee -> hiring task for HR -> the local 7B HR agent used
  `company_find_colleagues`, `company_search_skills`, `company_install_skill`, `company_create_role_template` and
  `company_hire_agent` against the real endpoints -> the new employee was auto-activated and the waiting task assigned to it and
  started (HR run: 12 tool calls, 117K prompt tokens, about 4 minutes; the model chose a $5 000 monthly budget for the hire, which
  no policy capped because the company had no monthly budget).

Verified by mocked transports only: cloud providers (OpenRouter, Anthropic, OpenAI, OpenAI-compatible) and their credential flow.
**Not verified:** the docker sandbox (daemon not running; the probe bug that treated a dead daemon as available was found live and
fixed), a Windows/Linux host, Hermes' native OpenRouter/Anthropic base-URL handling, worker hard-kill recovery of an orphaned
launcher (it ends by its own time limit; its run token dies when recovery finalizes the run).


---

# Milestone 5: autonomous operating model

Spec: `docs/specs/m5-autonomous-company.md`. Migration `0005_autonomy` (run `uv run alembic upgrade head`; the API refuses to
start without the `founder_notes` table). Existing companies get the policy defaults below and the built-in schedules; a NULL
`hire_default_monthly_budget_micros` becomes $5.

New tables: `founder_notes`, `note_replies`, `memory_entries`, `alerts`, `wake_requests`. New policy fields (humans only):

| field | default | meaning |
|---|---|---|
| `max_agent_monthly_budget_micros` | 25 000 000 ($25) | cap for any monthly budget an agent-initiated action hands out (hires, agent-set project budgets). Above it: `409 limit_exceeded`, `details.limit = max_agent_monthly_budget_micros` |
| `hire_default_monthly_budget_micros` | 5 000 000 ($5) | budget of a hire when none is requested (NULL = the same $5; clamped to the cap). An agent-proposed colleague (`POST /agents` with a run token) gets it too |
| `max_planning_runs_per_hour` | 6 | planning tasks (wakes and planning schedules) per agent and hour |
| `max_open_tasks` | 200 | open tasks in the company; agent-created work beyond it is `409 limit_exceeded` (`details.limit = max_open_tasks`) |
| `circuit_breaker_failures` | 8 | failed/timed-out runs within 10 minutes that auto-pause the company (0 = off) |

## Founder's notes (owner API, `/api/companies/{cid}`)

```json
FounderNote = { "id", "company_id", "author_user_id", "body", "attachments": ["artifact id"], "status": "new|triaged|archived",
                "triage": { "category": "directive|goal|requirement|idea|feedback|question|context|noise", "summary",
                            "actions": [{ "type", "ref_id", "label" }], "confidence": null } | null,
                "triaged_by_run_id", "pinned", "created_at", "updated_at", "last_reply": NoteReply | null, "reply_count" }
NoteDetail  = FounderNote + { "replies": [NoteReply] }
NoteReply   = { "id", "note_id", "author_type": "human|agent", "author_id", "body", "created_at" }
```
- `POST /notes {body (1-10240 chars), attachments?: [artifact ids of this company]}` -> 201. Owner/admin only (viewers read; agent tokens 403).
  Emits `note.created` and adds a reason to the AI Co-founder's wake request (60 s debounce).
- `GET /notes?status=&category=&pinned=` (newest first), `GET /notes/{id}` -> `NoteDetail`.
- `POST /notes/{id}/replies {body}` -> 201 `NoteReply`; a reply on a triaged note re-opens it (`status: new`) and wakes the Co-founder.
- `PATCH /notes/{id} {pinned?, status?: archived|new|triaged}` (`triaged` needs an existing triage; `new` re-opens and wakes).
- Audit `note.created|replied|updated|triaged` (never the note text). Events `note.created|updated|triaged|replied`
  (payloads carry `note_id`; replies also `author_type`).

## AI Co-founder (owner API)

- `GET /cofounder/summary` -> `CofounderSummary {agent_id, goals[], plan: {summary, updated_at, projects[{id, goal_id, name, status,
  lead_agent_id, tasks_done, tasks_total, open_tasks[]}]}, decisions[], blockers[], alerts[], last_run, wake_pending, unprocessed_notes}`.
  `plan.summary` is the `result_summary` of the Co-founder's last finished planning/routine task.
- `POST /cofounder/wake` (owner/admin) -> `{status: "queued"|"coalesced", task_id}`; 409 if the company is paused or has no founder.
  Event `cofounder.woke` whenever a wake task is created for the Co-founder.

## Memory and alerts (owner API)

```json
MemoryEntry = { "id", "company_id", "kind": "fact|decision|requirement|lesson|research", "title", "body",
                "status": "proposed|verified|rejected|superseded", "source": { "type", "id", "task_id", "label" } | null, "tags": [],
                "applies_to": [], "proposed_by_type", "proposed_by_id", "verified_by_type", "verified_by_id", "verified_at",
                "rejection_reason", "superseded_by_id", "created_at", "updated_at" }
Alert = { "id", "company_id", "severity": "info|warning|critical", "kind", "title", "body", "status": "open|resolved",
          "related_type", "related_id", "created_at", "resolved_at", "resolved_by_type", "resolved_by_id" }
```
- `GET /memory?status=&kind=&q=&tag=` (newest first), `GET /memory/{id}`, `POST /memory {kind, title?, body, tags?, applies_to?}`
  (humans only; verified immediately; identical live entries are reused), `POST /memory/{id}/verify|reject {reason?}`.
  Text that looks like a secret (API keys, tokens, private keys, `password=...`) is `422`. Events `memory.created|updated`.
- `GET /alerts?status=&severity=`, `GET /alerts/{id}`, `POST /alerts/{id}/resolve` (humans). Raised by the platform:
  `circuit_breaker` (critical, resolves itself on resume), `budget_threshold` (80 % warning / 100 % critical, daily and monthly,
  from the ledger), `task_failed`, `loop_guard`. One open alert per dedupe key. Events `alert.raised|resolved`.

## Agent API additions (`/api/agent/v1`, run token; roles enforced in the services)

| Method & path | Who | Notes |
|---|---|---|
| `GET /state` | any agent | role-scoped live state: founder = everything (goals, projects, tasks by status/open/blocked/unassigned, failures 24 h, pending approvals, budget with `cost_note`, headcount, agents with load and skills, hiring requests, decisions, verified memory + proposals to verify, alerts, schedules, unprocessed notes with text); lead = its projects/tasks/reports; HR = team + hiring requests; others = own tasks and project |
| `GET /context` | any agent | now also `memory` (verified entries that apply), `task.project`, `task.subtasks`, dependency/subtask artifacts with text excerpts, `task.review_target` (review / failure-analysis tasks), `policy.role`, `policy.max_open_tasks` |
| `GET /notes?status=new`, `POST /notes/{id}/triage`, `POST /notes/{id}/replies` | AI Co-founder only | triage `{category, summary, actions: [{type: goal\|project\|task\|schedule\|agent\|hire\|decision\|memory\|approval, ref_id}], confidence?, reply?}`: every `ref_id` must be a record of this company (`422`, `details[].loc = [body, actions, i, ref_id]`). `requirement`/`context` notes become verified memory (source = the note, verified by its author) unless the text contains a secret. Note text is shown with secret-looking spans redacted. When a planning run that triaged one note finishes, the goals/projects/tasks/decisions it created are linked to the triage. Identical consecutive replies are not duplicated |
| `POST/PATCH /goals`, `POST/PATCH /projects` | goals: AI Co-founder; projects: AI Co-founder (all fields), the project's lead (`description`, `status` only) | an agent can only name an active agent of kind `lead` as project lead (`409`, with the instruction to hire one); agent-set project budgets are capped by the company monthly budget (else the per-agent cap) |
| `POST/PATCH /schedules` | AI Co-founder (any agent), leads (self and direct reports) | routines only, at least 900 s apart (cron too), at most 40 enabled per company, heartbeats are human-only |
| `POST /escalations {reason, task_id?, block_task?}` | anyone but the Co-founder | event `escalation.raised` + wakes the Co-founder |
| `POST /help-requests {message, task_id?}` | anyone | a task for the requester's manager (else the Co-founder), at most 3 open, idempotent per (task, message) |
| `POST /tasks/{id}/retry`, `POST /tasks/{id}/cancel` `{reason?, assignee_agent_id?}` | founder, project lead, task creator, manager of the assignee | retry = failed -> backlog, attempts reset |
| `POST /memory`, `POST /memory/{id}/verify|reject` | propose: any agent (proposed only, 20 open per agent); verify: AI Co-founder for others' proposals, never its own | |

Task creation rules for agents (`POST /tasks`): specialists and HR may only create tasks for themselves (assignee defaults to
self; `403` otherwise - ask with `help-requests`); leads only inside projects they lead (the single led project is the default) or as
subtasks of their own tasks; agent-created tasks are always kind `work`, inherit the parent's project, and a planning/routine/review/
hiring run is **not** the default parent. A task nobody owns in a project with a lead goes to the lead (created by the lead: to its
least loaded report); tasks with `required_skills` are left to skill-gap detection. Loop guard: creating an identical open task
(same normalized title, project and assignee) returns the existing one (`200`). `max_open_tasks` and the per-run cap apply.
A manager can hand its own running task to a colleague with `PATCH /tasks/{id} {assignee_agent_id}`.

## Orchestration (control plane)

- **Wake queue** (`wake_requests`): events add a reason to one pending request per agent - Founder's notes (60 s debounce), goal
  created/changed, project paused/ended, escalation, new hire (wakes the hire's manager), approval decided (wakes the requester), alert
  raised, project assigned (wakes the lead), all work of a project finished (wakes the lead, else the Co-founder), task without owner.
  An agent never wakes itself for its own change. The scheduler turns due requests into one `planning` task per agent: merged into a
  wake task that has not started, deferred while one runs, held back by `max_planning_runs_per_hour`, never created while the company
  is paused. Idempotency key `wake:<request id>`.
- **Subtask waiting**: completing a task that still has open subtasks parks it as `backlog` + `blocked_reason: waiting_on_subtasks`
  (attempt refunded); it runs again when every subtask is closed (done, failed or cancelled) to integrate their results (at most 6 runs).
- **Reviews as tasks**: entering `in_review` creates one `review` task (key `review:<task>:<revision>`) for the reviewer (explicit, a QA
  agent for code deliverables, else the assignee's manager). Recording the review - by anyone, also with the review task's own id - closes
  it (completed if running, cancelled if never started). Revision limits are `policy.max_revisions`.
- **Failure analysis**: a task that fails for good (attempts or revisions exhausted) creates a `review` task (key `failure:<task>:<runs>`)
  for the project lead, else the assignee's manager, else the Co-founder, with the error and the options (retry, split, give up), plus a
  `task_failed` alert.
- **Project lead gap**: a project still without a lead after 120 s gets the least loaded active lead or, if none exists, a hiring task for HR
  (`hiring:lead:<project>`); activating a lead hands it every project waiting for one; the hiring task is cancelled once the project has a lead.
  Hiring requests for the same skill set are deduplicated, and a need HR already hired for is not hired twice (waiting tasks go to the hire).
- **Supervised mode**: tasks that managers (founder, leads, managers) create are held (`blocked_reason: awaiting_plan_approval`) behind one
  human approval per run (`kind: other`, `subject_type: agent_plan`); approving releases them, rejecting cancels them.
- **Circuit breaker / alerts**: see `circuit_breaker_failures`; the auto-pause is audited as `company.auto_paused`.

## Built-in schedules

Created with every company (and by the migration): AI Co-founder `heartbeat` (1800 s; follows `policy.heartbeat_interval_seconds` when a human
changes it), `daily-standup` (09:00), `weekly-strategy-review` (Mon 09:30), `daily-budget-review` (18:00), HR `skills-review` (monthly),
a lead's `project-health-check: <project>` (6 h, one per open project with a lead, follows the lead, disabled when the project ends), and an
operations agent's `failed-run-triage` (daily). `task_template.skip_when_idle` makes a due schedule fire without creating a task (audited as
skipped) while there is nothing to review; planning schedules respect the hourly planning limit.

## Role toolkits (`company_tools`, server-side per role; the runtime registers 30 company tools)

Specialist: `get_context, report_progress, submit_artifact, complete_task, update_task, create_task, request_help, propose_memory`.
Lead: `get_state, get_context, report_progress, submit_artifact, complete_task, update_task, create_task, review_task, record_decision,
find_colleagues, request_hire, manage_project, escalate, propose_memory`. AI Co-founder: `get_state, list_notes, triage_note, reply_to_note,
manage_goal, manage_project, manage_schedule, create_task, update_task, complete_task, review_task, record_decision, request_hire,
request_approval, decide_approval, verify_memory, propose_memory, submit_artifact`. HR: the M4 tools + `propose_memory`. Other managers keep
the M4 manager set. New built-in role templates: `project-lead`, `marketing-lead`, `engineering-lead` (kind `lead`); the founder template's
toolsets are `company, todo`.

Other M5 rules worth knowing: triaging a `directive` or `goal` note is refused (`409`, `details.code = act_first`) until the Co-founder created
something in the same run (goal, project, task, schedule, decision or hire request); runs of agents on a **local** model reserve 0 micro-USD
(they cost nothing, so a project budget of 0 no longer blocks them - found in the live run); a "new hire" wake tells the founder there is
nothing to onboard and tells a lead to use the hire.
