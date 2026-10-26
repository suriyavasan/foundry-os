#!/bin/sh
# Decide the version a commit would be released as, and whether it should be released at all.
# Used by .github/workflows/release.yml (the "a release on every green push to main" pipeline).
#
#   scripts/next_version.sh [--sha SHA] [--repo DIR]
#
# Prints key=value lines (append them to $GITHUB_OUTPUT):
#   version=X.Y.Z   the version this commit would be published as
#   last=X.Y.Z      the highest stable vA.B.C tag (empty when there is none)
#   sha=<full sha>  the commit that was examined
#   publish=true|false
#   reason=...      why the commit is, or is not, published
#
# Versioning is tag-only; nothing is committed. VERSION is a floor X.Y.Z. With no stable tag, or a floor
# above the highest tag, the version is the floor; otherwise it is the highest tag with the patch + 1.
# `scripts/release.sh 0.2.0` raises the floor. A pre-release VERSION (X.Y.Z-rc.1) is refused: the automatic
# path only publishes stable releases.
#
# publish=false (exit status 0, the run stays green) when
#   - the commit is already contained in the highest tag (a re-run, or an older commit that came second),
#   - its subject line (first line of the message) contains "[skip release]",
#   - every file changed since the highest tag is under docs/ or site/ or is a *.md file
#     (switch off with SKIP_DOCS_ONLY=0).
# Exit status 1 for a VERSION that is not X.Y.Z or a commit that cannot be resolved.
set -eu

die() { echo "next_version: $*" >&2; exit 1; }

SHA=HEAD
REPO=
while [ "$#" -gt 0 ]; do
    case $1 in
        --sha) [ "$#" -ge 2 ] || die "--sha needs a value"; SHA=$2; shift 2 ;;
        --repo) [ "$#" -ge 2 ] || die "--repo needs a value"; REPO=$2; shift 2 ;;
        -h | --help) sed -n '2,/^set -eu/p' "$0" | sed '$d' | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) die "unknown argument $1" ;;
    esac
done
if [ -z "$REPO" ]; then REPO=$(cd "$(dirname "$0")/.." && pwd); fi
cd "$REPO"

sha=$(git rev-parse --verify -q "$SHA^{commit}") || die "cannot resolve '$SHA' to a commit"
floor=$(git show "$sha:VERSION" 2>/dev/null | tr -d '[:space:]') || true
[ -n "$floor" ] || die "no VERSION file at $sha"
case $floor in
    *-*) die "VERSION '$floor' has a pre-release suffix; the automatic release path publishes stable X.Y.Z only" ;;
esac
printf '%s' "$floor" | grep -Eq '^[0-9]+\.[0-9]+\.[0-9]+$' || die "VERSION '$floor' is not X.Y.Z"

# the highest stable tag vA.B.C
last=$(git tag -l 'v[0-9]*' | grep -E '^v[0-9]+\.[0-9]+\.[0-9]+$' | sed 's/^v//' \
    | sort -t. -k1,1n -k2,2n -k3,3n | tail -n 1 || true)

if [ -z "$last" ]; then
    version=$floor
else
    higher=$(printf '%s\n%s\n' "$floor" "$last" | sort -t. -k1,1n -k2,2n -k3,3n | tail -n 1)
    if [ "$higher" = "$last" ]; then
        # floor <= last: the next patch of the highest tag
        version="${last%.*}.$(( ${last##*.} + 1 ))"
    else
        version=$floor
    fi
fi

publish=true
reason="new release v$version"
if [ -n "$last" ] && git merge-base --is-ancestor "$sha" "v$last" 2>/dev/null; then
    publish=false
    reason="commit is already contained in v$last"
elif git log -1 --format=%s "$sha" | grep -qF '[skip release]'; then
    publish=false
    reason="commit subject contains [skip release]"
elif [ -n "$last" ] && [ "${SKIP_DOCS_ONLY:-1}" != 0 ]; then
    changed=$(git diff --name-only "v$last" "$sha")
    if [ -z "$changed" ]; then
        publish=false
        reason="no file changed since v$last"
    else
        other=$(printf '%s\n' "$changed" | grep -Ev '^(docs/|site/)|\.md$' || true)
        if [ -z "$other" ]; then
            publish=false
            reason="only docs/, site/ and *.md changed since v$last"
        fi
    fi
fi

printf 'version=%s\nlast=%s\nsha=%s\npublish=%s\nreason=%s\n' "$version" "$last" "$sha" "$publish" "$reason"
