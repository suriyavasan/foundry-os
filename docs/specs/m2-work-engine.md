# M2 — Work engine (spec)

Goal: durable, policy-enforced work execution that later milestones plug into. After M2,
a task assigned to an active agent is dispatched automatically, executed through a
`RuntimeAdapter`, its events streamed live, its usage charged against an atomic budget
reservation, and its outcome reflected in the task — surviving restarts.

Conventions from `docs/architecture.md` and `AGENTS.md` apply (tenancy via `CompanyContext`,
services own authz, audit in same transaction, micro-USD money, Alembic migration, error
envelope, `{items,next_cursor}` lists, no fake data in production paths).

## 1. Data model (one Alembic migration `0002_work_engine`)

All tables: UUID string PK (except `company_events.id`, see below), `company_id` FK + index,
`created_at`/`updated_at` UTC. Enumerations stored as strings, validated in Python.

### CompanyPolicy (1:1 with Company, created with the company; backfill existing companies in the migration)
| field | type | default |
|---|---|---|
| autonomy_mode | `autonomous` \| `supervised` | `autonomous` |
| human_approval_required_for | json list of approval kinds | `["external_purchase","budget_increase"]` |
| daily_budget_micros, monthly_budget_micros | bigint null | null (no limit) |
| max_concurrent_runs | int | 2 |
| max_delegation_depth | int | 3 |
| max_tasks_per_planning_cycle | int | 10 |
| max_attempts_per_task | int | 3 |
| max_revisions | int | 2 |
| max_run_seconds | int | 900 |
| max_headcount | int | 12 |
| heartbeat_interval_seconds | int | 900 |
| default_run_reservation_micros | bigint | 50_000 ($0.05) — reserved per run when the model's cost is unknown |
| allowed_model_providers | json list | `[]` = any |
| budget_lock_version | bigint | 0 — written at the start of every reservation transaction (D9) |

Only human owner/admin may update. Agents: 403. Audit `policy.updated` with changed field names.

### Goal
`title` (1–200), `description`, `status` (`proposed`,`active`,`achieved`,`abandoned`), `priority` (`low`,`normal`,`high`,`urgent`), `owner_agent_id` (null → founder), `target_date` (date null), `created_by_type`/`created_by_id`.
Company `strategic_goal` stays as the headline; goals are the structured list.

### Project
`goal_id` (null), `name` (1–200), `description`, `status` (`planned`,`active`,`paused`,`completed`,`cancelled`), `lead_agent_id` (null), `budget_micros` (null), creator fields.

### Task
| field | notes |
|---|---|
| project_id, parent_task_id | null; same company (404 otherwise) |
| title (1–300), description, acceptance_criteria | text |
| kind | `work`,`review`,`planning`,`hiring`,`routine` |
| status | see state machine |
| priority | `low`,`normal`,`high`,`urgent` |
| assignee_agent_id, reviewer_agent_id | null; same company; must not be retired/rejected |
| required_skills | json list of skill tags (M4 uses it) |
| budget_micros | null = no per-task cap |
| attempt_count, revision_count | int |
| delegation_depth | parent's depth + 1 (root 0); reject > policy.max_delegation_depth (409 `limit_exceeded`) |
| due_at, started_at, completed_at | null |
| result_summary | text null |
| error | json null `{code, message, run_id}` |
| input_refs, output_refs | json lists (artifact ids / urls) |
| created_by_type/created_by_id | human/agent/system |
| idempotency_key | null; unique per (company_id, idempotency_key) — duplicate create returns the existing task with 200 instead of 201 |

**Task state machine** (service-enforced, invalid → 409 `invalid_transition`):

