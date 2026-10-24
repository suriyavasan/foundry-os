#!/bin/sh
# Foundry AI OS installer (macOS and Linux; no sudo, no root, nothing outside your home directory).
#
#   curl -fsSL https://raw.githubusercontent.com/suriyavasan/foundry-os/main/install.sh | sh
#   curl -fsSL https://raw.githubusercontent.com/suriyavasan/foundry-os/main/install.sh | sh -s -- --no-start
#
# What it does:
#   1. detects your OS and CPU (macOS or Linux; arm64 or x86_64);
#   2. if Homebrew is installed, uses it (`brew install suriyavasan/tap/foundry-os`), because then
#      `brew upgrade foundry-os` keeps you current. Skipped with --no-brew / FOUNDRYOS_NO_BREW=1, with
#      --version, and whenever the formula cannot be installed;
#   3. otherwise downloads the release bundle for your platform from GitHub, verifies its SHA-256
#      against the release's SHA256SUMS (refuses to continue on any mismatch), unpacks it to
#      ~/.foundry-os/versions/<version>, points ~/.foundry-os/current at it and links
#      ~/.local/bin/foundryos (and the `companyos` alias) to it;
#   4. starts it (unless --no-start), when this version knows how to run in the background.
#
# Running it again is safe: an installed version is not downloaded twice and links are refreshed.
# The bundle needs nothing else on your machine (no Python, Node, uv or git).
#
# Options                         Environment
#   --version X.Y.Z               FOUNDRYOS_VERSION        version to install (default: the latest release)
#   --prefix DIR                  FOUNDRYOS_PREFIX         install root (default ~/.foundry-os)
#   --bin-dir DIR                 FOUNDRYOS_BIN_DIR        where the foundryos link goes (default ~/.local/bin)
#   --no-start                    FOUNDRYOS_NO_START=1     install only
#   --no-service                  FOUNDRYOS_NO_SERVICE=1   do not install the login service
#   --no-modify-path              FOUNDRYOS_NO_MODIFY_PATH=1   never touch your shell startup file
#   --no-brew                     FOUNDRYOS_NO_BREW=1      do not use Homebrew
#   -y, --yes                     answer the Homebrew question with "yes"
#   --uninstall                   remove the installed versions and links (your data is kept)
#   -h, --help                    this text
#                                 FOUNDRYOS_DOWNLOAD_BASE  where the tarball and SHA256SUMS are downloaded
#                                                          from (default: the GitHub release; file:// works)
#                                 FOUNDRYOS_REPO           GitHub repository (default suriyavasan/foundry-os)
#
# Windows: use Docker or WSL (docs/install.md).

set -eu

REPO=${FOUNDRYOS_REPO:-suriyavasan/foundry-os}
BREW_FORMULA=suriyavasan/tap/foundry-os
RC_MARK='>>> foundry-os >>>'
RC_END='<<< foundry-os <<<'

WORK=
INCOMING=

say() { printf '%s\n' "$*"; }
note() { printf '  %s\n' "$*"; }
warn() { printf 'warning: %s\n' "$*" >&2; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }

cleanup() {
    [ -n "$WORK" ] && rm -rf "$WORK"
    [ -n "$INCOMING" ] && rm -rf "$INCOMING"
    return 0
}

usage() {
    sed -n '2,/^set -eu/p' "$0" | sed '$d' | sed 's/^# \{0,1\}//'
}

# ---------------------------------------------------------------------------------------------------
# platform

