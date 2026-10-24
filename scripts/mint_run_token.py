"""Verification helper (scripts/verify.sh): print a run token for an agent's *live* run.

Run with the backend's interpreter and the install's environment (COMPANY_OS_DATABASE_URL ...). It reads
the install's own secret key and signs a token for a run that is actually `starting`/`running`, exactly
as the worker does, so the verifier can make one request through the real agent API on behalf of that
run. It refuses when the agent has no live run. The token goes to stdout only; the caller keeps it in a
shell variable and never logs it.
"""

from __future__ import annotations

import sys

from sqlalchemy import select

from app.core.config import Settings
from app.core.keys import SecretKeyProvider
from app.db.session import build_engine, build_session_factory
from app.models.enums import RunStatus
from app.models.run import TaskRun
from app.services import agent_auth


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: mint_run_token.py AGENT_ID", file=sys.stderr)
        return 2
    settings = Settings()
    engine = build_engine(settings.resolved_database_url)
    try:
        with build_session_factory(engine)() as db:
            run = db.scalar(
                select(TaskRun)
                .where(
                    TaskRun.agent_id == sys.argv[1],
                    TaskRun.status.in_([RunStatus.STARTING, RunStatus.RUNNING]),
                )
                .order_by(TaskRun.created_at.desc())
            )
            if run is None:
                print("no live run for that agent", file=sys.stderr)
                return 1
            token = agent_auth.issue_token(
                SecretKeyProvider(settings), run, max_run_seconds=300
            )
            print(token)
            return 0
    finally:
        engine.dispose()


if __name__ == "__main__":
    sys.exit(main())