```
backlog ──assign+deps done──▶ ready ──dispatch──▶ in_progress
   ▲                            │                    │ run succeeded, needs review ─▶ in_review ─accept─▶ done
   │ unassign / deps reopen     │                    │ run succeeded, no review ────────────────────────▶ done
   └────────────────────────────┘                    │ run failed, attempts left ─▶ ready (retry)
blocked ◀──▶ (any open state, manual/agent with reason)   run failed, no attempts ─▶ failed
in_review ─request_revision─▶ revision_requested ─▶ ready (revision_count+1; > max_revisions → failed)
any non-terminal ─cancel─▶ cancelled           terminal: done, failed, cancelled
```
- "Needs review" = `acceptance_criteria` non-empty **and** a reviewer is resolvable (reviewer_agent_id, else assignee's manager agent). Otherwise success → `done`.
- Readiness is recomputed whenever a task is created/assigned/updated, a dependency finishes, or an agent's status changes: `backlog` → `ready` iff assignee is `active`, all dependencies are `done`, company `active`. `ready` → `backlog` if those stop holding.
- When a task reaches `done`, dependents are re-evaluated. When a parent's children are all terminal, nothing automatic happens to the parent in M2 (M5 decides).

### TaskDependency
`task_id`, `depends_on_task_id`, unique pair, same company, no self-dependency, **cycle rejected** (409).

### TaskRun (one execution attempt)
`task_id`, `agent_id`, `attempt_number`, `status` (`queued`,`starting`,`running`,`succeeded`,`failed`,`cancelled`,`timed_out`), `runtime`, `runtime_execution_id` (null), `idempotency_key` unique (`{task_id}:{attempt_number}:{revision_count}`), `queued_at`, `started_at`, `finished_at`, `heartbeat_at`, `worker_id`, `cancel_requested` (bool), `exit_reason` (text), `error` (json), `input_tokens`, `output_tokens`, `cost_micros`, `cost_kind` (`verified`/`estimated`/`unknown`), `reservation_id`.
Task business status and run status are separate (a task can be `ready` again after a failed run).

### RunEvent
`run_id`, `seq` (int, unique per run), `type`, `payload` (json, secrets redacted), `occurred_at`. Insert is idempotent on (run_id, seq).

### Artifact / ArtifactVersion
Artifact: `project_id`, `task_id`, `run_id` (all null), `created_by_type/id`, `title`, `kind` (`document`,`code`,`design`,`report`,`data`,`link`,`other`), `mime_type`, `status` (`draft`,`submitted`,`accepted`,`rejected`), `current_version` (int).
ArtifactVersion: `artifact_id`, `version`, `storage_key`, `size_bytes`, `sha256`, `created_by_type/id`, `note`. Unique (artifact_id, version).
Storage: `app/storage/` interface with `LocalFileStorage(root=data/artifacts)`; key = `{company_id}/{artifact_id}/{version}`; content never in DB. Max upload 25 MB (configurable). Downloads stream with the stored mime type and `Content-Disposition: attachment` for non-text types.

### Budget ledger
**BudgetReservation:** `agent_id`, `run_id` (null until bound), `project_id`, `task_id`, `amount_micros`, `status` (`held`,`settled`,`released`), `settled_micros`, `period_day` (date in company tz), `period_month` (`YYYY-MM`).
**UsageRecord:** `agent_id`, `project_id`, `task_id`, `run_id`, `runtime`, `provider`, `model`, `input_tokens`, `output_tokens`, `cached_tokens`, `cost_micros`, `cost_kind`, `dedupe_key` (unique per company), `occurred_at`.

`services/ledger.py`:
- `reserve(db, company_id, agent_id, task, amount) -> Reservation | raises BudgetExceeded`
  1. `UPDATE company_policies SET budget_lock_version = budget_lock_version + 1 WHERE company_id = :c` (serializes concurrent reservers on SQLite and Postgres — D9).
  2. For each applicable limit — company daily/monthly (policy), agent daily/monthly (agent fields), project `budget_micros`, task `budget_micros` — compute `committed = settled usage + held reservations` for the period and reject if `committed + amount > limit`.
  3. Insert reservation `held`. All in the caller's transaction.
- `settle(reservation, actual_micros)` → `settled`; `release(reservation)` → `released`.
- `record_usage(...)` idempotent on `dedupe_key` (duplicate → no-op, returns existing).
- Periods use the company timezone. Spend queries must not double count: settled reservations count by `settled_micros`, held by `amount_micros`, usage records are the source for reporting (reservations are for enforcement).
- **Must have a concurrency test:** N threads reserving against a limit that fits only K reservations → exactly K succeed (file-backed SQLite, separate sessions).

### Approval
`kind` (`external_purchase`,`budget_increase`,`external_communication`,`production_deploy`,`sensitive_action`,`hire`,`other`), `status` (`pending`,`approved`,`rejected`,`changes_requested`,`expired`), `approver` (`human`/`founder`, derived from policy at creation: kind ∈ `human_approval_required_for` → human, else founder), `requested_by_agent_id`, `task_id`, `title`, `reason`, `amount_micros`, `vendor`, `recurrence` (`one_time`,`monthly`,`yearly`, null), `risk` (`low`,`medium`,`high`), `evidence` (json), `scope` (json: the exact action payload), `scope_hash` (sha256 of canonical scope json), `decided_by_type/id`, `decided_at`, `decision_note`, `consumed_at`.
- An approval authorizes exactly one action: `consume(approval_id, scope)` succeeds only if approved, unconsumed and the scope hash matches; then sets `consumed_at`.
- Humans decide `human` approvals; the founder agent decides `founder` approvals (agent API in M3). An agent never decides its own request.

### Decision (decision log for autonomy transparency)
`made_by_type/id`, `title`, `rationale`, `related_task_id`, `related_project_id`, `context` (json).

### Schedule / ScheduleFire
Schedule: `name`, `kind` (`heartbeat`,`routine`), `agent_id`, `interval_seconds` (≥60) **or** `cron` (5-field; use `croniter`), `timezone`, `task_template` (json: title, description, kind, priority, acceptance_criteria), `enabled`, `next_run_at`, `last_fired_at`.
ScheduleFire: `schedule_id`, `scheduled_for`, `task_id`; unique (schedule_id, scheduled_for) — firing twice for the same slot is a no-op (idempotent after restarts).

### Job (durable queue)
`company_id` (null for system jobs), `kind`, `payload` (json), `status` (`queued`,`leased`,`done`,`failed`,`dead`), `run_after`, `lease_expires_at`, `leased_by`, `attempts`, `max_attempts` (default 5), `last_error`, `dedupe_key` (unique, null allowed).
Lease = select candidate ids ordered by run_after, then `UPDATE … SET status='leased', leased_by, lease_expires_at WHERE id=:id AND status='queued'` and accept only if rowcount == 1 (portable, no SKIP LOCKED needed). Expired leases return to `queued`. Exponential backoff on failure; `dead` after max_attempts.

### CompanyEvent (live-update outbox)
`id` BigInteger autoincrement PK (ordering cursor), `company_id`, `type`, `payload` (json, no secrets), `created_at`. Written in the same transaction as the change. Types at least: `task.created`, `task.updated`, `run.queued`, `run.started`, `run.event`, `run.finished`, `agent.updated`, `agent.activity` (current activity summary), `artifact.created`, `approval.created`, `approval.decided`, `activity` (mirrors AuditEvent). Pruned after 7 days by the scheduler.

## 2. Runtime execution path

### RuntimeAdapter (extend `app/runtimes/base.py`)
Keep the M1 interface; make it concrete for execution:
```python
class RuntimeAdapter(ABC):
    id: str; capabilities: frozenset[Capability]
    def validate_config(self, config) -> list[ConfigIssue]
    def check_health(self) -> RuntimeHealth
    def execute(self, request: ExecutionRequest, sink: EventSink, cancel: CancelToken) -> ExecutionResult
```
`execute` runs synchronously in the worker thread, calls `sink.emit(ExecutionEvent)` for each event (seq, type, payload, ts), checks `cancel.is_set()` cooperatively, and returns `ExecutionResult(status, summary, error, usage: UsageReport, artifacts: list[ArtifactRef])`. The worker owns timeouts (it sets the cancel token at `max_run_seconds`, result → `timed_out`). Adapters for remote runtimes (upstream Hermes gateway) implement `execute` via start/stream/stop internally.

`ExecutionRequest`: run_id, task (id, title, description, acceptance_criteria, kind, priority, input_refs), agent (id, name, role_title, instructions, responsibilities, model_provider, model_name, runtime_config, tool_permissions), company (id, name, strategic_goal, constraints), limits (max_run_seconds, budget_micros), run_token, agent_api_url, workspace_dir.

### Deterministic test runtime
`app/runtimes/scripted.py`: `ScriptedRuntime` replays a scripted sequence (events, usage, result, optional delay, optional failure, honours cancel). Registered **only** when `COMPANY_OS_ENABLE_TEST_RUNTIMES=1` (tests set it); never in default production config, and `/api/runtimes` labels it `kind: "test_fixture"`.

### Dispatcher (scheduler tick, every 2 s)
For each active company (status `active`):
1. Recompute readiness for tasks touched since last tick (or all open tasks — fine at this scale).
2. `running = count(runs in queued/starting/running)`; while `running < max_concurrent_runs`, take the next `ready` task by (priority desc, created_at) whose assignee is `active` and whose runtime is registered and healthy-or-unknown, and in **one transaction**: reserve budget (`task.budget_micros` or policy default), create TaskRun `queued` (idempotency key), set task `in_progress` + `started_at` (first attempt), `attempt_count+1`, enqueue Job `execute_run` (dedupe_key = run id), emit events, audit `run.queued`.
3. `BudgetExceeded` → task → `blocked` with `error={code:"budget_exceeded"}` + audit + company event (not retried until budget changes; a `budget.changed` event or policy update re-evaluates blocked-by-budget tasks).
4. Assignee runtime `unassigned` → task stays `ready` but is skipped; overview reports it as "waiting for runtime".

### Worker (`execute_run` job)
1. Load run; if `cancel_requested` → cancelled. Mark `starting` → `running`, `worker_id`, heartbeats every 5 s (thread).
2. Mint run token (see §3), prepare `data/workspaces/{company_id}/{run_id}/`.
3. `adapter.execute(...)` with sink that persists RunEvents (idempotent) + `run.event` CompanyEvents and updates `agent.activity` (latest human-readable summary, throttled ≤1/s).
4. On result: record UsageRecord (dedupe `run:{run_id}`), settle reservation with actual cost (`cost_kind` from adapter; unknown cost → settle at reservation amount and mark `estimated`), finalize run, transition task per state machine, revoke run token, audit + events.
5. Any exception → run `failed` with sanitized error; same retry logic.

### Recovery (on worker/scheduler start, then every 30 s)
- Runs in `starting`/`running` whose `heartbeat_at` is older than 30 s → `failed` (`exit_reason="worker_lost"`), reservation released, task → retry logic.
- Expired job leases → `queued`.
- Schedules with `next_run_at` in the past fire **once** for the latest missed slot (no backfill storm).

### Processes
- `uv run companyos-worker` — runs the worker loop + scheduler loop (threads) with graceful SIGTERM shutdown (stop leasing, cancel running executions, wait ≤10 s).
- `COMPANY_OS_EMBEDDED_WORKER=1` starts the same loops inside the API process (lifespan) for one-command local dev. Default off in tests.

## 3. Agent API (`/api/agent/v1`, used by runtimes in M3)

Auth: `Authorization: Bearer <run token>`. Token = HMAC-SHA256 signed (key from `COMPANY_OS_SECRET_KEY`; in local mode auto-generate `data/secret.key`, mode 0600, never logged) over `{run_id, agent_id, company_id, exp}`; valid only while the run is `starting`/`running` and not expired. Resolves to an agent `Principal` bound to that company + run. All writes are attributed to the agent and audited. Agents can never touch policy, budgets, permissions, or their own lifecycle (existing M1 guard applies).

| Method | Path | Purpose |
|---|---|---|
| GET | `/context` | company (name, mission, goal, constraints), current task (+ parent, deps, artifacts), agent's own profile, manager, direct reports, active colleagues (name, role, skills tags), policy limits relevant to the agent |
| POST | `/tasks` | create a task/subtask. Body: title, description, acceptance_criteria, kind, priority, assignee_agent_id, reviewer_agent_id, parent_task_id (default current), depends_on [ids], required_skills, budget_micros, idempotency_key. Enforces delegation depth, `max_tasks_per_planning_cycle` (count of tasks created by this run), budget_micros ≤ parent's remaining budget if parent capped |
| PATCH | `/tasks/{id}` | limited: status `blocked` (+reason) / unblock, assignee (only tasks the agent created or manages) |
| POST | `/tasks/{id}/complete` | assignee only, during its run: `result_summary`, `output_refs` |
| POST | `/tasks/{id}/review` | reviewer only: `{decision: accept\|request_revision, note}` |
| POST | `/artifacts` | multipart: file + title/kind/task_id; or JSON `{title, kind, content}` for text; new version if `artifact_id` given |
| POST | `/progress` | `{message}` → RunEvent `progress` + `agent.activity` |
| POST | `/decisions` | record a decision |
| POST | `/approvals` | request approval → `{id, status, approver}` |
| POST | `/approvals/{id}/decide` | founder only, for `approver=founder`, never its own request |
| GET | `/approvals/{id}` | poll |

## 4. Owner API additions (`/api/companies/{cid}/…`)

Standard CRUD + list filters + pagination; owner/admin to mutate, viewer read-only.
- `GET/PATCH /policy`
- `GET/POST /goals`, `GET/PATCH /goals/{id}`
- `GET/POST /projects`, `GET/PATCH /projects/{id}`
- `GET/POST /tasks` (filters: status, project_id, assignee_agent_id, parent_task_id, kind), `GET/PATCH /tasks/{id}`, `POST /tasks/{id}/{cancel,block,unblock,retry}`, `POST /tasks/{id}/dependencies` `{depends_on_task_id}`, `DELETE /tasks/{id}/dependencies/{dep_id}`
- `GET /tasks/{id}/runs`, `GET /runs/{id}`, `GET /runs/{id}/events?after_seq=`, `POST /runs/{id}/cancel`
- `GET /artifacts` (filters: project_id, task_id, kind, status, q), `GET /artifacts/{id}` (+versions), `GET /artifacts/{id}/versions/{v}/content`, `POST /artifacts` (human upload)
- `GET /approvals` (filter status/approver), `POST /approvals/{id}/{approve,reject,request_changes}` (human approver only, `{note}`)
- `GET /decisions`
- `GET/POST /schedules`, `PATCH /schedules/{id}`
- `GET /usage?from=&to=&group_by=agent|project|model|day` → aggregates (micros, tokens, by cost_kind) — minimal in M2, expanded in M6
- `POST /pause`, `POST /resume` (company kill switch: pause → status `paused`, stops dispatch, sets `cancel_requested` on all active runs; resume → `active`)
- `GET /stream` — SSE. `id:` = CompanyEvent id; supports `Last-Event-ID` header and `?after=`; heartbeat comment every 15 s; polls the outbox every 1 s; membership check like any company route.
- `GET /agents/{id}/activity-now` → `{state: idle|working|blocked|paused|offline, current_task, current_run, summary, since}` (also included in agent list responses as `live` field).
- Overview: replace `available:false` blocks for tasks, spending, approvals, artifacts, routines with real aggregates (tasks by status, today/month spend vs budget by cost_kind, pending approvals by approver, latest 5 artifacts, upcoming schedules).

## 5. Tests (must exist)

- Task state machine: every valid/invalid transition; readiness recompute on dep done / agent paused.
- Dependency cycle rejection; cross-company references → 404.
- Delegation depth + per-run task cap via agent API.
- Task idempotency key returns the existing task.
- Ledger: limits per company/agent/project/task, daily vs monthly periods in company tz, **concurrent reservation test**, usage dedupe, settle/release math.
- Approvals: approver derivation from policy, single-use consume with scope hash, agent cannot decide own request, human-only kinds can't be decided by founder.
- Dispatcher + worker end-to-end with ScriptedRuntime: success → done; success with acceptance criteria → in_review → accept → done; failure with retries → ready → failed after max attempts; timeout → timed_out; cancel mid-run; budget exceeded → blocked; concurrency limit respected.
- Recovery: stale running run → failed + retried; expired lease requeued; schedule fires once per slot across two scheduler instances / restarts.
- Agent API: token required/expired/after-run-finished → 401; token of company A cannot touch company B; agent cannot PATCH policy/budgets.
- SSE: events delivered in order and resumable with Last-Event-ID (test the generator function directly, not a live socket).
- Pause/resume kill switch.
- Migration upgrade/downgrade on a fresh DB; model/migration parity test passes.
