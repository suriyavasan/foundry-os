"""M16 market radar: sources, keywords, items, briefs, ideas, host state, and the radar policy

Revision ID: 0015
Revises: 0014
Create Date: 2026-10-27 20:00:00.000000

* New tables ``radar_sources``, ``radar_keywords``, ``radar_items``, ``radar_briefs``,
  ``radar_ideas`` (all tenant-owned) and ``radar_host_state`` (install-wide politeness state per
  remote host: robots.txt cache, spacing, ``Retry-After``; it holds no company data).
* ``company_policies`` (human-only): ``radar_enabled`` and the radar limits. **Existing companies
  get ``radar_enabled = false``** (they opt in); the column default is then switched to true so a
  company created later starts with the radar on. Every non-null column has a server default, so
  nothing else is backfilled.
* Every existing company is seeded with the 100 radar sources (``app/radar/seed_sources.json``,
  seed version 1). They are inert until the owner turns the radar on: the fetcher only runs for
  companies with ``radar_enabled``. Keywords are derived when the radar is first turned on.

Portable (SQLite and Postgres): string enums, JSON, no partial indexes, no ``ON CONFLICT``.
The downgrade drops everything it added.
"""

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa

from alembic import op

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SEED_VERSION = 1  # frozen: this migration seeds version 1 whatever the code's seed version is now

_POLICY_INT_COLUMNS = (
    ("radar_max_fetches_per_day", "2000"),
    ("radar_max_items_per_day", "400"),
    ("radar_max_ideas_per_day", "5"),
    ("radar_max_open_ideas", "25"),
    ("radar_max_goals_per_week", "1"),
)
_POLICY_BOOL_COLUMNS = ("radar_auto_accept", "radar_agent_sources", "self_critique_enabled")
_POLICY_COLUMNS = (
    "radar_enabled",
    *(name for name, _ in _POLICY_INT_COLUMNS),
    *_POLICY_BOOL_COLUMNS,
    "radar_contact_email",
)


def _ts() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    ]


def _fk(table: str, column: str, target: str) -> sa.ForeignKeyConstraint:
    ref_table = target.split(".")[0]
    return sa.ForeignKeyConstraint(
        [column], [target], name=op.f(f"fk_{table}_{column}_{ref_table}")
    )


