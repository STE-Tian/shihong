"""把检索和分析放进后台线程，让 Streamlit 脚本能马上返回。"""

from __future__ import annotations

import threading
import time
import uuid

from hongsearch.analysis import analyze
from hongsearch.collectors import build_collector
from hongsearch.config import Settings
from hongsearch.db import Database
from hongsearch.llm import LLMClient, LLMError
from hongsearch.models import SavedAnalysis, SearchOutcome
from hongsearch.progress import Progress
from hongsearch.service import SearchService
from hongsearch.util import scrub


class Job:
    def __init__(self, kind: str):
        self.id = uuid.uuid4().hex[:12]
        self.kind = kind
        self.progress = Progress()
        self.finished = threading.Event()
        self.lock = threading.Lock()
        self.adopted = False
        self.outcome: SearchOutcome | None = None
        self.analysis: SavedAnalysis | None = None
        self.analysis_error: str | None = None
        self.anchor_search_id: int | None = None
        self.anchor_query = ""
        self.created_at = time.monotonic()


_jobs: dict[str, Job] = {}
_registry_lock = threading.Lock()


def get_job(job_id: str | None) -> Job | None:
    if not job_id:
        return None
    with _registry_lock:
        return _jobs.get(job_id)


def discard_job(job_id: str | None) -> None:
    if not job_id:
        return
    with _registry_lock:
        _jobs.pop(job_id, None)


def start_search_job(
    settings: Settings,
    database: Database,
    mode: str,
    query: str,
    platforms: list[str],
    time_range: str,
    force_refresh: bool,
) -> str:
    job = Job("search")
    job.progress.step("understand", "理解问题", "running", "正在准备这次检索。")
    _register(job)
    thread = threading.Thread(
        target=run_search_job,
        args=(job, settings, database, mode, query, list(platforms), time_range, force_refresh),
        name=f"hongsearch-search-{job.id}",
        daemon=True,
    )
    thread.start()
    return job.id


def run_search_job(
    job: Job,
    settings: Settings,
    database: Database,
    mode: str,
    query: str,
    platforms: list[str],
    time_range: str,
    force_refresh: bool,
) -> None:
    try:
        service = SearchService(settings, database, build_collector(mode, settings))
        job.outcome = service.search(query, platforms, time_range, force_refresh, progress=job.progress)
    except Exception as exc:
        job.outcome = SearchOutcome(
            search_id=None,
            query=query,
            keywords=[],
            platforms=list(platforms),
            time_range=time_range,
            mode=mode,
            items=[],
            warnings=[],
            error="检索中断：" + _safe_error(exc, settings),
        )
        job.progress.step("collect", "采集公开内容", "error", job.outcome.error or "")
    finally:
        job.finished.set()


def start_analysis_job(settings: Settings, database: Database, outcome: SearchOutcome) -> str:
    job = Job("analysis")
    job.anchor_search_id = outcome.search_id
    job.anchor_query = outcome.query
    job.progress.step("prepare", "准备分析材料", "running", "只提交当前列表里的标题、摘录和评论。")
    _register(job)
    thread = threading.Thread(
        target=run_analysis_job,
        args=(job, settings, database, outcome),
        name=f"hongsearch-analysis-{job.id}",
        daemon=True,
    )
    thread.start()
    return job.id


def run_analysis_job(job: Job, settings: Settings, database: Database, outcome: SearchOutcome) -> None:
    try:
        saved, error = _analyze(job.progress, settings, database, outcome)
        job.analysis = saved
        job.analysis_error = error
    except Exception as exc:
        job.analysis_error = "分析中断：" + _safe_error(exc, settings)
        job.progress.step("llm", "深度分析", "error", job.analysis_error)
    finally:
        job.finished.set()


def _analyze(
    progress: Progress,
    settings: Settings,
    database: Database,
    outcome: SearchOutcome,
) -> tuple[SavedAnalysis | None, str | None]:
    if outcome.error or not outcome.items:
        progress.step("prepare", "准备分析材料", "error", "先检索出内容，再做深度分析。")
        return None, "先检索出内容，再做深度分析。"
    progress.step("prepare", "准备分析材料", "done", f"{len(outcome.items)} 条。不会上传 Cookie。")
    client = LLMClient(settings.llm_base_url, settings.llm_api_key, settings.llm_model)
    if client.enabled:
        progress.step(
            "llm",
            "调用大模型",
            "running",
            f"模型 {settings.llm_model}。接口超过 {int(client.timeout)} 秒会停止。",
        )
    else:
        progress.step("llm", "整理摘录", "running", "未配置大模型，改为摘录。")
    error: str | None = None
    try:
        result = analyze(outcome.query, outcome.items, client)
    except LLMError as exc:
        error = str(exc)
        result = analyze(outcome.query, outcome.items, None)
        result.report = f"> 大模型没有生成报告，下面改为摘录。原因：{exc}\n\n" + result.report
        progress.step("llm", "调用大模型", "error", error)
    else:
        label = "调用大模型" if result.kind == "llm" else "整理摘录"
        progress.step("llm", label, "done")
    saved = SavedAnalysis(kind=result.kind, model=result.model, report=result.report, created_at="")
    if outcome.search_id:
        progress.step("save", "保存分析", "running")
        saved = database.save_analysis(outcome.search_id, result.kind, result.model, result.report)
        progress.step("save", "保存分析", "done")
    return saved, error


def _register(job: Job) -> None:
    with _registry_lock:
        _prune_locked()
        _jobs[job.id] = job


def _prune_locked() -> None:
    now = time.monotonic()
    stale = [
        job_id
        for job_id, job in _jobs.items()
        if job.finished.is_set() and now - job.created_at > 120
    ]
    for job_id in stale:
        _jobs.pop(job_id, None)


def _safe_error(exc: Exception, settings: Settings) -> str:
    text = scrub(str(exc), [settings.xhs_cookies, settings.dy_cookies, settings.llm_api_key])
    return text[:300] or exc.__class__.__name__
