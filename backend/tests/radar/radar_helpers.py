"""Builders shared by the radar tests."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models.policy import CompanyPolicy
from app.models.radar import RadarItem, RadarKeyword, RadarSource
from app.radar.parsers import RawEntry


def new_source(
    db: Session,
    company_id: str,
    slug: str = "src",
    *,
    category: str = "blog",
    adapter: str = "feed",
    fetch_url: str | None = None,
    enabled: bool = True,
    keep_unmatched: bool = False,
    query_mode: str = "none",
    interval: int = 21_600,
    origin: str = "seed",
    **extra,
) -> RadarSource:
    source = RadarSource(
        company_id=company_id,
        slug=slug,
        name=slug.replace("-", " ").title(),
        url=f"https://{slug}.example.test/",
        category=category,
        access_method="rss",
        adapter=adapter,
        fetch_url=fetch_url if fetch_url is not None else f"https://{slug}.example.test/feed.xml",
        query_mode=query_mode,
        keep_unmatched=keep_unmatched,
        enabled=enabled,
        origin=origin,
        interval_seconds=interval,
        **extra,
    )
    db.add(source)
    db.flush()
    return source


def new_keyword(
    db: Session,
    company_id: str,
    term: str,
    *,
    kind: str = "topic",
    weight: int = 3,
    enabled: bool = True,
    origin: str = "owner",
) -> RadarKeyword:
    kw = RadarKeyword(
        company_id=company_id, term=term, kind=kind, weight=weight, enabled=enabled, origin=origin
    )
    db.add(kw)
    db.flush()
    return kw


def entry(
    title: str,
    url: str | None = None,
    *,
    excerpt: str = "",
    published: datetime | None = None,
    **kw,
) -> RawEntry:
    slug = "".join(ch if ch.isalnum() else "-" for ch in title.lower())
    return RawEntry(
        url=url if url is not None else f"https://news.example.test/{slug}",
        title=title,
        excerpt=excerpt,
        published_at=published,
        **kw,
    )


def policy_of(db: Session, company_id: str) -> CompanyPolicy:
    return db.scalars(select(CompanyPolicy).where(CompanyPolicy.company_id == company_id)).one()


def clear_radar(db: Session, company_id: str) -> None:
    """A company starts with the 100 seed sources and its derived keywords (the service seeds them
    at creation). Tests that build their own world start from an empty radar."""
    db.execute(delete(RadarItem).where(RadarItem.company_id == company_id))
    db.execute(delete(RadarSource).where(RadarSource.company_id == company_id))
    db.execute(delete(RadarKeyword).where(RadarKeyword.company_id == company_id))
    db.commit()
