"""Artifact content storage interface. Content never lives in the database; the DB keeps
metadata plus a ``storage_key``. Swap the implementation (S3, ...) without touching services."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class StoredObject:
    key: str
    size_bytes: int
    sha256: str


class ArtifactStorage(Protocol):
    def put(
        self, key: str, chunks: Iterable[bytes], *, max_bytes: int | None = None
    ) -> StoredObject:
        """Store ``chunks`` under ``key``. Raises ``PayloadTooLarge`` past ``max_bytes``
        (nothing is kept in that case)."""
        ...

    def open(self, key: str, chunk_size: int = 64 * 1024) -> Iterator[bytes]: ...

    def exists(self, key: str) -> bool: ...

    def delete(self, key: str) -> None: ...
