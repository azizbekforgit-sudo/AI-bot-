import sys

import pytest

from claude_assistant.integration.hotkeys import parse_hotkey
from claude_assistant.integration.startup import StartupManager, launch_command
from claude_assistant.tools import apps, clock, files, web


# ---- startup ---------------------------------------------------------------------------

class FakeRegistry:
    def __init__(self):
        self.values = {}

    def get(self, name):
        return self.values.get(name)

    def set(self, name, value):
        self.values[name] = value

    def delete(self, name):
        self.values.pop(name, None)


def test_startup_enable_disable():
    reg = FakeRegistry()
    mgr = StartupManager(reg, name="TestAssistant")
    assert not mgr.is_enabled()
    mgr.apply(True)
    assert mgr.is_enabled()
    assert reg.values["TestAssistant"].endswith("--minimized")
    mgr.apply(False)
    assert not mgr.is_enabled() and reg.values == {}
    mgr.disable()  # idempotent


def test_launch_command_runs_module():
    cmd = launch_command()
    assert "-m claude_assistant --minimized" in cmd and cmd.startswith('"')


def test_startup_unsupported_without_backend(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    mgr = StartupManager()
    assert not mgr.supported and not mgr.is_enabled()
    with pytest.raises(RuntimeError):
        mgr.enable()


# ---- hotkeys ------------------------------------------------------------------------------

def test_parse_hotkey():
    assert parse_hotkey("ctrl+alt+space") == (0x2 | 0x1, 0x20)
    assert parse_hotkey("ctrl+shift+f5") == (0x2 | 0x4, 0x74)
    assert parse_hotkey("ctrl+alt+x") == (0x3, ord("X"))
    for bad in ("space", "ctrl+foo", "hyper+x"):
        with pytest.raises(ValueError):
            parse_hotkey(bad)


# ---- files ------------------------------------------------------------------------------------

@pytest.fixture
def tree(tmp_path):
    allowed = tmp_path / "allowed"
    (allowed / "telegram-bot" / "src").mkdir(parents=True)
    (allowed / "telegram-bot" / "README.md").write_text("бот", encoding="utf-8")
    (allowed / "notes" / "bot-ideas.txt").parent.mkdir()
    (allowed / "notes" / "bot-ideas.txt").write_text("идеи", encoding="utf-8")
    (allowed / "node_modules" / "bot-lib").mkdir(parents=True)
    secret = tmp_path / "private"
    secret.mkdir()
    (secret / "bot-secret.txt").write_text("x", encoding="utf-8")
    return allowed, secret


def test_search_only_in_permitted_roots(tree):
    allowed, _ = tree
    result = files.search("bot", [allowed])
    found = result.data["paths"]
    assert any(p.endswith("telegram-bot") for p in found)
    assert not any("private" in p or "node_modules" in p for p in found)
    assert found[0].endswith("telegram-bot")  # folders (projects) rank first


def test_search_without_roots(tmp_path):
    assert not files.search("x", [tmp_path / "missing"]).ok


def test_read_text_respects_roots(tree):
    allowed, secret = tree
    assert files.read_text(allowed / "notes" / "bot-ideas.txt", [allowed]).data["text"] == "идеи"
    assert not files.read_text(secret / "bot-secret.txt", [allowed]).ok
    assert not files.read_text(allowed / ".." / "private" / "bot-secret.txt", [allowed]).ok


# ---- web --------------------------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("github.com", "https://github.com"),
    ("https://example.org/a?b=1", "https://example.org/a?b=1"),
    ("ya точка ru", "https://ya.ru"),
])
def test_normalize_url(raw, expected):
    assert web.normalize_url(raw) == expected


@pytest.mark.parametrize("raw", ["javascript:alert(1)", "file:///C:/Windows/system32", "просто текст", ""])
def test_normalize_url_rejects(raw):
    assert web.normalize_url(raw) is None


def test_open_url_uses_browser(monkeypatch):
    opened = []
    monkeypatch.setattr(web.webbrowser, "open", lambda url, new=0: opened.append(url) or True)
    assert web.open_url("github.com").ok and opened == ["https://github.com"]
    assert not web.open_url("javascript:alert(1)").ok


# ---- apps ---------------------------------------------------------------------------------------

def test_open_unknown_program_fails_honestly():
    result = apps.open_app("фотошоп", "definitely-not-installed-xyz.exe")
    assert not result.ok and "Не нашёл" in result.message


@pytest.mark.skipif(sys.platform == "win32", reason="uses a POSIX helper process")
def test_open_and_close_app_verifies_process(tmp_path):
    script = tmp_path / "fakeapp"
    script.write_text("#!/bin/sh\nexec sleep 30\n", encoding="utf-8")
    script.chmod(0o755)
    command = str(script)
    opened = apps.open_app("тест", command, verify_timeout=2)
    assert opened.ok and opened.verified
    closed = apps.close_app("тест", "sleep")
    assert closed.ok and closed.verified


def test_app_that_crashes_is_reported(tmp_path):
    if sys.platform == "win32":
        pytest.skip("POSIX helper")
    script = tmp_path / "crashy"
    script.write_text("#!/bin/sh\nexit 3\n", encoding="utf-8")
    script.chmod(0o755)
    result = apps.open_app("крэш", str(script), verify_timeout=2)
    assert not result.ok and "код" in result.message


def test_protected_processes_never_closed():
    result = apps.close_app("проводник", "explorer.exe")
    assert result.ok  # nothing matched: explorer.exe is in the protected list and is filtered out
    assert "explorer.exe" in apps.PROTECTED


def test_resolve_alias():
    assert apps.resolve_alias("vs code пожалуйста", {"vs code": "code"}) == ("vs code", "code")
    assert apps.resolve_alias("фотошоп", {"vs code": "code"}) is None


# ---- clock -------------------------------------------------------------------------------------------

def test_clock_phrases():
    from datetime import datetime

    assert clock.time_phrase(datetime(2026, 1, 1, 21, 5)) == "Сейчас 21 час 5 минут."
    assert clock.time_phrase(datetime(2026, 1, 1, 3, 22)) == "Сейчас 3 часа 22 минуты."
    assert clock.date_phrase(datetime(2026, 10, 11)) == "Сегодня воскресенье, 11 октября 2026 года."
