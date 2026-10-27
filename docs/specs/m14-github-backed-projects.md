# M14 — GitHub-backed projects — spec

Owner direction (2026-10-27): "As part of onboarding, setting up the repo is key: start a new repo / project or
use an existing repo, and take the relevant input from the user. The whole agent team runs in full autonomous
mode and maintains the GitHub."

Status: **in progress.** Step 0 (this document, the schema, the contracts, the GitHub client and the test
fakes) is done; the five work packages follow. **Verified with fakes only** (a fake GitHub REST API and a local
smart-HTTP git remote). No call to GitHub, no live agent and no real coding CLI has been used; section 9 lists
the GitHub facts that must be confirmed against the official documentation before they are written into
`docs/github.md`.

The milestone is split into **step 0** (sequential groundwork: schema, contracts, fakes) and **five parallel work
packages**: WP-A (GitHub API side), WP-B (git engine and sync), WP-C (repository service, owner API, PR review),
WP-D (CLI), WP-E (web). Section 8 ends with the file-ownership table. Section 11 records how step 0 differs from
the first plan, and section 10 the owner decisions that are now fixed.

---------------------------------------------------------------------------------------------------------------------

## 0. What exists today (checked against the code on `main` @ 0cb59c3)

| Claim in the brief | Verified | Notes for implementers |
|---|---|---|
| `integrations/github.py` is a small client for the free-subdomain PR flow | yes | `GithubClient(ApiClient)`: `identity`, `repo`, `ensure_fork`, `sync_fork`, `branch_sha`, `create_branch`, `put_file`, `open_pull`, `pull`; `submit_registry_pr()`. No ETag, rate-limit or token-expiry handling. `base_url` comes from `Settings.github_api_url` |
| `github` provider in `integrations/catalog.py` with `github.read`, `domain.preview`, `domain.register_pr` | yes | `_t_github` stores `meta.login` and the classic scopes from `x-oauth-scopes`. The `help` text speaks only of forks |
| `github.pr.open` / `github.issue.comment` in `guardrails/registry.py` (EXTERNAL_COMMUNICATION, no executor) | yes | Also mentioned in `runtime/tools/company_tools.py` (`company_provider_action` description), `docs/api.md` line 968 |
| M12 `services/project_workspaces.py` | yes | `MAIN_BRANCH = "main"` is hard-coded in `ensure_repo`, `prepare` and `_has_main`. `_git()` has a fixed 60 s timeout and no way to add environment variables or `-c` options. `_merge` merges into whatever is checked out in `<project>/main`. Called only from `engine/executor.py` (`prepare`, `finalize`, `discard`) |
| `backend/app/services/onboarding.py` | **does not exist** | The web onboarding is driven by the client (`web/src/lib/onboarding.ts` reducer + `OnboardingFlow.tsx`) over existing endpoints (companies, AI setup, notes). The idea is posted as the first note in `afterAi()` (`seedCompanyIdea`) |
| `backend/app/cli/onboard.py` | **wrong path** | It is `backend/app/cli/commands/onboard.py` (the `Wizard` class, 1,688 lines) plus `backend/app/cli/onboarding.py` (`STEPS`, `SECRET_KEYS`, `WizardOptions`, `validate_options`). `seed_idea()` runs after all steps |
| Coding CLIs have no network for git | yes | per `docs/cli-runtimes.md`. The control plane does all git |
| — | **gap** | `services/secrets_scan.py` does not match fine-grained PATs (`github_pat_…`); only `gh[pousr]_…`. `runtime/company_agent/protocol.py` does. So the outbound DLP and `scrub()` miss the exact token type M14 recommends. Fixed in step 0 |
| — | **gap** | The worker's `EngineContext` has no `IntegrationContext` (HTTP client, cipher), so the worker cannot call providers today. WP-B adds a small `RepoEnv` |
| — | fact | `guardrails/engine.check()` only accepts agent principals; the dispatcher already runs a platform action under an agent's context (`dispatcher._agent_ctx`, used for `model.paid_fallback`). M14 reuses that pattern for system GitHub operations |
| — | fact | `retry_policy.TRANSIENT_CODES` already contains `merge_conflict` |
| — | fact | `reachability.public_url(ic)` and `reachability.tunnel_is_ephemeral(ic)` exist (needed for the webhook gate) |
| — | fact | git 2.56.0 is on this machine (`merge-tree --write-tree` is available for the fake remote) |

---------------------------------------------------------------------------------------------------------------------

## 1. Decisions (recorded in `docs/architecture.md` §9 as D38 to D44)

| # | Decision |
|---|---|
| D38 | **GitHub-backed project.** A project may have one remote repository. The M12 project repository (`<workspaces>/<company>/projects/<project>/main`) becomes a clone of the remote that only the control plane controls. Agents and coding CLIs never touch the network or the token. Each project has one **integration branch** `foundry/<project-slug>` (the M12 `main` branch for remote projects). Run worktrees branch from it and merge back `--no-ff` (M12 is unchanged). History is append-only: upstream changes come in through **merge commits, never rebase**, and **nothing is ever force-pushed**. |
| D39 | **Credentials.** A fine-grained personal access token is stored once in the encrypted credential vault behind the M8 GitHub connected account. Git authenticates through `GIT_ASKPASS` (a static script) that reads the token from the git child's environment only: not argv, not `.git/config`, not the remote URL, and no credential helper. Before every network operation the control plane rewrites `.git/config` from the database ("config pinning"). A GitHub App is designed for (`auth_kind`), not built in M14. |
| D40 | **Merge modes** (owner-only repository policy): `pr_human_merge` (Foundry opens PRs and a human merges), `pr_auto_merge` (Foundry merges after its internal review and green CI), `direct_push` (push to the default branch, only when that branch is unprotected). Foundry merges only when GitHub reports `mergeable_state` `clean` (or `has_hooks`) **and** the PR head equals the internally reviewed SHA (passed as `sha` to the merge API). An admin token therefore can never bypass branch protection through Foundry. |
| D41 | **Polling first.** Every repository is polled (default 5 min) with conditional requests (ETag). Webhooks are optional, offered only on a stable public URL (never on a quick tunnel), and only *poke* a sync: their payload is never trusted or used. |
| D42 | **GitHub content is untrusted input.** Issues, comments, reviews, CI logs and alerts are stored clipped, scrubbed and flagged, handed to agents only inside `<untrusted source="github">` blocks, filtered by author association, and turned into a bounded number of tasks. |
| D43 | **Push gate.** Every commit that is about to leave the machine is scanned: every commit's added lines, not just the net diff. The scan covers credential-shaped strings, the company's own stored secrets, internal-only memory, blocked file names, protected paths (`.github/workflows/**` by default) and size limits. The same scan runs when a run is merged into the integration branch of a remote-backed project, so secrets never reach the integration branch. |
| D44 | **System GitHub operations go through the guardrail engine.** Push, PR open/update, merge and branch delete are registered actions of `kind="system"` (not callable through `provider-actions`). They are checked with `engine.check` under the project lead's context (else the AI Co-founder) and carry an action-level default decision (`ActionSpec.default_decision`). Emergency stop, human deny/approval rules, rate limits and the guardrail log all apply. |

---------------------------------------------------------------------------------------------------------------------

## 2. Onboarding and repository choices

### 2.1 The three choices

| | (a) New GitHub repository | (b) Existing GitHub repository | (c) No GitHub |
|---|---|---|---|
| What happens | Foundry creates `owner/name` (empty, `auto_init=false`), clones it, makes the initial commit (README with the company name and idea, a minimal `.gitignore`) on the chosen default branch and pushes it (an initial push to an empty repo needs no PR) | Foundry clones the repository, creates `foundry/<slug>` from the default branch, and creates the **codebase onboarding task** (§2.4) before any other work in the project | Today's behaviour: local M12 repository, no remote |
| Project | Created by the step (name, default = repo name) and bound | Same | None created at onboarding |
| Default `merge_mode` | `pr_auto_merge` (the company owns the repo; full autonomy) | `pr_human_merge` (humans already work there) | — |
| Default `issue_intake` | `collaborators` | `labeled` (label `foundry`); open issues that exist before binding are **not** imported | — |
| Default `ci_mode` | `if_present` | `required` if `.github/workflows/` exists on the default branch, else `if_present` | — |
| Default `docs_upkeep` | true | false | — |

The company setting `company_policies.new_project_repository` decides what happens to **later** projects the AI
Co-founder creates: `local` (default) or `company_repo` (the new project gets its own binding to the company's
default repository, with its own work branch `foundry/<slug>` and its own clone). Onboarding with (a) or (b) sets
it to `company_repo` and marks the binding `is_company_default`. Creating new repositories for later projects is
the agent action `github.repo.create`, which always needs a human approval (§4.2).

### 2.2 Exact inputs

**Token (both a and b).**

| Token | Use | Requirements |
|---|---|---|
| **Fine-grained PAT (recommended)** | (a) and (b) | Resource owner = the user or the organisation that owns or will own the repo. Repository access: (b) *Only select repositories* → the repo; (a) *All repositories* (needed to create one) and then narrowed to the new repo, **or** create an empty repo on github.com yourself and use (b) with the "empty repository" path, which needs no Administration permission. Expiry: up to the owner (Foundry warns before it expires, §5.6) |
| Classic PAT | supported, with a warning (all repos of the account) | `repo` (or `public_repo` for public repos only). `workflow` only if `allow_workflow_changes`. `admin:repo_hook` only for webhooks. Never `delete_repo` |
| GitHub App | **not in M14** (judgment below) | — |

Fine-grained repository permissions (shown as a checklist in the step, web and CLI):

| Permission | (a) new | (b) existing | Used for | Missing means |
|---|---|---|---|---|
| Metadata | Read (automatic) | Read | everything | cannot work |
| Contents | Read and write | Read and write | clone, fetch, push, merge API, releases | cannot bind (`token_cannot_push`) |
| Pull requests | Read and write | Read and write | open, update, comment, merge | PR modes refused; only `direct_push` possible |
| Issues | Read and write | Read and write (Read if intake is off) | intake, comments, labels | intake / comments disabled (capability flag) |
| Commit statuses | Read | Read | CI state | CI read from Actions / check runs only |
| Actions | Read | Read | workflow runs, failure logs | CI fix tasks without logs |
| Checks | Read *(if the token page offers it — **unverified**, see §9)* | Read | check runs | fall back to statuses / Actions |
| Dependabot alerts | Read | Read | security alerts | alerts off |
| Administration | Read and write (only to create the repo; remove afterwards) | not needed | `POST /user/repos`, `POST /orgs/{org}/repos` | (a) refused with the exact message; suggest the empty-repo path |
| Workflows | **No** (only if `allow_workflow_changes`) | No | pushing `.github/workflows/**` | workflow edits are refused at merge time anyway |
| Webhooks | Read and write only if webhooks are enabled | same | `POST /repos/{o}/{r}/hooks` | polling only |

Organisation notes shown in the step: an organisation may require an admin to approve a fine-grained PAT (until
then, the API answers 403 and the step shows "pending organisation approval"), and SAML SSO may require authorising
the token (the `X-GitHub-SSO` header is shown with its URL).

**GitHub App, judged.** For a self-hosted, single-owner install, a GitHub App needs these steps: app registration
(the manifest flow can do it, but its redirect needs a browser that reaches this server, and over SSH that means a
port-forward), installing the app on the repos, storing an RSA private key, and minting a JWT and then
installation tokens every hour. Benefits:

- a bot identity: its PRs can be approved by the owner (with a PAT the owner cannot approve PRs opened under their own name);
- 1-hour tokens;
- Checks write;
- webhooks built in.

That is a better identity, but it means twice the setup for a person who just wants to paste one token. **M14 ships
PAT only.** It stores `auth_kind = "pat"` and reserves `"app"`. The "cannot approve my own PR" problem is solved in
M14 by recommending a free machine-user account for the token (docs/github.md, WP-D). App support is a follow-up
(M14b). (Owner decision 2.)

**Other inputs.**

