# Foundry AI OS

**A company that runs itself, on your own computer.** Foundry AI OS is a self-hosted operating system for a company staffed by AI agents. You are the **Founder**. An **AI Co-founder** turns your ideas into goals, hires specialists, hands out work and reports back. You watch it all on one screen, and you stay in charge of anything that costs money or leaves the building.

> Foundry AI OS was called "Company OS" while it was being built. Some internal names (for example the `COMPANY_OS_*` settings) still use the old name.

**Status:** early software, not a finished product. The core is built and was tested on one Mac with a small local model. Parts are still in progress and parts have never been run. [What is and is not verified](#what-is-and-is-not-verified) says exactly which.

## What it does

- **An AI Co-founder manages everyone.** Write a thought in plain words. The Co-founder reads it, sets goals, starts projects, assigns leads and replies to you with what it did.
- **HR hires and trains.** When the company lacks a skill, the HR agent finds or writes a skill (a short instruction pack) and hires an AI employee who has it.
- **Real work gets done.** Employees plan, research, write and produce files you can open. Leads review the work. Schedules (daily stand-up, weekly review, budget check) keep things moving without you.
- **Command Center and Reflections.** Type a note or a `/command` and always get an answer back. The Co-founder also posts reflections on how the company is doing. You react, and the standards you endorse become rules every agent follows.
- **Guardrails written as code, not as polite requests.** Risky actions are sorted into tiers. Anything with real money or a real-world commitment waits for your approval. Budgets, rate limits and an emergency stop are enforced by the program, and agents cannot change their own limits.
- **It runs on your machine.** Your data stays in a folder on your computer. You bring your own AI: a key from OpenRouter, Anthropic or OpenAI, any OpenAI-compatible service, or a free local model through Ollama.
- **Coding tools can do the work.** Optionally, employees can do their work through coding command-line tools you already have (Claude Code, Codex, OpenCode, Google Antigravity CLI), while your AI key runs the management side.
- **Reach it from anywhere.** Use it on your phone over your home network, or through a secure tunnel, your own domain and company email. You connect each outside account once; the AI Co-founder never signs up for anything by itself.
- **A free public URL, no account.** `foundryos reach public start` gives you an `https://<words>.trycloudflare.com` address (a Cloudflare quick tunnel). It changes whenever the tunnel restarts; `foundryos reach --watch` and `foundryos status` show the current one. For a stable address use a named tunnel on your own domain or Tailscale Funnel.

## Install

> Published since v0.1.0 (2026-10-24). Bundles exist for Apple Silicon macOS and Linux (x86_64, arm64); Intel Macs use Docker.

**1. Homebrew** (Apple Silicon Macs and Linux)

```bash
brew install suriyavasan/tap/foundry-os
```

Then run `foundryos`. It starts in the background and opens your browser (on a machine with no screen it prints the addresses instead, see [Headless / SSH](#headless--ssh)). Update later with `brew upgrade foundry-os`. To start it at login: `brew services start foundry-os`.

**2. One-line installer** (Apple Silicon Macs and Linux, no Homebrew needed)

```bash
curl -fsSL https://raw.githubusercontent.com/suriyavasan/foundry-os/main/install.sh | sh
```

It installs, starts the background service and opens your browser (over SSH it hands over to `foundryos setup` instead). Update later with `foundryos update`.

**3. Docker** (any system, including Windows)

```bash
docker run -d --name foundry-os -p 8700:8700 -v foundry-os:/data ghcr.io/suriyavasan/foundry-os:latest
```

Then open <http://localhost:8700>. Your data lives in the `foundry-os` volume.

Windows without Docker is not supported in the first release. Use Docker, or WSL (Linux inside Windows).

### Headless / SSH

On a server or a Mac you only reach over SSH there is no browser, so nothing tries to open one. Set everything up in the terminal:

```bash
curl -fsSL https://raw.githubusercontent.com/suriyavasan/foundry-os/main/install.sh | sh -s -- --linger
foundryos setup        # account, company, your AI, work mode, background service, optional free public URL
```

`foundryos setup` can be run again (finished steps are skipped) and has a script form: `foundryos setup --non-interactive --secrets-stdin ...` (secrets are never command-line options). `--linger` keeps the service running after you log out on Linux. To use the screen from your own computer, forward the port (`ssh -N -L 8700:127.0.0.1:8700 user@host`, then open <http://localhost:8700>) or use the public URL. Details: [docs/install.md](docs/install.md#headless-server--ssh). This path was tested with fakes (fake tunnel, service manager and providers), not on a real server.

### From source

For developers. You need `uv` and Python 3.12 or newer, plus Node 20 or newer and `pnpm` to build the screen once. The full steps are in [docs/deploy.md](docs/deploy.md).

```bash
cd backend
uv sync
uv run companyos doctor     # tells you what is missing and how to fix it
uv run companyos up         # starts everything at http://127.0.0.1:8700
```

The agent runtime (the part that lets employees actually work) is set up separately; see section 2 of [docs/deploy.md](docs/deploy.md).

## First run

1. **Create your account.** A username and a strong password. This account owns the company.
2. **Connect your AI.** Pick OpenRouter, Anthropic, OpenAI, any OpenAI-compatible service, or local Ollama. Foundry tests the connection and will not continue until it works. The company cannot run without a working AI.
3. **Choose how work gets done.** "API only" (everything runs on your AI key) or "API plus coding tools". You can skip this and change it later.
4. **Write your first note.** For example: "Build a landing page for our first product." The AI Co-founder takes it from there.

Steps 2 and 3 belong to the new setup screens that are still being finished. On a machine without a screen, `foundryos setup` does all of this in the terminal. See [Getting started](docs/guide/getting-started.md).

## Requirements

- An Apple Silicon Mac or a Linux computer. Intel Macs and Windows: use Docker (or WSL on Windows).
- A few hundred MB of disk plus room for your data. `doctor` warns below 2 GB free.
- An AI to think with: an API key, or Ollama with a model that supports tool calling. A small local model works but is slow and follows long plans only partly.
- From source only: Python 3.12+, `uv`, Node 20+ and `pnpm`.

## What is and is not verified

We only claim what was run against the real thing. The dated record is in [docs/roadmap.md](docs/roadmap.md).

**Verified (macOS, SQLite, a local 7B model, 2026-10-24):** creating a company, hiring an employee, running a task end to end, saving results, recording usage and cost, blocking spend over budget, approvals for protected actions, pausing and retiring agents, recovering after a crash, and deploying from the written steps.

**Verified for v0.1.0 (2026-10-24):** `brew install` and the one-line installer on an Apple Silicon Mac; the Linux bundles and the Docker image are built, installed and health-checked in CI.

**Not verified:** Docker Compose, PostgreSQL, Linux and Windows hosts outside CI, any cloud AI model, and a real tunnel behind TLS. The coding-tool support, Reflections and Command Center are built but still in progress. A small local model is a weak agent: it did not finish a project on its own in 30 minutes. A stronger model is the biggest improvement you can make.

## Safety boundaries

- **No automated sign-ups.** Agents never create accounts that need a CAPTCHA, a phone check or accepting terms. You connect those accounts once, and after that the AI Co-founder works inside the guardrails.
- **Real money needs you.** Buying a domain, a subscription or anything outside budget waits for your approval, bound to the exact amount.
- **Guardrails are code.** Risk tiers, approvals, budgets, rate limits and an emergency stop are enforced by the program. Agents cannot raise their own limits.
- **Secrets stay hidden.** Keys are stored encrypted, are write-only in the screen, and are never shown to agents.
- **Estimates are labelled.** Costs are marked verified, estimated or unknown, and an estimate is never shown as a charge.

More in [docs/security.md](docs/security.md), including the known gaps.

## Guides

- [Getting started](docs/guide/getting-started.md)
- [Connect your AI](docs/guide/connect-your-ai.md)
- [How your company works](docs/guide/how-your-company-works.md)
- [Guardrails and money](docs/guide/guardrails-and-money.md)
- [Reach it from anywhere](docs/guide/reach-it-from-anywhere.md)
- [Team](docs/guide/team.md)
- [The command line](docs/guide/cli.md)
- [Updates and backups](docs/guide/updates-backups.md)
- [Troubleshooting](docs/guide/troubleshooting.md)
- [FAQ](docs/guide/faq.md)

For developers: [AGENTS.md](AGENTS.md), [docs/architecture.md](docs/architecture.md), [docs/deploy.md](docs/deploy.md), [docs/api.md](docs/api.md), [docs/roadmap.md](docs/roadmap.md).

## License and credits

Foundry AI OS is open source under the [MIT License](LICENSE).

Its agent runtime is a pruned fork of [Hermes Agent](https://github.com/NousResearch/hermes-agent) by Nous Research (MIT). We keep their license and mark every change we made. See [runtime/FORK.md](runtime/FORK.md).
