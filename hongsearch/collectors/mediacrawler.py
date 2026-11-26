"""可选的实时采集：调用本机已安装的 MediaCrawler，只做小规模关键词搜索。

这里不实现签名、验证码、设备指纹或代理池。登录确认由使用者在自己的浏览器里完成。
"""

from __future__ import annotations

import json
import os
import shlex
import sys
import time
from datetime import datetime
from pathlib import Path

from hongsearch.collectors.base import Collection, CollectorError
from hongsearch.collectors.live_run import (
    decide_run_outcome,
    read_cdp_connect_existing,
    read_cdp_port,
    run_process,
)
from hongsearch.config import ROOT, Settings
from hongsearch.models import Comment, Item
from hongsearch.progress import Progress
from hongsearch.text import crawler_query
from hongsearch.util import parse_count, parse_timestamp, platform_label, scrub

RUNNER_PATH = Path(__file__).with_name("runner.py")


def parse_output_dir(root: Path, platform: str) -> list[Item]:
    contents: list[dict] = []
    comments: list[dict] = []
    if not root.exists():
        return []
    for path in sorted(root.rglob("*")):
        if path.suffix.lower() not in {".jsonl", ".json"}:
            continue
        name = path.name.lower()
        rows = _load_rows(path)
        if "comment" in name:
            comments.extend(rows)
        elif "content" in name:
            contents.extend(rows)
    return _assemble(platform, contents, comments)


class MediaCrawlerCollector:
    mode = "live"

    def __init__(self, settings: Settings, runner=None, sleeper=time.sleep):
        self.settings = settings
        self._runner = runner or self._default_run
        self._sleeper = sleeper
        self._progress = Progress()

    def collect(
        self,
        keywords: list[str],
        platforms: list[str],
        progress: Progress | None = None,
    ) -> Collection:
        query = crawler_query(keywords)
        previous = self._progress
        self._progress = progress or Progress()
        try:
            return self._collect(query, platforms)
        finally:
            self._progress = previous

    def _collect(self, query: str, platforms: list[str]) -> Collection:
        warnings: list[str] = []
        items: list[Item] = []
        logs: list[str] = []
        failures = 0
        attempts = 0
        started = False
        for platform in platforms:
            if self._progress.cancelled:
                warnings.append("检索已取消，不再打开下一个平台。")
                break
            if self.settings.login_type == "cookie" and not self.settings.cookie_for(platform):
                warnings.append(f"未配置{platform_label(platform)} Cookie，已跳过该平台。")
                continue
            if started:
                seconds = self.settings.inter_platform_sleep_sec
                self._progress.step(
                    "pause",
                    "平台之间停一下",
                    "running",
                    f"停 {seconds:.0f} 秒，避免连续打开下一个平台。",
                )
                try:
                    self._pause(seconds)
                except CollectorError as exc:
                    warnings.append(str(exc))
                    break
                self._progress.step("pause", "平台之间停一下", "done", f"已停 {seconds:.0f} 秒。")
            started = True
            attempts += 1
            label = platform_label(platform)
            self._progress.step(f"{platform}-boot", f"启动{label}采集", "running", "正在启动 MediaCrawler。")
            try:
                found, log, warning = self._runner(platform, query)
            except CollectorError as exc:
                failures += 1
                warnings.append(str(exc))
                if self._progress.cancelled:
                    break
                continue
            items.extend(found)
            if log:
                logs.append(f"## {label}\n{log}")
            if warning:
                warnings.append(warning)
            if self._progress.cancelled:
                break
        failed = attempts == 0 or (failures == attempts and not items)
        if attempts == 0 and not any("跳过" in item or "已取消" in item for item in warnings):
            warnings.append("没有可检索的平台。")
        return Collection(
            items=items,
            warnings=warnings,
            logs="\n\n".join(logs)[-4000:],
            failed=failed,
        )

    def _pause(self, seconds: float) -> None:
        if self._sleeper is not time.sleep:
            self._sleeper(seconds)
            if self._progress.cancelled:
                raise CollectorError("检索已取消。")
            return
        deadline = time.monotonic() + seconds
        while True:
            if self._progress.cancelled:
                raise CollectorError("检索已取消。")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            time.sleep(min(0.2, remaining))

    def build_command(self) -> list[str]:
        argv = self._python_argv()
        script = str(RUNNER_PATH)
        if argv and "python" in Path(argv[-1]).name.lower():
            return [*argv, "-u", script]
        return [*argv, script]

    def build_env(self, platform: str, query: str, save_path: Path) -> dict[str, str]:
        env = os.environ.copy()
        env.update(
            {
                "PYTHONIOENCODING": "utf-8",
                "PYTHONUTF8": "1",
                "PYTHONUNBUFFERED": "1",
                "MEDIACRAWLER_HOME": self.settings.mediacrawler_home,
                "PLATFORM": platform,
                "KEYWORDS": query,
                "LOGIN_TYPE": self.settings.login_type,
                "PLATFORM_COOKIES": self.settings.cookie_for(platform),
                "SAVE_DATA_PATH": str(save_path),
                "MAX_NOTES": str(self.settings.max_notes),
                "MAX_COMMENTS": str(self.settings.max_comments),
                "SLEEP_SEC": str(int(self.settings.inter_platform_sleep_sec)),
                "HEADLESS": "true" if self.settings.mediacrawler_headless else "false",
            }
        )
        return env

    def _python_argv(self) -> list[str]:
        raw = self.settings.mediacrawler_python.strip()
        if raw:
            return shlex.split(raw)
        home = Path(self.settings.mediacrawler_home) if self.settings.mediacrawler_home else None
        if home is not None:
            candidate = home / ".venv" / "bin" / "python"
            if candidate.is_file():
                return [str(candidate)]
        return [sys.executable]

    def _secrets(self) -> list[str]:
        return [self.settings.xhs_cookies, self.settings.dy_cookies, self.settings.llm_api_key]

    def _default_run(self, platform: str, query: str) -> tuple[list[Item], str, str | None]:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        run_dir = ROOT / "data" / "mediacrawler_runs" / f"{stamp}-{platform}"
        run_dir.mkdir(parents=True, exist_ok=True)
        command = self.build_command()
        secret = self.settings.cookie_for(platform)
        if secret and any(secret in part for part in command):
            raise CollectorError("已拒绝启动：Cookie 出现在了命令行参数里。")
        env = self.build_env(platform, query, run_dir)
        label = platform_label(platform)
        try:
            result = run_process(
                command=command,
                cwd=self.settings.mediacrawler_home or None,
                env=env,
                run_dir=run_dir,
                platform=platform,
                login_type=self.settings.login_type,
                timeout_sec=self.settings.crawler_timeout_sec,
                progress=self._progress,
                secrets=self._secrets(),
                cdp_port=read_cdp_port(self.settings.mediacrawler_home),
                connect_existing=read_cdp_connect_existing(self.settings.mediacrawler_home),
            )
        except CollectorError:
            raise
        except Exception as exc:
            items = parse_output_dir(run_dir, platform)
            detail = scrub(str(exc), self._secrets())[:200]
            if items:
                self._mark_saved(platform, len(items))
                return items, "", f"{label}采集中断（{detail}），保留已经写好的 {len(items)} 条公开内容。"
            raise CollectorError(f"{label}采集失败：{detail}") from exc
        items = parse_output_dir(run_dir, platform)
        try:
            items, log, warning = decide_run_outcome(
                items,
                result,
                platform,
                self.settings.crawler_timeout_sec,
                self._secrets(),
            )
        except CollectorError as exc:
            self._progress.step(f"{platform}-result", f"{label}采集", "error", str(exc))
            raise
        self._mark_saved(platform, len(items))
        return items, log, warning

    def _mark_saved(self, platform: str, count: int) -> None:
        label = platform_label(platform)
        if count:
            self._progress.step(f"{platform}-notes", f"获取{label}笔记", "done", f"读到 {count} 条。")
        if self._progress.has(f"{platform}-comments"):
            self._progress.step(f"{platform}-comments", f"获取{label}评论", "done", "这一轮写入的评论已读回。")
        if self._progress.has(f"{platform}-cdp"):
            current = next(
                (step for step in self._progress.snapshot() if step.key == f"{platform}-cdp"),
                None,
            )
            if current is not None and current.state in {"running", "waiting"}:
                self._progress.step(f"{platform}-cdp", "连接 Chrome 远程调试", "done", "这一轮已结束。")


