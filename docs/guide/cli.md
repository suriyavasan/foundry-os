# The command line

Most people never need it. But everything in the screen can also be done from a terminal, which is handy for scripts and for fixing problems.

## Two names, one program

The command is **`foundryos`**. The old name **`companyos`** keeps working as an alias, so existing scripts do not break (one difference: `companyos` with no arguments still prints the help, because that is what it always did).

In a copy you run from source, put `uv run` in front inside `backend/` (`uv run foundryos status`). The installer and Homebrew put `foundryos` on your PATH.

## Starting and stopping

Just type `foundryos`. If Foundry AI OS is not running, it starts in the background, waits until it answers, and opens your browser (on the very first run you land on the setup screen). If it is already running, it only opens the browser.

| Command | Does |
|---|---|
| `foundryos` | Starts Foundry AI OS in the background if it is not running, then opens your browser |
| `foundryos start` | Same, without opening the browser |
| `foundryos stop` | Stops the background copy (for a background *service*, until you next log in or start it) |
| `foundryos restart` | Stop, then start |
| `foundryos open` | Opens the browser (it must be running) |
| `foundryos logs [-f] [-n 100]` | Shows the end of the server log; `-f` keeps following it |
| `foundryos version` (or `--version`) | Prints the version; `version --json` also says how it was installed and where the data is |
| `foundryos status` | The company at a glance (not the process: that is `service status`) |
| `foundryos service status` | Is it running, how it was started, and whether it starts at login |
| `foundryos service install` / `uninstall` | Start at login: a LaunchAgent on macOS, a systemd user service on Linux. Homebrew installs use `brew services` instead |
| `foundryos update`, `rollback` | See [Updates and backups](updates-backups.md) |
| `foundryos uninstall [--purge]` | Remove the program. Your data stays unless you add `--purge` and confirm |
| `foundryos serve` | Runs in the foreground without a browser. This is what the background service runs |
| `foundryos up` | Runs in your terminal at <http://127.0.0.1:8700>. Ctrl-C stops it |
| `foundryos doctor` | Checks the machine and prints the fix for each problem. Exits with an error if a check fails |

Useful options for `up`: `--port 8700`, `--open` (open the browser), `--no-build`, `--no-worker`.

Where the background copy writes its log: `<data folder>/logs/foundry-os.log` (10 MB per file, the last 5 are kept). A Homebrew service logs to Homebrew's `var/log/foundry-os.log`, which `foundryos logs` also finds.

**Background service in detail.** `foundryos service install` writes `~/Library/LaunchAgents/ai.foundry-os.plist` (macOS) or `~/.config/systemd/user/foundry-os.service` (Linux), starts it, and from then on it starts when you log in and restarts if it crashes. On Linux it runs only while you are logged in, unless you add `--linger` (which uses `loginctl enable-linger` and may ask for permission). If you installed with Homebrew, `service install` does nothing except tell you to use `brew services start foundry-os`: it never creates a second service next to Homebrew's. `foundryos stop` also stops the service (until the next login); `foundryos service uninstall` removes the autostart.

## Signing in

```bash
foundryos login        # asks for your username and password, stores a personal token
foundryos whoami
foundryos logout
```

The password is never stored, only a token. Secrets are never accepted as arguments, so they do not end up in your shell history. They come from a hidden prompt or from standard input.

## Everyday commands

```bash
foundryos status                               # company, spending, approvals, alerts
foundryos note "Build a landing page for our first product"
foundryos notes ls
foundryos goal ls
foundryos goal add "Reach 100 newsletter signups"
foundryos agents ls
foundryos agents templates                     # the job templates HR can hire from
foundryos agents hire -t research-analyst -n Riley
foundryos agents pause Riley                   # also: resume, retire
foundryos tasks ls
foundryos tasks create "Write the pricing page copy" --assignee Riley
foundryos runs tail --agent Riley -f           # watch an employee work, live
foundryos approvals ls
foundryos approvals approve ID                 # or: reject ID --note "why"
foundryos skills search "docker" --github
foundryos reach                                # your local, network and public addresses
```

IDs can be shortened to a unique start. Agents can be named instead of using IDs. Add `--json` to any command for machine-readable output.

## Money and limits

```bash
foundryos budget show
foundryos budget set --monthly 50              # USD
foundryos budget set --monthly 5 --agent Riley
foundryos policy show
```

## Emergency controls

```bash
foundryos pause                  # pause one company
foundryos resume
foundryos stop --all             # emergency stop: every company, every run (owner only)
foundryos resume --all
```

## Setting up AI

```bash
foundryos providers detect --register          # find a local Ollama and add it
foundryos providers ls
foundryos providers test "Ollama 127.0.0.1:11434"
foundryos credentials set NAME --stdin         # store an API key from standard input
```

## Machine-only commands

These work only on the computer that runs Foundry, because they read the files in the data folder:

```bash
foundryos auth setup-token               # the setup token for a first sign-up from another device
foundryos auth reset-password USERNAME   # forgot the password? this is the recovery
```

`reset-password` signs the account out everywhere and records it in the audit log. It is deliberately not available over the network.

## Backups

```bash
foundryos backup create
foundryos backup restore ARCHIVE --verify-only
```

More in [Updates and backups](updates-backups.md).

## Exit codes

0 ok, 1 error, 2 wrong usage, 3 not signed in, 4 not allowed, 5 not found, 6 conflict, 7 server not reachable.

## Settings by environment

The CLI reads `COMPANYOS_API_URL`, `COMPANYOS_COMPANY` and `COMPANYOS_TOKEN`. Its saved files live in `~/.config/companyos/` (mode 0600). `FOUNDRYOS_DATA_DIR` moves the data folder, `FOUNDRYOS_PORT` the port, and `FOUNDRYOS_UPDATE_CHECK=0` switches off the daily check for a new release (the older `COMPANY_OS_*` names still work and win when both are set). The full list is in [docs/deploy.md](../deploy.md#7-configuration-reference).
