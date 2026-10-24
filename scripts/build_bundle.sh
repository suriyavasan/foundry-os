#!/bin/sh
# Build a self-contained Foundry AI OS release bundle for the platform this machine runs on.
# CI runs exactly this on each runner (.github/workflows/release.yml); you can run it locally.
#
#   scripts/build_bundle.sh <platform> [options]
#
#   platform           darwin-arm64 | darwin-x86_64 | linux-x86_64 | linux-arm64 (must be this machine's)
#   --out DIR          where the tarball and its .sha256 go (default: <repo>/dist)
#   --web-dist DIR     prebuilt web UI (default: <repo>/web/dist if it exists, else `pnpm build` it)
#   --web-licenses F   `pnpm licenses list --prod --json` output, for THIRD_PARTY_NOTICES
#   --version V        override the version (default: the VERSION file)
#   --work DIR         build directory (default: a fresh temporary directory, deleted afterwards)
#   --keep-work        do not delete the build directory
#   --min-free-mb N    abort when free disk space drops below N MB (default 1024)
#
# Output: <out>/foundry-os-<version>-<platform>.tar.gz and <same>.sha256
#
# What it does (docs/specs/m11-easy-install.md section 2):
#   1. downloads python-build-standalone (pinned in packaging/versions.env, SHA-256 verified) twice:
#      python/backend and python/runtime, so the backend's and the Hermes runtime's dependencies never
#      share a site-packages (the runtime keeps Hermes' exact pins);
#   2. installs each from its frozen lockfile (`uv export --frozen` + `uv pip install --require-hashes
#      --only-binary :all:` straight into the standalone interpreter: no venv, nothing to relocate);
#   3. copies the backend and runtime sources (no tests, no caches), the web UI, the launcher;
#   4. strips tests and caches, pre-compiles bytecode (so the installed bundle is never written to);
#   5. proves it is relocatable: moves the bundle to another directory and runs it from there;
#   6. writes the tarball and its checksum.
set -eu

REPO=$(cd "$(dirname "$0")/.." && pwd)
# shellcheck disable=SC1091
. "$REPO/packaging/versions.env"

PLATFORM=
OUT=$REPO/dist
WEB_DIST=
WEB_LICENSES=
VERSION=$(cat "$REPO/VERSION")
WORK=
KEEP_WORK=0
MIN_FREE_MB=${BUNDLE_MIN_FREE_MB:-1024}
UV=${UV:-uv}

usage() { sed -n '2,/^set -eu/p' "$0" | sed '$d' | sed 's/^# \{0,1\}//'; }
die() { echo "build_bundle: $*" >&2; exit 1; }
log() { printf '==> %s\n' "$*"; }

while [ "$#" -gt 0 ]; do
    case $1 in
        --out) OUT=$2; shift 2 ;;
        --web-dist) WEB_DIST=$2; shift 2 ;;
        --web-licenses) WEB_LICENSES=$2; shift 2 ;;
        --version) VERSION=$2; shift 2 ;;
        --work) WORK=$2; shift 2 ;;
        --keep-work) KEEP_WORK=1; shift ;;
        --min-free-mb) MIN_FREE_MB=$2; shift 2 ;;
        -h | --help) usage; exit 0 ;;
        -*) die "unknown option $1 (see --help)" ;;
        *) [ -z "$PLATFORM" ] || die "one platform only"; PLATFORM=$1; shift ;;
    esac
done
[ -n "$PLATFORM" ] || { usage >&2; exit 2; }

case $PLATFORM in
    darwin-arm64) TRIPLE=aarch64-apple-darwin; WANT_OS=Darwin; WANT_ARCH=arm64 ;;
    darwin-x86_64) TRIPLE=x86_64-apple-darwin; WANT_OS=Darwin; WANT_ARCH=x86_64 ;;
    linux-x86_64) TRIPLE=x86_64-unknown-linux-gnu; WANT_OS=Linux; WANT_ARCH=x86_64 ;;
    linux-arm64) TRIPLE=aarch64-unknown-linux-gnu; WANT_OS=Linux; WANT_ARCH=arm64 ;;
    *) die "unknown platform '$PLATFORM'" ;;
esac
HOST_ARCH=$(uname -m)
[ "$HOST_ARCH" = aarch64 ] && HOST_ARCH=arm64
[ "$(uname -s)" = "$WANT_OS" ] && [ "$HOST_ARCH" = "$WANT_ARCH" ] ||
    die "$PLATFORM bundles must be built on a $PLATFORM machine (this one is $(uname -s)/$(uname -m)): the bundle's interpreters are executed during the build"

command -v "$UV" >/dev/null 2>&1 || die "uv is required to build bundles (https://docs.astral.sh/uv/)"
have_uv=$("$UV" --version | awk '{print $2}')
[ "$have_uv" = "$UV_VERSION" ] || echo "warning: uv $have_uv, the release pipeline pins $UV_VERSION" >&2