def _load_rows(path: Path) -> list[dict]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    rows: list[dict] = []
    if path.suffix.lower() == ".jsonl":
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                rows.append(row)
        return rows
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return []
    if isinstance(data, dict):
        return [data]
    if isinstance(data, list):
        return [row for row in data if isinstance(row, dict)]
    return []


def _assemble(platform: str, contents: list[dict], comments: list[dict]) -> list[Item]:
    id_field = "note_id" if platform == "xhs" else "aweme_id"
    grouped: dict[str, list[dict]] = {}
    for row in comments:
        key = str(row.get(id_field) or "").strip()
        if key:
            grouped.setdefault(key, []).append(row)
    items: list[Item] = []
    for row in contents:
        external_id = str(row.get(id_field) or "").strip()
        if not external_id:
            continue
        parsed_comments: list[Comment] = []
        for comment in grouped.get(external_id, [])[:10]:
            text = str(comment.get("content") or "").strip()
            if not text:
                continue
            parsed_comments.append(
                Comment(
                    author=_author(comment),
                    content=text[:300],
                    like_count=parse_count(comment.get("like_count")),
                    published_at=parse_timestamp(comment.get("create_time")),
                )
            )
        if platform == "xhs":
            url = str(row.get("note_url") or "").strip() or f"https://www.xiaohongshu.com/explore/{external_id}"
            published = parse_timestamp(row.get("time"))
        else:
            url = str(row.get("aweme_url") or "").strip() or f"https://www.douyin.com/video/{external_id}"
            published = parse_timestamp(row.get("create_time"))
        description = str(row.get("desc") or row.get("content") or "").strip()
        items.append(
            Item(
                platform=platform,
                external_id=external_id,
                title=_title(row, description),
                content=description[:2000],
                author=_author(row),
                published_at=published,
                like_count=parse_count(row.get("liked_count")),
                collect_count=parse_count(row.get("collected_count")),
                comment_count=parse_count(row.get("comment_count")),
                share_count=parse_count(row.get("share_count")),
                url=url,
                tags=_tags(row.get("tag_list")),
                comments=parsed_comments,
                source_keyword=str(row.get("source_keyword") or "").strip(),
            )
        )
    return items


def _author(row: dict) -> str:
    name = str(row.get("nickname") or "").strip()
    return name or "未署名"


def _title(row: dict, description: str) -> str:
    title = str(row.get("title") or "").strip()
    if title:
        return title[:180]
    return (description[:40] or "未命名内容")


def _tags(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        parts = [str(item).strip() for item in value]
    else:
        parts = str(value).replace("，", ",").split(",")
    return [part for part in (item.strip() for item in parts) if part]
