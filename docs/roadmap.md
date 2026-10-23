# Company OS — Roadmap (v2)

Revised 2026-10-23 after M1, following the owner's direction:

- **Autonomous company.** Agents decide and act on their own. The human co-founder sets the
  goal and the hard limits and is not asked to approve routine work.
- **Our own agent layer.** Hermes Agent is forked into `runtime/` and reshaped into a
  company-employee runtime (company toolset, task protocol, per-employee skills, sandbox).
- **Skills per role.** Designers, developers, marketing, sales, customer success, etc. get
  curated AI skills (SKILL.md). An **HR agent** hires new employees and authors or installs
  skills when the company lacks a capability.
- **One canvas UI.** No sidebar. The first widget is a live org chart of circular avatars
  in a tree; hovering an avatar shows what that employee is doing right now.
- **An OS, not just a web app:** API + worker/scheduler + `companyos` CLI + dashboard.

North-star: *set a company goal → the AI Founder plans → the company hires the skills it
lacks → specialists execute real tasks through the forked runtime → the dashboard shows
live work, artifacts and actual costs — without the human approving each step.*

| # | Milestone | Outcome | Status |
|---|---|---|---|
| M1 | Foundation | Architecture, AGENTS.md, backend + web skeleton, company/org persistence, runtime registry | done (2026-10-23) |
| M2 | Work engine | Goals, projects, tasks + dependencies, task runs + events, artifacts, policies, budget ledger with atomic reservations, approvals, decisions, schedules; DB job queue, worker, scheduler, crash recovery; live event stream (SSE); agent API with scoped run tokens; deterministic test runtime | planned |
| M3 | Company agent runtime (Hermes fork) | Lean fork in `runtime/` with attribution; company toolset calling the agent API; JSONL run protocol; model providers (Ollama local first, OpenRouter/Anthropic/OpenAI); encrypted credential store; restricted execution (workspace jail, tool allowlists; Docker when available); real end-to-end task on a local model | planned |
| M4 | Skills & HR | Skill library + catalog (curated from Hermes' MIT skills, GitHub discovery), license + security scan, role templates (founder, HR, engineering, design, marketing, sales, customer success, research, QA, ops), per-agent skill materialization, HR agent that fills capability gaps by hiring and authoring skills | planned |
| M5 | Autonomous company | Founder heartbeat loop: goal → plan → tasks → delegate → review → revise; managers delegate; hiring on gaps; routines; autonomy policy + hard governance limits; company pause / kill switch | planned |
| M6 | Finance & governance | Usage ledger attribution, enforcement at dispatch and mid-run, free-first model routing, minimal human gate (real-money purchases, budget increases), finance views | planned |
| M7 | Canvas UI | Sidebar-less single canvas: avatar org tree with live hover cards, live activity, tasks, goals/plan, artifacts, skills, finance, approvals widgets; command palette; detail drawers; light/dark | planned |
| M8 | OS packaging & hardening | `companyos` CLI (up/status/goal/agents/tasks/runs tail/skills/budget), single-port serve, Docker Compose, password auth + sessions, backups, security review, final verification checklist | planned |

Execution order: M2 → (M3 ∥ M7 shell) → M4 → M5 → M6 → M7 completion → M8.

## Autonomy and its limits

The company runs without asking the human for routine decisions. The following remain
**hard limits enforced in code** (not prompts), all configurable by the human only:

- Company and per-agent daily/monthly budgets (atomic reservations before every run).
- Max concurrent runs, max delegation depth, max tasks per planning cycle, max retries,
  max revisions, max run time, max headcount.
- Tool allowlists and a restricted execution environment per agent.
- Agents can never change their own permissions, budgets or the company policy.

By default the human is only asked about **real-money commitments outside the budget**
(external purchases, budget increases). The owner can widen or narrow that list
(`policy.human_approval_required_for`), or set it to empty for full autonomy.

## M1 notes (done 2026-10-23)

- Hermes source inspected at commit `db1fa15`; findings in `docs/architecture.md` §6.1.
- Backend: companies, members, departments, agents with lifecycle, audit log, overview,
  runtime registry. 125 pytest tests.
- Web: console with 10 sections (to be replaced by the canvas UI in M7). 76 vitest tests.
  `src/api/contract.check.ts` fails typecheck if backend OpenAPI drifts from client types.
- Verified in a browser against the live backend.

## Environment facts (2026-10-23)

- Local models available through Ollama: `qwen2.5:7b` (tool calling verified),
  `deepseek-r1:14b`, `olmo2:13b`. No cloud LLM keys configured.
- Docker is installed but the daemon is not running → sandboxing falls back to a
  restricted local mode until Docker is started.
- `gh` CLI is authenticated → usable for GitHub skill discovery.
- Disk is ~98% full (≈9 GB free): keep the runtime fork lean, avoid large extras.

## Known limitations (rolling)

- No real authentication yet (M8); local single-owner mode only.
- No task execution yet (M2/M3).
- Founder cannot be retired (succession: M5).
