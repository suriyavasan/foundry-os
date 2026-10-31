"""M21: agent meetings (meetings, meeting_participants, meeting_messages, tasks.meeting_id, policy limits)

Revision ID: 0021
Revises: 0020
Create Date: 2026-10-30 20:00:00.000000

* ``meetings`` / ``meeting_participants`` / ``meeting_messages`` -- a meeting, who is invited and
  what was said (docs/specs/m21-agent-meetings.md).
* ``tasks.meeting_id`` -- the meeting a task of the new kind ``meeting`` (one speaking turn) belongs
  to. ``tasks.kind`` is a plain string column: the new value needs no schema change.
* ``company_policies.meetings_enabled`` (default on) / ``max_meetings_per_agent_per_day`` (3) /
  ``max_meetings_per_company_per_day`` (12).

Portable (SQLite and Postgres). The downgrade drops all of it.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0021"
down_revision: str | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "meetings",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("company_id", sa.String(length=36), nullable=False),
        sa.Column("organizer_agent_id", sa.String(length=36), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("agenda", sa.String(length=2000), nullable=False),
        sa.Column("expected_outcome", sa.String(length=500), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=True),
        sa.Column("task_id", sa.String(length=36), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("start_at", sa.DateTime(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("ended_at", sa.DateTime(), nullable=True),
        sa.Column("round", sa.Integer(), server_default="0", nullable=False),
        sa.Column("max_rounds", sa.Integer(), server_default="2", nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("outcome", sa.JSON(), nullable=False),
        sa.Column("cancel_reason", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(["organizer_agent_id"], ["agents.id"]),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_meetings_company_id", "meetings", ["company_id"])
    op.create_index("ix_meetings_company_status", "meetings", ["company_id", "status"])
    op.create_index("ix_meetings_status_start_at", "meetings", ["status", "start_at"])
    op.create_index("ix_meetings_organizer_agent_id", "meetings", ["organizer_agent_id"])

    op.create_table(
        "meeting_participants",
        sa.Column("meeting_id", sa.String(length=36), nullable=False),
        sa.Column("agent_id", sa.String(length=36), nullable=False),
        sa.Column("company_id", sa.String(length=36), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("position", sa.Integer(), server_default="0", nullable=False),
        sa.Column("joined", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("passes", sa.Integer(), server_default="0", nullable=False),
        sa.ForeignKeyConstraint(["meeting_id"], ["meetings.id"]),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"]),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.PrimaryKeyConstraint("meeting_id", "agent_id"),
    )
    op.create_index("ix_meeting_participants_company_id", "meeting_participants", ["company_id"])

    op.create_table(
        "meeting_messages",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("company_id", sa.String(length=36), nullable=False),
        sa.Column("meeting_id", sa.String(length=36), nullable=False),
        sa.Column("agent_id", sa.String(length=36), nullable=False),
        sa.Column("round", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=16), server_default="turn", nullable=False),
        sa.Column("body", sa.String(length=4000), nullable=False),
        sa.Column("proposals", sa.JSON(), nullable=False),
        sa.Column("task_id", sa.String(length=36), nullable=True),
        sa.Column("run_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(["meeting_id"], ["meetings.id"]),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_meeting_messages_company_id", "meeting_messages", ["company_id"])
    op.create_index("ix_meeting_messages_meeting_id", "meeting_messages", ["meeting_id"])

    with op.batch_alter_table("tasks", schema=None) as batch_op:
        batch_op.add_column(sa.Column("meeting_id", sa.String(length=36), nullable=True))
        batch_op.create_foreign_key(
            "fk_tasks_meeting_id_meetings", "meetings", ["meeting_id"], ["id"]
        )
        batch_op.create_index("ix_tasks_meeting_id", ["meeting_id"])

    with op.batch_alter_table("company_policies", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("meetings_enabled", sa.Boolean(), server_default=sa.true(), nullable=False)
        )
        batch_op.add_column(
            sa.Column(
                "max_meetings_per_agent_per_day",
                sa.Integer(),
                server_default="3",
                nullable=False,
            )
        )
        batch_op.add_column(
            sa.Column(
                "max_meetings_per_company_per_day",
                sa.Integer(),
                server_default="12",
                nullable=False,
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("company_policies", schema=None) as batch_op:
        batch_op.drop_column("max_meetings_per_company_per_day")
        batch_op.drop_column("max_meetings_per_agent_per_day")
        batch_op.drop_column("meetings_enabled")
    with op.batch_alter_table("tasks", schema=None) as batch_op:
        batch_op.drop_index("ix_tasks_meeting_id")
        batch_op.drop_constraint("fk_tasks_meeting_id_meetings", type_="foreignkey")
        batch_op.drop_column("meeting_id")
    op.drop_index("ix_meeting_messages_meeting_id", table_name="meeting_messages")
    op.drop_index("ix_meeting_messages_company_id", table_name="meeting_messages")
    op.drop_table("meeting_messages")
    op.drop_index("ix_meeting_participants_company_id", table_name="meeting_participants")
    op.drop_table("meeting_participants")
    op.drop_index("ix_meetings_organizer_agent_id", table_name="meetings")
    op.drop_index("ix_meetings_status_start_at", table_name="meetings")
    op.drop_index("ix_meetings_company_status", table_name="meetings")
    op.drop_index("ix_meetings_company_id", table_name="meetings")
    op.drop_table("meetings")
