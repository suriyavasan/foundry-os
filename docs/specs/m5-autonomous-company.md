# M5 — Autonomous operating model — spec (v2)

Owner direction (2026-10-24): the company is autonomous top to bottom; the AI Co-founder
exists to manage all other agents; the human Founder pours thoughts into a notes widget
and the AI Co-founder aligns the company to them. Builds on M2 (engine), M3 (runtime),
M4 (skills/HR). Guardrails referenced here are specified in M6.

Terminology: human = **Founder** (owner). AI agent with kind `founder` = **AI Co-founder**
(UI label; internal kind unchanged). Update all UI copy accordingly.

## 1. Layered operating loops

All loops are durable (schedules + job queue), idempotent (coalesced wake tasks), rate
limited (M6), and paused by the company kill switch.

### 1.1 Strategic loop — AI Co-founder
Wakes (creates a `planning` task, coalesced: at most one open; ≤ `max_planning_runs_per_hour`):
- a new **Founder's note** (debounced 60 s to batch bursts),
- goal created/changed, project done/failed/blocked, escalation from a lead, hire activated,
  approval decided, alert raised,
- schedules: `daily-standup` (every morning, company tz), `weekly-strategy-review`,
  `daily-budget-review`, plus `heartbeat_interval_seconds` safety heartbeat.

Each run reads live state (`company_get_state`) and the **unprocessed notes**, then:
1. Triage every note (§2) and act on it.
2. Maintain goals (OKR-style: goal → measurable key results in description) and projects;
   assign each project a **lead** (hire one via HR if no suitable lead exists).
3. Allocate budgets to projects/agents within the company budget (M6 enforces).
4. Create/adjust schedules (routines) the company needs.
5. Review escalations, failed work and blocked tasks → reassign, change approach, or ask HR.
6. Record decisions with rationale; write a short "what changed" summary to the Founder.

### 1.2 Management loop — department leads (`kind=lead`)
Triggers: project assigned/activated, a report's task done/failed, review due, schedule
`project-health-check`. Responsibilities: break the project into tasks with acceptance
criteria and dependencies, assign to reports by skills/load, review deliverables
(maker-checker), unblock, escalate to the AI Co-founder when stuck (`company_escalate`).
A lead may only create tasks inside projects it leads (or subtasks of its own tasks).

### 1.3 Execution loop — specialists
Task ready → run → artifacts → complete or block with reason. May create subtasks only
for itself or request help (`company_request_help` → lead), never assign to others.

### 1.4 Support loops
- **HR** (M4): hiring tasks, skill gaps, skill authoring; monthly `skills-review` routine.
- **Finance** (optional template `finance-analyst`, hired on demand): budget threshold
  alerts → cost review, recommends model routing changes (cannot change policy; proposes
  to AI Co-founder → which may request Founder approval if it raises spend).
- **Review/QA**: reviewer assignment for tasks with acceptance criteria (manager by
  default, QA agent when one exists for code/deliverables of kind `code`).
- **Ops** (template `devops-engineer`): runtime health, reachability health (M8),
  failed-run triage routine.

### 1.5 Self-healing & learning
- Failure analysis: when a task fails (attempts exhausted), the dispatcher creates a
  `review` task for its lead/AI Co-founder with the error history; options: retry with a
  different agent, different model (M6 routing), split the task, or cancel with a decision.
- Stall detection (M6) cancels runs without progress; repeated stalls → escalate.
- **Lessons**: after a failure or revision, the reviewer may propose a `lesson` memory
  entry; the AI Co-founder verifies; verified lessons are injected into relevant agents'
  context (by family/tags). HR may turn recurring lessons into a company skill.

## 2. Founder's notes (thought stream)

### Data
`founder_notes`: `company_id`, `author_user_id`, `body` (markdown, ≤ 10 KB),
`attachments` (artifact ids, optional), `status` (`new`,`triaged`,`archived`),
`triage` (json: `{category, summary, actions:[{type, ref_id}], confidence}`),
`triaged_by_run_id`, `pinned` (bool), timestamps.
`note_replies`: `note_id`, `author_type` (`human`/`agent`), `author_id`, `body`, `created_at`
— threaded conversation under each note.

