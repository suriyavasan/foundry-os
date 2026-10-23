# M6 — Guardrails & governance (incl. finance) — spec (v2)

Owner direction: guardrails are part of the agentic framework. Autonomy is the default,
so safety must come from code-enforced layers, not from asking the human. Principles:
**defense in depth · least privilege · fail closed · everything audited · agents never
change their own limits · enforcement in the control plane, not prompts.**

## 1. Action risk tiers & policy engine (`app/guardrails/`)

Every agent-API endpoint and every runtime tool is registered with a **risk tier**:

| tier | examples |
|---|---|
| `read` | get context/state, search colleagues, read artifacts |
| `internal_write` | create/update tasks, artifacts, progress, decisions, memory proposals |
| `org_change` | hire, assign skills, create role templates, create schedules |
| `external_read` | web search/fetch, GitHub read |
| `external_communication` | send email, post publicly, open PRs on external repos, invite humans |
| `external_commitment` | accept terms, register accounts/domains, sign up for services |
| `spend` | any action with a cost outside model usage (purchases, subscriptions) |
| `identity` | create mailboxes, change DNS, deploy the control UI, rotate credentials |
| `destructive` | delete/overwrite external resources, cancel others' work at scale |

Policy table `guardrail_rules` (company-scoped, human-editable only): `tier` (or a specific
`action`), `scope` (all agents / family / agent id), `decision` ∈ `allow`, `peer_review`,
`cofounder_approval`, `human_approval`, `deny`, optional `conditions` (amount ≤ X, rate ≤
N/day, domain allowlist). Defaults:

| tier | default |
|---|---|
| read, internal_write | allow |
| org_change | allow within headcount/budget (M4 rules) |
| external_read | allow (rate limited, private URLs blocked) |
| external_communication | peer_review (another agent checks content) → then allow |
| external_commitment | human_approval |
| spend | human_approval above `auto_spend_limit_micros` (default 0), else cofounder_approval |
| identity | cofounder_approval, plus human_approval for anything touching domains/DNS ownership |
| destructive | human_approval |

`guardrails.check(ctx, action, payload) -> Decision` is called by every agent-API service
before acting. `peer_review`/`*_approval` create an Approval (M2) with the exact action
scope (single-use, scope-hash bound); the action executes only via `consume`. Deny → 403
`guardrail_denied` with the rule id. Every decision is logged (`guardrail_events`: action,
tier, decision, rule, agent, run, payload digest — no secrets).

## 2. Rate limits & quotas
Per agent and per company sliding windows (DB-backed, portable): API calls/min, tasks
created/hour, external communications/day, hires/day, skill installs/day, web fetches/hour.
Defaults conservative; exceeding → 429 `rate_limited` + guardrail event; repeated → alert.

## 3. Execution containment (recap + additions)
- Run-scoped tokens; role-based company tools per template; sandbox none → no shell;
  file jail; private-URL fetch blocked; minimal env; secrets never in prompts.
- **Network egress policy** (when Docker sandbox is on): default deny except allowlisted
  domains per role (configurable); documented.
- **Stall detection:** no event/progress for `stall_seconds` (default 180) → cancel run
  (`exit_reason=stalled`); counts toward circuit breaker.
- **Runaway detection:** per-run caps on tool calls, tasks created, artifacts, tokens;
  per-company caps on concurrent runs and open tasks; planning-storm limits (M5).
- **Circuit breaker** + **emergency stop**: company auto-pause on failure spikes; global
  `POST /api/system/emergency-stop` (owner) + CLI `companyos stop --all` cancels every run
  in every company and disables dispatch until resumed.

## 4. Content guardrails
- **Prompt-injection containment:** untrusted content (web pages, fetched files, emails,
  artifacts from other agents, pasted URLs in notes) is wrapped in tagged blocks by the
  runtime (`<untrusted source=…>`), and the system prompt states such content is data.
  Tool results never change an agent's permissions; any permission change requires a
  human. Detect common injection phrases in tool results → flag event (no auto-block).
- **Outbound DLP:** every `external_communication` payload and every artifact marked for
  external publication is scanned for secrets (credential patterns, our own tokens/keys),
  private data markers (emails/phones beyond allowlist), and internal-only memory; findings
  block the action → revision request.
- **Maker-checker:** `peer_review` decisions route the exact outbound content to a reviewer
  agent (not the author; the author's manager or a QA/brand agent) with a checklist
  (accuracy, tone, legal/brand constraints from company constraints, DLP results).
- **Claims & honesty:** agents must not represent themselves as human in external comms;
  outbound emails carry a configurable AI disclosure footer (default on).

## 5. Identity & credential guardrails
- Credentials are scoped: each credential lists allowed actions/tools and families;
  runtime receives only a short-lived, action-scoped grant via the control plane (the
  control plane performs the external call where possible — agents never see raw tokens).
- Rotation and revocation endpoints; revocation on agent retirement (M7 lifecycle).

## 6. Finance (carried from v1)
- Mid-run budget checkpoints with reservation extension; cancel on exhaustion.
- Free-first model routing (`free_first`/`pinned`/`quality_first`, paid fallback rules).
- Threshold alerts (50/80/100 %), external expenses, wasted cost accounting.
- Reporting API: `/finance/summary|breakdown|trend|runs|expenses` in company tz.
- UI: Spend widget, Finance panel, budget editors.

## 7. Guardrails UI
- **Guardrails panel** (`?panel=guardrails`): rules table (edit by owner/admin), recent
  guardrail events (allowed/denied/pending) with filters, rate-limit status, emergency stop.
- Approvals widget shows pending peer reviews and approvals by tier.
- Agent panel "Permissions" tab shows effective tiers/tools for that agent.

## 8. Tests
Rule resolution (specific action > family > tier default), each decision type end to end,
approval scope binding/single use, rate limits (window edges), stall + runaway + circuit
breaker + emergency stop, DLP blocks secrets and our own tokens, maker-checker never self-
reviews, injection tagging in runtime, credential grants scoped and revoked, finance tests
from v1. Agents can never edit guardrail rules (403) — including via run tokens.
