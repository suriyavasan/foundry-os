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

- **Status:** done (2026-10-31), see `docs/specs/m22-human-work-and-outsourcing.md` (M22) · **Size:** L · **Area:** HR agent (Harper), People ops panel
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
- **Open questions (answered in M22):** Fiverr and Upwork first (plus "Other"); freelancers appear in the HR/People ops panel and in
  the org List view under "Contractors", not on the map. Built as "draft brief, a person approves, posts and records": no
  marketplace automation, no payment by Foundry.

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

- **Status:** done (2026-10-31), see `docs/specs/m23-intensity.md` (M23); verified with tests only · **Size:** M · **Area:** top-right of the shell, policy/scheduler
- **Ask:** A slider in the top-right that sets how hard all agents work.
  - **Low:** conserve — fewer autonomous runs, cheaper models, longer idle gaps, lower spend.
  - **High:** continuous — agents keep picking up work non-stop, spend more.
- **To design:** what each step maps to (scheduler cadence, watchdog wake frequency,
  max concurrent runs, model tier, auto-approve thresholds). Must stay inside the hard
  budget caps — the slider changes pace, never raises budgets.
- **Constraints:** Founder/admin only; agents cannot move it (rule 2). Changes audited.

## B6. Multiple model API keys and per-task routing

- **Status:** merged into B10 (M17 AI stack) · **Size:** L · **Area:** AI providers settings, worker
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

## B9. Onboarding: "Sign in to Claude" through the official Claude Code

- **Status:** done (2026-10-28) as an option in the AI stack (B10); not yet verified with a real sign-in; spec `docs/specs/b9-claude-plan.md` · **Size:** L · **Area:** onboarding,
  `backend/app/runtimes/cli/claude_code.py`, `services/cli_runtimes.py`, company-agent runtime
- **Ask:** let a founder with only a Claude subscription run the company: Foundry sets up Claude Code
  and the founder signs in once with their Claude account, used for (ideally) all agents.
- **What the rules say** (code.claude.com/docs/en/legal-and-compliance and /authentication, read 2026-10-28):
  - Allowed: an end user signs in to the **unmodified** Claude Code binary with their own subscription,
    including where a product preinstalls or runs Claude Code. Conditions: the product's publisher agrees to
    Anthropic's Commercial Terms; the binary is installed and run as published, no auth method removed or
    disabled; each user authenticates with their own API key / plan / 3P provider and is billed directly
    (no paying for, reselling or intermediating usage); sign-in completes through Anthropic's own flow.
  - Not allowed: offering Claude.ai login in our own app, routing requests through Free/Pro/Max credentials
    on users' behalf, or collecting / storing / intermediating Claude.ai credentials or session tokens.
    Agent-SDK products must use API keys.
  - "Advertised usage limits for Pro and Max plans assume ordinary, individual usage"; Anthropic may enforce
    without notice. A 24/7 autonomous company is the riskiest kind of use for a personal plan.
- **Plan:**
  1. Install, don't bundle: onboarding installs Claude Code from Anthropic's official channel (npm package
     via the existing confirmed install endpoint, or the official installer shown for the user to run), so it
     stays "as published" and self-updates. Do not vendor the binary in our bundles, Docker image or formula
     (proprietary licence; we would also be shipping a modified/pinned copy).
  2. Foundry-owned config dir: run Claude Code with `CLAUDE_CONFIG_DIR=<data>/claude-code` so the company's
     login is separate from the person's own `~/.claude` (separate Keychain entry on macOS).
  3. Sign-in = Claude Code's own flow: spawn `claude auth login` (with that config dir); it opens Anthropic's
     page and receives the callback on its own localhost server. On SSH/headless, `foundryos claude login`
     execs `claude auth login` attached to the user's terminal, so the paste-code step goes straight to
     Claude Code, never through our UI or API.
  4. Status only via `claude auth status` (exit code + JSON `authMethod`). Never read `.credentials.json` or
     the Keychain, never use `claude setup-token` / store `CLAUDE_CODE_OAUTH_TOKEN`, never log tokens.
  5. Work agents on `cli:claude_code` use that login (the existing `own_login` mode, re-pointed at the
     Foundry config dir; no `--bare`, which is API-key only).
  6. "For everything" (management too) is a separate step: management agents would also have to run as
     `claude -p` with the company tools over MCP (guardrails stay server-side). Our own company-agent runtime
     calling the API with the subscription is **not** allowed. Ship it behind an explicit "Use my Claude plan
     for management too" choice with the usage-limit warning; recommend an API key for heavy autonomy.
  7. Usage limits: detect Claude Code's usage-limit errors, pause CLI work until the reset time and say so;
     default the intensity slider (B5) to low on a subscription; never show subscription runs as charges (B8).
  8. Wording: "runs Claude Code" is fine in plain text; no Claude/Anthropic names or logos in our product or
     feature names. Owner action: accept Anthropic's Commercial Terms before shipping this.
- **Open questions:** how far "ordinary, individual usage" stretches for an always-on company (ask Anthropic
  sales before marketing subscription mode for management); Windows support.

## B10. One AI stack (M17)

- **Status:** done (2026-10-28), spec `docs/specs/m17-ai-stack.md`; plan sign-ins not yet exercised live · **Size:** L
- **Ask:** the AI settings were confusing (management model / work model / coding CLI / engine). Replace them
  with one list of any number of AIs of any type — Claude plan (Claude Code), Anthropic / OpenAI / OpenRouter /
  OpenAI-compatible / Ollama, Cursor (plan or key), Codex, OpenCode, Antigravity — that the owner orders
  (primary, secondary…), sets "use for" (heavy lifting / everyday) and importance (most … backup only), and
  edits any time, in onboarding and Settings. Legal ways only: Claude via the official Claude Code sign-in or the
  Anthropic API; one plan sign-in per CLI per company.
- Subsumes B6 (multiple keys + routing) and B9's engine switch.

## B11. Install every coding CLI from the UI

- **Status:** done (2026-10-28), not yet released; real installers never run during development · **Size:** M
- **Ask:** install any supported CLI (Claude Code, Codex, OpenCode, Cursor, Antigravity) from the UI.
- **Done:** each CLI has install methods: npm and/or the vendor's official installer script (URLs from the vendors'
  docs, see `docs/cli-runtimes.md` "Install"). Scripts are downloaded by Foundry over HTTPS from one allowlisted
  host, SHA-256 recorded, run as `[bash|sh, file]` (no pipe, no sudo) after the owner confirms the exact command.
  Settings > AI has "Coding tools on this computer". Codex and OpenCode don't document where their scripts put the
  binary; if it lands outside the searched dirs the tool shows "not installed" until it is on PATH.
