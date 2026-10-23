# M4 — Skills & HR — spec

Owner direction: every employee type (designer, developer, marketing, sales, customer
success, …) has AI skills it can use; skills come from Hermes' library and from GitHub;
an **HR agent** hires a new employee and creates/installs skills when the company lacks a
capability. Architecture: `docs/architecture.md` §6.3–6.4.

Skill format = SKILL.md with YAML frontmatter (`name`, `description`, `version`, `author`,
`license`, `platforms`, `metadata.hermes.{tags, category, related_skills,
requires_toolsets, requires_tools}`, `required_environment_variables`, …). Hermes finds
skills by recursive search for `SKILL.md` under `HERMES_HOME/skills` (any depth).

## 1. Data model (migration `0004_skills_and_roles`, after M3b's migration)

### SkillBlob (global, content-addressed, not tenant data)
`sha256` (PK), `size_bytes`, `file_count`, `created_at`. Content on disk at
`data/skills/blobs/<sha256>/` (the whole skill directory, normalized: no symlinks,
no executables outside `scripts/`, ≤ 2 MB, ≤ 100 files).

### Skill (tenant-owned)
`company_id`, `slug` (unique per company), `name`, `description`, `category`,
`tags` (json), `version`, `author`, `license` (SPDX or null), `source_kind`
(`hermes_bundled`,`hermes_optional`,`github`,`company_authored`,`uploaded`),
`source_ref` (e.g. `hermes@db1fa153:skills/creative/claude-design`,
`github:owner/repo@<commit_sha>:path`), `blob_sha256`, `trust` (`builtin`,`trusted`,
`community`), `scan_verdict` (`safe`,`caution`,`dangerous`,`unscanned`),
`scan_report` (json summary — findings, no secret values), `status` (`active`,
`quarantined`,`archived`), `requires_toolsets` (json), `required_env` (json names only),
`created_by_type/id`. Re-installing a newer version creates a new row version? Keep it
simple: `version` + `blob_sha256` update in place, previous blob kept, audit records both.

### AgentSkill
`company_id`, `agent_id`, `skill_id`, `assigned_by_type/id`, unique (agent_id, skill_id).
Only `active` skills can be assigned. Agents can't assign skills to themselves;
HR agent and humans can.

### RoleTemplate
Built-in templates are **code** (`backend/app/roles/builtin/*.yaml`, versioned with the
repo) and are readable by every company; company-custom templates (created by HR for
novel roles) live in table `role_templates` (`company_id`, same fields).
Fields: `slug`, `family` (avatar type: `founder`,`hr`,`engineering`,`design`,`marketing`,
`sales`,`customer_success`,`research`,`qa`,`operations`,`finance`,`generic`), `title`,
`description`, `default_kind`, `default_department`, `instructions` (role playbook),
`default_toolsets`, `default_skill_slugs`, `required_skill_tags` (what this role is good at).

Agent gains `role_template` (slug, null) and `role_family` (string, default inferred:
founder→`founder`, else `generic`) — exposed in the API (the canvas UI already prefers
`role_family`).

### Built-in role templates and seed skills
Curate from `runtime/skills` and `runtime/optional-skills` (Hermes, MIT). **Read each
candidate SKILL.md before mapping it** — skip skills that need unavailable toolsets/apps,
platform-specific apps (Apple, smart home, games), crypto trading, offensive security, or
paid services by default. Starting point (adjust after reading):

| family / template | seed skills (Hermes) |
|---|---|
| founder (`founder-ceo`) | weekly-review-planning, decision-questionnaire, one-three-one-rule, competitor-news-monitor, document-to-action-items |
| hr (`hr-partner`) | hermes-agent-skill-authoring (adapt: authoring skills for company employees), decision-questionnaire + company-authored `hiring-playbook` and `writing-company-skills` (write these) |
| engineering (`software-engineer`) | codebase-inspection, github, systematic-debugging, test-driven-development, requesting-code-review, simplify-code, spike, code-wiki, pr-lens, rest-graphql-debug |
| design (`product-designer`) | claude-design, design-md, popular-web-designs, architecture-diagram, excalidraw, concept-diagrams, impeccable, baoyu-infographic |
| marketing (`growth-marketer`) | social-media-content-calendar, humanizer, creative-ideation, competitor-news-monitor, blogwatcher, rss-feeds, youtube-content, baoyu-article-illustrator |
| sales (`account-executive`) | domain-intel, comps-analysis, email-inbox-triage, product-price-monitor, humanizer + company-authored `sales-outreach-playbook` |
| customer_success (`customer-success-manager`) | email-inbox-triage, document-to-action-items, meeting-action-items, llm-wiki, humanizer + company-authored `support-triage` |
| research (`research-analyst`) | arxiv, grounded-citations, llm-wiki, duckduckgo-search, research-paper-writing, competitor-news-monitor |
| qa (`qa-engineer`) | test-driven-development, adversarial-ux-test, systematic-debugging, requesting-code-review |
| operations (`devops-engineer`) | docker-management, sdlc-review, watchers, publish-site |
| finance (`finance-analyst`) | 3-statement-model, dcf-model, comps-analysis, excel-author, xlsx |

