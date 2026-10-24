# M11 — Foundry AI OS: one-command install, Homebrew, updates — spec

Owner direction (2026-10-24): any normal person must be able to install it. Offer several
options; one is Homebrew so future updates ship through Homebrew. One install command must
work. Product name: **Foundry AI OS**. Public repo: `github.com/suriyavasan/foundry-os`
(MIT). Tap: `github.com/suriyavasan/homebrew-tap`.

## 0. Names
- Product **Foundry AI OS**; Homebrew formula **`foundry-os`**; command **`foundryos`**
  (not `foundry`: Microsoft's Foundry Local CLI installs a `foundry` binary). `companyos`
  stays as an alias for existing users/scripts.
- Rename user-visible strings (UI title, onboarding, emails/footers, docs, CLI help, README)
  from "Company OS" to "Foundry AI OS". Internal identifiers (`company_os` DB name, `COMPANY_OS_*`
  env vars, Python package paths) stay; add `FOUNDRYOS_*` aliases for the documented env vars
  (`FOUNDRYOS_DATA_DIR`, `FOUNDRYOS_PORT`).

## 1. The install options (README "Install" section, in this order)
1. **Homebrew (macOS, Linux):**
   `brew install suriyavasan/tap/foundry-os` → then `foundryos` (starts it in the background
   and opens the browser). Updates: `brew upgrade foundry-os`. Optional autostart:
   `brew services start foundry-os`.
2. **One-line installer (macOS, Linux; no Homebrew needed):**
   `curl -fsSL https://raw.githubusercontent.com/suriyavasan/foundry-os/main/install.sh | sh`
   → installs, starts the background service, opens the browser. Updates: `foundryos update`.
3. **Docker (any OS incl. Windows):**
   `docker run -d --name foundry-os -p 8700:8700 -v foundry-os:/data ghcr.io/suriyavasan/foundry-os:latest`
4. **From source (developers):** current `docs/deploy.md` flow.
Windows native: not in v1 — Docker or WSL (documented).

## 2. Self-contained release bundles (no uv, pnpm, git, Node or system Python needed)
Built in GitHub Actions for `darwin-arm64` (macos-14), `darwin-x86_64` (macos-13),
`linux-x86_64` (ubuntu-22.04), `linux-arm64` (ubuntu-22.04-arm):
`foundry-os-<version>-<platform>.tar.gz` containing
```
foundry-os/
  VERSION
  bin/foundryos                 # POSIX sh launcher → backend python -m app.cli
  python/backend/               # python-build-standalone 3.14 + backend deps (no venv, relocatable)
  python/runtime/               # python-build-standalone 3.14 + runtime (Hermes fork) deps
  app/backend/  app/runtime/    # sources (no tests, no caches), runtime skills included
  web/dist/                     # prebuilt UI
  LICENSE  THIRD_PARTY_NOTICES  # incl. Hermes MIT notice
```
- Interpreters: `uv python install 3.14` (python-build-standalone, relocatable by design);
  deps installed into each interpreter from the frozen lockfiles (`uv export --frozen` →
  `uv pip install --python <bundle python> --system …` or equivalent verified approach) —
  backend and runtime stay separate because the runtime keeps Hermes' exact pins.
- Strip `__pycache__`, tests, docs from site-packages where safe; report bundle size
  (target < 200 MB compressed).
- `SHA256SUMS` + per-file `.sha256` attached to the GitHub Release.
- The launcher sets `COMPANY_OS_RUNTIME_PYTHON`, `COMPANY_OS_RUNTIME_DIR`,
  `COMPANY_OS_WEB_DIST`, and the default data dir; it never writes inside the bundle.
- A smoke job in CI unpacks each bundle on a clean runner, runs `foundryos --version`,
  `foundryos doctor --json`, starts it, hits `/api/health`, stops it.

## 3. Installed-mode behaviour
- **Data dir** (when not run from a git checkout): macOS
  `~/Library/Application Support/Foundry AI OS`, Linux `${XDG_DATA_HOME:-~/.local/share}/foundry-os`;
  logs in `<data>/logs/` (rotated). Override `FOUNDRYOS_DATA_DIR`. Source checkouts keep
  `<repo>/data`.
- **`foundryos` with no arguments** = "make it work": if not running, start it in the
  background (as the user service when installed, else a detached supervised process with a
  pid file), wait for health, open the browser (setup screen on first run). Subcommands:
  `start`, `stop`, `restart`, `status` (existing semantics), `open`, `logs [-f]`, `update`,
  `rollback`, `version`, `service install|uninstall`, `uninstall [--purge]`, plus all existing
  CLI groups.
- **Background service:** macOS LaunchAgent (`~/Library/LaunchAgents/ai.foundry-os.plist`,
  `RunAtLoad`, `KeepAlive`), Linux `systemd --user` unit (`foundry-os.service`, `loginctl
  enable-linger` only if the user opts in). Homebrew installs use the formula's `service do`
  block instead (no duplicate agents; `foundryos service install` detects Homebrew and defers
  to `brew services`).
- **Updates:**
  - Homebrew: `brew upgrade foundry-os` (+ `brew services restart`); `foundryos update`
    detects a Homebrew install and prints that command instead of self-updating.
  - Installer installs: `foundryos update [--version X]` downloads the release for the
    platform, verifies SHA-256 against `SHA256SUMS` from the same release (refuses on
    mismatch/missing), unpacks to `~/.foundry-os/versions/<v>/`, atomically switches
    `~/.foundry-os/current`, restarts the service; keeps the previous version for
    `foundryos rollback`. `foundryos` checks for a newer release at most once a day and shows
    a banner in the UI/CLI (opt-out setting; no telemetry beyond the GitHub API call).
  - **Every version change takes an automatic backup** of the data dir before running
    migrations (`<data>/backups/pre-<version>-<ts>.tar.gz`, keep the last 5) and refuses to
    start if a migration fails (rollback instructions printed).
- **Uninstall:** `foundryos uninstall` removes service + binaries, keeps data unless
  `--purge` (typed confirmation). `brew uninstall foundry-os` leaves data (caveats say how to
  remove it).

## 4. `install.sh` (repo root, served from `main`)
POSIX sh, `set -eu`, readable, no `sudo`, idempotent:
1. Detect OS/arch (`darwin|linux`, `arm64|x86_64`); refuse unsupported with a helpful
   message (Windows → Docker/WSL).
2. If Homebrew is present and `FOUNDRYOS_NO_BREW` is unset → offer "use Homebrew (recommended
   for updates)"; non-interactive default = Homebrew. Else continue.
