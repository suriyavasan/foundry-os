# Changelog

All notable changes to Foundry AI OS. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versions follow [Semantic Versioning](https://semver.org/). Every green push to `main` is released
automatically with the next patch version (see `docs/install.md`, "Cut a release"). A release's notes are its
`## [x.y.z]` section here when there is one, else the commit subjects since the previous release.
`scripts/release.sh <version>` is for a new minor or major: it moves the "Unreleased" section under that version.

## [Unreleased]

### Added

- Google Antigravity CLI (`agy`) as a fourth coding-CLI work runtime (`cli:antigravity`), next to Claude Code,
  Codex and OpenCode. It runs only with a Gemini API key (Google AI Studio) in an isolated per-agent home, with
  shell commands and URL fetches denied by generated permission rules and only the workspace files and the
  company tools allowed. It is coded from `agy --help` and Google's documentation and tested with a fake binary;
  it has not been run against a real prompt. Google's installer is a `curl | bash` script, so Foundry AI OS shows
  the command for you to run yourself instead of installing it.

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
