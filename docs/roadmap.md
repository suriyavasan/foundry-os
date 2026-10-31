# Company OS — Roadmap (v3)

Revised 2026-10-24 with the owner's expanded direction:

- **Autonomous top to bottom.** Strategy, management, execution and support all run as
  agent loops. The human **Founder** does not micromanage; the **AI Co-founder** (internal
  kind `founder`, displayed as "AI Co-founder") exists to manage every other agent.
- **Founder's notes.** A widget right of the org chart where the human pours in any
  thought (relevant or not). The AI Co-founder triages every note and turns it into goals,
  tasks, schedules, requirements or memory — and replies with what it did.
- **Guardrails are part of the agent framework**, enforced in code at every layer.
- **Login & team.** Username + password at onboarding; invite other humans by email; humans
  can sit inside the org tree and take tasks like any agent.
- **Reachability & identity.** The landing screen shows how to reach the control UI on the
  local network and on the public internet (tunnel, free domain, optional Vercel hosting);
  the company gets its own email identity.
- **Our own agent layer** (Hermes fork in `runtime/`), **skills per role + HR agent**,
  **single-canvas UI**, **an OS with CLI**.

North-star: *the Founder signs up with a username and password, writes a few thoughts →
the AI Co-founder sets goals, hires the skills it lacks and schedules the work → the team
executes real tasks → the Founder watches progress, artifacts and costs on one canvas,
reachable from anywhere — without approving each step.*