Categories: `directive` (do X), `goal` (change direction), `requirement` (constraint/spec),
`idea` (consider), `feedback` (on work), `question` (Founder asks), `context` (background
info), `noise` (nothing actionable — still acknowledged and kept).

### Behaviour
- Founder posts a note → CompanyEvent `note.created` → AI Co-founder wake (debounced).
- AI Co-founder tools: `company_list_notes(status=new)`, `company_triage_note(id, category,
  summary, actions)`, `company_reply_to_note(id, body)`. Every note gets a triage and a
  reply ("Created goal G, project P led by X, scheduled weekly review" / "Noted, nothing to
  do yet"). Actions reference real created records (validated server-side).
- `requirement`/`context` notes become **verified memory** entries automatically (source =
  note, verified_by = human author) so every agent sees them in context.
- Founder can reply in the thread; replies re-open triage (wake again).
- Notes are untrusted *input* only in the sense of prompt injection from pasted content:
  the AI Co-founder treats note content as the Founder's intent, but URLs/pasted documents
  inside are data (M6 injection containment).

### UI (canvas)
The **Founder's notes** widget sits immediately **right of the org chart** (org span 8,
notes span 4, same height); Live activity moves to the next row. Widget: composer
(multiline, ⌘↵ to send, optional attach artifact), list of notes newest first with status
chip (New → Triaged with category), AI Co-founder reply preview, actions as linked chips
(goal/project/task/schedule), expand into a thread panel (`?panel=note:<id>`). Unread AI
replies badge. Notes panel (`?panel=notes`) with filters by category/status.

## 3. Live company state & tools
Agent API `GET /state` (role-scoped slices: AI Co-founder sees everything; leads see their
projects; specialists their tasks) — goals, projects (progress, lead, budget), open tasks
by status with blocked reasons, failures (24 h), approvals, budget utilisation, headcount
by family + capacity, hiring requests, decisions, verified memory, alerts, unprocessed
notes count. Toolset additions: `company_get_state`, `company_manage_goal`,
`company_manage_project`, `company_manage_schedule` (create/pause routines within policy),
`company_escalate`, `company_request_help`, `company_propose_memory`,
`company_verify_memory`, `company_list_notes`, `company_triage_note`,
`company_reply_to_note`.

## 4. Orchestration rules (deterministic, control plane)
Carried from v1: subtask waiting (`waiting_on_subtasks`), reviews as tasks, revision loop
with limits, unassigned tasks routed by skills (M4) or to the AI Co-founder,
supervised-mode plan approval. New:
- Project lead routing: tasks created in a project without assignee go to the project
  lead's queue (lead assigns on its next run).
- Escalations create an `escalation` CompanyEvent + wake the escalation target.
- One open wake task per (agent, trigger kind).

## 5. Company memory
As v1 (`memory_entries` with provenance, proposed → verified, secrets rejected, only
verified in context) plus `kind=lesson` scoping by `applies_to` (families/tags) and
note-sourced entries.

## 6. Built-in schedules
Created on company creation (enabled unless noted): AI Co-founder `heartbeat` (30 min),
`daily-standup` (09:00), `weekly-strategy-review` (Mon 09:30), `daily-budget-review`
(18:00), lead `project-health-check` (every 6 h per active project), HR `skills-review`
(monthly), Ops `failed-run-triage` (daily, only when an ops agent exists).

## 7. Owner API & UI
`POST/GET /notes`, `GET /notes/{id}` (+ replies), `POST /notes/{id}/replies`,
`PATCH /notes/{id}` (pin/archive); `POST /cofounder/wake`, `GET /cofounder/summary`
(current goals, active plan, recent decisions, blockers, alerts, last run);
memory and alerts endpoints (v1). UI: Founder's notes widget + panel, AI Co-founder panel
(summary, decisions, plan tree goals→projects→tasks), Memory panel, Alerts strip.

## 8. Tests (deterministic via ScriptedRuntime agent-API steps)
Note → wake debounce/coalesce → triage + reply + actions validated; requirement note →
verified memory in other agents' context; goal → project with lead → lead decomposes →
specialists execute → reviews → done; escalation path; failure analysis task; lesson
proposal/verification/injection; schedule creation within limits; paused company never
wakes; restart mid-plan stays idempotent. **Live run (manual, honest report)** on local
Ollama: Founder writes a note → AI Co-founder creates goal/project/tasks → work executes.
