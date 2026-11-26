import threading

from helpers import make_db, make_settings
from hongsearch.collectors.base import Collection
from hongsearch.collectors.mock import MockCollector
from hongsearch.jobs import Job, get_job, run_search_job, start_search_job
from hongsearch.models import Comment, Item
from hongsearch.progress import Progress
from hongsearch.service import SearchService


def test_demo_search_records_steps_in_order(tmp_path):
    settings = make_settings(tmp_path)
    database = make_db(settings)
    progress = Progress()
    outcome = SearchService(settings, database, MockCollector()).search(
        "广州有哪些值得推荐的日料店？",
        ["xhs", "dy"],
        "all",
        True,
        progress=progress,
    )
    keys = [step.key for step in progress.snapshot()]
    assert keys.index("understand") < keys.index("demo")
    assert "rank" in keys
    assert outcome.items
    assert progress.snapshot()[0].state == "done"


def test_cancel_before_collect_does_not_save(tmp_path):
    settings = make_settings(tmp_path)
    database = make_db(settings)
    progress = Progress()
    progress.request_cancel()
    outcome = SearchService(settings, database, MockCollector()).search(
        "广州有哪些值得推荐的日料店？",
        ["xhs"],
        "all",
        True,
        progress=progress,
    )
    assert outcome.error == "检索已取消。"
    assert outcome.search_id is None
    assert database.list_history() == []


def test_partial_live_result_is_saved_and_warned(tmp_path):
    home = tmp_path / "MediaCrawler"
    home.mkdir()
    (home / "main.py").write_text("# stub\n", encoding="utf-8")
    settings = make_settings(tmp_path, mediacrawler_home=str(home), login_type="qrcode")

    class Partial:
        mode = "live"

        def collect(self, keywords, platforms, progress=None):
            return Collection(
                items=[_sample()],
                warnings=["小红书采集在 240 秒时仍未结束，已停止这次任务，并保留已经写好的 1 条公开内容。"],
            )

    outcome = SearchService(settings, make_db(settings), Partial()).search(
        "广州有哪些值得推荐的日料店？",
        ["xhs"],
        "all",
        True,
    )
    assert outcome.error is None
    assert outcome.items
    assert outcome.search_id is not None
    assert any("保留" in warning for warning in outcome.warnings)


def test_start_search_job_returns_while_collector_blocks(tmp_path, monkeypatch):
    settings = make_settings(tmp_path)
    database = make_db(settings)
    started = threading.Event()
    release = threading.Event()

    class Slow:
        mode = "demo"

        def collect(self, keywords, platforms, progress=None):
            started.set()
            release.wait(5)
            return Collection(items=[])

    monkeypatch.setattr("hongsearch.jobs.build_collector", lambda mode, settings: Slow())
    job_id = start_search_job(settings, database, "demo", "广州有哪些值得推荐的日料店？", ["xhs"], "all", True)
    try:
        assert started.wait(2)
        job = get_job(job_id)
        assert job is not None
        assert job.finished.is_set() is False
    finally:
        release.set()
        job = get_job(job_id)
        assert job is not None
        assert job.finished.wait(2)


def test_search_job_hides_cookie_in_unexpected_errors(tmp_path, monkeypatch):
    secret = "web_session=supersecretvalue"
    settings = make_settings(tmp_path, xhs_cookies=secret)
    database = make_db(settings)

    class Boom:
        mode = "demo"

        def collect(self, keywords, platforms, progress=None):
            raise RuntimeError(f"bad {secret}")

    monkeypatch.setattr("hongsearch.jobs.build_collector", lambda mode, settings: Boom())
    job = Job("search")
    run_search_job(job, settings, database, "demo", "广州有哪些值得推荐的日料店？", ["xhs"], "all", True)
    assert job.outcome is not None
    assert job.outcome.error
    assert secret not in job.outcome.error
    assert "[已隐藏]" in job.outcome.error


def _sample() -> Item:
    return Item(
        platform="xhs",
        external_id="n1",
        title="广州日料店样本",
        content="这是一家广州日料店",
        author="作者",
        published_at="2026-11-20",
        like_count=3,
        collect_count=1,
        comment_count=1,
        share_count=0,
        url="https://example.com/n1",
        comments=[Comment(author="评论", content="还行", like_count=1)],
    )