| # | Milestone | Outcome | Status |
|---|---|---|---|
| M1 | Foundation | Architecture, AGENTS.md, backend + web skeleton, company/org persistence | done |
| M2 | Work engine | Goals, projects, tasks, runs, events, artifacts, policy, budget ledger, approvals, schedules, job queue, worker, SSE, agent API | done |
| M3a | Agent runtime (Hermes fork) | `runtime/` fork, run protocol, company toolset, file jail, live on local Ollama | done |
| M3b | Runtime integration | Adapter, model providers, encrypted credentials, HR tools in runtime, local-owner auth gate, real E2E task | done |
| M4 | Skills & HR | Skill library (Hermes + GitHub), scan/quarantine, role templates, HR agent, hiring, gap detection; skills UI | done |
| M5 | Autonomous operating model | Layered loops (strategic/management/execution/support), Founder's notes + AI Co-founder triage, event triggers, self-healing, lessons, company memory, schedules | backend + runtime done (UI done earlier); live run on a local 7B only partly autonomous - see status notes |
| M6 | Guardrails & governance | Action risk tiers + policy engine, rate limits, maker-checker for external actions, outbound DLP, injection defenses, stall/runaway detection, emergency stop, mid-run budgets, free-first routing, finance views, guardrail audit | done |
| M7 | Accounts, login & team | Username/password onboarding, sessions, roles, email/link invitations, humans in the org tree with assignable tasks, notifications | done |
| M8 | Reachability & identity | Landing "how to reach" (LAN + public), tunnel, free-domain flows, optional paid domain (approval), Vercel-hosted control UI, company email identity, connected-accounts vault | done |
| M9 | OS packaging & hardening | `companyos` CLI, single-port serve, Docker Compose, backups, security review, final verification | done for the local install (CLI, `companyos up`, single-port serve + CSP, backup/restore, security review, `scripts/verify.sh`: 10/10 items passed on 2026-10-24 on macOS + SQLite + local qwen2.5:7b); **Docker image/Compose and PostgreSQL written but not verified** - see "Final verification" |
| M10 | AI setup, coding-CLI workforce, Reflections, Command Center | Mandatory working-API onboarding gate (OpenRouter / Anthropic / OpenAI / OpenAI-compatible); optional Claude Code / Codex / OpenCode work runtimes (API manages the company, CLIs do the work) with isolation + MCP company tools; animated single-screen onboarding; Reflections + human-set company standards; Command Center where every input gets an output | done (live: OpenCode + local model; Claude Code/Codex not run live) |
| M11 | Foundry AI OS: easy install | Rename to Foundry AI OS (`foundryos`); self-contained release bundles (own Python, prebuilt UI); Homebrew tap `suriyavasan/tap/foundry-os`; one-line `install.sh`; Docker image on GHCR; background service; `foundryos update`/rollback with automatic pre-migration backups; release CI | released v0.1.0 (2026-10-24) |
| M12 | Full autonomy | The company runs end to end without the human: autonomy watchdog and closure loops (reviews, blocks, hires, project/goal completion, initiative), transient-failure backoff and breaker auto-resume, per-role models and CLI role-family scope, shared per-project git repository for hybrid work, decomposition caps; spec `specs/m12-full-autonomy.md` | done (verified with scripted runtimes and fake CLIs only) |
| M13 | Headless setup and a free public URL | `foundryos setup` (the whole onboarding in a terminal, resumable, scriptable with `--non-interactive` and `--secrets-stdin`); no browser on SSH or display-less boxes; Linux linger and macOS `user/` launchd fallback with LaunchDaemon hints; the Cloudflare quick tunnel as the free public URL with stored, audited, announced URL changes (`reach --watch`, `reach public start\|stop`, `public-url.json`); no passkeys on ephemeral origins; spec `specs/m13-headless-setup-and-public-url.md` | done (verified with fakes only) |
| M14 | GitHub-backed projects | Onboarding asks for a new or an existing GitHub repository (fine-grained token, machine-user recommended); the project repository becomes a clone the control plane syncs (fetch, merge-not-rebase, never force-push); agents' work goes to GitHub through pull requests with internal review, CI evaluation and, for new repositories, automatic merge; issue triage, review comments, CI and security-alert fixes, stale-branch cleanup; push gate for secrets and workflow files; system GitHub operations pass the guardrail engine; spec `specs/m14-github-backed-projects.md` | done (verified with a fake GitHub API and local bare repositories only; no real GitHub) |
| M15 | Agent cards and purchasing | Agents request purchases (`company_request_purchase`); a deterministic policy (bands all 0 by default, recurring and new vendors to a person, code ceilings), approval bound to the exact scope, per-purchase card from a provider adapter (`manual` / `stripe_issuing` / `mercury`) or the owner's own card, a human checkout task, verified expense on settlement, subscription tracking, freeze on stop/pause, a Wallet panel and widget on the canvas; the card number never touches Foundry; spec `specs/m15-agent-cards-and-purchasing.md`; decisions D45, D46 | done (verified with a fake card provider and recorded API responses only; no real Stripe Issuing or Mercury call) |
| M16 | Self-critique and market radar | 100 seeded sources (57 fetched by a polite SSRF-guarded fetcher, 33 reached by agents through `site:` search, 10 off with a reason), keywords, an items feed, daily briefs, an ideas backlog with caps and duplicate checks, weekly self-critique and UI inspiration, a control-plane page reader, a Market Researcher role, four routines, watchdog D10, a Radar panel; spec `specs/m16-self-critique-and-market-radar.md`; decisions D47 to D49 | in progress: WP-2 (services, APIs, routines, runtime tools, docs) done and verified with fakes only; no real feed, Hacker News, One Page Love or Siteinspire call |
| M18 | Low-friction autonomy | Decide-don't-ask doctrine, founder questions with defaults, approval deadlines (a timeout never approves), faster unblocking, retries with memory, one automatic re-plan; a durable LangGraph work graph per task (prepare → act → self-check → finish, interrupts for answers/approvals, crash resume) over our own tenant-scoped checkpointer, runtimes unchanged; spec `specs/m18-low-friction-autonomy.md`; decisions D54, D56 | done (2026-10-31); verified with scripted runtimes and a fake Claude Code only; Claude Code `--resume` only reuses a session within one run (each run has its own workspace) |
| M19 | Chat channels | Talk to the company from Telegram, Discord and WhatsApp (official APIs only): link with a one-time code, instructions become founder notes and the answers come back to the chat, approvals and agent questions with buttons, critical alerts, WhatsApp 24-hour window; spec `specs/m19-chat-channels.md`; decision D53 | done (2026-10-31); verified against fake platform servers only (no real bot) |
| M20 | Chat-first canvas | The Command Center as a chat in the first fold, agents' questions inline, a compact org Map (radial circles with illustrated faces, reporting and collaboration lines) or List, CC0 DiceBear faces generated locally; spec `specs/m20-chat-first-canvas.md`; decision D55 | done (2026-10-31); checked through tests and DOM only (no screenshot review) |
| M21 | Agent meetings | Agents call 1-3 colleagues to a short turn-based meeting (a LangGraph per meeting, every turn a normal task); the outcome becomes decisions and tasks only the accepting owners get; spec `specs/m21-agent-meetings.md`; decision D57 | done (2026-10-31); verified with scripted runtimes only (never a live model) |
| M22 | Human work and outsourcing | Agents ask people for what only a person can do (sign, call, KYC, errands): a task for a member or the human pool that blocks the agent's task through a plain dependency; HR drafts freelance jobs for Fiverr or Upwork that a person always approves, posts on the marketplace's own site and records (post, hire, delivery, payment as a verified expense); Foundry never posts, hires or pays; People ops panel, chat bubble, org List "Contractors"; spec `specs/m22-human-work-and-outsourcing.md`; decision D59 | done (2026-10-31); verified with scripted runtimes and mocked web only |
| M23 | Company intensity | One 1-5 control (top bar and Settings -> Policy) for how hard the company works: a preset written into the pace fields (heartbeat, parallel runs, planning rate, initiative, meetings, self-check), never a budget; hand edits show "Custom"; levels 4-5 need a confirmation on a plan-only AI stack, which starts at level 2; spec `specs/m23-intensity.md`; decision D58 | done (2026-10-31); verified with tests only |

