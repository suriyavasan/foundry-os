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
  "tasks":     { "available": false, "milestone": "M3" },
  "spending":  { "available": false, "milestone": "M6" },
  "approvals": { "available": false, "milestone": "M6" },
  "artifacts": { "available": false, "milestone": "M7" },
  "routines":  { "available": false, "milestone": "M5" },
  "recent_activity": [AuditEvent]   // newest 10
}
```

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
  "created_at", "updated_at", "status_changed_at"
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
