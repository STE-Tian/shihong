"""检索流程：先看历史，不够再采集，然后按相关度排序。"""

from __future__ import annotations

from hongsearch.collectors.base import CollectorError
from hongsearch.config import Settings, live_problems
from hongsearch.db import Database
from hongsearch.models import ResultItem, SearchOutcome, UnderstoodQuery
from hongsearch.progress import Progress
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
        progress: Progress | None = None,
    ) -> SearchOutcome:
        progress = progress or Progress()
        cleaned = (query or "").strip()[:200]
        progress.step("understand", "理解问题", "running", "正在拆关键词。")
        understood = understand_query(cleaned)
        if not cleaned:
            progress.step("understand", "理解问题", "error", "请输入问题或关键词。")
            return self._empty(cleaned, platforms, time_range, understood, "请输入问题或关键词。")
        effective_platforms, effective_time, warnings = resolve_filters(
            platforms, time_range, understood
        )
        if not understood.keywords:
            progress.step("understand", "理解问题", "error", "问题里还没有具体关键词。")
            return self._empty(
                cleaned,
                platforms,
                time_range,
                understood,
                "问题里还没有具体关键词。可以试试「广州 日料店」「RTX 5070 Ti」「PQC」或「城市骑行」。",
            )
        if not effective_platforms:
            progress.step("understand", "理解问题", "error", "请至少选择一个平台。")
            return self._empty(cleaned, effective_platforms, effective_time, understood, "请至少选择一个平台。", warnings)
        progress.step(
            "understand",
            "理解问题",
            "done",
            "关键词：" + "、".join(understood.keywords),
        )
        if progress.cancelled:
            return self._cancelled(cleaned, effective_platforms, effective_time, understood, warnings, progress)

        days = None if effective_time == "all" else int(effective_time)
        combined = crawler_query(understood.keywords)

        progress.step("cache", "查看本地缓存", "running", "先看本机有没有能直接用的结果。")
        if self.collector.mode == "live" and not force_refresh:
            recent = self.db.find_recent_same(cleaned, effective_platforms, effective_time, mode="live")
            if recent is not None:
                recent.warnings = [
                    "十分钟内做过相同的实时检索，这次直接展示那次结果。勾选「忽略缓存，重新检索」才会再打开采集。",
                    *recent.warnings,
                ]
                progress.step("cache", "查看本地缓存", "done", "十分钟内有相同的实时检索，直接展示那次结果。")
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
                progress.step("cache", "查看本地缓存", "done", f"历史里有 {len(ranked)} 条足够相关的内容。")
                return self.db.save_search(outcome)
        if force_refresh:
            progress.step("cache", "查看本地缓存", "done", "已选择忽略缓存。")
        else:
            progress.step("cache", "查看本地缓存", "done", "没有足够的历史结果，准备采集。")
        if progress.cancelled:
            return self._cancelled(cleaned, effective_platforms, effective_time, understood, warnings, progress)

        if self.collector.mode == "live":
            problems = live_problems(self.settings, effective_platforms)
            if problems:
                message = "暂时不能实时检索：" + "；".join(problems)
                progress.step("config", "检查实时配置", "error", message)
                return self._empty(
                    cleaned,
                    effective_platforms,
                    effective_time,
                    understood,
                    message,
                    warnings,
                )

        progress.step("collect", "采集公开内容", "running", "只采集公开笔记、视频和一级评论。")
        try:
            collection = self.collector.collect(understood.keywords, effective_platforms, progress=progress)
        except CollectorError as exc:
            progress.step("collect", "采集公开内容", "error", str(exc))
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
            if any("已取消" in item for item in warnings):
                message = "检索已取消。"
            else:
                message = "这次没有采集到内容。"
            progress.step("collect", "采集公开内容", "error", message)
            outcome = self._empty(
                cleaned,
                effective_platforms,
                effective_time,
                understood,
                message,
                warnings,
            )
            outcome.logs = collection.logs
            return outcome

        progress.step("collect", "采集公开内容", "done")
        in_range = [
            item
            for item in collection.items
            if item.platform in effective_platforms and within_days(item.published_at, days)
        ]
        if collection.items and not in_range:
            warnings.append("采集到了内容，但没有落在所选时间范围内。可以把时间改成「不限」再看。")
        progress.step("rank", "按相关度整理", "running")
        ranked, relaxed = rank_items(in_range, understood.keywords, self.settings.display_limit)
        if relaxed:
            warnings.append("没有内容同时覆盖全部关键词，下面是放宽后的结果。")
        if progress.cancelled and not any("已取消" in item for item in warnings):
            warnings.append("检索已取消，下面是已经采集到的内容。")
        progress.step("rank", "按相关度整理", "done", f"保留 {len(ranked)} 条。")
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
        progress.step("save", "写入本地记录", "running")
        saved = self.db.save_search(outcome)
        progress.step("save", "写入本地记录", "done")
        return saved

    def _cancelled(
        self,
        query: str,
        platforms: list[str],
        time_range: str,
        understood: UnderstoodQuery,
        warnings: list[str],
        progress: Progress,
    ) -> SearchOutcome:
        progress.step("cancel", "取消检索", "done", "已停止，还没有新的采集结果。")
        return self._empty(query, platforms, time_range, understood, "检索已取消。", warnings)

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