Order: M3b ∥ M4-UI → M5 → M6 → M7 → M8 → M9. UI for each milestone ships with it.

## Autonomy model (summary — details in specs/m5)

| Layer | Who | Cadence / triggers | Does |
|---|---|---|---|
| Strategic | AI Co-founder | Founder's note, goal change, daily + weekly reviews, escalations | Triage notes, set/adjust goals & projects, assign leads, hiring plan, schedules, budget allocation, decisions, reply to the Founder |
| Management | Department leads | project created/assigned, subtask done, review due | Break projects into tasks, assign to reports, review, unblock, escalate |
| Execution | Specialists | task ready | Do the work with their skills, produce artifacts, request help/hire |
| Support | HR, Finance, QA/Review, Ops | hiring task, budget threshold, review task, health check | Hire + skills; cost optimisation; acceptance reviews; runtime/reachability health |

## Guardrails (summary — details in specs/m6-guardrails-and-governance.md)

Every action an agent can take is classified by **risk tier** (read · internal write ·
external read · external communication · external commitment · spend · identity/account ·
destructive). Policy maps each tier to *allow*, *peer review* (maker-checker by another
agent), *AI Co-founder approval*, *human approval* or *deny*. On top: budgets, rate
limits, sandbox + file jail, outbound DLP, prompt-injection containment, runaway/stall
detection, circuit breaker and emergency stop. Agents can never change their own limits.

## Hard boundaries (not automated, by design)

- Agents do not sign up for third-party accounts that require CAPTCHA/phone verification
  or acceptance of terms (e.g. Gmail, Vercel, registrars). The Founder connects such
  accounts once (API token / OAuth); after that the AI Co-founder operates them.
- Real-money commitments outside budget (paid domains, subscriptions, budget increases)
  require the Founder's approval by default (configurable).

## Status notes

- **M16 status (2026-10-27):** the company reads the outside world and turns it into work. Sources are fetched by the
  control plane (no model); the Market Researcher (the Co-founder until one is hired) writes a daily brief and proposes
  ideas with evidence; the Co-founder accepts ideas into projects (one new goal a week at most); a weekly self-critique and
  UI inspiration run for QA/Design. Outside text reaches agents only inside untrusted `content` fields. **Not run for
  real:** any feed, Hacker News, One Page Love, Siteinspire or DuckDuckGo call. Details:
  `specs/m16-self-critique-and-market-radar.md` section 8.

