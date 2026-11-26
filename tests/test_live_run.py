import os
import socket
import sys
import threading
import time
from pathlib import Path

import pytest

from helpers import make_settings
from hongsearch.collectors.base import CollectorError
from hongsearch.collectors.live_run import (
    ProcessResult,
    decide_run_outcome,
    interpret_log_line,
    probe_cdp,
    read_cdp_connect_existing,
    read_cdp_port,
    run_process,
)
from hongsearch.collectors.mediacrawler import MediaCrawlerCollector, parse_output_dir
from hongsearch.progress import Progress

CONTENT = (
    '{"note_id": "n1", "title": "五日寿司", "desc": "广州的一家日料店", '
    '"time": "2026-11-20", "nickname": "周末去吃", "liked_count": "3", '
    '"collected_count": "1", "comment_count": "1", "share_count": "0", '
    '"note_url": "https://www.xiaohongshu.com/explore/n1", "tag_list": "寿司", '
    '"source_keyword": "广州 日料"}\n'
)
COMMENT = (
    '{"note_id": "n1", "content": "要排队", "nickname": "路过", "like_count": 1, '
    '"create_time": "2026-11-20"}\n'
)
SECRET = "web_session=supersecretvalue"


def test_interpret_known_mediacrawler_lines():
    assert interpret_log_line("[CDPBrowserManager] Please check your browser for a confirmation dialog and accept it") == "cdp_accept"
    assert interpret_log_line("[CDPBrowserManager] CDP port 9222 is not accessible") == "cdp_closed"
    assert interpret_log_line("[CDPBrowserManager] Successfully connected to existing browser") == "cdp_ready"
    assert interpret_log_line("[XiaoHongShuLogin.login_by_qrcode] waiting for scan code login, remaining time is 120s") == "qr"
    assert interpret_log_line("[XiaoHongShuLogin.check_login_state] CAPTCHA appeared, please verify manually.") == "captcha"
    assert interpret_log_line("Cannot connect to existing browser on port 9222") == "cdp_failed"


def test_probe_cdp_against_a_local_socket():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        sock.listen(1)
        port = sock.getsockname()[1]
        assert probe_cdp("127.0.0.1", port, timeout=0.5) is True
    assert probe_cdp("127.0.0.1", port, timeout=0.2) is False


def test_read_cdp_settings(tmp_path):
    home = tmp_path / "MediaCrawler"
    config = home / "config"
    config.mkdir(parents=True)
    (config / "base_config.py").write_text(
        "CDP_DEBUG_PORT = 9333\nCDP_CONNECT_EXISTING = False\n",
        encoding="utf-8",
    )
    assert read_cdp_port(str(home)) == 9333
    assert read_cdp_connect_existing(str(home)) is False
    assert read_cdp_port(None) == 9222
    assert read_cdp_connect_existing(None) is True


def test_closed_port_tells_the_user_how_to_enable_debugging(tmp_path):
    progress = Progress()
    run_script(tmp_path, "print('hi', flush=True)\n", 1, progress, probe=lambda: False)
    waiting = [step for step in progress.events() if step.state == "waiting"]
    assert waiting
    detail = waiting[0].detail
    assert "还没有连上" in detail
    assert "127.0.0.1:9222" in detail
    assert "chrome://inspect/#remote-debugging" in detail


def test_open_port_does_not_claim_debugging_is_off(tmp_path):
    progress = Progress()
    run_script(tmp_path, "print('ready', flush=True)\n", 1, progress, probe=lambda: True)
    details = " ".join(step.detail for step in progress.events() if step.key.endswith("-cdp"))
    assert "已检测到" in details
    assert "接受" in details
    assert "还没有连上" not in details


def test_qr_and_accept_lines_become_waiting_steps(tmp_path):
    script = (
        'print("[CDPBrowserManager] Please check your browser for a confirmation dialog and accept it", flush=True)\n'
        'print("[DouYinLogin.login_by_qrcode] waiting for scan code login, remaining time is 120s", flush=True)\n'
    )
    progress = Progress()
    run_script(tmp_path, script, 1, progress, probe=lambda: True)
    details = [step.detail for step in progress.events() if step.state == "waiting"]
    assert any("接受" in detail for detail in details)
    assert any("扫" in detail and "抖音" in detail for detail in details)


