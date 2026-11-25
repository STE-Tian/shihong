"""检索流程：先看历史，不够再采集，然后按相关度排序。"""

from __future__ import annotations

from hongsearch.collectors.base import CollectorError
from hongsearch.config import Settings, live_problems
from hongsearch.db import Database
from hongsearch.models import ResultItem, SearchOutcome, UnderstoodQuery
from hongsearch.rank import rank_items
from hongsearch.text import crawler_query, resolve_filters, understand_query
from hongsearch.util import within_days


class SearchService:
    def __init__(self, settings: Settings, database: Database, collector):
        self.settings = settings
        self.db = database
        self.collector = collector

    def search(
        self,
        query: str,
        platforms: list[str],
        time_range: str,
        force_refresh: bool,
    ) -> SearchOutcome:
        cleaned = (query or "").strip()[:200]
        understood = understand_query(cleaned)
        if not cleaned:
            return self._empty(cleaned, platforms, time_range, understood, "请输入问题或关键词。")
        effective_platforms, effective_time, warnings = resolve_filters(
            platforms, time_range, understood
        )
        if not understood.keywords:
            return self._empty(
                cleaned,
                platforms,
                time_range,
                understood,
                "问题里还没有具体关键词。可以试试「广州 日料店」「RTX 5070 Ti」「PQC」或「城市骑行」。",
            )
        if not effective_platforms:
            return self._empty(cleaned, effective_platforms, effective_time, understood, "请至少选择一个平台。", warnings)

        days = None if effective_time == "all" else int(effective_time)
        combined = crawler_query(understood.keywords)

        if self.collector.mode == "live" and not force_refresh:
            recent = self.db.find_recent_same(cleaned, effective_platforms, effective_time, mode="live")
            if recent is not None:
                recent.warnings = [
                    "十分钟内做过相同的实时检索，这次直接展示那次结果。勾选「忽略缓存，重新检索」才会再打开采集。",
                    *recent.warnings,
                ]
                return recent

        if not force_refresh:
            cached = [
                item
                for item in self.db.load_items_for_cache()
                if item.platform in effective_platforms and within_days(item.published_at, days)
            ]
            ranked, relaxed = rank_items(cached, understood.keywords, self.settings.display_limit)
            if ranked and not relaxed and len(ranked) >= self.settings.min_cache_results:
                outcome = self._pack(
                    cleaned,
                    understood,
                    effective_platforms,
                    effective_time,
                    "cache",
                    ranked,
                    warnings,
                    combined,
                    relaxed,
                )
                return self.db.save_search(outcome)

        if self.collector.mode == "live":
            problems = live_problems(self.settings, effective_platforms)
            if problems:
                return self._empty(
                    cleaned,
                    effective_platforms,
                    effective_time,
                    understood,
                    "暂时不能实时检索：" + "；".join(problems),
                    warnings,
                )

        try:
            collection = self.collector.collect(understood.keywords, effective_platforms)
        except CollectorError as exc:
            return self._empty(
                cleaned,
                effective_platforms,
                effective_time,
                understood,
                str(exc),
                warnings,
            )

        warnings = [*warnings, *collection.warnings]
        if collection.failed and not collection.items:
            outcome = self._empty(
                cleaned,
                effective_platforms,
                effective_time,
                understood,
                "这次没有采集到内容。",
                warnings,
            )
            outcome.logs = collection.logs
            return outcome

        in_range = [
            item
            for item in collection.items
            if item.platform in effective_platforms and within_days(item.published_at, days)
        ]
        if collection.items and not in_range:
            warnings.append("采集到了内容，但没有落在所选时间范围内。可以把时间改成「不限」再看。")
        ranked, relaxed = rank_items(in_range, understood.keywords, self.settings.display_limit)
        if relaxed:
            warnings.append("没有内容同时覆盖全部关键词，下面是放宽后的结果。")
        mode = "demo" if self.collector.mode == "demo" else "live"
        outcome = self._pack(
            cleaned,
            understood,
            effective_platforms,
            effective_time,
            mode,
            ranked,
            warnings,
            combined,
            relaxed,
            collection.logs,
        )
        return self.db.save_search(outcome)

    def _pack(
        self,
        query: str,
        understood: UnderstoodQuery,
        platforms: list[str],
        time_range: str,
        mode: str,
        ranked: list[tuple],
        warnings: list[str],
        combined: str,
        relaxed: bool,
        logs: str = "",
    ) -> SearchOutcome:
        return SearchOutcome(
            search_id=None,
            query=query,
            keywords=understood.keywords,
            platforms=platforms,
            time_range=time_range,
            mode=mode,
            items=[ResultItem(item=item, relevance=score) for item, score in ranked],
            warnings=warnings,
            relaxed=relaxed,
            crawler_query=combined,
            intent=understood.intent,
            logs=logs,
        )

    def _empty(
        self,
        query: str,
        platforms: list[str],
        time_range: str,
        understood: UnderstoodQuery,
        message: str,
        warnings: list[str] | None = None,
    ) -> SearchOutcome:
        return SearchOutcome(
            search_id=None,
            query=query,
            keywords=understood.keywords,
            platforms=platforms,
            time_range=time_range,
            mode=getattr(self.collector, "mode", ""),
            items=[],
            warnings=list(warnings or []),
            error=message,
            intent=understood.intent,
            crawler_query=crawler_query(understood.keywords),
        )
