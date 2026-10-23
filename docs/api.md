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
company answers 404. A request without a token is the local owner (see "Known limits" below).

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

- Local auth mode has no authentication: a process that omits its run token is treated as the owner. Real
  isolation of agents from the owner API needs M8 auth plus the runtime sandbox (M3).
- Only the `scripted` test runtime can execute tasks; the Hermes adapter reports health but `supports_execution` is
  false, so tasks assigned to it wait ("waiting for runtime"). Nothing here was exercised against a real model.
- The SSE cursor is the outbox row id. On SQLite writers are serialized so ids commit in order; on Postgres a
  slow transaction could in principle commit a lower id after a higher one was already delivered.
