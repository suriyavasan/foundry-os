#!/bin/sh
# Cut a release: bump VERSION, move the "Unreleased" changelog section under the new version, commit, tag
# and push. The tag push starts .github/workflows/release.yml (bundles, GitHub Release, Docker image, tap).
#
#   scripts/release.sh <version> [--no-push] [--yes]     e.g. scripts/release.sh 0.2.0
#   scripts/release.sh --notes <version>                 print that version's changelog notes (used by CI)
#
# It prints what it is about to do and asks for confirmation (unless --yes). It refuses to run with a dirty
# working tree, off the main branch, for a version that is not newer than VERSION, or for a tag that exists.
set -eu

ROOT=$(cd "$(dirname "$0")/.." && pwd)
cd "$ROOT"

die() { echo "release: $*" >&2; exit 1; }

# changelog_notes <version>: the body of "## [version]" (falls back to "## [Unreleased]")
changelog_notes() {
    v=$1
    body=$(awk -v h="## [$v]" 'index($0, h) == 1 { on = 1; next } on && /^## \[/ { exit } on { print }' CHANGELOG.md)
    if [ -z "$(printf '%s' "$body" | tr -d '[:space:]')" ]; then
        body=$(awk 'index($0, "## [Unreleased]") == 1 { on = 1; next } on && /^## \[/ { exit } on { print }' CHANGELOG.md)
    fi
    printf '%s\n' "$body" | sed -e :a -e '/^[[:space:]]*$/{$d;N;ba' -e '}' | sed '/./,$!d'
}

if [ "${1:-}" = --notes ]; then
    [ -n "${2:-}" ] || die "usage: release.sh --notes <version>"
    notes=$(changelog_notes "${2#v}")
    if [ -z "$notes" ]; then notes="See the commit history for changes in this release."; fi
    printf '%s\n' "$notes"
    exit 0
fi

NEW=
PUSH=1
YES=0
while [ "$#" -gt 0 ]; do
    case $1 in
        --no-push) PUSH=0; shift ;;
        --yes | -y) YES=1; shift ;;
        -h | --help) sed -n '2,/^set -eu/p' "$0" | sed '$d' | sed 's/^# \{0,1\}//'; exit 0 ;;
        -*) die "unknown option $1" ;;
        *) [ -z "$NEW" ] || die "one version only"; NEW=${1#v}; shift ;;
    esac
done
[ -n "$NEW" ] || die "usage: release.sh <version> [--no-push] [--yes]"
printf '%s' "$NEW" | grep -Eq '^[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z.]+)?$' || die "'$NEW' is not a semantic version (X.Y.Z or X.Y.Z-rc.1)"

OLD=$(cat VERSION)
branch=$(git rev-parse --abbrev-ref HEAD)
[ "$branch" = main ] || die "releases are cut from main (you are on '$branch')"
[ -z "$(git status --porcelain)" ] || die "the working tree is not clean; commit or stash first"
git rev-parse -q --verify "refs/tags/v$NEW" >/dev/null && die "tag v$NEW already exists"
# NEW == OLD is fine for the very first release (VERSION starts at 0.1.0 with the notes under Unreleased)
grep -q "^## \[$NEW\]" CHANGELOG.md && die "CHANGELOG.md already has a '## [$NEW]' section"
newest=$(printf '%s\n%s\n' "$OLD" "$NEW" | sed 's/-.*//' | sort -t. -k1,1n -k2,2n -k3,3n | tail -n 1)
[ "$newest" = "${NEW%%-*}" ] || die "$NEW is older than the current version $OLD"
grep -q '^## \[Unreleased\]' CHANGELOG.md || die "CHANGELOG.md has no '## [Unreleased]' section"
[ -n "$(changelog_notes "$NEW")" ] || die "the Unreleased section of CHANGELOG.md is empty: write the release notes first"

# the other places a version lives are not rewritten here; say so when they disagree
for f in backend/pyproject.toml web/package.json; do
    [ -f "$f" ] || continue
    have=$(sed -n 's/^[[:space:]]*"\{0,1\}version"\{0,1\}[[:space:]]*[:=][[:space:]]*"\([^"]*\)".*/\1/p' "$f" | head -n 1)
    [ -z "$have" ] || [ "$have" = "$NEW" ] || echo "note: $f says version $have (the bundles use VERSION; update it if you want them to agree)" >&2
done

today=$(date +%Y-%m-%d)
echo "This will:"
if [ "$NEW" = "$OLD" ]; then echo "  - keep VERSION at $NEW (first release)"; else echo "  - set VERSION: $OLD -> $NEW"; fi
echo "  - CHANGELOG.md: '## [Unreleased]' becomes '## [$NEW] - $today' (a fresh empty Unreleased section is added)"
echo "  - commit 'Release v$NEW' and create the annotated tag v$NEW"
if [ "$PUSH" = 1 ]; then
    echo "  - push main and v$NEW to origin (this starts the release workflow)"
else
    echo "  - NOT push (--no-push)"
fi
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

git add VERSION CHANGELOG.md
git commit -q -m "Release v$NEW"
git tag -a "v$NEW" -m "Foundry AI OS $NEW"
echo "Committed and tagged v$NEW."
if [ "$PUSH" = 1 ]; then
    git push origin main "v$NEW"
    echo "Pushed. Watch: https://github.com/suriyavasan/foundry-os/actions"
else
    echo "Push when ready:  git push origin main v$NEW"
fi
