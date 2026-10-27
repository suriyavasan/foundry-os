"""Autonomous GitHub maintenance (M14 step 0: contracts; WP-A implements).

Deterministic intake: GitHub content (issues, reviews, failing checks, Dependabot alerts) becomes a
bounded number of tasks. Everything GitHub-controlled is clipped, scrubbed, flagged for prompt
injection and handed to agents only inside ``<untrusted source="github">`` blocks
(docs/specs/m14-github-backed-projects.md 4.1, decision D42).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy.orm import Session

from app.core.principal import Principal
from app.models.repository import ProjectRepository
from app.services.repo_errors import not_built

if TYPE_CHECKING:
    from app.services.repo_sync import RepoEnv, SyncSnapshot


@dataclass
class MaintenanceReport:
    """What one ``process`` call did (for the audit trail and the sync log)."""

    items_new: int = 0
    items_ignored: int = 0
    items_queued: int = 0
    tasks_created: int = 0
    notes: list[str] = field(default_factory=list)


def process(
    db: Session,
    env: RepoEnv,
    binding: ProjectRepository,
    snapshot: SyncSnapshot,
    now: datetime,
) -> MaintenanceReport:
    """Intake -> ``repo_items`` -> tasks, from the sync snapshot (no GitHub reads here).

    Issue triage (planning task to the lead), review comments, CI failures on the default branch,
    Dependabot alerts, stale ``foundry/*`` branch cleanup (through ``repo_gate.authorize``). Does
    nothing while the company is paused. Does not commit."""
    raise not_built()


def sync_routines(db: Session, binding: ProjectRepository, principal: Principal) -> None:
    """Create / update / disable the binding's built-in schedules (``repo-docs-upkeep:<rid>``,
    ``repo-release-check:<rid>``) from its policy (``docs_upkeep``, ``releases``)."""
    raise not_built()
