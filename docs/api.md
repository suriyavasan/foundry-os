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


---

# Milestone 6: guardrails & governance (incl. finance)

Spec: `docs/specs/m6-guardrails-and-governance.md`. Migration `0006_guardrails` (run `uv run alembic upgrade head`; the API
refuses to start without the `guardrail_rules` table). **Defaults live in code, not in the database** (`app/guardrails/rules.py`):
a company has no `guardrail_rules` rows until a human adds an override, so existing companies get the spec defaults the moment this code
runs and the migration needs no backfill. New tables: `guardrail_rules`, `guardrail_events`, `rate_limit_hits`, `system_state`,
`external_expenses`, `budget_alert_marks`; new columns on `company_policies`, `approvals` (`tier`, `action`, `reviewer_agent_id`,
`outbound`, `dlp_findings`), `task_runs` (`routing_reason`, `model_provider_id`, `model_used`) and `credentials` (`provider`,
`allowed_actions`, `allowed_families`).

## Risk tiers, the registry and the default table

Every agent-API endpoint, every runtime tool and every control-plane provider action is registered with one of nine tiers
(`app/guardrails/registry.py`, `app/guardrails/tools.json`). **Fail closed:** an unregistered endpoint, tool or action has no tier and is
denied (`403 guardrail_denied`, logged as tier `destructive`); `tests/test_guardrails_registry.py` enumerates every `/api/agent/v1` route
from the OpenAPI document and fails when one is missing. Default decision per tier:

| tier | examples (registered actions) | default |
|---|---|---|
| `read` | `context.read`, `state.read`, `colleagues.search`, runtime `read_file`, `skills_list` | allow |
| `internal_write` | `task.create/update/complete`, `artifact.submit`, `progress.report`, `memory.propose`, `peer_review.decide`, `write_file` | allow |
| `org_change` | `hire.create`, `hire.request`, `skills.install/author/assign`, `role_templates.create`, `goal/project/schedule.*` | allow (M4 headcount/budget rules still apply) |
| `external_read` | `skills.catalog`, runtime `web_search`, provider `email.read`, `github.read` | allow, rate limited |
| `external_communication` | provider `email.send`, `social.post`, `github.issue.comment`, `github.pr.comment`, `invite.send`; platform (system) `github.pr.open` | **peer_review**, then allow (the system actions of a bound repository default to allow: consent is the repository's `merge_mode`) |
| `external_commitment` | `terms.accept`, `account.register`, `domain.register` | human_approval |
| `spend` | `purchase.make`, `subscription.start`, platform `model.paid_fallback` | human_approval above `auto_spend_limit_micros` (default 0), else cofounder_approval |
| `identity` | `mailbox.create`, `ui.deploy`, `credential.rotate`, `dns.change` | cofounder_approval; human_approval for `dns.change` and `domain.register` (domain/DNS ownership) |
| `destructive` | `resource.delete` | human_approval |

## Rules and precedence

`guardrail_rules` are company-scoped and **editable by humans only** (owner/admin; viewers read; agents - run token or not - get `403`).

```json
GuardrailRule = { "id", "company_id", "tier", "action": "email.send" | null, "scope_type": "all|family|agent",
                  "scope_value": "<role family>|<agent id>" | null, "decision": "allow|peer_review|cofounder_approval|human_approval|deny",
                  "conditions": { "max_amount_micros"?, "max_per_day"?, "allowed_domains"? }, "created_by_type", "created_by_id",
                  "created_at", "updated_at" }
```
- `GET /guardrails/rules` (page), `POST /guardrails/rules` (201), `GET|PATCH|DELETE /guardrails/rules/{id}` (`DELETE` -> 204).
  `422` for an unknown action, an action of another tier, a family that does not exist, a `scope_value` with scope `all`, unknown
  condition keys; `404` for an agent of another company. Audit `guardrail_rule.created|updated|deleted`; event `guardrail.rule_changed`.
- **Resolution** (the winner among the rules that apply): (1) a rule for the **specific action** beats a tier rule, (2) then **agent**
  beats **family** beats **all**, (3) then the **newest** (`created_at`, then id) wins a tie; no applicable rule -> the default table.
  Conditions decide whether a rule applies at all: `max_amount_micros` - while the action's `amount_micros` is <= it; `max_per_day` -
  while *fewer than N* such actions of this agent were allowed today (company timezone); `allowed_domains` - for actions whose target
  host / e-mail domain is on the list (it is also the DLP allowlist). A rule whose condition fails is skipped, so "allow up to $1, then
  ask a human" is a rule plus the default.
- `GET /agents/{id}/effective-permissions` -> `EffectivePermissions {agent_id, tiers: [{tier, decision, source: default|rule, rule_id}],
  tools: [{name, tier, decision, rule_id}], rate_limits: [RateLimitStatus]}` (the Permissions tab).

## `guardrails.check` and the agent API gate

`app/guardrails/engine.check(db, ctx, action, payload)` is the one decision point. Instead of a call inside each service, every
`/api/agent/v1` handler sits behind a router-level dependency (`app/api/guard.py`) that runs first: registry lookup (fail closed) ->
emergency stop (`503 emergency_stop`) -> `api_calls_per_min` limit -> (provider actions: credential scope + executor pre-check) -> `check`.
`check` either returns (the action may run) or raises, after committing its `guardrail_events` row:

| outcome | HTTP | body |
|---|---|---|
| unregistered / `deny` rule / outbound DLP | 403 | `{"error": {"code": "guardrail_denied", "message", "details": {"action", "tier", "rule_id", "reason"?, "dlp_findings"?}}}` |
| rate limit | 429 | `code: rate_limited`, `details: {key, scope: agent|company, limit, used, window_seconds, retry_after_seconds}` |
| waits for a review / approval | **202** | `{"status": "pending_approval", "message", "approval_id", "approver": "peer|founder|human", "decision", "reviewer_agent_id", "tier", "action", "rule_id"}` (not an error envelope) |
| emergency stop | 503 | `code: emergency_stop` |

**Approvals are bound to the exact action.** The scope is `{v, action, agent_id, payload_digest}` (the digest covers the whole request body
and path parameters except `idempotency_key`); `approvals.consume` checks its hash and spends it once. The agent is woken when the approval
is decided and repeats the **identical** call: the engine finds the approved, unconsumed approval with that hash and consumes it. The same
content by another agent, other content by the same agent, or the same content a second time each need a new approval. While one is pending
the same call returns the same approval (no duplicates); a rejected / changes-requested approval makes the identical content `403` for 24 h.
A rate-limit hit is recorded only when the action actually succeeded (a request that fails validation costs no quota).
`cofounder_approval` for the Co-founder's own action, and `peer_review` when no independent reviewer exists, escalate to `human_approval`.

`guardrail_events`: `GET /guardrails/events?decision=&tier=&agent_id=&limit=&cursor=` (newest first, humans only) -> `GuardrailEvent {id,
company_id, created_at, agent_id, run_id, task_id, action, tier, decision (allow|peer_review|cofounder_approval|human_approval|deny|
rate_limited), rule_id, approval_id, payload_digest, reason}`. **A digest, never the payload.** Allowed `read`-tier calls are not logged (a
run makes dozens); everything else is. Each row is mirrored as the stream event `guardrail.event`.

## Peer review (maker-checker)

`peer_review` creates an approval with `approver: "peer"` and a `review` task for the reviewer: the author's manager, else a QA agent, else a
marketing ("brand") agent, else the AI Co-founder, else any other active non-HR agent - **never the author**; nobody independent -> a human
decides. The approval carries `tier`, `action`, `reviewer_agent_id`, `outbound {channel, to, subject, body}` (the exact content) and
`dlp_findings [{kind, severity, description, count}]` (never a matched value). The reviewer's run spec carries the same plus a checklist
and the company constraints (`spec.task.peer_review`); its toolset is just `company_decide_peer_review` + `company_complete_task`.
`POST /peer-reviews/{approval_id}/decide {decision: approve|reject|request_changes, note?}` (agent API, only the assigned reviewer; `403`
for the author or anyone else; humans can decide via `POST /approvals/{id}/approve|reject|request_changes`). Deciding closes the review task
and wakes the author. `GET /approvals?tier=` filters by tier; `Approval.approver` is now `human|founder|peer`.

## Outbound DLP, untrusted content, disclosure

- **Outbound DLP** (`app/guardrails/dlp.py`) scans every `external_communication` payload before anything is parked or sent: credential
  shapes (`sk-`, GitHub/AWS/Slack/Stripe/Google keys, private-key blocks, JWTs, `password = ...`), the company's **own secrets** (run tokens,
  the owner token, every stored credential value), e-mail addresses and phone numbers beyond the allowlist (the recipients plus the rule's
  `allowed_domains`), and verbatim passages of **verified memory tagged `internal`** (also `internal-only`, `confidential`, `private`).
  Findings block the action (`403`, `details.reason = dlp_blocked`, `details.dlp_findings`) - a revision request: remove it and submit
  again. Findings never contain the matched text, and neither do events, audit rows or approvals.
- **Untrusted content** (runtime): results of web/file/command tools and other agents' artifact excerpts (`company_get_context`, review
  targets, the draft under peer review) are wrapped in `<untrusted source="...">`; the system prompt calls such text data. Injection phrases
  (instruction override, role change, prompt/secret exfiltration, tool directives, permission changes, chat-template markup) are flagged:
  a `[guardrail]` progress event and `POST /guardrails/flags` (a `guardrail_events` row `content.injection_flag`) - never auto-blocked.
- **AI disclosure**: `email.send` provider calls get a footer (`policy.ai_disclosure_enabled`, default on; `ai_disclosure_text`, `{company}`
  is replaced) appended by the control plane before the executor runs.

## Credentials: scoped grants and control-plane provider actions

Agents never hold a credential. `Credential` gains `provider`, `allowed_actions` (exact actions or `prefix.*`; **empty = no agent may use it**)
and `allowed_families` (empty = any family) - set on `POST /credentials` or `PATCH /credentials/{id}` (humans only); `POST
/credentials/{id}/rotate {value}` replaces the secret in place (audited); revocation exists since M3b. `GET /context` lists the credentials
usable by the calling agent as `credentials: [{id, name, provider, allowed_actions}]` (metadata only).
`POST /api/agent/v1/provider-actions {credential_id, action, params}` -> checks the credential scope for the agent's role family (`403`),
that an executor is connected (**`501 not_available` with `details {available: false, milestone: "M8"}`** otherwise - nothing is faked and
nothing is parked for review), runs the guardrail engine, reveals the secret inside the control plane for the one call, hands it to the
executor and returns `{status: "executed", action, result}` with the secret scrubbed from the result (audit `provider_action.executed`:
action, provider, payload digest). The executor registry (`app.state.provider_executors`) is empty until M8 registers real providers; the
tests register a recording executor. The tool `company_provider_action` is offered only to agents with a usable credential.

## Rate limits

DB-backed sliding windows (`rate_limit_hits`; a hit exactly one window old is outside the window), per agent and per company, defaults in
code (`app/guardrails/ratelimit.py`):

