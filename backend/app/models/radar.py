"""M16 market radar tables (migration 0015).

Everything here is tenant-owned (``company_id``) except ``RadarHostState``, which is install-wide
politeness state per remote host (robots.txt cache, spacing, ``Retry-After``). It holds no company
data and is shared across companies on purpose: two companies on one install must not double the
load on a host.

Item text comes from the open internet and is the most hostile input the company reads. It is stored
as plain text only (see ``app/radar/normalize.py``) and flagged at ingest (``injection_flags``).
"""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum

from sqlalchemy import (
    Boolean,
    Date,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.core.ids import new_id
from app.core.time import utcnow
from app.db.base import Base, TimestampMixin
from app.db.types import UTCDateTime


class SourceCategory(StrEnum):
    SOCIAL = "social"
    FORUM = "forum"
    NEWS = "news"
    BLOG = "blog"
    LAUNCH = "launch site"
    REVIEW = "review site"
    UI_INSPIRATION = "UI inspiration"
    RESEARCH = "research"
    CHANGELOG = "changelog"


class AccessMethod(StrEnum):
    RSS = "rss"
    ATOM = "atom"
    JSON_API = "json_api"
    JSON_API_FREE_TOKEN = "json_api_free_token"
    SEARCH_ENGINE_ONLY = "search_engine_only"


class SourceOrigin(StrEnum):
    SEED = "seed"
    OWNER = "owner"
    AGENT = "agent"


class QueryMode(StrEnum):
    NONE = "none"
    PER_KEYWORD = "per_keyword"


class FetchStatus(StrEnum):
    """``RadarSource.last_status``. Never a response body, never a secret."""

    OK = "ok"
    NOT_MODIFIED = "not_modified"
    ROBOTS_DISALLOWED = "robots_disallowed"
    HTTP_4XX = "http_4xx"
    HTTP_5XX = "http_5xx"
    TIMEOUT = "timeout"
    NETWORK_ERROR = "network_error"  # connection refused, TLS failure, DNS failure
    TOO_LARGE = "too_large"
    BLOCKED_DESTINATION = "blocked_destination"
    PARSE_ERROR = "parse_error"
    RATE_LIMITED = "rate_limited"
    CONTENT_TYPE = "content_type"  # the response is not an XML/JSON type this adapter accepts
    REDIRECT_REFUSED = "redirect_refused"  # downgrade, a 4th hop, or a redirect without a target
    CONFIG_ERROR = "config_error"  # a placeholder has no value, or no fetch URL
    NEEDS_TOKEN = "needs_token"
    NO_KEYWORDS = "no_keywords"  # a per-keyword source and no enabled keyword to ask for


class DisabledReason(StrEnum):
    """``RadarSource.disabled_reason`` (why ``enabled`` is false)."""

    OWNER = "owner"
    FAILING = "failing"  # 10 failures in a row
    ROBOTS_DISALLOWED = "robots_disallowed"
    NEEDS_TOKEN = "needs_token"
    SEARCH_ONLY = "search_only"  # read by agents through web search, never fetched
    NEEDS_VALUES = "needs_values"  # a placeholder (handle, publication, repo) the owner must choose
    TERMS = "terms"  # the site's terms restrict automated use
    UNREACHABLE = "unreachable"  # not reachable when the seed list was checked
    SUGGESTION = "suggestion"  # an agent suggested it; the owner has not enabled it


class KeywordKind(StrEnum):
    PRODUCT = "product"
    COMPETITOR = "competitor"
    TOPIC = "topic"
    NEGATIVE = "negative"  # a match drops the item


class KeywordOrigin(StrEnum):
    DERIVED = "derived"
    OWNER = "owner"
    AGENT = "agent"


class ItemKind(StrEnum):
    POST = "post"
    STORY = "story"
    RELEASE = "release"
    PAPER = "paper"
    DESIGN = "design"
    REVIEW = "review"
    QUESTION = "question"


class ItemStatus(StrEnum):
    NEW = "new"
    SEEN = "seen"  # shown in a digest
    USED = "used"  # cited in a brief or an idea
    DISMISSED = "dismissed"


class BriefKind(StrEnum):
    DAILY = "daily"
    SELF_CRITIQUE = "self_critique"
    UI_INSPIRATION = "ui_inspiration"


class IdeaKind(StrEnum):
    FIX = "fix"
    FEATURE = "feature"
    UI = "ui"
    POSITIONING = "positioning"
    RESEARCH = "research"


class IdeaOrigin(StrEnum):
    RADAR = "radar"
    SELF_CRITIQUE = "self_critique"
    UI_INSPIRATION = "ui_inspiration"
    OWNER = "owner"


class IdeaStatus(StrEnum):
    OPEN = "open"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    MERGED = "merged"
    DONE = "done"
    EXPIRED = "expired"


OPEN_IDEA_STATUSES = (IdeaStatus.OPEN.value,)


class RadarSource(TimestampMixin, Base):
    """One place the radar reads: a feed, a JSON API, or (``search_engine_only``) a site agents
    reach through ``web_search``. Seeded per company so the owner edits their own copy."""

    __tablename__ = "radar_sources"
    __table_args__ = (
        UniqueConstraint("company_id", "slug", name="uq_radar_sources_company_slug"),
        Index("ix_radar_sources_company_due", "company_id", "enabled", "next_fetch_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), nullable=False)
    slug: Mapped[str] = mapped_column(String(80), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    url: Mapped[str] = mapped_column(String(500), nullable=False)
    category: Mapped[str] = mapped_column(String(24), nullable=False)
    access_method: Mapped[str] = mapped_column(String(24), nullable=False)
    adapter: Mapped[str] = mapped_column(String(32), nullable=False)
    fetch_url: Mapped[str | None] = mapped_column(String(1000))
    search_template: Mapped[str | None] = mapped_column(String(300))
    query_mode: Mapped[str] = mapped_column(
        String(16), default=QueryMode.NONE, server_default="none", nullable=False
    )
    keep_unmatched: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    origin: Mapped[str] = mapped_column(String(16), default=SourceOrigin.OWNER, nullable=False)
    seed_version: Mapped[int | None] = mapped_column(Integer)
    edited_by_owner: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    interval_seconds: Mapped[int] = mapped_column(
        Integer, default=21_600, server_default="21600", nullable=False
    )
    tos_note: Mapped[str | None] = mapped_column(String(300))
    rate_note: Mapped[str | None] = mapped_column(String(300))
    etag: Mapped[str | None] = mapped_column(String(200))
    last_modified: Mapped[str | None] = mapped_column(String(200))
    last_fetched_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    next_fetch_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_status: Mapped[str | None] = mapped_column(String(32))
    last_error: Mapped[str | None] = mapped_column(String(300))
    # Owner-only, on owner-added sources only, audited. Agents and seeds can never set it.
    robots_override: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    template_values: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    consecutive_failures: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    disabled_reason: Mapped[str | None] = mapped_column(String(32))
    items_total: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    created_by_type: Mapped[str | None] = mapped_column(String(16))
    created_by_id: Mapped[str | None] = mapped_column(String(36))


class RadarKeyword(TimestampMixin, Base):
    """What the radar looks for. Uniqueness of ``lower(term)`` per company is enforced by the
    service (portable: no functional index)."""

    __tablename__ = "radar_keywords"
    __table_args__ = (Index("ix_radar_keywords_company_enabled", "company_id", "enabled"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), nullable=False)
    term: Mapped[str] = mapped_column(String(80), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), default=KeywordKind.TOPIC, nullable=False)
    origin: Mapped[str] = mapped_column(String(16), default=KeywordOrigin.OWNER, nullable=False)
    weight: Mapped[int] = mapped_column(Integer, default=3, server_default="3", nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_by_type: Mapped[str | None] = mapped_column(String(16))
    created_by_id: Mapped[str | None] = mapped_column(String(36))


class RadarItem(Base):
    """One thing read from outside. Plain text only; ``(company_id, url_hash)`` is the dedupe."""

    __tablename__ = "radar_items"
    __table_args__ = (
        UniqueConstraint("company_id", "url_hash", name="uq_radar_items_company_url_hash"),
        Index("ix_radar_items_company_fetched", "company_id", "fetched_at"),
        Index("ix_radar_items_company_status_score", "company_id", "status", "match_score"),
        Index("ix_radar_items_company_title_hash", "company_id", "title_hash"),
        Index("ix_radar_items_source_id", "source_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), nullable=False)
    source_id: Mapped[str | None] = mapped_column(ForeignKey("radar_sources.id"))
    url: Mapped[str] = mapped_column(String(1000), nullable=False)
    url_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    title_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    external_id: Mapped[str | None] = mapped_column(String(200))
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    excerpt: Mapped[str] = mapped_column(String(1000), default="", nullable=False)
    author: Mapped[str | None] = mapped_column(String(120))
    published_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    fetched_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
    kind: Mapped[str] = mapped_column(String(16), default=ItemKind.POST, nullable=False)
    image_url: Mapped[str | None] = mapped_column(String(1000))
    engagement: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    also_seen_on: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    matched_keywords: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    match_score: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    injection_flags: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    status: Mapped[str] = mapped_column(String(12), default=ItemStatus.NEW, nullable=False)
    relevance: Mapped[int | None] = mapped_column(Integer)


class RadarBrief(Base):
    """What an agent concluded from a batch of items. The body is an artifact; this row is the
    index (summary, themes with item ids). One brief per (company, kind, day)."""

    __tablename__ = "radar_briefs"
    __table_args__ = (
        UniqueConstraint(
            "company_id", "kind", "period_day", name="uq_radar_briefs_company_kind_day"
        ),
        Index("ix_radar_briefs_company_created", "company_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), default=BriefKind.DAILY, nullable=False)
    period_start: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    period_end: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    # The UTC date of ``period_end``: the portable key of "one brief per kind per day".
    period_day: Mapped[date] = mapped_column(Date, nullable=False)
    author_agent_id: Mapped[str | None] = mapped_column(String(36))
    task_id: Mapped[str | None] = mapped_column(String(36))
    artifact_id: Mapped[str | None] = mapped_column(String(36))
    summary: Mapped[str] = mapped_column(String(2000), default="", nullable=False)
    themes: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    item_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    ideas_created: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)


class RadarIdea(TimestampMixin, Base):
    """The backlog: a change worth making, with evidence. ``priority_score`` is deterministic
    (``impact * confidence * 4 / effort``); the service computes it."""

    __tablename__ = "radar_ideas"
    __table_args__ = (
        Index("ix_radar_ideas_company_status", "company_id", "status"),
        Index("ix_radar_ideas_company_priority", "company_id", "priority_score"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    problem: Mapped[str] = mapped_column(Text, default="", nullable=False)
    proposal: Mapped[str] = mapped_column(Text, default="", nullable=False)
    kind: Mapped[str] = mapped_column(String(16), default=IdeaKind.FEATURE, nullable=False)
    origin: Mapped[str] = mapped_column(String(16), default=IdeaOrigin.RADAR, nullable=False)
    evidence: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    impact: Mapped[int] = mapped_column(Integer, default=3, server_default="3", nullable=False)
    effort: Mapped[int] = mapped_column(Integer, default=3, server_default="3", nullable=False)
    confidence: Mapped[int] = mapped_column(Integer, default=3, server_default="3", nullable=False)
    priority_score: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    status: Mapped[str] = mapped_column(String(12), default=IdeaStatus.OPEN, nullable=False)
    decided_by_type: Mapped[str | None] = mapped_column(String(16))
    decided_by_id: Mapped[str | None] = mapped_column(String(36))
    decision_note: Mapped[str | None] = mapped_column(String(1000))
    decided_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    task_id: Mapped[str | None] = mapped_column(String(36))
    project_id: Mapped[str | None] = mapped_column(String(36))
    goal_id: Mapped[str | None] = mapped_column(String(36))
    merged_into_id: Mapped[str | None] = mapped_column(String(36))
    created_by_agent_id: Mapped[str | None] = mapped_column(String(36))
    created_by_type: Mapped[str | None] = mapped_column(String(16))
    created_by_id: Mapped[str | None] = mapped_column(String(36))


class RadarHostState(Base):
    """Install-wide politeness state for one remote host (not tenant-owned, like ``system_state``).
    ``host`` is ``hostname`` or ``hostname:port`` for a non-default port."""

    __tablename__ = "radar_host_state"

    host: Mapped[str] = mapped_column(String(255), primary_key=True)
    robots_fetched_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    robots_status: Mapped[str | None] = mapped_column(String(16))  # ok | missing | unreachable
    robots_txt: Mapped[str | None] = mapped_column(Text)  # capped at 64 KB
    crawl_delay_seconds: Mapped[int | None] = mapped_column(Integer)
    next_allowed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    retry_after_until: Mapped[datetime | None] = mapped_column(UTCDateTime)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, onupdate=utcnow, nullable=False
    )


__all__ = [
    "OPEN_IDEA_STATUSES",
    "AccessMethod",
    "BriefKind",
    "DisabledReason",
    "FetchStatus",
    "IdeaKind",
    "IdeaOrigin",
    "IdeaStatus",
    "ItemKind",
    "ItemStatus",
    "KeywordKind",
    "KeywordOrigin",
    "QueryMode",
    "RadarBrief",
    "RadarHostState",
    "RadarIdea",
    "RadarItem",
    "RadarKeyword",
    "RadarSource",
    "SourceCategory",
    "SourceOrigin",
]
