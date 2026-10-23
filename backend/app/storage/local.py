from __future__ import annotations

import hashlib
import os
import re
import tempfile
from collections.abc import Iterable, Iterator
from pathlib import Path

from app.core.errors import PayloadTooLarge
from app.storage.base import StoredObject

_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class LocalFileStorage:
    """Filesystem storage. Keys are ``{company_id}/{artifact_id}/{version}``; every segment is
    validated so a key can never escape ``root``."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def _path(self, key: str) -> Path:
        parts = key.split("/")
        if not parts or any(not _SEGMENT.match(p) or p in (".", "..") for p in parts):
            raise ValueError(f"invalid storage key: {key!r}")
        return self.root.joinpath(*parts)

    def put(
        self, key: str, chunks: Iterable[bytes], *, max_bytes: int | None = None
    ) -> StoredObject:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256()
        size = 0
        fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=".upload-")
        try:
            with os.fdopen(fd, "wb") as fh:
                for chunk in chunks:
                    size += len(chunk)
                    if max_bytes is not None and size > max_bytes:
                        raise PayloadTooLarge(
                            f"Upload exceeds the {max_bytes} byte limit",
                            details={"max_bytes": max_bytes},
                        )
                    digest.update(chunk)
                    fh.write(chunk)
            os.replace(tmp_name, path)
        except BaseException:
            Path(tmp_name).unlink(missing_ok=True)
            raise
        return StoredObject(key=key, size_bytes=size, sha256=digest.hexdigest())

    def open(self, key: str, chunk_size: int = 64 * 1024) -> Iterator[bytes]:
        path = self._path(key)
        with path.open("rb") as fh:
            while chunk := fh.read(chunk_size):
                yield chunk

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)
