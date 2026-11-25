"""从问句里抽出关键词、平台和时间范围。不调用大模型。"""

from __future__ import annotations

import logging
import re

import jieba

from hongsearch.models import UnderstoodQuery

jieba.setLogLevel(logging.ERROR)
jieba.initialize()

USER_WORDS = (
    "日料店",
    "日料",
    "居酒屋",
    "回转寿司",
    "后量子密码",
    "后量子",
    "城市骑行",
    "吹风机",
    "戴森",
    "徕芬",
    "显卡",
    "寿司",
    "密钥交换",
)
for _word in USER_WORDS:
    jieba.add_word(_word)

FILLERS = (
    "有哪些值得推荐的",
    "值得推荐的",
    "值得推荐",
    "用户反馈",
    "评价是什么",
    "讨论有哪些",
    "热门视频",
    "帮我找出",
    "最近三个月",
    "近三个月",
    "最近一个月",
    "近一个月",
    "最近一月",
    "最近一周",
    "近一周",
    "最近90天",
    "近90天",
    "最近30天",
    "近30天",
    "最近7天",
    "近7天",
    "小红书上",
    "抖音上",
    "大家对",
    "的评价",
    "是什么",
    "有哪些",
    "哪些",
    "帮我",
    "找出",
    "请问",
    "想知道",
    "怎么样",
    "怎么选",
    "如何",
    "什么",
    "一下",
    "关于",
    "大家",
    "评价",
    "讨论",
    "推荐",
    "值得",
    "最近",
    "某款",
    "产品",
    "笔记",
    "视频",
    "评论",
    "反馈",
    "话题",
    "某个",
    "小红书",
    "抖音",
)

STOPWORDS = frozenset(
    {
        "的",
        "了",
        "吗",
        "呢",
        "吧",
        "啊",
        "和",
        "与",
        "或",
        "在",
        "是",
        "有",
        "对",
        "把",
        "被",
        "及",
        "上",
        "里",
        "中",
        "这",
        "那",
        "一个",
        "我们",
        "他们",
        "这个",
        "那个",
        "可以",
        "不是",
        "以及",
        "相关",
        "内容",
        "用户",
        "热门",
        "还是",
        "还有",
        "比较",
        "真的",
        "就是",
        "来看",
        "看看",
        "想问",
        "如果",
        "因为",
        "所以",
    }
)

DAY_PATTERNS: tuple[tuple[int, tuple[str, ...]], ...] = (
    (90, ("最近三个月", "近三个月", "最近90天", "近90天")),
    (30, ("最近一个月", "近一个月", "最近一月", "最近30天", "近30天")),
    (7, ("最近一周", "近一周", "最近7天", "近7天")),
)

PHRASE_PATTERNS = (
    re.compile(r"RTX\s*\d{3,4}\s*(?:Ti|SUPER)?", re.IGNORECASE),
    re.compile(r"GTX\s*\d{3,4}\s*(?:Ti|SUPER)?", re.IGNORECASE),
    re.compile(r"\bPQC\b", re.IGNORECASE),
    re.compile(r"WH-?\d{3,4}XM\d", re.IGNORECASE),
)

_SORTED_FILLERS = tuple(sorted(FILLERS, key=len, reverse=True))


def understand_query(raw: str) -> UnderstoodQuery:
    text = (raw or "").strip()
    has_xhs = "小红书" in text
    has_dy = "抖音" in text
    platform_hint = None
    if has_xhs and not has_dy:
        platform_hint = "xhs"
    elif has_dy and not has_xhs:
        platform_hint = "dy"
    return UnderstoodQuery(
        raw=text,
        keywords=extract_keywords(text),
        platform_hint=platform_hint,
        day_hint=detect_days(text),
        intent=intent_label(text),
    )


