#!/bin/sh
# Smoke-test a release bundle the way a user meets it: unpack it somewhere new, make it READ-ONLY,
# and run it.
#
#   scripts/smoke_bundle.sh <foundry-os-VERSION-PLATFORM.tar.gz> [--port N] [--keep] [--installer]
#
#   --installer   unpack it with install.sh (from a file:// download base, into a temporary --prefix) instead
#                 of tar, so the installer and the bundle are tested together
#
# Checks (any failure exits non-zero with the server log):
#   - the tarball matches its .sha256 (when present); with --installer, install.sh verifies SHA256SUMS
#   - `foundryos --version` prints the bundle version
#   - `foundryos doctor --json` runs and prints valid JSON
#   - the agent runtime's own import check passes with the bundled runtime interpreter
#   - `foundryos serve --port N` starts (migrates a fresh database in a data dir whose name contains
#     a space), /api/health answers, the web UI is served on the same port
#   - SIGTERM stops it and frees the port, and nothing is left running
#   - nothing was written inside the bundle (it is read-only during the whole test)
#
# The default port is a free one picked at random; the real instance on :8700 is never touched.
set -eu

die() { echo "smoke_bundle: FAIL: $*" >&2; exit 1; }
log() { printf '==> %s\n' "$*"; }

TARBALL=
PORT=
KEEP=0
INSTALLER=0
REPO=$(cd "$(dirname "$0")/.." && pwd)
while [ "$#" -gt 0 ]; do
    case $1 in
        --port) PORT=$2; shift 2 ;;
        --keep) KEEP=1; shift ;;
        --installer) INSTALLER=1; shift ;;
        -h | --help) sed -n '2,/^set -eu/p' "$0" | sed '$d' | sed 's/^# \{0,1\}//'; exit 0 ;;
        -*) die "unknown option $1" ;;
        *) TARBALL=$1; shift ;;
    esac
done
[ -f "${TARBALL:-}" ] || die "usage: smoke_bundle.sh <bundle.tar.gz> [--port N] [--keep]"
TARBALL=$(cd "$(dirname "$TARBALL")" && pwd)/$(basename "$TARBALL")
[ -n "$PORT" ] && case $PORT in 8700 | 5173) die "refusing port $PORT (reserved for a real install)" ;; esac

T=$(mktemp -d "${TMPDIR:-/tmp}/foundryos-smoke.XXXXXX")
UP_PID=
cleanup() {
    if [ -n "$UP_PID" ] && kill -0 "$UP_PID" 2>/dev/null; then
        kill -TERM "$UP_PID" 2>/dev/null || true
        i=0
        while kill -0 "$UP_PID" 2>/dev/null && [ "$i" -lt 30 ]; do sleep 1; i=$((i + 1)); done
        kill -KILL "$UP_PID" 2>/dev/null || true
    fi
    if [ "$KEEP" = 1 ]; then echo "kept $T"; return 0; fi
    chmod -R u+w "$T" 2>/dev/null || true
    rm -rf "$T"
}
trap cleanup EXIT INT TERM

# --- checksum ----------------------------------------------------------------------------------------------
if [ -f "$TARBALL.sha256" ]; then
    want=$(awk '{print $1}' "$TARBALL.sha256")
    if command -v sha256sum >/dev/null 2>&1; then got=$(sha256sum "$TARBALL" | awk '{print $1}'); else got=$(shasum -a 256 "$TARBALL" | awk '{print $1}'); fi
    [ "$want" = "$got" ] || die "checksum mismatch for $TARBALL"
    log "checksum ok"
fi

