"""检索进度。后台线程写入，界面线程只读快照。"""

from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from dataclasses import dataclass


@dataclass(frozen=True)
class ProgressView:
    key: str
    label: str
    state: str
    detail: str
    started_at: float


class Progress:
    """按 key 更新步骤。相同内容不重复记录，等待中的步骤保留开始时间，方便界面计算已等待多久。"""

    def __init__(self) -> None:
        self.started_at = time.monotonic()
        self._lock = threading.Lock()
        self._order: list[str] = []
        self._steps: dict[str, ProgressView] = {}
        self._events: list[ProgressView] = []
        self._cancel = threading.Event()
        self._proc: subprocess.Popen[str] | None = None
        self._proc_lock = threading.Lock()

    def step(self, key: str, label: str, state: str = "running", detail: str = "") -> None:
        now = time.monotonic()
        with self._lock:
            previous = self._steps.get(key)
            if (
                previous is not None
                and previous.state == state
                and previous.label == label
                and previous.detail == detail
            ):
                return
            started = now
            if previous is not None and previous.state in {"running", "waiting"} and state in {
                "running",
                "waiting",
                "done",
                "error",
            }:
                started = previous.started_at
            view = ProgressView(key, label, state, detail, started)
            if key not in self._steps:
                self._order.append(key)
            self._steps[key] = view
            self._events.append(view)

    def snapshot(self) -> tuple[ProgressView, ...]:
        with self._lock:
            return tuple(self._steps[key] for key in self._order)

    def events(self) -> tuple[ProgressView, ...]:
        with self._lock:
            return tuple(self._events)

    def has(self, key: str) -> bool:
        with self._lock:
            return key in self._steps

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    def bind_process(self, proc: subprocess.Popen[str]) -> None:
        with self._proc_lock:
            self._proc = proc
            already = self._cancel.is_set()
        if already:
            terminate_process(proc)

    def clear_process(self, proc: subprocess.Popen[str]) -> None:
        with self._proc_lock:
            if self._proc is proc:
                self._proc = None

    def request_cancel(self) -> None:
        self._cancel.set()
        self.step("cancel", "取消检索", "running", "正在停止采集进程。已经写好的内容会尽量保留。")
        with self._proc_lock:
            proc = self._proc
        if proc is None or proc.poll() is not None:
            return
        try:
            _signal_group(proc, signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError):
            return


def _signal_group(proc: subprocess.Popen[str], sig: int) -> None:
    pid = proc.pid
    if pid and os.getpgid(pid) == pid:
        os.killpg(pid, sig)
        return
    if sig == signal.SIGKILL:
        proc.kill()
    else:
        proc.terminate()


def terminate_process(proc: subprocess.Popen[str]) -> None:
    """结束采集子进程这一组，不碰用户事先打开的 Chrome。"""

    if proc.poll() is not None:
        return
    try:
        _signal_group(proc, signal.SIGTERM)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            proc.terminate()
        except OSError:
            return
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        try:
            _signal_group(proc, signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            try:
                proc.kill()
            except OSError:
                return
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            return
