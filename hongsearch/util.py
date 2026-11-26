"""日期、数字和短文本的小工具。"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Shanghai")

PLATFORM_LABELS = {"xhs": "小红书", "dy": "抖音"}


def now_text() -> str:
    return datetime.now(TZ).strftime("%Y-%m-%d %H:%M")


def today() -> date:
    return datetime.now(TZ).date()


def days_ago(days: int, base: date | None = None) -> str:
    anchor = base or today()
    return (anchor - timedelta(days=days)).isoformat()


def within_days(published_at: str | None, days: int | None, base: date | None = None) -> bool:
    if days is None:
        return True
    if not published_at:
        return False
    try:
        published = date.fromisoformat(published_at[:10])
    except ValueError:
        return False
    anchor = base or today()
    delta = (anchor - published).days
    return 0 <= delta <= days


def parse_count(value: object) -> int:
    if value is None or value == "":
        return 0
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value).strip().replace(",", "").replace("，", "")
    if not text:
        return 0
    multiplier = 1
    if text.endswith("亿"):
        multiplier = 100_000_000
        text = text[:-1]
    elif text.endswith("万"):
        multiplier = 10_000
        text = text[:-1]
    try:
        return int(float(text) * multiplier)
    except ValueError:
        return 0


def parse_timestamp(value: object) -> str | None:
    if value is None or value == "" or value == 0:
        return None
    if isinstance(value, str):
        stripped = value.strip()
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}.*", stripped):
            return stripped[:10]
        if re.fullmatch(r"\d+(\.\d+)?", stripped):
            value = float(stripped)
        else:
            return None
    if isinstance(value, (int, float)):
        ts = float(value)
        if ts > 10**11:
            ts /= 1000
        try:
            return datetime.fromtimestamp(ts, TZ).date().isoformat()
        except (OverflowError, OSError, ValueError):
            return None
    return None


def format_count(value: int) -> str:
    number = int(value or 0)
    if number < 10_000:
        return str(number)
    if number < 100_000_000:
        text = f"{number / 10_000:.1f}".rstrip("0").rstrip(".")
        return f"{text}万"
    text = f"{number / 100_000_000:.1f}".rstrip("0").rstrip(".")
    return f"{text}亿"


def snippet(text: str, limit: int = 110) -> str:
    compact = re.sub(r"\s+", " ", text or "").strip()
    if len(compact) <= limit:
        return compact
    return compact[:limit].rstrip() + "…"


def platform_label(platform: str) -> str:
    return PLATFORM_LABELS.get(platform, platform)


def scrub(text: str, secrets: list[str]) -> str:
    """把已知的 Cookie 和密钥从日志里换成占位符。短字符串不处理，避免误伤普通文本。"""

    cleaned = text or ""
    for secret in secrets:
        if secret and len(secret) >= 6:
            cleaned = cleaned.replace(secret, "[已隐藏]")
    return cleaned


def clamp_int(value: object, default: int, low: int, high: int) -> int:
    try:
        number = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return max(low, min(high, number))
