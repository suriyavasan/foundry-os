"""What Foundry AI OS knows about each supported coding CLI (static facts; the verified flags live in
``docs/cli-runtimes.md``)."""

from __future__ import annotations

from dataclasses import dataclass

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
)


@dataclass(frozen=True)
class CliSpec:
    id: str  # claude_code | codex | opencode
    display_name: str
    binaries: tuple[str, ...]  # executable names looked up on PATH and in extra locations
    known_dirs: tuple[str, ...]  # extra places a user-level install lands (relative to ~ if no /)
    npm_package: str
    login_hint: str
    auth_modes: tuple[str, ...] = ("own_login", "api_key")
    # Environment variable that carries an API key in api_key mode.
    key_env: str | None = None

    @property
    def runtime_id(self) -> str:
        return f"cli:{self.id}"

    @property
    def install_command(self) -> list[str]:
        return ["npm", "install", "-g", self.npm_package]


CLIS: dict[str, CliSpec] = {
    "claude_code": CliSpec(
        id="claude_code",
        display_name="Claude Code",
        binaries=("claude",),
        known_dirs=(".claude/local", ".local/bin", ".npm-global/bin"),
        npm_package="@anthropic-ai/claude-code",
        login_hint="Run `claude` in a terminal and sign in with /login",
        key_env="ANTHROPIC_API_KEY",
    ),
    "codex": CliSpec(
        id="codex",
        display_name="Codex CLI",
        binaries=("codex",),
        known_dirs=(".local/bin", ".npm-global/bin"),
        npm_package="@openai/codex",
        login_hint="Run `codex login` in a terminal",
        key_env="CODEX_API_KEY",
    ),
    "opencode": CliSpec(
        id="opencode",
        display_name="OpenCode",
        binaries=("opencode",),
        known_dirs=(".opencode/bin", ".local/bin", ".npm-global/bin"),
        npm_package="opencode-ai",
        login_hint="Run `opencode auth login` in a terminal",
        key_env=None,  # provider-specific: see opencode.PROVIDER_KEY_ENV
    ),
}


def spec_for(cli_id: str) -> CliSpec | None:
    return CLIS.get(cli_id)


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
    return {
        "available": False,
        "kind": "none",
        "detail": "No OS sandbox: OpenCode's shell, web and subagent tools are denied by permission rules.",
    }
