"""拾红的检索界面。业务逻辑在 hongsearch 包里。"""

from __future__ import annotations

import time
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

from hongsearch.config import ENV_PATH, load_settings, reload_settings, resolve_runtime_mode
from hongsearch.db import Database
from hongsearch.jobs import discard_job, get_job, start_analysis_job, start_search_job
from hongsearch.models import ResultItem, SavedAnalysis, SearchOutcome
from hongsearch.util import format_count, platform_label, snippet

st.set_page_config(page_title="拾红", page_icon="📕", layout="wide")

PLATFORM_CHOICES = {"全部": ["xhs", "dy"], "小红书": ["xhs"], "抖音": ["dy"]}
TIME_CHOICES = {"不限": "all", "近 7 天": "7", "近 30 天": "30", "近 90 天": "90"}
MODE_CHOICES = {"自动": "auto", "演示": "demo", "实时": "live"}
MODE_LABELS = {"demo": "演示数据", "live": "实时采集", "cache": "历史缓存"}
EXAMPLES = [
    "广州有哪些值得推荐的日料店？",
    "最近 RTX 5070 Ti 有哪些用户反馈？",
    "小红书上大家对戴森吹风机的评价是什么？",
    "最近一个月关于 PQC 的讨论有哪些？",
    "帮我找出抖音上城市骑行的热门视频",
]

st.markdown(
    """
    <style>
    .block-container { max-width: 1080px; padding-top: 1.6rem; }
    [data-testid="stMain"] h1 {
        letter-spacing: -0.04em;
        font-weight: 720;
        margin-bottom: 0.2rem;
    }
    [data-testid="InputInstructions"] { display: none; }
    .warnbox {
        background: #fff7ed;
        border: 1px solid #fed7aa;
        color: #7c2d12;
        padding: 0.75rem 0.9rem;
        border-radius: 12px;
        font-size: 0.92rem;
        line-height: 1.55;
    }
    .badge {
        display: inline-block;
        padding: 0.08rem 0.48rem;
        border-radius: 999px;
        font-size: 0.78rem;
        font-weight: 650;
        margin-right: 0.35rem;
    }
    .badge-xhs { background: #ffe4e6; color: #be123c; }
    .badge-dy { background: #e7e5e4; color: #1c1917; }
    .badge-demo { background: #fef3c7; color: #92400e; }
    .meta { color: #78716c; font-size: 0.92rem; }
    @media (max-width: 900px) {
        [data-testid="stSidebar"] {
            position: fixed;
            z-index: 100;
            top: 0;
            left: 0;
            height: 100%;
            background: #fffdfb;
            box-shadow: 8px 0 24px rgba(28, 25, 23, 0.12);
        }
        [data-testid="stAppViewContainer"],
        [data-testid="stMain"] {
            margin-left: 0 !important;
        }
    }
    </style>
    """,
    unsafe_allow_html=True,
)


@st.cache_resource
def open_database(path_str: str) -> Database:
    database = Database(Path(path_str))
    database.init()
    return database


def use_example(text: str) -> None:
    st.session_state.query = text
    if st.session_state.get("search_job_id"):
        st.session_state.search_notice = "上一次检索还在进行。可以先取消，再开始新的。"
        return
    st.session_state.pending_search = True


def mark_search() -> None:
    if st.session_state.get("search_job_id"):
        st.session_state.search_notice = "上一次检索还在进行。可以先取消，再开始新的。"
        return
    st.session_state.pending_search = True


def mark_analysis() -> None:
    if st.session_state.get("search_job_id") or st.session_state.get("analysis_job_id"):
        return
    st.session_state.pending_analysis = True


def open_history(search_id: int) -> None:
    st.session_state.open_search_id = search_id


def choice_from_platforms(platforms: list[str]) -> str:
    if set(platforms) == {"xhs"}:
        return "小红书"
    if set(platforms) == {"dy"}:
        return "抖音"
    return "全部"


def choice_from_time(time_range: str) -> str:
    for label, value in TIME_CHOICES.items():
        if value == time_range:
            return label
    return "不限"


def init_state() -> None:
    st.session_state.setdefault("query", "")
    st.session_state.setdefault("platform_choice", "全部")
    st.session_state.setdefault("time_choice", "不限")
    st.session_state.setdefault("force_refresh", False)
    st.session_state.setdefault("mode_choice", "自动")
    st.session_state.setdefault("outcome", None)
    st.session_state.setdefault("analysis", None)
    st.session_state.setdefault("analysis_error", None)
    st.session_state.setdefault("search_job_id", None)
    st.session_state.setdefault("analysis_job_id", None)
    st.session_state.setdefault("search_notice", None)


