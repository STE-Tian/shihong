import shutil
from pathlib import Path

from helpers import make_db, make_settings
from hongsearch.collectors.base import CollectorError
from hongsearch.collectors.mediacrawler import MediaCrawlerCollector, parse_output_dir, scrub
from hongsearch.collectors.runner import prepare_argv
from hongsearch.config import live_problems
from hongsearch.models import Comment, Item
from hongsearch.service import SearchService

FIXTURES = Path(__file__).parent / "fixtures"


def test_parse_xhs_drops_private_ids(tmp_path):
    target = tmp_path / "xhs"
    target.mkdir()
    shutil.copy(FIXTURES / "xhs_contents.jsonl", target / "search_contents_2026-11-25.jsonl")
    shutil.copy(FIXTURES / "xhs_comments.jsonl", target / "search_comments_2026-11-25.jsonl")
    items = parse_output_dir(target, "xhs")
    assert [item.external_id for item in items] == ["n1", "n2"]
    first = items[0]
    assert first.author == "周末去吃"
    assert first.like_count == 12000
    assert first.tags == ["寿司", "广州"]
    assert first.url.endswith("/n1")
    assert [comment.content for comment in first.comments] == ["要排队"]
    blob = " ".join([first.title, first.content, first.author, first.url, *first.tags])
    assert "should-not-leak" not in blob
    assert "secret-user" not in first.comments[0].author
    assert items[1].author == "未署名"
    assert items[1].title == "只有正文的笔记"
    assert items[1].published_at == "2026-11-01"


def test_parse_douyin_uses_public_url(tmp_path):
    target = tmp_path / "dy"
    target.mkdir()
    shutil.copy(FIXTURES / "dy_contents.jsonl", target / "search_contents_2026-11-25.jsonl")
    shutil.copy(FIXTURES / "dy_comments.jsonl", target / "search_comments_2026-11-25.jsonl")
    items = parse_output_dir(target, "dy")
    assert len(items) == 1
    assert items[0].author == "阿*"
    assert items[0].url == "https://www.douyin.com/video/v1"
    assert items[0].comments[0].content == "注意安全"
    assert "hash-should-stay-out" not in items[0].content


def test_cookie_never_enters_argv(monkeypatch):
    secret = "web_session=supersecretvalue"
    monkeypatch.setenv("PLATFORM", "xhs")
    monkeypatch.setenv("KEYWORDS", "广州 日料店")
    monkeypatch.setenv("LOGIN_TYPE", "cookie")
    monkeypatch.setenv("SAVE_DATA_PATH", "/tmp/hongsearch-out")
    monkeypatch.setenv("MAX_NOTES", "8")
    monkeypatch.setenv("MAX_COMMENTS", "5")
    monkeypatch.setenv("HEADLESS", "false")
    monkeypatch.setenv("PLATFORM_COOKIES", secret)
    argv = prepare_argv()
    assert "--cookies" not in argv
    assert secret not in " ".join(argv)
    assert argv[argv.index("--enable_ip_proxy") + 1] == "false"
    assert argv[argv.index("--get_sub_comment") + 1] == "false"
    assert argv[argv.index("--max_concurrency_num") + 1] == "1"


def test_parent_command_and_scrub(tmp_path):
    secret = "web_session=supersecretvalue"
    settings = make_settings(
        tmp_path,
        mediacrawler_home=str(tmp_path),
        mediacrawler_python="uv run python",
        xhs_cookies=secret,
    )
    collector = MediaCrawlerCollector(settings)
    command = collector.build_command()
    assert command[:3] == ["uv", "run", "python"]
    assert secret not in " ".join(command)
    env = collector.build_env("xhs", "广州 日料店", tmp_path)
    assert env["PLATFORM_COOKIES"] == secret
    assert env["KEYWORDS"] == "广州 日料店"
    assert scrub(f"失败 {secret}", [secret]) == "失败 [已隐藏]"


def test_missing_cookie_skips_platform_without_calling_it(tmp_path):
    settings = make_settings(tmp_path, xhs_cookies="cookie-value-xhs", dy_cookies="")
    called = []

    def runner(platform, query):
        called.append(platform)
        return [_sample("xhs", "a")], "", None

    collector = MediaCrawlerCollector(settings, runner=runner, sleeper=lambda _seconds: called.append("sleep"))
    collection = collector.collect(["广州", "日料店"], ["xhs", "dy"])
    assert called == ["xhs"]
    assert collection.failed is False
    assert any("抖音" in warning for warning in collection.warnings)


def test_live_problems_block_before_runner(tmp_path):
    settings = make_settings(tmp_path, mediacrawler_home="", login_type="cookie")
    called = {"n": 0}

    def runner(platform, query):
        called["n"] += 1
        return [], "", None

    database = make_db(settings)
    service = SearchService(settings, database, MediaCrawlerCollector(settings, runner=runner))
    outcome = service.search("广州有哪些值得推荐的日料店？", ["xhs", "dy"], "all", True)
    assert outcome.error
    assert "MEDIACRAWLER_HOME" in outcome.error
    assert called["n"] == 0
    assert live_problems(settings, ["xhs"])


def test_live_runner_sleeps_between_platforms(tmp_path):
    home = tmp_path / "MediaCrawler"
    home.mkdir()
    (home / "main.py").write_text("# stub\n", encoding="utf-8")
    settings = make_settings(
        tmp_path,
        mediacrawler_home=str(home),
        xhs_cookies="xhs-cookie-value",
        dy_cookies="dy-cookie-value",
    )
    sleeps = []

    def runner(platform, query):
        assert "xhs-cookie-value" not in query
        return [_sample(platform, platform)], "", None

    collector = MediaCrawlerCollector(settings, runner=runner, sleeper=sleeps.append)
    database = make_db(settings)
    outcome = SearchService(settings, database, collector).search(
        "广州有哪些值得推荐的日料店？",
        ["xhs", "dy"],
        "all",
        True,
    )
    assert outcome.mode == "live"
    assert outcome.error is None
    assert sleeps == [2]
    assert {item.item.platform for item in outcome.items} == {"xhs", "dy"}


def test_failed_runner_does_not_save(tmp_path):
    home = tmp_path / "MediaCrawler"
    home.mkdir()
    (home / "main.py").write_text("# stub\n", encoding="utf-8")
    settings = make_settings(tmp_path, mediacrawler_home=str(home), xhs_cookies="xhs-cookie-value")

    def runner(platform, query):
        raise CollectorError("登录窗口已关闭")

    database = make_db(settings)
    outcome = SearchService(
        settings,
        database,
        MediaCrawlerCollector(settings, runner=runner),
    ).search("广州有哪些值得推荐的日料店？", ["xhs"], "all", True)
    assert outcome.error
    assert outcome.search_id is None
    assert database.list_history() == []


def _sample(platform: str, external_id: str) -> Item:
    return Item(
        platform=platform,
        external_id=external_id,
        title="广州日料店样本",
        content="这是一家广州日料店",
        author="作者",
        published_at="2026-11-20",
        like_count=3,
        collect_count=1,
        comment_count=1,
        share_count=0,
        url=f"https://example.com/{external_id}",
        comments=[Comment(author="评论", content="还行", like_count=1)],
    )


def test_items_table_has_no_user_id(tmp_path):
    database = make_db(make_settings(tmp_path))
    with database._lock:
        conn = database._connect()
        try:
            columns = [row[1] for row in conn.execute("PRAGMA table_info(items)")]
        finally:
            conn.close()
    assert "user_id" not in columns
    assert "ip_location" not in columns