Company-authored seed skills ship in `backend/app/roles/skills/<slug>/SKILL.md` (written in
this milestone, license `MIT`, source_kind `company_authored`, authored by "Company OS").

## 2. Skill catalog & sources (`backend/app/skills/`)

- **Library seeding:** `services/skills.seed_company_library(ctx)` imports every skill
  referenced by built-in templates (from the runtime checkout path setting
  `COMPANY_OS_RUNTIME_DIR`, default `<repo>/runtime`) — run on company creation
  (in a background job, not blocking the request) and via `POST /skills/seed`.
- **Catalog search** (`GET /skills/catalog?q=&source=`): searches (a) the company library,
  (b) all Hermes bundled + optional skills in the runtime checkout (frontmatter index cached
  in memory), (c) GitHub when `source=github` (see below). Results: slug, name,
  description, tags, license, source_ref, installed (bool).
- **GitHub source:** `skills/github.py`
  - Search: GitHub REST repository search for skill repositories
    (`q=<terms> topic:agent-skills` OR `filename:SKILL.md` code search when a token is
    configured) and listing of `SKILL.md` files in a repo via the git trees API
    (`GET /repos/{o}/{r}/git/trees/{sha}?recursive=1`).
  - Token: optional credential `github` in the credential store (M3b). No token → unauthenticated
    (rate-limited, repo search + trees only). Never use the developer's `gh` login implicitly;
    the CLI may offer an explicit `companyos skills github-login` that stores `gh auth token`
    after the owner confirms (M8).
  - Install: resolve the ref to a **commit sha**, download only that skill directory
    (contents API / raw at the sha), enforce size/file limits, check **license**: skill-level
    `license` frontmatter or LICENSE file, else repo license (`GET /repos/{o}/{r}/license`).
    Allowed SPDX: MIT, Apache-2.0, BSD-2-Clause, BSD-3-Clause, ISC, CC0-1.0, CC-BY-4.0,
    Unlicense, MPL-2.0. Anything else/unknown → refuse with a clear error.
  - All HTTP via an injectable client; tests use recorded fixtures (no network in tests).
- **Security scan:** run the runtime's guard as a subprocess:
  `<runtime python> -m company_agent.skills_scan <dir>` (add this tiny module to
  `runtime/company_agent/`, wrapping `tools/skills_guard.py` → JSON
  `{verdict, findings:[{category,pattern,file,line}]}`). Policy: `builtin` (Hermes bundled/
  optional + Company OS seed) allow any verdict but record it; `trusted` (repos owned by
  `anthropics`, `openai`, `huggingface`, `NVIDIA` — mirror `tools/skills_guard.py:21-33`)
  block `dangerous`; `community` (other GitHub, company-authored, uploads) block `caution`
  and `dangerous` → `status=quarantined` (stored for inspection, never materialized).
  If the runtime venv is missing → refuse imports of non-builtin skills (fail closed).
- **Materialization:** before each run, the company_agent adapter writes the agent's
  assigned **active** skills into `HERMES_HOME/skills/<slug>/` (copy from blob; replace
  the directory atomically; remove unassigned skills). Skills whose `requires_toolsets`
  are not available to that run are still materialized but flagged in the role prompt.

## 3. HR & hiring

- **Company creation** now also creates an active **HR agent** ("Harper", template
  `hr-partner`, reports to the founder, department "People" created if missing) — in the
  same transaction as the founder. Existing companies: `POST /hr/bootstrap` (owner) or the
  migration-time backfill job.
