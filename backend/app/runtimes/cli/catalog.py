"""What Foundry AI OS knows about each supported coding CLI (static facts; the verified flags live in
``docs/cli-runtimes.md``)."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit

# Anything in an argv (or in a generated config) that would switch a CLI's safety off. The adapters
# assert none of these are present before spawning; a test asserts it for every adapter and mode.
FORBIDDEN_ARGV = (
    "--dangerously-skip-permissions",
    "--allow-dangerously-skip-permissions",
    "bypassPermissions",
    "--auto",
    "danger-full-access",
    "--dangerously-bypass-approvals-and-sandbox",
    "--yolo",
    "--full-auto",
    "always-proceed",  # Antigravity CLI ``toolPermission`` that auto-approves every tool call
)


@dataclass(frozen=True)
class InstallMethod:
    """One way Foundry may install a CLI. ``npm``: ``npm install -g <package>``. ``script``: download the
    vendor's official installer from ``url`` (HTTPS, host must equal ``host``), then run
    ``[interpreter, <private temp file>]`` -- never a pipe, never a shell string, never sudo."""

    kind: str  # "npm" | "script"
    package: str | None = None
    url: str | None = None
    interpreter: str | None = None  # "bash" | "sh"
    host: str | None = None  # the only host the script (and every redirect) may come from

    @property
    def command(self) -> str:
        """The exact, human-readable thing that will happen (shown before the owner confirms)."""
        if self.kind == "npm":
            return f"npm install -g {self.package}"
        return f"Download {self.url} and run it with {self.interpreter}"


def npm_method(package: str) -> InstallMethod:
    return InstallMethod(kind="npm", package=package)


def script_method(url: str, interpreter: str) -> InstallMethod:
    host = urlsplit(url).hostname
    assert url.startswith("https://") and host and interpreter in ("bash", "sh")
    return InstallMethod(kind="script", url=url, interpreter=interpreter, host=host)


@dataclass(frozen=True)
class CliSpec:
    id: str  # claude_code | codex | opencode | antigravity | cursor
    display_name: str
    binaries: tuple[str, ...]  # executable names looked up on PATH and in extra locations
    known_dirs: tuple[str, ...]  # extra places a user-level install lands (relative to ~ if no /)
    npm_package: str | None  # None: not an npm package, never installed by Foundry AI OS
    login_hint: str
    auth_modes: tuple[str, ...] = ("own_login", "api_key")
    # Environment variable that carries an API key in api_key mode.
    key_env: str | None = None
    # Shown instead of an install button when the CLI has no npm package (the person runs it themselves).
    manual_install: str | None = None
    # Ordered install methods (the first one that can run here is the default). Every script URL below
    # comes from the vendor's official documentation (see ``docs/cli-runtimes.md``, "Install").
    install_methods: tuple[InstallMethod, ...] = ()
    # The command a person runs in a terminal to sign this CLI in with their own account, for
    # ``foundryos setup`` (inherited stdio, so device codes and URLs reach the terminal). UNVERIFIED:
    # taken from each CLI's documentation / ``--help``, never run by the test suite or on this machine
    # by Foundry AI OS. ``None``: the CLI has no own-login mode.
    headless_login_argv: tuple[str, ...] | None = None

    @property
    def runtime_id(self) -> str:
        return f"cli:{self.id}"

    @property
    def install_command(self) -> list[str] | None:
        """The one command the install endpoint may run; None when there is no automatic install."""
        return ["npm", "install", "-g", self.npm_package] if self.npm_package else None

    def method(self, kind: str) -> InstallMethod | None:
        return next((m for m in self.install_methods if m.kind == kind), None)


CLIS: dict[str, CliSpec] = {
    "claude_code": CliSpec(
        id="claude_code",
        display_name="Claude Code",
        binaries=("claude",),
        known_dirs=(".claude/local", ".local/bin", ".npm-global/bin"),
        npm_package="@anthropic-ai/claude-code",
        install_methods=(
            npm_method("@anthropic-ai/claude-code"),
            script_method("https://claude.ai/install.sh", "bash"),
        ),
        login_hint="Run `foundryos ai login` in a terminal (or use Sign in with Claude in Settings > AI)",
        key_env="ANTHROPIC_API_KEY",
        # `claude auth login` (checked against `claude --help` 2.1.308). It must run with Foundry's own
        # CLAUDE_CONFIG_DIR: use `foundryos ai login`, never this argv on its own.
        headless_login_argv=("claude", "auth", "login"),
    ),
    "codex": CliSpec(
        id="codex",
        display_name="Codex CLI",
        binaries=("codex",),
        known_dirs=(".local/bin", ".npm-global/bin"),
        npm_package="@openai/codex",
        install_methods=(
            npm_method("@openai/codex"),
            script_method("https://chatgpt.com/codex/install.sh", "sh"),
        ),
        login_hint="Run `codex login` in a terminal",
        key_env="CODEX_API_KEY",
        # Unverified: `codex login --device-auth` is the device-code flow for machines without a browser.
        headless_login_argv=("codex", "login", "--device-auth"),
    ),
    "opencode": CliSpec(
        id="opencode",
        display_name="OpenCode",
        binaries=("opencode",),
        known_dirs=(".opencode/bin", ".local/bin", ".npm-global/bin"),
        npm_package="opencode-ai",
        install_methods=(
            npm_method("opencode-ai"),
            script_method("https://opencode.ai/install", "bash"),
        ),
        login_hint="Run `opencode auth login` in a terminal",
        key_env=None,  # provider-specific: see opencode.PROVIDER_KEY_ENV
        # Unverified: `opencode auth login` is an interactive provider picker that stores its own login.
        headless_login_argv=("opencode", "auth", "login"),
    ),
    "antigravity": CliSpec(
        id="antigravity",
        display_name="Antigravity CLI",
        binaries=("agy",),
        known_dirs=(".local/bin",),
        npm_package=None,  # Google ships a shell installer, run as a downloaded file (never piped)
        install_methods=(script_method("https://antigravity.google/cli/install.sh", "bash"),),
        login_hint=(
            "Create a Gemini API key in Google AI Studio and add it here (no own-login mode: "
            "Foundry AI OS never writes into your own ~/.gemini)"
        ),
        auth_modes=("api_key",),
        key_env="GEMINI_API_KEY",
        manual_install="curl -fsSL https://antigravity.google/cli/install.sh | bash",
    ),
    "cursor": CliSpec(
        id="cursor",
        display_name="Cursor CLI",
        binaries=("agent",),
        known_dirs=(".local/bin",),
        npm_package=None,  # Cursor ships a shell installer, run as a downloaded file (never piped)
        install_methods=(script_method("https://cursor.com/install", "bash"),),
        login_hint=(
            "Create a Cursor API key in the Cursor Dashboard (API Keys) and add it here (no own-login "
            "mode: Foundry AI OS never writes into your own ~/.cursor)"
        ),
        auth_modes=("api_key",),
        key_env="CURSOR_API_KEY",
        manual_install="curl https://cursor.com/install -fsS | bash",
    ),
}


def spec_for(cli_id: str) -> CliSpec | None:
    return CLIS.get(cli_id)


INSTALL_INTERPRETERS = ("bash", "sh")
_ELEVATORS = ("sudo", "doas", "su", "pkexec", "runas")


def assert_safe_install_argv(argv: list[str], method: InstallMethod) -> None:
    """The install argv is exactly ``[npm, install, -g, <package>]`` or ``[bash|sh, <file>]``: no shell
    string (``-c``), no pipe, no privilege elevation."""
    from pathlib import PurePath

    assert_safe_argv(argv)
    if any(PurePath(a).name in _ELEVATORS for a in argv):
        raise ValueError("refusing to run an installer with elevated privileges")
    if method.kind == "npm":
        if len(argv) != 4 or PurePath(argv[0]).name != "npm" or argv[1:3] != ["install", "-g"]:
            raise ValueError("unexpected npm install argv")
    elif method.kind == "script":
        if (
            len(argv) != 2
            or PurePath(argv[0]).name not in INSTALL_INTERPRETERS
            or argv[1].startswith("-")
            or "|" in argv[1]
        ):
            raise ValueError("unexpected installer argv")
    else:
        raise ValueError("unknown install method")


def assert_safe_argv(argv: list[str]) -> None:
    """Refuse to spawn a CLI with any flag that disables its safety."""
    for arg in argv:
        for bad in FORBIDDEN_ARGV:
            if (
                arg == bad
                or (bad.startswith("--") and arg.startswith(bad + "="))
                or (not bad.startswith("--") and bad in arg)
            ):
                raise ValueError(f"refusing to start a CLI with {bad!r}")


def sandbox_for(cli_id: str, installed: bool) -> dict[str, object]:
    """Whether a CLI may run shell commands inside a real sandbox on this machine (spec M10 section 3).

    In M10 only Codex is ever given a shell (``--sandbox workspace-write``: Seatbelt on macOS, Landlock
    on Linux, from the documentation -- not exercised here because Codex is not installed). Claude Code's
    own sandbox cannot be verified by Foundry AI OS, so its shell tools stay off; OpenCode has no OS sandbox
    (its shell tool is denied by permission rules)."""
    import sys

    if cli_id == "codex":
        supported = sys.platform in ("darwin", "linux")
        return {
            "available": supported and installed,
            "kind": "os" if supported else "none",
            "detail": (
                "codex exec --sandbox workspace-write (Seatbelt on macOS, Landlock on Linux; network "
                "off). Taken from the documentation: not exercised on this machine."
                if supported
                else "No OS sandbox on this platform: Codex is not offered shell access here."
            ),
        }
    if cli_id == "claude_code":
        return {
            "available": False,
            "kind": "none",
            "detail": (
                "Shell tools (Bash) are never enabled: Foundry AI OS cannot verify Claude Code's sandbox. "
                "Only file tools are allowed, confined to the workspace (--restricted)."
            ),
        }
    if cli_id == "antigravity":
        return {
            "available": False,
            "kind": "none",
            "detail": (
                "Shell commands are denied by permission rules (command(*)), as are URL fetches; only "
                "workspace files and the company tools are allowed. agy's own terminal sandbox flag is "
                "on but was not exercised."
            ),
        }
    if cli_id == "cursor":
        return {
            "available": False,
            "kind": "none",
            "detail": (
                "Shell commands and web fetches are denied by permission rules (Shell(*), WebFetch(*)); "
                "only workspace files and the company tools are allowed. agent's own --sandbox enabled "
                "is on but was not exercised."
            ),
        }
    return {
        "available": False,
        "kind": "none",
        "detail": "No OS sandbox: OpenCode's shell, web and subagent tools are denied by permission rules.",
    }
