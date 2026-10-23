"""Everything the skills service needs from the outside world, injectable for tests."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from app.core.config import Settings
from app.skills.blobstore import BlobStore
from app.skills.github import GithubClient
from app.skills.hermes import HermesCatalog
from app.skills.scan import RuntimeScanner, SkillScanner


@dataclass
class SkillsEnv:
    blobs: BlobStore
    runtime_dir: Path
    scanner: SkillScanner
    github: Callable[[], GithubClient]
    hermes: HermesCatalog


def build_skills_env(
    settings: Settings,
    *,
    scanner: SkillScanner | None = None,
    github: Callable[[], GithubClient] | None = None,
) -> SkillsEnv:
    runtime_dir = settings.resolved_runtime_dir
    token = settings.github_token.get_secret_value() if settings.github_token else None

    def default_github() -> GithubClient:
        return GithubClient(token=token, api_url=settings.github_api_url)

    return SkillsEnv(
        blobs=BlobStore(settings.skills_blobs_dir),
        runtime_dir=runtime_dir,
        scanner=scanner
        or RuntimeScanner(
            settings.resolved_runtime_python, runtime_dir, settings.skill_scan_timeout_seconds
        ),
        github=github or default_github,
        hermes=HermesCatalog(runtime_dir),
    )
