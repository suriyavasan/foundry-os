"""Turn parsed entries into stored items: normalise, dedupe, match, cap, flag, insert.

Order of the decisions for one fetch of one source:

1. an entry without a usable http(s) URL or a title is dropped;
2. the canonical URL's hash is the dedupe key: an item already stored for the company is not stored
   again (the new source is recorded in ``also_seen_on``); the same title (hash) seen on another URL
   within seven days is the same story and collapses the same way;
3. keywords: a negative keyword drops the item; an item that matches nothing is dropped unless the
   source ``keep_unmatched`` (changelogs, UI galleries);
4. caps: the best ``MAX_ITEMS_PER_SOURCE_FETCH`` by score, then the company's remaining daily budget
   (``radar_max_items_per_day``), again by score;
5. each stored item gets ``injection_flags`` (categories only, never the phrases) from the plain
   text *before* the ``<untrusted`` sequences are neutralised, and one ``content.injection_flag``
   guardrail event is logged for the batch. Flagged items are kept, never auto-blocked.

Nothing here commits; the caller's transaction owns the rows.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.guardrails import log as guard_log
from app.models.policy import CompanyPolicy
from app.models.radar import ItemStatus, RadarItem, RadarSource
from app.radar import (
    ITEM_RETENTION_DAYS,
    MAX_AUTHOR,
    MAX_EXCERPT,
    MAX_EXTERNAL_ID,
    MAX_ITEMS_PER_SOURCE_FETCH,
    MAX_TITLE,
    TITLE_DEDUPE_DAYS,
)
from app.radar.adapters import Adapter, default_kind
from app.radar.matching import KeywordRef, Matcher
from app.radar.normalize import (
    canonical_url,
    cap_text,
    clean_text,
    https_url,
    neutralize_untrusted,
    plain_text,
    title_hash,
    url_hash,
)
from app.radar.parsers import RawEntry
from app.services.email_identity import detect_injection

KINDS = ("post", "story", "release", "paper", "design", "review", "question")


@dataclass
class IngestStats:
    seen: int = 0
    new: int = 0
    duplicates: int = 0  # same URL already stored
    title_duplicates: int = 0  # same story, another URL, within seven days
    dropped_invalid: int = 0
    dropped_unmatched: int = 0
    dropped_negative: int = 0
    dropped_cap: int = 0
    flagged: int = 0
    flag_categories: list[str] = field(default_factory=list)

    def add(self, other: IngestStats) -> None:
        for name in (
            "seen",
            "new",
            "duplicates",
            "title_duplicates",
            "dropped_invalid",
            "dropped_unmatched",
            "dropped_negative",
            "dropped_cap",
            "flagged",
        ):
            setattr(self, name, getattr(self, name) + getattr(other, name))
        for cat in other.flag_categories:
            if cat not in self.flag_categories:
                self.flag_categories.append(cat)


@dataclass
class _Candidate:
    entry: RawEntry
    url: str
    url_hash: str
    title_hash: str
    title: str
    excerpt: str
    flags: list[str]
    matched: list[str]
    score: int


def _note_also_seen(item: RadarItem, slug: str, own_source_id: str) -> None:
    if item.source_id == own_source_id:
        return
    seen = list(item.also_seen_on or [])
    if slug not in seen:
        item.also_seen_on = [*seen, slug][:20]


def ingest(
    db: Session,
    *,
    company_id: str,
    source: RadarSource,
    adapter: Adapter,
    batches: Sequence[tuple[str | None, Sequence[RawEntry]]],
    keywords: Sequence[KeywordRef],
    policy: CompanyPolicy,
    now: datetime,
) -> IngestStats:
    """Store the new, matching, in-budget items of ``batches`` (``(query keyword, entries)`` pairs,
    one per request of the fetch) for ``source``."""
    stats = IngestStats()
    matcher = Matcher(keywords)
    fallback_kind = default_kind(source.category, adapter)

    # 1. normalise and drop the unusable
    candidates: dict[str, _Candidate] = {}
    seen_titles: set[str] = set()
    for query_term, entries in batches:
        for entry in entries:
            stats.seen += 1
            canonical = canonical_url(entry.url)
            raw_title = plain_text(entry.title)
            if canonical is None or not raw_title:
                stats.dropped_invalid += 1
                continue
            raw_excerpt = plain_text(entry.excerpt)
            h = url_hash(canonical)
            if h in candidates:
                stats.duplicates += 1
                continue
            t_hash = title_hash(raw_title)
            if t_hash in seen_titles:
                stats.title_duplicates += 1
                continue
            # 3. keywords (on the plain text; the stored text is neutralised and capped below)
            match = matcher.match(
                raw_title,
                raw_excerpt,
                category=source.category,
                engagement=entry.engagement,
                implied=query_term,
            )
            if match.negative:
                stats.dropped_negative += 1
                continue
            if not match.matched and not source.keep_unmatched:
                stats.dropped_unmatched += 1
                continue
            candidates[h] = _Candidate(
                entry=entry,
                url=canonical,
                url_hash=h,
                title_hash=t_hash,
                title=cap_text(neutralize_untrusted(raw_title), MAX_TITLE),
                excerpt=cap_text(neutralize_untrusted(raw_excerpt), MAX_EXCERPT),
                flags=detect_injection(f"{raw_title}\n{raw_excerpt}"),
                matched=match.matched,
                score=match.score,
            )
            seen_titles.add(t_hash)

    if not candidates:
        return stats

    # 2. what is already stored
    existing_by_url = {
        row.url_hash: row
        for row in db.scalars(
            select(RadarItem).where(
                RadarItem.company_id == company_id, RadarItem.url_hash.in_(list(candidates))
            )
        )
    }
    cutoff = now - timedelta(days=TITLE_DEDUPE_DAYS)
    wanted_titles = {c.title_hash for c in candidates.values() if c.url_hash not in existing_by_url}
    existing_by_title: dict[str, RadarItem] = {}
    if wanted_titles:
        for row in db.scalars(
            select(RadarItem).where(
                RadarItem.company_id == company_id,
                RadarItem.title_hash.in_(list(wanted_titles)),
                RadarItem.fetched_at >= cutoff,
            )
        ):
            existing_by_title.setdefault(row.title_hash, row)
    fresh: list[_Candidate] = []
    for cand in candidates.values():
        stored = existing_by_url.get(cand.url_hash)
        if stored is not None:
            stats.duplicates += 1
            _note_also_seen(stored, source.slug, source.id)
            continue
        same_story = existing_by_title.get(cand.title_hash)
        if same_story is not None:
            stats.title_duplicates += 1
            _note_also_seen(same_story, source.slug, source.id)
            continue
        fresh.append(cand)

    # 4. caps: best first
    fresh.sort(
        key=lambda c: (c.score, c.entry.published_at.timestamp() if c.entry.published_at else 0.0),
        reverse=True,
    )
    if len(fresh) > MAX_ITEMS_PER_SOURCE_FETCH:
        stats.dropped_cap += len(fresh) - MAX_ITEMS_PER_SOURCE_FETCH
        fresh = fresh[:MAX_ITEMS_PER_SOURCE_FETCH]
    stored_today = int(
        db.scalar(
            select(func.count(RadarItem.id)).where(
                RadarItem.company_id == company_id,
                RadarItem.fetched_at >= now - timedelta(days=1),
            )
        )
        or 0
    )
    remaining = max(0, int(policy.radar_max_items_per_day) - stored_today)
    if len(fresh) > remaining:
        stats.dropped_cap += len(fresh) - remaining
        fresh = fresh[:remaining]

    # 5. store
    for cand in fresh:
        entry = cand.entry
        kind = entry.kind if entry.kind in KINDS else fallback_kind
        image = https_url(entry.image_url) if source.category == "UI inspiration" else None
        db.add(
            RadarItem(
                company_id=company_id,
                source_id=source.id,
                url=cand.url,
                url_hash=cand.url_hash,
                title_hash=cand.title_hash,
                external_id=clean_text(entry.external_id, MAX_EXTERNAL_ID) or None,
                title=cand.title,
                excerpt=cand.excerpt,
                author=clean_text(entry.author, MAX_AUTHOR) or None,
                published_at=entry.published_at,
                fetched_at=now,
                kind=kind,
                image_url=image,
                engagement=dict(entry.engagement),
                also_seen_on=[],
                matched_keywords=list(cand.matched),
                match_score=cand.score,
                injection_flags=list(cand.flags),
                status=ItemStatus.NEW,
            )
        )
        stats.new += 1
        if cand.flags:
            stats.flagged += 1
            for category in cand.flags:
                if category not in stats.flag_categories:
                    stats.flag_categories.append(category)
    if fresh:
        source.items_total = int(source.items_total or 0) + len(fresh)
    if stats.flagged:
        guard_log.record(
            db,
            company_id=company_id,
            action="content.injection_flag",
            tier="external_read",
            decision="allow",
            reason=(
                f"possible prompt injection in {stats.flagged} radar item(s) from {source.slug}: "
                + ", ".join(stats.flag_categories)
            ),
        )
    db.flush()
    return stats


def prune(db: Session, now: datetime) -> int:
    """Delete items older than 90 days that no brief or idea cites (status ``used`` is kept)."""
    result = db.execute(
        delete(RadarItem).where(
            RadarItem.fetched_at < now - timedelta(days=ITEM_RETENTION_DAYS),
            RadarItem.status.in_(
                [ItemStatus.NEW.value, ItemStatus.SEEN.value, ItemStatus.DISMISSED.value]
            ),
        )
    )
    return int(result.rowcount or 0)
