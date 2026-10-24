"""Which Company OS processes are using a data directory right now.

Each long-running process (API, worker) drops ``<data dir>/run/<role>.<pid>.json`` while it lives.
``restore`` refuses to touch a data directory that has a live process; ``doctor`` reports them.
"""

from __future__ import annotations

import atexit
import contextlib
import json
import os
import time
from pathlib import Path

RUN_DIR = "run"


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, owned by someone else
    except OSError:
        return False
    return True


def register(data_dir: Path, role: str, *, port: int | None = None) -> Path | None:
    """Create this process's run file. Best effort: a read-only data dir must not stop the server."""
    try:
        run = data_dir / RUN_DIR
        run.mkdir(parents=True, exist_ok=True)
        path = run / f"{role}.{os.getpid()}.json"
        path.write_text(
            json.dumps(
                {"role": role, "pid": os.getpid(), "port": port, "started_at": int(time.time())}
            ),
            encoding="utf-8",
        )
    except OSError:
        return None
    atexit.register(unregister, path)
    return path


def unregister(path: Path | None) -> None:
    if path is not None:
        with contextlib.suppress(OSError):
            path.unlink()


def live_processes(data_dir: Path) -> list[dict]:
    """Processes whose run file names a pid that is still alive (stale files are deleted)."""
    run = data_dir / RUN_DIR
    found: list[dict] = []
    try:
        entries = sorted(run.glob("*.json"))
    except OSError:
        return found
    for entry in entries:
        try:
            info = json.loads(entry.read_text(encoding="utf-8"))
            pid = int(info["pid"])
        except (OSError, ValueError, KeyError, TypeError):
            with contextlib.suppress(OSError):
                entry.unlink()
            continue
        if _alive(pid):
            found.append({**info, "file": str(entry)})
        else:
            with contextlib.suppress(OSError):
                entry.unlink()
    return found