def consume_history(database: Database) -> None:
    search_id = st.session_state.pop("open_search_id", None)
    if not search_id:
        return
    outcome = database.get_outcome(int(search_id))
    if outcome is None:
        st.session_state.outcome = None
        st.session_state.analysis_error = "这条历史已经不在本地库里。"
        return
    st.session_state.outcome = outcome
    st.session_state.analysis = database.latest_analysis(outcome.search_id) if outcome.search_id else None
    st.session_state.analysis_error = None
    st.session_state.query = outcome.query
    st.session_state.platform_choice = choice_from_platforms(outcome.platforms)
    st.session_state.time_choice = choice_from_time(outcome.time_range)


def format_elapsed(seconds: float) -> str:
    total = max(0, int(seconds))
    if total < 60:
        return f"已进行 {total} 秒"
    minutes, rest = divmod(total, 60)
    return f"已进行 {minutes} 分 {rest} 秒"


def mount_panel(func, *, active: bool, key: str) -> None:
    # 有任务时每秒重画这一小块。整页脚本不能停在采集过程上，否则按钮会全部失效。
    panel = st.fragment(run_every=1 if active else None, key=key)(func)
    panel()


def _start_search(database: Database, settings) -> None:
    if st.session_state.get("search_job_id"):
        st.session_state.search_notice = "上一次检索还在进行。可以先取消，再开始新的。"
        return
    mode, _reason = resolve_runtime_mode(settings, MODE_CHOICES[st.session_state.mode_choice])
    st.session_state.search_job_id = start_search_job(
        settings,
        database,
        mode,
        st.session_state.query,
        PLATFORM_CHOICES[st.session_state.platform_choice],
        TIME_CHOICES[st.session_state.time_choice],
        bool(st.session_state.force_refresh),
    )


def _start_analysis(database: Database, settings) -> None:
    if st.session_state.get("analysis_job_id") or st.session_state.get("search_job_id"):
        return
    outcome: SearchOutcome | None = st.session_state.get("outcome")
    if outcome is None:
        return
    st.session_state.analysis_error = None
    st.session_state.analysis_job_id = start_analysis_job(settings, database, outcome)


def _consume_finished_search() -> bool:
    job_id = st.session_state.get("search_job_id")
    job = get_job(job_id)
    if job is None:
        if job_id:
            st.session_state.search_job_id = None
        return False
    if not job.finished.is_set():
        return False
    with job.lock:
        if job.adopted:
            return False
        job.adopted = True
        outcome = job.outcome
    if outcome is None:
        outcome = SearchOutcome(
            search_id=None,
            query=st.session_state.get("query", ""),
            keywords=[],
            platforms=[],
            time_range="all",
            mode="",
            items=[],
            warnings=[],
            error="检索没有返回结果。",
        )
    st.session_state.search_job_id = None
    st.session_state.outcome = outcome
    st.session_state.analysis = None
    st.session_state.analysis_error = None
    discard_job(job_id)
    return True


def _consume_finished_analysis() -> bool:
    job_id = st.session_state.get("analysis_job_id")
    job = get_job(job_id)
    if job is None:
        if job_id:
            st.session_state.analysis_job_id = None
        return False
    if not job.finished.is_set():
        return False
    with job.lock:
        if job.adopted:
            return False
        job.adopted = True
        analysis = job.analysis
        error = job.analysis_error
        anchor_id = job.anchor_search_id
        anchor_query = job.anchor_query
    st.session_state.analysis_job_id = None
    discard_job(job_id)
    current: SearchOutcome | None = st.session_state.get("outcome")
    if current is None or current.search_id != anchor_id or current.query != anchor_query:
        return True
    st.session_state.analysis = analysis
    st.session_state.analysis_error = error
    return True


def search_activity_panel() -> None:
    if _consume_finished_search():
        st.rerun(scope="app")
    job = get_job(st.session_state.get("search_job_id"))
    if job is None or job.finished.is_set():
        return
    _render_search_progress(job)


def analysis_activity_panel() -> None:
    if _consume_finished_analysis():
        st.rerun(scope="app")
    job = get_job(st.session_state.get("analysis_job_id"))
    if job is None or job.finished.is_set():
        return
    _render_analysis_progress(job)


def _render_search_progress(job) -> None:
    steps = job.progress.snapshot()
    elapsed = format_elapsed(time.monotonic() - job.progress.started_at)
    current = next((step for step in reversed(steps) if step.state in {"waiting", "running", "error"}), None)
    if current is None and steps:
        current = steps[-1]
    if current is not None and current.state == "waiting":
        title = f"需要你处理：{current.label}"
    elif current is not None:
        title = current.label
    else:
        title = "检索进行中"
    with st.status(f"{title} · {elapsed}", expanded=True):
        st.caption("检索在后台进行，页面上的其他按钮仍然可以点。这次仍使用开始时的平台和时间，完成后会更新主栏里的结果。")
        for step in steps:
            prefix = {"done": "完成", "waiting": "等待你", "error": "失败", "running": "进行中"}.get(
                step.state, step.state
            )
            st.markdown(f"**{prefix}** · {step.label}")
            if not step.detail:
                continue
            if step.state == "waiting":
                st.warning(step.detail)
            elif step.state == "error":
                st.error(step.detail)
            else:
                st.caption(step.detail)
        if st.button("取消检索", key="cancel-search"):
            job.progress.request_cancel()


