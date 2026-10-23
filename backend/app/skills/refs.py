"""Parsing of skill ``source_ref`` strings accepted by the install endpoints.

Accepted: ``hermes:<slug|path>``, ``hermes@<sha>:<path>`` (the canonical stored form),
``github:<owner>/<repo>[@<ref>]:<path>`` and ``https://github.com/<owner>/<repo>/tree/<ref>/<path>``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath

_NAME = r"[A-Za-z0-9_.-]{1,100}"
_REF = r"[A-Za-z0-9_./+-]{1,200}"
_GITHUB = re.compile(rf"^github:({_NAME})/({_NAME})(?:@({_REF}))?(?::(.*))?$")
_URL = re.compile(
    rf"^https://github\.com/({_NAME})/({_NAME})(?:/(?:tree|blob)/([A-Za-z0-9_.+-]{{1,200}})(?:/(.*))?)?/?$"
)
_HERMES = re.compile(r"^hermes(?:@[0-9a-f]{7,40})?:(.+)$")


class BadSourceRef(ValueError):
    pass


@dataclass(frozen=True)
class HermesRef:
    target: str


@dataclass(frozen=True)
class GithubRef:
    owner: str
    repo: str
    ref: str | None
    path: str  # skill directory inside the repo ("" = repository root)

    def canonical(self, commit_sha: str) -> str:
        return f"github:{self.owner}/{self.repo}@{commit_sha}:{self.path}"


def _clean_path(raw: str | None) -> str:
    if (raw or "").startswith("/"):
        raise BadSourceRef("invalid path in source_ref")
    path = (raw or "").strip().strip("/")
    if path in ("", "."):
        return ""
    pure = PurePosixPath(path)
    if (
        "\\" in path
        or "\0" in path
        or pure.is_absolute()
        or any(p in ("", ".", "..") for p in pure.parts)
        or len(path) > 300
    ):
        raise BadSourceRef("invalid path in source_ref")
    return pure.as_posix()


def parse_source_ref(value: str) -> HermesRef | GithubRef:
    text = value.strip()
    if m := _HERMES.match(text):
        target = m.group(1).strip().strip("/")
        if not target or ".." in target.split("/") or "\\" in target:
            raise BadSourceRef("invalid hermes source_ref")
        return HermesRef(target)
    if (m := _GITHUB.match(text)) or (m := _URL.match(text)):
        owner, repo, ref, path = m.groups()
    else:
        raise BadSourceRef(
            "source_ref must look like 'hermes:<slug>' or 'github:<owner>/<repo>[@<ref>]:<path>'"
        )
    if repo.endswith(".git"):
        repo = repo[:-4]
    if ".." in (ref or "").split("/"):
        raise BadSourceRef("invalid ref in source_ref")
    return GithubRef(owner, repo, ref, _clean_path(path))
