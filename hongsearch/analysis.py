"""把检索结果整理成带来源编号的中文报告。"""

from __future__ import annotations

from dataclasses import dataclass

from hongsearch.llm import LLMClient, LLMError
from hongsearch.models import ResultItem
from hongsearch.util import format_count, platform_label, snippet

SYSTEM_PROMPT = """你是公开社媒内容的研究助理。只能根据用户给出的检索材料作答，不能补充材料里没有的店名、价格、测试数据或体验。材料来自小红书笔记和抖音视频的标题、正文摘录和公开评论，不代表整体舆情。用简体中文，语气克制、具体。每个判断后面用方括号编号标出来源，例如 [1]。材料互相矛盾时要并列写出来，不要收成一个虚假共识。材料不够就直接说不够。不要编造引用编号。"""


@dataclass
class AnalysisResult:
    kind: str
    model: str
    report: str


def analyze(query: str, results: list[ResultItem], llm: LLMClient | None) -> AnalysisResult:
    if llm is not None and llm.enabled:
        try:
            body = llm.chat(build_messages(query, results), temperature=0.2)
        except LLMError:
            raise
        report = body.rstrip() + "\n\n" + source_appendix(results)
        return AnalysisResult(kind="llm", model=llm.model, report=report)
    report = extractive_report(query, results) + "\n\n" + source_appendix(results)
    return AnalysisResult(kind="extractive", model="", report=report)


def build_messages(query: str, results: list[ResultItem]) -> list[dict[str, str]]:
    blocks = []
    for index, result in enumerate(results, start=1):
        item = result.item
        comments = []
        for comment in item.comments[:5]:
            comments.append(
                f"- {comment.author}：{snippet(comment.content, 120)}（赞 {format_count(comment.like_count)}）"
            )
        comment_text = "\n".join(comments) if comments else "- （这条材料没有附带评论）"
        blocks.append(
            "\n".join(
                [
                    f"[{index}] 平台：{platform_label(item.platform)}",
                    f"标题：{item.title}",
                    f"作者：{item.author}",
                    f"日期：{item.published_at or '未注明'}",
                    (
                        "互动："
                        f"点赞 {format_count(item.like_count)}，"
                        f"收藏 {format_count(item.collect_count)}，"
                        f"评论 {format_count(item.comment_count)}，"
                        f"分享 {format_count(item.share_count)}"
                    ),
                    f"正文：{snippet(item.content, 400)}",
                    "评论：",
                    comment_text,
                ]
            )
        )
    material = "\n\n".join(blocks) if blocks else "（没有检索材料）"
    user = f"""问题：{query}

材料：
{material}

请用 Markdown 写出下面几节，不要另写“参考来源”：
## 结论
## 观点对照
## 评论里的具体说法
## 证据边界
## 可继续核实的问题
"""
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]


def extractive_report(query: str, results: list[ResultItem]) -> str:
    lines = [
        "## 说明",
        "未配置大模型 API Key，下面是按当前检索结果整理的摘录，不是模型撰写的研究结论。配置 `LLM_API_KEY` 后可以生成对照式分析。",
        "",
        "## 检索范围",
        f"- 问题：{query or '（空）'}",
        f"- 条目：{len(results)}",
        "",
        "## 观点摘录",
    ]
    if not results:
        lines.append("这次没有可摘录的内容。")
        return "\n".join(lines)
    positive_words = ("推荐", "值得", "新鲜", "稳定", "好用", "满意", "省心")
    negative_words = ("不值", "劝退", "排队", "溢价", "吵", "避雷", "一般", "闪退")
    positive_ids: list[int] = []
    negative_ids: list[int] = []
    for index, result in enumerate(results, start=1):
        item = result.item
        text = f"{item.title} {item.content} " + " ".join(comment.content for comment in item.comments)
        if any(word in text for word in positive_words):
            positive_ids.append(index)
        if any(word in text for word in negative_words):
            negative_ids.append(index)
        lines.extend(
            [
                "",
                f"### [{index}] {item.title}",
                (
                    f"- {platform_label(item.platform)} · {item.author} · "
                    f"{item.published_at or '未注明日期'}"
                ),
                f"- 正文：{snippet(item.content, 180)}",
            ]
        )
        if item.comments:
            lines.append("- 评论原话：")
            for comment in item.comments[:3]:
                lines.append(f"  - {comment.author}：{snippet(comment.content, 80)}")
    lines.extend(["", "## 粗略对照", "只检查摘录里有没有出现一组固定的词，不能当成情感分析结论。"])
    if positive_ids and negative_ids:
        pos = "、".join(f"[{i}]" for i in positive_ids)
        neg = "、".join(f"[{i}]" for i in negative_ids)
        lines.append(f"- 推荐或肯定的说法出现在 {pos}。")
        lines.append(f"- 保留、抱怨或劝退的说法出现在 {neg}。")
        lines.append("- 这些编号可以同时出现在两边，请回到原文看具体在说什么。")
    elif positive_ids:
        lines.append("- 这批材料里更多是推荐或肯定，几乎没看到明确的保留意见。样本很小，不能据此推断整体口碑。")
    elif negative_ids:
        lines.append("- 这批材料里更多是保留或负面说法。样本很小，不能据此推断整体口碑。")
    else:
        lines.append("- 这组固定词没有同时命中，请直接读上面的摘录。")
    return "\n".join(lines)


def source_appendix(results: list[ResultItem]) -> str:
    lines = ["## 参考来源"]
    if not results:
        lines.append("没有可引用的条目。")
        return "\n".join(lines)
    for index, result in enumerate(results, start=1):
        item = result.item
        url = item.url or "（没有原文链接）"
        lines.append(f"- [{index}] {platform_label(item.platform)} · {item.title} · {url}")
    return "\n".join(lines)
