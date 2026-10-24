"""Workspace snapshots: what the CLI produced when it did not submit artifacts itself."""

from __future__ import annotations

import hashlib
import mimetypes
import os
from dataclasses import dataclass
from pathlib import Path

from app.runtimes.base import ArtifactRef

MAX_FILES = 20
MAX_FILE_BYTES = 1_000_000
SKIP_DIRS = {".git", "node_modules", ".venv", "__pycache__", ".opencode", ".claude", ".codex"}


@dataclass(frozen=True)
class Snapshot:
    files: dict[str, str]  # relative path -> sha256


def _walk(root: Path):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for name in sorted(filenames):
            path = Path(dirpath) / name
            if path.is_symlink() or not path.is_file():
                continue  # a link could point outside the workspace
            yield path


def snapshot(root: str | Path) -> Snapshot:
    base = Path(root)
    files: dict[str, str] = {}
    if not base.is_dir():
        return Snapshot(files)
    for path in _walk(base):
        try:
            if path.stat().st_size > MAX_FILE_BYTES * 4:
                continue
            files[str(path.relative_to(base))] = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            continue
    return Snapshot(files)


def changed_files(root: str | Path, before: Snapshot) -> list[ArtifactRef]:
    """New or modified small files since ``before``, as artifacts (text only, at most MAX_FILES)."""
    base = Path(root)
    after = snapshot(base)
    out: list[ArtifactRef] = []
    for rel, digest in after.files.items():
        if before.files.get(rel) == digest:
            continue
        path = base / rel
        try:
            data = path.read_bytes()
        except OSError:
            continue
        if len(data) > MAX_FILE_BYTES:
            continue
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            continue  # binary files are left in the workspace, not attached
        mime = mimetypes.guess_type(rel)[0] or "text/plain"
        kind = (
            "code"
            if Path(rel).suffix in {".py", ".js", ".ts", ".go", ".rs", ".java", ".c"}
            else "document"
        )
        out.append(ArtifactRef(title=rel[:200], content=text, kind=kind, mime_type=mime))
        if len(out) >= MAX_FILES:
            break
    return out