def test_logs_and_progress_do_not_keep_cookies(tmp_path):
    script = (
        f'print("leak {SECRET}", flush=True)\n'
        'print("[XiaoHongShuLogin.login_by_qrcode] waiting for scan code login", flush=True)\n'
    )
    progress = Progress()
    result, _run_dir = run_script(tmp_path, script, 1, progress, probe=lambda: True)
    assert SECRET not in result.log
    assert "[已隐藏]" in result.log
    assert all(SECRET not in step.detail for step in progress.events())


def test_timeout_keeps_notes_already_written_and_shows_them_while_running(tmp_path):
    script = (
        "import os, time\n"
        "from pathlib import Path\n"
        "root = Path(os.environ['OUT'])\n"
        f"(root / 'search_contents_2026-11-26.jsonl').write_text({CONTENT!r}, encoding='utf-8')\n"
        f"(root / 'search_comments_2026-11-26.jsonl').write_text({COMMENT!r}, encoding='utf-8')\n"
        "print('[CDPBrowserManager] Successfully connected to existing browser', flush=True)\n"
        "time.sleep(30)\n"
    )
    progress = Progress()
    holder: dict = {}

    def work():
        holder["pair"] = run_script(tmp_path, script, 8, progress, probe=lambda: True)

    thread = threading.Thread(target=work)
    thread.start()
    saw_while_running = False
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and thread.is_alive():
        if any(step.key.endswith("notes") and "已写入" in step.detail for step in progress.snapshot()):
            saw_while_running = True
            progress.request_cancel()
            break
        time.sleep(0.05)
    thread.join(timeout=8)
    assert saw_while_running
    assert not thread.is_alive()
    result, run_dir = holder["pair"]
    items = parse_output_dir(run_dir, "xhs")
    kept, _log, warning = decide_run_outcome(items, result, "xhs", 8)
    assert [item.external_id for item in kept] == ["n1"]
    assert kept[0].comments
    assert warning is not None
    assert "保留" in warning
    cdp = next(step for step in progress.snapshot() if step.key.endswith("-cdp"))
    assert cdp.state == "done"
    assert "还没有连上" not in cdp.detail


def test_timeout_without_files_names_the_blocker():
    qr = ProcessResult(returncode=None, timed_out=True, cancelled=False, log="", phase="qr")
    with pytest.raises(CollectorError, match="扫码"):
        decide_run_outcome([], qr, "xhs", 240)
    closed = ProcessResult(returncode=None, timed_out=True, cancelled=False, log="", phase="cdp_closed")
    with pytest.raises(CollectorError, match="chrome://inspect/#remote-debugging"):
        decide_run_outcome([], closed, "dy", 240)


def test_nonzero_exit_and_crash_text_keep_saved_rows_without_secrets():
    result = ProcessResult(returncode=1, timed_out=False, cancelled=False, log=f"fail {SECRET}", phase="notes")
    kept, log, warning = decide_run_outcome([object()], result, "xhs", 30, [SECRET])
    assert len(kept) == 1
    assert warning is not None and "可能不完整" in warning
    assert SECRET not in log
    assert SECRET not in warning
    empty = ProcessResult(returncode=1, timed_out=False, cancelled=False, log=f"fail {SECRET}", phase="boot")
    with pytest.raises(CollectorError) as caught:
        decide_run_outcome([], empty, "xhs", 30, [SECRET])
    assert SECRET not in str(caught.value)