# --- unpack, read-only -----------------------------------------------------------------------------------
export HOME="$T/home"
mkdir -p "$HOME"
if [ "$INSTALLER" = 1 ]; then
    log "installing with install.sh (file:// download base, SHA256SUMS verified)"
    mkdir -p "$T/release"
    cp "$TARBALL" "$T/release/"
    "$REPO/packaging/sums.sh" "$T/release" >/dev/null
    name=$(basename "$TARBALL" .tar.gz)
    name=${name#foundry-os-}
    for plat in darwin-arm64 darwin-x86_64 linux-arm64 linux-x86_64; do name=${name%-"$plat"}; done
    FOUNDRYOS_DOWNLOAD_BASE="file://$T/release" sh "$REPO/install.sh" --prefix "$T/prefix" --version "$name" --no-start > "$T/install.log" 2>&1 ||
        { cat "$T/install.log" >&2; die "install.sh failed"; }
    B=$T/prefix/versions/$name
    FOUNDRYOS=$T/prefix/bin/foundryos
    COMPANYOS=$T/prefix/bin/companyos
    [ -L "$FOUNDRYOS" ] || die "install.sh did not link $FOUNDRYOS"
else
    mkdir -p "$T/unpack"
    tar -xzf "$TARBALL" -C "$T/unpack"
    B=$T/unpack/foundry-os
    FOUNDRYOS=$B/bin/foundryos
    COMPANYOS=$B/bin/companyos
fi
[ -x "$B/bin/foundryos" ] || die "no bin/foundryos in the bundle"
WANT_VERSION=$(cat "$B/VERSION")
find "$B" -name '._*' | grep -q . && die "AppleDouble (._*) files in the tarball"
chmod -R a-w "$B"
touch "$T/marker"
sleep 1

export FOUNDRYOS_DATA_DIR="$T/data dir"
export COMPANYOS_CONFIG_DIR="$T/config"
unset COMPANY_OS_DATABASE_URL COMPANY_OS_DATA_DIR COMPANY_OS_PORT FOUNDRYOS_PORT PYTHONPATH PYTHONHOME

log "foundryos --version"
v=$("$FOUNDRYOS" --version)
[ "$v" = "foundryos $WANT_VERSION" ] || die "--version printed '$v', expected 'foundryos $WANT_VERSION'"
echo "    $v"
c=$("$COMPANYOS" --version)
[ "$c" = "$v" ] || die "the companyos alias printed '$c'"

log "foundryos doctor --json"
set +e
"$FOUNDRYOS" doctor --json > "$T/doctor.json" 2> "$T/doctor.err"
rc=$?
set -e
[ "$rc" -le 1 ] || { cat "$T/doctor.err" >&2; die "doctor exited $rc"; }
"$B/python/backend/bin/python3" -I -c 'import json,sys; d=json.load(open(sys.argv[1])); print("    doctor: %d checks, ok=%s" % (len(d["checks"]), d["ok"]))' "$T/doctor.json" ||
    die "doctor did not print valid JSON"
"$B/python/backend/bin/python3" -I -c '
import json,sys
for c in json.load(open(sys.argv[1]))["checks"]:
    if c["name"] == "agent runtime" and c["status"] != "ok":
        sys.exit("agent runtime check: %s" % c["detail"])' "$T/doctor.json" || die "doctor reports the agent runtime as broken"

log "agent runtime import check (bundled runtime interpreter)"
(cd "$B/app/runtime" && PYTHONDONTWRITEBYTECODE=1 "$B/python/runtime/bin/python3" -P scripts/check_imports.py > "$T/runtime-check.log" 2>&1) ||
    { tail -20 "$T/runtime-check.log" >&2; die "runtime import check failed"; }
tail -1 "$T/runtime-check.log"

# --- start, health, UI, stop ---------------------------------------------------------------------------
if [ -z "$PORT" ]; then
    PORT=$("$B/python/backend/bin/python3" -I -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1])')
fi
log "foundryos serve --port $PORT (data dir: $FOUNDRYOS_DATA_DIR)"
"$FOUNDRYOS" serve --port "$PORT" > "$T/server.log" 2>&1 &
UP_PID=$!

fetch() { curl -fsS --max-time 5 "$1"; }
i=0
until fetch "http://127.0.0.1:$PORT/api/health" > "$T/health.json" 2>/dev/null; do
    i=$((i + 1))
    kill -0 "$UP_PID" 2>/dev/null || { cat "$T/server.log" >&2; die "the server exited before becoming healthy"; }
    [ "$i" -lt 120 ] || { tail -40 "$T/server.log" >&2; die "no healthy /api/health within 120 s"; }
    sleep 1
done
echo "    /api/health after ${i}s: $(cat "$T/health.json")"
fetch "http://127.0.0.1:$PORT/" > "$T/index.html" || die "GET / failed"
grep -qi '<div id="root"' "$T/index.html" || grep -qi '<!doctype html' "$T/index.html" || die "GET / did not return the web UI"
echo "    web UI served on the same port"
[ -f "$FOUNDRYOS_DATA_DIR/company_os.db" ] || die "no database in the data dir"

log "stopping (SIGTERM)"
kill -TERM "$UP_PID"
i=0
while kill -0 "$UP_PID" 2>/dev/null; do
    i=$((i + 1))
    [ "$i" -lt 60 ] || die "the server did not stop within 60 s"
    sleep 1
done
wait "$UP_PID" 2>/dev/null || true
UP_PID=
if curl -fsS --max-time 2 "http://127.0.0.1:$PORT/api/health" >/dev/null 2>&1; then die "port $PORT still answers after stop"; fi
if pgrep -f "$B/python/" >/dev/null 2>&1; then
    pgrep -fl "$B/python/" >&2 || true
    die "processes of the bundle are still running"
fi
echo "    stopped cleanly, nothing left running"

log "nothing was written inside the bundle"
if find "$B" -newer "$T/marker" 2>/dev/null | grep -q .; then
    find "$B" -newer "$T/marker" | head >&2
    die "files were created or changed inside the (read-only) bundle"
fi
echo "    ok"
log "PASS $(basename "$TARBALL")"
