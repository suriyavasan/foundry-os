"""Skills guard wrapper: ``python -m company_agent.skills_scan <skill_dir> [--source SRC]``.

Runs the Hermes skills guard (``tools/skills_guard.py``, regex static scan) on one skill directory
and prints ONE JSON document on stdout::

    {"verdict": "safe|caution|dangerous", "scanner_version": "...", "trust_level": "...",
     "summary": "...", "allowed": true|false|null, "reason": "...",
     "findings": [{"category", "pattern", "file", "line", "severity", "description", "match"}]}

The control plane (``backend/app/skills/scan.py``) runs this under the runtime's own virtualenv and
applies Company OS' install policy on top of the verdict; ``allowed``/``reason`` here are the guard's own
opinion for the given ``--source`` and are informational only.

``--source`` is the guard's source id (default ``community``): ``official`` -> builtin trust,
``owner/repo[/path]`` of a trusted repo -> trusted, anything else -> community. It never reaches the
network and never executes anything from the scanned directory. Exit codes: 0 scanned (any verdict),
2 usage error / not a directory, 3 the guard could not be imported.

Stdlib only; importing this module must not import Hermes beyond ``tools.skills_guard``.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Optional, Sequence

RUNTIME_ROOT = Path(__file__).resolve().parent.parent

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_GUARD_UNAVAILABLE = 3


def _load_guard() -> Any:
    """Import ``tools.skills_guard`` from THIS runtime tree (never from an installed package)."""
    root = str(RUNTIME_ROOT)
    if root not in sys.path:
        sys.path.insert(0, root)
    from tools import skills_guard  # noqa: PLC0415 - deliberate late import

    return skills_guard


def scan_directory(skill_dir: Path, source: str = "community") -> dict[str, Any]:
    """Scan ``skill_dir`` and return the JSON-serialisable report (see module docstring)."""
    guard = _load_guard()
    result = guard.scan_skill(skill_dir, source=source)
    allowed, reason = guard.should_allow_install(result)
    return {
        "verdict": result.verdict,
        "scanner_version": guard.SCANNER_VERSION,
        "trust_level": result.trust_level,
        "summary": result.summary,
        "allowed": allowed,
        "reason": reason,
        "findings": [
            {
                "category": f.category,
                "pattern": f.pattern_id,
                "file": f.file,
                "line": f.line,
                "severity": f.severity,
                "description": f.description,
                "match": f.match,
            }
            for f in result.findings
        ],
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m company_agent.skills_scan",
        description="Scan one skill directory with the Hermes skills guard; prints JSON.",
    )
    parser.add_argument("skill_dir", help="directory containing SKILL.md")
    parser.add_argument(
        "--source",
        default="community",
        help="guard source id: 'official' (builtin), 'owner/repo[/path]' (trusted repos), else community",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    try:
        args = _parser().parse_args(argv)
    except SystemExit as exc:  # argparse already printed usage to stderr
        return EXIT_USAGE if exc.code else EXIT_OK
    skill_dir = Path(args.skill_dir)
    if not skill_dir.is_dir():
        print(json.dumps({"error": "not_a_directory", "message": "skill_dir is not a directory"}))
        return EXIT_USAGE
    try:
        report = scan_directory(skill_dir, args.source)
    except ImportError as exc:
        print(json.dumps({"error": "guard_unavailable", "message": f"cannot import skills guard: {exc}"}))
        return EXIT_GUARD_UNAVAILABLE
    print(json.dumps(report))
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
