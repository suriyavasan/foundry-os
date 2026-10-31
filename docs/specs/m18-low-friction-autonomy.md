# M18 — Low-friction autonomy: decide-don't-ask + a durable work graph (LangGraph × Claude Code)

Status: Part A and Part B implemented (2026-10-30; API in `docs/api.md` "Durable work graph", decision in `docs/architecture.md`). Not verified live: real Claude Code `--resume` across a crash; Postgres. Rules: `AGENTS.md`. Audit of today's loop: see "Why" below.

## Why

The company is autonomous by default, but work still stops and waits on people far more than it needs to:

1. Approvals routed to a human never time out; nothing has a default outcome. Agents get a 202 and park.
2. Approvals routed to the Co-founder do not wake it (only the heartbeat finds them, and it can go quiet); an
   approval the Co-founder requests is routed to itself, which it may not decide, and nobody is told — a dead end.
3. The agent prompt tells every agent "anything that spends money, deploys or contacts outside people needs
   company_request_approval first", producing approvals policy never asked for (guardrails already gate those
   actions in code).
4. There is no structured way to ask the founder a question, so ambiguity becomes either a silent assumption or a
   blocked task.
5. A run that crashes, stalls or times out restarts from zero with no memory of the previous attempt.
6. `blocked` escalates slowly (Co-founder after 2 h, human after 24 h); `failed` after 3 attempts is effectively
   terminal for agents.

## Principles

* **Decide, don't ask.** A choice that can be undone is made by the agent, logged as a Decision, and visible to the
  founder, who can override it later. Only preferences only the founder knows become questions, and every question
  carries the default the agent will use.
* **Nothing waits forever.** Every wait (approval, question, block) has a deadline and a defined outcome.
* **Safety lines do not move.** Timeouts never approve anything a human was required to approve, never raise a
  budget, never spend beyond the founder's caps. A human approval that times out is *declined*, and the requester
  is woken to continue another way. Enforcement stays in code (guardrails, budgets, approvals).
* **Durable, resumable runs.** Work survives crashes and waits as a checkpointed graph, not a restart.

## Part A — friction fixes (code-enforced)

### A1. Approvals: deadlines, wakes, no dead ends
* New `approvals.expires_at` (nullable `UTCDateTime`). Set at creation for every pending approval:
  * human approver: `now + policy.approval_timeout_seconds` (new policy field, default 86400 = 24 h,
    min 3600, max 7 days);
  * Co-founder (agent) approver: `now + policy.agent_approval_timeout_seconds` (default 1800, min 300, max 86400).
  * Existing purchase-request expiry (7 days) keeps its own rule; do not change purchasing.
* Creating an approval whose approver is the Co-founder **wakes the Co-founder immediately** (system wake, reason
  `approval_pending`, 5 s debounce) — for every creation path (guardrails, `company_request_approval`, hiring).
* An approval requested by the Co-founder itself (or by any agent that would be its own approver) is routed to a
  human approver and the human is notified (fixes the dead end).
* Sweep (scheduler, every 60 s, bounded batch): pending approvals past `expires_at`:
  * Co-founder approver → re-routed to a human (approver = human, new `expires_at` from the human timeout,
    notify), once. Audited `approval.escalated`.
  * human approver → status `expired` (new value if not present) with `decision_note="No answer within <n>h; declined
    by timeout"`; never consumable. The requesting agent is woken (reason `approval_expired`) and the linked task, if
    it was waiting on the approval, returns to `ready` with a `review_note` telling the agent the action was declined
    by timeout and to finish another way or record why it cannot. Audited `approval.expired`.
* Deciding an approval (approve or decline, by anyone) wakes the requesting agent (reason `approval_decided`) if not
  already done today.
* API: `ApprovalOut` gains `expires_at`. Policy schema gains the two timeout fields (owner/admin only, audited).

### A2. Founder questions (non-blocking by default)
New model `founder_questions` (tenant-owned):
`id, company_id, agent_id, task_id?, run_id?, question (≤1000), options (JSON list[str] ≤ 6, each ≤ 120, may be
empty), default_answer (≤ 500, required), blocking (bool, default false), status (open|answered|defaulted|cancelled),
answer?, answered_by_user_id?, answered_via (web|telegram|discord|whatsapp|slack|api)?, deadline (UTCDateTime),
answered_at?, created_at, updated_at`.

