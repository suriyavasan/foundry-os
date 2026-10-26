# M12 — Full autonomy — spec

Owner direction (2026-10-26): the company must run end to end without the human: a note becomes
goals and projects, hires happen, work is decomposed, executed, reviewed and completed, and the
company then finds its next work, with standups, weekly reviews and budget checks running on their
own. Two first-class modes: **API only**, and **Hybrid** (a model API for management, Claude Code /
Codex / OpenCode on the same machine for the heavy work). Cloud APIs are optimised first, local
models second. The human stays the Founder: limits, budgets and approvals remain theirs.

Status: in progress. Work packages WP0 to WP5 below; WP0 (schema, policy, docs) is the shared base.

## 1. Gaps found (ranked) and what closes them

| # | Gap | Fix (package) |
|---|---|---|
| 1 | Hybrid / Claude Code: `--max-budget-usd` equals the run reservation (default $0.05), so nearly every real run hits the cap, fails, and trips the breaker | CLI runs reserve `cli_run_reservation_micros` ($1 default) (WP2) |
| 2 | No shared project workspace: every run gets a fresh directory; task 2 cannot see task 1's code; a CLI that succeeds without `submit_artifact` loses its files | One git repository per project, run worktrees, serialized merges, "Changes" artifact (WP4) |
| 3 | Projects and goals never close; nothing says "what next"; no goals and no open work means idle forever | Closure loops, auto-complete, goal review, initiative wake (WP1) |
| 4 | Reviews hang: a reviewer finishing without a decision leaves the target `in_review`; a failed or retired reviewer is never replaced | Review-decision guard (WP2), re-issue ladder (WP1) |
| 5 | 429 / overload retried at once, consuming attempts, tripping the breaker, which pauses the company until a human resumes | Transient vs permanent failures, backoff, breaker auto-resume (WP2) |
| 6 | No model per role: `free_first` can move management runs to a free or local model; hybrid sends research to CLIs that have no web | Management model is never displaced; optional work model; CLI role-family scope (WP3) |
| 7 | One pool of 2 runs; a long CLI run starves management | Reserved management slots; per-agent parallelism for CLI project work (WP2) |
| 8 | Blocked tasks wake nobody | Block hook plus watchdog D2 (WP1) |
| 9 | Hiring dead ends (hiring task finished without a match; headcount full) | Watchdog D3 (WP1) |
| 10 | Retiring an agent orphans its tasks and projects | Watchdog D7 (WP1) |
| 11 | A failed planning run loses its wake reasons | Re-queue hook (WP1) |
| 12 | Over-decomposition unbounded across runs; only exact-title duplicates; unlimited agent retries | Caps, fuzzy duplicates, agent retry limit (WP2); planning tool and prompts (WP3) |
| 13 | Routines cost but do not act: heartbeat fires while any goal is active; standups only report | Attention-based heartbeat, standup digest, "take actions" templates (WP1) |
| 14 | Co-founder state lists the 25 oldest projects | Open first, recently ended next (WP3) |
| 15 | Budget blocks are silent; the task-budget block never expires | Wake the lead / manager / Co-founder on a block (WP2) |
| 16 | No code execution without Docker | Documented, not fixed here |
| 17 | CLI runs cancelled as stalled after 180 s without events | CLI stall limit at least 600 s (WP4) |
| 18 | Local 7B: one tool call per task; same state size for every model | `company_plan_project`, compact state for small contexts (WP3) |

## 2. Design

### 2.1 Schema (migration `0011_full_autonomy`, WP0)

`company_policies` (human-only; agents can never change them; every change is an audited
`policy.updated`):

| Field | Type | Default | Meaning |
|---|---|---|---|
| `work_model_provider_id`, `work_model_name` | string(36) / string(160), null | null | Optional separate model for work runs. Set together or both cleared; the provider must be an enabled provider of the company |
| `cli_role_families` | JSON list, null | null | Role families whose work tasks may run on the CLI; null = all; others stay on the company agent runtime (web tools). Values are the existing role families |
| `cli_run_reservation_micros` | bigint | 1,000,000 | Reservation of a non-free CLI run; a CLI's own spend cap derives from it |
| `management_reserved_slots` | int 0..99 | 1 | Slots of `max_concurrent_runs` that work runs may not use. Must be lower than `max_concurrent_runs` when set; the dispatcher clamps the effective value to `max_concurrent_runs - 1` |
| `max_parallel_runs_per_agent` | int 1..10 | 1 | Parallel runs of one agent (CLI work tasks in a project only) |
| `max_work_tasks_per_project` | int 1..200 | 15 | Agent-created work tasks per project |
| `max_subtasks_per_task` | int 1..50 | 5 | Child tasks per parent |
| `circuit_breaker_cooldown_seconds` | int 0..604800 | 1800 | A breaker pause resumes itself after this; 0 = human only |
| `initiative_enabled` | bool | true | Idle company: wake the Co-founder to start new work |
| `initiative_interval_seconds` | int 600..2592000 | 21600 | Idle time before an initiative wake |
| `project_auto_complete` | bool | true | The system completes a project whose work tasks are finished (at least one done) |

