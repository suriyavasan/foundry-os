# The command line

Most people never need it. But everything in the screen can also be done from a terminal, which is handy for scripts and for fixing problems.

## Two names, one program

The command is **`foundryos`**. The old name **`companyos`** keeps working as an alias, so existing scripts do not break (one difference: `companyos` with no arguments still prints the help, because that is what it always did).

In a copy you run from source, put `uv run` in front inside `backend/` (`uv run foundryos status`). The installer and Homebrew put `foundryos` on your PATH.

## Starting and stopping

Just type `foundryos`. If Foundry AI OS is not running, it starts in the background, waits until it answers, and opens your browser (on the very first run you land on the setup screen). If it is already running, it only opens the browser.

**On a machine with no screen** (you are logged in over SSH, you are inside a container, or it is a Linux server without a display) it never opens a browser. It prints the local address, how to forward the port from your own computer (`ssh -N -L 8700:127.0.0.1:8700 user@host`), and the public address if you have one. If nobody has created the account yet and you are at a terminal, it offers to run `foundryos setup` right there. To force a browser anyway, set `FOUNDRYOS_OPEN_BROWSER=1`; to never open one, set `FOUNDRYOS_NO_BROWSER=1`.

| Command | Does |
|---|---|
| `foundryos` | Starts Foundry AI OS in the background if it is not running, then opens your browser (on a machine with no screen: prints the addresses instead) |
| `foundryos start` | Same, without opening the browser |
| `foundryos setup` | The whole first-time setup in the terminal, see [below](#set-up-from-the-terminal) |
| `foundryos stop` | Stops the background copy (for a background *service*, until you next log in or start it) |
| `foundryos restart` | Stop, then start |
| `foundryos open` | Opens the browser (it must be running). With no screen it prints the addresses |
| `foundryos logs [-f] [-n 100]` | Shows the end of the server log; `-f` keeps following it |
| `foundryos version` (or `--version`) | Prints the version; `version --json` also says how it was installed and where the data is |
| `foundryos status` | The company at a glance, with the public address first when there is one (not the process: that is `service status`) |
| `foundryos service status` | Is it running, how it was started, and whether it starts at login |
| `foundryos service install [--linger]` / `uninstall` | Start at login: a LaunchAgent on macOS, a systemd user service on Linux. Homebrew installs use `brew services` instead. `--linger` (Linux) keeps it running after you log out |
| `foundryos update`, `rollback` | See [Updates and backups](updates-backups.md) |
| `foundryos uninstall [--purge]` | Remove the program. Your data stays unless you add `--purge` and confirm |
| `foundryos serve` | Runs in the foreground without a browser. This is what the background service runs |
| `foundryos up` | Runs in your terminal at <http://127.0.0.1:8700>. Ctrl-C stops it |
| `foundryos doctor` | Checks the machine and prints the fix for each problem. Exits with an error if a check fails. It also says whether this looks like a headless machine, whether Linux linger is on, and whether the service survives a logout |

Useful options for `up`: `--port 8700`, `--open` (open the browser), `--no-build`, `--no-worker`.

Where the background copy writes its log: `<data folder>/logs/foundry-os.log` (10 MB per file, the last 5 are kept). A Homebrew service logs to Homebrew's `var/log/foundry-os.log`, which `foundryos logs` also finds.

**Background service in detail.** `foundryos service install` writes `~/Library/LaunchAgents/ai.foundry-os.plist` (macOS) or `~/.config/systemd/user/foundry-os.service` (Linux), starts it, and from then on it starts when you log in and restarts if it crashes. On Linux it runs only while you are logged in, unless linger is on. `--linger` runs `loginctl enable-linger` for your user. Foundry never runs `sudo`: if that is refused it prints the line to run yourself (`sudo loginctl enable-linger $USER`). Over SSH, where the shell has no user session bus (after `su`, for example), `systemctl --user` is retried with `XDG_RUNTIME_DIR=/run/user/<uid>`. If you installed with Homebrew, `service install` does nothing except tell you to use `brew services start foundry-os`: it never creates a second service next to Homebrew's. `foundryos stop` also stops the service (until the next login); `foundryos service uninstall` removes the autostart.

**A Mac you only reach over SSH.** The LaunchAgent normally needs a graphical login (`gui/<uid>`). Without one, Foundry loads it into `user/<uid>` instead. It runs now and restarts if it crashes, but it does not start after a reboot until someone logs in, and `service status` and `doctor` say so. To start at boot with nobody logged in, turn on automatic login or use a LaunchDaemon: `foundryos service install` prints the exact `sudo` commands (copying the generated plist to `/Library/LaunchDaemons`, then `launchctl bootstrap system ...`). It never runs them, and it tells you to remove the per-user agent first so two copies do not fight over the port.

## Set up from the terminal

```bash
foundryos setup
```

This is the same onboarding as the web app, in your terminal, for a machine you reach over SSH. It walks through these steps and skips the ones that are already done, so you can run it again at any time:

| Step | What it does |
|---|---|
| `service` | Starts Foundry if it is not running, and asks whether to keep it running in the background and after a reboot (on Linux also whether to stay running after you log out) |
| `account` | Creates the owner account (hidden password prompt) and saves a personal token for the CLI |
| `2fa` | Optional authenticator-app sign-in. A QR code is drawn in the terminal; recovery codes are shown once |
| `company` | Company name, a one-line idea (up to 280 characters), timezone |
| `ai` | Connects the model API and runs the same five live checks as the web app. It will not continue until they pass |
| `work` | API only, or API plus a coding tool (Claude Code, Codex, OpenCode, Antigravity), including its install, sign-in and a test |
| `public-url` | Offers a free public address (a Cloudflare quick tunnel), with one confirmed download of `cloudflared` |

Run one step with `foundryos setup --only ai` (or `service`, `account`, `2fa`, `company`, `work`, `public-url`).

For scripts, add `--non-interactive`. It never prompts: it lists everything that is missing, exits with status 2 and creates nothing if an input is absent. Answers are options: `--username`, `--display-name`, `--email`, `--company-name`, `--idea`, `--timezone`, `--provider` (`openrouter`, `anthropic`, `openai`, `openai-compatible`, `ollama`), `--model`, `--base-url`, `--allow-private-network`, `--work-mode api|hybrid`, `--cli`, `--cli-auth own-login|api-key|provider`, `--install-cli`, `--service/--no-service`, `--linger/--no-linger`, `--2fa/--no-2fa`, `--public-url quick|named|tailscale|off`, `--hostname`. `--json` prints one JSON document at the end.

Secrets are never options. Put `key=value` lines on standard input and add `--secrets-stdin`; the allowed keys are `password`, `api_key`, `cli_api_key` and `setup_token`:

```bash
foundryos setup --non-interactive --secrets-stdin --username founder --company-name Acme \
  --provider openrouter --model <model-id> --work-mode api --service --public-url quick < ~/foundry-secrets
```

`--2fa` needs a code from your app, so it cannot be combined with `--non-interactive`. A full example is in [Installing: headless server](../install.md#headless-server--ssh).

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
foundryos reach --watch                        # keep running; print the public address now and every time it changes
foundryos reach public start                   # a free public address (a Cloudflare quick tunnel)
foundryos reach public stop                    # turn it off (it stays off after a restart)
```

IDs can be shortened to a unique start. Agents can be named instead of using IDs. Add `--json` to any command for machine-readable output.

## A public address

```bash
foundryos reach public start [quick|named|tailscale] [--hostname app.example.com] [--yes]
```

`quick` is the default: a free, random `https://<words>.trycloudflare.com` address with no account and no router settings. If `cloudflared` is not installed, the command prints the exact version, download address and SHA-256 and asks before downloading (`--yes` answers for you; the download is still checked against that checksum). It refuses until you have a strong password or two-factor sign-in, like the web app.

A quick tunnel's address **changes every time the tunnel restarts**, including when Foundry restarts. Three ways to keep up with it:

- `foundryos reach --watch` prints the address now, and a new line each time it changes. It waits out a server restart. Press Ctrl-C to stop.
- `foundryos status` and `foundryos` (the start command) show the current address.
- The file `<data folder>/public-url.json` always holds the latest address (`url`, `provider`, `state`, `changed_at`, `previous_url`), readable only by you. When the tunnel stops, `url` is `null`.

The changes are also recorded in the activity log and the server log (`Public URL: ...`), and the web app shows them under Reachability. `named` (your own domain on Cloudflare) and `tailscale` (Tailscale Funnel) give a stable address. See [Reach it from anywhere](reach-it-from-anywhere.md).

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
