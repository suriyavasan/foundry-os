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
| M6 | Guardrails & governance | Action risk tiers + policy engine, rate limits, maker-checker for external actions, outbound DLP, injection defenses, stall/runaway detection, emergency stop, mid-run budgets, free-first routing, finance views, guardrail audit | planned |
| M7 | Accounts, login & team | Username/password onboarding, sessions, roles, email/link invitations, humans in the org tree with assignable tasks, notifications | planned |
| M8 | Reachability & identity | Landing "how to reach" (LAN + public), tunnel, free-domain flows, optional paid domain (approval), Vercel-hosted control UI, company email identity, connected-accounts vault | planned |
| M9 | OS packaging & hardening | `companyos` CLI, single-port serve, Docker Compose, backups, security review, final verification | planned |

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

- Auth: local single-owner mode until M3b-sec (token gate) and M7 (accounts).
- A 7B local model needs launcher scaffolding to follow workflows; bigger/cloud models
  will plan better (provider routing in M6).