- **M15 status (2026-10-27):** agents request purchases with `company_request_purchase`; a three-band policy (auto, AI
  Co-founder, human; all bands start at zero) decides; an approved purchase gets a single-use card with an exact limit
  (Stripe Issuing or Mercury, US entities) or, with a manual card (India and everyone else), a checkout task for the
  human. Card numbers never reach the server. Reconciliation polls every 15 minutes, settles verified expenses, freezes
  cards on anomalies; subscriptions get renewal reviews. Wallet tile, panel and setup wizard with per-provider funding
  guides. **Not run for real:** any card provider API (Mercury's card API paths are partly assumed, see the adapter
  docstring), a real checkout, Stripe webhooks. Details: `specs/m15-agent-cards-and-purchasing.md`.

- **M14 status (2026-10-27):** onboarding (web and `foundryos setup`) offers a new GitHub repository, an existing one, or
  none. Agents work on `foundry/<project>` and open pull requests; the internal reviewer reviews them and they merge when
  CI is green and the policy allows it. Issues, review comments, CI failures and Dependabot alerts become tasks. The token
  stays in the control plane (`GIT_ASKPASS`, never argv or `.git/config`); every push is secret-scanned; there is no force
  push. Backend suite about 2,360 tests, runtime 270 and web suites green. **Not run for real:** github.com itself (token
  permissions, branch protection, checks, rate limits, webhooks), a real coding CLI committing in a GitHub-backed worktree.
  Details: `specs/m14-github-backed-projects.md` sections 9 and 11, `docs/github.md`.

