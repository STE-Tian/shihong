"""检索结果和查询理解用到的数据结构。"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Comment:
    author: str
    content: str
    like_count: int = 0
    published_at: str | None = None


@dataclass
class Item:
    """一条公开笔记或视频。不保存用户 ID、头像、私信或精确位置。"""

    platform: str
    external_id: str
    title: str
    content: str
    author: str
    published_at: str | None
    like_count: int
    collect_count: int
    comment_count: int
    share_count: int
    url: str
    tags: list[str] = field(default_factory=list)
    comments: list[Comment] = field(default_factory=list)
    source_keyword: str = ""


@dataclass
class ResultItem:
    item: Item
    relevance: float
    db_id: int | None = None


@dataclass
class UnderstoodQuery:
    raw: str
    keywords: list[str]
    platform_hint: str | None
    day_hint: int | None
    intent: str


@dataclass
class SearchOutcome:
    search_id: int | None
    query: str
    keywords: list[str]
    platforms: list[str]
    time_range: str
    mode: str
    items: list[ResultItem]
    warnings: list[str]
    error: str | None = None
    logs: str = ""
    relaxed: bool = False
    crawler_query: str = ""
    intent: str = ""


@dataclass
class SavedAnalysis:
    kind: str
    model: str
    report: str
    created_at: str