def test_cancel_stops_a_sleeping_process_and_keeps_written_rows(tmp_path):
    script = (
        "import os, time\n"
        "from pathlib import Path\n"
        "root = Path(os.environ['OUT'])\n"
        f"(root / 'search_contents_2026-11-26.jsonl').write_text({CONTENT!r}, encoding='utf-8')\n"
        "time.sleep(30)\n"
    )
    progress = Progress()
    holder: dict = {}

    def work():
        holder["pair"] = run_script(tmp_path, script, 20, progress, probe=lambda: True)

    thread = threading.Thread(target=work)
    thread.start()
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and thread.is_alive():
        if any(step.key.endswith("notes") for step in progress.snapshot()):
            break
        time.sleep(0.05)
    started = time.monotonic()
    progress.request_cancel()
    thread.join(timeout=5)
    assert not thread.is_alive()
    assert time.monotonic() - started < 5
    result, run_dir = holder["pair"]
    assert result.cancelled is True
    items = parse_output_dir(run_dir, "xhs")
    _kept, _log, warning = decide_run_outcome(items, result, "xhs", 20)
    assert warning is not None and "已取消" in warning and "保留" in warning


def test_default_run_reads_the_directory_when_the_process_times_out(tmp_path, monkeypatch):
    home = tmp_path / "MediaCrawler"
    home.mkdir()
    settings = make_settings(
        tmp_path,
        mediacrawler_home=str(home),
        login_type="qrcode",
        crawler_timeout_sec=30,
    )
    created: dict = {}

    def fake_run_process(**kwargs):
        run_dir = Path(kwargs["run_dir"])
        (run_dir / "search_contents_2026-11-26.jsonl").write_text(CONTENT, encoding="utf-8")
        (run_dir / "search_comments_2026-11-26.jsonl").write_text(COMMENT, encoding="utf-8")
        created["dir"] = run_dir
        return ProcessResult(returncode=None, timed_out=True, cancelled=False, log="timed out", phase="notes")

    monkeypatch.setattr("hongsearch.collectors.mediacrawler.run_process", fake_run_process)
    collector = MediaCrawlerCollector(settings)
    try:
        items, _log, warning = collector._default_run("xhs", "广州 日料")
    finally:
        directory = created.get("dir")
        if directory is not None:
            for child in directory.glob("*"):
                child.unlink(missing_ok=True)
            directory.rmdir()
    assert [item.external_id for item in items] == ["n1"]
    assert items[0].comments[0].content == "要排队"
    assert warning is not None and "保留" in warning


def test_default_run_keeps_rows_when_the_process_wrapper_crashes(tmp_path, monkeypatch):
    home = tmp_path / "MediaCrawler"
    home.mkdir()
    settings = make_settings(
        tmp_path,
        mediacrawler_home=str(home),
        xhs_cookies=SECRET,
        login_type="cookie",
    )
    created: dict = {}

    def fake_run_process(**kwargs):
        run_dir = Path(kwargs["run_dir"])
        (run_dir / "search_contents_2026-11-26.jsonl").write_text(CONTENT, encoding="utf-8")
        created["dir"] = run_dir
        raise RuntimeError(f"boom {SECRET}")

    monkeypatch.setattr("hongsearch.collectors.mediacrawler.run_process", fake_run_process)
    collector = MediaCrawlerCollector(settings)
    try:
        items, _log, warning = collector._default_run("xhs", "广州 日料")
    finally:
        directory = created.get("dir")
        if directory is not None:
            for child in directory.glob("*"):
                child.unlink(missing_ok=True)
            directory.rmdir()
    assert items
    assert warning is not None and "保留" in warning
    assert SECRET not in warning


def run_script(tmp_path: Path, body: str, timeout: float, progress: Progress, probe):
    script = tmp_path / f"crawl-{progress.started_at:.0f}.py"
    script.write_text(body, encoding="utf-8")
    run_dir = tmp_path / f"out-{script.stem}"
    run_dir.mkdir()
    env = os.environ.copy()
    env["OUT"] = str(run_dir)
    env["PYTHONUNBUFFERED"] = "1"
    result = run_process(
        command=[sys.executable, "-u", str(script)],
        cwd=str(tmp_path),
        env=env,
        run_dir=run_dir,
        platform="dy" if "DouYin" in body else "xhs",
        login_type="qrcode",
        timeout_sec=timeout,
        progress=progress,
        secrets=[SECRET],
        probe=probe,
    )
    return result, run_dir
