#!/bin/sh
# Releases are automatic: every green push to main is published by .github/workflows/release.yml, versioned
# by scripts/next_version.sh (VERSION is the floor; each release is the highest tag + 1 patch, or the floor
# when that is higher). This script only handles the two human parts.
#
#   scripts/release.sh <version> [--yes]
#       Cut a minor/major: raise the VERSION floor (e.g. 0.2.0) and move the "Unreleased" changelog section
#       under it. It edits those two files and nothing else: no commit, no tag, no push. Review the diff,
#       then commit and push; CI -> release.yml publishes it (the tag is created when the release is
#       published). <version> must be a stable X.Y.Z, newer than the highest stable tag.
#   scripts/release.sh --notes <version> [--since <tag>] [--sha <sha>]
#       Print the release notes for <version> (used by CI): the "## [<version>]" section of CHANGELOG.md if
#       there is one, else "- subject (abbrev)" for each non-merge commit since <tag> (default: the highest
#       stable tag) up to <sha> (default HEAD); then a compare link.
#
# Cut mode asks for confirmation (unless --yes) and refuses to run with a dirty working tree or off main.
set -eu

ROOT=$(cd "$(dirname "$0")/.." && pwd)
cd "$ROOT"

die() { echo "release: $*" >&2; exit 1; }

REPO_SLUG=${GITHUB_REPOSITORY:-suriyavasan/foundry-os}

# trim leading/trailing blank lines from stdin
trim_blank() { sed -e :a -e '/^[[:space:]]*$/{$d;N;ba' -e '}' | sed '/./,$!d'; }

# section_body <heading-prefix>: the body of the CHANGELOG section whose heading starts with the prefix
section_body() {
    awk -v h="$1" 'index($0, h) == 1 { on = 1; next } on && /^## \[/ { exit } on { print }' CHANGELOG.md | trim_blank
}

# highest_tag: the highest stable vA.B.C tag without the "v" (empty when there is none)
highest_tag() {
    git tag -l 'v[0-9]*' | grep -E '^v[0-9]+\.[0-9]+\.[0-9]+$' | sed 's/^v//' \
        | sort -t. -k1,1n -k2,2n -k3,3n | tail -n 1 || true
}

# ------------------------------------------------------------------ --notes
if [ "${1:-}" = --notes ]; then
    [ -n "${2:-}" ] || die "usage: release.sh --notes <version> [--since <tag>] [--sha <sha>]"
    V=${2#v}
    shift 2
    SINCE=
    SHA=HEAD
    while [ "$#" -gt 0 ]; do
        case $1 in
            --since) [ "$#" -ge 2 ] || die "--since needs a value"; SINCE=${2#v}; shift 2 ;;
            --sha) [ "$#" -ge 2 ] || die "--sha needs a value"; SHA=$2; shift 2 ;;
            *) die "unknown argument $1" ;;
        esac
    done
    [ -n "$SINCE" ] || SINCE=$(highest_tag)
    [ "$SINCE" != "$V" ] || SINCE=
    notes=$(section_body "## [$V]")
    if [ -z "$notes" ]; then
        if [ -n "$SINCE" ] && git rev-parse -q --verify "refs/tags/v$SINCE" >/dev/null; then
            notes=$(git log --no-merges --format='- %s (%h)' "v$SINCE..$SHA")
        else
            notes=$(git log --no-merges -n 100 --format='- %s (%h)' "$SHA")
        fi
    fi
    [ -n "$notes" ] || notes="See the commit history for changes in this release."
    printf '%s\n' "$notes"
    echo
    if [ -n "$SINCE" ]; then
        printf '**Full changelog**: https://github.com/%s/compare/v%s...v%s\n' "$REPO_SLUG" "$SINCE" "$V"
    else
        printf '**Full changelog**: https://github.com/%s/commits/v%s\n' "$REPO_SLUG" "$V"
    fi
    exit 0
fi

