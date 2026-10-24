# Changelog

All notable changes to Foundry AI OS. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versions follow [Semantic Versioning](https://semver.org/). `scripts/release.sh` moves the "Unreleased"
section under the new version when a release is cut, and the release workflow publishes that section as the
GitHub Release notes.

## [Unreleased]

### Added

- First public release of Foundry AI OS: a self-hosted operating system for an autonomous company of AI
  agents. An AI Co-founder turns the Founder's notes into goals, hires the skills it lacks, schedules work and
  runs it under budgets, approvals and guardrails enforced in code; one canvas shows the org, work, artifacts
  and costs.
- Install options: Homebrew (`brew install suriyavasan/tap/foundry-os`), a one-line installer
  (`install.sh`), Docker (`ghcr.io/suriyavasan/foundry-os`) and from source.
- Self-contained release bundles for macOS (Apple Silicon, Intel) and Linux (x86_64, arm64): their own
  Python 3.14 interpreters, locked dependencies, the agent runtime and the prebuilt web UI. Nothing to install
  first (no Python, Node, uv or git).
- `SHA256SUMS` for every release; the installer and the Homebrew formula verify downloads against it.
