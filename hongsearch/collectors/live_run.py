"""跑一次 MediaCrawler 子进程，并把等待原因和已写入的条数报出来。

子进程自己计时。超时或中途失败时，调用方仍应去运行目录里解析已经写好的 JSONL。
"""

from __future__ import annotations

import queue
import re
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from hongsearch.collectors.base import CollectorError
from hongsearch.progress import Progress, terminate_process
from hongsearch.util import platform_label, scrub

CDP_HOST = "127.0.0.1"
DEFAULT_CDP_PORT = 9222
_EOF = object()

_PHASE_RANK = {
    "boot": 0,
    "boot_ok": 1,
    "cdp_closed": 2,
    "cdp_launch": 3,
    "cdp_open": 3,
    "cdp_accept": 4,
    "cdp_failed": 5,
    "cdp_ready": 6,
    "qr": 7,
    "captcha": 7,
    "login_ok": 8,
    "notes": 9,
    "comments": 10,
}


@dataclass(frozen=True)
class ProcessResult:
    returncode: int | None
    timed_out: bool
    cancelled: bool
    log: str
    phase: str


def probe_cdp(host: str = CDP_HOST, port: int = DEFAULT_CDP_PORT, timeout: float = 0.3) -> bool:
    """看本机调试端口是否在听。连接被拒绝会马上返回，不会多等。"""

    import socket

    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def read_cdp_connect_existing(home: str | None, default: bool = True) -> bool:
    """MediaCrawler 默认连接已经打开的 Chrome。读不到配置时按默认处理。"""

    if not home:
        return default
    path = Path(home) / "config" / "base_config.py"
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return default
    match = re.search(r"(?m)^\s*CDP_CONNECT_EXISTING\s*=\s*(True|False)\s*$", text)
    if not match:
        return default
    return match.group(1) == "True"


def read_cdp_port(home: str | None, default: int = DEFAULT_CDP_PORT) -> int:
    """只读 MediaCrawler 配置里的端口数字，不导入那个项目。"""

    if not home:
        return default
    path = Path(home) / "config" / "base_config.py"
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return default
    match = re.search(r"(?m)^\s*CDP_DEBUG_PORT\s*=\s*(\d+)\s*$", text)
    if not match:
        return default
    port = int(match.group(1))
    if not 1 <= port <= 65535:
        return default
    return port


def interpret_log_line(line: str) -> str | None:
    """把 MediaCrawler 的一行日志收成阶段名。不把原文带进界面。"""

    text = line.lower()
    if (
        "cannot connect to existing browser" in text
        or "cdp connection failed" in text
        or "cdp browser launch failed" in text
    ):
        return "cdp_failed"
    if "confirmation dialog" in text or "accept it" in text or "remote debugging dialog" in text:
        return "cdp_accept"
    if "is not accessible" in text or "still waiting for browser" in text:
        return "cdp_closed"
    if "successfully connected" in text:
        return "cdp_ready"
    if "is accessible" in text or "connecting to existing browser" in text:
        return "cdp_open"
    if (
        "waiting for scan" in text
        or "login_by_qrcode" in text
        or "by qrcode" in text
        or "扫码" in line
        or "二维码" in line
    ):
        return "qr"
    if "captcha" in text or "请通过验证" in line or "请手动验证" in line or "verify manually" in text:
        return "captcha"
    if "login successful" in text or "login status confirmed" in text:
        return "login_ok"
    if line.startswith("拾红："):
        return "boot_ok"
    if "begin search" in text or "search xiaohongshu" in text or "search douyin" in text:
        return "notes"
    if "comment" in text and any(word in text for word in ("begin", "get", "fetch", "crawl")):
        return "comments"
    return None


def count_saved_rows(run_dir: Path) -> tuple[int, int]:
    """数已经落盘的行。文件正在被写时，最后一行不完整也只影响这一次计数。"""

    notes = 0
    comments = 0
    if not run_dir.exists():
        return 0, 0
    for path in run_dir.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in {".jsonl", ".json"}:
            continue
        name = path.name.lower()
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if path.suffix.lower() == ".jsonl":
            count = sum(1 for line in text.splitlines() if line.strip())
        else:
            count = 1 if text.strip() else 0
        if "comment" in name:
            comments += count
        elif "content" in name:
            notes += count
    return notes, comments