def upgrade() -> None:
    # Existing rows get radar_enabled = false (opt-in) ...
    with op.batch_alter_table("company_policies", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("radar_enabled", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        for name, default in _POLICY_INT_COLUMNS:
            batch_op.add_column(
                sa.Column(name, sa.Integer(), nullable=False, server_default=default)
            )
        for name in _POLICY_BOOL_COLUMNS:
            batch_op.add_column(
                sa.Column(name, sa.Boolean(), nullable=False, server_default=sa.true())
            )
        batch_op.add_column(sa.Column("radar_contact_email", sa.String(length=200), nullable=True))
    # ... and companies created afterwards start with it on.
    with op.batch_alter_table("company_policies", schema=None) as batch_op:
        batch_op.alter_column(
            "radar_enabled",
            existing_type=sa.Boolean(),
            existing_nullable=False,
            server_default=sa.true(),
        )

    op.create_table(
        "radar_sources",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("company_id", sa.String(length=36), nullable=False),
        sa.Column("slug", sa.String(length=80), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("url", sa.String(length=500), nullable=False),
        sa.Column("category", sa.String(length=24), nullable=False),
        sa.Column("access_method", sa.String(length=24), nullable=False),
        sa.Column("adapter", sa.String(length=32), nullable=False),
        sa.Column("fetch_url", sa.String(length=1000), nullable=True),
        sa.Column("search_template", sa.String(length=300), nullable=True),
        sa.Column("query_mode", sa.String(length=16), nullable=False, server_default="none"),
        sa.Column("keep_unmatched", sa.Boolean(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("origin", sa.String(length=16), nullable=False),
        sa.Column("seed_version", sa.Integer(), nullable=True),
        sa.Column("edited_by_owner", sa.Boolean(), nullable=False),
        sa.Column("interval_seconds", sa.Integer(), nullable=False, server_default="21600"),
        sa.Column("tos_note", sa.String(length=300), nullable=True),
        sa.Column("rate_note", sa.String(length=300), nullable=True),
        sa.Column("etag", sa.String(length=200), nullable=True),
        sa.Column("last_modified", sa.String(length=200), nullable=True),
        sa.Column("last_fetched_at", sa.DateTime(), nullable=True),
        sa.Column("next_fetch_at", sa.DateTime(), nullable=True),
        sa.Column("last_status", sa.String(length=32), nullable=True),
        sa.Column("last_error", sa.String(length=300), nullable=True),
        sa.Column("robots_override", sa.Boolean(), nullable=False),
        sa.Column("template_values", sa.JSON(), nullable=False),
        sa.Column("consecutive_failures", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("disabled_reason", sa.String(length=32), nullable=True),
        sa.Column("items_total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_by_type", sa.String(length=16), nullable=True),
        sa.Column("created_by_id", sa.String(length=36), nullable=True),
        *_ts(),
        _fk("radar_sources", "company_id", "companies.id"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_radar_sources")),
        sa.UniqueConstraint("company_id", "slug", name="uq_radar_sources_company_slug"),
    )
    op.create_index(
        "ix_radar_sources_company_due",
        "radar_sources",
        ["company_id", "enabled", "next_fetch_at"],
        unique=False,
    )

    op.create_table(
        "radar_keywords",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("company_id", sa.String(length=36), nullable=False),
        sa.Column("term", sa.String(length=80), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("origin", sa.String(length=16), nullable=False),
        sa.Column("weight", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("created_by_type", sa.String(length=16), nullable=True),
        sa.Column("created_by_id", sa.String(length=36), nullable=True),
        *_ts(),
        _fk("radar_keywords", "company_id", "companies.id"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_radar_keywords")),
    )
    op.create_index(
        "ix_radar_keywords_company_enabled",
        "radar_keywords",
        ["company_id", "enabled"],
        unique=False,
    )

    op.create_table(
        "radar_items",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("company_id", sa.String(length=36), nullable=False),
        sa.Column("source_id", sa.String(length=36), nullable=True),
        sa.Column("url", sa.String(length=1000), nullable=False),
        sa.Column("url_hash", sa.String(length=64), nullable=False),
        sa.Column("title_hash", sa.String(length=64), nullable=False),
        sa.Column("external_id", sa.String(length=200), nullable=True),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("excerpt", sa.String(length=1000), nullable=False),
        sa.Column("author", sa.String(length=120), nullable=True),
        sa.Column("published_at", sa.DateTime(), nullable=True),
        sa.Column("fetched_at", sa.DateTime(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("image_url", sa.String(length=1000), nullable=True),
        sa.Column("engagement", sa.JSON(), nullable=False),
        sa.Column("also_seen_on", sa.JSON(), nullable=False),
        sa.Column("matched_keywords", sa.JSON(), nullable=False),
        sa.Column("match_score", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("injection_flags", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=12), nullable=False),
        sa.Column("relevance", sa.Integer(), nullable=True),
        _fk("radar_items", "company_id", "companies.id"),
        _fk("radar_items", "source_id", "radar_sources.id"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_radar_items")),
        sa.UniqueConstraint("company_id", "url_hash", name="uq_radar_items_company_url_hash"),
    )
    op.create_index(
        "ix_radar_items_company_fetched", "radar_items", ["company_id", "fetched_at"], unique=False
    )
    op.create_index(
        "ix_radar_items_company_status_score",
        "radar_items",
        ["company_id", "status", "match_score"],
        unique=False,
    )
    op.create_index(
        "ix_radar_items_company_title_hash",
        "radar_items",
        ["company_id", "title_hash"],
        unique=False,
    )
    op.create_index("ix_radar_items_source_id", "radar_items", ["source_id"], unique=False)

    op.create_table(
        "radar_briefs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("company_id", sa.String(length=36), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("period_start", sa.DateTime(), nullable=False),
        sa.Column("period_end", sa.DateTime(), nullable=False),
        sa.Column("period_day", sa.Date(), nullable=False),
        sa.Column("author_agent_id", sa.String(length=36), nullable=True),
        sa.Column("task_id", sa.String(length=36), nullable=True),
        sa.Column("artifact_id", sa.String(length=36), nullable=True),
        sa.Column("summary", sa.String(length=2000), nullable=False),
        sa.Column("themes", sa.JSON(), nullable=False),
        sa.Column("item_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("ideas_created", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        _fk("radar_briefs", "company_id", "companies.id"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_radar_briefs")),
        sa.UniqueConstraint(
            "company_id", "kind", "period_day", name="uq_radar_briefs_company_kind_day"
        ),
    )
    op.create_index(
        "ix_radar_briefs_company_created",
        "radar_briefs",
        ["company_id", "created_at"],
        unique=False,
    )

    op.create_table(
        "radar_ideas",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("company_id", sa.String(length=36), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("problem", sa.Text(), nullable=False),
        sa.Column("proposal", sa.Text(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("origin", sa.String(length=16), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("impact", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("effort", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("confidence", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("priority_score", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(length=12), nullable=False),
        sa.Column("decided_by_type", sa.String(length=16), nullable=True),
        sa.Column("decided_by_id", sa.String(length=36), nullable=True),
        sa.Column("decision_note", sa.String(length=1000), nullable=True),
        sa.Column("decided_at", sa.DateTime(), nullable=True),
        sa.Column("task_id", sa.String(length=36), nullable=True),
        sa.Column("project_id", sa.String(length=36), nullable=True),
        sa.Column("goal_id", sa.String(length=36), nullable=True),
        sa.Column("merged_into_id", sa.String(length=36), nullable=True),
        sa.Column("created_by_agent_id", sa.String(length=36), nullable=True),
        sa.Column("created_by_type", sa.String(length=16), nullable=True),
        sa.Column("created_by_id", sa.String(length=36), nullable=True),
        *_ts(),
        _fk("radar_ideas", "company_id", "companies.id"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_radar_ideas")),
    )
    op.create_index(
        "ix_radar_ideas_company_status", "radar_ideas", ["company_id", "status"], unique=False
    )
    op.create_index(
        "ix_radar_ideas_company_priority",
        "radar_ideas",
        ["company_id", "priority_score"],
        unique=False,
    )

    op.create_table(
        "radar_host_state",
        sa.Column("host", sa.String(length=255), nullable=False),
        sa.Column("robots_fetched_at", sa.DateTime(), nullable=True),
        sa.Column("robots_status", sa.String(length=16), nullable=True),
        sa.Column("robots_txt", sa.Text(), nullable=True),
        sa.Column("crawl_delay_seconds", sa.Integer(), nullable=True),
        sa.Column("next_allowed_at", sa.DateTime(), nullable=True),
        sa.Column("retry_after_until", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("host", name=op.f("pk_radar_host_state")),
    )

    _seed_existing_companies()


def _seed_existing_companies() -> None:
    """Insert the seed sources for every company that exists now (new companies are seeded by the
    service). Inert until the owner turns the radar on."""
    from app.radar.seed import load_seed_sources, source_columns

    conn = op.get_bind()
    company_ids = [row[0] for row in conn.execute(sa.text("SELECT id FROM companies"))]
    if not company_ids:
        return
    sources = sa.table(
        "radar_sources",
        sa.column("id", sa.String),
        sa.column("company_id", sa.String),
        sa.column("slug", sa.String),
        sa.column("name", sa.String),
        sa.column("url", sa.String),
        sa.column("category", sa.String),
        sa.column("access_method", sa.String),
        sa.column("adapter", sa.String),
        sa.column("fetch_url", sa.String),
        sa.column("search_template", sa.String),
        sa.column("query_mode", sa.String),
        sa.column("keep_unmatched", sa.Boolean),
        sa.column("enabled", sa.Boolean),
        sa.column("origin", sa.String),
        sa.column("seed_version", sa.Integer),
        sa.column("edited_by_owner", sa.Boolean),
        sa.column("interval_seconds", sa.Integer),
        sa.column("tos_note", sa.String),
        sa.column("rate_note", sa.String),
        sa.column("robots_override", sa.Boolean),
        sa.column("template_values", sa.JSON),
        sa.column("consecutive_failures", sa.Integer),
        sa.column("disabled_reason", sa.String),
        sa.column("items_total", sa.Integer),
        sa.column("created_at", sa.DateTime),
        sa.column("updated_at", sa.DateTime),
    )
    now = datetime.now(UTC).replace(tzinfo=None)
    rows = []
    for company_id in company_ids:
        for seed in load_seed_sources():
            cols = source_columns(seed)
            cols["seed_version"] = SEED_VERSION
            rows.append(
                {
                    "id": str(uuid.uuid4()),
                    "company_id": company_id,
                    "created_at": now,
                    "updated_at": now,
                    **cols,
                }
            )
    op.bulk_insert(sources, rows)


def downgrade() -> None:
    op.drop_table("radar_host_state")
    op.drop_index("ix_radar_ideas_company_priority", table_name="radar_ideas")
    op.drop_index("ix_radar_ideas_company_status", table_name="radar_ideas")
    op.drop_table("radar_ideas")
    op.drop_index("ix_radar_briefs_company_created", table_name="radar_briefs")
    op.drop_table("radar_briefs")
    op.drop_index("ix_radar_items_source_id", table_name="radar_items")
    op.drop_index("ix_radar_items_company_title_hash", table_name="radar_items")
    op.drop_index("ix_radar_items_company_status_score", table_name="radar_items")
    op.drop_index("ix_radar_items_company_fetched", table_name="radar_items")
    op.drop_table("radar_items")
    op.drop_index("ix_radar_keywords_company_enabled", table_name="radar_keywords")
    op.drop_table("radar_keywords")
    op.drop_index("ix_radar_sources_company_due", table_name="radar_sources")
    op.drop_table("radar_sources")
    with op.batch_alter_table("company_policies", schema=None) as batch_op:
        for column in reversed(_POLICY_COLUMNS):
            batch_op.drop_column(column)