* Agent tool `company_ask_founder(question, default_answer, options=[], blocking=false, deadline_hours=4)` →
  agent API `POST /agent-api/questions` (any agent; guardrails: rate limit **max 5 open questions per company** and
  **max 2 per agent per 24 h**; beyond that → 429 `question_limit` with message "Decide yourself and record it with
  company_record_decision"). `deadline_hours` clamps to 1–24.
  * `blocking=false` (default): returns immediately; the agent continues using the default and states the
    assumption in its result. If the founder later answers with something other than the default, the asking agent
    is woken (reason `founder_answered`) with the question+answer so it can adjust.
  * `blocking=true`: allowed only when the run has a task; the task waits (`blocked_reason=waiting_for_founder`,
    new enum value) until answered or deadline; in graph runs (Part B) this is a graph interrupt, otherwise the
    task returns to ready on answer/deadline and the next run receives the answer (or "no answer; use your default")
    in its context.
* Deadline sweep (same 60 s sweep): open questions past deadline → `defaulted` (answer = default), wake/resume as
  above. Audited.
* Human API (members with admin/owner role):
  `GET /companies/{cid}/questions?status=open` (list, `{items,next_cursor}`),
  `POST /companies/{cid}/questions/{qid}/answer {answer}` (must be non-empty ≤ 500; answering an option or free text),
  `POST /companies/{cid}/questions/{qid}/cancel`. Events `question.created` / `question.updated` on the SSE bus.
  Notification `founder_question` to owners/admins (in-app; M19 delivers to chat).
* The founder's answer is trusted founder input (same standing as a founder note), delivered to the agent as
  "The founder answered: …". The question text is agent-authored and shown to humans as such (never rendered as
  HTML/markdown links that could phish).
* An agent can never answer or cancel a question (human principals only).

### A3. Prompt doctrine (runtime/company_agent/prompts.py and runtimes/cli role brief / prompt)
Replace the rule "Anything that spends money, deploys or contacts outside people needs company_request_approval
first" with:
* "Spending, deploying and contacting outside people are checked automatically. Just do the work; if an action needs
  approval the system tells you — continue with everything else and finish what you can."
* "For choices you could undo later, decide yourself, record the decision with company_record_decision (what and
  why), and continue."
* "Ask the founder (company_ask_founder) only for preferences only they can know. Always give the default you will
  use, and keep going with it unless you set blocking=true."
* Keep: untrusted-data rule, never-impersonate, no secrets, cannot change own permissions/budget/policy.
* Remove "Never ask a human for input" (replaced by the bullets above). Same doctrine in the CLI prompt/role brief
  (`backend/app/runtimes/cli/prompt.py`, `role_brief.py`), including the MCP tool `company_ask_founder`.

### A4. Faster unblocking
* Watchdog blocked-task ladder: lead immediately (unchanged), Co-founder after **30 min** (was 2 h), human alert
  after **8 h** (was 24 h). Constants in `autonomy_watchdog.py`.
* `company_update_task action=block` requires `unblock_condition` (≤ 300 chars; what would unblock it). If the
  agent's block reason is a question for the founder, the tool docstring tells it to use `company_ask_founder`
  with `blocking=true` instead.
* Heartbeat skip: the "unchanged since a no-action heartbeat" skip expires after 6 h (always run at least every 6 h
  when anything needs attention).

### A5. Retries with memory
* When a run fails (any failure class) the next attempt's context carries the previous attempt's outcome: failure
  class, error message (redacted, ≤ 1000 chars), last progress report, and whether a `run/<id>` branch with commits
  exists. Stored on the task (`tasks.last_attempt` JSON, nullable) — in Part B this comes from the graph state.
* After attempts are exhausted the failure-analysis task (existing) is given to the lead (else Co-founder) with a
  new allowed outcome: **retry with a new approach** — the analyst may call `company_update_task action=retry` with
  `new_instructions` (≤ 2000), which resets the attempt counter **once per task** (tracked `tasks.replans`, max 1)
  and appends the instructions to the task description under "Revised approach". Beyond that, human only.

## Part B — durable work graph (LangGraph orchestrates, Claude Code does the work)

### B1. Dependency and persistence
* Backend depends on `langgraph` (MIT, ≥ 1.2, < 2). No LangChain model wrappers are used: nodes call our own
  runtime adapters. No LangGraph Platform / server / cloud.
