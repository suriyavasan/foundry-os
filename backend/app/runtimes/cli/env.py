"""Finding and probing the CLIs on this machine. Injectable so tests use fake binaries."""

from __future__ import annotations

import contextlib
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from app.runtimes.cli.catalog import CliSpec

SAFE_ENV_KEYS = ("PATH", "LANG", "LC_ALL", "LC_CTYPE", "TZ", "SSL_CERT_FILE", "SSL_CERT_DIR")
PROXY_ENV_KEYS = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "NO_PROXY",
    "ALL_PROXY",
    "http_proxy",
    "https_proxy",
    "no_proxy",
    "all_proxy",
)


def base_env() -> dict[str, str]:
    """The only variables a CLI child inherits from the server (no secrets, no COMPANY_OS_*)."""
    return {k: os.environ[k] for k in (*SAFE_ENV_KEYS, *PROXY_ENV_KEYS) if k in os.environ}


CLAUDE_CONFIG_DIRNAME = "claude-code"


def claude_config_dir(data_dir: Path) -> Path:
    """Foundry's own Claude Code configuration directory (``<data dir>/claude-code``, 0700). It is the
    ``CLAUDE_CONFIG_DIR`` of every own-login Claude Code process, so the person's own ``~/.claude`` is never
    used or touched."""
    path = Path(data_dir) / CLAUDE_CONFIG_DIRNAME
    path.mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(OSError):
        os.chmod(path, 0o700)
    return path


@dataclass
class CliEnvironment:
    """Where to look for binaries. ``extra_dirs`` come first (tests point it at a temp dir)."""

    extra_dirs: list[Path] = field(default_factory=list)
    home: Path = field(default_factory=Path.home)
    # False: look only in ``extra_dirs`` (tests use this so a developer's real CLIs never matter).
    use_system_path: bool = True
    # The installation's data directory (set by the app); locates Foundry's Claude Code config dir.
    data_dir: Path | None = None

    def search_path(self, spec: CliSpec | None = None) -> str:
        dirs = [str(d) for d in self.extra_dirs]
        if not self.use_system_path:
            return os.pathsep.join(dict.fromkeys(dirs + ["/usr/bin", "/bin"]))
        if spec is not None:
            dirs += [str(self.home / d) if not d.startswith("/") else d for d in spec.known_dirs]
        dirs += [d for d in os.environ.get("PATH", "").split(os.pathsep) if d]
        for fixed in ("/opt/homebrew/bin", "/usr/local/bin"):
            if fixed not in dirs:
                dirs.append(fixed)
        return os.pathsep.join(dict.fromkeys(dirs))

    def find_binary(self, spec: CliSpec) -> str | None:
        path = self.search_path(spec)
        for name in spec.binaries:
            found = shutil.which(name, path=path)
            if found:
                return str(Path(found))
        return None

    def find_tool(self, name: str) -> str | None:
        found = shutil.which(name, path=self.search_path())
        return str(Path(found)) if found else None

    def version(self, binary: str, *, timeout: float = 10.0) -> str | None:
        """``<binary> --version`` with a minimal environment (never starts a session)."""
        try:
            done = subprocess.run(
                [binary, "--version"],
                capture_output=True,
                text=True,
                timeout=timeout,
                stdin=subprocess.DEVNULL,
                env={**base_env(), "PATH": self.search_path()},
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        out = (done.stdout or done.stderr or "").strip().splitlines()
        return out[0].strip()[:120] if out and done.returncode == 0 else None
