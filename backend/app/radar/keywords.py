"""Keyword derivation: what the radar looks for, taken from the company's own words.

Deterministic, no model. From the company profile:

* the company name (``product``, weight 5);
* project names (``product``, weight 4);
* noun-phrase-like chunks of the mission (1 to 3 words, no stop words, at most 8; ``topic``, 3);
* key terms of goal titles (at most 6; ``topic``, 3);
* ``constraints`` lines that start with ``competitor:`` (``competitor``, 4).

The owner can edit, disable or add; agents may add ``topic`` and ``competitor`` keywords. Derivation
only ever *adds* missing ``derived`` keywords: it never touches an owner or agent keyword and never
re-enables one the owner disabled. At most 40 keywords are enabled per company.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.principal import Principal
from app.models.company import Company
from app.models.enums import GoalStatus, ProjectStatus
from app.models.radar import KeywordKind, KeywordOrigin, RadarKeyword
from app.models.work import Goal, Project
from app.radar import MAX_ENABLED_KEYWORDS
from app.services import audit

MAX_MISSION_TERMS = 8
MAX_GOAL_TERMS = 6
MIN_TERM_LENGTH = 3
MAX_TERM_LENGTH = 80

STOP_WORDS = frozenset(
    """a about above after again all also am an and any are as at be because been before being
    below between both but by can could did do does doing down during each few for from further had
    has have having he her here hers him his how i if in into is it its itself just me more most my
    no nor not now of off on once only or other our out over own same she should so some such than
    that the their them then there these they this those through to too under until up us very was
    we were what when where which while who whom why will with would you your yours build builds
    building make makes making help helps helping let get gets new using use used via per every
    like want wants need needs aim aims goal goals mission company companies product products
    service services solution solutions platform tool tools create creates ship ships first best
    better great good easy easily simple faster fast people team teams world everyone anyone
    something anything things thing way ways""".split()  # noqa: SIM905
)
_WORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9+#.'\-]*[A-Za-z0-9+#]|[A-Za-z0-9]")


@dataclass(frozen=True)
class DerivedKeyword:
    term: str
    kind: str
    weight: int


def clean_term(term: str) -> str:
    return " ".join(term.split())[:MAX_TERM_LENGTH].strip(" .,;:-")


def noun_phrases(text: str, limit: int) -> list[str]:
    """Up to ``limit`` distinct phrases of 1 to 3 consecutive non-stop words, best first (more
    frequent, then longer, then earlier)."""
    chunks: list[list[str]] = []
    for sentence in re.split(r"[\n.;:!?()\[\]—–]|,\s|\s-\s", text or ""):
        current: list[str] = []
        for word in _WORD.findall(sentence):
            if word.lower() in STOP_WORDS or len(word) < 2:
                if current:
                    chunks.append(current)
                current = []
            else:
                current.append(word)
        if current:
            chunks.append(current)
    counts: Counter[str] = Counter()
    first_seen: dict[str, int] = {}
    for chunk in chunks:
        for size in (3, 2, 1):
            for i in range(0, len(chunk) - size + 1):
                phrase = " ".join(chunk[i : i + size])
                key = phrase.lower()
                if len(phrase) < MIN_TERM_LENGTH:
                    continue
                counts[key] += 1
                first_seen.setdefault(key, len(first_seen))
    display = {}
    for chunk in chunks:
        for size in (3, 2, 1):
            for i in range(0, len(chunk) - size + 1):
                display.setdefault(
                    " ".join(chunk[i : i + size]).lower(), " ".join(chunk[i : i + size])
                )

    def usable(key: str) -> bool:
        words = key.split()
        if len(words) > 1:
            return True
        word = display[key]
        # a lone word is a keyword only when it looks like a name (Slack, AI, GPT4)
        return word[0].isupper() or any(ch.isdigit() for ch in word)

    ranked = sorted(
        counts,
        key=lambda k: (-counts[k], -min(len(k.split()), 2), first_seen[k]),
    )
    picked: list[str] = []
    for key in ranked:
        if not usable(key):
            continue
        # a phrase already contained in a better one adds nothing
        if any(key in other or other in key for other in picked):
            continue
        picked.append(key)
        if len(picked) >= limit:
            break
    if len(picked) < min(3, limit):  # little to go on: add the longer lone words too
        for key in ranked:
            if len(picked) >= min(3, limit):
                break
            if len(key) >= 6 and not any(key in other or other in key for other in picked):
                picked.append(key)
    return [display[k] for k in picked]


def derive_terms(db: Session, company_id: str) -> list[DerivedKeyword]:
    company = db.get(Company, company_id)
    if company is None:
        return []
    found: dict[str, DerivedKeyword] = {}

    def add(term: str, kind: str, weight: int) -> None:
        term = clean_term(term)
        if len(term) < MIN_TERM_LENGTH or term.lower() in STOP_WORDS:
            return
        found.setdefault(term.lower(), DerivedKeyword(term, kind, weight))

    add(company.name, KeywordKind.PRODUCT.value, 5)
    for name in db.scalars(
        select(Project.name)
        .where(
            Project.company_id == company_id,
            Project.status.in_([ProjectStatus.PLANNED, ProjectStatus.ACTIVE]),
        )
        .order_by(Project.created_at)
        .limit(10)
    ):
        add(name, KeywordKind.PRODUCT.value, 4)
    for line in company.constraints or []:
        if isinstance(line, str) and line.lower().startswith("competitor:"):
            for name in line.split(":", 1)[1].split(","):
                add(name, KeywordKind.COMPETITOR.value, 4)
    for phrase in noun_phrases(company.mission or "", MAX_MISSION_TERMS):
        add(phrase, KeywordKind.TOPIC.value, 3)
    titles = db.scalars(
        select(Goal.title)
        .where(
            Goal.company_id == company_id,
            Goal.status.in_([GoalStatus.PROPOSED, GoalStatus.ACTIVE]),
        )
        .order_by(Goal.created_at)
        .limit(10)
    )
    goal_terms: list[str] = []
    for title in titles:
        for phrase in noun_phrases(title, 2):
            if phrase.lower() not in goal_terms:
                goal_terms.append(phrase.lower())
    for term in goal_terms[:MAX_GOAL_TERMS]:
        add(term, KeywordKind.TOPIC.value, 3)
    return list(found.values())


def enabled_count(db: Session, company_id: str) -> int:
    return int(
        db.scalar(
            select(func.count(RadarKeyword.id)).where(
                RadarKeyword.company_id == company_id, RadarKeyword.enabled.is_(True)
            )
        )
        or 0
    )


def apply_derived(
    db: Session, company_id: str, *, principal: Principal | None = None
) -> list[RadarKeyword]:
    """Insert the derived keywords the company lacks (case-insensitive on the term). Returns the
    new rows. Writes one ``radar.keywords.derived`` audit event when anything was added (company
    creation included; WP-2's tests count it). No commit."""
    existing = {
        t.lower()
        for t in db.scalars(select(RadarKeyword.term).where(RadarKeyword.company_id == company_id))
    }
    room = MAX_ENABLED_KEYWORDS - enabled_count(db, company_id)
    created: list[RadarKeyword] = []
    for derived in derive_terms(db, company_id):
        if derived.term.lower() in existing or room <= 0:
            continue
        row = RadarKeyword(
            company_id=company_id,
            term=derived.term,
            kind=derived.kind,
            origin=KeywordOrigin.DERIVED,
            weight=derived.weight,
            enabled=True,
            created_by_type=principal.type.value if principal else "system",
            created_by_id=principal.id if principal else None,
        )
        db.add(row)
        created.append(row)
        existing.add(derived.term.lower())
        room -= 1
    if created:
        db.flush()
        audit.record(
            db,
            company_id=company_id,
            principal=principal,
            action="radar.keywords.derived",
            target_type="radar_keyword",
            target_id=None,
            summary=f"Derived {len(created)} radar keyword(s) from the company profile",
            data={"terms": [k.term for k in created]},
        )
    return created
