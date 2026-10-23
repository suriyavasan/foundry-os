# M6 — Finance & governance — spec

Builds on M2's ledger (reservations, usage records, approvals). Rules: money is integer
micro-USD; `cost_kind` ∈ `verified`/`estimated`/`unknown`; never present estimates as
charges; enforcement is code; agents never raise their own budgets.

## 1. Enforcement
- **Before dispatch** (M2): atomic reservation across company/agent/project/task limits.
- **Mid-run checkpoints:** runtimes emit cumulative `usage` events. The worker converts them
  to cost (provider price table when the runtime gives tokens only → `estimated`) and, when
  run cost reaches 80% of its reservation, tries to **extend** the reservation by another
  increment (same atomic path). If the extension fails → cancel the run with
  `exit_reason="budget_exhausted"`, task → `blocked` (`blocked_reason="budget_exceeded"`),
  alert. Document the limitation: a single model call can overshoot by at most one call's
  cost; providers that only report usage at the end can overshoot by the remaining run.
- **Paid tools/services:** policy `paid_tool_limits` `{tool_or_service: {daily_micros,
  monthly_micros}}` checked by the agent API when an agent requests an
  `external_purchase` approval or reports an external expense.
- **External expenses:** table `external_expenses` (`vendor`, `description`,
  `amount_micros`, `recurrence`, `approval_id`, `agent_id`, `project_id`, `incurred_at`,
  `cost_kind` — `verified` only when entered/confirmed by a human or a billing
  integration). Counted in budgets and finance views. Created by humans, or from a consumed
  `external_purchase` approval.

## 2. Free-first model routing
Policy `model_routing`: `free_first` (default), `pinned`, `quality_first`.
- `free_first`: agents without an explicit model use the company's enabled local/free
  providers (Ollama, or providers marked `is_free`). Fallback to a paid provider only if
  `allow_paid_fallback` is true, the paid provider is enabled and within budget; if the
  policy lists `paid_model_fallback` in `human_approval_required_for`, the run is not
  started — an approval is requested and the task blocked until approved (approval is
  single-use per task).
- Routing decision recorded on the TaskRun (`routing_reason`).

## 3. Thresholds & notifications
Policy `budget_alert_thresholds` (default `[0.5, 0.8, 1.0]`) for company and agent
daily/monthly budgets → Alert once per (scope, period, threshold) — idempotent.

## 4. Reporting API (`/api/companies/{cid}/finance/...`, company timezone, periods defined
as [start, end) in company tz and echoed in responses)
- `GET /finance/summary?period=day|week|month&date=YYYY-MM-DD` → totals split by cost_kind,
  budget utilization (company, per agent), token totals, wasted cost (failed/cancelled/
  timed_out runs), external expenses.
- `GET /finance/breakdown?period=&by=agent|project|provider|model|runtime`
- `GET /finance/trend?granularity=day|week&from=&to=` (zero-filled series per cost_kind)
- `GET /finance/runs?…` cost per run listing.
- `GET/POST /finance/expenses`.
- Approval inbox (exists) gains filters and the request fields shown in UI: requesting
  agent, business reason, expected cost, vendor, recurrence, risk, evidence.

## 5. UI
Spend widget (today/month vs budget per cost_kind, threshold markers), Finance panel
(period switcher, breakdowns, trend chart per cost_kind with estimated clearly hatched/
labelled, wasted cost, expenses table + add expense), Approvals panel (full request detail,
approve/reject/request changes with note, history), budget editors in Settings → Policy and
agent Budget tab showing utilization.

## 6. Tests
Mid-run extension success/failure + cancellation; paid-fallback routing with and without
approval; threshold alerts idempotent; period boundaries in non-UTC timezones; wasted cost
accounting; duplicate usage events; approval rejection blocks; agent cannot create
expenses beyond limits or approve its own; concurrent reservations (existing).
