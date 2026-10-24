# Installing Foundry AI OS

Four ways, easiest first. All of them give you the same product; they differ in how updates reach you.

| | Command | Best for | Updates |
|---|---|---|---|
| **Homebrew** (macOS, Linux) | `brew install suriyavasan/tap/foundry-os` | most people on a Mac | `brew upgrade foundry-os` |
| **Installer** (macOS, Linux) | `curl -fsSL https://raw.githubusercontent.com/suriyavasan/foundry-os/main/install.sh \| sh` | no Homebrew, servers | run the installer again |
| **Docker** (any OS, incl. Windows) | `docker run -d --name foundry-os -p 127.0.0.1:8700:8700 -v foundry-os:/data ghcr.io/suriyavasan/foundry-os:latest` | Windows, isolation, servers | `docker pull` + recreate |
| **From source** | [docs/deploy.md](deploy.md) | development | `git pull` |

Windows has no native build in this version: use Docker, or WSL with the installer.

> **Status (read this).** The pipeline described here is written and was exercised locally on one platform
> (macOS arm64); see [What was verified](#what-was-verified). Nothing has been published yet: until the first
> release (`v0.1.0`) exists, the Homebrew tap and the Docker image do not exist either.

## What you get

A release bundle is self-contained: its own Python 3.14 interpreters (one for the control plane, one for the
agent runtime, so their dependency pins never mix), all dependencies from the frozen lockfiles, the agent
runtime and the prebuilt web UI. **You do not need Python, Node, uv, pnpm or git.** What you still need is a
model: a local [Ollama](https://ollama.com) or an API key for a cloud provider (the onboarding screen walks
you through it).

Requirements: macOS 11+ on Apple Silicon or Intel, or Linux (glibc, arm64 or x86_64; not Alpine/musl). About
600 MB of disk for the install, plus your data.

## Homebrew

```sh
brew install suriyavasan/tap/foundry-os
foundryos                          # starts Foundry AI OS and opens the browser
brew services start foundry-os     # optional: keep it running in the background, start at login
brew upgrade foundry-os            # update
brew uninstall foundry-os          # remove (your data stays)
```

Homebrew downloads the bundle for your platform, verifies its SHA-256 from the formula, and unpacks it into the
Cellar. The formula keeps the bundle as a tarball until `post_install` because Homebrew would otherwise try to
rewrite the install names of the bundle's prebuilt Python extension modules and report a failed install (they have
no header room for it). Recent Homebrew versions have a tap-trust mechanism (the local test tap was trusted
automatically when installed by name); how it behaves for the real `suriyavasan/tap` has not been observed yet.

## The installer

```sh
curl -fsSL https://raw.githubusercontent.com/suriyavasan/foundry-os/main/install.sh | sh
# options go after `sh -s --`:
curl -fsSL https://raw.githubusercontent.com/suriyavasan/foundry-os/main/install.sh | sh -s -- --no-start
```

It never uses `sudo` and writes only to your home directory:

1. detects OS and CPU (Apple Silicon under Rosetta gets the native build; Windows and Alpine are refused with a
   pointer to Docker);
2. if Homebrew is installed, uses it (non-interactive default; interactively it asks). It falls back to the
   direct download if the Homebrew install fails. Not used with `--no-brew`, `--version`, `--prefix` or a custom
   download base;
3. otherwise downloads `foundry-os-<version>-<platform>.tar.gz` and `SHA256SUMS` from the GitHub Release,
   **verifies the SHA-256 and stops on any mismatch or missing entry**, unpacks to
   `~/.foundry-os/versions/<version>`, points `~/.foundry-os/current` at it and links `~/.local/bin/foundryos`
   (plus the `companyos` alias). If `~/.local/bin` is not on your `PATH` it adds one clearly marked block to your
   shell startup file (once; `--no-modify-path` to skip it);
4. starts it unless `--no-start` (see the note on the service below).

Running it again is safe: an installed version is not downloaded twice and the links are refreshed. The previous
version stays in `~/.foundry-os/versions/` so you can switch back by pointing `current` at it.

| Option | Environment | Meaning |
|---|---|---|
| `--version X.Y.Z` | `FOUNDRYOS_VERSION` | install this release instead of the latest |
| `--prefix DIR` | `FOUNDRYOS_PREFIX` | install root (default `~/.foundry-os`); links go to `DIR/bin` |
| `--bin-dir DIR` | `FOUNDRYOS_BIN_DIR` | where the `foundryos` link goes (default `~/.local/bin`) |
| `--no-start` | `FOUNDRYOS_NO_START=1` | install only |
| `--no-service` | `FOUNDRYOS_NO_SERVICE=1` | do not install the login service |
| `--no-modify-path` | `FOUNDRYOS_NO_MODIFY_PATH=1` | never touch your shell startup file |
| `--no-brew` | `FOUNDRYOS_NO_BREW=1` | do not use Homebrew even if present |
| `-y`, `--yes` | | answer yes to the Homebrew question |
| `--uninstall` | | remove the installed versions and links; **your data is kept** |
| | `FOUNDRYOS_DOWNLOAD_BASE` | download from here instead of the GitHub Release (`https://...` or `file:///dir`; used by the tests) |
| | `FOUNDRYOS_REPO` | GitHub repository (default `suriyavasan/foundry-os`) |

