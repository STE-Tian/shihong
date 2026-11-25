from pathlib import Path

from hongsearch.config import Settings
from hongsearch.db import Database


def make_settings(tmp_path: Path, **overrides) -> Settings:
    values = dict(
        app_mode="demo",
        database_path=tmp_path / "hongsearch.db",
        llm_base_url="https://example.com/v1",
        llm_api_key="",
        llm_model="fake",
        mediacrawler_home="",
        mediacrawler_python="",
        login_type="cookie",
        xhs_cookies="",
        dy_cookies="",
        max_notes=8,
        max_comments=5,
        crawler_timeout_sec=30,
        inter_platform_sleep_sec=2,
        mediacrawler_headless=False,
        min_cache_results=3,
        display_limit=12,
    )
    values.update(overrides)
    return Settings(**values)


def make_db(settings: Settings) -> Database:
    database = Database(settings.database_path)
    database.init()
    return database