def resolve_filters(
    ui_platforms: list[str],
    ui_time_range: str,
    understood: UnderstoodQuery,
) -> tuple[list[str], str, list[str]]:
    """界面选择优先。只有选了「全部」或不限时间时，才采用问句里的提示。"""

    warnings: list[str] = []
    if set(ui_platforms) == {"xhs", "dy"} and understood.platform_hint:
        platforms = [understood.platform_hint]
        name = "小红书" if understood.platform_hint == "xhs" else "抖音"
        warnings.append(f"问题里提到了{name}，本次只检索该平台。想两边一起看，把平台名从问题里去掉即可。")
    else:
        platforms = list(ui_platforms)

    if ui_time_range == "all" and understood.day_hint:
        time_range = str(understood.day_hint)
        warnings.append(f"已按问题中的说法，把时间限定为近 {understood.day_hint} 天。")
    else:
        time_range = ui_time_range
    return platforms, time_range, warnings


def detect_days(text: str) -> int | None:
    for days, phrases in DAY_PATTERNS:
        if any(phrase in text for phrase in phrases):
            return days
    return None


def extract_keywords(text: str) -> list[str]:
    keywords: list[str] = []
    for pattern in PHRASE_PATTERNS:
        for match in pattern.finditer(text):
            add_keyword(keywords, normalize_phrase(match.group(0)))
    for match in re.finditer(r"[A-Za-z][A-Za-z0-9+\-]{2,}", text):
        add_keyword(keywords, match.group(0).upper())

    consumed = text
    for filler in _SORTED_FILLERS:
        consumed = consumed.replace(filler, " ")
    consumed = re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]+", " ", consumed)
    for token in jieba.lcut(consumed):
        token = token.strip()
        if not token or token in STOPWORDS or token.lower() in STOPWORDS:
            continue
        if re.fullmatch(r"[A-Za-z0-9+\-]+", token):
            if len(token) < 3:
                continue
            add_keyword(keywords, token.upper() if token.isascii() else token)
            continue
        if token.isdigit():
            if len(token) >= 3:
                add_keyword(keywords, token)
            continue
        if len(token) >= 2:
            add_keyword(keywords, token)
    return keywords[:5]


def add_keyword(keywords: list[str], token: str) -> None:
    cleaned = token.strip()
    if not cleaned:
        return
    low = cleaned.lower()
    kept: list[str] = []
    for existing in keywords:
        existing_low = existing.lower()
        if existing_low == low:
            return
        if low in existing_low and len(cleaned) < len(existing):
            return
        if existing_low in low and len(existing) < len(cleaned):
            continue
        kept.append(existing)
    kept.append(cleaned)
    keywords[:] = kept


def normalize_phrase(phrase: str) -> str:
    compact = re.sub(r"\s+", " ", phrase).strip()
    gpu = re.fullmatch(r"(RTX|GTX)\s*(\d{3,4})\s*(Ti|SUPER)?", compact, re.IGNORECASE)
    if gpu:
        suffix = gpu.group(3)
        suffix_text = ""
        if suffix:
            suffix_text = "Ti" if suffix.lower() == "ti" else "SUPER"
            suffix_text = f" {suffix_text}"
        return f"{gpu.group(1).upper()} {gpu.group(2)}{suffix_text}"
    if re.fullmatch(r"PQC", compact, re.IGNORECASE):
        return "PQC"
    return compact.upper()


def intent_label(text: str) -> str:
    if any(word in text for word in ("评价", "反馈", "口碑", "避雷")):
        return "口碑与反馈"
    if "讨论" in text:
        return "讨论梳理"
    if "热门" in text:
        return "热门内容"
    if any(word in text for word in ("推荐", "值得", "哪家", "哪些")):
        return "推荐向检索"
    return "关键词检索"


def crawler_query(keywords: list[str]) -> str:
    """合成一条平台搜索词，避免把预算拆成多次互不相关的搜索。"""

    parts = []
    for keyword in keywords[:4]:
        cleaned = keyword.replace(",", " ").replace("，", " ").strip()
        if cleaned:
            parts.append(cleaned[:40])
    return " ".join(parts)
