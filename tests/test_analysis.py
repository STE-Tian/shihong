from hongsearch.analysis import analyze, build_messages
from hongsearch.llm import LLMClient, chat_completions_url
from hongsearch.models import Comment, Item, ResultItem


def _result() -> ResultItem:
    item = Item(
        platform="xhs",
        external_id="n1",
        title="五日寿司会再去吗",
        content="广州这家日料店需要预约。",
        author="饭后散步",
        published_at="2026-11-20",
        like_count=12000,
        collect_count=10,
        comment_count=2,
        share_count=1,
        url="https://www.xiaohongshu.com/explore/demo-n1",
        comments=[Comment(author="路过", content="要排队", like_count=3)],
    )
    return ResultItem(item=item, relevance=5)


def test_extractive_report_cites_sources():
    client = LLMClient("https://example.invalid/v1", "", "fake")
    result = analyze("广州日料店怎么样？", [_result()], client)
    assert result.kind == "extractive"
    assert "[1]" in result.report
    assert "五日寿司会再去吗" in result.report
    assert "https://www.xiaohongshu.com/explore/demo-n1" in result.report
    assert "未配置大模型" in result.report


def test_llm_report_appends_sources_and_does_not_invent_transport():
    class Fake:
        enabled = True
        model = "fake-model"

        def chat(self, messages, temperature=0.2):
            assert temperature == 0.2
            assert "五日寿司会再去吗" in messages[1]["content"]
            assert "Cookie" not in messages[1]["content"]
            return "## 结论\n样本很少，需要预约 [1]"

    result = analyze("广州日料店怎么样？", [_result()], Fake())
    assert result.kind == "llm"
    assert result.model == "fake-model"
    assert "需要预约 [1]" in result.report
    assert "## 参考来源" in result.report


def test_messages_number_results():
    messages = build_messages("问题", [_result()])
    assert messages[0]["role"] == "system"
    assert "[1]" in messages[1]["content"]


def test_chat_completions_url():
    assert chat_completions_url("https://api.deepseek.com/v1") == "https://api.deepseek.com/v1/chat/completions"
    assert chat_completions_url("https://api.deepseek.com") == "https://api.deepseek.com/v1/chat/completions"
    assert (
        chat_completions_url("https://example.com/v1/chat/completions")
        == "https://example.com/v1/chat/completions"
    )
