"""演示采集器：只返回内置样本。"""

from __future__ import annotations

from hongsearch.collectors.base import Collection
from hongsearch.collectors.mock_data import demo_items


class MockCollector:
    mode = "demo"

    def collect(self, keywords: list[str], platforms: list[str]) -> Collection:
        del keywords
        selected = [item for item in demo_items() if item.platform in platforms]
        return Collection(items=selected)
