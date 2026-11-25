"""按关键词给候选内容打分。相关度优先于互动量。"""

from __future__ import annotations

import math
import re

from hongsearch.models import Item

_SPACE = re.compile(r"\s+")


def rank_items(
    items: list[Item],
    keywords: list[str],
    limit: int,
) -> tuple[list[tuple[Item, float]], bool]:
    if not keywords:
        return [], False
    deduped = _dedupe(items)
    strict = _score_all(deduped, keywords, require_all=True)
    if strict or len(keywords) == 1:
        return _sort(strict)[:limit], False
    loose = _score_all(deduped, keywords, require_all=False)
    return _sort(loose)[:limit], True


def _dedupe(items: list[Item]) -> list[Item]:
    seen: set[tuple[str, str]] = set()
    kept: list[Item] = []
    for item in items:
        key = (item.platform, item.external_id)
        if key in seen:
            continue
        seen.add(key)
        kept.append(item)
    return kept


def _score_all(items: list[Item], keywords: list[str], require_all: bool) -> list[tuple[Item, float]]:
    scored: list[tuple[Item, float]] = []
    for item in items:
        score = score_item(item, keywords, require_all=require_all)
        if score > 0:
            scored.append((item, score))
    return scored


def score_item(item: Item, keywords: list[str], require_all: bool) -> float:
    fields = {
        "title": item.title or "",
        "body": item.content or "",
        "tags": " ".join(item.tags),
        "comments": " ".join(comment.content for comment in item.comments),
    }
    weights = {"title": 6.0, "body": 3.0, "tags": 3.0, "comments": 1.0}
    matched = 0
    score = 0.0
    for keyword in keywords:
        hit = False
        for name, text in fields.items():
            if contains(text, keyword):
                score += weights[name]
                hit = True
        if hit:
            matched += 1
    if matched == 0:
        return 0.0
    if require_all and matched < len(keywords):
        return 0.0
    engagement = item.like_count + item.collect_count * 2 + item.comment_count
    score += min(3.0, math.log10(engagement + 1))
    return score


def contains(haystack: str, keyword: str) -> bool:
    if not haystack or not keyword:
        return False
    if keyword.lower() in haystack.lower():
        return True
    compact_keyword = _SPACE.sub("", keyword).lower()
    if len(compact_keyword) < 3:
        return False
    return compact_keyword in _SPACE.sub("", haystack).lower()


def _sort(scored: list[tuple[Item, float]]) -> list[tuple[Item, float]]:
    return sorted(
        scored,
        key=lambda pair: (pair[1], pair[0].published_at or "", pair[0].like_count),
        reverse=True,
    )