`tasks`: `next_attempt_at` (datetime, null; index `ix_tasks_company_status_next_attempt` on company,
status, next_attempt_at), `transient_failures` (int, 0), `agent_retry_count` (int, 0).
`task_runs`: `workspace_ref` (JSON, null: `{kind, base, commit, merged, conflict}`).
New tenant-owned table `autonomy_interventions` (`id`, `company_id`, `kind`, `subject_type`,
`subject_id`, `count`, `last_at`, `data` JSON, `created_at`; unique on company, kind, subject). It
bounds every automatic nudge and holds the heartbeat fingerprint.
Portable SQL (SQLite and Postgres); all non-null columns carry server defaults, so existing rows
need no backfill.

### 2.2 Autonomy watchdog (WP1, decision D31)

`services/autonomy_watchdog.py`, called by the scheduler every 60 s for companies that are active,
AI-ready and not emergency-stopped; each detector runs in its own transaction with its own
try/except. Every remediation goes through `_intervene(kind, subject, spacing, max_steps)`, backed
by `autonomy_interventions`, audited as `autonomy.intervened`. When a ladder is exhausted a critical
alert `autonomy_stalled` (dedupe key `stall:<kind>:<id>`) tells the human.

- D1 review stall (in_review, no open review task, over 5 min): re-issue to the reviewer, then the Co-founder, then alert.
- D2 blocked by an agent: the hook wakes the lead / manager at once; after 2 h the Co-founder; after 24 h an alert.
- D3 hire stall (waiting for a hire, no open hiring task, over 10 min): assign to the project lead's least-loaded report, else the lead (audited "skills fallback"); else wake the Co-founder.
- D4 project with a lead but no work tasks after 10 min: wake the lead twice (20 min apart), then the Co-founder, then alert.
- D5 finished-not-closed (no open tasks, at least one work task, quiet 10 min): wake the lead, then the Co-founder; with `project_auto_complete` and at least one done task the system completes the project (audit `project.auto_completed`); else alert.
- D6 goal with no open projects (over 10 min): wake the Co-founder twice ("mark achieved / abandoned or create the next project"), then alert.
- D7 retired agents: unassign their open tasks and route them to the project lead; clear the project lead (the lead-hiring safety net takes over).
- D8 unowned open work task, no project lead, over 15 min: wake the Co-founder twice, then alert.
- D9 initiative: `initiative_enabled`, no open work / review / hiring tasks, no new notes, idle at least `initiative_interval_seconds`: wake the Co-founder (trigger `initiative`) with the mission: create at most one goal with one project, or record a decision. At most 4 per day.

Hooks: blocked-by-agent wakes the lead / manager; a failed planning (wake-key) task re-queues its
"Why you were woken" lines after 120 s (max 2); the project-finished check wakes the lead whenever
no tasks are open, even with zero done; a failed review task enters D1. `needs_attention` replaces
`has_work` in the heartbeat (new notes, failed or blocked tasks, pending approvals, open alerts,
goals without projects, projects without leads; running work no longer counts); the heartbeat skips
while the attention fingerprint is unchanged since a heartbeat that took no action. A deterministic
standup digest (`services/digest.py`, no model call, at most 3 KB) is appended to routines whose
template says `"digest": true`.

### 2.3 Failures and budgets (WP2, decision D32)

`services/retry_policy.py` classifies a failure as transient (429, rate limit, too many requests,
overloaded, 529, 502 / 503 / 504, connection reset / refused, temporarily unavailable; codes
`unavailable`, `worker_lost`, `job_dead`) or permanent. Backoff has no jitter: permanent = immediate
once, then 60 s, 240 s (cap 900 s); transient = `30 * 2^(n-1)` s capped at 900 s. A transient failure
(up to 6 in a row) refunds the attempt and does not count for the circuit breaker; the dispatcher
skips tasks with a future `next_attempt_at`. An agent may retry its own task twice
(`agent_retry_count`); a human retry resets every counter. `maybe_auto_resume` resumes a company the
breaker paused (never one a human paused) after `circuit_breaker_cooldown_seconds`, at most twice per
24 h, then only a human can. A review task cannot be completed while its target is still `in_review`
(409 `review_decision_required`). Agent-created tasks obey `max_work_tasks_per_project`,
`max_subtasks_per_task`, and a duplicate check on normalised titles or token similarity of at least
0.85 against open tasks and tasks done in the last 7 days. A blocked budget wakes the project lead,
else the manager, else the Co-founder (trigger `budget`); the Co-founder still cannot raise budgets.

