from helpers import make_settings
from hongsearch.config import load_settings, resolve_runtime_mode


def test_auto_without_install_is_demo(monkeypatch):
    monkeypatch.setenv("APP_MODE", "auto")
    monkeypatch.setenv("MEDIACRAWLER_HOME", "")
    monkeypatch.setenv("XHS_COOKIES", "")
    monkeypatch.setenv("DY_COOKIES", "")
    monkeypatch.setenv("LOGIN_TYPE", "cookie")
    monkeypatch.setenv("MAX_NOTES", "100")
    settings = load_settings()
    mode, reason = resolve_runtime_mode(settings, "auto")
    assert mode == "demo"
    assert settings.max_notes == 20
    assert "演示" in reason


def test_auto_with_home_and_cookie_is_live(tmp_path, monkeypatch):
    home = tmp_path / "MediaCrawler"
    home.mkdir()
    (home / "main.py").write_text("# stub\n", encoding="utf-8")
    monkeypatch.setenv("APP_MODE", "auto")
    monkeypatch.setenv("MEDIACRAWLER_HOME", str(home))
    monkeypatch.setenv("LOGIN_TYPE", "cookie")
    monkeypatch.setenv("XHS_COOKIES", "xhs-cookie-value")
    monkeypatch.setenv("DY_COOKIES", "")
    settings = load_settings()
    mode, _reason = resolve_runtime_mode(settings, "auto")
    assert mode == "live"


def test_explicit_demo_choice_wins(tmp_path):
    settings = make_settings(tmp_path, app_mode="live", mediacrawler_home="/anywhere", xhs_cookies="x")
    mode, reason = resolve_runtime_mode(settings, "demo")
    assert mode == "demo"
    assert "演示" in reason
