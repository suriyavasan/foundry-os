# GitHub-backed projects (M14): the operator guide

Your AI team can keep a project's code in a GitHub repository: it clones it, works in isolated branches, pushes
a work branch, opens pull requests, reviews them, waits for CI and (if you allow it) merges. This page is for the
person who sets that up. The design is in `docs/specs/m14-github-backed-projects.md`; the safety rules are in
`docs/security.md`.

What it never does: force-push, push to a branch you did not allow, give a coding CLI or an agent the token,
or work around branch protection. Agents and coding CLIs have no network access for git; the control plane does all of it.

## 1. Choose a path

| | New repository | Existing repository | No GitHub |
|---|---|---|---|
| What happens | Foundry creates `owner/name` (empty), makes the first commit (README, `.gitignore`) and pushes it | Foundry clones it, makes its own work branch `foundry/<project>` and first reads the codebase (a task) | Projects stay on this machine, as before |
| Default merge mode | `pr_auto_merge`: agents merge their own pull requests after their own review and green CI | `pr_human_merge`: agents open pull requests, **you** merge | none |
| Default issue intake | issues by owners, members and collaborators | only issues labelled `foundry`; old issues are not imported | none |
| Default CI rule | green if the repository has CI | `required` if `.github/workflows/` exists | none |
| Token needs | Administration (to create the repository) | no Administration | none |

You can change all of these later (`foundryos repo set`, or the repository panel in the web app).

Two ways to get there from a terminal:

```bash
# during first-run setup (interactive: a menu, a hidden prompt for the token)
foundryos setup                 # or: foundryos setup --only repo

# without any prompts (scripts, CI, a headless server); the token never goes on the command line
printf 'github_token=%s\n' "$GITHUB_TOKEN_FOR_FOUNDRY" | foundryos setup --only repo --non-interactive \
  --github existing --github-repo my-org/my-app --github-merge pr-human --secrets-stdin

# any time later
foundryos repo connect --repo my-org/my-app --project-name "My app"        # asks for the token hidden
foundryos repo connect --create --repo my-new-app --project-name "My app"  # creates it on GitHub first
```

The idea you typed during setup becomes the first Founder's note **after** this step, so the AI Co-founder sees the
project together with its repository.

## 2. Create the token

Foundry uses one **fine-grained personal access token** per GitHub account, stored encrypted in the credential vault.
It is read only when the control plane talks to GitHub or runs git, and it is never shown, logged, written into
`.git/config`, a remote URL, an agent's instructions or any config an agent can see.

Create it at <https://github.com/settings/personal-access-tokens/new> and set:

- **Resource owner**: you, or the organisation that owns (or will own) the repository.
- **Expiration**: your choice. Foundry warns you 14 days and again 3 days before it ends (see section 7).
- **Repository access**
  - existing repository: *Only select repositories*, then pick the repository;
  - new repository: *All repositories* (GitHub needs that to let a token create one). Narrow it to the new
    repository afterwards, and remove Administration. **Or** create an empty repository on github.com yourself and
    use the "existing repository" path: no Administration permission is needed at all. That is the more cautious route.
- **Repository permissions**:

| Permission | New repo | Existing repo | What Foundry uses it for | If it is missing |
|---|---|---|---|---|
| Metadata | Read (automatic) | Read | everything | nothing works |
| Contents | Read and write | Read and write | clone, fetch, push, merge | cannot connect (`token_cannot_push`) |
| Pull requests | Read and write | Read and write | open, update, comment, merge | pull-request modes refused; only `direct_push` is possible |
| Issues | Read and write | Read and write (Read if intake is off) | issue intake, comments, labels | intake and comments are switched off |
| Commit statuses | Read | Read | CI state | CI is read from Actions and check runs only |
| Actions | Read | Read | workflow runs and failure logs | CI fix tasks get no logs |
| Checks | Read (if the token page offers it) | Read | check runs | falls back to statuses and Actions |
| Dependabot alerts | Read | Read | security alerts | alert tasks are switched off |
| Administration | Read and write, **only to create the repository** | not needed | creating the repository | creating is refused with the exact message |
| Workflows | **No** (only if you enable workflow changes) | No | pushing `.github/workflows/**` | workflow edits are blocked anyway |
| Webhooks | only if you enable webhooks | only if you enable webhooks | creating the webhook | Foundry polls instead |

The setup step and `foundryos repo connect` print this checklist before they ask for the token, then show you what the
token can actually do (`inspect`): the missing permissions are listed, each with the feature it turns off.

