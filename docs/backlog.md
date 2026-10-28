# Backlog — founder ideas and pending changes

Running list of ideas from the founder. Nothing here is committed to a milestone
until it moves into `docs/roadmap.md` and gets a spec in `docs/specs/`.

Status: `idea` → `ready` (scoped, can be picked up) → `in-progress` → `done` (link the commit/spec).
Size: S (≤ a day), M (a few days), L (milestone-sized, needs a spec).

---

## B1. Finance: show estimated spend first

- **Status:** done (2026-10-28) · **Size:** S · **Area:** `web/src/features/finance`
- **Ask:** Most providers never report an actual charge, so the "Charged $0.00" card
  leads with a number that is nearly always zero. Put **Estimated** first/most prominent;
  keep Charged visible but secondary.
- **Constraints:** Rule 5 still holds — estimates stay labelled "≈ … est." and are never
  presented as a charge. The same reorder applies to the "Wasted on failed runs" strip and
  per-agent budget rows.
- **Done:** Totals lead with Estimated; Charged is second and, at $0, says most providers do
  not report charges. The wasted strip, budget rows and `SpendBlock` (overview widget, agent
  profile, pre-M6 fallback) lead with the estimate. Charged is always shown, never hidden.

## B2. Per-agent budget allocation (daily + monthly)

- **Status:** done (2026-10-28) · **Size:** M · **Area:** finance / agent profile / policy
- **Ask:** The founder can set a **daily** and a **monthly** budget for every agent.
- **What exists:** `Agent.daily_budget_micros` and `Agent.monthly_budget_micros` are
  already in the model (`backend/app/models/agent.py`) and the budget-utilization view
  shows a monthly bar. Needs verification of how far daily enforcement goes.
- **Done:** enforcement already covered both periods (`services/ledger.py` reserves against
  agent daily + monthly and company daily + monthly). `/finance/summary` now returns day and
  month rows for every active/paused agent (limit null = none), so Finance → Budget utilization
  lists each agent with daily and monthly side by side and an inline Edit (owners/admins only;
  the server already refuses agents and viewers). Saving refreshes Finance.
- **Constraints:** Agents must not be able to change their own budgets (rule 2).
  Budget edits write an AuditEvent.

## B3. HR: human-work panel and outsourcing marketplace

- **Status:** idea · **Size:** L (needs a spec) · **Area:** HR agent (Harper), new HR panel
- **Ask:** HR already hires AI agents. Extend HR to cover work an agent cannot do:
  1. **Human-required work list** — a panel listing tasks that only a human can do, or
     that need human intervention (sign a form, make a call, physical errand, KYC, etc.).
     Agents flag work into it; the founder (or a team member) picks it up.
  2. **Outsourcing to freelancers** — HR can search marketplaces (Fiverr, Upwork, …) for
     simple jobs someone else can do, draft the brief, propose a price, and on approval
     hire/pay through the existing purchasing + approvals flow (M15 cards).
- **Constraints:** Every engagement and payment is gated by approval and budget in code
  (rule 9); posting a job publicly is outward-facing and must be approved. No claims the
  marketplace integration works until exercised against the real site (rule 8) — likely
  starts as "draft brief + manual post" before any API/browser automation.
- **Open questions:** Which marketplaces first? Does a freelancer appear on the org chart
  (as a contractor node) or only in the HR panel?

## B4. Horizontal organization chart

- **Status:** done (2026-10-28) · **Size:** M · **Area:** `web/src/features/canvas/OrgTree.tsx`, `lib/orgLayout.ts`
- **Ask:** Lay the org chart out left → right instead of top → down so the whole
  organization and who is working is visible at a glance (the vertical layout pushes the
  third level below the fold).
- **Notes:** Keep collapse/expand, fit-to-view and the "working" indicator. Consider a
  layout toggle (horizontal default) rather than removing vertical.
- **Done:** left to right is the default (compact cards: avatar beside name/role/state;
  `layoutOrgHorizontal` transposes the top-down layout). A toolbar button switches to
  top-down; the choice is kept per browser (`company-os.org-orientation`). Collapse/expand,
  fit and states work in both. Applies to the dashboard widget and the Organization panel.
  Not yet viewed in a browser.

## B5. Company "intensity" slider (spend vs. continuity)

