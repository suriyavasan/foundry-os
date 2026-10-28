"""Deterministic keyword matching and scoring (no model).

An item matches when an enabled keyword appears in its title or excerpt as a whole word or phrase
(case-insensitive). A *negative* keyword drops the item. The score is::

    sum of matched keyword weights (a title hit counts double)
    + the source's weight (changelogs and UI galleries rank above chatter)
    + an engagement bonus, capped at 5 (points // 50 + comments // 20)

Sources that fetch per keyword already matched on the provider's side: the keyword the batch was
fetched for counts as matched even when the (truncated) excerpt does not repeat it.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

SOURCE_WEIGHT = {
    "changelog": 3,
    "UI inspiration": 2,
    "review site": 2,
    "research": 1,
    "social": 1,
    "forum": 1,
    "news": 1,
    "blog": 1,
    "launch site": 1,
}
ENGAGEMENT_BONUS_CAP = 5


@dataclass(frozen=True)
class KeywordRef:
    term: str
    kind: str  # product | competitor | topic | negative
    weight: int = 3


@dataclass
class Match:
    matched: list[str] = field(default_factory=list)
    score: int = 0
    negative: bool = False  # a negative keyword hit: drop the item


def _pattern(term: str) -> re.Pattern[str]:
    words = [re.escape(w) for w in term.split()]
    return re.compile(r"(?<![\w])" + r"\s+".join(words) + r"(?![\w])", re.IGNORECASE)


def engagement_bonus(engagement: Mapping[str, int] | None) -> int:
    if not engagement:
        return 0
    points = int(engagement.get("points", 0) or 0)
    comments = int(engagement.get("comments", 0) or 0)
    return min(ENGAGEMENT_BONUS_CAP, max(0, points) // 50 + max(0, comments) // 20)


class Matcher:
    def __init__(self, keywords: Iterable[KeywordRef]) -> None:
        self._positive: list[tuple[KeywordRef, re.Pattern[str]]] = []
        self._negative: list[re.Pattern[str]] = []
        self._by_term: dict[str, KeywordRef] = {}
        for kw in keywords:
            term = " ".join(kw.term.split())
            if not term:
                continue
            if kw.kind == "negative":
                self._negative.append(_pattern(term))
            else:
                ref = KeywordRef(term, kw.kind, kw.weight)
                self._positive.append((ref, _pattern(term)))
                self._by_term[term.lower()] = ref

    def match(
        self,
        title: str,
        excerpt: str,
        *,
        category: str,
        engagement: Mapping[str, int] | None = None,
        implied: str | None = None,
    ) -> Match:
        result = Match()
        for pattern in self._negative:
            if pattern.search(title) or pattern.search(excerpt):
                result.negative = True
                return result
        score = 0
        for ref, pattern in self._positive:
            in_title = bool(pattern.search(title))
            if in_title or pattern.search(excerpt):
                result.matched.append(ref.term)
                score += ref.weight * (2 if in_title else 1)
        if implied:
            ref = self._by_term.get(" ".join(implied.split()).lower())
            if ref is not None and ref.term not in result.matched:
                result.matched.append(ref.term)
                score += ref.weight
        score += SOURCE_WEIGHT.get(category, 1) + engagement_bonus(engagement)
        result.score = score
        return result