detect_platform() {
    case $(uname -s) in
        Darwin) os=darwin ;;
        Linux) os=linux ;;
        MINGW* | MSYS* | CYGWIN*)
            die "Windows is not supported natively. Use Docker (docker run -d -p 127.0.0.1:8700:8700 -v foundry-os:/data ghcr.io/suriyavasan/foundry-os:latest) or WSL."
            ;;
        *) die "unsupported operating system: $(uname -s). Use Docker (see docs/install.md)." ;;
    esac
    case $(uname -m) in
        arm64 | aarch64) arch=arm64 ;;
        x86_64 | amd64) arch=x86_64 ;;
        *) die "unsupported CPU architecture: $(uname -m) (supported: arm64, x86_64)" ;;
    esac
    # A shell running under Rosetta reports x86_64 on an Apple Silicon Mac: install the native build.
    if [ "$os" = darwin ] && [ "$arch" = x86_64 ] && [ "$(sysctl -n hw.optional.arm64 2>/dev/null || echo 0)" = 1 ]; then
        arch=arm64
    fi
    if [ "$os" = linux ]; then
        if [ -e /lib/ld-musl-x86_64.so.1 ] || [ -e /lib/ld-musl-aarch64.so.1 ] ||
            (have ldd && ldd --version 2>&1 | grep -qi musl); then
            die "this Linux uses musl libc (Alpine?); the bundles need glibc. Use the Docker image instead (docs/install.md)."
        fi
    fi
    PLATFORM=$os-$arch
}

# ---------------------------------------------------------------------------------------------------
# download + verify

fetch() { # url dest
    case $1 in
        file://*) cp "${1#file://}" "$2" ;;
        *)
            if have curl; then
                if [ -t 2 ]; then
                    curl -fL --retry 3 --connect-timeout 20 --progress-bar -o "$2" "$1"
                else
                    curl -fsSL --retry 3 --connect-timeout 20 -o "$2" "$1"
                fi
            elif have wget; then
                wget -q -O "$2" "$1"
            else
                die "curl or wget is required to download Foundry AI OS"
            fi
            ;;
    esac
}

sha256_of() {
    if have sha256sum; then
        sha256sum "$1" | awk '{print $1}'
    elif have shasum; then
        shasum -a 256 "$1" | awk '{print $1}'
    else
        die "sha256sum or shasum is required to verify the download"
    fi
}

latest_version() {
    fetch "https://api.github.com/repos/$REPO/releases/latest" "$WORK/latest.json" 2>/dev/null || true
    v=$(sed -n 's/.*"tag_name"[[:space:]]*:[[:space:]]*"v\{0,1\}\([^"]*\)".*/\1/p' "$WORK/latest.json" 2>/dev/null | head -n 1)
    if [ -z "$v" ] && have curl; then
        # rate-limited API: follow the "latest" redirect instead
        v=$(curl -fsSIL -o /dev/null -w '%{url_effective}' "https://github.com/$REPO/releases/latest" 2>/dev/null | sed -n 's|.*/tag/v\{0,1\}||p')
    fi
    [ -n "$v" ] || die "could not find the latest release of $REPO. Is there a release yet? Try --version X.Y.Z."
    printf '%s' "$v"
}

# ---------------------------------------------------------------------------------------------------
# locations

data_dir_hint() {
    if [ -n "${FOUNDRYOS_DATA_DIR:-}" ]; then
        printf '%s' "$FOUNDRYOS_DATA_DIR"
    elif [ "$(uname -s)" = Darwin ]; then
        printf '%s' "$HOME/Library/Application Support/Foundry AI OS"
    else
        printf '%s' "${XDG_DATA_HOME:-$HOME/.local/share}/foundry-os"
    fi
}

shell_rc() {
    case ${SHELL:-} in
        */zsh) printf '%s' "${ZDOTDIR:-$HOME}/.zshrc" ;;
        */bash)
            if [ "$(uname -s)" = Darwin ] && [ ! -f "$HOME/.bashrc" ]; then
                printf '%s' "$HOME/.bash_profile"
            else
                printf '%s' "$HOME/.bashrc"
            fi
            ;;
        */fish) printf '' ;;
        *) printf '%s' "$HOME/.profile" ;;
    esac
}