**Classic tokens** work, with a warning: a classic token reaches every repository of the account. If you must use one,
tick `repo` (or `public_repo` for public repositories only), `workflow` only if you enable workflow changes, and
`admin:repo_hook` only for webhooks. Never tick `delete_repo`.

**GitHub Apps are not supported in M14** (a follow-up). The token records `auth_kind = "pat"`.

**Organisations.** An organisation can require an owner to approve fine-grained tokens first: until then GitHub answers
403 and Foundry shows "waiting for organisation approval". If the organisation uses SAML single sign-on, authorise the
token for it (Foundry shows the URL GitHub sends). Both are on GitHub's side; Foundry cannot do them for you.

## 3. Use a machine user for the token

A pull request is opened under the **token's account**. GitHub does not let you approve your own pull request, so if the
token is yours, a "require one approval" branch rule can never be satisfied by you.

Recommended: create a free second GitHub account (for example `yourname-foundry-bot`), invite it to the repository as a
collaborator with *Write* access (or add it to the organisation as a member of a team with write access), create the
token there, and keep your own account as the reviewer. Then:

- you approve and merge what the agents open;
- GitHub's history shows clearly what the team did;
- revoking the token touches nothing of yours.

Check GitHub's current terms about machine accounts before you create one.

## 4. Branch protection (your job; Foundry only reads it)

Foundry **reads and respects** protection; it does not create it (private repositories need a paid plan for protected
branches, and the choice is yours). Advice:

- Protect the default branch: require a pull request, require status checks (your CI), block force pushes and deletion.
- Keep `pr_human_merge` (existing repositories) until you trust the team; then switch with
  `foundryos repo set --merge pr-auto`.
- Foundry merges only when GitHub reports the pull request `clean` (or `has_hooks`) **and** its head is exactly the
  commit the team reviewed (it sends that SHA to the merge call). With an admin token it still cannot bypass protection.
- `direct_push` (no pull request) is refused while the default branch is protected, and is a poor fit for anything you
  care about.
- Do not give the agents' token `Workflows`: they could then change CI to say whatever they like. With the default
  policy a push touching `.github/workflows/**`, `.github/actions/**` or `.gitmodules` is blocked before it leaves the machine.
- Keep secrets out of the repository anyway: every commit is scanned for credential-shaped strings, your stored
  secrets, `.env`-style files, private keys and oversized files before it is pushed, and a finding blocks the push.

## 5. What happens day to day

- **Work**: each task runs in its own branch and merges into the project's work branch `foundry/<project>`. After a
  quiet period (default 10 minutes) Foundry pushes it and opens one pull request into the default branch.
- **Review**: an agent reviews the pull request (not the one who did most of the work). Human reviews and comments from
  collaborators are turned into tasks too.
- **CI**: failing checks on the pull request become fix tasks, up to a limit (then it waits for you).
- **Merge**: `pr_human_merge` leaves the merge to you (you get a notification); `pr_auto_merge` merges once the review
  is accepted and CI is green.
