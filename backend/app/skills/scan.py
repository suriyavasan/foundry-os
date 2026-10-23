"""Security scan of skills through the runtime's own skills guard, plus Company OS' install policy.

The guard runs as a subprocess under the runtime's virtualenv (``python -m
company_agent.skills_scan <dir>``); the backend never imports Hermes code.
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from app.models.enums import ScanVerdict, SkillTrust

# Mirror of runtime/tools/skills_guard.py (TRUSTED_REPOS, INSTALL_POLICY).
TRUSTED_REPOS = frozenset(
    {"openai/skills", "anthropics/skills", "huggingface/skills", "nvidia/skills"}
)
_INDEX = {ScanVerdict.SAFE: 0, ScanVerdict.CAUTION: 1, ScanVerdict.DANGEROUS: 2}
INSTALL_POLICY: dict[str, tuple[bool, bool, bool]] = {
    # allowed for verdict:           safe  caution dangerous
    SkillTrust.BUILTIN: (True, True, True),
    SkillTrust.TRUSTED: (True, True, False),
    SkillTrust.COMMUNITY: (True, False, False),
}
MAX_FINDINGS_STORED = 100


class ScannerUnavailable(RuntimeError):
    """The runtime (python/venv/guard) is not available: non-builtin imports must be refused."""


class ScanFailed(RuntimeError):
    """The scanner ran but did not produce a usable report."""


@dataclass
class ScanReport:
    verdict: str
    findings: list[dict[str, Any]] = field(default_factory=list)
    scanner_version: str | None = None
    summary: str = ""

    def to_json(self) -> dict[str, Any]:
        """Compact, secret-free summary stored on the skill (findings only: no file content)."""
        return {
            "verdict": self.verdict,
            "scanner_version": self.scanner_version,
            "summary": self.summary[:500],
            "finding_count": len(self.findings),
            "findings": [
                {
                    "category": f.get("category"),
                    "pattern": f.get("pattern"),
                    "severity": f.get("severity"),
                    "file": f.get("file"),
                    "line": f.get("line"),
                    "description": str(f.get("description", ""))[:200],
                }
                for f in self.findings[:MAX_FINDINGS_STORED]
            ],
        }


UNSCANNED = ScanReport(verdict=ScanVerdict.UNSCANNED, summary="not scanned")


class SkillScanner(Protocol):
    def available(self) -> bool: ...

    def scan(self, path: Path, *, source: str) -> ScanReport: ...


class RuntimeScanner:
    def __init__(self, python: Path, runtime_dir: Path, timeout: float = 120.0) -> None:
        self.python = python
        self.runtime_dir = runtime_dir
        self.timeout = timeout

    def available(self) -> bool:
        return (
            self.python.is_file()
            and os.access(self.python, os.X_OK)
            and (self.runtime_dir / "company_agent" / "skills_scan.py").is_file()
            and (self.runtime_dir / "tools" / "skills_guard.py").is_file()
        )

    def scan(self, path: Path, *, source: str) -> ScanReport:
        if not self.available():
            raise ScannerUnavailable("the runtime Python environment is not installed")
        env = {
            "PATH": os.environ.get("PATH", os.defpath),
            "LANG": "C.UTF-8",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONNOUSERSITE": "1",
        }
        try:
            proc = subprocess.run(
                [
                    str(self.python),
                    "-m",
                    "company_agent.skills_scan",
                    # "=" form: a source starting with "-" must not parse as an option
                    f"--source={source}",
                    str(path),
                ],
                cwd=self.runtime_dir,
                env=env,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ScanFailed(f"scanner did not run: {type(exc).__name__}") from exc
        return parse_scan_output(proc.returncode, proc.stdout)


def parse_scan_output(returncode: int, stdout: str) -> ScanReport:
    try:
        data = json.loads(stdout)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ScanFailed(f"scanner exited {returncode} without a JSON report") from exc
    if returncode != 0 or not isinstance(data, dict) or "error" in data:
        message = data.get("message") if isinstance(data, dict) else None
        raise ScanFailed(f"scanner failed ({returncode}): {str(message or '')[:200]}")
    verdict = data.get("verdict")
    if verdict not in (ScanVerdict.SAFE, ScanVerdict.CAUTION, ScanVerdict.DANGEROUS):
        raise ScanFailed("scanner returned an unknown verdict")
    findings = data.get("findings")
    return ScanReport(
        verdict=verdict,
        findings=[f for f in findings if isinstance(f, dict)] if isinstance(findings, list) else [],
        scanner_version=str(data.get("scanner_version") or "") or None,
        summary=str(data.get("summary") or ""),
    )


def allowed_by_policy(trust: str, verdict: str) -> bool:
    """Company OS install policy: may a skill of this trust level with this verdict be active?"""
    if verdict == ScanVerdict.UNSCANNED:
        return trust == SkillTrust.BUILTIN
    return INSTALL_POLICY[SkillTrust(trust)][_INDEX[ScanVerdict(verdict)]]


def trust_for_github(owner: str, repo: str) -> str:
    return (
        SkillTrust.TRUSTED if f"{owner}/{repo}".lower() in TRUSTED_REPOS else SkillTrust.COMMUNITY
    )