| key | window | per agent | per company | counted for |
|---|---|---|---|---|
| `api_calls_per_min` | 60 s | 120 | 600 | every agent-API call |
| `tasks_created_per_hour` | 1 h | 30 | 150 | `task.create` |
| `external_communications_per_day` | 1 day | 10 | 40 | `external_communication` actions |
| `hires_per_day` | 1 day | 10 | 20 | `hire.create` |
| `skill_installs_per_day` | 1 day | 10 | 30 | `skills.install` |
| `web_fetches_per_hour` | 1 h | 120 | 600 | runtime `web_search` (asked through `POST /guardrails/check-tool`) |
| `purchase_requests_per_day` | 1 day | 5 | 20 | `purchase.request` (M15) |

`GET /guardrails/rate-limits[?agent_id=]` -> `{items: [RateLimitStatus {agent_id|null, key, limit, used, window_seconds, resets_at}],
next_cursor}` (company windows, plus the windows of the given agent or of every agent active in a window). A third refusal of the same
agent and key within ten minutes raises a `rate_limit` alert. Agent-API endpoints for the runtime: `POST /guardrails/check-tool {tool}` ->
`{allowed, tool, tier}` or the errors above (unknown tool: `403`); `POST /guardrails/flags {tool, source?, categories[]}` -> 201.

## Containment

- **Stall detection**: a run with no event or progress for `policy.stall_seconds` (default 180, 30-3600) is cancelled; status `failed`,
  `exit_reason` and `error.code` `stalled` (counts toward the circuit breaker).
- **Runaway caps** per run (`policy.max_tool_calls_per_run` 300, `max_artifacts_per_run` 50, `max_tokens_per_run` 3 000 000; tasks:
  `max_tasks_per_planning_cycle` plus a cancel after 3 refused `company_create_task` calls): `failed` with `exit_reason` =
  `runaway_tool_calls|runaway_artifacts|runaway_tokens|runaway_tasks_created`.
- **Emergency stop** (owner only; persists in `system_state`): `POST /api/system/emergency-stop {reason?}` -> `EmergencyStopStatus {active,
  activated_at, activated_by, reason, runs_cancelled}`; cancels every live run in **every company** (`exit_reason: emergency_stop`, tasks
  go back to `ready` with the attempt refunded), disables dispatch, schedules, wakes and run claiming, and makes the agent API answer
  `503 emergency_stop`. `POST /api/system/resume` lifts it. `GET /api/system/status` -> `{emergency_stop: EmergencyStopStatus, version}`.
  Humans who own at least one company only: agents (run tokens included) and viewers get `403` on all three. Audit
  `system.emergency_stop|resumed` in every company; events `system.emergency_stop` / `system.resumed`. (`companyos stop --all` arrives
  with the CLI in M9.)

## Finance

- **Mid-run budget checkpoints**: every `usage` event with a measured cost compares it with the run's reservation. If it is higher, the
  reservation is extended (by headroom, else by just what was spent) when every task/project/agent/company budget still fits (audit
  `budget.reservation_extended`); if not, the run is cancelled with `exit_reason: budget_exhausted` and billed what it measured (cost kind
  as reported); its task returns to `ready` and the dispatcher parks it as `blocked` / `budget_exceeded` with `retry_after`.
- **Model routing** (`policy.model_routing`, default `free_first`; `allow_paid_fallback`, default true; company-agent runtime only):
  `free_first` uses the agent's own model if free, else the first enabled **free** provider (local, or priced at zero; the company default
  first), else the paid one - which, with `allow_paid_fallback: false`, is the `spend`-tier action `model.paid_fallback` (payload: task,
  provider, model, `amount_micros` = the reservation): the task is `blocked` with `error.code = paid_model_approval` and `error.approval_id`
  until the approval (human above `auto_spend_limit_micros`, else the Co-founder) is decided; approving releases the task and the next
  dispatch spends the approval (single use, per task). `pinned` = the agent's own model; `quality_first` = the agent's own paid model, else
  the paid provider with the highest output price (the only quality signal available), else free. The decision is stored on the run
  (`TaskRun.routing_reason`, `model_provider_id`, `model_used`) and a free route reserves 0.
- **Threshold alerts**: `policy.budget_alert_thresholds` (default `[50, 80, 100]`) for company daily/monthly and every agent daily/monthly
  budget; one `budget_threshold` alert (the highest newly crossed threshold) per (scope, period, threshold), remembered in
  `budget_alert_marks` so resolving an alert does not re-fire it within the period.
- **Reporting** (`/companies/{cid}/finance`, members read, agents `403`; `from`/`to` are `YYYY-MM-DD`, **inclusive, company timezone**,
  default month-to-date, at most 366 days, echoed back with `timezone`):
  `GET /summary` -> `FinanceSummary {from, to, timezone, totals {verified_micros, estimated_micros, unknown_micros, input_tokens,
  output_tokens, runs}, wasted {split + runs}` (failed, cancelled, timed-out runs)`, external_expenses_micros, budgets [BudgetStatus {scope,
  agent_id, period, limit_micros, verified_micros, estimated_micros}]}`; `GET /breakdown?group_by=agent|project|provider|model|runtime` ->
  `{group_by, from, to, items [{key, label, <split>, input_tokens, output_tokens, runs}]}` (costliest first, `unassigned` for none);
  `GET /trend` -> `{from, to, points [{date, <split>}]}` (one point per local day, zero-filled); `GET /runs` (page, newest finished first)
  -> `FinanceRun {run_id, task_id, task_title, agent_id, status, started_at, finished_at, cost_micros, cost_kind, model, provider}`.
