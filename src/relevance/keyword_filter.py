"""关键词层评分。对应 schema 第 10.1 节。

keyword_score = clamp(positive_hit * 0.12 - negative_hit * 0.25, -1, 1)
negative_hit >= 2 时标记 reject_candidate（除非语义分极高，由 ranker 决定）。
"""

from __future__ import annotations

from dataclasses import dataclass

from src.models import ArxivPaper, ResearchProfile

POSITIVE_WEIGHT = 0.12
NEGATIVE_WEIGHT = 0.25


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


@dataclass
class KeywordResult:
    keyword_score: float
    positive_hits: list[str]
    negative_hits: list[str]
    reject_candidate: bool


def _count_hits(haystack: str, keywords: list[str]) -> list[str]:
    hits = []
    for kw in keywords:
        if kw.lower() in haystack:
            hits.append(kw)
    return hits


def score_keywords(paper: ArxivPaper, profile: ResearchProfile) -> KeywordResult:
    haystack = " ".join(
        [paper.title, paper.abstract, " ".join(paper.categories)]
    ).lower()

    positive_hits = _count_hits(haystack, profile.positive_keywords)
    negative_hits = _count_hits(haystack, profile.negative_keywords)

    raw = len(positive_hits) * POSITIVE_WEIGHT - len(negative_hits) * NEGATIVE_WEIGHT
    keyword_score = _clamp(raw, -1.0, 1.0)

    return KeywordResult(
        keyword_score=keyword_score,
        positive_hits=positive_hits,
        negative_hits=negative_hits,
        reject_candidate=len(negative_hits) >= 2,
    )