- **Upstream changes** (your own commits, other people's merges) are merged into the work branch. Foundry never rebases
  and never force-pushes. If they conflict, a task is created to resolve it.
- **Issues, security alerts, stale `foundry/*` branches**: handled by the rules in `foundryos repo set` and the
  repository panel. Text from GitHub (issues, comments, logs) is treated as untrusted input.
- **Existing repositories** first get a "learn the codebase" task: an architecture note and a handful of memory entries.
  Other work in that project waits until it is done (or has failed).

Look at it with `foundryos repo list`, `foundryos repo status`, `foundryos repo prs`, `foundryos repo items`,
`foundryos repo activity`; sync at once with `foundryos repo sync`.

## 6. Webhooks (optional)

Polling is the default and needs nothing from you: every repository is checked every 5 minutes (60 s to 1 h) with
conditional requests, which cost very little of GitHub's rate limit. Webhooks only make that faster: GitHub calls
`/api/webhooks/github/<repository id>` on your server, Foundry checks the HMAC signature, and then does a normal sync.
**The webhook payload itself is never trusted or used.**

- They need a **stable public URL** (a named Cloudflare tunnel, Tailscale Funnel, your own domain). A quick tunnel changes
  its address on every restart, so Foundry refuses with `public_url_ephemeral`. See `docs/deploy-reachability.md`.
- The token needs the **Webhooks** (Read and write) permission, otherwise: `webhook_permission_missing`.
- Turn it on or off: `foundryos repo webhook --on` / `--off` (or the repository panel). The secret is generated for you
  and stored encrypted.
- If your public URL changes later, the repository shows the webhook as stale and polling continues; turn it off and on again.

## 7. Keeping it healthy

- `foundryos doctor` checks that `git` is installed and lists the repositories that need attention (a refused token, a
  paused or erroring repository, a blocked push, a token that expires within 14 days). It reads the local database only.
- **Replace the token** (it expired, was revoked, or you changed its permissions): `foundryos repo token`. It asks for the
  new token hidden (or reads `github_token=` from `--secrets-stdin`), stores it in place, tests it and queues a sync.
  Nothing is lost while the token is bad: the team keeps working locally and the next sync pushes.
- **Pause** all network traffic for a repository with `foundryos repo pause` (local work continues); `resume` undoes it.
- **Disconnect** a project with `foundryos repo disconnect`. Nothing is deleted on GitHub; the clone stays and the
  project keeps working locally. Connect again later with `foundryos repo connect --project NAME ...`.
- Connecting a project that already has local history to an **empty** repository can publish that history
  (`--publish-local`); to a **non-empty** repository the old local copy is moved aside, not deleted and not merged.

## 8. Troubleshooting

| You see | Meaning | What to do |
|---|---|---|
| `administration_permission_missing` | The token cannot create repositories | Add Administration (Read and write) for the moment, or create an empty repository on github.com and connect it as an existing one |
| `token_cannot_push` | No Contents (Read and write) on that repository | Edit the token: repository access must include it, Contents read and write |
| `repository_not_accessible` | Wrong name, or the token cannot see it | Check `owner/name`; add the repository to the token's access; an organisation may have to approve the token, or you must authorise it for SAML SSO |
| `repository_exists` | The name is taken | Pick another name or connect the existing repository |
| `repository_archived` | Read-only on GitHub | Unarchive it first |
| `repository_too_large` | Over the size limit (`repo_max_size_mb`, 1 GB) | Use a smaller repository or raise the limit |
| `confirm_public_required` | A public repository needs an explicit yes | `--github-public-ok` (setup) or `--public-ok` (`repo connect`); remember everything the agents commit becomes public |
| `git_unavailable` | `git` is not installed here | Install git, then `foundryos doctor` |
| status `auth_failed` | GitHub answered 401 | `foundryos repo token`. Foundry probes about once an hour until it works |
| a feature is "missing a permission" (alert `github_capability_missing`) | The token lacks e.g. Issues or Actions | Add the permission in the token's settings; the feature switches itself back on |
| sync state `push_blocked` | The push gate found a secret, a protected path or an oversized file | Read the alert; remove the cause in the project's branch; then `foundryos repo reset-integration` (the old head stays as a local `foundry/rescue-*` branch) |
| sync state `upstream_conflict` | Your default branch conflicts with the team's work | A task resolves it (twice at most). Otherwise merge the default branch into the work branch yourself and push; Foundry picks it up |
| sync state `upstream_rewritten` | Someone force-pushed the default branch | Foundry pushes nothing until you decide: `foundryos repo reset-integration` |
| `rate_limited` | GitHub's limit | Nothing; it backs off to the reset time |
| "PR was closed without merge" | Someone closed it | Pull-request opening is paused so it does not loop; `foundryos repo resume-prs` when you want it again |
| `direct_push` refused (`branch_protected`) | The default branch is protected | Use `pr-human` or `pr-auto` |
| `public_url_ephemeral` | A quick tunnel has no stable address | Use a named tunnel or Tailscale, or keep polling |
| `webhook_permission_missing` | The token lacks Webhooks | Add it, or keep polling |
| `repository_busy` (disconnect) | A pull request is being merged or a run is active | Try again in a minute |
| "I cannot approve the pull request" | It was opened under your own account | Use a machine user for the token (section 3) |

## 9. Verified / not verified

Verified in the test suite against a faithful fake of the GitHub REST API and a real local git server (smart HTTP):
the CLI and web flows, token handling (the token is never in argv, `.git/config`, the remote URL or logs), the push
gate, the sync and pull-request state machine, and the owner API.

**Not verified against the real GitHub** (nothing in M14's development calls it), so check these against GitHub's own
documentation when you set up, and report differences:

- the exact permission names and the page layout on the token form, and whether **Checks: Read** is offered for fine-grained tokens;
- the token-creation URLs used in this guide and in the setup step;
- the response header with the token's expiry date (without it Foundry shows no expiry warning);
- that a token without Contents (write) is refused when Foundry checks push access before binding;
- the `mergeable_state` values and the merge call's SHA check;
- the update-branch call with `expected_head_sha`;
- reading branch rules with only Metadata (read);
- creating a repository with a fine-grained token needing Administration (write);
- organisation approval of fine-grained tokens and the SSO header format;
- GitHub Enterprise Server (the API and web URLs can be overridden, but that is untested).
