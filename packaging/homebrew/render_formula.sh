#!/bin/sh
# Render the Homebrew formula from the template and a release's checksums.
#
#   packaging/homebrew/render_formula.sh <release-dir> [options]
#
#   <release-dir>        a directory holding SHA256SUMS (as attached to the GitHub Release), or the
#                        foundry-os-*.tar.gz.sha256 files that scripts/build_bundle.sh writes
#   --version V          default: the VERSION file at the repository root
#   --base-url URL       where the tarballs are downloaded from
#                        (default https://github.com/suriyavasan/foundry-os/releases/download/v<V>);
#                        file:///some/dir works for a local test tap
#   --allow-missing      a platform without a checksum gets an all-zero SHA-256 (local test taps
#                        built for one platform only; never for a published formula)
#   -o FILE              write to FILE instead of stdout
set -eu

HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/../.." && pwd)

die() { echo "render_formula: $*" >&2; exit 1; }

DIR=
VERSION=$(cat "$ROOT/VERSION")
BASE=
ALLOW_MISSING=0
OUT=
while [ "$#" -gt 0 ]; do
    case $1 in
        --version) VERSION=$2; shift 2 ;;
        --base-url) BASE=$2; shift 2 ;;
        --allow-missing) ALLOW_MISSING=1; shift ;;
        -o) OUT=$2; shift 2 ;;
        -h | --help) sed -n '2,/^set -eu/p' "$0" | sed '$d' | sed 's/^# \{0,1\}//'; exit 0 ;;
        -*) die "unknown option $1" ;;
        *) DIR=$1; shift ;;
    esac
done
[ -d "${DIR:-}" ] || die "usage: render_formula.sh <release-dir> [--version V] [--base-url URL] [-o FILE]"
VERSION=${VERSION#v}
[ -n "$BASE" ] || BASE=https://github.com/suriyavasan/foundry-os/releases/download/v$VERSION
BASE=${BASE%/}

sha_for() { # file name -> hash on stdout, or nothing
    if [ -f "$DIR/SHA256SUMS" ]; then
        awk -v f="$1" '{ n = $2; sub(/^\*/, "", n) } n == f { print $1; exit }' "$DIR/SHA256SUMS"
    elif [ -f "$DIR/$1.sha256" ]; then
        awk '{print $1; exit}' "$DIR/$1.sha256"
    fi
}

# PLATFORMS (packaging/versions.env) is the one list of bundle platforms; the template's variables use _ for -
PLATFORMS=
# shellcheck disable=SC1091
. "$ROOT/packaging/versions.env"
[ -n "$PLATFORMS" ] || die "PLATFORMS is empty in packaging/versions.env"

SEDARGS="-e s|@VERSION@|$VERSION|g"
for platform in $PLATFORMS; do
    plat=$(printf '%s' "$platform" | tr - _)
    file=foundry-os-$VERSION-$platform.tar.gz
    sha=$(sha_for "$file")
    if [ -z "$sha" ]; then
        [ "$ALLOW_MISSING" = 1 ] || die "no checksum for $file in $DIR"
        sha=0000000000000000000000000000000000000000000000000000000000000000
        echo "render_formula: WARNING: no checksum for $file (zero placeholder)" >&2
    fi
    case $sha in *[!0-9a-f]* | "") die "bad checksum for $file: $sha" ;; esac
    [ "${#sha}" = 64 ] || die "bad checksum length for $file"
    SEDARGS="$SEDARGS -e s|@URL_$plat@|$BASE/$file|g -e s|@SHA_$plat@|$sha|g"
done

# shellcheck disable=SC2086
if [ -n "$OUT" ]; then
    sed $SEDARGS "$HERE/foundry-os.rb.tmpl" > "$OUT"
else
    sed $SEDARGS "$HERE/foundry-os.rb.tmpl"
fi
