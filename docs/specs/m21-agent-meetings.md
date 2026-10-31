# M21 — Agent meetings: agents plan together without routing through the Co-founder

Status: backend built (2026-10-30; scripted and fake-runtime tests only, never run with a live model); web pending. Deviations from the plan are in "Implementation notes" below. Builds on M18 Part B (LangGraph work graph + `SqlCheckpointSaver`). Rules: `AGENTS.md`.

## Goal
Any agent can call a meeting with 1–3 other agents (any participant can call follow-up meetings). They discuss in
turns, agree on decisions and action items, and the outcome becomes Decisions and tasks owned by the participants —
no round-trip through the Co-founder. The founder sees every meeting (agenda, transcript, outcome) and the org map
draws a line between agents in a meeting.

## Data (migration `0021_agent_meetings`, portable)
* `meetings` (tenant): `id, company_id, organizer_agent_id, title (≤200), agenda (≤2000), expected_outcome (≤500),
  project_id?, task_id? (the task that prompted it), status (scheduled|running|concluded|cancelled|failed),
  start_at, started_at?, ended_at?, round (int), max_rounds (int, 2 default, ≤3), summary (Text?), outcome (JSON:
  {decisions:[{title, rationale}], action_items:[{id, title, description, owner_agent_id, accepted: bool,
  task_id?}]}), cancel_reason?, created_at, updated_at`.
* `meeting_participants`: `meeting_id, company_id, agent_id, role (organizer|participant), joined (bool), passes
  (int)`; PK `(meeting_id, agent_id)`.
* `meeting_messages`: `id, company_id, meeting_id, agent_id, round, body (≤ 4000), proposals (JSON: action items
  proposed / accepted ids), run_id?, created_at`.
* Task kind `meeting` (new enum value): one turn = one task assigned to the speaking agent, `parent` none, linked by
  `meeting_id` (new nullable `tasks.meeting_id`). Hidden from work boards/lists by default (filter `kind != meeting`
  where boards list work), shown in the meeting view.
* Policy: `meetings_enabled` (default true), `max_meetings_per_agent_per_day` (3), `max_meetings_per_company_per_day`
  (12). Owner/admin only, audited.

