# Company OS — Roadmap

North-star success criterion: *enter a company goal → the AI Founder creates a plan →
three specialist agents collaborate through real task execution → inspect the completed
artifacts and actual costs in one dashboard.*

Update the status column and notes at the end of each milestone.

| # | Milestone | Outcome | Status |
|---|---|---|---|
| M1 | Foundation | Architecture, AGENTS.md, backend + web skeleton, company/org persistence, full navigation shell, Command Center on live data, runtime registry with Hermes health check | done (2026-10-23) |
| M2 | Dashboard | Polished org chart, employee profile tabs, hire/edit/pause/retire flows, project & task screens (UI) | planned |
| M3 | Backend & data model | Projects, tasks, dependencies, task runs, policies, budgets (schema), schedules, artifacts metadata, decisions, approvals; pagination; migrations; tests | planned |
| M4 | Runtimes | RuntimeAdapter contract finalized, Hermes adapter (gateway Runs API), per-agent isolated HERMES_HOME provisioning, credential store, runtime health screen, one real end-to-end task | planned |
| M5 | Founder & orchestration | Goal → plan → approval → tasks → dispatch → review → revise; structured handoffs; durable schedules; hard governance limits | planned |
| M6 | Finance & governance | Usage ledger, atomic budget reservations, approval inbox, free-first routing, finance dashboard | planned |
| M7 | Knowledge & lifecycle | Artifact storage + versioning, company memory with provenance, full hire/retire lifecycle, performance metrics | planned |
| M8 | Integrations & deployment | Integrations framework, auth/sessions, Docker Compose (api, worker, scheduler, db, storage), backups, security hardening, final verification | planned |

## M1 notes (done 2026-10-23)

- Hermes source inspected at commit `db1fa15`; findings in `docs/architecture.md` §6.1.
- Backend: FastAPI + SQLAlchemy + Alembic (initial migration), companies, members,
  departments, agents with lifecycle state machine, audit log, overview aggregate,
  runtime registry. 125 pytest tests (tenancy isolation, roles, lifecycle, founder rules,
  manager cycles, secret-key rejection, agent self-modification guard, error envelope).
- Web: Vite/React/TS console with all 10 nav areas, onboarding, Command Center, org
  chart/table, employee profile tabs (overview, activity, configuration, budget,
  permissions), lifecycle actions with confirmation, activity feed, runtime health,
  settings. 76 vitest tests. `src/api/contract.check.ts` fails typecheck if the backend
  OpenAPI drifts from the client types.
- Verified manually in a browser against the live backend: create company → founder
  created → propose hire → activate → pause; budget stored as exact micros; audit trail
  complete; founder retirement refused; non-member company returns 404.
- Not verified: Postgres (SQLite only so far); live Hermes (not installed here — adapter
  health check tested with mocked HTTP only).

## Next up (M2 → M3)

M2 polishes the dashboard UI on what exists; most remaining screens (projects, tasks)
need M3's data model. Recommended order: do M3's Project/Task/TaskRun/Policy schema
first, then build M2's project and task screens on real endpoints.

## Known limitations (rolling)

- No real authentication yet (M8); local single-owner mode only.
- No task execution yet; Hermes adapter only reports health (M4).
- Budgets and tool permissions are stored but not enforced yet (M6 / M4).
- Founder cannot be retired (succession arrives with M7 lifecycle work).
- Web bundle is a single 500 kB chunk; add route-level code splitting later.
