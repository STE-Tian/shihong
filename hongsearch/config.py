"""从环境变量读取配置。Cookie 和 API Key 只留在进程环境里。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from hongsearch.util import clamp_int

ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / ".env"


def env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


@dataclass(frozen=True)
class Settings:
    app_mode: str
    database_path: Path
    llm_base_url: str
    llm_api_key: str
    llm_model: str
    mediacrawler_home: str
    mediacrawler_python: str
    login_type: str
    xhs_cookies: str
    dy_cookies: str
    max_notes: int
    max_comments: int
    crawler_timeout_sec: int
    inter_platform_sleep_sec: float
    mediacrawler_headless: bool
    min_cache_results: int
    display_limit: int

    @property
    def llm_enabled(self) -> bool:
        return bool(self.llm_api_key.strip())

    def cookie_for(self, platform: str) -> str:
        if platform == "xhs":
            return self.xhs_cookies.strip()
        if platform == "dy":
            return self.dy_cookies.strip()
        return ""


def load_settings() -> Settings:
    load_dotenv(ENV_PATH, override=False)
    mode = os.getenv("APP_MODE", "auto").strip().lower()
    if mode not in {"auto", "demo", "live"}:
        mode = "auto"
    login_type = os.getenv("LOGIN_TYPE", "cookie").strip().lower()
    if login_type not in {"cookie", "qrcode"}:
        login_type = "cookie"
    database = os.getenv("DATABASE_PATH", "data/hongsearch.db").strip() or "data/hongsearch.db"
    database_path = Path(database)
    if not database_path.is_absolute():
        database_path = ROOT / database_path
    try:
        sleep_sec = float(os.getenv("INTER_PLATFORM_SLEEP_SEC", "3"))
    except ValueError:
        sleep_sec = 3.0
    sleep_sec = min(30.0, max(2.0, sleep_sec))
    return Settings(
        app_mode=mode,
        database_path=database_path,
        llm_base_url=os.getenv("LLM_BASE_URL", "https://api.deepseek.com/v1").strip(),
        llm_api_key=os.getenv("LLM_API_KEY", "").strip(),
        llm_model=os.getenv("LLM_MODEL", "deepseek-chat").strip() or "deepseek-chat",
        mediacrawler_home=os.getenv("MEDIACRAWLER_HOME", "").strip(),
        mediacrawler_python=os.getenv("MEDIACRAWLER_PYTHON", "").strip(),
        login_type=login_type,
        xhs_cookies=os.getenv("XHS_COOKIES", "").strip(),
        dy_cookies=os.getenv("DY_COOKIES", "").strip(),
        max_notes=clamp_int(os.getenv("MAX_NOTES"), 8, 1, 20),
        max_comments=clamp_int(os.getenv("MAX_COMMENTS"), 5, 0, 10),
        crawler_timeout_sec=clamp_int(os.getenv("CRAWLER_TIMEOUT_SEC"), 240, 30, 900),
        inter_platform_sleep_sec=sleep_sec,
        mediacrawler_headless=env_bool("MEDIACRAWLER_HEADLESS", False),
        min_cache_results=clamp_int(os.getenv("MIN_CACHE_RESULTS"), 3, 1, 12),
        display_limit=clamp_int(os.getenv("DISPLAY_LIMIT"), 12, 3, 20),
    )


def reload_settings() -> Settings:
    """侧边栏按钮使用：让 .env 里的新值覆盖当前进程。"""

    load_dotenv(ENV_PATH, override=True)
    return load_settings()


def has_mediacrawler(settings: Settings) -> bool:
    if not settings.mediacrawler_home:
        return False
    return (Path(settings.mediacrawler_home) / "main.py").is_file()


def has_credentials(settings: Settings) -> bool:
    if settings.login_type == "qrcode":
        return True
    return bool(settings.xhs_cookies or settings.dy_cookies)


def resolve_runtime_mode(settings: Settings, choice: str) -> tuple[str, str]:
    if choice == "demo":
        return "demo", "你选择了演示模式，不会访问小红书或抖音。"
    if choice == "auto":
        if has_mediacrawler(settings) and has_credentials(settings):
            return "live", "已检测到本机 MediaCrawler 和登录方式，本次可以实时检索公开内容。"
        return "demo", "还没有可用的 MediaCrawler 登录配置，当前使用演示数据。"
    return "live", "你选择了实时检索。登录确认和平台验证需要你自己在浏览器里完成。"


def live_problems(settings: Settings, platforms: list[str]) -> list[str]:
    problems: list[str] = []
    home = Path(settings.mediacrawler_home) if settings.mediacrawler_home else None
    if home is None or not str(home):
        problems.append("未设置 MEDIACRAWLER_HOME")
    elif not (home / "main.py").is_file():
        problems.append("MEDIACRAWLER_HOME 下没有找到 MediaCrawler 的 main.py")
    if settings.login_type == "cookie":
        missing = []
        if "xhs" in platforms and not settings.xhs_cookies:
            missing.append("小红书")
        if "dy" in platforms and not settings.dy_cookies:
            missing.append("抖音")
        if missing and len(missing) == len(platforms):
            problems.append("Cookie 登录还缺少" + "、".join(missing) + "的 Cookie")
    return problems
