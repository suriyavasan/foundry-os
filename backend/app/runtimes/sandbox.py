"""Sandbox availability for code-running tools (terminal / code_execution).

``auto`` = docker when the daemon answers, else ``none``; with ``none`` those toolsets are never
granted (the launcher removes them too, as a second independent check).
"""

from __future__ import annotations

import re
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass

CACHE_SECONDS = 20.0
_VERSION = re.compile(r"^\d+(\.\d+)+")


@dataclass(frozen=True)
class DockerStatus:
    reachable: bool
    detail: str


class DockerProbe:
    """Cached ``docker info`` probe (the CLI can hang when the daemon is half-started)."""

    def __init__(self, ttl: float = CACHE_SECONDS, timeout: float = 4.0) -> None:
        self._ttl = ttl
        self._timeout = timeout
        self._lock = threading.Lock()
        self._cached: tuple[float, DockerStatus] | None = None

    def status(self) -> DockerStatus:
        now = time.monotonic()
        with self._lock:
            if self._cached and now - self._cached[0] < self._ttl:
                return self._cached[1]
        result = self._probe()
        with self._lock:
            self._cached = (now, result)
        return result

    def _probe(self) -> DockerStatus:
        exe = shutil.which("docker")
        if exe is None:
            return DockerStatus(False, "docker CLI not found")
        try:
            done = subprocess.run(
                [exe, "info", "--format", "{{.ServerVersion}}"],
                capture_output=True,
                text=True,
                timeout=self._timeout,
                stdin=subprocess.DEVNULL,
            )
        except (OSError, subprocess.TimeoutExpired):
            return DockerStatus(False, "docker daemon did not answer")
        # The CLI exits 0 and prints "Cannot connect to the Docker daemon ..." on STDOUT when the
        # daemon is down (found by the live run): only a version-looking answer counts.
        version = done.stdout.strip()
        if done.returncode != 0 or not _VERSION.match(version):
            return DockerStatus(False, "docker daemon not reachable")
        return DockerStatus(True, f"docker daemon {version}")


def resolve_backend(policy_value: str, docker: DockerStatus) -> str | None:
    """Effective sandbox: "docker" | "none". None = policy demands docker but it is unavailable."""
    if policy_value == "none":
        return "none"
    if policy_value == "docker":
        return "docker" if docker.reachable else None
    return "docker" if docker.reachable else "none"
