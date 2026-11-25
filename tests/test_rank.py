from hongsearch.models import Item
from hongsearch.rank import rank_items


def _item(title: str, content: str, platform: str = "xhs", external_id: str = "1") -> Item:
    return Item(
        platform=platform,
        external_id=external_id,
        title=title,
        content=content,
        author="作者",
        published_at="2026-11-01",
        like_count=10,
        collect_count=0,
        comment_count=0,
        share_count=0,
        url="https://example.com",
    )


def test_requires_all_keywords_before_relaxing():
    items = [
        _item("广州骑行", "只提到广州", external_id="a"),
        _item("广州日料店", "这是一家日料店", external_id="b"),
    ]
    ranked, relaxed = rank_items(items, ["广州", "日料店"], limit=10)
    assert relaxed is False
    assert [item.external_id for item, _score in ranked] == ["b"]


def test_relaxes_when_nothing_matches_all_keywords():
    items = [_item("只有广州", "广州", external_id="a")]
    ranked, relaxed = rank_items(items, ["广州", "日料店"], limit=10)
    assert relaxed is True
    assert len(ranked) == 1


def test_model_name_ignores_inner_spaces():
    items = [_item("显卡", "我的 RTX5070Ti 到了", external_id="g")]
    ranked, relaxed = rank_items(items, ["RTX 5070 Ti"], limit=5)
    assert relaxed is False
    assert ranked[0][0].external_id == "g"