| Input | (a) | (b) | Validation |
|---|---|---|---|
| Project name | default: repo name | default: repo name | 1-200 chars; or pick an existing project after onboarding |
| Owner | default: token login; or an org (typed or picked from `GET /user/orgs`) | from `owner/name` | `^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$` |
| Repository | name (default: slug of the company name) | `owner/name` or `https://github.com/owner/name(.git)` | name `^[A-Za-z0-9._-]{1,100}$`, not `.`/`..`, no `.git` suffix |
| Visibility | `private` (default) / `public` (needs `confirm_public: true`: "your agents' code will be public") | read from GitHub | — |
| Description | default: the idea (≤ 350) | — | no secret-like text (`find_secret`) |
| Default (base) branch | default `main` | default: the repo's; may pick another existing branch (e.g. `develop`) | ref-format check; must exist for (b) |
| Work branch | `foundry/<project-slug>` (editable) | same | must start with `foundry/`, differ from the base branch |
| Branch protection | Foundry **reads and respects** protection; it does not create it in M14 (protection on private repos needs a paid GitHub plan; the step links to the settings page) | same | — |
| Merge mode | `pr_auto_merge` | `pr_human_merge` | `direct_push` is refused when the base branch is protected or a ruleset requires a PR (409 `branch_protected`) |
| CI expectation (`ci_mode`) | `if_present` | `required` if workflows exist | `required`: merge only with ≥ 1 check and all green; `if_present`: all reported checks green, or none reported within `ci_wait_seconds`; `ignore` (not recommended) |
| Issue intake | `collaborators` | `labeled` + label | §4.1 |

Where the defaults for (a)/(b) live: `schemas/repository.py::default_policy(mode)` (step 0), used by web, CLI and
service alike (the web reads them from the OpenAPI defaults and the inspect response; it does not duplicate them).

### 2.3 Flow (the same HTTP calls from the web and from `foundryos setup`)

1. `POST /api/companies/{cid}/credentials` `{name: "GitHub token", kind: "api_key", value}` → `credential_id` (existing).
2. `POST /api/companies/{cid}/connected-accounts` `{provider: "github", label: "GitHub", credential_id}` (existing):
   the check now also stores `meta.token_kind` (`fine_grained` | `classic` | `other`, derived from the token prefix
   inside the control plane), `meta.token_expires_at` (from the `GitHub-Authentication-Token-Expiration` response
   header, when present) and `meta.login`. If a working GitHub account already exists, the step offers to reuse it.
3. `POST /api/companies/{cid}/github/inspect` `{connected_account_id, full_name?, owner?}` → `GithubInspectResult`:
   login, token kind, expiry, owner candidates, and for a repo: exists / accessible / archived / empty / size /
   default branch / protection summary / has workflows / push allowed / capabilities / `missing_permissions[]` /
   `warnings[]`.
4. `POST /api/companies/{cid}/repositories` (`RepositoryCreate`, §6.3) → 201 `ProjectRepository` with status
   `creating` (a) or `cloning` (b); a `repo_clone` job is enqueued.
5. Poll `GET /api/companies/{cid}/repositories/{rid}` (or SSE `repository.updated`) until `active` or `error`.
6. The idea becomes the first Founder's note **after** this step (web: moved from `afterAi` to the end of the repo
   step; CLI: `seed_idea()` already runs after all steps), so the Co-founder's triage sees the project with its
   repository. The Co-founder state shows `repository` on project rows, and the triage prompt line (WP-C) says:
   "If an active project already exists for this idea (the Founder created it with a repository), link it to your
   goal with `project.update` instead of creating a new project."

**Web** (WP-E): the setup flow becomes `welcome → account → company → ai → repo → team` (gate mode unchanged). The
`RepoStep` has three cards (new / existing / no GitHub); there is no pre-selected choice and "Skip" = (c). Its
states: token → inspect results (permission checklist with what is missing and which feature it turns off) →
details → creating/cloning progress → done. Error and "not available" (`501 {available:false, milestone:"M14"}`)
states are explicit. `TeamStep` adds a "Code" line: `owner/repo · <merge mode label>`.

**CLI** (WP-D): new step `repo` in `STEPS = ("service", "account", "2fa", "company", "ai", "work", "repo",
"public-url")`.