- **External expenses**: `GET /finance/expenses?from=&to=` (page; recurring ones are listed when an occurrence falls in the range),
  `POST /finance/expenses {vendor, description?, amount_micros, recurrence: one_time|monthly|yearly, project_id?, incurred_on}` (humans
  only; audit + event `expense.created`). Reported separately as `external_expenses_micros` (monthly/yearly expenses count once per
  occurrence in the range, day clamped to the month's length); they never count against model budgets.

## Policy fields (humans only; agents `403`)

`max_agent_monthly_budget_micros` (M5), `hire_default_monthly_budget_micros` (M5), `budget_alert_thresholds` `[50,80,100]`, `model_routing`
`free_first`, `allow_paid_fallback` `true`, `auto_spend_limit_micros` `0`, `stall_seconds` `180`; additionally `max_tool_calls_per_run`,
`max_artifacts_per_run`, `max_tokens_per_run`, `ai_disclosure_enabled` `true`, `ai_disclosure_text` `null`.

## Stream events

`guardrail.event`, `guardrail.rule_changed`, `system.emergency_stop`, `system.resumed`, `expense.created` (plus the existing
`approval.created` which now carries `tier`, and `alert.raised` for threshold / rate-limit alerts).

## Tools, sandbox network and what is not built

- The runtime registers **32** company tools: M6 adds `company_decide_peer_review` (only in a peer-review run) and
  `company_provider_action` (only for agents with a usable credential). The run spec carries a new `guardrails` block
  (`tool_tiers`, `server_checked_tiers`, `untrusted_tools`, `flag_injection`) and `task.peer_review`.
- **Network egress with the Docker sandbox**: the sandbox already runs with the network off (`sandbox.network: false`), which is "default
  deny". Per-role domain allowlists are **not built** (they need a running Docker daemon to verify); until then no sandboxed tool reaches
  the network at all, and the only outside-reaching tool, `web_search`, goes through `check-tool`.
- Not built: expiry of pending approvals (`expired` is never set), per-run caps for artifacts "marked for external publication" (no such
  marking exists; only provider actions are DLP-scanned), revocation of credentials when an agent retires (M7 lifecycle), pruning of
  `guardrail_events`, the `companyos stop --all` CLI (M9).

## Deviations from the spec and the web assumptions

- The gate is a router-level dependency over the whole agent API (a registry-backed test proves coverage) rather than a call in each
  service; services that need their own decision call `guardrails.check` directly (the dispatcher does for `model.paid_fallback`).
- `max_per_day` means "applies while *fewer than N* were allowed today" (the web hint says "at most N happened"); `GET /system/status` nests
  the status under `emergency_stop` (the web accepts both); `GET /guardrails/rate-limits` answers `{items, next_cursor}`.
- A stalled run is `failed` (not `cancelled`) so that it counts toward the circuit breaker; a budget-exhausted run is `cancelled`.
- Allowed `read`-tier calls are not written to `guardrail_events`; rate-limit hits are counted for actions that succeeded.
- Default `model_routing` is `free_first`: a company that has both a free (local) and a paid provider now runs its company-agent runs on
  the free one unless it sets `pinned`.

## Verified and not verified

Verified (2026-10-24, macOS arm64, Python 3.14): the full backend and runtime test suites; the generated OpenAPI document typechecks
against `web/src/api/contract.check.ts` with every M6 check switched on; a live run on a temporary instance (own SQLite file, port 8705)
with the real launcher, real Hermes and a local `qwen2.5:7b` on a temporary Ollama (port 11435): free-first routing recorded on the run,
`web_search` asked through `check-tool` (events + rate-limit windows), its result tagged `<untrusted>`, a `deny` rule for `tool.web_search`
blocking the tool with the rule id in the model's context, and the emergency stop cancelling the live run in 0.6 s, holding dispatch and
resuming it.

**Not** verified against a real system: any paid model provider (checkpoints, extension and the paid-fallback approval are exercised
with the scripted runtime and a fake `company_agent` adapter), a real stall of a hung model, a connected provider (no executor exists
before M8: the provider-action framework is exercised with a recording test executor), a Docker sandbox, Postgres (SQL is portable and
SQLite-tested), concurrent budget extensions under several worker processes.

# Milestone 7: accounts, login and team

Migration `0007_accounts` (run `uv run alembic upgrade head`; the API refuses to start without the `auth_sessions` table). New
dependencies: `argon2-cffi`, `pyotp`. `COMPANY_OS_AUTH_MODE` is now `password` (default) or `local` (dev only, see below).

## Who is calling (resolution order)

A request that carries an `Authorization: Bearer` header is judged on it alone; otherwise the cookies decide.

| Credential | Principal | CSRF | Notes |
|---|---|---|---|
| `Bearer cos_run.…` | that agent | no | unchanged; agents are refused by every account/team route (see "Agents never...") |
| `Bearer cos_pat_…` | the token's user | no | personal API token, scoped `read` / `write` / `admin` |
| `Bearer <data/owner.token>` | the installation owner (the claimed `is_local_owner` user) | no | bootstrap / CLI fallback; valid in every mode |
| cookie `cos_session` (+ `cos_csrf`) | the session's user | **yes** on POST/PUT/PATCH/DELETE | HttpOnly, `SameSite=Lax`, `Secure` over https (or `COMPANY_OS_COOKIE_SECURE=1`), 30 d max-age |
| cookie `cos_owner` | the installation owner | no (`SameSite=Strict`) | bootstrap only: always in `local` mode, in `password` mode only while the install is unclaimed |
| none / wrong | | | `401 unauthenticated`, `details.reason = "authentication_required"` (`local` mode: `owner_credential_required`) |

Sessions are server-side rows (`auth_sessions`): the cookie value is 256 random bits, only its SHA-256 is stored. Sliding 14 days
idle (`COMPANY_OS_SESSION_IDLE_DAYS`), absolute 30 days (`COMPANY_OS_SESSION_ABSOLUTE_DAYS`), never extended past the absolute cap;
`last_seen`/expiry are written at most once a minute. Revoked, expired and unknown sessions are all `401`.

**CSRF** (signed double submit): `cos_csrf` is readable by the page and equals `HMAC(server key, session hash)`. A state-changing
cookie request must send it back in `X-CSRF-Token`; the header must equal the cookie *and* the value derived from the session, so a
cookie planted from a sibling origin or another session's token is useless. Failure: `403 csrf_failed` (`details.reason = "csrf"`).
`GET /api/auth/session` re-issues the cookie for a valid session (how the web app recovers). Bearer requests, `setup`, `login` and the
anonymous half of `accept` have no CSRF surface.

## Auth endpoints (`/api/auth`)

| Method & path | Body → response | Notes |
|---|---|---|
| `GET /session` (public) | → `AuthSession {state, user, setup_token_required, two_factor_required, expires_at}` | `state`: `setup_required` (the placeholder owner has no password; `setup_token_required` is true unless the request is from loopback or already holds the owner credential) · `unauthenticated` · `authenticated` |
| `POST /setup` | `SetupRequest {username, display_name, password, email?, setup_token?}` → `AuthSession` (+ cookies) | once. **Claims** the existing placeholder owner (same user id, memberships and company); on a database without one it creates the first user. `409 setup_complete` afterwards. Needs the setup token (= `data/owner.token`) unless loopback or owner credential: `403 setup_token_required` / `invalid_setup_token` |
| `POST /login` | `LoginRequest {username, password, totp_code?}` → `AuthSession` (+ cookies) | 2FA account without a code: `200 {state: "unauthenticated", two_factor_required: true}` and no cookie; send again with `totp_code` (6 digits or a recovery code) |
| `POST /logout` | → `204`, cookies cleared | idempotent; a valid session still needs the CSRF header |
| `POST /password` | `PasswordChange {current_password, new_password}` → `204` | revokes every other session; the current password is throttled |
| `GET /sessions` | → `{items: SessionInfo[], next_cursor}` | `current` marks this browser |
| `DELETE /sessions` · `DELETE /sessions/{id}` | → `204` | log out everywhere (cookies cleared) · revoke one (own sessions only, else `404`) |
| `GET /tokens` · `POST /tokens` · `DELETE /tokens/{id}` | `ApiTokenCreate {name, scope}` → `ApiTokenCreated` (`token` once) | `cos_pat_…`; only the SHA-256 is stored; max 25 active per user |
| `POST /2fa/enroll` | → `TwoFactorEnroll {secret, otpauth_uri}` | secret stored encrypted (Fernet); not active until verified |
| `POST /2fa/verify` | `{code}` → `{enabled: true, recovery_codes?}` | the first success enables 2FA and returns 10 one-time recovery codes (only their hashes are stored) |
| `POST /2fa/disable` | `{code, password}` → `204` | a TOTP or recovery code *and* the password |
| `GET /local?token=` | → `303` + `cos_owner` | the M3b-sec owner-token link, unchanged |

- **Passwords**: Argon2id (`argon2-cffi`; 64 MiB, 3 passes, 4 lanes by default, per-hash random salt; parameters are inside the stored
  hash and a weaker hash is upgraded at the next login). Policy: at least 12 characters (at most 256), not a common password (also after
  "leet" and digit padding), no keyboard/alphabet runs or repeated blocks, no username/name/email inside it, a character-diversity
  estimate (≥ 50 bits). Violations are `422` with `details[].loc = ["body", "password"]`; the password is never echoed.
- **Usernames**: 3-32 of `[a-z0-9._-]`, unique; login is case-insensitive. Email is optional (needed for invitations) and unique.
- **Login throttling**: failures are counted per `user:<name>` (3 free) and per `ip:<address>` (9 free), unknown usernames included, then
  back off `2^n` seconds up to `COMPANY_OS_LOGIN_MAX_LOCKOUT_SECONDS` (900). While locked, even the right password gets
  `429 rate_limited` with `details.retry_after_seconds` and a `Retry-After` header. Wrong password, unknown user and wrong code all
  answer `401 invalid_credentials` with the same body, and an Argon2 check always runs, so neither the response nor its timing reveals
  whether an account exists. 2FA codes, the current-password check and the setup token are throttled the same way. TOTP time-steps are
  single use (replays are refused).
- **API token scopes**: `read` = GET only; `write` = every company route except team/policy/secrets/guardrails/system and
  `PATCH /companies/{cid}` (`403 token_scope`); `admin` = all of that, as far as the owner's role allows. No token, whatever its scope,
  can use `/api/auth/*` (tokens, sessions, password, 2FA): `403 token_cannot_manage_account`. A token never exceeds its user's role.
- **`local` mode** (`COMPANY_OS_AUTH_MODE=local`) is dev-only: the app refuses to start when `COMPANY_OS_BIND_HOST` is not a loopback
  address, and answers `403 forbidden` (`details.reason = "local_mode_loopback_only"`) to any request that is not loopback.
  Loopback means: the TCP peer is loopback **and** there are no forwarding headers (`X-Forwarded-For`, `CF-*`, ...) **and** the Host
  header is `localhost`/`127.0.0.1`/`::1`, so a tunnel is never mistaken for the local machine.

## Roles, members and the org tree

Roles per company: `owner`, `admin`, `member` (a "human agent"), `viewer`. Enforced in the services on every mutating route
(`tests/test_accounts_team.py::test_role_matrix_on_every_mutating_route` walks ~35 of them for all four roles):

| | read | tasks/agents/goals/projects/policy/credentials/... | team (invite, edit, remove) | own assigned work | emergency stop |
|---|---|---|---|---|---|
| owner | yes | yes | everything | yes | installation owner only |
| admin | yes | yes | members and viewers only; cannot invite/edit/remove admins or owners | yes | no |
| member | yes | no (403) | no (403) | complete / block / unblock, upload artifacts to it, review and decide what is routed to them | no |
| viewer | yes | no | no | cannot be assigned | no |

Nobody changes their own role or removes themselves; a company always keeps one owner (`409`). Non-members, removed members and
unknown companies are all `404`.

An org reference is `agent:<id>` or `member:<id>` (the `CompanyMember` id, different in every company). It is used by `manager_ref`
(agents and members), `assignee_ref` / `reviewer_ref` (tasks) and `approver_ref` (approvals).

| Endpoint | Notes |
|---|---|
| `GET /companies/{cid}/members` · `GET …/{id}` | `Member {id, company_id, user_id, username, display_name, email, role, title, manager_ref, status, created_at}`; `?include_removed=true`; `email` only for owners/admins (and yourself); agents `403` |
| `PATCH …/members/{id}` | `MemberUpdate {role?, title?, manager_ref?}`; managers are validated (same company, can manage, no cycle across agents and people) |
| `DELETE …/members/{id}` → `204` | status `removed` (row kept). Open tasks go to their manager (agent or person) if still active, else the AI Co-founder, else unassigned; review duty and routed approvals are dropped; agents and people who reported to them move up; when it was their last company, their sessions and API tokens are revoked. History (task assignee refs, audit) is kept |
| `GET/POST …/invitations` · `DELETE …/invitations/{id}` | owner/admin. `InvitationCreate {email, role, title?, manager_ref?}` → `InvitationCreated` = `Invitation` + `delivery: "link"`, `invite_url: "/invite/<token>"`, `token` (once). **No email provider exists before M8, so `delivery` is always `link` and nothing is sent.** Token: 256 random bits, only its SHA-256 stored, single use, 7 days, bound to the email; a re-invite revokes the previous link; status `pending/accepted/expired/revoked` is derived |
| `POST /invitations/{token}/accept` | body `{}` when signed in (the invited email must equal the account's email: `403 email_mismatch`), else `{username, display_name, password}` which creates the account (its email is the invited one; `409 account_exists` if one exists: sign in instead) and signs it in. Unknown, expired, used and revoked tokens are the same `404` |
| `PATCH /agents/{id}` · `POST /agents` | accept `manager_ref` (`member:<id>` makes an agent report to a person; only humans may set that); `Agent.manager_ref` is returned, `manager_agent_id` stays for agent managers |

## Humans on tasks, reviews and approvals

- `POST/PATCH /tasks`: `assignee_ref` and `reviewer_ref` (`agent:` or `member:`); viewers/unknown/foreign members are refused
  (`422`/`404`), one assignee only, nobody reviews their own work. `Task.assignee_ref` / `reviewer_ref` are returned
  (`assignee_agent_id` is null for a person). `GET /tasks?assignee_ref=member:<id>`.
- A person's task follows the normal readiness rules (dependencies, company active) and then waits `ready` for them: **the dispatcher
  never dispatches it and no run is ever created**; the person gets a `task_assigned` notification instead.
- `POST /tasks/{id}/complete` `CompleteTaskRequest {comment?, artifact_ids?}`: the assignee (or an owner/admin on their behalf) completes a
  `ready`/`in_progress` person-task (`409` otherwise, `409` for agent tasks). Same path as an agent's completion: reviewable work goes to
  `in_review`, the rest to `done`, dependents become ready. Blocking uses the existing `POST /tasks/{id}/block|unblock` (assignee allowed).
  A member may upload an artifact to a task assigned to them (`POST /artifacts` with `task_id`).
- Reviews: with `reviewer_ref=member:…` (or an agent manager of the assignee) a task with acceptance criteria goes `in_review` on
  completion; the reviewer gets `review_requested` and uses `POST /tasks/{id}/review`; the assignee cannot review their own work.
- Approvals: `Approval.approver_ref`; `POST /approvals/{id}/route {approver_ref}` (owner/admin) sends a pending *human* approval to one
  member (or back to null = any owner/admin); that member may then approve/reject/request changes; owners/admins always can.
- Agent API: `GET /agent/v1/members` (`members.read`, tier `read`) lists people as colleagues `{ref, name, title, role, manager_ref,
  open_tasks}` (never email/username/credentials; viewers excluded). `POST /agent/v1/tasks` and `PATCH …/tasks/{id}` accept
  `assignee_ref`/`reviewer_ref` for roles that may assign others (AI Co-founder, leads); specialists get `403`. The existing
  `task.create` rate limit applies.

## Notifications (`/api/notifications`, per person)

`GET /notifications?limit&cursor&unread` → `{items: Notification[], unread_count, next_cursor}`; `POST /notifications/read` with `{ids}`
or `{all: true}` → `{marked, unread_count}` (ids of other people are ignored). Kinds: `task_assigned`, `review_requested`,
`approval_needed` (to the routed member, else to owners/admins), `note_replied` (the AI Co-founder answered *your* note), `alert`
(owners/admins). Each creates a `notification.created` event on the company stream carrying only `{notification_id, user_id, kind}`.
Email delivery of notifications arrives with M8.

## Agents never manage accounts

Users, sessions, invitations, tokens, passwords, 2FA, memberships are **human-only**: registry actions `user.create`, `session.create`,
`session.revoke`, `token.create`, `token.revoke`, `password.change`, `twofa.manage`, `invitation.create|revoke|accept`,
`member.update|remove`, `account.manage` (tier `identity`, `human_only`). `guardrails.rules.resolve` denies them before it reads any
rule, no rule can be written for them (`422`), and every account/team route answers a run token with `403 guardrail_denied`
(`details.reason = "human_only"`) and a `guardrail_events` row. The public routes (`setup`, `login`, `accept`) refuse a request that
presents a run token. The route inventory test fails if a new account route lacks the gate.

## Deviations from the spec and the web assumptions

- `GET /auth/sessions` and `GET /auth/tokens` answer `{items, next_cursor}` (the web reads arrays and pages). `/api/me` returns
  `user.email = ""` and `user.username = ""` for an account without them, and `memberships[].member_id`.
- A failed second factor is `401 invalid_credentials` (`details.reason = "invalid_code"`); lockout is `429`, not `423`.
- Invitations: every unusable token is `404` (the web page says it must not distinguish); `invite_url` is relative.
- Added beyond the web contract: `POST /approvals/{id}/route`, `Task.reviewer_ref`, `?include_removed`, `?unread`.
- `owner` cookie and Bearer requests skip CSRF (SameSite=Strict / no ambient credential); only `cos_session` requests need it.
- The instance-wide emergency stop/resume (M6) is now limited to the installation owner (`is_local_owner`): with several accounts, the
  owner of some other company must not be able to cancel everyone's runs.
- Email-on-notification, email delivery of invitations and the daily digest are not built (M8): the UI gets `delivery: "link"`.

## Existing installs (migration and claim)

`0007` flags the placeholder owner (`owner@localhost`, no password) as `is_local_owner`, makes `users.email` optional and adds
`username`, `password_hash`, TOTP columns, `company_members.{title,manager_ref,status,removed_at}`, `agents.manager_member_id`,
`tasks.{assignee,reviewer}_member_id`, `approvals.approver_member_id` and the tables `auth_sessions`, `api_tokens`,
`login_throttles`, `invitations`, `notifications`. Until the first-run setup, `GET /auth/session` reports `setup_required`
and the owner token/cookie keep working. `POST /auth/setup` then sets username/display name/password (and email) **on that user**: its
id, its company memberships and the company stay as they were. The `cos_owner` cookie stops working in `password` mode from then on;
`Authorization: Bearer <owner token>` keeps acting as the claimed account.

## Verified and not verified (Milestone 7)

Verified: the full backend suite; every browser path (setup, login, sessions, CSRF, throttling, tokens, 2FA, invitations, role matrix)
through real cookies and headers; the migration on a database built at revision `0006` (and on a copy of the live database) including
the claim; the generated OpenAPI against `web/src/api/contract.check.ts` with every M7 check switched on; a live smoke on a temporary
instance (own SQLite file, port 8706): setup from loopback → login with cookie + CSRF → invite → accept as a second user → task assigned to
that person → completed through the human endpoint → notifications.

**Not** verified: a real authenticator app (codes are generated with `pyotp` in the tests), a TLS-terminating proxy or tunnel in front
(the `Secure` flag, `X-Forwarded-*` handling), Postgres, email delivery (none exists), concurrent first-run setups from several
processes (the claim is an atomic conditional UPDATE, tested in one process).

---

## Milestone 8 — Reachability & identity

Spec: `docs/specs/m8-reachability-and-identity.md`. Operator guide: `docs/deploy-reachability.md`. Schema names and shapes follow the web
contract (`web/src/api/types.ts` "Milestone 8"); every M8 check in `web/src/api/contract.check.ts` type-checks against this backend's
OpenAPI (verified on a copy of `web/`). **No response carries a secret.** Provider tokens go once to `POST /companies/{cid}/credentials`
(M3b store); a connected account only references the credential id.

### Device level (installation owner only)

"Owner" is the account that claimed the install (`users.is_local_owner`, the emergency-stop rule). Everyone else gets `403`; an agent's run
token gets `403 guardrail_denied` (human-only action `reachability.manage`, with a `guardrail_events` row); a `write`-scoped API token gets
`403` too (`reachability` is admin-only for unsafe methods).

| Method & path | Purpose |
|---|---|
| `GET /reachability` | `ReachabilityStatus`: mode (`local`/`lan`/`public`), device name, hostname, `local_urls`, `lan` (addresses with interface and URL, `<host>.local` mDNS name), `tunnel` (state, URL, health, restarts, `binary`, `providers`, and since M13 `ephemeral`, `last_url`, `url_changed_at`), `active_url`, `public_exposure_blocked_reason` / `_code`, `domain` summary. Polls are local only (no network calls). |
| `PATCH /reachability/lan` `{enabled}` | Persists the bind preference and returns `ReachabilityStatus`. Switching **on** needs the exposure gate. |
| `POST /reachability/tunnel/start` `TunnelConfig {provider, hostname?}` | Starts the supervised tunnel; returns `TunnelStatus` (usually `starting`; poll). Gate enforced. |
| `POST /reachability/tunnel/stop` | Stops it; `TunnelStatus`. |
| `GET /reachability/tunnel/install-plan` *(added)* | Looks up the official cloudflared release and the published SHA-256 for this platform (metadata only; also fills `tunnel.binary.download_*`). |
| `POST /reachability/tunnel/install` `{confirm: true, sha256?}` | Verified install into `data/bin/`. `409 confirmation_required` without `confirm`; `409 checksum_changed` when `sha256` is not the published one; `502` when the checksum is missing/disagrees/does not match the download. |
| `GET /reachability/tunnel/logs` *(added)* | Last 200 lines of the tunnel process, secrets masked. |

**Public-exposure gate** (`public_exposure_blocked_code`): `auth_local` (`COMPANY_OS_AUTH_MODE=local`), `no_password` (the install is not claimed yet),
`weak_password` (the owner's password was not set under the M7 password policy and 2FA is off). Both LAN enabling and every tunnel start (also the
agent actions `tunnel.start`, `hosted_ui.deploy`) are refused with `403` `{details: {reason: "public_exposure_blocked", code}}`. When the gate is open and
2FA is off, `public_exposure_advice = "two_factor_recommended"` (advice, not a block). Switching LAN **off** is always allowed.

**LAN.** The listening socket belongs to the launcher, not the app: `lan.enabled` is true only while the *running process* is bound to a
non-loopback address (`COMPANY_OS_BIND_HOST`, set by `python -m app.serve`); `lan.requested` is the saved preference and
`lan.restart_required` is true while they differ. `python -m app.serve` (and M9's `companyos up`) applies the preference at start and refuses
to bind to the network when the gate is closed. `mode` is derived from facts (a running tunnel, a bound listener), never from the preference.

**Tunnel.** Providers: `cloudflare_quick` (cloudflared `--url`, random `*.trycloudflare.com`, no account), `cloudflare_named` (token of a tunnel
created through the owner's connected Cloudflare account; the hostname must be on a zone that account sees; the CNAME to
`<tunnel-id>.cfargotunnel.com` is created proxied), `tailscale_funnel` (`tailscale funnel <port>` in the foreground; needs the Tailscale daemon
running and a connected `tailscale` account). The supervisor restarts a dead process with backoff (1 s, 2 s, 4 s … capped at 60 s; `restarts`
counts them), gives up with `state: "error"` after 8 consecutive failures, health-checks `<url>/api/health` every 30 s and restarts a tunnel
that fails three checks in a row. A tunnel that was running is resumed at startup (`COMPANY_OS_TUNNEL_AUTORESUME`). The child gets a minimal
environment (never the server's secrets); the named-tunnel token travels in `TUNNEL_TOKEN`, not argv.

**Public URL tracking (M13).** Three fields were added to `TunnelStatus` (all in `GET /reachability` under `tunnel`, and in the body of the start / stop / install answers):

| Field | Type | Meaning |
|---|---|---|
| `ephemeral` | `bool` (default `false`) | `true` when the address changes every time the tunnel starts: provider `cloudflare_quick`, or a `*.trycloudflare.com` host. `false` for `cloudflare_named` and `tailscale_funnel` |
| `last_url` | `string \| null` | The last public URL the tunnel had. Kept across stops and restarts, so a stopped tunnel still shows what the address was. `null` before the first one |
| `url_changed_at` | `datetime \| null` | When `last_url` appeared |

Each time a running tunnel reports a URL different from `last_url`, the server stores it (with the previous one and the last five), writes the audit event
`reachability.tunnel_url_changed` (`data`: `url`, `previous_url`, `provider`) for each company of the installation owner, emits `reachability.updated`, logs `Public URL: <url>`,
and rewrites `<data dir>/public-url.json` (`url`, `provider`, `state`, `changed_at`, `previous_url`; mode 0600; `url` is `null` while the tunnel is stopped, starting or in error).
An unchanged URL records nothing. A stop now clears `url` through the same path. The CLI (`foundryos reach --watch`, `reach public start|stop`, `status`) is built on
`GET /reachability`, `GET /reachability/tunnel/install-plan`, `POST /reachability/tunnel/install`, `POST /reachability/tunnel/start` and `POST /reachability/tunnel/stop`; it adds no endpoints.
`foundryos setup` uses the existing auth, company, model-provider, AI-setup, CLI-runtime and reachability endpoints and adds none either.

### Connected accounts (`/companies/{cid}`, owners/admins)

| Method & path | Purpose |
|---|---|
| `GET /connected-accounts` | Page of `ConnectedAccount {id, provider, label, credential_id, scopes, status (connected/invalid/revoked), last_checked_at, metadata, last_error}`. |
| `POST /connected-accounts` `ConnectedAccountCreate {provider, label, credential_id, extra_credential_ids?, metadata?}` | Connects, **runs the connection test**, and stores the result (`invalid` + `last_error` when it fails). Binds the credential to the provider and to the agent actions the provider enables. |
| `POST /connected-accounts/{id}/test` | `ProviderCheck {ok, message, checked_at, account_label, scopes}`; also updates the account. |
| `DELETE /connected-accounts/{id}` | Revokes (returns the account with `status: "revoked"`): agents lose the provider at once, its credentials are revoked/wiped unless still used elsewhere. |
| `POST /connected-accounts/oauth/start` `{provider}` | `501 not_available` `{reason: "oauth_not_configured"}` for `gmail`/`outlook`: OAuth needs a client registered by the operator; nothing is faked. |
| `GET /connected-accounts/providers` *(added)* | What can be connected: secret fields, metadata fields, the agent actions each enables. |

Providers: `cloudflare` (account, zones, DNS records, tunnels, Email Routing), `vercel` (projects, deployments, domains), `github` (identity, fork/branch/file/PR),
`agentmail` (inboxes, send), `resend` (domains, send), `tailscale` (local CLI; no secret), `porkbun` and `namecheap` (availability, pricing, registration).
Extra secrets are keyed by field name in `extra_credential_ids` (`secret_api_key` for Porkbun; `webhook_secret` for AgentMail/Resend/Cloudflare inbound mail);
they are control-plane only and are never offered to agents. Metadata holds non-secret facts only (a secret-looking key or value is `422`).
Viewers/members get `403`; agents get `403 guardrail_denied` (`connected_account.manage`); a non-member gets `404`.

### Provider actions (agents, `POST /agent/v1/provider-actions`)

An agent never sees a credential: the control plane finds the connected account behind `credential_id`, runs the guardrail engine, then calls the
provider itself. Parameters are validated **before** a review/approval is created (nobody is asked to approve a call that can never run).

| Action | Tier | Default decision | Provider | Notes |
|---|---|---|---|---|
| `dns.read` `{domain, type?}` | external_read | allow | cloudflare | |
| `dns.change` `{domain, changes[]}` | identity (+ domain/DNS ownership) | **human** | cloudflare | `changes[] {type A/AAAA/CNAME/TXT/MX, name, value, ttl?, proxied?}`; NS/SOA never. Plan then apply; only the listed names are touched. |
| `domain.search` `{query}` | external_read | allow | porkbun, namecheap | quotes are `cost_kind: "estimated"` |
| `domain.preview` `{registry_id, name, target?}` | external_read | allow | github | the exact file + PR text |
| `domain.register_pr` `{…preview content}` | external_commitment | **human, cannot be lowered** | github | content must equal what the registry definition generates |
| `domain.purchase` `{domain, years:1, max_price_micros}` | spend | **human, cannot be lowered** | porkbun, namecheap | re-quoted at purchase time; above the ceiling nothing is bought |
| `hosted_ui.deploy` `{proxy_target, project?}` / `hosted_ui.status` | identity / external_read | Co-founder / allow | vercel | needs the exposure gate |
| `tunnel.start` `{provider: cloudflare_quick\|cloudflare_named, hostname?}` / `tunnel.stop` / `tunnel.status` | identity / identity / external_read | Co-founder / Co-founder / allow | cloudflare | gate enforced |
| `mailbox.create` `{local_part, owner_agent_id, display_name?}` | identity | Co-founder | agentmail, resend | |
| `email.send` `{mailbox?, to, subject, body}` | external_communication | peer review | agentmail, resend | DLP, maker-checker, rate limit, AI-disclosure footer; sender must be a mailbox the agent owns |
| `email.read` `{mailbox?, limit?, unread_only?}` | external_read | allow | agentmail, resend | inbound mail, wrapped as `<untrusted source="email">` |

New registry rule (`ActionSpec.human_required`): `domain.register`, `domain.register_pr`, `domain.purchase` resolve to *at least* `human_approval` whatever the
rules say (a `deny` rule is still honoured). The placeholder `ui.deploy` was replaced by `hosted_ui.deploy`. `company_provider_action` results are marked
`untrusted` in the run spec. Approvals created by these actions carry the exact display fields the web renders in `scope` (`file_content`, `pr_title`,
`pr_body`, `repo`, `changes`, `domain`, `max_price_micros`, `address`, `proxy_target` …); the approval is still bound by the payload digest, so the
repeated call must be identical and is single-use.

### Domain

| Method & path | Purpose |
|---|---|
| `GET /domain` | `DomainPlan {domain, path, status, dns_status, tls_status, dns_records, steps, registration, last_checked_at, error}` |
| `GET /domain/registries` / `GET /domain/free-providers` | Pages. Registry: `is-a.dev` (rules, eligibility and warnings as the registry documents them: non-commercial, human review, "do not use AI to generate your request"). Free provider: DigitalPlat FreeDomain (assisted: you register in its dashboard). |
| `GET /domain/search?q=` | `DomainAvailability[]` from the connected registrars; a bare name is checked on `.com .net .org .io` only. `409` without a registrar account. |
| `POST /domain/registrations/preview` `{registry_id, name, target?}` | `DomainRegistrationPreview`: the exact file, branch, PR title/body, who it is published as, eligibility and warnings. |
| `POST /domain/registrations` `DomainRegistrationCreate` | `github_registry` and `paid_registrar` create an **approval for a human** (scope = exact content / price ceiling; `registration.approval_id`); nothing is published or bought until an owner/admin approves it (`/approvals/{id}/approve`), which consumes the approval and then acts. `free_provider` starts the assisted flow (no approval; steps in the plan). One domain at a time (`409 domain_exists`). Years must be 1. |
| `POST /domain/verify` | Follows the PR (merged?), checks DNS (DoH lookups) and TLS (a real handshake) and returns the plan. |
| `POST /domain/dns/plan`, `POST /domain/dns/apply` *(added)* | The Founder changes DNS through the connected Cloudflare account (a human acting directly). |

### Hosted control UI

`GET /hosted-ui` → `HostedUiDeployment | null` (refreshes a `deploying` deployment from Vercel); `POST /hosted-ui/deploy` deploys `web/dist` (setting
`COMPANY_OS_WEB_DIST_DIR`) with a generated `vercel.json` rewriting `/api/:path*` to the public URL (active domain, else the running tunnel) and the SPA
fallback to `/index.html`. A `vercel.json` inside the build is ignored. `409 no_account`, `422` without a public https URL or without a build, `403` without
the exposure gate. The human click is the authorization; the agent path needs the Co-founder's approval.

### E-mail identity

| Method & path | Purpose |
|---|---|
| `GET /email`, `PATCH /email` `{provider?, ai_disclosure_footer?}` | `EmailIdentity`. Providers `agentmail`, `cloudflare_resend` (Resend on the company domain; needs a domain), `gmail`/`outlook` (`501`, OAuth not configured). The footer toggle is the company policy (`ai_disclosure_enabled`), human only. |
| `POST /email/refresh` *(added)* | Re-checks the Resend sending domain and activates waiting mailboxes. |
| `GET /mailboxes`, `POST /mailboxes` `{local_part, owner_agent_id, display_name?}` | Mailboxes (`company@`, `cofounder@`, per agent) mapped to one agent. Created by the Founder directly or by an agent through `mailbox.create`. |
| `POST /api/webhooks/email/{company_id}/{source}` | Inbound mail, **no session**: `agentmail`/`resend` (Svix signature, secret = the account's `webhook_secret`) or `worker` (`X-Company-OS-Signature: sha256=<hmac>` from a Cloudflare Email Worker). Unknown company, no secret, bad/stale signature are all the same `401`; body ≤ 1 MiB (`413`); dedupe by message id. Stored as untrusted, scanned for injection phrases (flag only), the owner agent is woken with a neutral note (no mail text). |
| `GET/PUT /notifications/preferences` *(added)* | `{email_kinds}` opt-in per kind, `email_available`. |

Invitations (M7): `delivery: "email"` when the company has a working identity and a mailbox (the link exists only in that message; `invite_url` and `token` are
`null`), else `"link"` as before; a send failure falls back to `"link"` with `delivery_note`. Notification e-mails are sent after the transaction commits, only for opted-in
kinds, never with credential-shaped content, capped at 200 platform e-mails per company per day (`system_emails_per_day`).

`GET /companies/{cid}/reachability/checklist` *(added)* → the Founder's one-time steps with status (`done`/`todo`/`attention`).

### Events (company stream, invalidate-only)

`reachability.updated`, `tunnel.updated`, `connected_account.updated`, `domain.updated`, `mailbox.updated`, `hosted_ui.updated`.

### Differences from the web assumptions

- `POST /connected-accounts/oauth/start` always answers `501` (no OAuth client ships with the repo). `credential_id` is optional in `ConnectedAccountCreate` (Tailscale has no secret); the web type always sends one, which is accepted.
- `lan.enabled` means *bound*, not *requested* (see LAN above); the UI should show `lan.requested` / `lan.restart_required` after a toggle.
- `DELETE /connected-accounts/{id}` returns the revoked account (200), not 204.
- `DomainRegistrationCreate` accepts one year only; `github_registry` accepts an optional `target`.
- Hosted-UI deploys by a human have no approval (`approval_id` is null); only the agent action is approved.
- Added fields: `lan.{requested,bind_host,restart_required}`, `public_exposure_advice`, `two_factor_enabled`, `DomainRegistration.{cost_kind,max_price_micros}`, `DomainRegistry.warnings`, `Mailbox.error`, `ConnectedAccount.last_error`.

### Verified and not verified (Milestone 8)

Verified: the adapters against scripted HTTP (no network in the test suite); the tunnel supervisor with fake binaries (URL parsing, restart/backoff, give-up, health, stop,
redaction, minimal environment); the cloudflared install against a scripted release (checksum match/mismatch/missing/disagreeing, official-URL guard, no
download without confirmation); tiers, human floors and approval binding per provider action; the exposure gate; webhook signatures; inbound mail as untrusted data;
DLP/footer/rate limits on outbound mail; invitations and notifications by e-mail; the OpenAPI against the web's M8 type checks; a live smoke on a temporary
instance (own SQLite file, port 8707): first-run setup, real LAN address/mDNS detection, the saved LAN preference applied by `python -m app.serve` (reachable
on the machine's LAN IP), the real Tailscale CLI status, cloudflared "install required" state, connect + test of a fake Cloudflare.

**Not** verified against the real systems: any provider API (Cloudflare, Vercel, GitHub, AgentMail, Resend, Porkbun, Namecheap — shapes follow their documentation
and fixtures only; Namecheap's XML and registrant fields least of all), a real cloudflared quick/named tunnel or Tailscale Funnel, the real cloudflared download,
Svix signatures from the real services, the Cloudflare Email Worker, a LAN URL from *another* device, the is-a.dev registry accepting the generated file, TLS
through a real domain, Postgres.

---

## Milestone 9 — Serving, headers and the CLI

No endpoints were added or changed. What changed around them (operator view: `docs/deploy.md`, `docs/security.md`):

- **Same-port UI.** `GET`/`HEAD` for any path outside `/api` is answered from `web/dist` (`COMPANY_OS_WEB_DIST`, `COMPANY_OS_SERVE_WEB`):
  `/assets/*` -> `Cache-Control: public, max-age=31536000, immutable`; other files -> `no-cache` + `ETag`/`Last-Modified` (`If-None-Match` -> 304);
  a path without a file extension that matches no file -> `index.html` (client-side routes); a missing file with an extension -> the usual
  JSON 404. `/api/**` is never answered by this layer, so an unknown API path is still `404 {"error": {"code": "not_found"}}` and
  `POST /c/anything` is not served by the UI. If `index.html` is missing the server behaves exactly as before (UI not built).
- **Headers on every response:** `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`, `X-Frame-Options: DENY`, `Permissions-Policy`,
  `Cross-Origin-Opener-Policy: same-origin`; `Strict-Transport-Security` only for https requests. `/api/**` (except `/api/docs`) also gets
  `Content-Security-Policy: default-src 'none'; frame-ancestors 'none'` (`default-src 'none'; sandbox` on artifact `.../content`), and JSON
  answers without their own `Cache-Control` get `no-store`. The UI's HTML gets the SPA policy documented in `docs/security.md`.
- **Process files.** The API and the worker create `<data dir>/run/<role>.<pid>.json` while they run (used by `backup restore` and `doctor`).
- **Clients.** The `companyos` CLI is an ordinary API client (personal API token, or the owner token on the server machine); its exit codes
  (3 unauthenticated, 4 forbidden, 5 not found, 6 conflict, 7 unreachable) map the HTTP statuses above.

---

## Milestone 10 — AI setup, coding CLIs, Reflections, Command Center

Spec: `docs/specs/m10-ai-setup-cli-reflections.md`. CLI facts and the isolation model: `docs/cli-runtimes.md`. Schema names in the OpenAPI document:
`AiSetupStatus`, `AiSetupUpdate`, `CliRuntimeStatus`, `ProviderTest` (extended), `CommandRequest`, `CommandExchange`, `CommandOutput`, `Reflection`,
`ReflectionDetail`, `ReflectionResponse`, `ReflectionResponseCreate`, `CompanyStandard`, `CompanyStandardCreate`, `CompanyStandardUpdate`.
Migration `0009_ai_setup_cli_reflections` (existing companies get the `daily-reflection` routine and an exchange row per existing Founder's note).

### New error codes (both 409)

| code | when |
|---|---|
| `ai_setup_required` | creating a note, command, task or agent while the company has no working AI (`details.reason` = the blocking reason) |
| `ai_not_ready` | `PUT /ai-setup` with a provider/model/CLI that is not ready (`details.reason` is a blocking reason, or `cli_not_installed` / `cli_not_configured`) |

### The readiness gate (`/api/companies/{cid}/ai-setup`)

`GET` (any member) → `AiSetupStatus`:

```json
{ "ready": false, "blocking_reason": "no_tested_provider",
  "management": { "provider_id": null, "provider_name": null, "kind": null, "model": null, "last_test": null },
  "work_mode": "api", "work_runtime": null, "clis": [CliRuntimeStatus] }
```

`blocking_reason` is `no_tested_provider` | `provider_test_failed` | `no_default_model` | `null`. A company is **ready** when an enabled model provider is the company
default (`policy.default_model_provider_id` + `default_model_name`) and its latest test **for that model** has `ok: true`. Providers tested before M10 carry no
`checks`, so an existing company is not ready until its provider is tested again.

`PUT` (owner/admin) `AiSetupUpdate {management_provider_id, management_model, work_mode: "api"|"hybrid", work_runtime: "cli:claude_code"|"cli:codex"|"cli:opencode"|"cli:antigravity"|null}`
→ `AiSetupStatus`. Makes the provider the company default (equipping the Co-founder and HR), stores the work mode. `hybrid` needs a `cli:*` runtime that is installed and has
a sign-in mode; `api` clears the runtime. 409 `ai_not_ready` otherwise, 404 for a provider of another company, 403 for viewers/members.

**What the gate does while not ready:** tasks stay in `backlog` with `blocked_reason: "ai_setup_required"` (they are released by the next scheduler tick once ready), nothing
is dispatched, the wake queue wakes nobody (requests wait), and creating notes / commands / tasks / agents is 409 `ai_setup_required`. Reads, policy, providers,
credentials, `ai-setup`, auth and team stay available. The gate has no configuration switch.

### Provider test (extended) and model list

`POST /model-providers/{id}/test` (optional body `{model}` tests another model than the provider's default) → `ModelProvider` whose `last_test` is now:

```json
{ "status": "ok|warning|error", "message": "…", "checked_at": "…Z", "tested_at": "…Z", "ok": true, "model": "qwen2.5:7b",
  "checks": [ {"name": "reachable|auth|tool_calling|context|model_exists", "ok": true, "detail": "…"} ],
  "served_context": 32768, "latency_ms": 7093,
  "model_available": true, "tool_calling": true, "context_length_configured": 32768, "context_length_served": 32768, "warnings": [] }
```

`ok` is true only when all five checks pass. `tool_calling` is **really probed** with one tiny request carrying a tool (a few dozen tokens; OpenAI-style `tools`, Anthropic `tool_use`,
Ollama's OpenAI endpoint). `context` needs ≥ 32 768 tokens: the served window for Ollama (`/api/ps`), the provider-declared window for cloud (OpenRouter's list value when it
has one, else `context_length` of the provider). OpenRouter's public model list proves nothing about a key, so `auth` uses its `/auth/key` endpoint. A 4K-window Ollama is now
`status: "error"` (it was a warning before M10). Supported kinds: `openrouter`, `anthropic`, `openai`, `openai_compatible` (any base URL), `ollama`.

`GET /model-providers/{id}/models` (owner/admin) → `{items: [{id, name, context_length|null, tool_calling|null}], next_cursor: null, available: bool, detail: string|null}`.
`available: false` (with `detail`) when the provider offers no readable list; `tool_calling` is known for Ollama only.

### Coding CLIs (`/api/cli-runtimes`, installation owner only; an agent's run token → 403 `guardrail_denied`, a company admin who is not the installation owner → 403)

| Method & path | Purpose |
|---|---|
| `GET /cli-runtimes` → `{items: [CliRuntimeStatus], next_cursor: null}`; `GET /cli-runtimes/{id}` | `id` ∈ `claude_code`, `codex`, `opencode`, `antigravity` (Antigravity CLI, binary `agy`) |
| `POST /cli-runtimes/{id}/install` `{confirm: true}` → `202 CliInstallLog` | runs exactly `install.command` (`npm install -g <package>`) as a supervised child: no shell, no sudo, 15 min limit. `confirm` missing/false → 422 and nothing starts; no npm → 409; already installed → 409; `antigravity` (no npm package; Google ships a `curl \| bash` installer, never run from here) → 409 with the manual command |
| `GET /cli-runtimes/{id}/install-log` → `CliInstallLog` | `{id, status: none|running|succeeded|failed, command, started_at, finished_at, exit_code, lines[≤400]}`, output scrubbed of secret-looking spans |
| `PUT /cli-runtimes/{id}/auth` `{mode: "own_login"|"api_key", credential_id?, model_provider_id?, model?}` → `CliRuntimeStatus` | `mode` must be in the CLI's `auth_modes` (Antigravity CLI: `api_key` only, a Gemini API key; `own_login` → 422). `api_key` needs `credential_id` (a credential of a company you administer; never returned). `model_provider_id` (OpenCode only) points the CLI at a company model provider (endpoint and key). OpenCode needs `model_provider_id` or a `provider/model` name in `model`. Clears `last_test` |
| `POST /cli-runtimes/{id}/test` → `CliRuntimeStatus` | one tiny headless prompt ("Reply with the single word OK.") in a temp dir, 60 s limit, `$0.05` cap where the CLI supports one, no tools / no company tools; stores `auth.last_test` |

`CliRuntimeStatus`: `{id, display_name, installed, binary_path, version, auth_modes: ["own_login","api_key"], auth: {mode, configured, credential_id, last_test: {ok, detail, tested_at}|null, model_provider_id, model},
sandbox: {available, kind: "os"|"docker"|"none", detail}, install: {available, command|null, requires: ["npm"]|[], status, manual|null}, login_hint, warnings[]}`. `install.available` = the CLI has an npm package, npm is present and the CLI is not installed; `install.manual` is a command the person runs themselves (Antigravity CLI), never run by the server.
Interactive logins (`claude` /login, `codex login`, `opencode auth login`) are the person's step in a terminal and are never automated.

### Work modes and routing

`policy.work_mode` / `policy.work_runtime` (set only through `PUT /ai-setup`; `GET /policy` also shows them). The dispatcher chooses the runtime **per run** and writes it to
`run.runtime` with `run.routing_reason` (`hybrid: work task on cli:opencode`, `agent runtime pinned by the owner`, …):

* `api`: the agent's own runtime (usually `company_agent`).
* `hybrid`: `kind=work` tasks of non-management agents run on `work_runtime`. Founder, HR, leads, agents with reports, and every planning / review / hiring / routine task stay on `company_agent`
  with the management model.
* Override (owner only): an agent whose `runtime` is itself `cli:*` always uses it; `runtime_config.pin_runtime: true` keeps an agent on the company agent. An agent can never change either.
* A usable work runtime is required: when the CLI is gone, work waits (`waiting_for_runtime`); it never silently falls back to a paid API model.
* Cost kinds of CLI runs: a local model → `verified` $0; `api_key` with a CLI-reported cost → `estimated`; `api_key` without a cost → the reservation is charged as an `estimated` (the existing rule for
  unknown cost); `own_login` → `unknown` with nothing charged (`subscription`).

### Reflections and standards (`/api/companies/{cid}`; any member reads, owner/admin writes; agents → 403)

| Method & path | Purpose |
|---|---|
| `GET /reflections?status=&limit=&cursor=` → `Page[Reflection]` (newest first) | `Reflection`: `{id, company_id, author_agent_id, author_name, kind: observation|standard_proposal|retro|risk|praise, title, body, evidence: [{type: task|artifact|run|project|agent|goal, id, label}], proposed_standard|null, status: open|endorsed|revised|rejected|adopted|archived, standard_id|null, response_count, last_response: ReflectionResponse|null, created_at, updated_at}` |
| `GET /reflections/{id}` → `ReflectionDetail` | `Reflection` + `responses: [ReflectionResponse]` oldest first |
| `POST /reflections/{id}/responses` `ReflectionResponseCreate {reaction: endorse|challenge|refine|comment, body?}` → `201 ReflectionResponse` | `body` is required except for `endorse`. Returns `{id, reflection_id, author_type, author_id, author_name, reaction, body, created_at, reflection_status, standard_id}`. Wakes the AI Co-founder (5 s debounce, urgent) |
| `POST /reflections/{id}/archive` → `ReflectionDetail` | `archived` |
| `GET /standards?status=active|retired` → `Page[CompanyStandard]`; `POST /standards` `CompanyStandardCreate {title, statement ≤ 500, applies_to[]}` (201); `PATCH /standards/{id}` `CompanyStandardUpdate {title?, statement?, status?, applies_to?}` | `CompanyStandard`: `{id, company_id, title, statement, source_reflection_id|null, adopted_by_user_id, adopted_at, status: active|retired, applies_to[], created_at, updated_at}`. At most 25 active; `applies_to` = role families / template slugs / agent ids (empty = everyone) |

Rules (code, not prompts): **a standard exists only through a human** — endorsing a `standard_proposal` (original or revised) creates it and sets the reflection to `adopted`;
`POST /standards` is human-authored; the AI Co-founder has no route that creates, edits or retires one. `challenge`/`refine` set a non-adopted reflection back to `open`;
`endorse` on any other kind sets `endorsed`. `rejected` exists in the enum but no M10 action sets it. Active standards are injected into **every** agent's context: `GET /agent/v1/context`
(`standards: [{id, title, statement}]`), `GET /agent/v1/state`, the `company_agent` run spec (`company.standards`, rendered in the role prompt) and the prompt of every CLI run.

Stream events (company stream, invalidate-only): `reflection.created`, `reflection.updated`, `reflection.responded`, `standard.adopted`, `standard.updated`, `command.updated`, `cli_runtime.updated`,
`ai_setup.updated`.

#### Agent API additions (AI Co-founder only on the server; each route has a risk tier — `internal_write`)

| Method & path | Tool |
|---|---|
| `POST /agent/v1/reflections` `{kind, title, body ≤ 6 KB, evidence: [{type,id}], proposed_standard ≤ 500 (standard_proposal only)}` → 201 `Reflection` (200 and the existing one for the same title within 7 days) | `company_post_reflection` |
| `POST /agent/v1/reflections/{id}/replies` `{body}` → 201 `ReflectionResponse` | `company_reply_reflection` |
| `POST /agent/v1/reflections/{id}/revise` `{body?, title?, proposed_standard?, note?}` → `Reflection` (status `revised`; refused for adopted/archived) | `company_revise_reflection` |

Limits: 5 reflections per day, 20 waiting for the Founder, evidence ids must exist in the company, secret-looking text is refused (409). `GET /agent/v1/state` gains `standards` and (Co-founder)
`reflections_to_answer` (reflections whose last response is the Founder's). The Co-founder's toolkit has 3 more tools (35 company tools in the runtime). The `daily-reflection` routine (cron `0 20 * * *`,
company timezone, skipped when idle) is a built-in schedule of every AI Co-founder.

### Command Center (`/api/companies/{cid}`)

| Method & path | Purpose |
|---|---|
| `POST /command` `CommandRequest {input ≤ 10 KB}` → `201 CommandExchange` | `/command …` → executed now with the caller's own permissions, `status: "done"`. Anything else → a Founder's note, `status: "pending"` (owner/admin only; 403 for viewers and members). 409 `ai_setup_required` before the AI is ready |
| `GET /command-center/feed?limit=&cursor=` → `Page[CommandExchange]` (newest first, commands and notes) | any member of the company, agents 403 |
| `POST /command-center/{id}/retry` → `CommandExchange` | owner/admin; only a `failed` free-text exchange (409 otherwise): back to `pending`, the Co-founder is woken again |

`CommandExchange`: `{id, input, input_kind: "command"|"note", created_at, status: "done"|"pending"|"processing"|"answered"|"failed", output: CommandOutput|null, note_id|null, run_id|null, actions: [{type, ref_id, label}], updated_at}`.
`CommandOutput`: `{kind: "text"|"table"|"list"|"error", title, text, columns|null, rows|null, links|null}`; for a `table` `rows` are lists of strings aligned with `columns`; for a `list` each row is a one-string list; `links` = `[{label, panel}]`
where `panel` is a canvas panel string (`tasks`, `approvals`, `finance`, `goals`, `org`, `standards`, `reflections`, `task:<id>`).

Commands: `/help`, `/status`, `/goals`, `/agents`, `/tasks [status]`, `/spend [day|month]`, `/approvals`, `/standards`, `/pause`, `/resume`, `/goal <text>`, `/task <title> [@agent]`, `/hire <role template> [reason]`
(creates a hiring task for HR), `/approve <id>`, `/reject <id> [note]`. They call the same services as the owner API, so the role matrix applies unchanged; a refusal (a viewer's `/pause`) is an
`error` output with the reason, never a silent no-op. Created records come back as `actions` chips. Every note written through `POST /notes` also appears in the feed.

**Free-text lifecycle and the guaranteed output.** `pending` → `processing` (a Co-founder run started: `run_id` is set, and its `run.event` stream is the live progress) → `answered` (its reply, or the triage summary when it never wrote one;
`actions` = the triage records). If the run fails, times out, finishes without answering, or nothing answers within 15 minutes, the system writes an output saying what happened (`output` set, status back to `pending`) and
retries **once** automatically; a second failure ends as `failed` with an `error` output. A run cancelled only because the company was paused / the worker stopped does not use up an attempt. Free text wakes the Co-founder after 5 s (not
60 s) with an `urgent` wake task.

### Deviations and notes

- `POST /command` answers 201 for slash commands as well (a record is created either way).
- The model list (`/models`) response shape, the `install.status` field, `model_provider_id`/`model` on the CLI auth call, and the panel strings in `links` are additions the spec did not fix.
- The CLI settings live in the existing `system_state` table (one row per CLI, a credential id only, never a secret) — no table of their own.
- CLI-runtime API keys reference a credential of a company the installation owner administers; a run uses the key only for agents of the credential's company.

### Verified and not verified (Milestone 10)

Verified: see the M10 report and `docs/cli-runtimes.md` ("What was and was not verified"). In short — the gate, provider checks (mocked HTTP), routing, the three adapters against fake binaries emitting
recorded-format streams, the MCP server (stdio, against a stub API and against OpenCode live), reflections/standards rules, the Command Center, the migration; **live**: OpenCode + a temporary local Ollama
(qwen2.5:7b) executing a hybrid-mode work task through the company MCP tools. **Not verified**: Claude Code and Codex runs (Claude Code was never started beyond `--version`; Codex is not installed), the
real `npm install` path, `own_login` runs, Docker/Postgres.

## Passkeys (WebAuthn sign-in, `/api/auth`)

A passkey is a second way to sign in (and a phishing-resistant one). Humans only: an agent's run token gets `403 guardrail_denied`
(action `passkey.manage`, with a guardrail event) on every route below, including the two public login routes; an API token gets
`403 token_cannot_manage_account`; the owner-token bootstrap credential gets `403` with `details.reason = "passkeys_need_session"`.
The management routes need a real browser session, and the cookie-authenticated `POST`/`PATCH`/`DELETE` ones need the CSRF header.
Server library: `webauthn` (py_webauthn 3.x, BSD-3). Attestation is `none`; nothing about the authenticator model is trusted.

| Method & path | Body → response | Notes |
|---|---|---|
| `GET /passkeys` | → `{items: PasskeyInfo[], next_cursor: null}` | oldest first |
| `POST /passkeys/register/options` | `{name?}` → `PasskeyRegistrationOptions {attempt_id, public_key}` | `public_key` = `PublicKeyCredentialCreationOptions` JSON as produced by `webauthn.options_to_json` (base64url `challenge`, `user.id`, `excludeCredentials[].id`); `residentKey` and `userVerification` are `"preferred"`; `excludeCredentials` = this person's passkeys; `attestation: "none"`. At most 20 passkeys per person (`409`). A new request supersedes the person's unfinished one |
| `POST /passkeys/register/verify` | `{attempt_id, name?, credential}` → `201 PasskeyInfo` | `credential` = the browser's `RegistrationResponseJSON`. Audit `auth.passkey_added`. `422` with `details.reason`: `challenge_unknown` / `challenge_used` / `challenge_expired` / `origin_mismatch` / `verification_failed`; `409` if the credential is already registered |
| `PATCH /passkeys/{id}` | `{name}` → `PasskeyInfo` | own passkeys only (`404` otherwise). Audit `auth.passkey_renamed` |
| `DELETE /passkeys/{id}` | optional `{password}` → `204` | needs a session **younger than 10 minutes** or the account password in the body (else `403`, `details.reason = "reauthentication_required"`; a wrong password is `401 invalid_credentials`, throttled like the password change). The last sign-in method of an account **without a password** cannot be removed: `409`, `details.reason = "last_sign_in_method"`. Audit `auth.passkey_removed` |
| `POST /passkey/login/options` (public) | `{username?}` → `PasskeyLoginOptions {attempt_id, public_key}` | `public_key` = `PublicKeyCredentialRequestOptions` JSON; `userVerification: "preferred"`. Without a username `allowCredentials` is `[]` (discoverable credentials / conditional UI) |
| `POST /passkey/login/verify` (public) | `{attempt_id, credential}` → `AuthSession` + `cos_session` / `cos_csrf` cookies | `credential` = `AuthenticationResponseJSON`. Same response and cookies as the password login. A passkey satisfies 2FA: no TOTP prompt. Every failure is the same `401 invalid_credentials` ("Passkey sign-in failed") |

`PasskeyInfo = {id, name, created_at, last_used_at, transports: string[], backed_up: bool}`. Never returned: credential id, public key, counter.

`AuthSession` (from `GET /session`, `/setup`, `/login`, `/passkey/login/verify`) gained `passkeys_count: int` (the signed-in person's passkeys, else 0)
and `passkey_supported_origin: bool` (can *this request's* origin use WebAuthn at all; show the passkey UI only when true).
Since M13 it also has `passkey_unsupported_reason: "ephemeral_origin" | null`: `"ephemeral_origin"` when the request's host is a quick-tunnel address (`*.trycloudflare.com`,
or the host of the running tunnel when that tunnel is a quick tunnel), `null` otherwise. It is only ever set together with `passkey_supported_origin: false`; a client that does
not know the field treats it like any unsupported origin. The web app uses it to say "Passkeys need a stable address" instead of the plain-http message.

**Challenges.** Created by the server, stored in `passkey_challenges`, valid 5 minutes, single use, and burnt *before* verification (a failed
attempt cannot be retried: ask for new options). A registration challenge is bound to the person who asked; a login challenge to the anonymous
`attempt_id`. The client's own challenge is never trusted. Expired rows are swept; at most 1000 unfinished login challenges exist at a time
(`429`, `retry_after_seconds`).

**RP id and origins** are derived from the request's `Host` header, never from the client:

| Host | RP id | Accepted `clientDataJSON.origin` |
|---|---|---|
| `localhost`, `127.0.0.1` (any port) | `localhost` | `http(s)://<that host:port>`, `http://localhost:<port>` and `http://127.0.0.1:<port>`, and the loopback entries of `COMPANY_OS_CORS_ORIGINS` (the Vite dev server) |
| the host of a **configured https origin**: the running tunnel URL (M8; **not** a quick tunnel, see below), a company custom domain (M8), an https `COMPANY_OS_PUBLIC_URL`, or an entry of `COMPANY_OS_PASSKEY_ORIGINS` (comma separated or a JSON list of origins) | that host name | exactly that origin |
| anything else (LAN IP, `*.local`, an unknown name, a quick-tunnel address) | none | `passkey_supported_origin = false`; both options routes answer `400 passkey_origin_unsupported` (`passkey_unsupported_reason = "ephemeral_origin"` for a quick-tunnel address) |

Verification rejects a response whose origin is not in the accepted set, whose RP-id hash differs, or whose RP id differs from the one the
challenge was issued for. A passkey is bound to its RP id: one registered on `localhost` does not work on the tunnel domain and vice versa,
and a quick tunnel (`*.trycloudflare.com`) gets a new host name on every start. Since M13 a quick-tunnel host (any `*.trycloudflare.com`, and the running tunnel's host when its provider is
`cloudflare_quick`) gets **no** relying party at all, so no passkey can be created or used there; use a named tunnel or a custom domain, or `http://localhost` (an SSH port-forward).

**LAN limits.** Browsers expose WebAuthn only in a secure context (HTTPS, or `localhost`). Opening Foundry AI OS at `http://192.168.x.x:8700` or
`http://my-mac.local:8700` therefore cannot create or use a passkey (the browser refuses before the server is asked), and the server answers
`passkey_supported_origin: false`. From another device on the LAN use the password (plus TOTP), or reach the install through its HTTPS address.
The hosted control UI on another origin (Vercel) is not covered: its origin differs from the API's Host, so passkeys there are not supported yet.

**Rate limits and enumeration.** `passkey/login/verify` shares the password login's throttle table and parameters: failures count per IP (`ip:<address>`,
the same bucket as password logins) and per credential (`pk:<hash of the credential id>`), with the same exponential backoff and `429 rate_limited` (while
locked, even a genuine assertion is refused). `passkey/login/options` answers the same shape for every username: a person with passkeys gets their real
`allowCredentials`; an unknown username, or a person without a passkey, gets one or two **stable decoy ids** derived from the server key and the name (no
`transports`), so the response does not say whether the account exists.

**Clone detection.** The signature counter is checked only after the signature verified: if the stored counter is above 0 and the new one is not greater, the
sign-in is refused (generic `401`), the stored counter is left alone, and `auth.passkey_clone_suspected` is audited. Authenticators that always report 0 keep working.

**Exposure gate (M8).** At least one passkey counts as strong authentication, like 2FA: an owner whose password was not set under the policy may expose the
device once a passkey exists (`GET /reachability`: `public_exposure_blocked_code` clears and `public_exposure_advice` is no longer `two_factor_recommended`).
Since M13 a passkey registered on an ephemeral origin does not count: the `auth.passkey_added` audit event now carries `rp_id` next to `passkey_id`, and the gate ignores passkeys whose
`rp_id` is a `*.trycloudflare.com` host. A passkey added before M13 has no `rp_id` in its event and counts as before.

**Verified and not verified.** Verified: full registration and login ceremonies against a software authenticator built with `cryptography` (ES256, `attestation: none`,
real signatures and counters; tests `test_passkeys.py`), the policy above, migration `0010` up/down. Not verified: a real browser/OS authenticator (Touch ID, Windows Hello, a
FIDO2 key, a phone via hybrid), non-`none` attestation formats against real hardware, RS256/EdDSA authenticators, Postgres. User verification is requested (`preferred`) but not
required, so a passkey on an authenticator without a PIN/biometric is possession-only while still counting as 2FA; requiring it is a one-line policy change if wanted.

## Milestone 14 — GitHub-backed projects (`/api/companies/{cid}`)

A project may be backed by one GitHub repository ("binding"). Owner/admin humans change bindings; any human member reads them
(viewers included); an agent's run token gets `403` on every route below except the agent review route; a non-member gets `404`;
a `write`-scoped API token cannot change repositories (an `admin` token can, a `read` token reads). Lists return `{items, next_cursor}`.
The token, the ETags, the sync lease and the webhook secret are never in any response, audit event or log. Design: `docs/specs/m14-github-backed-projects.md`.

| Method & path | Body → response | Notes |
|---|---|---|
| `GET /repositories/status` | → `RepoAvailability {available, reason, git_version, milestone: "M14"}` | any member; `available: false` when git (or file locking) is missing. Creating then answers `501 not_available` with `details {available: false, milestone: "M14", reason: "git_unavailable"}` |
| `POST /github/inspect` | `GithubInspectRequest {connected_account_id, full_name?, owner?, mode}` → `GithubInspectResult` | owner/admin; what the token can do (owner candidates, token kind and expiry, repository facts, `missing_permissions`, `warnings`) |
| `GET /repositories?project_id=&limit=&cursor=` | → `Page<ProjectRepository>` | oldest first |
| `POST /repositories` | `RepositoryCreate` → `201 ProjectRepository` | see below |
| `GET /repositories/{rid}` | → `ProjectRepository` | includes `project_name`, `webhook {enabled, url, stale}` and `open_pull_request` (summary of the open Foundry PR, else null) |
| `PATCH /repositories/{rid}` | `RepositoryUpdate {policy?, default_branch?, is_company_default?}` → `ProjectRepository` | only the given policy fields change. `409 branch_protected` for `merge_mode: direct_push` while the base branch is protected (from the stored protection summary); `409 repository_busy` when the base branch changes while a PR is open; `409` on a disconnected binding. Audit `repository.policy_updated` with `data.changed = {"policy.<field>": {old, new}, ...}`; nothing changed = no event |
| `POST /repositories/{rid}/sync` | → `202 {queued: true}` | `409` unless `active`, `auth_failed` or `error` (a paused binding makes no network call) |
| `POST /repositories/{rid}/pause` / `…/resume` | → `ProjectRepository` | idempotent; `409` from `pending`/`creating`/`cloning`/`disconnected` (pause) or from anything but `paused` (resume) |
| `POST /repositories/{rid}/disconnect` | `{confirm: true}` → `ProjectRepository` | `422` without `confirm`; `409 repository_busy` while a Foundry PR is `merging` or a run of the project is running. The row and the clone stay (`status = disconnected`, work continues locally); the webhook secret is revoked and the hook, if any, is left on GitHub (`data.webhook_left_on_github` in the audit event). Connect the project again later with `POST /repositories` (the row is reused) |
| `POST /repositories/{rid}/integration/reset` | `{to: "remote_work_branch" \| "default_branch", confirm: true}` → `ProjectRepository` | keeps the old head as the branch `foundry/rescue-<UTC>` (in the audit event); refused (`409`) while a run is active or there is no clone yet |
| `POST /repositories/{rid}/pull-requests/resume-opening` | → `ProjectRepository` | clears `pr_opening_paused` (set when a PR was closed without merge) |
| `PUT /repositories/{rid}/webhook` | `{enabled}` → `ProjectRepository` | enabling needs a stable public URL: `409 public_url_ephemeral` for no tunnel or a quick tunnel; `409` (`details.reason = webhook_permission_missing`) when the token lacks the Webhooks permission |
| `GET /repositories/{rid}/pull-requests` | → `Page<RepoPullRequest>` | newest first |
| `GET /repositories/{rid}/items?kind=&status=` | → `Page<RepoItem>` | newest first; every item has `untrusted: true`, `body_excerpt` is clipped and scrubbed, `injection_flags` lists what the scanner saw. Never present these as the company's own words |
| `GET /repositories/{rid}/activity` | → `Page<AuditEvent>` | audit events with `target_type = "repository"`, newest first |
| `POST /webhooks/github/{repository_id}` (public) | signed body → `202 {accepted: true}` | see Webhook |

**Creating (`POST /repositories`).** `mode: "create"` makes an empty repository (`auto_init` false; Foundry writes the first commit in the worker) under `owner`
(default: the token's login; an organisation the token can create in is detected) and binds it with `status: "creating"`, `origin: "created"`. `mode: "connect"` binds an existing one:
`status: "cloning"`, `origin: "connected"`, or `"creating"`/`"created"` when the repository is empty, or `"creating"`/`"published"` with `publish_local`. Exactly one of `project_id` / `project_name`
(a new project is created in the same transaction). The response is the binding right after the request; the `repo_clone` job (worker) moves it to `active` or `error`
(poll `GET /repositories/{rid}` or listen for the `repository.updated` event). Defaults follow the choice (`default_policy`): `create` is `pr_auto_merge` with docs upkeep, `connect` is
`pr_human_merge` with intake of `foundry`-labelled issues and `ci_mode: required` when `.github/workflows` exists; `policy` overrides any field. `onboarding_status` is `pending` for a connected non-empty repository.
`company_default` (default true) makes it the company's default repository and sets `new_project_repository = company_repo`: later projects (created by the Co-founder or a person) get their own binding to it
(own work branch `foundry/<project-slug>`, own clone, no second issue intake or alerts). `work_branch` defaults to `foundry/<project-slug>` (a suffix is added if another project of the company uses it).
A project's existing local repository: kept aside as `projects/<project>/local-<UTC>` (never deleted, `status_reason` says so and an info alert is raised) when the remote has content, simply replaced when it has no history,
kept in place and published when `publish_local` is set and the remote is empty (`422` if it is not).

| Error | Status | When |
|---|---|---|
| `no_account` | 409 | the connected account is not a live GitHub account of this company |
| `project_has_repository` | 409 | the project already has a binding that is not disconnected |
| `confirm_public_required` | 422 | `visibility: public` without `confirm_public` |
| `repository_exists` | 409 | create: the name is taken |
| `administration_permission_missing` | 409 | create: the token lacks Administration (nothing was created) |
| `repository_not_accessible` | 409 | the token cannot see the repository (or the owner) |
| `repository_archived` / `repository_too_large` | 409 | archived; larger than `repo_max_size_mb` |
| `token_cannot_push` | 409 | `details.missing_permissions` lists what the token lacks |
| `branch_protected` | 409 | `direct_push` on a protected base branch |
| `repository_busy` | 409 | a run of the project is active (connect, disconnect, reset); a PR is merging (disconnect); a PR is open (base branch change) |
| 422 | | a branch or name is invalid, a branch override does not exist, the description contains a secret |

`ProjectRepository.protection` holds the summary read at binding time (and refreshed by the sync loop): `{branch, protected, require_pr, required_checks, linear_history, required_signatures, non_fast_forward, checked_at}`.

**Webhook (optional, polling stays the default).** Enabling creates a hook on GitHub for `push`, `pull_request`, `pull_request_review`, `pull_request_review_comment`, `issues`, `issue_comment`, `check_suite`,
`check_run`, `status`, `workflow_run` and `dependabot_alert`, at `<public URL>/api/webhooks/github/<repository id>`, with a random 32-byte secret stored encrypted in the credential store
(a credential named `GitHub webhook secret <id>`; its value is never returned). A delivery needs a valid `X-Hub-Signature-256` (HMAC-SHA256 of the raw body, constant-time compare). An unknown repository, a binding
without a webhook, a revoked secret and a bad or missing signature all answer the same `401 webhook_rejected`. The body is capped at `webhook_max_bytes` (`413`, checked before the signature). A valid delivery
only sets the binding's next sync to "now" (and only for an `active` binding; a `ping` does nothing); the payload is never parsed or trusted. `webhook.stale` is true when the recorded public URL differs from the hook's URL (polling continues; enable again to replace the hook).

**Agent route.** `POST /api/agent/v1/repo/pulls/{pull_id}/review` (run token; action `repo.pr.review`, `internal_write`): body `PullReviewBody {decision: "accept" | "request_changes", note?, head_sha?}` →
`PullReviewResult {pull_id, number, verdict: "accepted" | "changes_requested", verdict_sha, fix_task_id}`. Only the assignee of the pull request's review task (`403` for anyone else, `404` for a pull request of another company);
`409 pull_request_changed` when the pull request's head is no longer the reviewed commit, when `head_sha` (a full SHA or a prefix) is not the reviewed commit, or when the pull request is merged/closed; `409` when the review task is not running.
The verdict writes `verdict`, `verdict_note` (scrubbed), `verdict_sha` and `verdict_agent_id` and completes the review task; it never changes the PR's `stage` (the sync loop does). `request_changes` also creates the work task
`Address the review of PR #<n>` (assigned to the project lead) and an `internal_review` item. Repeating the same call returns the same result. The runtime tool is `company_review_pull_request(pull_id, decision, note, head_sha?)`,
offered only on runs of review tasks that reference `github:pr:<id>`.

**State.** The AI Co-founder's (and a lead's) `GET /agent/v1/state` project rows gained `repository: {full_name, merge_mode, status} | null`.

**Audit actions** (all `target_type = "repository"`, `target_id = <repository id>`): `repository.created`, `.connected`, `.published`, `.policy_updated`, `.paused`, `.resumed`, `.disconnected`,
`.integration_reset`, `.pr_opening_resumed`, `.webhook_enabled`, `.webhook_disabled`, `.pr_review_requested`, `.pr_verdict`; creating a repository for a company that still had `new_project_repository = local`
also writes `policy.updated`. Event: `repository.updated {repository_id}`.

**Verified and not verified (WP-C).** Verified against the GitHub fake and a local git remote (`backend/tests/test_m14_*.py`): every route above, tenancy and roles, the refusals, the local-history handling, the webhook signature
path and the review rules. Not verified: any request against real GitHub (permission names, the organisation-approval and SSO answers, the hook API), the clone job itself (worker, WP-B).