# --- scratch space and disk guard -----------------------------------------------------------------------
free_mb() { df -Pk "$1" | awk 'NR==2 {print int($4/1024)}'; }
CLEAN_WORK=0
if [ -z "$WORK" ]; then
    WORK=$(mktemp -d "${TMPDIR:-/tmp}/foundryos-bundle.XXXXXX")
    CLEAN_WORK=1
else
    mkdir -p "$WORK"
fi
[ "$KEEP_WORK" = 1 ] && CLEAN_WORK=0
cleanup() { [ "$CLEAN_WORK" = 1 ] && rm -rf "$WORK"; return 0; }
trap cleanup EXIT INT TERM
check_disk() {
    f=$(free_mb "$WORK")
    if [ "$f" -lt "$MIN_FREE_MB" ]; then
        die "only ${f} MB free on the build volume (minimum ${MIN_FREE_MB} MB): stopping and cleaning up"
    fi
}
check_disk
mkdir -p "$OUT"
OUT=$(cd "$OUT" && pwd)
WORK=$(cd "$WORK" && pwd)

CACHE=${BUNDLE_CACHE_DIR:-$WORK/cache}
STAGE=$WORK/build/foundry-os
mkdir -p "$CACHE" "$WORK/build"
NAME=foundry-os-$VERSION-$PLATFORM

# --- 1. interpreters -------------------------------------------------------------------------------------
sha256_of() {
    if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | awk '{print $1}'; else shasum -a 256 "$1" | awk '{print $1}'; fi
}
PBS_FILE=cpython-$PBS_PYTHON_VERSION+$PBS_TAG-$TRIPLE-install_only_stripped.tar.gz
PBS_URL=https://github.com/astral-sh/python-build-standalone/releases/download/$PBS_TAG/$(printf '%s' "$PBS_FILE" | sed 's/+/%2B/')
var=PBS_SHA256_$(printf '%s' "$TRIPLE" | tr '-' '_')
eval "PBS_SHA256=\${$var:-}"
[ -n "$PBS_SHA256" ] || die "no pinned SHA-256 for $TRIPLE in packaging/versions.env"

log "python-build-standalone $PBS_PYTHON_VERSION ($PBS_TAG, $TRIPLE)"
if [ ! -f "$CACHE/$PBS_FILE" ]; then
    curl -fsSL --retry 3 -o "$CACHE/$PBS_FILE.part" "$PBS_URL"
    mv "$CACHE/$PBS_FILE.part" "$CACHE/$PBS_FILE"
fi
[ "$(sha256_of "$CACHE/$PBS_FILE")" = "$PBS_SHA256" ] ||
    { rm -f "$CACHE/$PBS_FILE"; die "SHA-256 mismatch for $PBS_FILE (expected $PBS_SHA256)"; }

mkdir -p "$STAGE/python" "$STAGE/app"
for which in backend runtime; do
    rm -rf "$WORK/py-extract"; mkdir -p "$WORK/py-extract"
    tar -xzf "$CACHE/$PBS_FILE" -C "$WORK/py-extract"
    mv "$WORK/py-extract/python" "$STAGE/python/$which"
done
rm -rf "$WORK/py-extract"
check_disk

py_of() { printf '%s/python/%s/bin/python3' "$STAGE" "$1"; }
site_of() { "$(py_of "$1")" -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])'; }

# --- 2. dependencies from the frozen lockfiles ----------------------------------------------------------------
install_deps() { # which reqs-file
    log "installing $1 dependencies ($(grep -c '==' "$2") pinned requirements, hashes enforced)"
    (cd "$WORK" && UV_NO_CONFIG=1 UV_PYTHON_DOWNLOADS=never "$UV" pip install \
        --python "$(py_of "$1")" --break-system-packages --no-cache --link-mode copy \
        --require-hashes --no-deps --only-binary :all: --quiet -r "$2")
    # --no-deps: the lockfile is already fully resolved (and uv export does not carry the runtime's
    # [tool.uv] override-dependencies, so re-resolving would reject Hermes' own pins)
    (cd "$WORK" && UV_NO_CONFIG=1 "$UV" pip check --python "$(py_of "$1")") ||
        echo "    note: 'uv pip check' reports the problems above for the $1 environment (informational)" >&2
}
(cd "$REPO/backend" && "$UV" export --frozen --no-dev --no-emit-project --no-header --no-annotate \
    -o "$WORK/backend-requirements.txt" >/dev/null)