def sync_progress(
    progress: Progress,
    platform: str,
    phase: str,
    port: int,
    notes: int,
    comments: int,
    login_type: str,
    *,
    port_open: bool,
) -> None:
    label = platform_label(platform)
    host = f"{CDP_HOST}:{port}"
    if phase in {"boot", "cdp_closed", "cdp_open"}:
        progress.step(f"{platform}-boot", f"启动{label}采集", "running", "正在启动 MediaCrawler。")
    else:
        progress.step(f"{platform}-boot", f"启动{label}采集", "done", "采集进程已启动。")

    if phase == "cdp_failed":
        progress.step(f"{platform}-cdp", "连接 Chrome 远程调试", "error", _cdp_failed_detail(host))
    elif phase == "cdp_closed":
        progress.step(f"{platform}-cdp", "连接 Chrome 远程调试", "waiting", _cdp_closed_detail(host))
    elif phase == "cdp_accept":
        progress.step(f"{platform}-cdp", "连接 Chrome 远程调试", "waiting", _cdp_accept_detail(host))
    elif phase == "cdp_launch":
        progress.step(f"{platform}-cdp", "连接 Chrome 远程调试", "running", _cdp_launch_detail())
    elif phase in {"cdp_open", "boot", "boot_ok"}:
        progress.step(f"{platform}-cdp", "连接 Chrome 远程调试", "running", _cdp_open_detail(host))
    else:
        progress.step(f"{platform}-cdp", "连接 Chrome 远程调试", "done", "已连上 Chrome。")

    if phase == "qr":
        progress.step(f"{platform}-login", f"登录{label}", "waiting", _qr_detail(label))
    elif phase == "captcha":
        progress.step(f"{platform}-login", f"登录{label}", "waiting", _captcha_detail())
    elif phase == "login_ok" or phase in {"notes", "comments"}:
        progress.step(f"{platform}-login", f"登录{label}", "done", "已进入采集。")
    elif phase == "cdp_ready" and login_type == "qrcode":
        progress.step(f"{platform}-login", f"登录{label}", "running", _qr_hint(label))
    elif phase == "cdp_ready" and login_type == "cookie":
        progress.step(
            f"{platform}-login",
            f"登录{label}",
            "running",
            "使用已填写的 Cookie。页面不会显示 Cookie。若登录态失效，需要你自己更新 .env。",
        )

    if notes or comments or phase in {"notes", "comments"}:
        note_state = "done" if comments else "running"
        note_detail = f"已写入 {notes} 条。" if notes else "正在获取公开笔记。"
        progress.step(f"{platform}-notes", f"获取{label}笔记", note_state, note_detail)
        if comments or phase == "comments":
            comment_detail = f"已写入 {comments} 条。" if comments else "正在获取评论。"
            progress.step(f"{platform}-comments", f"获取{label}评论", "running", comment_detail)


def decide_run_outcome(
    items: list,
    result: ProcessResult,
    platform: str,
    timeout_sec: float,
    secrets: list[str] | None = None,
) -> tuple[list, str, str | None]:
    """超时、取消和非零退出时，只要目录里已经有内容，就当作部分成功返回。"""

    label = platform_label(platform)
    log = scrub(result.log, list(secrets or []))
    if result.cancelled:
        if items:
            return items, log, f"检索已取消，保留{label}已经写好的 {len(items)} 条公开内容。"
        raise CollectorError("检索已取消。")
    if result.timed_out:
        if items:
            return (
                items,
                log,
                (
                    f"{label}采集在 {int(timeout_sec)} 秒时仍未结束，已停止这次任务，"
                    f"并保留已经写好的 {len(items)} 条公开内容。"
                    "页面会按相关度显示其中的一部分，评论可能还不完整。"
                    "若浏览器里还有这次打开的页面，可以手动关上。"
                ),
            )
        raise CollectorError(timeout_message(label, result.phase, _port_from_log(log)))
    if result.returncode not in (0, None) and not items:
        raise CollectorError(failure_message(label, result.returncode, log))
    warning = None
    if result.returncode not in (0, None) and items:
        warning = f"{label}进程退出码为 {result.returncode}，下面是已经写好的 {len(items)} 条，可能不完整。"
    elif not items:
        warning = f"{label}没有返回内容。可能是登录态失效、关键词过窄，或页面要求你本人完成验证。"
    return items, log, warning


