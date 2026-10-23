"""Skill normalization and the content-addressed blob store.

A skill is normalized into an in-memory file listing (limits enforced), hashed over a canonical
listing (sorted relative path + content) and stored once at ``<root>/<sha256>/``. Nothing from
the source is executed or trusted: symlinks, traversal, oversized skills and native binaries are
refused; executable bits survive only for text scripts under ``scripts/``.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

MAX_TOTAL_BYTES = 2 * 1024 * 1024
MAX_FILES = 100
MAX_PATH_LENGTH = 200
SKILL_FILE = "SKILL.md"
# Dropped silently: VCS/editor noise, and the guard's ignore files (they could hide files
# from the scan).
_SKIP_DIRS = frozenset({".git", ".github", "__pycache__", ".scan-cache", "node_modules", ".hub"})
_SKIP_FILES = frozenset({".DS_Store", ".skillignore", ".clawhubignore", "Thumbs.db"})
_BINARY_MAGIC = (
    b"\x7fELF",
    b"MZ",
    b"\xfe\xed\xfa\xce",
    b"\xfe\xed\xfa\xcf",
    b"\xce\xfa\xed\xfe",
    b"\xcf\xfa\xed\xfe",
    b"\xca\xfe\xba\xbe",
)


class SkillRejected(ValueError):
    """The skill violates a storage rule (limits, symlinks, unsafe paths, native binaries)."""


@dataclass(frozen=True)
class SkillFile:
    path: str  # POSIX, relative to the skill root
    data: bytes
    executable: bool = False


@dataclass(frozen=True)
class NormalizedSkill:
    files: tuple[SkillFile, ...]
    sha256: str
    size_bytes: int

    @property
    def file_count(self) -> int:
        return len(self.files)

    def read(self, path: str) -> bytes | None:
        return next((f.data for f in self.files if f.path == path), None)

    def write_to(self, dest: Path) -> None:
        write_files(self.files, dest)


@dataclass(frozen=True)
class BlobInfo:
    sha256: str
    size_bytes: int
    file_count: int


def hash_files(files: list[tuple[str, bytes]] | tuple[tuple[str, bytes], ...]) -> str:
    """sha256 over a canonical listing: for each file sorted by path, ``path NUL size NUL data``."""
    digest = hashlib.sha256()
    for path, data in sorted(files, key=lambda item: item[0]):
        digest.update(path.encode("utf-8") + b"\0" + str(len(data)).encode() + b"\0")
        digest.update(data)
    return digest.hexdigest()


def _check_path(raw: str) -> str:
    if not raw or "\0" in raw or "\\" in raw or len(raw) > MAX_PATH_LENGTH:
        raise SkillRejected(f"unsafe file path: {raw[:60]!r}")
    pure = PurePosixPath(raw)
    if (
        pure.is_absolute()
        or pure.as_posix() != raw  # not canonical: "x/./y", "x//y", trailing "/"
        or any(part in ("", ".", "..") for part in pure.parts)
    ):
        raise SkillRejected(f"unsafe file path: {raw[:60]!r}")
    if any(part.lower().startswith(".env") for part in pure.parts):
        raise SkillRejected(f"environment files are not allowed in skills: {raw[:60]!r}")
    return pure.as_posix()


def _check_content(path: str, data: bytes) -> None:
    if data.startswith(_BINARY_MAGIC):
        raise SkillRejected(f"native executable content is not allowed: {path}")


def normalize_files(entries: list[SkillFile] | list[tuple[str, bytes, bool]]) -> NormalizedSkill:
    """Validate and canonicalize a file listing (used for directories, GitHub downloads and
    authored skills alike). Raises ``SkillRejected``."""
    files: dict[str, SkillFile] = {}
    total = 0
    for entry in entries:
        f = entry if isinstance(entry, SkillFile) else SkillFile(*entry)
        path = _check_path(f.path)
        parts = PurePosixPath(path).parts
        if parts[-1] in _SKIP_FILES or any(p in _SKIP_DIRS for p in parts[:-1]):
            continue
        if path in files:
            raise SkillRejected(f"duplicate file path: {path}")
        _check_content(path, f.data)
        total += len(f.data)
        if total > MAX_TOTAL_BYTES:
            raise SkillRejected(f"skill exceeds the {MAX_TOTAL_BYTES // (1024 * 1024)} MB limit")
        if len(files) + 1 > MAX_FILES:
            raise SkillRejected(f"skill has more than {MAX_FILES} files")
        # Executables only under scripts/ (text scripts); everywhere else the bit is cleared.
        executable = f.executable and parts[0] == "scripts" and len(parts) > 1
        files[path] = SkillFile(path, f.data, executable)
    if SKILL_FILE not in files:
        raise SkillRejected("the skill has no SKILL.md at its root")
    ordered = tuple(files[p] for p in sorted(files))
    return NormalizedSkill(
        files=ordered,
        sha256=hash_files([(f.path, f.data) for f in ordered]),
        size_bytes=total,
    )


def normalize_dir(src: Path) -> NormalizedSkill:
    """Read a skill directory (never following symlinks) into a ``NormalizedSkill``."""
    if not src.is_dir() or src.is_symlink():
        raise SkillRejected("skill source is not a directory")
    entries: list[SkillFile] = []
    total = 0
    for root, dirs, names in os.walk(src, followlinks=False):
        root_path = Path(root)
        kept: list[str] = []
        for d in sorted(dirs):
            if (root_path / d).is_symlink():
                raise SkillRejected(f"symlinks are not allowed: {(root_path / d).relative_to(src)}")
            if d not in _SKIP_DIRS:
                kept.append(d)
        dirs[:] = kept
        for name in sorted(names):
            path = root_path / name
            rel = path.relative_to(src).as_posix()
            if path.is_symlink():
                raise SkillRejected(f"symlinks are not allowed: {rel}")
            if name in _SKIP_FILES:
                continue
            st = path.lstat()
            if not stat.S_ISREG(st.st_mode):
                raise SkillRejected(f"not a regular file: {rel}")
            total += st.st_size
            if total > MAX_TOTAL_BYTES or len(entries) >= MAX_FILES:
                raise SkillRejected("skill exceeds the size or file-count limits")
            entries.append(SkillFile(rel, path.read_bytes(), bool(st.st_mode & 0o111)))
    return normalize_files(entries)


def write_files(files: tuple[SkillFile, ...] | list[SkillFile], dest: Path) -> None:
    for f in files:
        target = dest / f.path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(f.data)
        target.chmod(0o755 if f.executable else 0o644)


def hash_dir(path: Path) -> str | None:
    """Canonical hash of an on-disk skill directory (None if it cannot be read as one)."""
    if not path.is_dir():
        return None
    try:
        listing: list[tuple[str, bytes]] = []
        for root, _dirs, names in os.walk(path, followlinks=False):
            for name in names:
                p = Path(root) / name
                if p.is_symlink():
                    return None
                listing.append((p.relative_to(path).as_posix(), p.read_bytes()))
        return hash_files(listing)
    except OSError:
        return None


class BlobStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def path(self, sha256: str) -> Path:
        if len(sha256) != 64 or any(c not in "0123456789abcdef" for c in sha256):
            raise SkillRejected("invalid blob hash")
        return self.root / sha256

    def exists(self, sha256: str) -> bool:
        return (self.path(sha256) / SKILL_FILE).is_file()

    def put(self, skill: NormalizedSkill) -> BlobInfo:
        """Store the skill (idempotent: an existing intact blob is reused)."""
        dest = self.path(skill.sha256)
        info = BlobInfo(skill.sha256, skill.size_bytes, skill.file_count)
        if self.exists(skill.sha256) and hash_dir(dest) == skill.sha256:
            return info
        self.root.mkdir(parents=True, exist_ok=True)
        tmp = Path(tempfile.mkdtemp(prefix=".tmp-", dir=self.root))
        try:
            skill.write_to(tmp)
            tmp.chmod(0o755)
            if dest.exists():  # corrupt or partial leftover: replace it
                shutil.rmtree(dest, ignore_errors=True)
            try:
                os.replace(tmp, dest)
            except OSError:
                if not self.exists(skill.sha256):  # lost a race that did not produce a blob
                    raise
                shutil.rmtree(tmp, ignore_errors=True)
        except BaseException:
            shutil.rmtree(tmp, ignore_errors=True)
            raise
        return info

    def verify(self, sha256: str) -> bool:
        return hash_dir(self.path(sha256)) == sha256

    def list_files(self, sha256: str) -> list[dict[str, int | str]]:
        base = self.path(sha256)
        out: list[dict[str, int | str]] = []
        for p in sorted(base.rglob("*")):
            if p.is_file() and not p.is_symlink():
                out.append({"path": p.relative_to(base).as_posix(), "size_bytes": p.stat().st_size})
        return out

    def read_text(
        self, sha256: str, rel_path: str, *, max_bytes: int = 200_000
    ) -> tuple[str, bool]:
        """Text preview of one file. Raises ``SkillRejected`` for unsafe paths,
        ``FileNotFoundError``
        when the file does not exist, ``UnicodeDecodeError`` for binary content."""
        rel = _check_path(rel_path)
        base = self.path(sha256).resolve()
        target = (base / rel).resolve()
        if not target.is_relative_to(base) or not target.is_file():
            raise FileNotFoundError(rel)
        with target.open("rb") as fh:
            data = fh.read(max_bytes + 1)
        truncated = len(data) > max_bytes
        return data[:max_bytes].decode("utf-8"), truncated
