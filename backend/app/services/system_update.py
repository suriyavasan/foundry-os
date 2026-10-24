"""Instance-wide update status for the owner (docs/specs/m11 section 3).

The check itself lives in ``app.ops.updatecheck`` (one cached GitHub request a day). Seeing the
status and switching the check on or off are owner-only, like the other installation-wide settings;
switching it writes an audit event to each of the owner's companies.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.principal import Principal
from app.ops import updatecheck
from app.services import audit, reachability


def get_status(db: Session, settings: Settings, principal: Principal) -> dict[str, Any]:
    reachability.require_installation_owner(db, principal)
    return updatecheck.status(settings, refresh="if-stale")


def set_enabled(
    db: Session, settings: Settings, principal: Principal, enabled: bool
) -> dict[str, Any]:
    reachability.require_installation_owner(db, principal)
    updatecheck.set_preference(settings, enabled)
    action = "update_check.enabled" if enabled else "update_check.disabled"
    summary = "Turned the daily update check on" if enabled else "Turned the daily update check off"
    for company_id in reachability.owner_company_ids(db, principal.id):
        audit.record(
            db,
            company_id=company_id,
            principal=principal,
            action=action,
            target_type="system",
            target_id=None,
            summary=summary,
        )
    db.commit()
    return updatecheck.status(settings, refresh="never")