add_to_path() { # bin_dir
    case ":$PATH:" in
        *":$1:"*) return 0 ;;
    esac
    if [ "$MODIFY_PATH" != 1 ]; then
        note "$1 is not on your PATH. Add it, or call $1/foundryos by its full path."
        return 0
    fi
    rc=$(shell_rc)
    if [ -z "$rc" ]; then
        note "$1 is not on your PATH. For fish: fish_add_path $1"
        return 0
    fi
    if [ -f "$rc" ] && grep -F "$RC_MARK" "$rc" >/dev/null 2>&1; then
        note "$1 is not on this shell's PATH yet; $rc already has the entry, so open a new terminal."
        return 0
    fi
    {
        printf '\n# %s\n' "$RC_MARK"
        # shellcheck disable=SC2016
        printf 'export PATH="%s:$PATH"\n' "$1"
        printf '# %s\n' "$RC_END"
    } >> "$rc"
    note "Added $1 to your PATH in $rc (between '$RC_MARK' and '$RC_END' - delete those lines to undo)."
    note "Open a new terminal, or run: export PATH=\"$1:\$PATH\""
}

link_command() { # name target
    dest=$BIN_DIR/$1
    if [ -e "$dest" ] && [ ! -L "$dest" ]; then
        warn "$dest exists and is not a link; leaving it alone"
        return 0
    fi
    ln -sf "$2" "$dest"
}

# ---------------------------------------------------------------------------------------------------
# steps

interactive() { (exec < /dev/tty) 2>/dev/null; }

want_brew() {
    [ -z "$NO_BREW" ] && [ -z "$REQUESTED_VERSION" ] && [ -z "$CUSTOM_LOCATION" ] && have brew || return 1
    if [ "$ASSUME_YES" = 1 ] || ! interactive; then
        return 0
    fi
    printf 'Homebrew found. Install with Homebrew (recommended: "brew upgrade foundry-os" keeps you current)? [Y/n] ' > /dev/tty
    read -r answer < /dev/tty || answer=
    case $answer in
        n | N | no | No | NO) return 1 ;;
    esac
    return 0
}

install_with_brew() {
    if brew list --formula foundry-os >/dev/null 2>&1; then
        say "Foundry AI OS is already installed with Homebrew; checking for an update."
        brew upgrade "$BREW_FORMULA" </dev/null || warn "brew upgrade did not complete"
    else
        say "Installing with Homebrew ($BREW_FORMULA)..."
        brew install "$BREW_FORMULA" </dev/null || return 1
    fi
    FOUNDRYOS=$(brew --prefix)/bin/foundryos
    [ -x "$FOUNDRYOS" ] || return 1
    return 0
}

