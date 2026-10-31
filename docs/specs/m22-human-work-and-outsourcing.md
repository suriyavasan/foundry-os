# M22 (backlog B3) — Human work and outsourcing through HR

Status: done (2026-10-31); verified with scripted runtimes and mocked web only (see "As built" at the end). Rules: `AGENTS.md`. Builds on M7 (tasks assigned to people), M15 (purchasing),
M18 (questions/deadlines). Decisions taken (B3 open questions): marketplaces Fiverr and Upwork first (plus
"Other"); contractors appear in the HR panel and in the org **List** view under "Contractors", not on the Map.

## 1. Human work (things an agent cannot do)

Agents can already be blocked on things only a person can do (sign a form, make a call, KYC, a physical errand,
a bank step). Today that becomes a vague block. Now it becomes a **human task**.

* Data (migration `0022_human_work_and_outsourcing`): `tasks.requires_human` (bool, default false),
  `tasks.human_category` (`signature|call|identity|payment|physical|account|legal|other`, nullable),
  `tasks.human_instructions` (Text, nullable), `tasks.requested_by_agent_id` (nullable FK agents).
* Agent tool `company_request_human_work(title, instructions, category, why_human, due_in_hours?=48,
  assignee_member_id?=None, blocks_my_task=true)` → agent API `POST /agent-api/human-work`.
  * Creates a task with `requires_human=true`, `kind=work`, assigned to the given member (must be an active member
    of the company) or unassigned (the **human pool** — any owner/admin/member may pick it up).
  * `blocks_my_task=true` (default, only when the run has a task): the agent's current task gets a dependency on
    the new human task (existing dependency mechanism) and the run ends with the task waiting; when the human
    task is done the agent's task becomes ready and its next run sees the person's completion comment and
    artifacts.
  * Limits (code): ≤ 3 open human tasks per agent, ≤ 20 per company (429 `human_work_limit`: "Do it yourself or
    ask the founder a question"). `why_human` required (≤ 300). Guardrail registry entry like other agent tools.
  * Human tasks are never dispatched to a runtime (already true for member tasks; enforce for the pool too).
* Humans: `GET /companies/{cid}/human-work?status=open|done&mine=bool` (`{items,next_cursor}`),
  `POST /companies/{cid}/human-work/{task_id}/claim` (assigns to me; pool only),
  completion through the existing `complete_task_as_member` (comment + artifacts),
  `POST .../human-work/{task_id}/decline {reason}` (owner/admin; the requesting agent is woken with the reason and
  its blocked task returns to ready with a `review_note`). Audited. Notification `human_work` to the assignee (or
  to owners/admins for the pool); chat apps (M19) deliver it with `notify.questions` preference.
* Deadline: overdue human tasks appear first; at due + 24 h owners/admins get one reminder notification (no
  auto-cancel — a person's job, not a timeout).

## 2. Outsourcing (freelancers) — draft, approve, post manually, record

Fiverr has no public buyer API, Upwork's API needs an approved partner key, and both marketplaces' terms forbid
unauthorized automation and scraping, so (until an official integration is approved and exercised) Foundry **never posts, messages, hires or pays on a marketplace by itself**. HR prepares everything; a person does
the outward-facing steps on the marketplace's own site, then records what happened.

* Data: `outsource_jobs` (tenant): `id, company_id, created_by_agent_id, task_id? (the work it serves),
  title (≤200), brief (Text ≤ 6000: context, deliverables, acceptance criteria, timeline), skills (JSON list ≤ 10),
  marketplace (fiverr|upwork|other), budget_micros (BigInteger, the proposed max), status
  (draft|pending_approval|approved|posted|hired|delivered|closed|cancelled|declined), approval_id?, listing_url?,
  freelancer_name?, freelancer_profile_url?, agreed_price_micros?, paid_micros?, ledger_entry_id?,
  delivered_artifact_ids (JSON), notes (Text?), timestamps`.
* HR tool (HR role only) `company_draft_outsource_job(title, brief, skills, marketplace, budget_usd, task_id?)`:
  creates the job and a **human approval** (kind `outsource_job`, amount = budget, risk medium) — always human,
  regardless of policy (outward-facing + money). Limits: ≤ 5 open jobs per company; budget must fit the company's
  remaining monthly budget (if one is set) and the per-job ceiling `policy.outsource_max_job_micros` (default
  $200, owner/admin only). The brief is checked by the existing DLP/secret scan; no secrets, no personal data.
* Approve → `approved`; decline/expire (M18 deadlines) → `declined`, HR woken.
* After approval, owner/admin actions (each audited, each validates the transition):
  * `POST /outsource-jobs/{id}/posted {listing_url}` (https URL on the marketplace's own domain for fiverr/upwork;
    any https for other).
  * `POST /outsource-jobs/{id}/hired {freelancer_name, freelancer_profile_url?, agreed_price_usd}` —
    `agreed_price ≤ budget` (else 422 asking for a new approval via "Raise budget", which creates a new approval
    for the difference).
  * `POST /outsource-jobs/{id}/delivered {artifact_ids[], notes?}` — the linked task (if any) gets the artifacts
    and becomes ready/its dependency resolves.
  * `POST /outsource-jobs/{id}/paid {amount_usd}` — records a ledger entry `cost_kind=verified`,
    `source=outsourcing` against the company (and the task's project), never above `agreed_price` without a new
    approval. Foundry does not move money.
  * `POST /outsource-jobs/{id}/close` / `cancel {reason}`.
* `GET /companies/{cid}/outsource-jobs` (`{items,next_cursor}`), `GET .../{id}`.
* Helper links (no fetching, no scraping): the UI offers "Open Fiverr" / "Open Upwork" (the marketplace's public
  search or post-a-job pages with the job's title as the query) and "Copy brief".

## 3. Web
* **HR panel** (HR agent's panel or a new "People ops" panel reachable from the org widget and the HR agent):
  tabs **Human work** (open/mine/pool/done; claim, complete with comment + attachments, decline) and
  **Freelancers** (jobs by status with the step actions above, copy brief, open marketplace, record forms; the
  approval card links to the job).
* My Work (existing) shows my human tasks with their category and instructions.
* Org **List** view: a "Contractors" group listing hired/delivered jobs' freelancers (name, job, status).
* Command Center chat: a human-work request appears as a bubble from the requesting agent ("needs a person:
  sign the NDA") with "I'll do it" (claim) and "Open".

## Tests
Agent tool limits, assignee must be a member of the company, pool claim races (second claim 409), blocking
dependency resolves on completion and the agent sees the comment, decline wakes the agent, human tasks never
dispatched, tenancy everywhere; outsourcing: HR-only tool, approval always human, budget ceiling + company budget,
transition validation (can't mark hired before approved/posted, agreed ≤ budget, paid ≤ agreed), listing URL
domain rule, ledger entry verified and in micros, no network calls anywhere, audit per transition.

## As built (deviations and notes)

* Human tasks are created by `services/human_work.py` directly (not through the agent task-creation rules): the person-only limits
  (3 open per agent, 20 per company, 429 `human_work_limit`) replace the per-run cap and the duplicate check. The agent's own task
  waits through a plain `TaskDependency`; `tasks.finish_success` parks it (`blocked_reason = waiting_for_human`) when the run ends
  with an unfinished human dependency, so plain runs and work-graph runs behave the same. Cancelling or declining the human task
  removes the edge and returns the waiting task to the queue with a `review_note` (a cancelled dependency would otherwise block
  forever). A human task cannot be given to an agent, is skipped by the dispatcher and by the "unowned work" watchdog.
* Pool tasks stay in `backlog` (no assignee, so readiness cannot make them ready) until somebody claims one (atomic conditional
  update, a second claim is a 409). Completion is the existing `POST /tasks/{id}/complete` (assignee or owner/admin); a pooled task
  must be claimed first.
* `outsource_job` is added to `ApprovalKind` and to the approval kind literal; it is never requestable through the generic agent
  approval route and `derive_approver` returns human for it before it looks at the policy or the autonomy mode. The approval is
  not chat-decidable (the person reads the brief in the app). The job also exists as `subject_type = outsource_job` on the approval,
  which is how the UI links an approval to its job. A budget raise is a second approval (`scope.op = raise_budget`) created by an
  owner/admin; the job keeps its budget until it is approved. A declined or expired raise changes nothing.
* `hired` may be recorded from `approved` or `posted`, and again while nothing was paid (to correct the agreed price, never above the
  approved budget). `paid` is once per job. `delivered` attaches the artifacts to the linked task's `output_refs` and wakes the task's
  assignee agent and HR. `outsource_jobs.paid` writes an `ExternalExpense` (`cost_kind=verified`, `source=outsourcing`).
* The brief, title and skills are scanned (secrets, own tokens, e-mail addresses, phone numbers, card numbers, internal memory) and
  refused with 422 when anything is found. The company budget check subtracts usage committed this month and what other open
  jobs may still cost.
* Notification `human_work` (assignee, or owners/admins for the pool; also the overdue reminder) reaches linked chats with the
  `questions` preference; each notification is one outbox row, rendered at send time and skipped once the task is closed.
* Web: People ops panel (`?panel=people-ops`, tabs Human work and Freelancers, per-job limit control for owners/admins), a job panel
  (`?panel=outsource:<id>`) for the steps, a chat bubble with "I'll do it" / "Open", My Work shows the category and instructions,
  the org List view has a "Contractors" group, approvals link to their job, the HR agent's panel opens People ops.
* Helper links: Fiverr `search/gigs?query=` and Upwork `nx/search/talent/?q=` (never fetched; the exact public paths were not
  checked against the live sites).
* Not verified: nothing was exercised against Fiverr, Upwork or a live model; every flow is covered with scripted runtimes, a mocked
  API in the web tests and in-process backend tests. The tests fail on any outbound socket connection during the outsourcing flow.
