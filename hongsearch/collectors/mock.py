"""演示采集器：只返回内置样本。"""

from __future__ import annotations

from hongsearch.collectors.base import Collection
from hongsearch.collectors.mock_data import demo_items
from hongsearch.progress import Progress


class MockCollector:
    mode = "demo"

    def collect(
        self,
        keywords: list[str],
        platforms: list[str],
        progress: Progress | None = None,
    ) -> Collection:
        del keywords
        selected = [item for item in demo_items() if item.platform in platforms]
        if progress is not None:
            progress.step("demo", "读取演示数据", "running", "演示模式不访问小红书或抖音。")
            progress.step("demo", "读取演示数据", "done", f"样本 {len(selected)} 条。")
        return Collection(items=selected)