def timeout_message(label: str, phase: str, port: int = DEFAULT_CDP_PORT) -> str:
    host = f"{CDP_HOST}:{port}"
    if phase == "cdp_closed":
        return (
            f"{label}在连接 Chrome 远程调试时超时，还没有写回内容。"
            f"请打开 Chrome 的 chrome://inspect/#remote-debugging，勾选 Allow remote debugging，"
            f"确认显示 Server running at: {host}，然后重试。"
        )
    if phase in {"cdp_open", "cdp_accept", "boot", "boot_ok"}:
        return (
            f"{label}一直停在 Chrome 调试确认，还没有写回内容。"
            f"请在弹出的窗口里点「接受」（大约 60 秒内）。"
            f"若没有弹窗，确认远程调试页显示 {host}。"
        )
    if phase == "qr":
        return (
            f"{label}一直在等扫码登录，还没有写回内容。"
            "请用对应 App 扫描浏览器里的二维码，并自己完成验证码。可以重新检索后再扫。"
        )
    if phase == "captcha":
        return (
            f"{label}停在验证码，还没有写回内容。请你自己在浏览器里完成验证后重新检索。"
            "程序不会识别或绕过验证码。"
        )
    if phase == "cdp_failed":
        return _cdp_failed_detail(host)
    return (
        f"{label}采集超时，已停止这次任务，而且运行目录里没有可读的内容。"
        "若浏览器还开着，请手动关闭这次打开的页面。"
    )


def failure_message(label: str, returncode: int | None, log: str) -> str:
    lowered = log.lower()
    if "cannot connect to existing browser" in lowered or "cdp connection failed" in lowered:
        return f"{label}没有连上 Chrome。{_cdp_failed_detail(f'{CDP_HOST}:{DEFAULT_CDP_PORT}')}"
    detail = ""
    tail = log.strip()
    if tail:
        detail = tail[-400:]
    code = returncode if returncode is not None else "?"
    return f"{label}采集失败（退出码 {code}）。{detail}".strip()


def run_process(
    *,
    command: list[str],
    cwd: str | None,
    env: dict[str, str],
    run_dir: Path,
    platform: str,
    login_type: str,
    timeout_sec: float,
    progress: Progress,
    secrets: list[str],
    cdp_host: str = CDP_HOST,
    cdp_port: int = DEFAULT_CDP_PORT,
    connect_existing: bool = True,
    probe: Callable[[], bool] | None = None,
) -> ProcessResult:
    """启动采集并读输出。函数返回时进程已经结束；运行目录里的文件留给调用方解析。"""

    check_port = probe or (lambda: probe_cdp(cdp_host, cdp_port))
    port_open = bool(check_port())
    if port_open:
        phase = "cdp_open"
    elif connect_existing:
        phase = "cdp_closed"
    else:
        phase = "cdp_launch"
    notes, comments = count_saved_rows(run_dir)
    sync_progress(
        progress, platform, phase, cdp_port, notes, comments, login_type, port_open=port_open
    )
    if progress.cancelled:
        return ProcessResult(None, False, True, "", phase)

    try:
        proc = subprocess.Popen(
            command,
            cwd=cwd or None,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            start_new_session=True,
        )
    except OSError as exc:
        raise CollectorError(f"{platform_label(platform)}无法启动采集进程：{exc.__class__.__name__}") from exc

    progress.bind_process(proc)
    lines: list[str] = []
    line_queue: queue.Queue[object] = queue.Queue()
    reader = threading.Thread(
        target=_pump,
        args=(proc.stdout, line_queue),
        name=f"hongsearch-{platform}-log",
        daemon=True,
    )
    reader.start()
    deadline = time.monotonic() + max(1.0, timeout_sec)
    timed_out = False
    cancelled = False
    last_probe = time.monotonic()
    try:
        while True:
            if progress.cancelled:
                cancelled = True
                terminate_process(proc)
                break
            item = _next_line(line_queue)
            if isinstance(item, str):
                cleaned = scrub(item, secrets).rstrip("\n")
                if cleaned:
                    _remember(lines, cleaned)
                    new_phase = interpret_log_line(cleaned)
                    if new_phase and _should_apply(phase, new_phase):
                        phase = new_phase
            notes, comments = count_saved_rows(run_dir)
            if notes or comments:
                phase = "comments" if comments else "notes"
            elif phase in {"cdp_closed", "cdp_launch", "boot", "boot_ok"} and time.monotonic() - last_probe >= 2:
                last_probe = time.monotonic()
                port_open = bool(check_port())
                if port_open and phase in {"cdp_closed", "cdp_launch"}:
                    phase = "cdp_open"
            sync_progress(
                progress,
                platform,
                phase,
                cdp_port,
                notes,
                comments,
                login_type,
                port_open=port_open,
            )
            if proc.poll() is not None and (item is _EOF or (item is None and line_queue.empty())):
                break
            if time.monotonic() >= deadline:
                timed_out = True
                terminate_process(proc)
                break
    finally:
        progress.clear_process(proc)
        if proc.poll() is None:
            terminate_process(proc)
        reader.join(timeout=1)

    for cleaned in _drain(line_queue, lines, secrets):
        new_phase = interpret_log_line(cleaned)
        if new_phase and _should_apply(phase, new_phase):
            phase = new_phase
    notes, comments = count_saved_rows(run_dir)
    if notes or comments:
        phase = "comments" if comments else "notes"
    sync_progress(
        progress, platform, phase, cdp_port, notes, comments, login_type, port_open=port_open
    )
    if progress.cancelled:
        cancelled = True
    return ProcessResult(
        returncode=proc.poll(),
        timed_out=timed_out and not cancelled,
        cancelled=cancelled,
        log=_tail("\n".join(lines)),
        phase=phase,
    )


