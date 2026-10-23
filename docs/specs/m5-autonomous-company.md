# M5 — Autonomous company — spec

Owner direction: the company runs itself; the human sets the goal and limits and does not
micromanage. Architecture: `docs/architecture.md` §6.3, decision D8. Builds on M2 (engine),
M3 (runtime), M4 (skills/HR).

## 1. Founder heartbeat
- Every company has a `heartbeat` Schedule for its founder (created with the company;
  backfilled by migration), interval = `policy.heartbeat_interval_seconds` (default changes
  to 1800 s because local models are slow; owner-configurable).
- **Event triggers** also wake the founder (debounced): goal created/activated/changed,
  any task in a founder-owned project reaching `done`/`failed`/`blocked`, a hire activated,
  an approval decided, a run failing with `worker_lost`/`budget_exceeded`.
- A wake creates a **planning task** (`kind=planning`, assigned to the founder, title
  "Company review <timestamp>") **only if no planning task is open** (coalescing; idempotency
  key `planning:<company>:<open-window>`), and only within
  `policy.max_planning_runs_per_hour` (default 4). Paused companies never wake.
- Planning task description = instructions only (no state snapshot). The founder reads live
  state through tools.

## 2. Live company state for agents
Agent API `GET /state` (founder + leads; others get their own slice):
goals (status, priority), projects (status, lead, progress counts, budget used),
open tasks grouped by status with assignee/reviewer and `blocked_reason`, recent failures
(last 24 h, error codes), pending approvals, budget utilization (company + per agent, by
cost_kind), headcount by family and capacity (open tasks per agent), open hiring requests,
recent decisions (10), verified company memory (see §5), alerts.
Agent API additions for the founder: `POST/PATCH /goals`, `POST/PATCH /projects`
(lead, status), `POST /memory` (propose), `POST /memory/{id}/verify` (founder only).
Toolset additions: `company_get_state`, `company_manage_goal`, `company_manage_project`,
`company_propose_memory`, `company_verify_memory`.

## 3. Work orchestration rules (control plane, deterministic)
- **Subtask waiting:** when an assignee's run ends successfully **without** calling
  `complete_task` and the task has open children, the task → `blocked`
  (`blocked_reason="waiting_on_subtasks"`). When all children are terminal, it returns to
  `ready` for the assignee to integrate results (children summaries + artifacts appear in
  `/context`). A run that ends without completing and without children → treated as
  failed attempt (`exit_reason="not_completed"`).
- **Reviews as work:** when a task enters `in_review`, the dispatcher ensures exactly one
  open `review` task (assignee = reviewer, parent = the reviewed task, input_refs = its
  artifacts, idempotency `review:<task>:<revision>`). The reviewer's run must call
  `company_review_task` on the parent; the review task completes automatically when the
  parent's review is recorded.
- **Revision loop:** `request_revision` → parent `revision_requested` → `ready`, note
  visible in `/context`; exceeding `max_revisions` → `failed` + alert to founder.
- **Unassigned tasks** without required skills → assigned by the founder in its next
  planning run (they appear in `/state`); with required skills → M4 gap detection.
- **Supervised mode** (`autonomy_mode=supervised`): tasks created by agents start
  `blocked` with `blocked_reason="awaiting_plan_approval"`, grouped per planning run into one
  Approval (`kind=plan`, approver human, scope = task ids). Approve → unblock all; reject →
  cancel all; request changes → note returned to the founder's next planning run.
  Autonomous mode (default) skips this entirely.

## 4. Governance & safety nets
Existing limits (M2) stay. Add:
- `max_planning_runs_per_hour` (4), `max_open_tasks` (200) — task creation beyond → 409
  `limit_exceeded`.
- **Loop guard:** an agent creating a task whose normalized title + assignee duplicates an
  open task → returns the existing one (no new task).
- **Circuit breaker:** ≥ `policy.failure_circuit_threshold` (default 5) failed runs in
  15 min company-wide → company auto-paused, critical alert, audit `company.auto_paused`.
- **Alerts** table: `severity` (`info`,`warning`,`critical`), `kind`, `title`, `detail`,
  `related_*`, `resolved_at`, `resolved_by`. Created for: circuit breaker, budget thresholds
  (M6), revisions exhausted, hiring blocked by limits, runtime unavailable for active
  agents. Owner API list/resolve; included in overview and `/state`.
- The founder can never change policy, budgets, or permissions (existing guard) and never
  decides approvals reserved for humans.

## 5. Company memory
Table `memory_entries`: `kind` (`fact`,`decision`,`requirement`,`lesson`,`research`),
`title`, `content` (markdown, ≤ 8 KB), `status` (`proposed`,`verified`,`rejected`,
`superseded`), `source_type/id` (task, run, artifact, decision, human), `proposed_by`,
`verified_by_type/id`, `verified_at`, `supersedes_id`. Agents propose; founder or human
verifies; only `verified` entries appear in agents' `/context` and `/state` (with source).
Company vision/goal/constraints are always included from the Company record. Secrets are
rejected (reuse secret detection on content: patterns for API keys/tokens). Owner API:
list/filter, create (human entries are verified), verify/reject, supersede.

## 6. Built-in routines (created disabled except heartbeat; owner enables)
`daily-status-report` (founder, 18:00 company tz: write a status artifact),
`daily-budget-review` (founder, 09:00), `weekly-strategy-review` (founder, Mon 09:00),
`project-health-check` (founder, every 6 h). Templates in `backend/app/roles/routines.yaml`.

## 7. Owner API & UI
- `POST /founder/wake` (manual "run planning now"), `GET /founder/summary` (current goal,
  active plan = open projects/tasks created by the founder, recent decisions, blockers,
  open alerts, last planning run + result).
- `GET/PATCH /memory`, `GET/POST /alerts/{id}/resolve`.
- UI: Goals & plan widget shows founder summary; Founder panel; Alerts strip on the canvas
  top; Memory panel; supervised-mode plan approval in the Approvals widget.

## 8. Tests (deterministic)
Extend `ScriptedRuntime` so a script step can perform agent API calls in-process with the
run's token (e.g. `{"call": "POST /tasks", "body": {...}}`), so orchestration is testable
without an LLM.
- Heartbeat coalescing, rate limit, paused company never wakes, event-trigger debounce.
- Goal → founder planning run creates project + tasks with assignees → dispatch → work runs
  complete → reviews created and accepted → project progress updated.
- Subtask waiting and re-ready; not_completed → failed attempt.
- Review loop with revision limit; supervised plan approval/reject/changes.
- Loop guard; circuit breaker auto-pause; alerts creation/resolution.
- Memory: proposal, founder verification, secrets rejected, only verified in context.
- Restart recovery mid-plan (stale runs) keeps idempotency (no duplicate planning tasks).
- **Live north-star run (manual, reported honestly):** local Ollama, goal set → founder
  plans → at least one hire or assignment → specialists produce artifacts → founder reviews.
