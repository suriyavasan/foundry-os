#!/bin/sh
# Write SHA256SUMS for the release bundles in a directory (what the GitHub Release attaches, what
# install.sh and the Homebrew formula verify against). Cross-checks each bundle's own .sha256 file.
#
#   packaging/sums.sh <dir>
set -eu

dir=${1:?usage: sums.sh <dir>}
cd "$dir"
sha256_of() {
    if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | awk '{print $1}'; else shasum -a 256 "$1" | awk '{print $1}'; fi
}
: > SHA256SUMS.tmp
count=0
for f in foundry-os-*.tar.gz; do
    [ -f "$f" ] || { echo "sums.sh: no foundry-os-*.tar.gz in $dir" >&2; rm -f SHA256SUMS.tmp; exit 1; }
    h=$(sha256_of "$f")
    if [ -f "$f.sha256" ] && [ "$(awk '{print $1}' "$f.sha256")" != "$h" ]; then
        echo "sums.sh: $f does not match $f.sha256" >&2
        rm -f SHA256SUMS.tmp
        exit 1
    fi
    printf '%s  %s\n' "$h" "$f" >> SHA256SUMS.tmp
    count=$((count + 1))
done
sort -k2 SHA256SUMS.tmp > SHA256SUMS
rm -f SHA256SUMS.tmp
echo "SHA256SUMS: $count bundle(s)"
cat SHA256SUMS
