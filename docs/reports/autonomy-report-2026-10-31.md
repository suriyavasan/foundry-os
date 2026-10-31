# How autonomous can Foundry be? — report after M18–M21 (2026-10-31)

Scope: what the company can now do without the founder, what still needs a person (by design, by law, or because
it is not built yet), and what to do next. Everything below was checked in code and tests; nothing was run against
a live model, a real Claude Code / Cursor sign-in, or a real Telegram/Discord/WhatsApp bot (see "Verification").

## 1. The autonomy engine now

| Layer | What it does | Built in |
| --- | --- | --- |
| Wake loop | A 2 s scheduler turns events (notes, failures, alerts, schedules, a 30-min heartbeat) into debounced wakes; wakes become planning tasks | M5/M12 |
| Routing | Each run goes to an AI in the stack (order, heavy vs everyday, importance, plan limits) | M17 |
| **Work graph** | Each task is a durable LangGraph: prepare → act (Claude Code / Cursor / API agent) → self-check → finish; up to 2 self-revisions; interrupts for answers/approvals; resume after crash; progress stored in our own tenant-scoped checkpoint tables | **M18 B** |
| **Decide-don't-ask** | Reversible choices are decided and logged as Decisions; questions to the founder must carry a default and are non-blocking unless marked blocking (max 24 h) | **M18 A** |
| **Deadlines everywhere** | Co-founder approvals → a person after 30 min; human approvals → declined after 24 h; blocked tasks → Co-founder after 30 min, human after 8 h; questions → default at deadline | **M18 A** |
| **Meetings** | Agents plan together (2–4 agents, turn-based LangGraph), outcome = Decisions + tasks for owners who accepted; no Co-founder round-trip | **M21** |
| Guardrails & budgets | Spending, destructive, identity and outbound actions are gated in code; budgets reserved per run | M6/M15 |
| Founder surface | Chat-first Command Center, questions with one-tap answers, approvals in chat apps (Telegram/Discord/WhatsApp) | **M19/M20** |

## 2. What changed for the founder (the friction audit, before → after)

| # | Friction source (ranked at the start of this work) | Now |
| --- | --- | --- |
| 1 | Human approvals never time out; agents park | Every approval has a deadline; human ones are declined at the deadline and the agent continues another way |
| 2 | Co-founder approvals didn't wake it; its own requests were a dead end | It is woken immediately; its own requests go to a person; unanswered → a person after 30 min |
| 3 | Prompt told agents to ask approval for spend/deploy/outreach | Removed; guardrails already gate these in code |
| 4 | No way to ask the founder; ambiguity became a guess or a block | `company_ask_founder` with a mandatory default; limits 5 open/company, 2/agent/day |
| 5 | Crash/timeout restarted work from zero | Graph resumes from the last checkpoint; retries carry the last error |
| 6 | Blocked escalated slowly (2 h / 24 h); failed was terminal | 30 min / 8 h; one automatic re-plan with a new approach |
| 7 | Every reviewed task cost an extra review cycle | Self-check pass → done (explicit reviewer and code PRs keep the review) |
| 8 | Everything coordinated through the Co-founder | Agent-to-agent meetings |

## 3. How much autonomy is realistic

We cannot give a measured percentage yet — no live company has run on M18–M21 (that would be fake data). By
design, the levels are:

* **Day-to-day operations: autonomous.** Planning, hiring within headcount and budget, task breakdown, execution,
  self-review, re-planning after failures, coordination between agents, routine reporting. The founder is
  informed, not asked.
* **Founder touchpoints that remain by design** (all enforced in code, each now with a deadline or a default):
  1. Spending above the auto-spend limit (default **$0**) and all purchases (purchasing is **off** by default;
     checkout is always a person) — money is the founder's.
  2. Always-human actions: registering/purchasing domains, creating GitHub repositories, merging PRs into existing
     repos (`pr_human_merge` default), external commitments and destructive actions.
  3. Budget raises (agents can never raise their own budgets).
  4. Answers to questions only the founder can know — but agents proceed on their default if unanswered.
* **Hard external limits** (not ours to remove): personal Claude/Cursor plans have usage limits (a paused
  connection waits until reset; the API is the safe choice for heavy autonomy), WhatsApp only allows replies
  within 24 h of the founder's last message, platform terms (official APIs only).

**Practical ceiling:** with an API connection and budgets set, the company can run for days without input; the
founder's job becomes reviewing decisions after the fact and deciding money/legal/irreversible items, mostly from
a chat app with one tap. The two biggest remaining levers are money policy (§5.2) and real session resume (§5.1).

## 4. Costs and risks of more autonomy

* **More model calls**: self-check (one short call per completed task with acceptance criteria) and meetings (up
  to ~9 turns each, capped 3/agent/day and 12/company/day). Both are budgeted runs and can be turned off in
  Settings (`self_check_enabled`, `meetings_enabled`).
* **Decisions made without you**: they are logged (Decisions, meeting outcomes) and reversible by design; watch
  the decision log for the first weeks.
* **Timeouts decline**: an approval you miss is declined, not approved — work continues, but possibly a slower way.
* **Small models**: workers now see 14 company tools (was 10–12); weak local models may pick tools less reliably.

## 5. Next steps to go further (recommended order)

1. **Real Claude Code session resume across runs.** Today each run has its own workspace (`<workspaces>/<company>/<run_id>`)
   and Claude Code stores sessions per directory, so `--resume` only works within a run; across a crash or a wait
   it falls back to a new session plus the graph's summary. Fix: a stable per-task workspace and branch for
   graph threads (`task/<task_id>`), with cleanup on done.
2. **A money envelope instead of per-item approvals.** Let the founder set a monthly autonomy envelope and an
   auto-spend limit per item inside it (today $0 and purchasing off). This is the largest remaining source of
   human approvals, and it must stay the founder's decision.
3. ~~B5 intensity slider~~ — **done (M23, 2026-10-31)**: a 5-level control (Conserve → Continuous) sets pace
   (check-ins, concurrency, planning rate, initiative, meetings, self-check) and never budgets. Also done since this
   report: **B3 / M22** — agents hand work only a person can do to a human-work list (their task waits for the
   result), and HR drafts freelance jobs that a person approves, posts on Fiverr/Upwork, and records (Foundry never
   posts or pays on a marketplace).
4. **Measure it**: an "autonomy score" card — founder touchpoints per day (approvals, questions answered, blocks
   resolved, manual retries) vs. tasks completed — so the next report has real numbers.
5. **Live trial**: one week with a real API connection and a Telegram bot; then tune deadlines and limits.

## 6. Verification

* Backend: 3125 passed, 1 skipped (full suite, 2026-10-31). Runtime: 339 passed + import checks. Web: 1566
  passed, typecheck, lint, build.
* Graph-mode end-to-end flows have their own tests (`tests/test_m18_graph_e2e.py`, 8 tests: note → finished project, the
  M12 chains, hybrid CLI, explicit reviewer loop, crash recovery without repeating the act, blocking question
  answered through the API); 9 legacy
  end-to-end tests run in one-shot mode by design (they assert the old event lists and review cycle).
* Not exercised live: real models in verify mode and meetings, real Claude Code `--resume`, real chat-app bots,
  Postgres for the new tables.
