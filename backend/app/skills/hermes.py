"""Catalog of the Hermes bundled/optional skills in the runtime checkout
(``COMPANY_OS_RUNTIME_DIR``).

Discovery is a recursive search for ``SKILL.md`` below ``skills/`` and ``optional-skills/``; a
directory holding a SKILL.md is one skill (nothing nested is indexed). The frontmatter index is
cached in memory for a short time.
"""

from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from app.models.enums import SkillSourceKind
from app.skills.frontmatter import SkillFormatError, SkillMeta, parse_skill_md

BUNDLED_ROOT = "skills"
OPTIONAL_ROOT = "optional-skills"
FALLBACK_COMMIT = "db1fa153"  # upstream Hermes commit the fork was taken from
_SKIP_DIRS = frozenset({"index-cache", "node_modules", ".git", "__pycache__"})
CACHE_TTL_SECONDS = 30.0


@dataclass(frozen=True)
class HermesSkill:
    slug: str
    rel_path: str  # e.g. "skills/creative/claude-design"
    path: Path
    source_kind: str
    meta: SkillMeta

    def source_ref(self, commit: str) -> str:
        return f"hermes@{commit}:{self.rel_path}"


def upstream_commit(runtime_dir: Path) -> str:
    """Short upstream commit of the fork, read (not imported) from company_agent/__init__.py."""
    try:
        text = (runtime_dir / "company_agent" / "__init__.py").read_text(encoding="utf-8")
    except OSError:
        return FALLBACK_COMMIT
    match = re.search(r"UPSTREAM_COMMIT\s*=\s*[\"']([0-9a-f]{8,40})[\"']", text)
    return match.group(1)[:8] if match else FALLBACK_COMMIT


def _walk(root: Path, base: Path, kind: str) -> list[HermesSkill]:
    found: list[HermesSkill] = []
    stack = [root]
    while stack:
        current = stack.pop()
        if (current / "SKILL.md").is_file():
            try:
                text = (current / "SKILL.md").read_text(encoding="utf-8", errors="replace")
                parent = current.parent.name if current.parent != root else None
                meta = parse_skill_md(text, fallback_name=current.name, fallback_category=parent)
            except (OSError, SkillFormatError):
                continue
            found.append(
                HermesSkill(
                    slug=current.name,
                    rel_path=current.relative_to(base).as_posix(),
                    path=current,
                    source_kind=kind,
                    meta=meta,
                )
            )
            continue
        try:
            children = sorted(p for p in current.iterdir() if p.is_dir() and not p.is_symlink())
        except OSError:
            continue
        stack.extend(c for c in children if c.name not in _SKIP_DIRS and not c.name.startswith("."))
    return found


class HermesCatalog:
    _cache: dict[Path, tuple[float, list[HermesSkill]]] = {}
    _lock = threading.Lock()

    def __init__(self, runtime_dir: Path) -> None:
        self.runtime_dir = runtime_dir

    def available(self) -> bool:
        return (self.runtime_dir / BUNDLED_ROOT).is_dir() or (
            self.runtime_dir / OPTIONAL_ROOT
        ).is_dir()

    def commit(self) -> str:
        return upstream_commit(self.runtime_dir)

    def skills(self, *, refresh: bool = False) -> list[HermesSkill]:
        key = self.runtime_dir.resolve() if self.runtime_dir.exists() else self.runtime_dir
        now = time.monotonic()
        with self._lock:
            hit = self._cache.get(key)
            if hit and not refresh and now - hit[0] < CACHE_TTL_SECONDS:
                return hit[1]
        found: list[HermesSkill] = []
        for sub, kind in (
            (BUNDLED_ROOT, SkillSourceKind.HERMES_BUNDLED),
            (OPTIONAL_ROOT, SkillSourceKind.HERMES_OPTIONAL),
        ):
            root = self.runtime_dir / sub
            if root.is_dir():
                found.extend(_walk(root, self.runtime_dir, kind))
        found.sort(key=lambda s: (s.slug, s.rel_path))
        with self._lock:
            self._cache[key] = (now, found)
        return found

    def find(self, ref: str) -> HermesSkill | None:
        """Resolve ``<slug>`` or a relative path such as ``skills/creative/claude-design``.
        Bundled skills win over optional ones of the same slug."""
        ref = ref.strip().strip("/")
        skills = self.skills()
        by_path = next((s for s in skills if s.rel_path == ref), None)
        if by_path:
            return by_path
        matches = [s for s in skills if s.slug == ref]
        matches.sort(key=lambda s: s.source_kind != SkillSourceKind.HERMES_BUNDLED)
        return matches[0] if matches else None

    def search(self, query: str) -> list[HermesSkill]:
        words = [w for w in re.split(r"\s+", query.lower().strip()) if w]
        out = []
        for s in self.skills():
            haystack = " ".join(
                [s.slug, s.meta.name, s.meta.description, s.meta.category or "", *s.meta.tags]
            ).lower()
            if all(w in haystack for w in words):
                out.append(s)
        return out