| Flag | Values | Notes |
|---|---|---|
| `--github` | `new` \| `existing` \| `none` | non-interactive default `none` (today's behaviour), like `--public-url off` |
| `--github-repo` | `OWNER/NAME`, `NAME` (new: owner = token login) or a github.com URL | required for `existing` |
| `--github-visibility` | `private` \| `public` | new only; `public` with `--non-interactive` also needs `--github-public-ok` |
| `--github-default-branch` | branch | new: default `main`; existing: the repo's |
| `--github-merge` | `pr-human` \| `pr-auto` \| `direct` | default per choice |
| `--github-ci` | `if-present` \| `required` \| `ignore` | default per choice |
| `--github-issues` | `off` \| `collaborators` \| `labeled` \| `everyone` | default per choice |
| `--project-name` | text | default: repo name |
| secret `github_token` | `--secrets-stdin` line `github_token=…` or a hidden prompt | added to `SECRET_KEYS`; never argv |

Interactive: a menu "1) New GitHub repository 2) Existing repository 3) No GitHub". It prints the permission
checklist and the token page URL (`https://github.com/settings/personal-access-tokens/new`), then asks for the
token with a hidden prompt, prints the inspect result, asks the remaining questions, and waits for `active` with a
spinner (non-TTY: dots). Rerunning: "Already done: <owner/repo> bound to <project>". `--only repo` reruns the step
and `seed_idea()` also runs for `--only repo`.

### 2.4 Codebase onboarding task (option b, and any binding to a non-empty repo)

Created by the system when the clone becomes `active`, the repo has at least one file, and the company is
AI-ready (otherwise `onboarding_status = pending`, and it is created by the first sync after the gate opens).

- `TaskCreate`: `kind="work"`, `project_id`, title `Learn the codebase of <owner/name>`,
  `idempotency_key="repo-onboard:<repository_id>"`, `input_refs=["repo:onboard:<repository_id>"]`, assignee = the
  project lead when there is one (else unassigned: the existing lead and skill routing assigns it), reviewer =
  the default.
- Workspace: a **read-only** checkout of the work branch head (`WorkspaceSpec(kind="review", read_only=True)`), so
  nothing it writes can be merged.
- Description (deliverables), and `acceptance_criteria` checking the same:
  1. an artifact `Architecture note — <owner/name>` (Markdown, ≤ 30 KB: purpose, stack, layout, how to build /
     test / run, conventions, CI, risks, open questions);
  2. 3–8 `memory.propose` entries tagged `codebase` and `repo:<owner/name>` (stack, commands, conventions, where
     things live).
- Dispatch hold: until `onboarding_status = done`, other **work** tasks of that project are not dispatched (reason
  `codebase_onboarding`). Planning, review and hiring tasks are not held. When the onboarding task is `done` →
  `done`; when it is `failed` or `cancelled` → `failed` + a warning alert (`github_onboarding_failed`), and the
  hold is lifted (it must never block the project forever).
- The architecture note is **not** committed to the repository in M14 (owner decision 5).

### 2.5 Adding or changing a repository after onboarding

- **Repository settings** of a project (web: project row → "Connect GitHub"; CLI: `foundryos repo connect`) use the
  same `POST /repositories` with `project_id`.
- A local M12 project with history, connected to an **empty** remote: `publish_local: true` pushes the local
  `main` history (after the push gate scans **all** of it) as the remote default branch, then switches to the
  `foundry/<slug>` flow (`origin = "published"`).
- A local project with history, connected to a **non-empty** remote: the old local repository is moved aside to
  `projects/<project>/local-<UTC timestamp>` (kept, never deleted), the clone replaces it, and the response and an
  alert say so. Unrelated histories are never merged automatically.
- **Change repo**: `disconnect` and then `connect` again. `disconnect` is refused (409 `repository_busy`) while a
  Foundry PR is open in `merging`, or while a run of the project is active. A disconnected binding keeps its row
  (`status = disconnected`) and its clone; the project then works locally on `work_branch` (the workspace reads
  the branch from the row, so local work continues).
- **Replace the token**: the existing `POST /credentials/{credential_id}/rotate`, then `POST …/{rid}/sync`.
- **Pause / resume** sync (owner); `status = paused` means no network at all; local work continues.

---------------------------------------------------------------------------------------------------------------------

## 3. Repository ↔ project mapping, git topology and the sync loop

### 3.1 Layout

```
<workspaces>/<company>/projects/<project>/main     clone of origin; checked-out branch = work_branch (foundry/<slug>)
  refs/remotes/origin/<default_branch>             mirror of the base branch (fetch only)
  refs/remotes/origin/<work_branch>                the remote PR head branch (fetch only)
  refs/heads/<work_branch>                         integration branch (M12 merges land here)
  refs/heads/run/<run_id>                          M12 run branches (unchanged)
  refs/foundry-archive/<branch>                    local copy of a remote foundry/* branch before it is deleted
```

For local-only projects nothing changes (branch `main`). `project_workspaces` gets the branch from a
`WorkspaceSpec` and no longer assumes `main`.

### 3.2 Git safety (all in `services/repo_git.py`, the only module that runs network git)

- `_git()` in `project_workspaces` becomes public as `run_git(args, *, cwd, git_dir=None, work_tree=None,
  identity=None, check=True, timeout=GIT_TIMEOUT_SECONDS, extra_env=None, extra_config=())`. Every existing call
  keeps its behaviour.
- Network calls add these `-c` options: `credential.helper=` (empty resets the list), `core.askPass=`,
  `protocol.allow=never`, `protocol.https.allow=always` (plus `protocol.http.allow=always` **only** when
  `settings.github_allow_insecure_http`, i.e. tests), `http.followRedirects=false`, `submodule.recurse=false`,
  `fetch.recurseSubmodules=false`, `transfer.fsckObjects=true`, `core.sshCommand=/bin/false`, `lfs`-free (LFS is
  not supported in M14; filters cannot run because no filter config is loaded).
- Environment for network calls (on top of M12's `_env`): `GIT_ASKPASS=<data>/run/git-askpass` (a 0700 static
  script written by `askpass_script(settings)`). It prints `x-access-token` when the prompt contains `Username`,
  and `$FOUNDRY_GIT_TOKEN` when the prompt contains `Password` **and** the prompt names `$FOUNDRY_GIT_HOST`;
  anything else prints nothing, so auth fails instead of leaking. Also set: `FOUNDRY_GIT_TOKEN`,
  `FOUNDRY_GIT_HOST`, `GIT_TERMINAL_PROMPT=0`.
- The remote URL is `f"{settings.github_web_url}/{full_name}.git"` with no userinfo. Before every fetch or push,
  `pin_config(repo, binding)` rewrites `.git/config` atomically from the binding (core settings, `remote.origin.url`,
  the fetch refspecs) and rewrites `.git/info/exclude` (M12 `EXCLUDES`). A planted `url.*.insteadOf`,
  `credential.helper`, `core.hooksPath` or `remote.*.pushurl` is therefore gone before the token exists in any
  process.
- Clone/fetch/push timeout `settings.git_network_timeout_seconds` (600). Stderr is passed through `_mask(token)` and
  `scrub()` before it is logged or stored.
- **Push**: `push(settings, binding, auth, repo, *, sha, branch)` builds exactly
  `git push --porcelain origin <sha>:refs/heads/<branch>`. `assert_safe_push_argv(argv)` refuses `-f`, `--force*`,
  `--mirror`, `--delete`, `-d`, `--tags`, `--all`, `--prune`, a refspec starting with `+` or `:`, and any
  destination outside `refs/heads/`. A test greps the backend for `"push"` in git argv outside `repo_git.py` and
  fails. A rejected non-fast-forward raises `GitNonFastForward`; the caller fetches and integrates, then retries
  **once**.
- **Residual risk (documented in docs/security.md):** the token is in the environment of short-lived git children.
  Agents run as the same OS user, so a process that can execute arbitrary code (not the M10 CLIs as configured; the
  company agent with a local terminal) could read `/proc/<pid>/environ` on Linux during that window. Mitigation:
  a fine-grained token limited to the bound repos; the push gate; no shell for CLIs.

### 3.3 Branch flow (PR modes)

1. **Local work.** M12 is unchanged: run worktree `run/<id>` from `work_branch`, then a `--no-ff` merge into
   `work_branch` under the per-project lock. New for remote projects: before merging, finalize scans the run's commit
   (§5.4). A finding fails the run with `secret_detected` or `protected_path`, and the run branch is deleted, not
   kept.
2. **Sync (every `poll_interval_seconds`, or when poked)**, all git steps under the M12 project lock:
   1. `fetch`: `+refs/heads/<default>:refs/remotes/origin/<default>` and
      `+refs/heads/<work>:refs/remotes/origin/<work>` (a missing work branch is fine).
   2. Upstream rewrite check: if the stored `last_fetched_default_sha` is not an ancestor of the new
      `origin/<default>`, set `sync_state = upstream_rewritten`, raise the critical alert
      `github_upstream_rewritten`, and push nothing until the owner resets the integration branch (§6.2).
   3. Human commits on the work branch (someone used "apply suggestion" on GitHub): when `origin/<work>` has commits
      that are not in `work`, merge it in (fast-forward if possible, else `--no-ff`).
   4. Upstream: when `origin/<default>` has commits that are not in `work`, run
      `git merge --no-ff -m "Merge <default> into <work>" origin/<default>`.
   5. A conflict in 3 or 4: `merge --abort`, then the **conflict task** (below). `sync_state = upstream_conflict`.
      Pushing continues: the PR shows as conflicting and auto-merge waits.
3. **Ship.** If no Foundry PR is open, `work` has non-merge commits that are not in `origin/<default>`
   (`git rev-list --count --no-merges origin/<default>..<work>` > 0), the last merge into `work` is older than
   `pr_batch_quiet_seconds`, and `pr_opening_paused` is false: push gate → push `work` → open the PR
   `work → default`. If GitHub answers 422 "already exists", adopt the open PR found by
   `GET /pulls?head=<owner>:<work>&state=open`.
4. **One open Foundry PR per binding.** While it is `reviewing`, `approved` or `merging`, its head is **frozen**:
   new merges into `work` are not pushed. Upstream-only movement of the head (GitHub's "update branch" merge) does
   not invalidate a verdict when `git rev-list --no-merges <head> ^<review_sha> ^origin/<default>` is empty.
5. After a merge on GitHub (by Foundry or a human, merge or squash), the next sync does step 2.4 (upstream merge into
   `work`). The next PR's three-dot diff then contains only new work. GitHub may delete the head branch; the next
   push recreates it.

**Direct push mode**: after steps 2.1–2.4, if `work` is ahead and `origin/<default>` is an ancestor of `work`, the
base branch is unprotected and the push gate passes, push `work:<default>` (no PR). Foundry's merge commits then
appear in the default branch history (documented). If the base branch is protected (from the protection summary or
a push rejection), Foundry switches to PR behaviour for that sync and raises the warning alert
`github_direct_push_refused`; the owner's policy is not changed.

**The conflict task** (`repo_sync._ensure_conflict_task`): kind `work`, project, title `Resolve conflicts between
<default> and <work> (<files>)`, `idempotency_key="repo-conflict:<repository_id>:<origin_sha>"`,
`input_refs=["repo:upstream:<repository_id>:<origin_sha>"]`, assignee = the agent of the most recent merged run that
touched a conflicting file, else the project lead. Its workspace kind is `upstream_merge`:

- **prepare**: `worktree add -b run/<id> <path> <work>`, then in the worktree `merge --no-ff --no-commit <origin_sha>`,
  which leaves conflict markers. `MERGE_HEAD` lives in the trusted worktree gitdir. The prompt lists the conflicting
  files.
- **finalize**: refuses when any previously conflicting file still contains a line matching
  `^(<{7}|={7}|>{7})( |$)` (error `merge_conflict_unresolved`, retryable); otherwise `add -A`, `commit --no-edit`
  (a two-parent merge commit), then the normal M12 merge into `work`.

The task runs at most twice per origin SHA (the idempotency key and `max_attempts` 2). After that the warning alert
`github_upstream_conflict` tells the owner: "Merge `<default>` into `<work>` yourself and push to the work branch;
Foundry will pick it up" (step 2.3).

### 3.4 PR state machine (`repo_pull_requests.stage`, driven only by `repo_sync._advance_pull_request`)

| Stage | Entered when | Leaves to |
|---|---|---|
| `opening` | push done, PR create in progress (crash-safe: the row is created before the API call; adopted on retry) | `reviewing` (or `approved` when `pr_internal_review=false`) |
| `reviewing` | the head was pushed; `repo_reviews.ensure_pr_review_task()` created the review task (§3.5); `review_sha = head_sha` | `approved` (verdict accept on `review_sha`), `changes_requested` |
| `changes_requested` | internal verdict `request_changes`, a human review `CHANGES_REQUESTED` by an allowed author, or a CI failure on the head; each creates `repo_items` rows with `pull_request_id`, `revision` and `task_id` | when every feedback task of the current `revision` is terminal: push gate → fast-forward push `work` → `revision += 1` → `reviewing`. When `revision > max_pr_revisions` or `ci_fix_attempts > max_ci_fix_attempts` → `awaiting_human` |
| `approved` | the verdict was accepted (or internal review is off) | CI evaluation (§3.6): failure → `changes_requested` (CI fix); green → `pr_auto_merge`: `merging`; `pr_human_merge`: `awaiting_human` |
| `merging` | `mergeable_state in ("clean", "has_hooks")` and `head_sha == review_sha` (modulo upstream-only); guard `github.pr.merge` allowed | `merged`; GitHub 405/409 → `approved` (re-evaluated next sync; `last_error`); `blocked` / `behind` / `dirty` → `awaiting_human` or the conflict path |
| `awaiting_human` | human merge mode, protection needs a human approval, limits exhausted, or `ci_mode=required` with no checks after `ci_wait_seconds` | `merged` / `closed` when detected; `changes_requested` on new human feedback |
| `merged` | `merged: true` from the API (`merged_sha`, `merged_by`) | terminal |
| `closed` | closed without merge (by anyone) | terminal; sets `pr_opening_paused = true`, raises a warning alert `github_pr_closed` and wakes the lead (trigger `github`, "PR #N was closed without merge; read its comments"). No new PR until new commits are merged into `work` after the closed head, or the owner calls `…/pull-requests/resume-opening` |

When `behind` is reported and protection requires an up-to-date branch, Foundry calls the update-branch API
(`PUT /pulls/{n}/update-branch` with `expected_head_sha`), then fetches and integrates (step 2.3); the
upstream-only rule keeps the verdict.

PR text (deterministic, `repo_sync._pr_text`):

- Title: the single task title, or `<project name>: <n> changes`.
- Body:
  - the tasks from `merged_runs(origin/<default>, head)` (the run ids in the M12 merge messages `Merge run/<id>: …`
    → `task_runs` → tasks), as a list with agent names;
  - `Refs #<n>` for issues linked to those tasks (`input_refs` `github:issue:<repository_id>:<n>` on the task or
    its parent);
  - the company's AI-disclosure footer (`providers.disclosure_footer`).

The body passes the engine's DLP as EXTERNAL_COMMUNICATION.

### 3.5 Internal PR review

`repo_reviews.ensure_pr_review_task(db, binding, pr, now) -> Task`:

- kind `review`, project, title `Review pull request #<n>: <title>`;
- `idempotency_key="repo-pr-review:<pr_id>:<revision>"`, `input_refs=["github:pr:<pr_id>"]`;
- assignee: an active QA-family agent among the project lead's reports, else the project lead, else the Co-founder
  (never the agent that did most of the merged runs, when an alternative exists);
- the description holds the diffstat and the instruction "Record your verdict with
  `company_review_pull_request(pull_id, decision, note)`".

A `PR #<n> diff` artifact (`repo_git.diff_for_review(origin/<default>, review_sha)`, ≤ 200 KB, scrubbed) is
attached to the review task. Workspace: a read-only detached worktree at `review_sha`.

Verdict: agent route `POST /api/agent/v1/repo/pulls/{pull_id}/review` (action `repo.pr.review`, INTERNAL_WRITE),
body `{decision: "accept" | "request_changes", note}`, handled by `repo_reviews.record_verdict(db, rc, pull_id,
decision, note)`:

- only the assignee of the open review task of that PR (otherwise 403);
- `pr.head_sha != pr.review_sha` → 409 `pull_request_changed`;
- writes `verdict`, `verdict_note`, `verdict_sha`, `verdict_agent_id` (never `stage`);
- completes the review task;
- `request_changes` also creates the fix task `Address the review of PR #<n>` (kind work, assignee = the project
  lead, `repo_items` kind `internal_review`, `revision` = the PR's).

A review task that ends without a verdict: the sync re-issues it (key suffix `:r1`, `:r2`), then gives it to the
Co-founder, then raises the alert `github_pr_review_stalled`. Same ladder shape as M12 D1, implemented in
`repo_sync`, with bounds recorded in `autonomy_interventions` (`kind="pr_review"`).

### 3.6 CI evaluation (`repo_sync._checks_for(sha)`)

Sources, in order of the binding's capabilities: check runs for the ref, the combined commit status, and Actions
workflow runs for `head_sha`. Normalized as `[{name, source, status, conclusion, url}]` (≤ 50 entries).

| Normalized state | Rule |
|---|---|
| `success` | ≥ 1 entry, all completed, conclusions in `success`, `neutral`, `skipped` |
| `failure` | any conclusion in `failure`, `timed_out`, `action_required`, `startup_failure`; `cancelled` is ignored unless every entry is cancelled |
| `pending` | anything else with entries |
| `none` | no entries |

`if_present`: `none` for `ci_wait_seconds` after the push counts as green. `required`: `none` is never green.
`ignore`: always green. Required checks from the protection summary must all be present and `success` (GitHub
enforces this too).

### 3.7 Sync orchestration (`repo_sync.sync_repository`)

```
skip unless status == active and company status active and not emergency stop
lease: UPDATE project_repositories SET sync_started_at=now WHERE id=? AND (sync_started_at IS NULL OR < now-10min)
auth = RemoteAuth(host, token) via github_accounts.client_for_binding (decrypt once; never stored)
snapshot = SyncSnapshot(...)       # all GitHub API reads happen here (ETag, rate limit handled once)
  repo meta (default branch renamed? archived? visibility changed?), protection (every 6 h), open Foundry PR +
  reviews + checks on its head, issues since last_issues_sync_at, checks on the default head (if fix_ci_failures),
  dependabot alerts (hourly)
git: fetch → rewrite check → integrate work branch → integrate upstream (under project lock)
if company paused (human or breaker): stop here (reads only)
repo_maintenance.process(db, env, binding, snapshot, now)    # WP-A: intake → items → tasks
_advance_pull_request / _direct_push                          # WP-B
_ensure_onboarding_task / onboarding status                   # WP-B (uses the task spec in §2.4)
update binding: last_synced_at, last_fetched_default_sha, integration_head_sha, next_sync_at (+backoff),
  sync_failures, rate limits, etags, sync_state; release lease; emit repository.updated (only if something changed)
```

Scheduler: `repo_sync.enqueue_due(session_factory, now)` is called from `Scheduler.tick` every 30 s (inside the
not-stopped branch). It enqueues `repo_sync` jobs with `dedupe_key=f"repo_sync:{id}:{next_sync_at:%Y%m%dT%H%M%S}"`.
Worker job kinds `repo_clone` and `repo_sync` are handled by `repo_sync.run_job`, with a lease-extension thread
(`jobs.extend_lease` every 20 s) because a clone can outlast `LEASE_SECONDS`. `request_sync(db, rid, reason, now)`
sets `next_sync_at = now` (API "sync now", webhook, and the executor after a merge into `work` of a remote project).

Engine hooks (WP-B):

- `executor.py` calls `repo_sync.workspace_spec_for(db, task)` before `prepare`:
  - `repo:onboard:` → read-only review of `work`;
  - `github:pr:` → read-only review at `review_sha`;
  - `repo:upstream:` → kind `upstream_merge`;
  - a project with an active or disconnected binding → work branch, `scan_secrets=True`, `protected_paths`,
    `max_file_bytes`;
  - otherwise the M12 defaults.
- `dispatcher.py` skips tasks for which `repo_sync.dispatch_hold(db, task)` returns a reason: the binding is
  `pending`, `creating` or `cloning` (`repository_not_ready`), or the codebase onboarding hold applies.

---------------------------------------------------------------------------------------------------------------------

## 4. Autonomous GitHub maintenance

### 4.1 What the agents do on their own

| Activity | Trigger (deterministic, `repo_maintenance`) | Who acts | Bounds | GitHub actions used (default decision) |
|---|---|---|---|---|
| **Issue triage** | a new or reopened issue (not a PR) passing `issue_intake`. `collaborators`: `author_association` in OWNER, MEMBER, COLLABORATOR. `labeled`: has `intake_label`. `everyone`. `off` | **planning** task `Triage issue #<n>: <title>` to the project lead (`idempotency_key="repo-issue:<rid>:<n>:<reopen_count>"`, `input_refs=["github:issue:<rid>:<n>"]`, body in `<untrusted source="github">`, injection flags). The lead creates subtasks, comments, labels or closes | `max_open_intake_tasks` (default 5) open intake tasks per repo; the rest wait as `repo_items.status = queued` (FIFO). Issues older than the binding are never imported | `github.issue.read` (allow), `github.issue.comment` (peer review), `github.issue.update` (peer review), `github.issue.create` (peer review) |
| **PR review comments** | on a Foundry PR: a review `CHANGES_REQUESTED`, or review/line comments by an allowed author (`review_authors`) after `last_review_seen_at` | work task `Address review on PR #<n>` (lead; items kind `review`, `revision` = the PR's); replies through `github.pr.comment` | one task per review (`repo-review:<pr_id>:<review_id>`); comments by non-allowed authors → `repo_items` `ignored` (`author_not_allowed`) | `github.pr.read` (allow), `github.pr.comment` (peer review) |
| **CI failures** | (1) the Foundry PR head: §3.6 failure → task `Fix failing CI on PR #<n>: <checks>` (items kind `check_failure`); (2) the default branch head when `fix_ci_failures`: task `CI is failing on <default>` | lead → engineer | one open CI task per (branch, workflow); per PR `max_ci_fix_attempts` (3) → `awaiting_human`; logs: tail ≤ 32 KB per failed job, scrubbed, untrusted | `github.checks.read` (allow) |
| **Dependabot / security alerts** | open alerts with severity ≥ `security_alerts` (default `high`), polled hourly | work task `Fix <package> <severity> vulnerability (<GHSA>)` | one task per alert (`repo-alert:<rid>:<number>`); fixed or dismissed alerts → items `done`; at most 3 alert tasks open per repo | `github.alerts.read` (allow). Dismissing alerts: humans only (no action) |
| **Dependabot PRs** | not acted on in M14 (follow-up: internal review + auto-merge of patch/minor) | — | — | — |
| **Releases / tags** | when `releases != off`: a weekly built-in schedule `repo-release-check:<rid>` for the lead ("decide whether merged changes deserve a release; draft notes") | lead | tags only through the release API (never `git push --tags`); tag pattern `^v?\d+\.\d+\.\d+([-+.][0-9A-Za-z.-]+)?$`; target must be on the default branch | `github.release.draft` (peer review), `github.release.publish` (**human approval**, and `releases = publish` required) |
| **Stale branches** | daily, system only: remote `foundry/*` branches with no open PR that are merged into the default branch or older than `stale_branch_days` | system | non-`foundry/` branches are never touched; an unmerged branch is first copied to `refs/foundry-archive/<branch>` locally; local `run/*` branches older than 30 days pruned | `github.branch.delete` (system; allow; DESTRUCTIVE tier) |
| **README / docs upkeep** | when `docs_upkeep`: a weekly built-in schedule `repo-docs-upkeep:<rid>` for the lead ("compare README/docs with the changes merged in the last 7 days; create a work task if they are out of date") | lead → normal work → PR | the M12 caps on tasks per project apply | — |
| **Codebase onboarding** | §2.4 | lead / engineer | once per binding | `github.read` |
| **Upstream conflicts** | §3.3 | the agent of the last run that touched the files | 2 per origin SHA | — |

### 4.2 Actions, tiers and executors

`registry.py` gets `ActionSpec.default_decision: str | None = None`. `rules.default_decision()` returns it (when set)
before the `human_default` logic. `rules.resolve()` keeps applying the `human_required` floor. `kind="system"`
actions go into `ACTIONS` but **not** into `PROVIDER_ACTIONS`, so `providers.precheck` denies them for agents
(`guardrail_denied`, unknown provider action).

| Action | Tier | Kind | Default | Human by default? | Rate key | Executor / caller |
|---|---|---|---|---|---|---|
| `github.read` (exists) | external_read | provider | allow | no | — | `GithubExecutor.do_github_read` (any repo) |
| `github.issue.read` | external_read | provider | allow | no | — | `do_github_issue_read` (bound repos; untrusted wrap) |
| `github.pr.read` | external_read | provider | allow | no | — | `do_github_pr_read` |
| `github.checks.read` | external_read | provider | allow | no | — | `do_github_checks_read` (incl. log tail) |
| `github.alerts.read` | external_read | provider | allow | no | — | `do_github_alerts_read` |
| `github.issue.comment` (exists) | external_communication | provider | peer_review | no | `github_comments_per_day` | `do_github_issue_comment` |
| `github.pr.comment` | external_communication | provider | peer_review | no | `github_comments_per_day` | `do_github_pr_comment` |
| `github.issue.create` | external_communication | provider | peer_review | no | `github_comments_per_day` | `do_github_issue_create` |
| `github.issue.update` | external_communication | provider | peer_review | no | `github_comments_per_day` | `do_github_issue_update` (labels: only existing labels; `state: closed` requires `comment`, posted first) |
| `github.release.draft` | external_communication | provider | peer_review | no | `github_writes_per_hour` | `do_github_release_draft` (forces `draft: true`; `releases != off`) |
| `github.release.publish` | external_commitment | provider | human_approval (tier default) | **yes** | `github_writes_per_hour` | `do_github_release_publish` (`releases = publish`) |
| `github.repo.create` | identity | provider | human_approval, `human_required=True` (floor) | **yes, always** | — | `do_github_repo_create` → `repositories.bind_for_agent` (caller must be the Co-founder or that project's lead; visibility default private) |
| `github.pr.open` (exists; becomes system) | external_communication | **system** | `default_decision=allow` | no (DLP on title/body) | `github_pushes_per_hour` | `repo_sync` |
| `github.pr.update` | external_communication | system | allow | no | `github_pushes_per_hour` | `repo_sync` (fast-forward push of the head; body edit) |
| `github.branch.push` | external_communication | system | allow | no | `github_pushes_per_hour` | `repo_sync` (work branch; initial push; publish) |
| `github.push.default` | external_commitment | system | allow (consent = `merge_mode=direct_push`) | no | `github_pushes_per_hour` | `repo_sync._direct_push` |
| `github.pr.merge` | external_commitment | system | allow (consent = `merge_mode=pr_auto_merge`) | no; a human rule can require it (the approval is bound to repo, number, head_sha, method) | `github_pushes_per_hour` | `repo_sync` |
| `github.branch.delete` | destructive | system | allow (executor restricted to `foundry/*`, merged or stale, no open PR, archived locally) | no | — | `repo_maintenance` |
| `repo.pr.review` | internal_write | api | allow | no | — | agent route §3.5 |

New rate keys (`registry.py` constants, `ratelimit.LIMITS`): `github_comments_per_day` (per agent 30, per company 100),
`github_writes_per_hour` (10 / 30), `github_pushes_per_hour` (20 / 60). Agent GitHub writes do **not** use
`external_communications_per_day`.

System checks go through `repo_gate.authorize(db, binding, action, payload, *, env) -> GateDecision(allowed,
approval_id, reason)`. It picks the actor with `repo_sync.actor_for(binding)` (active project lead, else the
Co-founder) and calls `guard_engine.check(db, agent_ctx(actor), action, payload, env=GuardEnv(cipher=…))`:

- `ApprovalPending` → `allowed=False, approval_id`, `sync_state = awaiting_approval`; the sync then keeps the
  **exact payload** (for example `to_sha`) until the approval is decided.
- `GuardrailDenied` / `RateLimited` / `EmergencyStopped` → not allowed, with the reason.

Payloads are deterministic dicts:

- push: `{repo, branch, from_sha, to_sha, commits, files}`;
- open: `{repo, head, base, title, body}`;
- merge: `{repo, number, head_sha, method}`;
- delete: `{repo, branch, sha}`.

Every executor validates its parameters again: `repo` must be an `active` binding of the company
(`repositories.binding_for(db, company_id, full_name)`), and the agent must be the Co-founder, the lead of a project
bound to it, or running a task of such a project (`repositories.agent_may_use(db, rc, binding)`). Otherwise 409
`repo_not_bound` / 403. Results never carry the token. Issue, PR and comment bodies come back wrapped in
`<untrusted source="github">` with `injection_flags` (reuse `email_identity.detect_injection`).

Approval display (`integrations/scopes.py`): `github.repo.create` → owner, name, visibility, project;
`github.release.publish` → repo, tag, target_sha, name, body excerpt; `github.pr.merge` → repo, number, head_sha,
method.

Credential scopes: `catalog.SPECS["github"].actions` = the provider actions above (not the system ones).
`connected_accounts.refresh_scope(db, account)` (new) re-applies the spec's actions to the account's credential.
It is called when a binding is created, so tokens connected in M8 gain the new actions (audited
`connected_account.scope_refreshed`).

### 4.3 Polling vs webhooks

- **Polling (default):** `poll_interval_seconds` 300 (60–3600). Conditional requests with stored ETags (304 does not
  count against the primary limit). Typical cost: ~6–10 requests per sync, about 120 per hour per repo against
  5,000 per hour per token.
- **Webhooks (optional):** `PUT /api/companies/{cid}/repositories/{rid}/webhook {enabled: true}`.
  - Refused with 409 `public_url_ephemeral` unless `reachability.public_url(ic)` exists and
    `tunnel_is_ephemeral(ic)` is false.
  - Generates a 32-byte secret, stored as a credential (`webhook_secret_credential_id`).
  - Creates the hook (`POST /repos/{o}/{r}/hooks`; events `push`, `pull_request`, `pull_request_review`,
    `pull_request_review_comment`, `issues`, `issue_comment`, `check_suite`, `check_run`, `status`,
    `workflow_run`, `dependabot_alert`) at `<public_url>/api/webhooks/github/<repository_id>`.
  - Receiver `POST /api/webhooks/github/{repository_id}`: body ≤ `webhook_max_bytes`; verify
    `X-Hub-Signature-256` (HMAC-SHA256, constant-time). Unknown repo, no secret or a bad signature → identical 401
    `webhook_rejected`. Valid → `request_sync(reason="webhook")` (debounced: `next_sync_at = max(now, last_synced_at
    + 10 s)`) → 202 `{accepted: true}`. The payload is not parsed beyond `X-GitHub-Event` for the log.
  - When the public URL changes, the UI shows the webhook as `stale` (the stored `webhook_url` differs) and polling
    continues.

---------------------------------------------------------------------------------------------------------------------

## 5. Safety

### 5.1 Token storage
Stored only in the M3b credential vault (Fernet, `COMPANY_OS_SECRET_KEY`), referenced by the M8 connected account.
Decrypted per operation in the control plane (`connected_accounts.secrets_for`), and kept in memory only for one
sync or one action. Never in API responses, audit data, logs (masked by `ApiClient._mask` and `repo_git` stderr
masking), agent context, run specs, CLI configs or `.git/config`.

### 5.2 Token never in argv or agent-visible config
§3.2 (askpass + environment, config pinning, no helper, no userinfo URL). Tests: record every git `argv` and assert
the token is absent; assert `.git/config` and `FETCH_HEAD` do not contain it; a planted global credential helper is
never invoked; askpass prints nothing for another host.

### 5.3 No force push; protection respected
§3.2 `assert_safe_push_argv`; the single push function; merges only via the API, with `sha` and
`mergeable_state` checks (§3.4); `direct_push` only when unprotected; protection summary from
`GET /repos/{o}/{r}/branches/{b}` (`protected`) plus `GET /repos/{o}/{r}/rules/branches/{b}` (rulesets:
`pull_request`, `required_status_checks`, `non_fast_forward`, `required_signatures`, `required_linear_history`),
refreshed every 6 h and stored in `protection`. With `required_linear_history`, Foundry uses `squash` or `rebase`
(whichever the repo allows). With `required_signatures`, direct push is refused (merges by the API are signed by
GitHub).

### 5.4 Secret scanning before push (and before the local merge)
`repo_scan.scan_range(repo, base, head, *, policy, own_secrets, internal)`:

- reads `git log -p --no-ext-diff --no-textconv --format=%H <base>..<head>` (with `--root` semantics for a first
  push), taking only added lines, capped at `max_push_bytes`;
- runs `dlp.scan_text` with `own_secrets = GuardEnv.own_secrets()` (all stored credential values, including the
  GitHub token, plus the owner token) and `internal = dlp.internal_snippets()`.

What blocks:

| Finding | Blocks |
|---|---|
| `secret`, `own_token`, `internal_memory` | yes |
| `email`, `phone` | no (reported only; code is full of them) |
| `blocked_file` | yes: added paths matching `.env`, `.env.*` (except `.env.example`, `.env.sample`, `.env.template`), `*.pem`, `*.key`, `*.p12`, `*.pfx`, `id_rsa*`, `id_ed25519*`, `*.keystore`, `.git-credentials`, `.pypirc`, `credentials.json` |
| `protected_path` | yes: matches `policy.protected_paths` (default `.github/workflows/**`, `.github/actions/**`, `.gitmodules`); the workflow globs are lifted only by `allow_workflow_changes` |
| `file_too_large` | yes: > `max_file_bytes` (10 MB) |
| `push_too_large` | yes: total new blobs > `max_push_bytes` (100 MB), or > 500 commits |

Findings carry kind, severity, description, count and paths, never the matched text.

`project_workspaces.finalize` runs `scan_commit_for_merge` for remote-backed projects before the merge, so a
blocked run fails (`secret_detected` permanent / `protected_path` permanent / `workspace_too_large`) with a message
telling the agent what to remove. At push time a finding means: no push, `sync_state = push_blocked`, the critical
alert `github_push_blocked` and audit `repository.push_blocked`. The way out is
`POST …/integration/reset` (the old head is kept as the local branch `foundry/rescue-<UTC>`). Upstream commits (made
by humans on GitHub) are excluded from the scan (`^origin/<default>`, `^origin/<work>`).

### 5.5 Rate limits
- Every response updates `rate_limit_remaining` / `rate_limit_reset_at` on the binding (from `X-RateLimit-*`).
- When remaining < 500: the sync does only PR-state reads, merges and pushes (no intake, no alerts).
- When remaining = 0: `next_sync_at = reset_at`.
- Secondary limits (403/429 with `Retry-After`, or a message containing "secondary rate limit") raise
  `GithubRateLimited(retry_after)`; the backoff is `retry_after`, else `60·2^n` s capped at 900 s, with
  `sync_state = rate_limited`.
- Content-creating requests are spaced ≥ 1 s per client (injectable sleep).
- The guardrail rate keys in §4.2 apply to agent and system actions.

### 5.6 Token expiry, revocation and missing permissions
- `token_expires_at` comes from the response header. The warning alert `github_token_expiring` fires at 14 days and
  again at 3 days (dedupe `github-expiry:<account>:<days>`), with a notification to owners.
- 401 on any API or git call: binding `status = auth_failed`, connected account `status = invalid` (the existing
  check fields), the critical alert `github_auth_failed` with the fix ("create a new token, then Integrations →
  GitHub → Replace token"), and audit `repository.auth_failed`. No more network calls for that account except one
  probe per hour (so a transient 401 heals and abuse detection is not triggered). Local work continues (runs still
  merge into `work`; nothing is lost); on recovery the next sync pushes. Rotation (`/credentials/{id}/rotate`)
  triggers an immediate check and sync.
- 403 "Resource not accessible by personal access token": the capability goes false and the warning alert
  `github_capability_missing` names the permission; that feature is skipped (not `auth_failed`). 403 with
  `X-GitHub-SSO`: the alert carries the authorisation URL.
- The connected account is revoked by the owner: its bindings become `paused` (reason `account_revoked`).
- The repo is archived → `paused` (reason `archived`). Deleted or transferred (404/301) → `error` with the message.
  Visibility changed to public → `paused` (reason `visibility_changed`) until the owner resumes (owner decision 7).

### 5.7 Size limits
Repo `size` > `settings.repo_max_size_mb` (1024) → refuse to bind (409 `repository_too_large`). Clone fetches only
the base and work branches, no tags. M12's per-run 50 MB staged limit stays; plus a per-file limit (`max_file_bytes`)
at finalize. GitHub excerpts: issue/PR bodies ≤ 8 KB, CI logs ≤ 32 KB tail, alerts ≤ 4 KB, PR diff artifact
≤ 200 KB.

### 5.8 Prompt injection from GitHub
Untrusted wrapping and flags (§4.2), author filters, and bounded intake. The review task and the triage task state
that their content is data, not instructions. CI log downloads follow the `Location` redirect **without** the
`Authorization` header (`GithubClient.job_log_tail`).

---------------------------------------------------------------------------------------------------------------------

## 6. Schema, API, schemas and UI

### 6.1 Migration `backend/alembic/versions/0013_github_projects.py` (revision `"0013"`, down `"0012"`)

Portable SQL. Every non-null column has a server default, so existing rows need no backfill. Downgrade drops
everything it added.

**`project_repositories`** (model `ProjectRepository`, `app/models/repository.py`)

| Column | Type | Null | Default | Notes |
|---|---|---|---|---|
| `id` | String(36) PK | | uuid | |
| `company_id` | FK companies | no | | |
| `project_id` | FK projects | no | | unique `uq_project_repositories_project` (company_id, project_id) |
| `provider` | String(16) | no | `github` | |
| `auth_kind` | String(16) | no | `pat` | `pat`; `app` reserved |
| `connected_account_id` | FK connected_accounts | yes | | |
| `owner`, `name` | String(100) | no | | |
| `full_name` | String(201) | no | | index `ix_project_repositories_company_full_name` |
| `html_url`, `remote_url` | String(300) | yes / no | | `remote_url` never has userinfo (validated) |
| `visibility` | String(10) | no | `private` | `private` \| `public` \| `internal` |
| `default_branch`, `work_branch` | String(255) | no | | |
| `origin` | String(12) | no | `connected` | `created` \| `connected` \| `published` |
| `status` | String(16) | no | `pending` | `pending` \| `creating` \| `cloning` \| `active` \| `paused` \| `auth_failed` \| `error` \| `disconnected` |
| `status_reason` | Text | yes | | |
| `sync_state` | String(24) | no | `idle` | `idle` \| `upstream_conflict` \| `upstream_rewritten` \| `push_blocked` \| `rate_limited` \| `awaiting_approval` |
| `policy` | JSON | no | `{}` | validated by `RepoPolicy` (§6.3) |
| `capabilities`, `protection`, `etags` | JSON | no | `{}` | |
| `last_synced_at`, `next_sync_at`, `sync_started_at`, `last_issues_sync_at`, `last_alerts_sync_at`, `last_cleanup_at`, `token_expires_at`, `rate_limit_reset_at` | UTCDateTime | yes | | index `ix_project_repositories_due` (status, next_sync_at) |
| `sync_failures` | Integer | no | 0 | |
| `rate_limit_remaining` | Integer | yes | | |
| `last_fetched_default_sha`, `last_pushed_sha`, `integration_head_sha` | String(40) | yes | | |
| `webhook_id` | String(32) | yes | | |
| `webhook_secret_credential_id` | FK credentials | yes | | |
| `webhook_url` | String(500) | yes | | |
| `onboarding_status` | String(16) | no | `not_needed` | `not_needed` \| `pending` \| `running` \| `done` \| `failed` |
| `onboarding_task_id` | FK tasks | yes | | |
| `is_company_default`, `pr_opening_paused` | Boolean | no | false | one company default is enforced in the service |
| `created_by_type`, `created_by_id`, `created_at`, `updated_at` | | | | TimestampMixin |

**`repo_pull_requests`** (model `RepoPullRequest`): `id`, `company_id`, `repository_id` FK, `number` Integer, `url`
String(500), `title` String(300), `head_branch`/`base_branch` String(255), `head_sha`/`review_sha`/`verdict_sha`/
`merged_sha` String(40), `stage` String(24) default `opening`, `revision` Integer 0, `review_task_id` FK tasks,
`verdict` String(16) (`accepted` \| `changes_requested`), `verdict_note` Text, `verdict_agent_id` FK agents,
`checks_state` String(12) default `unknown`, `checks` JSON `[]`, `checks_since` UTCDateTime, `ci_fix_attempts`
Integer 0, `mergeable_state` String(16), `human_review_state` String(24), `last_review_seen_at` UTCDateTime,
`approval_id` FK approvals, `merged_by` String(100), `task_ids` JSON `[]`, `opened_at`/`merged_at`/`closed_at`
UTCDateTime, `last_error` Text, timestamps. Unique `uq_repo_pull_requests_repo_number` (repository_id, number);
index `ix_repo_pull_requests_repo_stage` (repository_id, stage).

**`repo_items`** (model `RepoItem`): `id`, `company_id`, `repository_id` FK, `kind` String(16) (`issue` \| `review` \|
`internal_review` \| `check_failure` \| `alert`), `external_id` String(64), `number` Integer, `title` String(300),
`url` String(500), `author` String(100), `author_association` String(24), `remote_state` String(16), `status`
String(12) default `new` (`new` \| `queued` \| `tasked` \| `ignored` \| `done`), `ignore_reason` String(64),
`task_id` FK tasks, `pull_request_id` FK repo_pull_requests, `revision` Integer, `severity` String(12),
`body_excerpt` Text, `injection_flags` JSON `[]`, `data` JSON `{}`, `remote_updated_at` UTCDateTime, timestamps.
Unique `uq_repo_items_repo_kind_external` (repository_id, kind, external_id); index `ix_repo_items_repo_status`
(company_id, repository_id, status).

**`company_policies.new_project_repository`**: String(16), not null, server default `local` (`local` \|
`company_repo`). Human-only like every policy field; validated in `schemas/policy.py` and `services/policy.py`;
audited by the existing `policy.updated`.

### 6.2 Endpoints (owner/admin humans unless stated; tenancy: non-members 404, viewers 403 on writes, agents 403)

| Method & path | Service function | Notes |
|---|---|---|
| `GET /api/companies/{cid}/repositories/status` | `repositories.availability()` | `{available, reason, git_version, milestone:"M14"}` (git or fcntl missing → `available:false`) |
| `POST /api/companies/{cid}/github/inspect` | `github_accounts.inspect` | `GithubInspectResult` |
| `GET /api/companies/{cid}/repositories?project_id=&limit=&cursor=` | `list_repositories` | `{items, next_cursor}`; members may read |
| `POST /api/companies/{cid}/repositories` | `create_repository` | 201; errors in §6.3 |
| `GET /api/companies/{cid}/repositories/{rid}` | `get_repository` | |
| `PATCH /api/companies/{cid}/repositories/{rid}` | `update_repository` | partial `RepoPolicyInput`, `default_branch`, `is_company_default` |
| `POST …/{rid}/sync` | `request_sync_now` | 202 `{queued: true}` |
| `POST …/{rid}/pause`, `POST …/{rid}/resume` | `pause` / `resume` | |
| `POST …/{rid}/disconnect` `{confirm: true}` | `disconnect` | 409 `repository_busy` |
| `POST …/{rid}/integration/reset` `{to: "remote_work_branch" \| "default_branch", confirm: true}` | `reset_integration` | keeps `foundry/rescue-<UTC>`; refused while a project run is active |
| `POST …/{rid}/pull-requests/resume-opening` | `resume_pr_opening` | |
| `PUT …/{rid}/webhook` `{enabled}` | `set_webhook` | 409 `public_url_ephemeral` |
| `GET …/{rid}/pull-requests` | `list_pull_requests` | |
| `GET …/{rid}/items?kind=&status=` | `list_items` | bodies only as excerpts, `untrusted: true` |
| `GET …/{rid}/activity` | `activity` | audit events `target_type="repository"`, `target_id=rid`, newest first |
| `POST /api/webhooks/github/{repository_id}` | `repositories.receive_webhook` | public, HMAC |
| `POST /api/agent/v1/repo/pulls/{pull_id}/review` | `repo_reviews.record_verdict` | agent (run token); `RouteSpec("POST", "/repo/pulls/{pull_id}/review", "repo.pr.review")` |

Audit actions (all `target_type="repository"`, `target_id=<rid>`; data never holds secrets or matched text):

`repository.created`, `repository.connected`, `repository.published`, `repository.ready`,
`repository.clone_failed`, `repository.policy_updated` (changed fields with old/new), `repository.paused`,
`repository.resumed`, `repository.disconnected`, `repository.integration_reset`, `repository.auth_failed`,
`repository.capability_missing`, `repository.webhook_enabled`, `repository.webhook_disabled`,
`repository.upstream_merged`, `repository.upstream_conflict`, `repository.upstream_rewritten`, `repository.pushed`,
`repository.push_blocked`, `repository.pr_opened`, `repository.pr_updated`, `repository.pr_review_requested`,
`repository.pr_verdict`, `repository.pr_ci_failed`, `repository.pr_awaiting_human`, `repository.pr_merged`,
`repository.pr_closed`, `repository.item_tasked`, `repository.item_ignored`, `repository.branch_deleted`,
`repository.onboarding_started`, `repository.onboarding_done`, `repository.onboarding_failed`;
plus `connected_account.scope_refreshed` (target the account).

Events: `repository.updated` `{repository_id}`. Alerts (kinds): `github_auth_failed`, `github_token_expiring`,
`github_capability_missing`, `github_push_blocked`, `github_upstream_conflict`, `github_upstream_rewritten`,
`github_direct_push_refused`, `github_pr_closed`, `github_pr_review_stalled`, `github_pr_needs_human`,
`github_onboarding_failed`. Notification to owners: `github_pr_ready` (PR awaiting a human merge). Wake trigger
`github`.

### 6.3 Pydantic schemas (`backend/app/schemas/repository.py`, step 0)

- `MergeMode = Literal["pr_human_merge", "pr_auto_merge", "direct_push"]`, `MergeMethod = Literal["squash",
  "merge", "rebase"]`, `CiMode = Literal["required", "if_present", "ignore"]`, `IssueIntake = Literal["off",
  "collaborators", "labeled", "everyone"]`, `AlertLevel = Literal["off", "low", "medium", "high", "critical"]`,
  `ReleaseMode = Literal["off", "draft", "publish"]`.
- `RepoPolicy` (full, with these defaults and ranges):

  | Field | Default | Range / values |
  |---|---|---|
  | `merge_mode` | `pr_human_merge` | `MergeMode` |
  | `pr_merge_method` | `squash` | `MergeMethod` |
  | `pr_internal_review` | true | bool |
  | `ci_mode` | `if_present` | `CiMode` |
  | `ci_wait_seconds` | 600 | 60–7200 |
  | `pr_batch_quiet_seconds` | 600 | 0–86400 |
  | `max_pr_revisions` | 3 | 0–10 |
  | `max_ci_fix_attempts` | 3 | 0–10 |
  | `issue_intake` | `collaborators` | `IssueIntake` |
  | `intake_label` | `foundry` | 1–50 |
  | `max_open_intake_tasks` | 5 | 0–50 |
  | `respond_to_reviews` | true | bool |
  | `review_authors` | `collaborators` | `collaborators` \| `everyone` |
  | `fix_ci_failures` | true | bool |
  | `security_alerts` | `high` | `AlertLevel` |
  | `releases` | `off` | `ReleaseMode` |
  | `stale_branch_days` | 30 | 0 or 7–365 |
  | `docs_upkeep` | false | bool |
  | `allow_workflow_changes` | false | bool |
  | `protected_paths` | the three defaults | ≤ 20 globs, each ≤ 200 |
  | `poll_interval_seconds` | 300 | 60–3600 |
  | `max_file_bytes` | 10 MB | 1 KB–50 MB |
  | `max_push_bytes` | 100 MB | 1 MB–500 MB |

  `default_policy(mode: Literal["create", "connect"], *, has_workflows: bool) -> RepoPolicy` applies §2.1.
  `RepoPolicyInput` is the same with every field optional (for create/patch).
- `RepositoryCreate`: `mode`, `connected_account_id`, `project_id | None`, `project_name | None` (exactly one),
  `project_description | None` (≤ 2000), `owner | None`, `name | None`, `full_name | None` (accepts a github.com URL),
  `visibility = "private"`, `confirm_public = False`, `description | None` (≤ 350), `default_branch | None`,
  `work_branch | None`, `initialize = True`, `publish_local = False`, `company_default = True`,
  `policy: RepoPolicyInput = {}`.
  Errors:

  | Error | Status / code |
  |---|---|
  | `project_has_repository` | 409 |
  | `no_account` | 409 |
  | `confirm_public_required` | 422 |
  | `repository_exists` | 409 (create) |
  | `repository_not_accessible` | 409 |
  | `repository_archived` | 409 |
  | `token_cannot_push` | 409 (`details.missing_permissions`) |
  | `repository_too_large` | 409 |
  | `administration_permission_missing` | 409 (create) |
  | `branch_protected` | 409 (direct_push) |
  | branch or name invalid | 422 |
  | `git_unavailable` | 501 `{available:false}` |

- `RepositoryUpdate`: `policy: RepoPolicyInput | None`, `default_branch | None`, `is_company_default | None`.
- `ProjectRepository` (response): every column in §6.1 except internals (`etags`, `sync_started_at`,
  `webhook_secret_credential_id`), plus `project_name`, `webhook: {enabled, url, stale}`, and
  `open_pull_request: RepoPullRequestSummary | None`.
- `RepoPullRequest`, `RepoPullRequestSummary`, `RepoItem` (adds `untrusted: true`), `GithubInspectRequest`,
  `GithubInspectResult`, `RepoAvailability`, `WebhookUpdate`, `IntegrationReset`, `PullReviewBody` (agent).
- `schemas/agent_api.py` (WP-C): the state project row gains `repository: {full_name, merge_mode, status} | None`.

### 6.4 Web UI (WP-E)

- **Onboarding** `steps/RepoStep.tsx`, using `features/repos/RepoConnectForm.tsx` (shared with the dialog).
- **Panel `repos`** (simple panel): `features/repos/RepositoriesPanel.tsx`. Table: project, `owner/name` (link),
  status, sync state, open PR stage, last sync, token expiry. "Connect a repository" (`ConnectRepoDialog.tsx`).
  The "not available" state uses `/repositories/status`.
- **Panel `repo:<id>`** (ID panel): `features/repos/RepoPanel.tsx` with tabs:
  - *Overview*: status, permission checklist, protection summary, PR card with its stage timeline;
  - *Settings*: `RepoPolicyForm.tsx` (owner/admin edit, viewer read-only; `direct_push` disabled with a reason
    when protected; the workflow toggle warns);
  - *Activity*: `RepoActivity.tsx` (audit events + items + PRs, newest first);
  - *Danger zone*: pause/resume, sync now, reset integration (typed confirmation), disconnect.
- `GoalsPanel` project rows: a repo badge → `repo:<id>`, or "Connect GitHub" (owner/admin).
- Command palette entry "Repositories". Every view handles the loading, empty, error and not-available states, and
  no button is inert.

---------------------------------------------------------------------------------------------------------------------

## 7. Tests (no real GitHub; the fakes come from step 0)

**Fakes (step 0):**

- `backend/tests/m14_github_fake.py`, `FakeGitHub`: a stateful in-memory GitHub REST fake (users, orgs, repos,
  branches + rules, pulls with reviews/comments/mergeable_state, issues + comments + labels, check runs, statuses,
  Actions runs/jobs/log redirects to a second host, dependabot alerts, releases, refs delete, hooks, rate-limit and
  ETag headers, token-expiry header, SSO/403 modes). `handler(request) -> httpx.Response` is used through
  `httpx.MockTransport`. Merging a PR performs the real merge in the bare repo (`git merge-tree --write-tree` +
  `commit-tree` + `update-ref`).
- `backend/tests/m14_git_remote.py`, `GitRemote`: bare repos under `tmp_path` served over **smart HTTP**: a
  `wsgiref` server in a thread wrapping `git http-backend`, with Basic auth that checks `x-access-token:<token>`;
  modes for wrong token (401), read-only token (403 on `git-receive-pack`), and `receive.denyNonFastForwards`.
  It records request auth headers so tests can prove the token went only over HTTP.
- `backend/tests/m14_helpers.py`: fixtures `github_world` (FakeGitHub + GitRemote wired: `settings.github_api_url`,
  `github_web_url`, `github_allow_insecure_http=True`, the app's and the engine's `IntegrationHttp.transport`),
  `bound_project(mode=…)`, `push_human_commit(...)`, `fake_ci(state)`. Tests import them; `conftest.py` is not
  changed.

**Test files and the cases each must cover:**

| File (WP) | Cases |
|---|---|
| `test_m14_migration.py` (0) | 0013 up/down on SQLite with existing rows; defaults; unique constraints; `new_project_repository` default `local`; the policy API accepts / validates / audits it; agents and viewers 403 |
| `test_m14_registry.py` (0) | tier and default decision of every new action (table §4.2); system actions are not in `PROVIDER_ACTIONS` and agent `provider-actions` with them → `guardrail_denied`; `github.repo.create` floor cannot be lowered by an allow rule; a human rule overrides `default_decision`; the agent-route enumeration test still passes with `/repo/pulls/{pull_id}/review` |
| `test_m14_fakes.py` (0) | clone over smart HTTP with the token; wrong token 401; read-only push 403; non-ff rejection; fake merge updates the bare repo; ETag 304 |
| `test_m14_github_client.py` (0) | auth headers; ETag reuse; rate-limit capture; secondary limit → `GithubRateLimited`; expiry header; SSO error; token masked in errors; `can_push` via `info/refs?service=git-receive-pack`; job log redirect fetched without `Authorization`; write spacing |
| `test_secrets_scan.py` (0, extend) | `github_pat_…` found and scrubbed; DLP flags it |
| `test_m14_github_actions.py` (A) | every executor: bound-repo restriction (`repo_not_bound`), agent scoping (403), untrusted wrap + flags, close requires comment, labels must exist, release draft forces draft / publish needs `releases=publish` + tag pattern + target on default, `repo.create` only after human approval and binds via `bind_for_agent`; peer review default on comment; DLP blocks a comment containing a stored credential; rate key → 429; results never contain the token; `refresh_scope` grants new actions to an M8 credential |
| `test_m14_inspect.py` (A) | token kind from prefix (fine-grained / classic / other) without returning it; not found vs no access; archived; empty; protection summary from branches + rules; capabilities (dependabot 403 → false); org approval pending; missing permission list per mode |
| `test_m14_maintenance.py` (A) | intake per mode (off / collaborators / labeled / everyone); PRs filtered from issues; pre-binding issues not imported; cap + FIFO queue; triage task kind `planning`, to the lead, idempotent per issue / reopen; review comments by non-allowed authors ignored; human CHANGES_REQUESTED → revision task with `revision`; CI failure on default → one task per (workflow, sha); alerts by severity, closed alerts → done; stale branch deletion only `foundry/*`, merged or stale, archived first, never with an open PR; docs and release schedules follow policy; company paused → nothing created |
| `test_m14_repo_git.py` (B) | token never in argv / `.git/config` / remote URL / FETCH_HEAD; askpass returns the token only for the expected host; a planted global helper is never called; a tampered `.git/config` (`insteadOf` → attacker) is re-pinned before fetch; clone of an empty repo + initial commit sets the default branch; clone of an existing repo; size refusal; timeout; `assert_safe_push_argv` refusals; grep test: no other module pushes |
| `test_m14_push_gate.py` (B) | a secret added and removed in an intermediate commit blocks; a stored credential value blocks; `.env` blocks, `.env.example` does not; workflow path blocks unless allowed; per-file and per-push limits; email / phone do not block; findings never contain the matched text; human upstream commits are not scanned; alert + audit; reset unblocks |
| `test_m14_workspaces.py` (B) | worktrees branch from the work branch; local-only projects unchanged (`main`); finalize scan → `secret_detected` and the branch is deleted; `upstream_merge` kind leaves markers, unresolved → `merge_conflict_unresolved`, resolved → two-parent merge; read-only review at an arbitrary SHA; onboarding read-only |
| `test_m14_sync.py` (B) | upstream human commit merged; conflict → one conflict task per origin SHA, ≤ 2 tries then alert; human commit on the work branch merged before push; upstream rewrite → paused pushes + alert; 401 → `auth_failed`, alert, hourly probe only, local runs still merge, rotation resumes; rate limit low / zero; lease prevents overlap; `enqueue_due` dedupe; emergency stop → no writes; company paused → fetch only; dispatch hold while cloning / onboarding; onboarding task created after AI ready, status follows the task |
| `test_m14_pr_flow.py` (B) | quiet period then PR; adopt an existing PR; review task created after push; freeze during review; accept + green + `pr_auto_merge` → merge with `sha`; pending waits; `if_present` with no checks merges after the wait; `required` with none → `awaiting_human`; CI failure → `changes_requested` → fix done → fast-forward push → re-review, revision cap → `awaiting_human`; upstream-only head change keeps the verdict; `pr_human_merge` → `awaiting_human` + notification, human merge detected; human close → `pr_opening_paused`, no reopen loop, resume endpoint; `mergeable_state=blocked` never merged (admin bypass impossible); 409 SHA mismatch; `direct_push` unprotected pushes, protected → PR behaviour + alert, non-ff → integrate and retry once; a human approval rule on `github.pr.merge` parks the merge until approved, then merges the approved SHA only |
| `test_m14_repositories_api.py` (C) | tenancy (other company 404, viewer 403, agent 403); create (validation, public confirm, exists → 409, Administration missing → 409, project auto-created, audit, company default + `new_project_repository`); connect (archived, cannot push, too large, branch override); one binding per project; local history moved aside vs publish to empty; PATCH (direct_push refused when protected, audit with changed fields); pause / resume / disconnect busy / reset (confirm, rescue branch) / sync now / resume-opening; availability without git |
| `test_m14_pr_review.py` (C) | reviewer choice (QA report > lead > Co-founder, not the main author); verdict only by the assignee; stale SHA 409; accept writes verdict only; request_changes → fix task + item; no verdict → re-issue ladder → alert |
| `test_m14_webhook.py` (C) | valid HMAC → sync requested + 202; bad signature / unknown repo / no secret → the same 401; body cap; payload ignored; enabling refused on an ephemeral URL; enabling creates the hook with the right events and stores the secret encrypted |
| `test_m14_projects_hook.py` (C) | a Co-founder-created project with `company_repo` gets its own binding / work branch / clone job; `local` → none; state rows show `repository` |
| `runtime/.../test_company_review_pull_request.py` (C) | tool schema; posts to the route; errors passed through |
| `test_m14_cli_setup_repo.py` (D) | non-interactive new / existing / none; missing token → exit 2 listing it; token never in argv / output / audit; `--only repo` resumable ("Already done"); invalid `--github-repo` → exit 2; public needs `--github-public-ok`; interactive menu paths; `--json` shape; `github_token` in `SECRET_KEYS`; idea seeded after the repo step |
| `test_m14_cli_repo.py` (D) | `foundryos repo list / status / connect / set / sync / pause / resume / disconnect / reset-integration / prs / activity` hit the right endpoints; confirmations; `doctor` git + repo checks |
| web (E) | `lib/onboarding.test.ts` (6 steps, idea after repo, back rules); `lib/repos.test.ts` (repo input parsing, name rules, permission checklist per mode, policy mapping, stage labels); `RepoStep` (three choices, masked token cleared after submit, inspect results, public confirm, cannot-push error, local only, progress, 501 not-available); `RepositoriesPanel` / `RepoPanel` / `RepoPolicyForm` / `RepoActivity` (loading / empty / error / not-available, owner vs viewer, protected disables direct push, PR stages) |
| `test_m14_e2e.py` (integrator, after all WPs) | (1) new repo → onboarding over the API → fake repo created + cloned → note → project linked → lead plans → scripted work run merges → push → PR → internal review accept → fake CI green → auto-merge → the file is on the bare repo's `main` → second change → second PR. (2) Existing repo with README → onboarding task (memory proposals + architecture artifact) → `foundry`-labelled issue → triage → work → PR in human-merge mode → `awaiting_human` → fake human merge → merged; a conflicting human push → conflict task → resolved → PR. (3) Token revoked mid-way → `auth_failed`, local work continues, rotation → push resumes. Hybrid variant: a fake `opencode` writes in a remote-backed project |

**Whole suites before "done":** backend `uv run pytest -q` + `ruff check` + `ruff format --check`; runtime
`.venv/bin/python -m pytest -q` + `scripts/check_imports.py`; web `pnpm test`, `pnpm typecheck`, `pnpm lint`.

---------------------------------------------------------------------------------------------------------------------

## 8. Work packages

### Step 0 — foundation and contracts (sequential; one agent; must merge before WP-A–E start)

Owns, then hands over (the "→" marks who owns a stub file afterwards):

- `backend/alembic/versions/0013_github_projects.py`; `backend/app/models/repository.py` (new),
  `backend/app/models/__init__.py`, `backend/app/models/policy.py`.
- `backend/app/schemas/repository.py` (new, complete §6.3), `backend/app/schemas/policy.py`,
  `backend/app/services/policy.py` (`new_project_repository`).
- `backend/app/guardrails/registry.py` (actions, system kind, `default_decision`, rate keys, RouteSpec),
  `backend/app/guardrails/rules.py` (`default_decision`), `backend/app/guardrails/ratelimit.py` (limits),
  `backend/app/guardrails/tools.json` (`company_review_pull_request` → `repo.pr.review`).
- `backend/app/core/config.py`: `github_web_url="https://github.com"`, `github_allow_insecure_http=False`,
  `repo_max_size_mb=1024`, `git_network_timeout_seconds=600`, `repo_sync_tick_seconds=30`.
- `backend/app/services/secrets_scan.py`: add `("github fine-grained token", r"\bgithub_pat_[A-Za-z0-9_]{22,}")`.
- `backend/app/integrations/github.py`: the complete `GithubClient`. ETag cache hook, `last_rate`
  (remaining, reset), `token_expires_at`, `GithubRateLimited`, `GithubSsoRequired`, write spacing, and the methods
  `create_repo(owner, name, *, private, description, org: bool)`, `get_branch`, `branch_rules`, `list_orgs`,
  `list_issues(since, state, per_page)`, `issue_comments`, `create_issue_comment`, `create_issue`, `update_issue`,
  `list_labels`, `find_pull(head)`, `create_pull`, `update_pull`, `get_pull`, `merge_pull(number, *, sha, method,
  title)`, `update_pull_branch(number, expected_head_sha)`, `list_reviews`, `list_review_comments`,
  `check_runs(ref)`, `combined_status(ref)`, `workflow_runs(head_sha)`, `run_jobs(run_id)`, `job_log_tail(job_id,
  limit)`, `dependabot_alerts(state)`, `create_release`, `delete_ref(branch)`, `create_hook`, `delete_hook`,
  `can_push(full_name)` (uses `github_web_url`). Existing methods are unchanged.
- Thin routes with their final signatures: `backend/app/api/routes/repositories.py` (new),
  `backend/app/api/routes/agent_repo.py` (new), the GitHub receiver added to
  `backend/app/api/routes/webhooks.py`, and router registration in `backend/app/main.py`.
- Stub modules with final signatures and docstrings (bodies raise `NotAvailable(milestone="M14")` or no-op):
  - `services/repositories.py` → C;
  - `services/repo_reviews.py` → C;
  - `services/repo_maintenance.py` → A;
  - `services/github_accounts.py` → A;
  - `services/repo_sync.py` (`RepoEnv`, `SyncSnapshot`, `WorkspaceSpec`, `request_sync`, `workspace_spec_for`,
    `dispatch_hold`, `actor_for`, `enqueue_due`, `run_job`) → B;
  - `services/repo_gate.py` (`authorize`, `GateDecision`) → B;
  - `services/repo_git.py` (signatures in §3.2) → B;
  - `services/repo_scan.py` (`scan_range`, `scan_commit_for_merge`, `PushFinding`) → B.
- Test infrastructure: `backend/tests/m14_github_fake.py`, `m14_git_remote.py`, `m14_helpers.py`;
  `test_m14_migration.py`, `test_m14_registry.py`, `test_m14_fakes.py`, `test_m14_github_client.py`; extend
  `test_secrets_scan.py` (or the existing secrets test file).
- Docs: `docs/specs/m14-github-backed-projects.md` (this plan as the spec), `docs/architecture.md` (D38–D44,
  entities), `docs/roadmap.md` (M14 row "in progress").

Acceptance: migration up/down; full backend suite green (stubs never break existing paths: no M14 code is called
from existing paths yet); `pnpm gen:api` produces every M14 type; the fakes' self-tests pass.

### WP-A — GitHub accounts, agent actions, maintenance intake (Sonnet)

Owns:

- `backend/app/integrations/catalog.py` (github actions, help text with the permission list, `_t_github` adds
  `token_kind` / `token_expires_at`);
- `backend/app/integrations/executors.py` (GithubExecutor removed, imported from the new module, registered);
- `backend/app/integrations/github_executor.py` (new);
- `backend/app/integrations/scopes.py`;
- `backend/app/services/connected_accounts.py` (`refresh_scope`);
- `backend/app/services/github_accounts.py` (`inspect`, `client_for_binding(db, env, binding)`,
  `probe_capabilities`, `token_kind`);
- `backend/app/services/repo_maintenance.py` (`process(db, env, binding, snapshot, now) -> MaintenanceReport`,
  `sync_routines(db, binding, principal)`, stale-branch cleanup through `repo_gate.authorize`);
- `backend/app/services/builtin_schedules.py` (`sync_repo_upkeep`, `sync_repo_release_check`);
- tests `test_m14_github_actions.py`, `test_m14_inspect.py`, `test_m14_maintenance.py`;
- `runtime/tools/company_tools.py` is **not** owned (C owns it).

Acceptance: its tests plus the whole backend suite; no M8 test changes except the `EXPECTED` additions it owns
(`test_m8_provider_actions.py` is not edited; new actions are asserted in A's own tests).

### WP-B — git engine, push gate, sync loop, PR state machine, engine hooks (Sonnet; the largest)

Owns:

- `backend/app/services/project_workspaces.py`, `repo_git.py`, `repo_scan.py`, `repo_sync.py`, `repo_gate.py`,
  `retry_policy.py` (`secret_detected`, `protected_path` permanent; `merge_conflict_unresolved` transient);
- `backend/app/engine/executor.py`, `dispatcher.py`, `worker.py`, `scheduler.py`, `context.py`;
- `docs/security.md` (M14 section: token handling, residual `/proc` risk, push gate, no force push);
- tests `test_m14_repo_git.py`, `test_m14_push_gate.py`, `test_m14_workspaces.py`, `test_m14_sync.py`,
  `test_m14_pr_flow.py`.

Calls (contracts): `github_accounts.client_for_binding` (A), `repo_maintenance.process` (A),
`repo_reviews.ensure_pr_review_task` (C). B's tests monkeypatch A/C stubs where needed.

Acceptance: its tests; M12 tests `test_m12_*` unchanged and green; whole suite.

### WP-C — repository service, owner API, webhook, PR review, agent route, projects hook (Sonnet)

Owns:

- `backend/app/services/repositories.py` (every function in §6.2, `binding_for`, `agent_may_use`,
  `bind_for_agent`, `on_project_created`, `receive_webhook`, `availability`);
- `backend/app/services/repo_reviews.py`;
- `backend/app/services/projects.py` (call `repositories.on_project_created`);
- `backend/app/services/state.py` and `backend/app/schemas/agent_api.py` (`repository` on state rows; the triage
  line in the Co-founder's guidance if it lives in `state.py`, else in `agent_context.py`, which C then owns too);
- `runtime/tools/company_tools.py` (`company_review_pull_request`; the `company_provider_action` description lists
  the GitHub actions instead of `github.pr.open`), runtime test, `runtime/FORK.md` if an upstream file is touched;
- `docs/api.md` (Milestone 14 section; table line 968 corrected);
- tests `test_m14_repositories_api.py`, `test_m14_pr_review.py`, `test_m14_webhook.py`, `test_m14_projects_hook.py`.

Uses `repo_git` (B) for clone/publish/reset through the job queue only (`repo_sync.request_sync` / `enqueue
repo_clone`), and `repo_git.reset_integration` directly (stub contract).

Acceptance: its tests; whole backend + runtime suites.

### WP-D — CLI and operator docs (Sonnet)

Owns:

- `backend/app/cli/onboarding.py` (`STEPS`, `SECRET_KEYS += ("github_token",)`, `WizardOptions` fields
  `github`, `github_repo`, `github_visibility`, `github_public_ok`, `github_default_branch`, `github_merge`,
  `github_ci`, `github_issues`, `project_name`, plus validation);
- `backend/app/cli/commands/onboard.py` (`step_repo` and helpers; `--only repo` seeds the idea);
- `backend/app/cli/commands/repo.py` (new), `backend/app/cli/main.py` (register);
- `backend/app/ops/doctor.py` (checks `git` and `github repositories`);
- `docs/github.md` (new: token creation per choice, the permission table, protection advice, the machine-user
  recommendation, webhooks, troubleshooting), `docs/deploy.md` (link only);
- tests `test_m14_cli_setup_repo.py`, `test_m14_cli_repo.py`.

Talks only to the step-0 routes (with stubs replaced, its tests run against the real services after WP-C merges;
before that, against a fake `ApiClient` transport like the M13 wizard tests).

Acceptance: its tests; `foundryos setup --help` lists the flags; whole suite.

### WP-E — web (Sonnet)

Owns:

- `web/src/api/repos.ts` (new), `web/src/api/index.ts`, `web/src/api/keys.ts`, `web/src/api/schema.d.ts`
  (regenerated), `web/src/api/types.ts`;
- `web/src/lib/onboarding.ts` (+ test), `web/src/lib/repos.ts` (+ test), `web/src/lib/panels.ts`;
- `web/src/features/onboarding/OnboardingFlow.tsx`, `steps/RepoStep.tsx` (new), `steps/TeamStep.tsx`, and the
  onboarding tests touched by the new step;
- `web/src/features/repos/*` (new);
- `web/src/features/canvas/PanelHost.tsx`, `CommandPalette.tsx`;
- `web/src/features/panels/GoalsPanel.tsx`.

Acceptance: `pnpm test`, `pnpm typecheck`, `pnpm lint`; design tokens only; every state handled.

### Shared files and who owns them

| File | Owner | Others may |
|---|---|---|
| `backend/app/main.py`, `api/routes/webhooks.py`, `api/routes/repositories.py`, `api/routes/agent_repo.py` | step 0 (routes thin and final) | C may change a route body only if a signature in §6.2 proves wrong, and reports it |
| `guardrails/registry.py`, `rules.py`, `ratelimit.py`, `tools.json`, `core/config.py`, `models/*`, `schemas/repository.py`, `alembic/versions/0013_*` | step 0 | nobody; a needed change goes back to the lead as a step-0 follow-up commit |
| `integrations/github.py` | step 0 | A may **add** methods at the end of the class (append-only) if a gap is found; B/C ask A |
| `services/tasks.py`, `reviews.py`, `wakes.py`, `alerts.py`, `notifications.py`, `audit.py`, `events.py`, `agent_api.py` routes | nobody (used, not edited) | — |
| `backend/tests/conftest.py` | nobody | fixtures live in `tests/m14_helpers.py` |
| `docs/architecture.md`, `docs/roadmap.md`, `docs/specs/m14-*.md` | step 0, then the integrator | WPs report deviations in their final message ("as implemented" goes in after merge) |

Commit discipline (memory: commit path by path): each WP commits only its own paths, never `git add -A`.

---------------------------------------------------------------------------------------------------------------------

## 9. Not verifiable here (rule 8) — to check against GitHub's documentation during WP-A/B

All tests use fakes; no real GitHub call is made in M14 development. These GitHub facts the design relies on must be
confirmed from the official docs before they are written into `docs/github.md`:

- the exact fine-grained permission names, and whether **Checks: read** is offered to fine-grained PATs (the design
  falls back to statuses / Actions);
- the response header `GitHub-Authentication-Token-Expiration`;
- `GET https://github.com/<o>/<r>.git/info/refs?service=git-receive-pack` answers 403 for a token without
  Contents write;
- the `mergeable_state` values and the merge API's `sha` parameter (409 on mismatch);
- `PUT /pulls/{n}/update-branch` with `expected_head_sha`;
- `GET /repos/{o}/{r}/rules/branches/{branch}` readable with Metadata read;
- creating a repository with a fine-grained PAT requires Administration write;
- prefilled token-creation URLs (not used unless verified);
- organisation approval of fine-grained PATs and the SSO header format;
- GitHub Enterprise Server (`github_api_url` / `github_web_url` overrides exist but are untested).

---------------------------------------------------------------------------------------------------------------------

## 10. Owner decisions (confirmed 2026-10-27: the defaults proposed here)

1. **Merge default.** New repo: `pr_auto_merge` (agents merge their own PRs after internal review and green CI).
   Existing repo: `pr_human_merge`.
2. **Token identity.** Fine-grained PAT only in M14 (GitHub App later). With a PAT, PRs are opened under the
   token's account, so that person cannot approve them: **recommend a free machine-user account**
   ("foundry-bot") holding the token (docs/github.md, WP-D).
3. **Creating repos** needs an "All repositories" + Administration-write token (to be narrowed afterwards); the
   alternative "create an empty repo on github.com yourself and connect it" is offered. Repository creation by an
   agent (`github.repo.create`) **always needs a human approval**.
4. **Workflow edits are blocked by default** (`protected_paths`, `allow_workflow_changes = false`). The new-repo
   option offers an optional **starter CI workflow** at onboarding (human consent there), so that
   `ci_mode = required` means something.
5. **Architecture note**: stays internal (artifact + memory); it is not committed to the repository.
6. **Issue intake on existing repos**: only issues labelled `foundry`; the existing backlog is never imported.
7. **Public repos**: allowed at creation only with an explicit confirmation (`confirm_public`); a repo that turns
   public later pauses pushes until the owner resumes.
8. **Later projects** share the company repo (`new_project_repository = company_repo` when onboarding chose
   GitHub); agent-requested new repos always need a human approval.
9. **Commit authorship**: agent names with `<agent_id>@agents.foundry.invalid`.
10. **Dependabot PR auto-merge**: out of M14 (follow-up).

---------------------------------------------------------------------------------------------------------------------

## 11. Step 0 as implemented (differences from the plan above that the work packages must know)

What step 0 delivered: migration `0013_github_projects` (revision `"0013"`, down `"0012"`; M15 adds `0014` on top),
models (`models/repository.py`, `CompanyPolicy.new_project_repository`), `schemas/repository.py`, the guardrail
registry changes (system actions, `default_decision`, rate keys, the verdict route, the runtime tool entry), the
settings, the fine-grained-token secret pattern, the complete `GithubClient`, the thin routes, the stub services
and the test infrastructure. Where the code differs from the text above, **the code wins**:

1. **Services return serialized schema objects** (`schemas.ProjectRepository`, `schemas.RepoPullRequest` ...), not
   ORM rows, as `tasks.serialize_tasks` does; the response needs `project_name`, `webhook` and
   `open_pull_request`, which are not columns. Functions that talk to GitHub or the machine take the
   `IntegrationContext` (`ic = request.app.state.integrations`) as a parameter: `create_repository`,
   `reset_integration`, `set_webhook`, `github_accounts.inspect`, `receive_webhook`.
2. **Authorization in the stubs is real.** Every write stub already calls `require_human_admin`; every read stub
   allows any human member and refuses agents (403). Keep these lines when you replace a body.
3. **`GithubClient`** (all existing methods unchanged). Constructor: `GithubClient(http, token, *, base_url,
   sleep, web_url, etags, clock, write_interval)`. Every list/read method takes the repository first
   (`list_issues(repo, ...)`, `find_pull(repo, head)`, `check_runs(repo, ref)`, `merge_pull(repo, number, *, sha,
   method, title)`). Conditional reads: pass the binding's `etags` dict to the constructor and `conditional=True`
   to `list_issues`, `get_pull`, `get_branch`, `get_repo`, `check_runs`, `combined_status`; a 304 returns the
   falsy sentinel `NOT_MODIFIED`. The client exposes `last_rate` (`remaining`, `limit`, `reset_at`),
   `token_expires_at`, `GithubRateLimited.wait_seconds(failures)` and `GithubSsoRequired.sso_url`. Additions to
   the plan: `get_repo`, `list_branches`, `path_exists` (is `.github/workflows` there), `is_empty`, `get_json`.
   Errors carry `details["reason"]` (`unauthorized`, `forbidden`, `not_accessible`, `not_found`, `not_allowed`,
   `conflict`, `unprocessable`, `rate_limited`, `sso_required`; merge adds `sha_mismatch` / `not_mergeable`) and
   `details["already_exists"]` for a duplicate pull request. Only the M14 content-creating methods are spaced by
   `write_interval` (1 s by default); the M8 methods behave as before. A moved repository (301) is an error.
4. **`services/repo_errors.py`** (new, shared) holds one exception class per code of the error table in 6.3
   (`TokenCannotPush`, `RepositoryExists`, `PublicUrlEphemeral`, `PullRequestChanged`, `RepoNotBound`, ...) and
   `not_built()`. Use them; do not invent codes.
5. **`repo_sync.RepoEnv`** has `from_integrations(ic)` and `from_engine(ctx)`. The worker's `EngineContext` has
   no integration HTTP of its own: `from_engine` reads `ctx.integration_http` (an `IntegrationHttp`, default a
   fresh one). WP-B adds that field to `EngineContext`; the test fixtures set it (`GithubWorld.wire_engine`).
   `actor_for` takes the session: `actor_for(db, binding)`. `request_sync` and `enqueue_clone` are real (a
   few lines each) so WP-C is not blocked; WP-B refines them (webhook debounce). `workspace_spec_for`,
   `dispatch_hold` and `enqueue_due` return their neutral value (`None`, `None`, `0`) until WP-B wires them.
   `WorkspaceSpec` and `SyncSnapshot` are defined in `repo_sync.py` (fields documented there).
6. **`GithubInspectRequest.mode`** (`create` | `connect`, default `connect`) selects which permission checklist
   `missing_permissions` is computed for. `PullReviewBody` has an optional `head_sha`; the verdict route answers
   `PullReviewResult` (`verdict`, `verdict_sha`, `fix_task_id`).
7. **`api/deps.py`**: `repositories` and `github` are in the `_ADMIN_ONLY` route list, so a `write`-scoped API token
   cannot change repositories (a `read` token reads them; an `admin` token does everything).
8. **`repo_git.assert_safe_push_argv`** and `RemoteAuth` are implemented (pure); everything else in `repo_git`,
   `repo_scan`, `repo_gate`, `repo_reviews`, `repo_maintenance`, `github_accounts` and `repositories` raises
   `NotAvailable(milestone="M14")` (501) until its package replaces it.
9. **`company_review_pull_request` is in `guardrails/tools.json`** (action `repo.pr.review`) but the runtime does not
   have the tool yet: `runtime/company_agent/tests/test_guard_registry.py` is red until WP-C adds the tool to
   `runtime/tools/company_tools.py` (and `COMPANY_TOOL_NAMES`).
10. **Test infrastructure**: `tests/m14_github_fake.py` (`FakeGitHub`), `tests/m14_git_remote.py` (`GitRemote`),
    `tests/m14_helpers.py` (`github_world`, `bare_world`, `bound_project`, `push_human_commit`, `fake_ci`,
    `askpass_env`). See the module docstrings; the self-tests are `test_m14_fakes.py` and
    `test_m14_github_client.py`.
