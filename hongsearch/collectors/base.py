"""采集器的共同约定。具体平台访问只放在可选的 MediaCrawler 适配器里。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from hongsearch.models import Item


class CollectorError(RuntimeError):
    pass


@dataclass
class Collection:
    items: list[Item] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    logs: str = ""
    failed: bool = False


class Collector(Protocol):
    mode: str

    def collect(self, keywords: list[str], platforms: list[str]) -> Collection:
        """返回候选内容。排序和时间过滤由检索服务负责。"""
