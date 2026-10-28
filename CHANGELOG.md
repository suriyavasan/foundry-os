# Changelog

All notable changes to Foundry AI OS. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versions follow [Semantic Versioning](https://semver.org/). Every green push to `main` is released
automatically with the next patch version (see `docs/install.md`, "Cut a release"). A release's notes are its
`## [x.y.z]` section here when there is one, else the commit subjects since the previous release.
`scripts/release.sh <version>` is for a new minor or major: it moves the "Unreleased" section under that version.

## [Unreleased]

### Added

- One **AI stack** replaces the separate model API / work model / coding CLI settings, in onboarding and in
  Settings. Add any number of AIs of any type — Claude through your plan (the official Claude Code, signed in on
  Anthropic's own page) or the Anthropic API; OpenAI, OpenRouter, OpenAI-compatible or a local Ollama model;
  Cursor through your plan or a Cursor API key; Codex, OpenCode or Antigravity with a key — then order them
  (primary, secondary…), choose what each is for (everything, heavy lifting such as planning, reviews and
  management, or everyday work) and how much to use it (most, more, normal, less, backup only). Foundry spreads
  runs by those shares, skips an AI that is paused or signed out, and falls back to backups; budgets, approvals
  and permissions apply to every one. Existing setups are carried over automatically.
- Plan sign-ins happen only through the CLI's own flow (`claude auth login`, `agent login`; `foundryos ai login`
  in a terminal). Foundry never sees or stores passwords, codes or tokens and allows one plan sign-in per CLI per
  company; any number of API keys is fine. When a plan hits its usage limit, that AI pauses until the reset and
  its work moves to the rest of the stack. `foundryos ai list|add|remove|test|login|logout`.
- Cursor CLI (`agent`) as a fifth coding-CLI work runtime (`cli:cursor`). It runs only with a Cursor API key in an
  isolated per-agent home, with shell commands and web fetches denied by generated permission rules. It is coded
  from Cursor's documentation and tested with a fake binary; it has not been run against the real CLI. Cursor's
  installer is a `curl | bash` script, so Foundry AI OS shows the command for you to run yourself.
- Dashboard "Coding CLIs" card (`GET /finance/cli-usage`): per CLI, today or this month, runs (succeeded / failed),
  tokens, time spent, cost (estimate first), the agents using it, and whether it is installed and how it signs in.
  Runs on a personal subscription login are labelled "subscription — not billed here", never "$0 charged".
- Finance: set any agent's daily and monthly budget inline from Budget utilization. Every working agent is listed
  with both periods side by side, including agents without a limit (their spend is still shown).
- `docs/backlog.md`: the running list of founder ideas (B1, B2, …) before they become milestones.
- Google Antigravity CLI (`agy`) as a fourth coding-CLI work runtime (`cli:antigravity`), next to Claude Code,
  Codex and OpenCode. It runs only with a Gemini API key (Google AI Studio) in an isolated per-agent home, with
  shell commands and URL fetches denied by generated permission rules and only the workspace files and the
  company tools allowed. It is coded from `agy --help` and Google's documentation and tested with a fake binary;
  it has not been run against a real prompt. Google's installer is a `curl | bash` script, so Foundry AI OS shows
  the command for you to run yourself instead of installing it.
- `foundryos setup`: the whole onboarding in a terminal, for servers and Macs you reach over SSH. Account (hidden
  password prompt), optional two-factor sign-in with a terminal QR code, company and idea, the model API with the same
  five live checks as the web app, API-only or API plus a coding CLI (install, sign-in, test), the background
  service and an optional free public URL. It can be run again (finished steps are skipped), `--only <step>` runs
  one, and `--non-interactive` with `--secrets-stdin` and `--json` makes it scriptable. Secrets are never command-line
  options.
- Free public URL from the terminal: `foundryos reach public start|stop` (Cloudflare quick tunnel by default, or
  `named` / `tailscale`; one confirmed, checksum-verified `cloudflared` download) and `foundryos reach --watch`.
  A quick tunnel's address changes on every start, so each change is now remembered (last five), audited, logged,
  shown in the web app (`TunnelCard`: "This address changes when Foundry restarts") and written to
  `<data>/public-url.json`. `foundryos status` shows the public URL first. `GET /api/reachability` reports
  `tunnel.ephemeral`, `tunnel.last_url` and `tunnel.url_changed_at`.
- Linux: `foundryos service install --linger` and `install.sh --linger` keep the service running after you log out
  (`loginctl enable-linger`, never `sudo`); `foundryos doctor` checks headless, linger and service lifetime.
  macOS: when there is no graphical login the LaunchAgent is loaded into `user/<uid>`, and `service install` prints
  the `sudo` steps for a LaunchDaemon.
- `company create` takes `--idea` and `--timezone`.

### Changed

- `PUT /ai-setup` is gone (410); use `/ai-stack`. `GET /ai-setup` now reports readiness from the stack. Model
  routing modes (free first / quality first / paid-fallback approval) no longer apply: the AI stack's order, use
  and importance decide. `foundryos setup` has no separate "work" step any more.
- Finance leads with the estimate: most providers never report a charge, so "Estimated" comes first and "Charged"
  second (with a note when it is $0) in the totals, the wasted-runs strip, budget rows and the spend blocks.
- The organization chart reads left to right by default (compact cards, levels side by side). One button switches
  back to top-down; the choice is remembered in the browser.
- On a machine with no screen (SSH session, container, Linux without a display) `foundryos`, `start`, `open` and `up`
  no longer try to open a browser. They print the local address, an SSH port-forward hint and the public URL, and
  offer `foundryos setup`. `FOUNDRYOS_OPEN_BROWSER=1` forces a browser, `FOUNDRYOS_NO_BROWSER=1` never opens one.
  `install.sh` hands over to `foundryos setup` on a headless terminal.
- `systemctl --user` is retried with `XDG_RUNTIME_DIR=/run/user/<uid>` when the shell has no user bus.
- Passkeys are refused on quick-tunnel (`*.trycloudflare.com`) addresses, because the address changes and a passkey
  made there would stop working; they no longer count toward the public-exposure gate. The session reports
  `passkey_unsupported_reason: "ephemeral_origin"` and the web app says passkeys need a stable address.
- A `cloudflared` process left behind by a crashed server is stopped before a new tunnel starts, and a quick tunnel
  runs with an isolated `HOME`.

### Known limits

- All of the above was tested with fakes (fake `cloudflared`, `launchctl`, `systemctl`, `loginctl`, coding tools and
  providers). No real tunnel, service manager, provider login or SSH hand-off was run. Cloudflare says quick tunnels
  do not support server-sent events; the web app falls back to polling, which we have not seen on a real tunnel.
  Security notes on the new exposure (a username lockout by strangers, claiming an unclaimed install on a shared
  machine, Docker's `--forwarded-allow-ips '*'`) are in `docs/security.md` section 6.

## [0.1.0] - 2026-10-24

> Intel Macs: use the Docker image. Key dependencies no longer publish Intel macOS builds, so there is no
> native Intel macOS bundle.

### Added

- First public release of Foundry AI OS: a self-hosted operating system for an autonomous company of AI
  agents. An AI Co-founder turns the Founder's notes into goals, hires the skills it lacks, schedules work and
  runs it under budgets, approvals and guardrails enforced in code; one canvas shows the org, work, artifacts
  and costs.
- Install options: Homebrew (`brew install suriyavasan/tap/foundry-os`), a one-line installer
  (`install.sh`), Docker (`ghcr.io/suriyavasan/foundry-os`) and from source.
- Self-contained release bundles for macOS (Apple Silicon) and Linux (x86_64, arm64): their own
  Python 3.14 interpreters, locked dependencies, the agent runtime and the prebuilt web UI. Nothing to install
  first (no Python, Node, uv or git).
- `SHA256SUMS` for every release; the installer and the Homebrew formula verify downloads against it.
- `foundryos` command (alias `companyos`): `foundryos` alone starts it in the background and opens the
  browser; `start`, `stop`, `restart`, `open`, `logs`, `serve`; `service install|uninstall|status`
  (LaunchAgent / systemd user unit / `brew services`); `update` + `rollback` with checksum verification;
  an automatic backup before every upgrade; a daily update check (opt-out); `uninstall [--purge]`.
