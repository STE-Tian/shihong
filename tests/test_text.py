from hongsearch.text import intent_label, resolve_filters, understand_query
from hongsearch.util import format_count, parse_count


def test_guangzhou_keywords():
    understood = understand_query("广州有哪些值得推荐的日料店？")
    assert "广州" in understood.keywords
    assert "日料店" in understood.keywords
    assert understood.platform_hint is None
    assert understood.day_hint is None
    assert understood.intent == "推荐向检索"


def test_gpu_keywords_keep_model_name():
    understood = understand_query("最近 RTX 5070 Ti 有哪些用户反馈？")
    assert understood.keywords == ["RTX 5070 Ti"]
    assert understood.intent == "口碑与反馈"
    assert understood.day_hint is None


def test_pqc_keywords_and_month():
    understood = understand_query("最近一个月关于 PQC 的讨论有哪些？")
    assert understood.keywords == ["PQC"]
    assert understood.day_hint == 30
    assert understood.intent == "讨论梳理"


def test_platform_hint_and_empty_topic():
    understood = understand_query("帮我找出抖音上某个话题的热门视频")
    assert understood.platform_hint == "dy"
    assert understood.keywords == []


def test_ui_platform_wins_over_hint():
    understood = understand_query("小红书上大家对戴森吹风机的评价是什么？")
    platforms, time_range, warnings = resolve_filters(["dy"], "all", understood)
    assert platforms == ["dy"]
    assert warnings == []
    assert "戴森" in understood.keywords
    assert "吹风机" in understood.keywords


def test_hint_used_when_ui_is_both():
    understood = understand_query("帮我找出抖音上城市骑行的热门视频")
    platforms, time_range, warnings = resolve_filters(["xhs", "dy"], "7", understood)
    assert platforms == ["dy"]
    assert time_range == "7"
    assert understood.day_hint is None
    assert any("抖音" in warning for warning in warnings)


def test_parse_and_format_counts():
    assert parse_count("1.2万") == 12000
    assert parse_count("3亿") == 300_000_000
    assert parse_count("") == 0
    assert format_count(12000) == "1.2万"
    assert format_count(800) == "800"
    assert intent_label("随便看看") == "关键词检索"
