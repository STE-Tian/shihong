from helpers import make_db, make_settings
from hongsearch.collectors.base import Collection
from hongsearch.collectors.mock import MockCollector
from hongsearch.service import SearchService


class Recorder:
    def __init__(self):
        self.calls = 0

    mode = "demo"

    def collect(self, keywords, platforms, progress=None):
        self.calls += 1
        return Collection(items=[])


def test_demo_queries_cover_the_examples(tmp_path):
    settings = make_settings(tmp_path)
    database = make_db(settings)
    service = SearchService(settings, database, MockCollector())

    guangzhou = service.search("广州有哪些值得推荐的日料店？", ["xhs", "dy"], "all", True)
    assert guangzhou.error is None
    assert {item.item.platform for item in guangzhou.items} == {"xhs", "dy"}
    assert guangzhou.search_id is not None
    assert any(comment.content for item in guangzhou.items for comment in item.item.comments)

    gpu = service.search("最近 RTX 5070 Ti 有哪些用户反馈？", ["xhs", "dy"], "all", True)
    assert gpu.keywords == ["RTX 5070 Ti"]
    assert len(gpu.items) >= 4

    pqc = service.search("最近一个月关于 PQC 的讨论有哪些？", ["xhs", "dy"], "all", True)
    assert pqc.time_range == "30"
    ids = {item.item.external_id for item in pqc.items}
    assert "demo-xhs-pqc-01" in ids
    assert "demo-dy-pqc-02" not in ids
    assert "demo-xhs-pqc-04" not in ids

    dryer = service.search("小红书上大家对戴森吹风机的评价是什么？", ["xhs", "dy"], "all", True)
    assert dryer.platforms == ["xhs"]
    assert dryer.items
    assert all(item.item.platform == "xhs" for item in dryer.items)

    riding = service.search("帮我找出抖音上城市骑行的热门视频", ["xhs", "dy"], "all", True)
    assert riding.platforms == ["dy"]
    assert len(riding.items) >= 3
    assert all(item.item.platform == "dy" for item in riding.items)

    vague = service.search("帮我找出抖音上某个话题的热门视频", ["xhs", "dy"], "all", True)
    assert vague.error
    assert vague.search_id is None
    assert vague.warnings == []


def test_cache_then_force_refresh(tmp_path):
    settings = make_settings(tmp_path, min_cache_results=3)
    database = make_db(settings)
    query = "广州有哪些值得推荐的日料店？"
    first = SearchService(settings, database, MockCollector()).search(query, ["xhs", "dy"], "all", False)
    assert first.mode == "demo"
    loaded = database.get_outcome(first.search_id)
    assert loaded is not None
    assert loaded.items[0].item.title == first.items[0].item.title

    recorder = Recorder()
    second = SearchService(settings, database, recorder).search(query, ["xhs", "dy"], "all", False)
    assert recorder.calls == 0
    assert second.mode == "cache"
    assert len(second.items) >= 3

    third = SearchService(settings, database, recorder).search(query, ["xhs", "dy"], "all", True)
    assert recorder.calls == 1
    assert third.mode == "demo"


def test_history_lists_saved_search(tmp_path):
    settings = make_settings(tmp_path)
    database = make_db(settings)
    SearchService(settings, database, MockCollector()).search(
        "最近 RTX 5070 Ti 有哪些用户反馈？",
        ["xhs"],
        "30",
        True,
    )
    history = database.list_history()
    assert history[0]["query"].startswith("最近 RTX")
    assert history[0]["mode"] == "demo"
    assert history[0]["result_count"] >= 1
