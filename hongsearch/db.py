"""SQLite：检索历史、内容快照和深度分析。"""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

from hongsearch.models import Comment, Item, ResultItem, SavedAnalysis, SearchOutcome
from hongsearch.util import now_text


class Database:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def init(self) -> None:
        with self._lock:
            conn = self._connect()
            try:
                conn.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS searches (
                        id INTEGER PRIMARY KEY,
                        query TEXT NOT NULL,
                        keywords TEXT NOT NULL,
                        platforms TEXT NOT NULL,
                        time_range TEXT NOT NULL,
                        mode TEXT NOT NULL,
                        result_count INTEGER NOT NULL,
                        created_at TEXT NOT NULL,
                        intent TEXT NOT NULL DEFAULT '',
                        relaxed INTEGER NOT NULL DEFAULT 0,
                        crawler_query TEXT NOT NULL DEFAULT '',
                        warnings TEXT NOT NULL DEFAULT '[]'
                    );

                    CREATE TABLE IF NOT EXISTS items (
                        id INTEGER PRIMARY KEY,
                        search_id INTEGER NOT NULL,
                        platform TEXT NOT NULL,
                        external_id TEXT NOT NULL,
                        title TEXT NOT NULL,
                        content TEXT NOT NULL,
                        author TEXT NOT NULL,
                        published_at TEXT,
                        like_count INTEGER NOT NULL,
                        collect_count INTEGER NOT NULL,
                        comment_count INTEGER NOT NULL,
                        share_count INTEGER NOT NULL,
                        url TEXT NOT NULL,
                        tags TEXT NOT NULL,
                        source_keyword TEXT NOT NULL DEFAULT '',
                        relevance REAL NOT NULL DEFAULT 0,
                        FOREIGN KEY(search_id) REFERENCES searches(id)
                    );

                    CREATE TABLE IF NOT EXISTS comments (
                        id INTEGER PRIMARY KEY,
                        item_id INTEGER NOT NULL,
                        author TEXT NOT NULL,
                        content TEXT NOT NULL,
                        like_count INTEGER NOT NULL,
                        published_at TEXT,
                        FOREIGN KEY(item_id) REFERENCES items(id)
                    );

                    CREATE TABLE IF NOT EXISTS analyses (
                        id INTEGER PRIMARY KEY,
                        search_id INTEGER NOT NULL,
                        kind TEXT NOT NULL,
                        model TEXT NOT NULL DEFAULT '',
                        report TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        FOREIGN KEY(search_id) REFERENCES searches(id)
                    );

                    CREATE INDEX IF NOT EXISTS idx_items_search ON items(search_id);
                    CREATE INDEX IF NOT EXISTS idx_comments_item ON comments(item_id);
                    CREATE INDEX IF NOT EXISTS idx_analyses_search ON analyses(search_id);
                    """
                )
                conn.commit()
            finally:
                conn.close()

    def save_search(self, outcome: SearchOutcome) -> SearchOutcome:
        with self._lock:
            conn = self._connect()
            try:
                cursor = conn.execute(
                    """
                    INSERT INTO searches (
                        query, keywords, platforms, time_range, mode, result_count,
                        created_at, intent, relaxed, crawler_query, warnings
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        outcome.query,
                        json.dumps(outcome.keywords, ensure_ascii=False),
                        json.dumps(outcome.platforms, ensure_ascii=False),
                        outcome.time_range,
                        outcome.mode,
                        len(outcome.items),
                        now_text(),
                        outcome.intent,
                        1 if outcome.relaxed else 0,
                        outcome.crawler_query,
                        json.dumps(outcome.warnings, ensure_ascii=False),
                    ),
                )
                search_id = int(cursor.lastrowid)
                stored: list[ResultItem] = []
                for result in outcome.items:
                    item = result.item
                    item_cursor = conn.execute(
                        """
                        INSERT INTO items (
                            search_id, platform, external_id, title, content, author,
                            published_at, like_count, collect_count, comment_count,
                            share_count, url, tags, source_keyword, relevance
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            search_id,
                            item.platform,
                            item.external_id,
                            item.title,
                            item.content,
                            item.author,
                            item.published_at,
                            item.like_count,
                            item.collect_count,
                            item.comment_count,
                            item.share_count,
                            item.url,
                            json.dumps(item.tags, ensure_ascii=False),
                            item.source_keyword,
                            result.relevance,
                        ),
                    )
                    item_id = int(item_cursor.lastrowid)
                    for comment in item.comments:
                        conn.execute(
                            """
                            INSERT INTO comments (item_id, author, content, like_count, published_at)
                            VALUES (?, ?, ?, ?, ?)
                            """,
                            (
                                item_id,
                                comment.author,
                                comment.content,
                                comment.like_count,
                                comment.published_at,
                            ),
                        )
                    stored.append(ResultItem(item=item, relevance=result.relevance, db_id=item_id))
                conn.commit()
            finally:
                conn.close()
        outcome.search_id = search_id
        outcome.items = stored
        return outcome

    def find_recent_same(
        self,
        query: str,
        platforms: list[str],
        time_range: str,
        mode: str = "live",
        within_minutes: int = 10,
    ) -> SearchOutcome | None:
        """相同的实时检索在短时间内直接复用，避免误触重复打开浏览器。"""

        target = sorted(platforms)
        with self._lock:
            conn = self._connect()
            try:
                rows = [
                    dict(row)
                    for row in conn.execute(
                        """
                        SELECT id, query, platforms, time_range, created_at, mode
                        FROM searches
                        ORDER BY id DESC
                        LIMIT 30
                        """
                    ).fetchall()
                ]
            finally:
                conn.close()
        from datetime import datetime, timedelta

        from hongsearch.util import TZ

        now = datetime.now(TZ)
        for row in rows:
            if row["query"] != query or row["time_range"] != time_range or row["mode"] != mode:
                continue
            try:
                stored_platforms = sorted(json.loads(row["platforms"] or "[]"))
                created = datetime.strptime(row["created_at"], "%Y-%m-%d %H:%M").replace(tzinfo=TZ)
            except (ValueError, TypeError):
                continue
            if stored_platforms != target:
                continue
            if now - created <= timedelta(minutes=within_minutes):
                return self.get_outcome(int(row["id"]))
        return None

    def list_history(self, limit: int = 20) -> list[sqlite3.Row]:
        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute(
                    """
                    SELECT id, query, mode, result_count, created_at, platforms, time_range
                    FROM searches
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (limit,),
                ).fetchall()
            finally:
                conn.close()
        return rows

    def get_outcome(self, search_id: int) -> SearchOutcome | None:
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute("SELECT * FROM searches WHERE id = ?", (search_id,)).fetchone()
                if row is None:
                    return None
                item_rows = conn.execute(
                    """
                    SELECT * FROM items
                    WHERE search_id = ?
                    ORDER BY relevance DESC, id ASC
                    """,
                    (search_id,),
                ).fetchall()
                results: list[ResultItem] = []
                for item_row in item_rows:
                    comments = conn.execute(
                        "SELECT * FROM comments WHERE item_id = ? ORDER BY id ASC",
                        (item_row["id"],),
                    ).fetchall()
                    item = Item(
                        platform=item_row["platform"],
                        external_id=item_row["external_id"],
                        title=item_row["title"],
                        content=item_row["content"],
                        author=item_row["author"],
                        published_at=item_row["published_at"],
                        like_count=item_row["like_count"],
                        collect_count=item_row["collect_count"],
                        comment_count=item_row["comment_count"],
                        share_count=item_row["share_count"],
                        url=item_row["url"],
                        tags=json.loads(item_row["tags"] or "[]"),
                        comments=[
                            Comment(
                                author=comment["author"],
                                content=comment["content"],
                                like_count=comment["like_count"],
                                published_at=comment["published_at"],
                            )
                            for comment in comments
                        ],
                        source_keyword=item_row["source_keyword"],
                    )
                    results.append(
                        ResultItem(item=item, relevance=item_row["relevance"], db_id=item_row["id"])
                    )
            finally:
                conn.close()
        return SearchOutcome(
            search_id=row["id"],
            query=row["query"],
            keywords=json.loads(row["keywords"] or "[]"),
            platforms=json.loads(row["platforms"] or "[]"),
            time_range=row["time_range"],
            mode=row["mode"],
            items=results,
            warnings=json.loads(row["warnings"] or "[]"),
            relaxed=bool(row["relaxed"]),
            crawler_query=row["crawler_query"],
            intent=row["intent"],
        )

    def load_items_for_cache(self, limit: int = 500) -> list[Item]:
        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute(
                    """
                    SELECT * FROM items
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (limit,),
                ).fetchall()
                items: list[Item] = []
                seen: set[tuple[str, str]] = set()
                for row in rows:
                    key = (row["platform"], row["external_id"])
                    if key in seen:
                        continue
                    seen.add(key)
                    comments = conn.execute(
                        "SELECT * FROM comments WHERE item_id = ? ORDER BY id ASC",
                        (row["id"],),
                    ).fetchall()
                    items.append(
                        Item(
                            platform=row["platform"],
                            external_id=row["external_id"],
                            title=row["title"],
                            content=row["content"],
                            author=row["author"],
                            published_at=row["published_at"],
                            like_count=row["like_count"],
                            collect_count=row["collect_count"],
                            comment_count=row["comment_count"],
                            share_count=row["share_count"],
                            url=row["url"],
                            tags=json.loads(row["tags"] or "[]"),
                            comments=[
                                Comment(
                                    author=comment["author"],
                                    content=comment["content"],
                                    like_count=comment["like_count"],
                                    published_at=comment["published_at"],
                                )
                                for comment in comments
                            ],
                            source_keyword=row["source_keyword"],
                        )
                    )
            finally:
                conn.close()
        return items

    def save_analysis(self, search_id: int, kind: str, model: str, report: str) -> SavedAnalysis:
        created_at = now_text()
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    """
                    INSERT INTO analyses (search_id, kind, model, report, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (search_id, kind, model, report, created_at),
                )
                conn.commit()
            finally:
                conn.close()
        return SavedAnalysis(kind=kind, model=model, report=report, created_at=created_at)

    def latest_analysis(self, search_id: int) -> SavedAnalysis | None:
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    """
                    SELECT kind, model, report, created_at
                    FROM analyses
                    WHERE search_id = ?
                    ORDER BY id DESC
                    LIMIT 1
                    """,
                    (search_id,),
                ).fetchone()
            finally:
                conn.close()
        if row is None:
            return None
        return SavedAnalysis(
            kind=row["kind"],
            model=row["model"],
            report=row["report"],
            created_at=row["created_at"],
        )
