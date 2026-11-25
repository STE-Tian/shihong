from hongsearch.collectors.base import Collection, CollectorError
from hongsearch.collectors.mock import MockCollector

__all__ = ["Collection", "CollectorError", "MockCollector", "build_collector"]


def build_collector(mode: str, settings):
    if mode == "live":
        from hongsearch.collectors.mediacrawler import MediaCrawlerCollector

        return MediaCrawlerCollector(settings)
    return MockCollector()