# ------------------------------------------------------------------ cut mode
NEW=
YES=0
while [ "$#" -gt 0 ]; do
    case $1 in
        --yes | -y) YES=1; shift ;;
        -h | --help) sed -n '2,/^set -eu/p' "$0" | sed '$d' | sed 's/^# \{0,1\}//'; exit 0 ;;
        -*) die "unknown option $1" ;;
        *) [ -z "$NEW" ] || die "one version only"; NEW=${1#v}; shift ;;
    esac
done
[ -n "$NEW" ] || die "usage: release.sh <version> [--yes]   or   release.sh --notes <version> [--since <tag>] [--sha <sha>]"
printf '%s' "$NEW" | grep -Eq '^[0-9]+\.[0-9]+\.[0-9]+$' || die "'$NEW' is not a stable semantic version (X.Y.Z)"

OLD=$(tr -d '[:space:]' < VERSION)
branch=$(git rev-parse --abbrev-ref HEAD)
[ "$branch" = main ] || die "releases are cut from main (you are on '$branch')"
[ -z "$(git status --porcelain)" ] || die "the working tree is not clean; commit or stash first"
grep -q "^## \[$NEW\]" CHANGELOG.md && die "CHANGELOG.md already has a '## [$NEW]' section"

newer() { # newer A B: A > B
    [ "$1" != "$2" ] && [ "$(printf '%s\n%s\n' "$1" "$2" | sort -t. -k1,1n -k2,2n -k3,3n | tail -n 1)" = "$1" ]
}
last=$(highest_tag)
if [ -n "$last" ]; then
    newer "$NEW" "$last" || die "$NEW is not newer than the highest release tag v$last"
fi
# an old pre-release VERSION (X.Y.Z-rc.1) counts as its X.Y.Z
oldnum=${OLD%%-*}
[ "$NEW" = "$oldnum" ] || newer "$NEW" "$oldnum" || die "$NEW is older than the current VERSION $OLD"

grep -q '^## \[Unreleased\]' CHANGELOG.md || die "CHANGELOG.md has no '## [Unreleased]' section"
[ -n "$(section_body '## [Unreleased]')" ] || die "the Unreleased section of CHANGELOG.md is empty: write the release notes first"

# the other places a version lives are not rewritten here; say so when they disagree
for f in backend/pyproject.toml web/package.json; do
    [ -f "$f" ] || continue
    have=$(sed -n 's/^[[:space:]]*"\{0,1\}version"\{0,1\}[[:space:]]*[:=][[:space:]]*"\([^"]*\)".*/\1/p' "$f" | head -n 1)
    [ -z "$have" ] || [ "$have" = "$NEW" ] || echo "note: $f says version $have (the bundles use VERSION; update it if you want them to agree)" >&2
done

today=$(date +%Y-%m-%d)
echo "This will edit two files (no commit, no tag, no push):"
echo "  - VERSION: $OLD -> $NEW (the floor: a push to main after this publishes exactly $NEW if it is above the latest tag)"
echo "  - CHANGELOG.md: '## [Unreleased]' becomes '## [$NEW] - $today' (a fresh empty Unreleased section is added)"
if [ "$YES" != 1 ]; then
    printf 'Continue? [y/N] '
    read -r answer
    case $answer in y | Y | yes | YES) ;; *) echo "aborted"; exit 1 ;; esac
fi

printf '%s\n' "$NEW" > VERSION
tmp=$(mktemp)
awk -v v="$NEW" -v d="$today" '
    index($0, "## [Unreleased]") == 1 && !done { print "## [Unreleased]"; print ""; print "## [" v "] - " d; done = 1; next }
    { print }
' CHANGELOG.md > "$tmp"
cat "$tmp" > CHANGELOG.md
rm -f "$tmp"

echo "Done. Review with:  git diff"
echo "Then commit and push; CI -> release.yml publishes v$NEW (the tag is created when the release is published):"
echo "  git add VERSION CHANGELOG.md && git commit -m 'Release v$NEW' && git push origin main"
