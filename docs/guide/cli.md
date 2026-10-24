# The command line

Most people never need it. But everything in the screen can also be done from a terminal, which is handy for scripts and for fixing problems.

## Two names, one program

The command is **`foundryos`**. The old name **`companyos`** keeps working as an alias, so existing scripts do not break.

> **Today versus v0.1.0.** The installed command is currently called `companyos`, and the examples below use it. The `foundryos` name, the no-argument "just start it" behavior and the install-related commands marked *(v0.1.0)* arrive with the first release. Everywhere this page says `companyos`, `foundryos` will do the same.

## Starting and stopping

| Command | Does |
|---|---|
| `foundryos` *(v0.1.0)* | Starts Foundry in the background if it is not running and opens your browser |
| `companyos up` | Starts everything in your terminal at <http://127.0.0.1:8700>. Press Ctrl-C to stop |
| `foundryos start`, `stop`, `restart` *(v0.1.0)* | Control the background copy |
| `foundryos open`, `logs [-f]` *(v0.1.0)* | Open the browser; show the log (`-f` follows it) |
| `foundryos version` *(v0.1.0)* | Print the version |
| `foundryos service install` / `uninstall` *(v0.1.0)* | Start at login on macOS (LaunchAgent) or Linux (systemd user service). Homebrew installs use `brew services` instead |
| `foundryos update`, `rollback` *(v0.1.0)* | See [Updates and backups](updates-backups.md) |
| `foundryos uninstall [--purge]` *(v0.1.0)* | Remove the program. Your data stays unless you add `--purge` and confirm |
| `companyos doctor` | Checks the machine and prints the fix for each problem. Exits with an error if a check fails |

Useful options for `up`: `--port 8700`, `--open` (open the browser), `--no-build`, `--no-worker`.

## Signing in

```bash
companyos login        # asks for your username and password, stores a personal token
companyos whoami
companyos logout
```

The password is never stored, only a token. Secrets are never accepted as arguments, so they do not end up in your shell history. They come from a hidden prompt or from standard input.

## Everyday commands

```bash
companyos status                               # company, spending, approvals, alerts
companyos note "Build a landing page for our first product"
companyos notes ls
companyos goal ls
companyos goal add "Reach 100 newsletter signups"
companyos agents ls
companyos agents templates                     # the job templates HR can hire from
companyos agents hire -t research-analyst -n Riley
companyos agents pause Riley                   # also: resume, retire
companyos tasks ls
companyos tasks create "Write the pricing page copy" --assignee Riley
companyos runs tail --agent Riley -f           # watch an employee work, live
companyos approvals ls
companyos approvals approve ID                 # or: reject ID --note "why"
companyos skills search "docker" --github
companyos reach                                # your local, network and public addresses
```

IDs can be shortened to a unique start. Agents can be named instead of using IDs. Add `--json` to any command for machine-readable output.

## Money and limits

```bash
companyos budget show
companyos budget set --monthly 50              # USD
companyos budget set --monthly 5 --agent Riley
companyos policy show
```

## Emergency controls

```bash
companyos pause                  # pause one company
companyos resume
companyos stop --all             # emergency stop: every company, every run (owner only)
companyos resume --all
```

## Setting up AI

```bash
companyos providers detect --register          # find a local Ollama and add it
companyos providers ls
companyos providers test "Ollama 127.0.0.1:11434"
companyos credentials set NAME --stdin         # store an API key from standard input
```

## Machine-only commands

These work only on the computer that runs Foundry, because they read the files in the data folder:

```bash
companyos auth setup-token               # the setup token for a first sign-up from another device
companyos auth reset-password USERNAME   # forgot the password? this is the recovery
```

`reset-password` signs the account out everywhere and records it in the audit log. It is deliberately not available over the network.

## Backups

```bash
companyos backup create
companyos backup restore ARCHIVE --verify-only
```

More in [Updates and backups](updates-backups.md).

## Exit codes

0 ok, 1 error, 2 wrong usage, 3 not signed in, 4 not allowed, 5 not found, 6 conflict, 7 server not reachable.

## Settings by environment

The CLI reads `COMPANYOS_API_URL`, `COMPANYOS_COMPANY` and `COMPANYOS_TOKEN`. Its saved files live in `~/.config/companyos/` (mode 0600). `FOUNDRYOS_DATA_DIR` and `FOUNDRYOS_PORT` *(v0.1.0)* will move the data folder and the port; today the equivalents are `COMPANY_OS_DATA_DIR` and `--port`. The full list is in [docs/deploy.md](../deploy.md#7-configuration-reference).