def _render_analysis_progress(job) -> None:
    steps = job.progress.snapshot()
    elapsed = format_elapsed(time.monotonic() - job.progress.started_at)
    current = next((step for step in reversed(steps) if step.state in {"running", "error"}), None)
    title = current.label if current is not None else "深度分析"
    with st.status(f"{title} · {elapsed}", expanded=True):
        st.caption("分析在后台进行，不会挡住检索进度和页面上的其他按钮。")
        for step in steps:
            prefix = {"done": "完成", "error": "失败", "running": "进行中"}.get(step.state, step.state)
            st.markdown(f"**{prefix}** · {step.label}")
            if step.detail:
                st.caption(step.detail)


def badge(platform: str) -> str:
    css = "badge-xhs" if platform == "xhs" else "badge-dy"
    return f'<span class="badge {css}">{platform_label(platform)}</span>'


def render_sidebar(database: Database, settings) -> None:
    with st.sidebar:
        st.header("检索方式")
        st.radio("模式", list(MODE_CHOICES), key="mode_choice")
        mode, reason = resolve_runtime_mode(settings, MODE_CHOICES[st.session_state.mode_choice])
        if mode == "demo":
            st.info(reason)
        else:
            st.success(reason)
        st.caption(f"大模型：{'已配置 ' + settings.llm_model if settings.llm_enabled else '未配置，分析会改成摘录'}")
        st.caption(f"登录方式：{settings.login_type}")
        st.caption("小红书 Cookie：" + ("已填写" if settings.xhs_cookies else "未填写"))
        st.caption("抖音 Cookie：" + ("已填写" if settings.dy_cookies else "未填写"))
        st.caption(f"MediaCrawler：{settings.mediacrawler_home or '未设置'}")
        if st.session_state.get("search_job_id"):
            st.caption("检索在后台进行。主栏能看到步骤，也可以取消。历史和筛选仍然可以点。")
        if st.button("重新加载 .env", width="stretch"):
            load_dotenv(ENV_PATH, override=True)
            reload_settings()
            st.cache_resource.clear()
            st.rerun()
        st.divider()
        st.header("检索记录")
        history = database.list_history(20)
        if not history:
            st.caption("还没有记录。完成一次检索后会写进本地 SQLite。")
        for row in history:
            label = f"{row['created_at'][5:]} · {MODE_LABELS.get(row['mode'], row['mode'])} · {row['result_count']} 条"
            st.button(
                label,
                key=f"history-{row['id']}",
                on_click=open_history,
                args=(int(row["id"]),),
                width="stretch",
            )
            st.caption(row["query"])


def render_result(index: int, result: ResultItem, demo: bool) -> None:
    item = result.item
    with st.container(border=True):
        st.markdown(
            f"**[{index}]** {badge(item.platform)} "
            f"<span class='meta'>{item.published_at or '未注明日期'}</span>",
            unsafe_allow_html=True,
        )
        st.markdown(f"### {item.title}")
        st.markdown(f"<span class='meta'>{item.author}</span>", unsafe_allow_html=True)
        st.markdown(
            "<span class='meta'>"
            f"点赞 {format_count(item.like_count)} · "
            f"收藏 {format_count(item.collect_count)} · "
            f"评论 {format_count(item.comment_count)} · "
            f"分享 {format_count(item.share_count)}"
            "</span>",
            unsafe_allow_html=True,
        )
        st.write(snippet(item.content, 140))
        if item.tags:
            st.caption("  ".join(f"#{tag}" for tag in item.tags))
        with st.expander(
            f"正文和评论（{len(item.comments)}）",
            key=f"detail-{item.platform}-{item.external_id}",
        ):
            st.write(item.content)
            if not item.comments:
                st.caption("这条结果没有附带评论。")
            for comment in item.comments:
                st.markdown(
                    f"**{comment.author}** · 赞 {format_count(comment.like_count)}  \n{comment.content}"
                )
        if item.url:
            link_name = "示例链接" if demo else "原文"
            st.markdown(f"[{link_name}]({item.url})")
        else:
            st.caption("没有原文链接。")