install_bundle() {
    detect_platform
    if [ -z "$VERSION" ]; then
        say "Looking up the latest release..."
        VERSION=$(latest_version)
    fi
    VERSION=${VERSION#v}
    file=foundry-os-$VERSION-$PLATFORM.tar.gz
    base=${DOWNLOAD_BASE:-https://github.com/$REPO/releases/download/v$VERSION}
    verdir=$PREFIX/versions/$VERSION

    if [ -x "$verdir/bin/foundryos" ]; then
        say "Foundry AI OS $VERSION ($PLATFORM) is already installed in $verdir."
    else
        say "Downloading Foundry AI OS $VERSION for $PLATFORM..."
        fetch "$base/SHA256SUMS" "$WORK/SHA256SUMS" || die "could not download $base/SHA256SUMS"
        expected=$(awk -v f="$file" '{ n = $2; sub(/^\*/, "", n) } n == f { print $1; exit }' "$WORK/SHA256SUMS")
        [ -n "$expected" ] || die "SHA256SUMS has no entry for $file (is there a $PLATFORM build of $VERSION?). Refusing to install."
        fetch "$base/$file" "$WORK/$file" || die "could not download $base/$file"
        actual=$(sha256_of "$WORK/$file")
        if [ "$actual" != "$expected" ]; then
            die "SHA-256 mismatch for $file (expected $expected, got $actual). Nothing was installed."
        fi
        note "SHA-256 verified ($actual)"

        mkdir -p "$PREFIX/versions"
        INCOMING=$PREFIX/versions/.incoming-$$
        rm -rf "$INCOMING"
        mkdir -p "$INCOMING"
        tar -xzf "$WORK/$file" -C "$INCOMING"
        [ -x "$INCOMING/foundry-os/bin/foundryos" ] || die "the archive does not look like a Foundry AI OS bundle"
        [ "$(cat "$INCOMING/foundry-os/VERSION")" = "$VERSION" ] || die "the archive contains a different version than $VERSION"
        rm -rf "$verdir"
        mv "$INCOMING/foundry-os" "$verdir"
        rm -rf "$INCOMING"
        INCOMING=
        note "Unpacked to $verdir"
    fi

    previous=
    [ -L "$PREFIX/current" ] && previous=$(readlink "$PREFIX/current")
    ln -sfn "versions/$VERSION" "$PREFIX/current"
    [ -z "$previous" ] || [ "$previous" = "versions/$VERSION" ] || note "Previous version kept: $PREFIX/$previous"

    mkdir -p "$BIN_DIR"
    link_command foundryos "$PREFIX/current/bin/foundryos"
    link_command companyos "$PREFIX/current/bin/foundryos"
    note "Linked $BIN_DIR/foundryos (and the companyos alias)"
    add_to_path "$BIN_DIR"
    FOUNDRYOS=$BIN_DIR/foundryos
}

start_it() {
    # `foundryos service ...` and a backgrounding `foundryos` arrive with the installed-mode CLI; an older
    # bundle would run in the foreground and block this script, so ask before using them.
    if "$FOUNDRYOS" service --help >/dev/null 2>&1 </dev/null; then
        if [ "$VIA_BREW" != 1 ] && [ -z "$NO_SERVICE" ]; then
            "$FOUNDRYOS" service install </dev/null || warn "could not install the login service (continuing)"
        fi
        say "Starting Foundry AI OS..."
        "$FOUNDRYOS" </dev/null || warn "could not start Foundry AI OS; run: foundryos"
    else
        note "Start it with: foundryos   (it opens your browser; keep that terminal open)"
    fi
}

next_steps() {
    say ""
    say "Foundry AI OS is installed."
    say "  Start / open it:  foundryos          (local address: http://127.0.0.1:8700)"
    say "  Check your setup: foundryos doctor"
    if [ "$VIA_BREW" = 1 ]; then
        say "  Update:           brew upgrade foundry-os"
        say "  Run at login:     brew services start foundry-os"
        say "  Uninstall:        brew uninstall foundry-os"
    else
        say "  Update:           run this installer again (it installs the latest release)"
        say "  Uninstall:        curl -fsSL https://raw.githubusercontent.com/$REPO/main/install.sh | sh -s -- --uninstall"
    fi
    say "  Your data lives in: $(data_dir_hint)   (kept when you uninstall)"
}

uninstall() {
    if have brew && brew list --formula foundry-os >/dev/null 2>&1; then
        say "Foundry AI OS was installed with Homebrew: run  brew services stop foundry-os; brew uninstall foundry-os"
    fi
    if [ -x "$BIN_DIR/foundryos" ] && "$BIN_DIR/foundryos" service --help >/dev/null 2>&1 </dev/null; then
        "$BIN_DIR/foundryos" service uninstall </dev/null || true
    fi
    for name in foundryos companyos; do
        if [ -L "$BIN_DIR/$name" ]; then
            case $(readlink "$BIN_DIR/$name") in
                "$PREFIX"/*) rm -f "$BIN_DIR/$name"; note "Removed $BIN_DIR/$name" ;;
            esac
        fi
    done
    if [ -d "$PREFIX/versions" ] || [ -L "$PREFIX/current" ]; then
        rm -rf "$PREFIX/versions" "$PREFIX/current"
        rmdir "$PREFIX/bin" 2>/dev/null || true
        rmdir "$PREFIX" 2>/dev/null || true
        note "Removed the installed versions in $PREFIX"
    else
        note "Nothing installed in $PREFIX"
    fi
    say "Your data was not touched: $(data_dir_hint)"
    say "Delete it yourself to start over. A PATH entry in your shell startup file (if the installer added one) is harmless."
}

main() {
    VERSION=${FOUNDRYOS_VERSION:-}
    PREFIX=${FOUNDRYOS_PREFIX:-}
    BIN_DIR=${FOUNDRYOS_BIN_DIR:-}
    NO_START=${FOUNDRYOS_NO_START:-}
    NO_SERVICE=${FOUNDRYOS_NO_SERVICE:-}
    NO_BREW=${FOUNDRYOS_NO_BREW:-}
    MODIFY_PATH=1
    [ -z "${FOUNDRYOS_NO_MODIFY_PATH:-}" ] || MODIFY_PATH=0
    DOWNLOAD_BASE=${FOUNDRYOS_DOWNLOAD_BASE:-}
    ASSUME_YES=0
    DO_UNINSTALL=0
    VIA_BREW=0

    while [ "$#" -gt 0 ]; do
        case $1 in
            --version) [ "$#" -ge 2 ] || die "--version needs a value"; VERSION=$2; shift 2 ;;
            --version=*) VERSION=${1#--version=}; shift ;;
            --prefix) [ "$#" -ge 2 ] || die "--prefix needs a value"; PREFIX=$2; shift 2 ;;
            --prefix=*) PREFIX=${1#--prefix=}; shift ;;
            --bin-dir) [ "$#" -ge 2 ] || die "--bin-dir needs a value"; BIN_DIR=$2; shift 2 ;;
            --bin-dir=*) BIN_DIR=${1#--bin-dir=}; shift ;;
            --no-start) NO_START=1; shift ;;
            --no-service) NO_SERVICE=1; shift ;;
            --no-modify-path) MODIFY_PATH=0; shift ;;
            --no-brew) NO_BREW=1; shift ;;
            -y | --yes) ASSUME_YES=1; shift ;;
            --uninstall) DO_UNINSTALL=1; shift ;;
            -h | --help) usage; exit 0 ;;
            *) die "unknown option: $1 (see --help)" ;;
        esac
    done

    : "${HOME:?HOME is not set}"
    REQUESTED_VERSION=$VERSION
    [ -z "$BIN_DIR" ] || MODIFY_PATH=0 # a bin dir the user chose is theirs to put on PATH
    CUSTOM_LOCATION=
    [ -z "$PREFIX" ] && [ -z "$BIN_DIR" ] && [ -z "$DOWNLOAD_BASE" ] || CUSTOM_LOCATION=1
    if [ -n "$PREFIX" ]; then
        case $PREFIX in /*) ;; *) PREFIX=$PWD/$PREFIX ;; esac
        [ -n "$BIN_DIR" ] || BIN_DIR=$PREFIX/bin
        MODIFY_PATH=0
    else
        PREFIX=$HOME/.foundry-os
    fi
    [ -n "$BIN_DIR" ] || BIN_DIR=$HOME/.local/bin
    case $BIN_DIR in /*) ;; *) BIN_DIR=$PWD/$BIN_DIR ;; esac

    if [ "$DO_UNINSTALL" = 1 ]; then
        uninstall
        return 0
    fi

    WORK=$(mktemp -d "${TMPDIR:-/tmp}/foundryos-install.XXXXXX")
    trap cleanup EXIT INT TERM HUP

    say "Foundry AI OS installer"
    if want_brew; then
        if install_with_brew; then
            VIA_BREW=1
        else
            warn "the Homebrew install did not work (is the tap published yet?); falling back to the release bundle"
        fi
    fi
    if [ "$VIA_BREW" != 1 ]; then
        install_bundle
    fi
    [ -n "$NO_START" ] || start_it
    next_steps
}

main "$@"
