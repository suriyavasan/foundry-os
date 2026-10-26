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

Requirements: macOS 11+ on Apple Silicon, or Linux (glibc, arm64 or x86_64; not Alpine/musl). **Intel Macs** use the Docker image: key libraries (cryptography, argon2-cffi-bindings, cbor2) no longer publish Intel macOS builds, so there is no native Intel bundle. About
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

**Not verified** (no machine/network for it): Linux bundles and `install.sh` on Linux (CI builds and smoke-tests both); Intel macOS (no bundle; Docker only); the
workflows on GitHub's runners; the Docker image (built only in CI; the local Docker daemon was off); the
formula against the real `https://` release; `brew services start`; launchd/systemd service templates; the
installer's Homebrew path against a real published tap; GHCR push; the end-to-end first-run flow in the
browser from an installed copy.

## For maintainers

### What is where

| Path | Purpose |
|---|---|
| `VERSION`, `CHANGELOG.md` | `VERSION` is the version floor (releases are the highest tag + 1 patch); `CHANGELOG.md` notes, used when a `## [X.Y.Z]` section exists |
| `packaging/versions.env` | pinned inputs: python-build-standalone release + SHA-256 per platform, uv version, runtime extras |
| `scripts/build_bundle.sh <platform>` | builds `foundry-os-<version>-<platform>.tar.gz` (+ `.sha256`); the same script CI runs |
| `scripts/smoke_bundle.sh <tarball> [--installer]` | the smoke test CI runs on every bundle |
| `scripts/release.sh <version>` | raise the minor/major floor (edits `VERSION` + `CHANGELOG.md`, no tag, no push); `--notes <version>` prints the release notes. `scripts/next_version.sh` computes the next automatic version |
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

Releases are automatic: **every green push to `main` is published.** Do nothing special for a normal change.

* **What happens.** When CI finishes green for a push to `main`, `release.yml` picks the version
  (`scripts/next_version.sh`), builds the web UI, one bundle per platform in `PLATFORMS`
  (`packaging/versions.env`: macos-14 arm64, ubuntu-22.04 x86_64, ubuntu-22.04-arm arm64), each installed through
  `install.sh` and smoke-tested, and the Docker image for amd64 and arm64 (its `foundryos --version` must match).
  Only if all of that passed does it publish: `SHA256SUMS`, a draft GitHub Release with every asset (checked
  against what was built), the multi-arch image tag, then the release goes public and is marked latest. Then
  Docker `:latest` / `:X.Y` move and the Homebrew tap is updated. Published assets are never replaced.
* **Version numbers.** `VERSION` is a floor. The release is the highest `vA.B.C` tag with the patch + 1, or
  `VERSION` itself when that is higher (or when there is no tag yet). Nothing is committed back; the tag is created
  when the release is published. `VERSION` must be plain `X.Y.Z` (a `-rc.1` suffix is refused).
* **A new minor or major.** Write the changes under `## [Unreleased]` in `CHANGELOG.md`, run
  `scripts/release.sh 0.2.0`, review `git diff`, then commit and push. It edits `VERSION` and `CHANGELOG.md` only
  (no commit, tag or push) and refuses a version that is not newer than the highest tag. CI then publishes `0.2.0`.
* **Release notes** are the `## [x.y.z]` section of `CHANGELOG.md` when there is one; otherwise one line per
  commit since the previous release, plus a compare link. Preview with `scripts/release.sh --notes 0.2.0`.
* **Skipped (the run stays green and says why):** a commit already contained in the latest tag; a commit message
  with `[skip release]` in its subject line; a push that only changes `docs/`, `site/` or `*.md` files.
* **By hand.** Run the *Release* workflow from main (Actions tab). `sha` picks a commit, `publish` (default off)
  decides whether it publishes; without it everything is built and tested and nothing is published. A failed
  publish leaves at most a draft release, which the next run deletes and recreates.
* **Preview the decision locally:** `scripts/next_version.sh --sha HEAD` (its tests: `sh scripts/tests/next_version_test.sh`).

### Things only the owner can do

* Create the repository `suriyavasan/homebrew-tap` (empty is fine) and let the workflow push to it, with one of:
  * **a deploy key (preferred):** `ssh-keygen -t ed25519 -N '' -f tap_key`, add `tap_key.pub` as a deploy key with
    *write access* on `suriyavasan/homebrew-tap`, and save the private `tap_key` as the secret
    `HOMEBREW_TAP_DEPLOY_KEY` in the `release` environment of `suriyavasan/foundry-os` (Settings > Environments),
    then delete the local key files;
  * **or a fine-grained personal access token** with *Contents: read and write* on that repository only, saved as
    `HOMEBREW_TAP_TOKEN` (same place; a repository secret works too).

  With neither, the `tap` job prints the formula and the exact diff, uploads it as the `foundry-os-formula-tap`
  artifact and fails (the release itself is already published); commit the formula to the tap by hand.
* Optionally protect the `release` environment (required reviewers) if you want a human to approve tap pushes.
* After the first image push, open the package `foundry-os` under the account's Packages on GitHub and set its
  visibility to **public** (GHCR creates new packages private), so `docker pull` works without logging in.
* Keep the repository public (the installer downloads from it without credentials).
* If `main` ever gets branch protection, a release needs no push to it (nothing is committed back), so no bypass
  is required.

### Updating the pins

`packaging/versions.env` holds the python-build-standalone tag and checksums. To move to a newer interpreter,
copy the four `install_only_stripped` lines for that release from its `SHA256SUMS`, rebuild, run the smoke test.
Hermes' dependency pins come from `runtime/uv.lock` and the backend's from `backend/uv.lock`; the bundle
installs exactly what they say, with hashes.