3. Resolve the latest release via the GitHub API (or `FOUNDRYOS_VERSION`), download the
   tarball + `SHA256SUMS`, verify with `shasum -a 256`/`sha256sum`, unpack to
   `~/.foundry-os/versions/<v>`, link `current`, link `~/.local/bin/foundryos` (add to PATH
   in the user's shell rc only with a printed notice; never edit rc files silently twice).
4. `foundryos service install && foundryos` (start + open browser), unless `--no-start`.
5. Print next steps (open URL, `foundryos update`, uninstall).
Flags/env: `--version`, `--no-start`, `--no-service`, `--prefix`, `FOUNDRYOS_NO_BREW=1`.
Tested with shellcheck and in CI on macOS + Linux runners (install from the just-built
artifacts via a local file URL override `FOUNDRYOS_DOWNLOAD_BASE`).

## 5. Homebrew tap (`suriyavasan/homebrew-tap`, `Formula/foundry-os.rb`)
- Binary-tarball formula: `on_macos`/`on_linux` × `on_arm`/`on_intel` → release URLs +
  sha256; `libexec.install Dir["*"]`; `bin.install_symlink libexec/"bin/foundryos"` plus
  `companyos` alias; `service do run [opt_bin/"foundryos", "serve"]; keep_alive true;
  log_path var/"log/foundry-os.log"; error_log_path …; environment_variables …`.
- `caveats`: how to open it, where data lives, `brew services start foundry-os`.
- `test do`: `assert_match version.to_s, shell_output("#{bin}/foundryos --version")`.
- `livecheck` against GitHub releases.
- No `depends_on "ollama"` (optional; onboarding explains). `brew audit --strict --online`
  and `brew install --build-from-source`-free install must pass for the tap.

## 6. Docker image
Multi-arch (`linux/amd64`, `linux/arm64`) from the same bundle (or the existing Dockerfile
adapted), non-root, `/data` volume, `HEALTHCHECK`, published to
`ghcr.io/suriyavasan/foundry-os:<version>` and `:latest` by CI. Built and smoke-tested in CI
(not locally: the dev machine has little disk).

## 7. Release automation (`.github/workflows/`)
- `ci.yml` on PR/push: backend tests (ruff + pytest with `tmp_path_retention_policy =
  "failed"` so test runs don't leave ~1 GB of temp files), web (typecheck/lint/test/build),
  runtime tests, shellcheck `install.sh`.
- `release.yml` on tag `v*`: build 4 bundles → smoke test each → `SHA256SUMS` → GitHub
  Release (notes from `CHANGELOG.md`) → Docker multi-arch push → update the tap formula
  (version, URLs, sha256) and push to `suriyavasan/homebrew-tap` **if** secret
  `HOMEBREW_TAP_TOKEN` exists (fine-grained PAT with contents:write on the tap repo — the
  owner creates it); otherwise the workflow prints the exact formula diff and fails the
  "tap" job with instructions (release still published).
- `scripts/release.sh <version>`: bump `VERSION`, move the Unreleased changelog section,
  commit, tag, push (prints what it will do and asks for confirmation).
- `scripts/build_bundle.sh <platform>`: the same build CI runs, usable locally.

## 8. Local verification before the first release (dev machine: macOS arm64, ~2.6 GB free)
- Build the `darwin-arm64` bundle locally (watch disk; delete build dirs afterwards).
- Install it with `install.sh` from the local file (`FOUNDRYOS_DOWNLOAD_BASE=file://…`) into a
  temp `--prefix` + temp data dir, run `foundryos` (no args) → health → stop → uninstall.
- `brew install` from a local formula pointing at the local tarball (`file://` URL) in a
  temp tap, `foundryos --version`, `brew uninstall`. Never touch the owner's running
  instance (:8700, `data/`).
- Then (owner-approved publication already given): create `suriyavasan/homebrew-tap`, tag
  `v0.1.0`, let CI publish. Report anything that needs the owner (the tap token).