### 2.4 Roles and models (WP3, decision D33)

`routing.choose(db, agent, limits, runtime=None, task=None)`: management runs (task not `work`, or
a management role) always use the management model (`default_model_*`); `free_first` never moves
them unless the owner pinned the agent or the management provider is disabled. Work runs use the
work model when set (the owner's consent, like a pinned model), else `free_first` / `quality_first`.
`choose_runtime`: a work task of a role family outside `cli_role_families` stays on the company agent
runtime ("needs web"). The agent state shows open projects first, then those ended in the last 7 days
(max 25), goals carry project counts, and small or local models get a compact profile.
`company_plan_project` lets a lead create 3 to 6 tasks with dependency keys in one tool call.

### 2.5 Shared project repository (WP4, decision D34)

One git repository per project (`<workspaces>/<company>/projects/<project>/main`, branch `main`).
A project task runs in `git worktree add -b run/<run_id>`; a review task gets a read-only detached
worktree; other tasks keep a per-run directory. On finish the run is committed as the agent and, on
success, merged `--no-ff` under a per-project file lock; a conflict aborts the merge and fails the
run as `merge_conflict` (retryable; the retry starts from the new `main`). A failed run keeps its
branch. Every run produces a "Changes" artifact (diffstat plus patch, at most 200 KB). Every git call
disables hooks, GPG signing and fsmonitor, uses a minimal environment, a 60 s timeout and no shell.
Without git the engine falls back to per-run directories and reports `{"available": false}`.

### 2.6 Decomposition (decision D35)

Planning is bounded at three levels: per run (`max_tasks_per_planning_cycle`), per parent
(`max_subtasks_per_task`) and per project (`max_work_tasks_per_project`), with fuzzy duplicate
detection so a re-planning lead cannot re-create finished work. A lead plans a project once.

## 3. Work packages

| WP | Scope | Owner files |
|---|---|---|
| WP0 | Migration 0011, models, policy API, this spec, decision log | `alembic/versions/0011_*`, `models/*`, `schemas/policy.py`, `services/policy.py`, docs |
| WP1 | Watchdog, closure loops, routines, digest | `services/autonomy_watchdog.py`, `digest.py`, `autonomy_hooks.py`, `reviews.py`, `projects.py`, `wakes.py`, `builtin_schedules.py`, `schedules.py`, `engine/scheduler.py` |
| WP2 | Task engine, failures, dispatch, budgets | `services/tasks.py`, `retry_policy.py`, `safety.py`, `runs.py`, `engine/dispatcher.py`, `engine/recovery.py` |
| WP3 | Routing, AI setup, state, prompts, tools | `services/routing.py`, `ai_setup.py`, `state.py`, `agent_context.py`, `agent_toolkit.py`, runtime prompts / tools |
| WP4 | Project repository and CLI flow | `services/project_workspaces.py`, `engine/executor.py`, `runtimes/*` |
| WP5 | Web settings (work model, CLI role families, new policy fields) | `web/` |
| E2E | `tests/test_m12_e2e.py`: API mode note to goal to project to lead hire to plan to work to review to auto-complete to goal achieved to initiative; hybrid: a fake CLI writes in the shared repo and task 2 sees task 1's file | tests |

## 4. Tests

- WP0: `tests/test_m12_migration.py` (0011 up / down on SQLite with defaults on existing rows; owner can change the new policy fields; agents and viewers get 403; invalid values 422; every change audited; work model must belong to the company).
- WP1 to WP4: watchdog ladders and bounds, closure, routines, failure classification and backoff, dispatch reservations, decomposition caps, routing per role, project workspace merges and conflicts; all with `ScriptedRuntime` and fake CLI binaries.
- Whole suites: backend `uv run pytest -q`, ruff check and format; runtime `.venv/bin/python -m pytest -q` and `scripts/check_imports.py`.

## 5. Not verifiable here

No live agent or coding CLI is run on the dev machine. Not verified: cloud-model planning quality,
cost and latency, prompt-cache hit rates; the real 429 / overload text of each provider (the
classifier matches documented phrases and codes); real Claude Code / Codex cost per run against the
$1 reservation, MCP tool naming, behaviour inside git worktrees; PostgreSQL; `fcntl` on non-POSIX
systems; real concurrency under load.