extras=
for e in $RUNTIME_EXTRAS; do extras="$extras --extra $e"; done
# shellcheck disable=SC2086
(cd "$REPO/runtime" && "$UV" export --frozen --no-dev --no-emit-project --no-header --no-annotate \
    $extras -o "$WORK/runtime-requirements.txt" >/dev/null)
install_deps backend "$WORK/backend-requirements.txt"
check_disk
install_deps runtime "$WORK/runtime-requirements.txt"
check_disk

# --- 3. sources, web UI, launcher ---------------------------------------------------------------------------------
log "copying sources"
EXCL="--exclude=__pycache__ --exclude=*.pyc --exclude=tests --exclude=.pytest_cache --exclude=.ruff_cache --exclude=.mypy_cache --exclude=.venv --exclude=.git --exclude=.DS_Store --exclude=*.egg-info"
mkdir -p "$STAGE/app/backend" "$STAGE/app/runtime" "$STAGE/web"
# shellcheck disable=SC2086
rsync -a $EXCL "$REPO/backend/app" "$REPO/backend/alembic" "$REPO/backend/alembic.ini" "$STAGE/app/backend/"
# shellcheck disable=SC2086
rsync -a $EXCL --exclude=/docs "$REPO/runtime/" "$STAGE/app/runtime/"

if [ -z "$WEB_DIST" ]; then
    if [ -f "$REPO/web/dist/index.html" ]; then
        WEB_DIST=$REPO/web/dist
    else
        log "building the web UI (pnpm)"
        (cd "$REPO/web" && pnpm install --frozen-lockfile && pnpm build)
        WEB_DIST=$REPO/web/dist
    fi
fi
[ -f "$WEB_DIST/index.html" ] || die "$WEB_DIST has no index.html"
log "web UI from $WEB_DIST"
cp -R "$WEB_DIST" "$STAGE/web/dist"

mkdir -p "$STAGE/bin"
cp "$REPO/packaging/bin/foundryos" "$STAGE/bin/foundryos"
chmod 755 "$STAGE/bin/foundryos"
ln -s foundryos "$STAGE/bin/companyos"
printf '%s\n' "$VERSION" > "$STAGE/VERSION"
cp "$REPO/LICENSE" "$STAGE/LICENSE"
commit=$(git -C "$REPO" rev-parse HEAD 2>/dev/null || echo unknown)
dirty=$([ -z "$(git -C "$REPO" status --porcelain 2>/dev/null)" ] || echo "+uncommitted changes")
cat > "$STAGE/BUILD_INFO" <<EOF
version: $VERSION
platform: $PLATFORM
commit: $commit$dirty
python: $PBS_PYTHON_VERSION (python-build-standalone $PBS_TAG)
built-with: uv $have_uv
EOF

