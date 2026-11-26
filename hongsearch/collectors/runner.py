"""在 MediaCrawler 自己的虚拟环境里启动一次小规模关键词搜索。

本文件只用标准库。它不实现签名、验证码、代理或登录绕过。
Cookie 从环境变量读入 MediaCrawler 的配置，不会放进命令行参数。
"""

from __future__ import annotations

import os
import sys


def prepare_argv() -> list[str]:
    notes = _clamp(os.environ.get("MAX_NOTES", "8"), 8, 1, 20)
    comments = _clamp(os.environ.get("MAX_COMMENTS", "5"), 5, 0, 10)
    headless = os.environ.get("HEADLESS", "false")
    return [
        "main.py",
        "--platform",
        os.environ["PLATFORM"],
        "--lt",
        os.environ.get("LOGIN_TYPE", "cookie"),
        "--type",
        "search",
        "--keywords",
        os.environ.get("KEYWORDS", ""),
        "--save_data_option",
        "jsonl",
        "--save_data_path",
        os.environ["SAVE_DATA_PATH"],
        "--crawler_max_notes_count",
        str(notes),
        "--max_comments_count_singlenotes",
        str(comments),
        "--max_concurrency_num",
        "1",
        "--get_comment",
        "true" if comments else "false",
        "--get_sub_comment",
        "false",
        "--get_media",
        "false",
        "--enable_ip_proxy",
        "false",
        "--headless",
        headless,
        "--start",
        "1",
    ]


def _clamp(raw: str, default: int, low: int, high: int) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, value))


def main() -> None:
    home = os.environ["MEDIACRAWLER_HOME"]
    print("拾红：正在启动 MediaCrawler", flush=True)
    os.chdir(home)
    sys.path.insert(0, home)
    secret = os.environ.get("PLATFORM_COOKIES", "")
    argv = prepare_argv()
    if secret and any(secret in arg for arg in argv):
        print("拒绝启动：Cookie 不应出现在命令行参数中。", file=sys.stderr)
        raise SystemExit(2)
    sys.argv = argv

    import config

    config.COOKIES = secret
    config.ENABLE_IP_PROXY = False
    config.ENABLE_GET_MEDIA = False
    config.ENABLE_GET_SUB_COMMENTS = False
    config.ENABLE_GET_WORDCLOUD = False
    config.MAX_CONCURRENCY_NUM = 1
    try:
        sleep_sec = int(os.environ.get("SLEEP_SEC", "3"))
    except ValueError:
        sleep_sec = 3
    config.CRAWLER_MAX_SLEEP_SEC = max(2, sleep_sec)

    from main import async_cleanup
    from main import main as crawl_main

    try:
        from tools.app_runner import run
    except Exception:
        import asyncio

        asyncio.run(crawl_main())
        return
    run(crawl_main, async_cleanup, cleanup_timeout_seconds=15.0)


if __name__ == "__main__":
    main()