- **Hiring policy** (CompanyPolicy additions): `auto_activate_hires` (bool, default true when
  `autonomy_mode=autonomous`), `max_headcount` (exists), `hire_default_monthly_budget_micros`
  (null). A hire is auto-activated iff autonomous mode, `hire` ∉
  `human_approval_required_for`, headcount (non-retired, non-rejected) < max_headcount, and the
  requested budgets fit within the company monthly budget minus other agents' monthly
  budgets (when a company budget exists). Otherwise the agent is `proposed` and an Approval
  (`kind=hire`, approver per policy) is created; approving it activates the agent.
- **Agent API additions** (`/api/agent/v1`, enforced in services):

  | endpoint | who |
  |---|---|
  | GET `/agents?skill_tags=&family=&status=` (colleague search: name, role, family, skill slugs+tags, load = open tasks) | any agent |
  | GET `/skills/catalog?q=&source=` | HR, founder |
  | POST `/skills/install` `{source_ref}` (hermes or github) | HR |
  | POST `/skills/author` `{slug, name, description, tags, body_markdown, files?}` → scanned, `company_authored`, `community` trust | HR |
  | POST `/agents/{id}/skills` `{skill_ids or slugs}` / DELETE | HR (never self) |
  | GET `/role-templates`, POST `/role-templates` (custom) | HR, founder |
  | POST `/hires` `{role_template, name, role_title?, manager_agent_id, department, skill_slugs[], model_provider?, model_name?, monthly_budget_micros?, reason, task_id}` | HR only |
  | POST `/hiring-requests` `{required_skills[], role_hint, reason, task_id}` → creates a `hiring` task assigned to the active HR agent (idempotent per task_id+skills) | any manager / founder |

- **Company toolset additions** (runtime `tools/company_tools.py`): `company_find_colleagues`,
  `company_request_hire`, and HR-only (server-enforced) `company_search_skills`,
  `company_install_skill`, `company_author_skill`, `company_assign_skills`,
  `company_list_role_templates`, `company_create_role_template`, `company_hire_agent`.
- **Gap detection (control plane, deterministic):** when a task with `required_skills`
  becomes otherwise ready but has **no assignee**, the dispatcher picks the least-loaded
  active agent whose skills' tags ⊇ required tags (prefer same project lead's department).
  If none exists, it creates (once) a hiring request → hiring task for HR, and the task stays
  `backlog` with `blocked_reason="waiting_for_hire"`; when a hire with matching skills is
  activated, the task is auto-assigned. (Unassigned tasks without required_skills are left
  for the founder in M5.)
- **HR role playbook** (`hr-partner` instructions + `hiring-playbook` skill): read the hiring
  task, search the library/catalog for matching skills, install missing ones (prefer
  Hermes builtin, then trusted GitHub), author a company skill when nothing suitable exists
  (concise, procedural SKILL.md), pick/create a role template, hire with a fitting name and
  manager, assign skills, then complete the hiring task with a summary. Never hire beyond
  limits; never grant tools the role doesn't need.

## 4. Owner API & UI

- Owner API: `GET /skills` (filters: status, source_kind, tag, q), `GET /skills/{id}`
  (frontmatter, files list, scan report, assigned agents), `GET /skills/{id}/files/{path}`
  (text preview), `POST /skills/install`, `POST /skills/{id}/{archive,unquarantine}`
  (unquarantine = explicit human override, audited), `GET/POST/DELETE /agents/{id}/skills`,
  `GET /role-templates`, `POST /agents` accepts `role_template` (applies defaults).
- UI (web): Skills widget on the canvas (skills by family, quarantined count, recent HR
  hires/installs, open hiring requests), agent panel **Skills** tab (assigned skills,
  add/remove), Skills panel (library table + catalog search incl. GitHub + install button +
  skill detail with SKILL.md preview and scan report), hire dialog uses role templates
  (choosing a template pre-fills title, family, instructions, skills). Avatars use
  `role_family`.

## 5. Tests
- Role template loading/validation (all built-in YAML valid; every seed skill slug exists
  in the runtime checkout).
- Seeding is idempotent; blobs dedupe by sha256.
- License allowlist; size/file/symlink limits; GitHub install pins commit sha (fixtures).
- Scan policy per trust level; quarantined skills never materialize; runtime missing →
  non-builtin import refused.
- AgentSkill rules: no self-assignment, only HR/human, only active skills, tenancy.
- Hiring: auto-activation conditions (each condition flipped), approval path, headcount
  limit, budget fit; HR-only endpoints reject other agents (403).
- Gap detection: auto-assign by tags, hiring request created once, auto-assign after hire.
- Materialization writes exactly the assigned skills and removes stale ones.
- Live (documented, manual): HR agent on local Ollama handles a hiring task end to end.