# --- 4. relocatable wiring, stripping, bytecode ---------------------------------------------------------------
for which in backend runtime; do
    SITE=$(site_of "$which")
    # app sources are found through a .pth with a RELATIVE entry (resolved against site-packages): no
    # PYTHONPATH needed, and the bundle can move
    rel=../../../../..
    [ "$(cd "$SITE/$rel" && pwd -P)" = "$(cd "$STAGE" && pwd -P)" ] || die "unexpected site-packages depth: $SITE"
    printf '%s/app/%s\n' "$rel" "$which" > "$SITE/foundryos-app.pth"

    # console scripts installed by pip carry the absolute build path in their shebang: drop them (the
    # launcher runs `python -m ...`) together with everything else in bin/ except the interpreter
    for f in "$STAGE/python/$which/bin"/*; do
        case $(basename "$f") in python | python3 | python3.*[0-9]) ;; *) rm -f "$f" ;; esac
    done
done

log "stripping tests, caches, pip and unused stdlib parts (the bundle is immutable)"
before=$(du -sk "$STAGE" | awk '{print $1}')
for which in backend runtime; do
    P=$STAGE/python/$which
    LIB=$(echo "$P"/lib/python3.*)
    rm -rf "$LIB/test" "$LIB/idlelib" "$LIB/turtledemo" "$LIB/tkinter" "$LIB/ensurepip" \
        "$LIB/pydoc_data" "$LIB"/config-3.* "$LIB"/site-packages/pip "$LIB"/site-packages/pip-*.dist-info \
        "$P"/lib/tcl* "$P"/lib/tk* "$P"/lib/itcl* "$P"/lib/thread* "$P"/share "$P"/include
    rm -f "$LIB"/lib-dynload/_tkinter*
    # test suites of installed packages (never imported at run time)
    find "$LIB/site-packages" -type d \( -name tests -o -name test \) -prune -exec rm -rf {} +
done
find "$STAGE" -name __pycache__ -type d -prune -exec rm -rf {} +
find "$STAGE" -name '*.pyc' -type f -delete
after=$(du -sk "$STAGE" | awk '{print $1}')
echo "    stripped $(( (before - after) / 1024 )) MB"

log "pre-compiling bytecode (unchecked hashes: the bundle is read-only at run time)"
compile_tree() { # which dirs...
    which=$1; shift
    "$(py_of "$which")" -m compileall -q -j 0 --invalidation-mode unchecked-hash "$@" >/dev/null 2>&1 || true
}
compile_tree backend "$STAGE/python/backend/lib" "$STAGE/app/backend"
compile_tree runtime "$STAGE/python/runtime/lib" "$STAGE/app/runtime"
check_disk

# --- notices -------------------------------------------------------------------------------------------------------------
log "THIRD_PARTY_NOTICES"
wl=
[ -n "$WEB_LICENSES" ] && wl="--web-licenses $WEB_LICENSES"
# shellcheck disable=SC2086
"$(py_of backend)" -I "$REPO/packaging/gen_notices.py" --out "$STAGE/THIRD_PARTY_NOTICES" \
    --site-packages "$(site_of backend)" --site-packages "$(site_of runtime)" \
    --hermes-license "$REPO/runtime/LICENSE" --python-version "$PBS_PYTHON_VERSION" \
    --pbs-tag "$PBS_TAG" $wl

# --- 5. relocatability proof -----------------------------------------------------------------------------------------------
chmod -R go-w "$STAGE"
log "checking that no build path leaked into the bundle"
leaks=$(grep -rIl -- "$WORK" "$STAGE" 2>/dev/null || true)
if [ -n "$leaks" ]; then
    echo "$leaks" | head -20 >&2
    die "the build directory ($WORK) is referenced by the files above: the bundle would not survive a move"
fi

log "moving the bundle to another directory and running it from there"
mkdir -p "$WORK/moved"
mv "$STAGE" "$WORK/moved/foundry-os"
STAGE=$WORK/moved/foundry-os
# a different working directory with a decoy `app` package: the bundle must not pick it up
mkdir -p "$WORK/decoy/app" && echo 'raise SystemExit("decoy app package imported")' > "$WORK/decoy/app/__init__.py"
got=$(cd "$WORK/decoy" && "$STAGE/bin/foundryos" --version)
[ "$got" = "foundryos $VERSION" ] || die "moved bundle: unexpected --version output '$got'"
touch "$WORK/marker"
sleep 1
probe() { # which, python code: run with a scratch data dir so nothing lands inside the bundle
    (cd "$WORK/decoy" && env PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1 \
        COMPANY_OS_DATA_DIR="$WORK/probe-data" COMPANY_OS_DATABASE_URL="sqlite:///$WORK/probe-data/probe.db" \
        "$STAGE/python/$1/bin/python3" -P -c "$2")
}
probe backend 'import app.main, app.cli.main, fastapi, uvicorn, sqlalchemy, alembic; print("backend imports ok from", app.main.__file__)'
probe runtime 'import openai, anthropic, ddgs, pydantic; print("runtime deps ok, pydantic", pydantic.VERSION)'
(cd "$STAGE/app/runtime" && env PYTHONDONTWRITEBYTECODE=1 "$STAGE/python/runtime/bin/python3" -P -c \
    'import company_agent.run; print("runtime sources ok from", company_agent.run.__file__)')
probe runtime 'import company_agent.run; print("runtime sources reachable from any directory (.pth)")'
if find "$STAGE" -newer "$WORK/marker" -type f 2>/dev/null | grep -q .; then
    echo "files were created inside the bundle while testing it:" >&2
    find "$STAGE" -newer "$WORK/marker" -type f | head >&2
    die "the bundle was written to during the relocation check"
fi

# --- 6. archive ---------------------------------------------------------------------------------------------------------------
log "sizes (uncompressed)"
for part in python/backend python/runtime app/backend app/runtime web/dist; do
    printf '    %-16s %6s MB\n' "$part" "$(( $(du -sk "$STAGE/$part" | awk '{print $1}') / 1024 ))"
done
printf '    %-16s %6s MB\n' total "$(( $(du -sk "$STAGE" | awk '{print $1}') / 1024 ))"

TARBALL=$OUT/$NAME.tar.gz
log "writing $TARBALL"
rm -f "$TARBALL" "$TARBALL.sha256"
(cd "$WORK/moved" && COPYFILE_DISABLE=1 tar -cf - foundry-os) | gzip -9 -n > "$TARBALL.part"
mv "$TARBALL.part" "$TARBALL"
printf '%s  %s\n' "$(sha256_of "$TARBALL")" "$NAME.tar.gz" > "$TARBALL.sha256"
size_bytes=$(wc -c < "$TARBALL" | tr -d ' ')
printf '    %s  (%s MB compressed)\n' "$(cat "$TARBALL.sha256")" "$(( size_bytes / 1048576 ))"
log "done"
