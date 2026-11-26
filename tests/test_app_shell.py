from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_search_ui_is_not_a_blocking_spinner():
    app = (ROOT / "app.py").read_text(encoding="utf-8")
    crawler = (ROOT / "hongsearch/collectors/mediacrawler.py").read_text(encoding="utf-8")
    assert "正在检索" not in app
    assert "正在整理分析" not in app
    assert "取消检索" in app
    assert "run_every" in app
    assert "subprocess.run" not in crawler
    assert "parse_output_dir" in crawler