- **M12 status (2026-10-27):** watchdog and closure loops, retry/backoff and breaker auto-resume, reserved
  management slots, per-role models and the CLI role-family scope, the shared per-project git repository,
  decomposition caps, `company_plan_project`, the digest and the web settings. Two end-to-end tests
  (`tests/test_m12_e2e.py`) run the whole company with no human after the first note: API mode (note → goal →
  project → lead and specialist hires → plan → work → review → project closed by the lead or by the watchdog →
  goal achieved → next goal on initiative) and hybrid mode (planning and reviews on the API runtime, work on a
  fake `opencode` binary, task 2 starting from task 1's merged files). **Not run for real:** any cloud or local
  model, Claude Code / Codex / OpenCode inside git worktrees, real MCP tool calls from a CLI, PostgreSQL,
  prompt-cache hit rates, real provider 429 texts. Details: `specs/m12-full-autonomy.md` sections 2.7 and 5.

- **M13 status (2026-10-26):** code in `099092d`, docs and web changes after it. Verified with fakes only: a fake `cloudflared`,
  `launchctl`, `systemctl` and `loginctl`, fake coding CLIs and fake providers. Backend suite 1,685 passed on `099092d`; web 1,217
  passed. **Not run for real:** a quick tunnel (no real URL opened from a phone; nothing observed about how long a URL lives or about
  server-sent events through it), a systemd user session with linger, a LaunchAgent in `user/<uid>` or a LaunchDaemon, a real
  provider or coding-CLI login through the wizard, the `install.sh` hand-off over a real SSH session. Details:
  `specs/m13-headless-setup-and-public-url.md` section 6.

- **v0.1.0 released (2026-10-24):** https://github.com/suriyavasan/foundry-os/releases/tag/v0.1.0 — bundles for
  Apple Silicon macOS, Linux x86_64 and Linux arm64 (each built and install-tested in CI), multi-arch Docker image
  on GHCR, Homebrew formula in `suriyavasan/homebrew-tap`. Verified on this Mac with the real public commands:
  `brew install suriyavasan/tap/foundry-os` (version, `brew test`, serve, UI, `doctor` all OK) and the one-line
  `curl … | sh` installer (download, SHA-256 verification, versioned install). No Intel macOS bundle: key
  dependencies no longer publish Intel macOS wheels — Intel Macs use Docker.

- **M5 live run (2026-10-24, local qwen2.5:7b):** one Founder's note → triaged as a
  directive + reply, a goal and a project, HR hired a project lead and a researcher ($5/mo
  each), 16 runs (12 ok), 7 artifacts (plan, audience and market analyses); positioning and
  page copy not reached in 30 min. Bottlenecks: 1–6 min per 7B run and over-decomposition.
  A stronger model (free OpenRouter models or a cloud key, routed by M6) is the biggest
  lever for end-to-end autonomy.

- **M3b live proof (2026-10-24, local qwen2.5:7b, 32K context):** a research-analyst hire
  executed a real task end to end (15 events, artifact with the requested 5 bullets,
  8.7K prompt / 690 output tokens, verified $0 local cost, 62 s); cancel in 1 s. Unprompted,
  a skill gap (docker) triggered HR: Harper searched/installed a skill, created a role
  template, hired a "Docker Expert" who was auto-assigned and completed the waiting task.
- Gap found: HR set a $5,000/month budget on its hire with no company budget configured →
  M6 adds `max_agent_monthly_budget_micros` and hire budget defaults (agents can never
  exceed policy caps).

- M1–M3a, M4 backend committed (`1a113fa`, `6d2ef72`, `555a561`, `361a9be`).
- Local models via Ollama (`qwen2.5:7b` verified). Ollama must run with
  `OLLAMA_CONTEXT_LENGTH=32768` (its OpenAI endpoint ignores `num_ctx`).
- Docker daemon not running → no sandboxed terminal for agents yet.
- Disk nearly full; keep footprint lean.

- **M5 live run (2026-10-24, local qwen2.5:7b, 32K, temporary Ollama on :11435; seven runs, the last reported here):** a
  Founder's note led, without a human, to a goal, a project, a hired project lead (HR, `project-lead` template, $5 budget), a
  hired market researcher, the lead's decomposition into tasks, executed tasks with artifacts, `waiting_on_subtasks`, review tasks
  for the Co-founder and one accepted review. It did not finish the project in 30 minutes (a 7B run takes 1-6 min; the lead
  over-decomposed). Each earlier run exposed a real defect that was fixed with tests (zero project budget blocking local runs,
  triage-before-act, HR named as lead, duplicate hires, made-up skill names causing hiring churn, "new hire" wakes inviting
  onboarding projects). Details in the M5 report; nothing here was verified with a cloud model.

## Known limitations (rolling)

- Auth: password accounts since M7 (`local` mode is development only).
- A 7B local model needs launcher scaffolding to follow workflows; bigger/cloud models
  will plan better (provider routing in M6).

## Final verification (M9, 2026-10-24)

Run by `scripts/verify.sh` (`VERIFY_START_OLLAMA=1 scripts/verify.sh`): copies the working tree (tracked + untracked,
not ignored; **uncommitted M9 work included**) to a temp dir as a clean-checkout equivalent, installs it by the steps in
`docs/deploy.md`, starts it with `companyos up --port 8710` on a temporary data dir, and exercises each item against a
temporary Ollama (`:11435`, `OLLAMA_CONTEXT_LENGTH=32768`, `qwen2.5:7b`, stopped by PID afterwards). Environment: macOS 26,
Apple Silicon, Python 3.14, SQLite, warm uv/pnpm caches. The owner's own instance (:8700) and Ollama.app were untouched.
The script ran twice; the first run failed items 7 and 9 because of two bugs **in the script** (a wrong request body and a
wrong way of detecting the restarted worker's pid), fixed and rerun. The product behaved the same in both runs. Final run:

| # | Item | Result | Evidence (and caveats) |
|---|---|---|---|
| 1 | Company created | PASS | `companyos company create` -> company + AI Co-founder + HR agent; reported by `companyos status` |
| 2 | AI employee configured | PASS | Ollama registered via `providers detect --register` (provider test ok, tool calling true, served context 32768); `agents hire -t research-analyst` -> runtime `company_agent`, model `qwen2.5:7b`, $5/month budget, active. The Co-founder and HR were paused during the run so the single local model served only the test task |
| 3 | Task executed through the company agent runtime | PASS | task assigned to the hire ran to `done` in 1 attempt through `company_agent` on `qwen2.5:7b`. The *quality* of the 7B model's work was not assessed; the first run of the script also passed this item (3 bullets saved as an artifact) |
| 4 | Events + artifacts persisted | PASS | 21 run events, 2 artifacts; artifact bytes re-read over the API match the stored SHA-256; the same 21 events were still there after item 9's worker crash. A full API/DB restart was not part of the script |
| 5 | Usage / costs recorded | PASS | 30,434 input / 574 output tokens on the run, `cost_kind=verified`, 0 micro-USD (local model), one usage-ledger record. A *priced* provider's run (item 6) was recorded as `estimated`, not `verified` |
| 6 | Spending policy enforced | PASS | agent budget $0.001 on a priced provider (the same Ollama declared as $1/Mtok, pinned routing): the task was **blocked before any run** (`budget_exceeded`, limit `agent_monthly`, 0 runs); raising the budget released it and it ran (estimated ~$0.01). No real money exists in this scenario - the point is the gate. The free (local) route reserves nothing and is never blocked by budgets |
| 7 | Approvals gate protected actions | PASS, with a caveat | an agent run's $12 `external_purchase` request became a pending approval routed to a human; the agent could not decide it (agent API 403, owner API 403); a human approved it with `companyos approvals approve`. **Caveat:** the request was made by the *test harness* through the real agent API with a token minted for the agent's live run, because the 7B model did not call the approval tool when asked (three attempts across two agents; in one it wrote a made-up "Approval ID" without calling anything). Only single-use consumption of an approval is not re-run live (backend tests cover it) |
| 8 | Agent paused and retired | PASS | paused agent: task stayed `backlog`, 0 runs; retired agent: listed as retired, `resume` refused (409), new work cannot be assigned (409) |
| 9 | Recovery after restart | PASS | `kill -9` of the worker during a run -> `companyos up` restarted it (1 s backoff, new pid); the orphaned run was marked `exit_reason=worker_lost` (stale heartbeat after 30 s) and the task was **retried automatically**: 1 `worker_lost` run + 1 succeeded run, attempts 2, task `done`. The crashed worker's orphaned runtime process was killed by pid by the script |
| 10 | Deploy from the docs | PASS, with a caveat | copy of the tree -> `uv sync` -> runtime venv -> `pnpm install` -> `companyos up`: healthy, UI served with CSP and SPA fallback, `/api/no-such` is a JSON 404, first-run setup from loopback, `companyos login` stored a 0600 token. **Caveats:** it was a copy of the working tree, not a `git clone` of a commit (M9 is uncommitted); uv and pnpm caches were warm (no cold download was timed); macOS only |

**Not verified in M9 (so not production-ready in these respects):** the Docker image build and the Compose stack (no
daemon); PostgreSQL (no server; the suite only ever ran on SQLite); the Docker-socket agent sandbox; running behind a TLS
proxy or a real tunnel; Linux/Windows; a cloud model (everything above used a local 7B). Also not claimed: that a 7B model
can run an autonomous company - see the M5 status notes; stronger models are the main lever.

## M9 status notes

- **Web bundle:** the single 1,093 kB entry chunk (307 kB gzip, Vite's 500 kB warning) became 51 files: the entry is 134 kB, the
  largest chunk is 219 kB (React), panels and dialogs are separate lazy chunks. What the canvas loads at start is
  **747 kB raw / 243 kB gzip** (framework 296 kB + shared app code 450 kB), i.e. 32% less raw and 21% less gzip than before,
  with ~390 kB loaded only when a panel is opened. The "well under 500 kB initial" aim was **met per chunk, not for the total
  the canvas needs**: React, the router, TanStack Query, Radix and the API client are needed on the first screen.
- **Tests at the end of M9:** backend 1,097 passed (ruff check + format clean; 93 are new: serving 12, CLI 33, backup 14, doctor 14,
  up 18, config-failure billing 2); web 939 passed (typecheck, lint, build clean; 6 tests got an `await waitFor` because
  navigation now waits for a lazy chunk); runtime 237 passed (run during the security review, not changed).
- **Defect found by the live run and fixed:** a run that failed on configuration before executing (e.g. "provider has no
  credential") settled its reservation as an *estimate* ($0.05 each, three times), counting phantom spend toward budgets.
  It now releases the reservation (`docs/security.md` finding 2; decision D26).
- **Dependency audit:** `pip-audit` (backend, runtime) and `pnpm audit` found nothing; Hermes' exact pins were not touched.
- **Gaps, in short:** Docker/Compose and Postgres unverified; `style-src 'unsafe-inline'`; the 7B model is a weak agent.
  Three M9 gaps were closed afterwards (SSRF guard for owner-supplied URLs, generic per-IP rate limit, encrypted
  backups: `docs/security.md` section 5). Full list: `docs/security.md` section 4.