To read the script before running it: it is `install.sh` at the root of this repository, plain POSIX `sh`.

### Starting, the service and updates (installed-mode CLI)

The launcher (`bin/foundryos`) finds its bundle, sets the data directory and the paths of the bundled
interpreters, runtime and web UI, marks the copy as installed (`FOUNDRYOS_INSTALLED=1`) and runs the CLI:

- `foundryos` starts Foundry AI OS in the background (as your user service when one is installed) and opens
  the browser; if it is already running it just opens the browser;
- `foundryos start | stop | restart | open | logs [-f]` control it; `foundryos serve` runs it in the
  foreground without a browser (what the Homebrew / launchd / systemd service runs);
- `foundryos service install | uninstall | status` sets up the login service (macOS LaunchAgent, Linux
  `systemd --user`; Homebrew installs use `brew services` instead);
- `foundryos update [--check]` / `foundryos rollback` update installer installs (checksums verified, the
  previous version kept); Homebrew installs print `brew upgrade foundry-os`;
- every start after a version change first writes a backup to `<data>/backups/` and refuses to start if a
  migration fails;
- `foundryos uninstall [--purge]` removes the program (and, with `--purge` and a typed confirmation, your
  data). Details: [guide/cli.md](guide/cli.md), [guide/updates-backups.md](guide/updates-backups.md).

`install.sh` runs `foundryos service install` and starts the app at the end of an install.

## Docker

```sh
docker run -d --name foundry-os -p 127.0.0.1:8700:8700 -v foundry-os:/data ghcr.io/suriyavasan/foundry-os:latest
docker exec foundry-os foundryos auth setup-token      # first run from a browser on another device only
open http://localhost:8700
```

* `-p 127.0.0.1:8700:8700` publishes the port on this machine only. Leave `127.0.0.1:` out only after you have
  read [deploy-reachability.md](deploy-reachability.md): the first account is created over HTTP and anyone who
  can reach the port while setup is pending can race you for it (from outside the container a setup token is
  required, which is why the command above exists).
* One container is a complete install: the default command applies database migrations and runs the API with the
  worker embedded. Data (database, secrets, artifacts) is in the `/data` volume; the process runs as uid 10001.
