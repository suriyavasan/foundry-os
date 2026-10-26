#!/bin/sh
# Tests for scripts/next_version.sh. Builds throwaway git repositories; touches nothing else.
#
#   sh scripts/tests/next_version_test.sh
set -eu

HERE=$(cd "$(dirname "$0")" && pwd)
SCRIPT=$HERE/../next_version.sh
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT INT TERM

export GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@example.com GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@example.com
export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_SYSTEM=/dev/null
unset SKIP_DOCS_ONLY || true

fails=0
n=0

new_repo() { # name floor -> a repository with one commit that has VERSION and src/app.py
    r=$TMP/$1
    mkdir -p "$r/src" "$r/docs" "$r/site"
    git init -q -b main "$r"
    printf '%s\n' "$2" > "$r/VERSION"
    echo one > "$r/src/app.py"
    git -C "$r" add -A
    git -C "$r" commit -q -m "first"
    echo "$r"
}
commit_file() { # repo path content message
    mkdir -p "$(dirname "$1/$2")"
    printf '%s\n' "$3" > "$1/$2"
    git -C "$1" add -A
    git -C "$1" commit -q -m "$4"
}
get() { printf '%s\n' "$2" | sed -n "s/^$1=//p"; }

check() { # name repo expected-version expected-publish [reason-substring]
    n=$((n + 1))
    out=$("$SCRIPT" --repo "$2" --sha "${SHA_ARG:-HEAD}" 2>&1) || { echo "not ok $n - $1: exit $?: $out"; fails=$((fails + 1)); return; }
    v=$(get version "$out"); p=$(get publish "$out"); why=$(get reason "$out")
    if [ "$v" = "$3" ] && [ "$p" = "$4" ] && { [ -z "${5:-}" ] || printf '%s' "$why" | grep -qF "$5"; }; then
        echo "ok $n - $1 (version=$v publish=$p)"
    else
        echo "not ok $n - $1: got version=$v publish=$p reason='$why', want version=$3 publish=$4 ${5:+reason~"$5"}"
        fails=$((fails + 1))
    fi
}
check_fails() { # name repo message-substring
    n=$((n + 1))
    if out=$("$SCRIPT" --repo "$2" --sha HEAD 2>&1); then
        echo "not ok $n - $1: expected a failure, got: $out"; fails=$((fails + 1))
    elif printf '%s' "$out" | grep -qF "$3"; then
        echo "ok $n - $1 (rejected)"
    else
        echo "not ok $n - $1: failed with '$out', want '$3'"; fails=$((fails + 1))
    fi
}

# 1. no tags: the floor is the first version
r=$(new_repo notags 0.1.0)
check "no tags -> the floor" "$r" 0.1.0 true

# 2. floor above the last tag: the floor wins
r=$(new_repo floor 0.1.0)
git -C "$r" tag v0.1.0
commit_file "$r" src/app.py two "code"
printf '0.2.0\n' > "$r/VERSION"; git -C "$r" commit -q -am "raise the floor"
check "floor above last tag -> floor" "$r" 0.2.0 true

# 3. floor equal to / below the last tag: patch + 1 of the highest tag (numeric sort: 0.10.0 > 0.9.0)
r=$(new_repo patch 0.1.0)
git -C "$r" tag v0.1.0
git -C "$r" tag v0.9.0
git -C "$r" tag v0.10.0
git -C "$r" tag v0.10.0-rc.1   # pre-release tags never count
git -C "$r" tag not-a-version
commit_file "$r" src/app.py two "code"
check "patch bump of the highest stable tag" "$r" 0.10.1 true

# 4. already contained in the latest tag (re-run of the same commit)
r=$(new_repo contained 0.1.0)
git -C "$r" tag v0.1.0
check "commit already in the latest tag -> skip" "$r" 0.1.1 false "already contained in v0.1.0"

# 4b. an older commit while a newer tag exists
r=$(new_repo older 0.1.0)
commit_file "$r" src/app.py two "code A"
old=$(git -C "$r" rev-parse HEAD)
commit_file "$r" src/app.py three "code B"
git -C "$r" tag v0.1.0
commit_file "$r" src/app.py four "code C"
SHA_ARG=$old
check "older commit than the latest tag -> skip" "$r" 0.1.1 false "already contained"
SHA_ARG=HEAD
check "newer commit than the latest tag -> publish" "$r" 0.1.1 true

# 5. docs-only changes since the latest tag
r=$(new_repo docs 0.1.0)
git -C "$r" tag v0.1.0
commit_file "$r" docs/guide.txt text "docs"
commit_file "$r" site/index.html html "site"
commit_file "$r" README.md text "readme"
commit_file "$r" backend/NOTES.md text "nested markdown"
check "docs/site/*.md only -> skip" "$r" 0.1.1 false "only docs/"
SKIP_DOCS_ONLY=0
export SKIP_DOCS_ONLY
check "docs-only with SKIP_DOCS_ONLY=0 -> publish" "$r" 0.1.1 true
unset SKIP_DOCS_ONLY
commit_file "$r" src/app.py two "code"
check "docs plus code -> publish" "$r" 0.1.1 true

# 6. the skip marker
r=$(new_repo marker 0.1.0)
git -C "$r" tag v0.1.0
commit_file "$r" src/app.py two "tweak [skip release]"
check "[skip release] -> skip" "$r" 0.1.1 false "[skip release]"
r=$(new_repo marker-body 0.1.0)
git -C "$r" tag v0.1.0
commit_file "$r" src/app.py two "feature

Explains the [skip release] marker in the body"
check "[skip release] only in the body -> publish" "$r" 0.1.1 true

# 7. a VERSION with a suffix is rejected, and so is garbage
r=$(new_repo suffix 0.2.0-rc.1)
check_fails "VERSION with a suffix is rejected" "$r" "pre-release suffix"
r=$(new_repo garbage 0.2)
check_fails "VERSION that is not X.Y.Z is rejected" "$r" "not X.Y.Z"

# 8. an unknown commit
r=$(new_repo badsha 0.1.0)
n=$((n + 1))
if "$SCRIPT" --repo "$r" --sha deadbeef >/dev/null 2>&1; then echo "not ok $n - unknown sha accepted"; fails=$((fails + 1)); else echo "ok $n - unknown sha rejected"; fi

echo
if [ "$fails" -gt 0 ]; then echo "$fails of $n checks FAILED"; exit 1; fi
echo "all $n checks passed"