* Checkpoints are stored by our own `SqlCheckpointSaver(BaseCheckpointSaver)` over SQLAlchemy in Alembic-managed
  tables (not the library's auto-created tables, which would bypass migrations and tenancy):
  * `graph_checkpoints(company_id, thread_id, checkpoint_ns, checkpoint_id, parent_checkpoint_id, type,
    checkpoint LargeBinary, metadata JSON, created_at)` PK `(thread_id, checkpoint_ns, checkpoint_id)`, index
    `(company_id, thread_id)`.
  * `graph_writes(company_id, thread_id, checkpoint_ns, checkpoint_id, task_id, idx, channel, type, value
    LargeBinary, task_path)` PK `(thread_id, checkpoint_ns, checkpoint_id, task_id, idx)`.
  * Serialization via the saver's `serde` (`dumps_typed`/`loads_typed`); company_id from
    `config["configurable"]["company_id"]` — required; a read with a mismatching company returns nothing.
  * Retention: keep the latest 20 checkpoints per thread; delete a thread's checkpoints 30 days after its task is
    done/cancelled (recovery sweep).
* Graph state must never contain secrets (tokens, keys, credentials) — only ids, counters, short summaries.

### B2. The work graph (one durable thread per task: `thread_id = "task:<task_id>"`)
```
prepare ──► act ──► check ──pass──► finish
             ▲  │      │ fail & revisions < 2
             │  │      └──────────► act (same session, with the feedback)
             │  ├─ asked(blocking) ─► wait_founder (interrupt) ─► act
             │  ├─ needs approval ──► wait_approval (interrupt) ─► act
             │  └─ blocked / failed ─► finish (existing block/fail rules)
```
* **prepare**: loads the task, the previous attempt summary (A5), founder answers, decided approvals, and
  decisions already recorded for this task; computes `attempt`, `revisions`.
* **act**: runs the agent through the routed runtime (existing `RuntimeAdapter.execute`): Claude Code / Cursor /
  other CLI or the company_agent runtime — LangGraph does not replace them. For Claude Code the run uses a
  persistent session: `--session-id <uuid>` on the first act of a thread, `--resume <uuid>` on later acts *when the
  same AI connection is routed again* (the session lives in that connection's own `CLAUDE_CONFIG_DIR`; drop
  `--no-session-persistence` for these runs). If a different connection is routed, start a new session and
  include the checkpoint summary instead. Other runtimes start fresh with the summary.
  * Graph mode changes completion: `company_complete_task` during a graph run records a **proposed result** on the
    run (`task_runs.proposed_result` JSON) and returns `{ok:true, status:"proposed"}`; the task is finalized by
    `finish`, not by the tool call. Outside graph mode behaviour is unchanged.
* **check** (self-verification): only when the act proposed completion and the task has acceptance criteria. A
  short, read-only call on the same routed connection: given the task, criteria and proposed result (and for code
  tasks the diff stat), return JSON `{pass: bool, feedback: str}`. On fail and `revisions < 2` → act again with the
  feedback appended; else → finish with `in_review` (existing review path) carrying the feedback. Pass → finish.
  Its cost is recorded like any run usage. Policy `self_check_enabled` (default true).
* **finish**: applies the outcome with the existing services (`finish_success`, block, fail rules). A task whose
  self-check passed goes to **done** unless it has an explicit reviewer or is a code task opening a PR (those keep
  the existing review). This removes one dispatch cycle per simple task.
* **wait_founder / wait_approval**: LangGraph `interrupt()`. The current `TaskRun` ends cleanly (slot freed, status
  `succeeded`, outcome `waiting`); the task waits with `blocked_reason` `waiting_for_founder` /
  `waiting_for_approval`. When the question is answered/defaulted or the approval decided/expired, the task returns
  to `ready`; its next run resumes the thread with `Command(resume=<answer|decision>)`.
* **Crash resume**: recovery's `worker_lost` re-dispatch resumes the thread from its last checkpoint (same session
  when possible) instead of starting over.
* One invocation per `TaskRun`; the run budget/timeouts/stall monitor and cancel apply to the whole invocation.
  Node transitions are recorded as `RunEvent`s (`graph.node` with node name, outcome) for the run timeline.

### B3. Rollout
* `policy.work_graph_enabled` (default **true** for new companies; migration sets existing companies to true as
  well — the graph is backward compatible). Owner can turn it off (falls back to today's one-shot runs).
* Management runs (Co-founder/HR/Lead planning) use the same graph minus `check`.

## Data / migrations
* `0018_low_friction_autonomy`: `approvals.expires_at`; policy `approval_timeout_seconds`,
  `agent_approval_timeout_seconds`; `founder_questions`; `tasks.last_attempt`, `tasks.replans`.
* `0020_work_graph`: `graph_checkpoints`, `graph_writes` (M19 uses `0019`); also `task_runs.graph_thread_id` / `task_runs.proposed_result` and the policy columns `self_check_enabled` / `work_graph_enabled` (default true; so `0018` carries only the Part A columns).

## Tests (business rules and boundaries)
Approval expiry (human → expired/declined, never approved; Co-founder → escalated once), immediate Co-founder wake,
self-request dead end fixed, question limits/deadline/default/answer wakes, tenancy on questions and checkpoints
(company mismatch reads nothing), agent cannot answer its own question, prompt no longer contains the approval rule,
graph: resume after simulated crash continues from last checkpoint, interrupt + resume with answer, check loop caps
at 2 revisions, proposed completion only finalizes in finish, Claude Code argv uses `--session-id`/`--resume`
correctly and never reuses a session across connections.

## Not verified live (to state at the end)
Real Claude Code session resume across a crash; real LangGraph behaviour under Postgres (tests use SQLite).
