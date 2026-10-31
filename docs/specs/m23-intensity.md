# M23 (backlog B5) — Company intensity: one control for how hard the company works

Status: done (2026-10-31); verified with tests only (never against a live company run for hours). Rules: `AGENTS.md`. The slider changes **pace, never budgets**.

## Levels

`company_policies.intensity` (int 1–5, default 3; migration `0023_intensity`) plus `intensity_custom` (bool,
default false). Setting a level writes the preset into the underlying policy fields in one audited change
(`policy.intensity_changed`, with before/after values). Editing any of those fields by hand afterwards sets
`intensity_custom=true` (UI shows "Custom"); choosing a level again re-applies the preset.

| Field | 1 Conserve | 2 Light | 3 Balanced | 4 Busy | 5 Continuous |
| --- | --- | --- | --- | --- | --- |
| `heartbeat_interval_seconds` | 7200 | 3600 | 1800 | 900 | 300 |
| `max_concurrent_runs` | 1 | 2 | 2 | 3 | 4 |
| `max_planning_runs_per_hour` | 2 | 4 | 6 | 10 | 15 |
| `initiative_enabled` / `initiative_interval_seconds` | off | on / 12 h | on / 6 h | on / 3 h | on / 1 h |
| `meetings_enabled` / `max_meetings_per_company_per_day` | off | on / 4 | on / 12 | on / 20 | on / 30 |
| `self_check_enabled` | off | on | on | on | on |
| `radar` routine cadence (if a policy field exists) | weekly | weekly | daily | daily | daily |

Use the real field names in `backend/app/models/policy.py` (map each row to what exists; skip rows with no field
and say so). `management_reserved_slots` stays ≥ 1 and < `max_concurrent_runs` when concurrency > 1.

Never changed by intensity: any budget or spend limit, purchasing bands, approval rules or timeouts, guardrails,
headcount, the AI stack.

## Guardrails
* Owner/admin only (service check), audited; agents cannot read-modify it through any tool (agent policy writes
  are already refused — add a test).
* **Plan-only stacks**: when every enabled AI connection is a personal plan (Claude/Cursor plan), levels 4–5 show a
  warning ("Personal plans assume ordinary individual use; high intensity may hit usage limits — add an API key
  for heavy autonomy") and require `confirm: true` in the request (409 `intensity_confirm_required` otherwise).
  New companies whose stack is plan-only start at level 2.
* Budgets still stop work at their caps; the UI says so under the slider ("Budgets still apply").

## API
`GET /companies/{cid}/intensity` → `{level, custom, preset: {field: value}, effective: {field: value},
plan_only: bool, warning?: str}`; `PUT /companies/{cid}/intensity {level, confirm?}` → same shape. Policy GET
includes `intensity` and `intensity_custom`.

## Web
Top-right of the shell (`features/canvas/TopBar.tsx`, next to the pause control): a compact 5-step slider/segmented
control with the level name ("Balanced") and a popover showing what the level means in plain words (check-ins
every 30 min, up to 2 agents at once, …), the plan-only warning and "Budgets still apply". Viewers/members see the
level read-only. Settings → policy shows the same control above the individual fields and marks "Custom".
Keyboard accessible (arrow keys), audited changes appear in activity.

## Tests
Presets applied exactly, custom flag on manual edit, re-apply clears custom, budgets untouched (assert every
budget field unchanged across all levels), owner/admin only, agent cannot change it, plan-only confirm rule and
default level 2, scheduler/dispatcher actually use the new values (heartbeat cadence and concurrency read from
policy), audit before/after; web: slider renders level, popover text, confirm dialog for 4–5 on plan-only, read-only
for viewers, custom badge.