* Multi-arch: `linux/amd64` and `linux/arm64`. Tags: `X.Y.Z`, `X.Y`, `latest`.
* `docker compose up -d --build` (the repository's `docker-compose.yml`) runs the same image as three services
  (migrate, api, worker) plus an optional PostgreSQL profile; see [deploy.md](deploy.md).
* To reach a model on the host (Ollama), register `http://host.docker.internal:11434` as the provider URL.

## Where things live

| | macOS | Linux |
|---|---|---|
| Your data (database, secrets, artifacts, logs) | `~/Library/Application Support/Foundry AI OS` | `${XDG_DATA_HOME:-~/.local/share}/foundry-os` |
| Installer install | `~/.foundry-os/` (links in `~/.local/bin`) | same |
| Homebrew install | `$(brew --prefix)/Cellar/foundry-os/<v>/libexec` | `.../Cellar/foundry-os/<v>/libexec` |
| Docker | the `/data` volume | the `/data` volume |

`FOUNDRYOS_DATA_DIR` moves the data directory, `FOUNDRYOS_PORT` the port (default 8700). The launcher also honours
any `COMPANY_OS_*` variable you set yourself. An install never writes inside the bundle itself.

Uninstalling (`brew uninstall`, `install.sh --uninstall`) leaves your data in place. To start from scratch delete
the data directory too.

## What was verified

On macOS 26 / Apple Silicon only, with a bundle built on that machine and a local `file://` download base:

| | Result |
|---|---|
| Bundle build (`scripts/build_bundle.sh darwin-arm64`) | built; ~177 MB compressed, ~550 MB unpacked |
| Moved to another directory and run from there (decoy `app/` package in the working directory) | passed |
| Read-only bundle: `--version`, `doctor --json`, runtime import check, start, `/api/health`, UI on the same port, SIGTERM stop, nothing left running, nothing written inside the bundle | passed (also through `install.sh`) |
| `install.sh`: checksum mismatch and missing `SHA256SUMS` entry refused; install; re-run is idempotent; PATH block added once; `--uninstall`; run through `dash` with the script piped in on stdin; the Homebrew branches against a stub `brew` | passed |
| Homebrew formula in a throw-away local tap: `brew style`, `brew audit --strict` (offline: URLs are `file://`), `brew install` (clean, no linkage failure), `foundryos --version`, `brew test`, `serve` + `/api/health`, `brew uninstall` | passed |
| `shellcheck -x` on all scripts, `actionlint` on both workflows, `docker compose config` on the compose files | clean |

**Not verified** (no machine/network for it): Linux bundles and `install.sh` on Linux; Intel macOS; the
workflows on GitHub's runners; the Docker image (built only in CI; the local Docker daemon was off); the
formula against the real `https://` release; `brew services start`; launchd/systemd service templates; the
installer's Homebrew path against a real published tap; GHCR push; the end-to-end first-run flow in the
browser from an installed copy.

## For maintainers

### What is where

| Path | Purpose |
|---|---|
| `VERSION`, `CHANGELOG.md` | the version of record; `scripts/release.sh` moves "Unreleased" under the new version |
| `packaging/versions.env` | pinned inputs: python-build-standalone release + SHA-256 per platform, uv version, runtime extras |
| `scripts/build_bundle.sh <platform>` | builds `foundry-os-<version>-<platform>.tar.gz` (+ `.sha256`); the same script CI runs |
| `scripts/smoke_bundle.sh <tarball> [--installer]` | the smoke test CI runs on every bundle |
| `scripts/release.sh <version>` | bump, tag, push (asks first); `--notes <version>` prints the release notes |
| `packaging/bin/foundryos` | the launcher shipped in every bundle |
| `packaging/homebrew/` | `foundry-os.rb.tmpl` and `render_formula.sh <release-dir>` |
| `packaging/gen_notices.py` | generates `THIRD_PARTY_NOTICES` for a bundle |
| `packaging/sums.sh <dir>` | writes `SHA256SUMS` |
| `Dockerfile`, `packaging/docker/entrypoint.sh`, `docker-compose*.yml` | the image |
| `.github/workflows/ci.yml`, `release.yml` | CI and the release pipeline |

### Build a bundle locally

```sh
cd web && pnpm install --frozen-lockfile && pnpm build && cd ..     # or pass --web-dist
scripts/build_bundle.sh darwin-arm64 --out dist                      # must match your machine
scripts/smoke_bundle.sh dist/foundry-os-*-darwin-arm64.tar.gz --installer
```

It needs `uv`, `rsync`, `curl` and about 1.5 GB of free disk; it refuses to continue below
`--min-free-mb` (default 1024) and deletes its work directory when done. The bundle is built by installing the
frozen lockfiles (`uv export --frozen` with hashes, `uv pip install --require-hashes --only-binary :all:
--no-deps`) straight into the python-build-standalone interpreters, so there is no virtualenv to relocate; the
script proves relocation by moving the finished bundle and running it. Hermes' exact pins are installed as
locked; `uv pip check` reports one conflict in the runtime environment (`browser-harness` wants `pillow==12.3.0`,
the lock's own override installs `13.0.0`), which is the lockfile's decision and is printed, not hidden.

### Cut a release

1. Write the changes under `## [Unreleased]` in `CHANGELOG.md`.
2. `scripts/release.sh 0.2.0` (the very first release can be `0.1.0`: `VERSION` already says so).
3. The tag push runs `release.yml`: web build, four bundles (macos-14 arm64, macos-15-intel x86_64, ubuntu-22.04
   x86_64, ubuntu-22.04-arm arm64) each installed through `install.sh` and smoke-tested, `SHA256SUMS`, the GitHub
   Release with the changelog notes (`install.sh` is attached too), the multi-arch image on GHCR, and the tap.
   `macos-13`, named in the original plan, no longer exists on GitHub Actions; `macos-15-intel` replaces it.
4. Running the workflow by hand (`workflow_dispatch`) builds and tests everything and publishes nothing.

### Things only the owner can do

* Create the repository `suriyavasan/homebrew-tap` (empty is fine), and a **fine-grained personal access token**
  with *Contents: read and write* on that repository only; save it as the secret `HOMEBREW_TAP_TOKEN` of
  `suriyavasan/foundry-os`. Without it the `tap` job prints the formula and the exact diff, uploads it as the
  `foundry-os-formula` artifact and fails (the release itself is still published); commit it to the tap by hand.
* After the first image push, open the package `foundry-os` under the account's Packages on GitHub and set its
  visibility to **public** (GHCR creates new packages private), so `docker pull` works without logging in.
* Make the repository public before the first release (the installer downloads from it without credentials).

### Updating the pins

`packaging/versions.env` holds the python-build-standalone tag and checksums. To move to a newer interpreter,
copy the four `install_only_stripped` lines for that release from its `SHA256SUMS`, rebuild, run the smoke test.
Hermes' dependency pins come from `runtime/uv.lock` and the backend's from `backend/uv.lock`; the bundle
installs exactly what they say, with hashes.