- **Status:** idea · **Size:** M · **Area:** top-right of the shell, policy/scheduler
- **Ask:** A slider in the top-right that sets how hard all agents work.
  - **Low:** conserve — fewer autonomous runs, cheaper models, longer idle gaps, lower spend.
  - **High:** continuous — agents keep picking up work non-stop, spend more.
- **To design:** what each step maps to (scheduler cadence, watchdog wake frequency,
  max concurrent runs, model tier, auto-approve thresholds). Must stay inside the hard
  budget caps — the slider changes pace, never raises budgets.
- **Constraints:** Founder/admin only; agents cannot move it (rule 2). Changes audited.

## B6. Multiple model API keys and per-task routing

- **Status:** idea · **Size:** L (needs a spec) · **Area:** AI providers settings, worker
- **Ask:** During setup, allow adding several API keys — Anthropic, OpenRouter, OpenAI,
  or even two keys for the same provider from different accounts. Once configured, the
  system decides which route each task takes.
- **What exists:** `ModelProvider` rows with their own `Credential`, prices and
  `default_model` (`backend/app/models/provider.py`). Need to check whether several
  providers of the same kind are allowed and how a run picks one today.
- **To design:** routing policy — by task type / required capability, by cost, by agent,
  failover on rate-limit or outage, and spreading load across accounts. Show the chosen
  route on each run. Ties into B5 (low intensity → cheaper route).
- **Constraints:** Keys never reach the frontend, logs or agent-visible config (rule 4).

## B7. Cursor CLI as a work runtime

- **Status:** done (2026-10-28), fake-binary tested only · **Size:** M · **Area:** `backend/app/runtimes/cli/`, AI setup UI
- **Ask:** Add the Cursor CLI as an option alongside Claude Code, Codex, OpenCode and
  Antigravity, so an employee's work can run through it.
- **What exists:** the M10 CLI adapter framework (`docs/cli-runtimes.md`): per-CLI argv
  builder, HOME isolation, `api_key` vs `own_login` modes, install endpoint, connectivity
  test, hybrid routing, the company MCP server for the agent tools.
- **To do:** a `cli:cursor` adapter (non-interactive flags, JSON event stream, model flag,
  sandbox/permission flags, how it takes an API key), install + connectivity test, an
  entry in the setup/runtime picker, and a "Verified flags" section in `docs/cli-runtimes.md`.
- **Done:** `cli:cursor` (`backend/app/runtimes/cli/cursor.py`), coded from cursor.com/docs/cli
  (read 2026-10-28); see "Cursor CLI" in `docs/cli-runtimes.md` for argv and gaps. `api_key`
  only — `own_login` refused (login location undocumented). Install is manual (`curl … | bash`
  is shown, never run). **Not run against the real binary**; first live check: does `--force`
  honour the `Shell(*)`/`WebFetch(*)` deny rules.
- **Constraints:** flags taken from the real binary's `--help` / official docs, never
  guessed; record exactly what was and wasn't run (rule 8). Same terms-of-use warning for
  `own_login`. The key only in the child process environment (rule 4).

## B8. Dashboard: CLI usage container

- **Status:** done (2026-10-28) · **Size:** M · **Area:** canvas dashboard widget, finance/usage
- **Ask:** A container on the dashboard showing how much each coding CLI is being used —
  Claude Code, Codex, Cursor (B7), and the others.
- **Show per CLI:** runs (today / this month), success vs failed, tokens in/out, time
  spent, cost split by provenance (estimate first, per B1), and which agents use it; also
  whether each CLI is installed / connected and in `api_key` or `own_login` mode.
- **Notes:** `own_login` (subscription) runs record no charge — show their run and token
  counts with a "subscription — not billed here" label, never a $0 charge. If a CLI
  reports subscription quota/limits, show them only when actually reported. Usage data
  likely already exists per run (`runtime` on runs/usage, finance breakdown supports
  `group_by=runtime`), so this may be mostly a widget over existing endpoints.
- **Done:** `GET /finance/cli-usage` + "Coding CLIs" dashboard widget. Subscription runs are
  detected from the run's final `usage` event (`billing.subscription`). Tested with mocks only,
  not yet viewed against a live backend.
- **Constraints:** every state handled (loading, empty, error, not available).