## Agent tools (company_agent tools + MCP server for CLIs; every role)
* `company_schedule_meeting(title, agenda, participant_agent_ids[1..3], expected_outcome, start_in_minutes=0
  (0..10080), project_id?, task_id?)` → `{meeting_id, status}`. Rules (service, enforced in code): participants are
  active agents of the same company, distinct, not the organizer; limits above (429 `meeting_limit` with "decide
  yourself or add it to an existing meeting"); an agent may have at most 1 running meeting as organizer; no
  meeting while the company is paused/emergency-stopped.
* `company_meeting_say(meeting_id, message, propose=[{title, description, owner_agent_id}], accept=[action_item_ids],
  pass_turn=false)` — only valid inside that agent's own meeting-turn run (run token bound to the turn task);
  records one message; ends the turn (runner hard-stops like complete_task).
* `company_cancel_meeting(meeting_id, reason)` — organizer only.

## The meeting graph (LangGraph thread `meeting:<id>`, same checkpointer as M18)
```
open ─► round(r) ─► [turn(agent_1) ─► … ─► turn(agent_n)] ─► more rounds? ─► synthesize ─► commit ─► close
```
* **open** (at `start_at`, by the scheduler sweep): status running; posts `meeting.started`.
* **turn(agent)**: creates a `meeting` task for the agent (priority high, budget = the agent's own budget; normal
  routing, dispatch, slots and budget reservation — nothing bypasses the engine) and **interrupts**. The turn's
  run gets the meeting context: agenda, expected outcome, participants (names/roles), the transcript so far
  (last 30 messages, each ≤ 4000), current proposals with their ids, and the instruction to speak once with
  `company_meeting_say`. When the turn task finishes (or fails/times out → counted as pass), the graph resumes.
  Turns are sequential within a round (the transcript is the shared state); order: organizer first, then
  participants in invitation order.
* Early end: after a full round where everyone passed or every proposal has been accepted/declined by its owner,
  skip remaining rounds.
* **synthesize**: one turn for the organizer with the whole transcript: produce summary, decisions, final action
  items (each owned by a participant). Uses `company_meeting_say` with a `final=true` flag carrying
  `summary`, `decisions`, `action_items`.
* **commit** (deterministic code, no model): for each final action item whose owner **accepted** it during the
  meeting (owner is the organizer → implicitly accepted): create a task assigned to the owner with
  `created_by` = owner agent, `origin={"meeting_id"}`, project from the meeting, through the normal task service
  (caps, duplicate check, project rules apply; refusals are recorded on the item, not fatal). Unaccepted items are
  listed in the summary as "not agreed". Decisions → `Decision` rows (made_by organizer, context meeting id).
  Wakes the owners. Audited `meeting.concluded`.
* **close**: status concluded, `ended_at`; event `meeting.updated`; summary visible to the founder.
* Watchdog: a meeting running > 2 h or with a turn stuck > 30 min → the stuck turn is counted as a pass; a meeting
  that can't progress → `failed` with reason; never a human alert by itself.

## Human API (read for members; cancel for owner/admin)
`GET /companies/{cid}/meetings?status=` (`{items,next_cursor}`), `GET /meetings/{id}` (with participants, messages,
outcome), `POST /meetings/{id}/cancel`. SSE `meeting.updated`. Agents' meeting messages are agent-authored text:
rendered as markdown without raw HTML, like other agent output.

## Web
* Live activity shows running meetings ("Meeting · Ana, Raj, Lee · Plan the launch · round 2/2").
* Meeting panel (`kind: 'meeting'`): agenda, participants (faces), transcript as a chat (same bubble components as
  M20, each agent's face), outcome (decisions, action items with task links / "not agreed"), cancel (admin).
* Org map (M20): dashed animated lines between agents in a running meeting.
* Agent panel: "Meetings" list (recent 10).

## Security / limits
* An agent can only speak in its own turn (run token ↔ turn task ↔ meeting), cannot add itself to meetings, cannot
  create tasks for agents who did not accept, cannot exceed task caps, and every turn is a normal budgeted run.
* Transcript content from other agents is passed as agent-authored context, never as instructions from the
  founder (wrapped as `<untrusted source="meeting">` like other agents' work).

## Tests
Scheduling rules (participants, limits, paused company), turn order and interrupts/resume via the checkpointer,
early end, pass on failed/timeout turn, commit creates tasks only for accepted items with the owner as creator and
respects caps, decisions recorded, cancel by organizer/admin only, tenancy (other company 404, cross-company
participant refused), meeting tasks hidden from work lists, say outside own turn refused.

## Implementation notes (backend)
* Graph: `app/engine/graph/meeting_graph.py` (`open -> turn* -> end_round -> synthesize -> commit -> close`, thread `meeting:<id>`,
  `SqlCheckpointSaver`, `durability="sync"`). Service: `app/services/meetings.py`. Driven by `meetings.advance` in every scheduler tick
  (2 s); the hook on a finished turn task is `autonomy_hooks.on_transition` -> `meetings.on_turn_task_terminal`, which only emits
  `meeting.updated` inside the caller's transaction (the graph is never run inside another transaction; the next tick resumes it).
* A turn is a task of kind `meeting` (`tasks.meeting_id`; priority high; no project, no budget of its own; idempotency key
  `meeting:<id>:<turn|synthesis>:<round>:<index>`). A failed / cancelled / blocked / 30-minute-old turn is a pass (recorded as a
  `[no contribution: ...]` message) and is never retried (`tasks.finish_failure`). Tasks created by `commit` also carry `meeting_id`
  (their origin); lists filter on `kind != meeting`, not on `meeting_id`.
* The turn context is built when the run starts (`meetings.turn_brief`) and travels as `spec.task.meeting` (company_agent) /
  `PromptInputs.meeting_brief` (coding CLIs); a turn offers only `company_meeting_say`, `company_get_context`, `company_find_colleagues`
  and `company_schedule_meeting` (`agent_toolkit.run_company_tools`).
* Additions to the spec: `decline=[item ids]` on `company_meeting_say` (an owner can refuse its item; early end needs it), a
  `position` column on `meeting_participants`, `kind` / `task_id` on `meeting_messages`, `agent_id` filter on `GET /meetings`,
  `meeting_id` on the agent live state. Failure reasons are stored in `cancel_reason`.
* Watchdog: inside the sweep (not `autonomy_watchdog`): turn > 30 min or blocked -> cancelled (a pass); meeting > 2 h -> `failed`;
  three graph errors in a row -> `failed`. Never a human alert. Meetings of a paused company wait; the emergency stop skips the tick.
* Commit: items are created through `tasks._create_task_core` with a run-shaped record of the owner, so the owner's role rules
  (a lead needs a project it leads), the per-run cap, the open-task cap, duplicates and supervised-mode plan approval apply; a
  refusal is stored on the item (`refused`). Only management-role owners are woken (specialists simply get the task).