def render_outcome(outcome: SearchOutcome) -> None:
    if outcome.error:
        if outcome.error.startswith("检索已取消"):
            st.warning(outcome.error)
        else:
            st.error(outcome.error)
    for warning in outcome.warnings:
        st.warning(warning)
    if outcome.logs:
        with st.expander("采集日志"):
            st.code(outcome.logs)
    if outcome.error:
        return
    platforms = "、".join(platform_label(platform) for platform in outcome.platforms)
    time_label = choice_from_time(outcome.time_range)
    st.markdown(f"### 检索结果 · {len(outcome.items)} 条")
    st.caption(
        f"{MODE_LABELS.get(outcome.mode, outcome.mode)} · {outcome.intent} · "
        f"{platforms} · {time_label} · 关键词：{'、'.join(outcome.keywords) or '无'}"
    )
    if outcome.mode == "demo":
        st.caption("这些是编写的演示样本，名称、评测和互动数字都不是真实采集。示例链接不会打开真实笔记。")
    if outcome.mode == "cache":
        st.caption("结果来自本地历史。想重新采集，勾选「忽略缓存，重新检索」。")
    if not outcome.items:
        st.info("没有找到足够相关的公开内容。可以换更具体的说法，或把时间改成不限。")
        return
    for index, result in enumerate(outcome.items, start=1):
        render_result(index, result, demo=outcome.mode == "demo")


def render_analysis(outcome: SearchOutcome | None) -> None:
    st.markdown("### 深度分析")
    mount_panel(
        analysis_activity_panel,
        active=bool(st.session_state.get("analysis_job_id")),
        key="analysis-activity",
    )
    st.caption("只把当前列表里的标题、正文摘录和评论交给模型，并要求用 [编号] 回到这些条目。不会上传 Cookie。")
    busy = bool(st.session_state.get("search_job_id") or st.session_state.get("analysis_job_id"))
    disabled = busy or outcome is None or bool(outcome.error) or not outcome.items
    st.button("生成深度分析", type="primary", disabled=disabled, on_click=mark_analysis)
    if st.session_state.get("analysis_error"):
        st.error(st.session_state.analysis_error)
    analysis: SavedAnalysis | None = st.session_state.get("analysis")
    if analysis is None:
        return
    if analysis.kind == "llm":
        st.caption(f"模型 {analysis.model or '未命名'} · {analysis.created_at}")
    else:
        st.caption(f"摘录整理，未调用大模型 · {analysis.created_at}")
    st.markdown(analysis.report)
    st.download_button(
        "下载 Markdown",
        data=analysis.report,
        file_name="拾红分析.md",
        mime="text/markdown",
        width="stretch",
    )


def main() -> None:
    init_state()
    settings = load_settings()
    database = open_database(str(settings.database_path))
    _consume_finished_search()
    _consume_finished_analysis()
    consume_history(database)
    if st.session_state.pop("pending_search", False):
        _start_search(database, settings)
    if st.session_state.pop("pending_analysis", False):
        _start_analysis(database, settings)
    render_sidebar(database, settings)

    st.markdown("# 拾红")
    st.markdown("检索小红书和抖音上与问题相关的公开笔记、视频和评论。先看原始结果，再生成有出处的分析。")
    st.markdown(
        """
        <div class="warnbox">
        只处理你本人登录后可见的公开内容。请遵守平台条款，控制频率，不要采集私信、好友或通讯录。
        演示模式不访问平台。实时模式依赖你本机的 MediaCrawler，平台改版后可能失效。
        Cookie 和 API Key 只放在本机 .env，不要写进仓库。验证码请你自己在浏览器里完成。
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.write("")
    st.caption("示例问题")
    left, right = st.columns(2)
    for index, example in enumerate(EXAMPLES):
        column = left if index % 2 == 0 else right
        column.button(
            example,
            key=f"example-{index}",
            on_click=use_example,
            args=(example,),
            width="stretch",
        )

    st.text_input("问题或关键词", key="query", placeholder="例如：广州有哪些值得推荐的日料店？")
    st.radio("平台", list(PLATFORM_CHOICES), key="platform_choice", horizontal=True)
    st.radio("时间", list(TIME_CHOICES), key="time_choice", horizontal=True)
    searching = bool(st.session_state.get("search_job_id"))
    st.button(
        "检索进行中" if searching else "检索",
        type="primary",
        on_click=mark_search,
        disabled=searching,
        width="stretch",
    )
    st.checkbox("忽略缓存，重新检索", key="force_refresh")
    notice = st.session_state.pop("search_notice", None)
    if notice:
        st.info(notice)
    mount_panel(search_activity_panel, active=searching, key="search-activity")

    outcome: SearchOutcome | None = st.session_state.get("outcome")
    if outcome is None and not searching:
        st.info("输入一个具体问题后再检索。没有 Cookie 和大模型密钥时，也可以先用上面的例子看演示结果。")
    elif outcome is not None:
        render_outcome(outcome)

    render_analysis(outcome)


main()