def _should_apply(current: str, new: str) -> bool:
    if new == "cdp_failed" or new == "captcha":
        return current not in {"notes", "comments"}
    if new == "qr" and current in {"notes", "comments"}:
        return False
    if current == "cdp_accept" and new == "cdp_open":
        return False
    if current == "cdp_open" and new == "cdp_accept":
        return True
    return _PHASE_RANK.get(new, 0) >= _PHASE_RANK.get(current, 0)


def _pump(stream, line_queue: queue.Queue[object]) -> None:
    try:
        if stream is None:
            return
        for line in iter(stream.readline, ""):
            line_queue.put(line)
    except Exception:
        return
    finally:
        line_queue.put(_EOF)


def _next_line(line_queue: queue.Queue[object]) -> object:
    try:
        return line_queue.get(timeout=0.4)
    except queue.Empty:
        return None


def _drain(line_queue: queue.Queue[object], lines: list[str], secrets: list[str]) -> list[str]:
    drained: list[str] = []
    while True:
        try:
            item = line_queue.get_nowait()
        except queue.Empty:
            return drained
        if item is _EOF or not isinstance(item, str):
            continue
        cleaned = scrub(item, secrets).rstrip("\n")
        if cleaned:
            _remember(lines, cleaned)
            drained.append(cleaned)


def _remember(lines: list[str], line: str, limit: int = 200) -> None:
    lines.append(line)
    if len(lines) > limit:
        del lines[:-limit]


def _tail(text: str) -> str:
    return "\n".join(text.strip().splitlines()[-30:])[-2000:]


def _port_from_log(log: str) -> int:
    match = re.search(r"127\.0\.0\.1:(\d+)", log)
    if not match:
        return DEFAULT_CDP_PORT
    return int(match.group(1))


def _cdp_closed_detail(host: str) -> str:
    return (
        f"还没有连上 {host}。请打开 Chrome，在地址栏打开 chrome://inspect/#remote-debugging，"
        "勾选 Allow remote debugging for this browser instance，"
        f"直到页面显示 Server running at: {host}。MediaCrawler 大约再等 60 秒。"
    )


def _cdp_open_detail(host: str) -> str:
    return (
        f"已检测到 {host}。如果 Chrome 弹出允许调试的确认框，请在 60 秒内点「接受」。"
        "没有弹窗就可以忽略这一句。"
    )


def _cdp_launch_detail() -> str:
    return "正在启动浏览器。如果 Chrome 弹出允许调试的确认框，请在 60 秒内点「接受」。"


def _cdp_accept_detail(host: str) -> str:
    return (
        f"Chrome 正在等你确认调试连接（{host}）。请切到浏览器，在 60 秒内点「接受」。"
        "超时后这次采集会停下来。"
    )


def _cdp_failed_detail(host: str) -> str:
    return (
        f"没有连上 Chrome 远程调试（{host}）。"
        "请打开 chrome://inspect/#remote-debugging，勾选 Allow remote debugging，"
        f"确认显示 Server running at: {host}。若弹出确认框，请在 60 秒内点接受。"
    )


def _qr_detail(label: str) -> str:
    return (
        f"请用{label} App 扫描浏览器里的二维码。"
        "若出现滑块或验证码，请你自己在浏览器里完成。程序不会代为识别或跳过。"
    )


def _qr_hint(label: str) -> str:
    return (
        f"如果浏览器里出现{label}二维码，请用对应 App 扫描。"
        "验证码也请你自己完成。已经登录过的话，这一步会自己过去。"
    )


def _captcha_detail() -> str:
    return "页面要求验证。请你自己在浏览器里完成。程序不会识别或绕过验证码。"
