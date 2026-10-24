"""The server log: where it is, rotation, and ``foundryos logs``.

Background and service runs append everything to ``<data dir>/logs/foundry-os.log`` (the file is opened
by the supervising process: ``launchd`` / ``systemd`` / the detached ``start``). Because that file
descriptor stays open for the life of the process, rotation is *copy and truncate*: the current content
is copied to ``foundry-os.log.1`` (older ones shift up, the oldest is dropped) and the live file is
emptied in place.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import threading
import time
from collections import deque
from collections.abc import Callable
from pathlib import Path

from app.core import install
from app.core.config import Settings

LOG_NAME = "foundry-os.log"
MAX_BYTES = 10 * 1024 * 1024
KEEP = 5
CHECK_EVERY_SECONDS = 300.0


def log_path(settings: Settings) -> Path:
    explicit = os.environ.get("FOUNDRYOS_LOG_FILE", "").strip()
    if explicit:
        return Path(explicit).expanduser()
    return settings.logs_dir / LOG_NAME


def candidate_logs(settings: Settings) -> list[Path]:
    """Every place a running copy may write its log; ``foundryos logs`` shows the freshest one."""
    paths = [log_path(settings), settings.logs_dir / LOG_NAME]
    brew = install.homebrew_prefix()
    if brew is not None:
        paths.append(brew / "var" / "log" / "foundry-os.log")
    seen: list[Path] = []
    for p in paths:
        if p not in seen:
            seen.append(p)
    return seen


def pick_log(settings: Settings) -> Path | None:
    existing = [p for p in candidate_logs(settings) if p.is_file()]
    if not existing:
        return None
    return max(existing, key=lambda p: p.stat().st_mtime)


def rotate(path: Path, *, keep: int = KEEP) -> None:
    """Copy ``path`` to ``path.1`` (shifting older copies) and empty ``path`` in place."""
    oldest = path.with_name(f"{path.name}.{keep}")
    with contextlib.suppress(OSError):
        oldest.unlink()
    for n in range(keep - 1, 0, -1):
        src = path.with_name(f"{path.name}.{n}")
        if src.exists():
            os.replace(src, path.with_name(f"{path.name}.{n + 1}"))
    shutil.copyfile(path, path.with_name(f"{path.name}.1"))
    with path.open("r+b") as fh:
        fh.truncate(0)


def rotate_if_large(path: Path, *, max_bytes: int = MAX_BYTES, keep: int = KEEP) -> bool:
    try:
        if path.stat().st_size < max_bytes:
            return False
        rotate(path, keep=keep)
    except OSError:
        return False
    return True


class Rotator:
    """Daemon thread that rotates the log now and then while the server runs."""

    def __init__(
        self,
        path: Path,
        *,
        max_bytes: int = MAX_BYTES,
        keep: int = KEEP,
        every: float = CHECK_EVERY_SECONDS,
    ) -> None:
        self.path, self.max_bytes, self.keep, self.every = path, max_bytes, keep, every
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="log-rotation", daemon=True)

    def start(self) -> Rotator:
        rotate_if_large(self.path, max_bytes=self.max_bytes, keep=self.keep)
        self._thread.start()
        return self

    def _run(self) -> None:
        while not self._stop.wait(self.every):
            rotate_if_large(self.path, max_bytes=self.max_bytes, keep=self.keep)

    def stop(self) -> None:
        self._stop.set()


def tail_lines(path: Path, count: int) -> list[str]:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            return [line.rstrip("\n") for line in deque(fh, maxlen=max(count, 0))]
    except OSError:
        return []


def follow(
    path: Path,
    emit: Callable[[str], None],
    *,
    poll: float = 0.5,
    stop: Callable[[], bool] = lambda: False,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """``tail -f``: print what is appended; start over when the file is rotated (truncated)."""
    try:
        fh = path.open("r", encoding="utf-8", errors="replace")
    except OSError:
        return
    with fh:
        fh.seek(0, os.SEEK_END)
        while not stop():
            where = fh.tell()
            line = fh.readline()
            if line:
                emit(line.rstrip("\n"))
                continue
            try:
                if path.stat().st_size < where:
                    fh.seek(0)
                    continue
            except OSError:
                pass
            sleep(poll)
