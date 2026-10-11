import json
import threading
import time
from pathlib import Path

import pytest

from claude_assistant.brain.claude_code import BrainRequest, ClaudeCodeBrain, classify_error


def _request(tmp_path, **kw):
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    defaults = dict(text="проверь git status", autonomy="balanced", workspaces=[ws])
    defaults.update(kw)
    return BrainRequest(**defaults)


def _run(brain, request, cancel=None):
    events = []
    result = brain.run(request, events.append, cancel or threading.Event())
    return result, events


def test_missing_claude_reports_not_installed(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path))
    brain = ClaudeCodeBrain(claude_path=str(tmp_path / "nope"))
    result, _ = _run(brain, _request(tmp_path))
    assert not result.ok and result.error_kind == "not_installed"


def test_check_detects_version(fake_claude):
    status = ClaudeCodeBrain(str(fake_claude)).check()
    assert status.available and status.version.startswith("9.9.9")
    assert status.supports_permission_prompts_flag


def test_success_streams_events_and_verifies(fake_claude, tmp_path, monkeypatch):
    record = tmp_path / "rec.json"
    monkeypatch.setenv("FAKE_CLAUDE_RECORD", str(record))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-should-not-leak")
    brain = ClaudeCodeBrain(str(fake_claude), use_subscription=True)
    result, events = _run(brain, _request(tmp_path, memory_context="- любит кофе", user_name="Азиз"))
    assert result.ok, result.error_message
    assert result.text == "Готово: рабочее дерево чистое."
    assert result.session_id == "sess-123"
    assert result.tool_calls == 1
    kinds = [e.kind for e in events]
    assert kinds[:1] == ["init"] and "tool_use" in kinds and "tool_result" in kinds

    rec = json.loads(record.read_text(encoding="utf-8"))
    argv = rec["argv"]
    assert "-p" in argv and "stream-json" in argv
    assert argv[argv.index("--permission-mode") + 1] == "acceptEdits"
    assert "--permission-prompts" in argv
    assert "--dangerously-skip-permissions" not in argv
    assert "проверь git status" not in " ".join(argv)  # the prompt travels via stdin only
    assert rec["prompt"].endswith("проверь git status")
    assert "любит кофе" in rec["prompt"] and "Азиз" in rec["prompt"]
    assert rec["has_api_key"] is False  # subscription mode strips the API key
    assert Path(rec["cwd"]).name == "ws"


def test_resume_sends_only_user_text(fake_claude, tmp_path, monkeypatch):
    record = tmp_path / "rec.json"
    monkeypatch.setenv("FAKE_CLAUDE_RECORD", str(record))
    brain = ClaudeCodeBrain(str(fake_claude))
    _run(brain, _request(tmp_path, resume_session="sess-123", text="а теперь закоммить"))
    rec = json.loads(record.read_text(encoding="utf-8"))
    assert rec["argv"][rec["argv"].index("--resume") + 1] == "sess-123"
    assert rec["prompt"] == "а теперь закоммить"


def test_assisted_uses_plan_mode(fake_claude, tmp_path, monkeypatch):
    record = tmp_path / "rec.json"
    monkeypatch.setenv("FAKE_CLAUDE_RECORD", str(record))
    _run(ClaudeCodeBrain(str(fake_claude)), _request(tmp_path, autonomy="assisted"))
    argv = json.loads(record.read_text(encoding="utf-8"))["argv"]
    assert argv[argv.index("--permission-mode") + 1] == "plan"


def test_denials_are_reviewed(fake_claude, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_SCENARIO", "denial")
    result, _ = _run(ClaudeCodeBrain(str(fake_claude)), _request(tmp_path))
    assert result.ok
    by_cmd = {d.tool_input["command"]: d for d in result.denials}
    assert by_cmd["npm install left-pad"].approvable and by_cmd["npm install left-pad"].risk == "high"
    assert not by_cmd["Set-MpPreference -DisableRealtimeMonitoring $true"].approvable


def test_auth_error_classified(fake_claude, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_SCENARIO", "auth")
    result, _ = _run(ClaudeCodeBrain(str(fake_claude)), _request(tmp_path))
    assert not result.ok and result.error_kind == "auth"
    assert "войдите" in result.error_message


def test_rate_limit_classified(fake_claude, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_SCENARIO", "rate_limit")
    result, _ = _run(ClaudeCodeBrain(str(fake_claude)), _request(tmp_path))
    assert result.error_kind == "rate_limit"


def test_timeout_kills_process(fake_claude, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_SCENARIO", "hang")
    started = time.monotonic()
    result, _ = _run(ClaudeCodeBrain(str(fake_claude)), _request(tmp_path, timeout_s=1.5))
    assert result.error_kind == "timeout"
    assert time.monotonic() - started < 10


def test_cancel_stops_quickly(fake_claude, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_SCENARIO", "hang")
    cancel = threading.Event()
    threading.Timer(0.8, cancel.set).start()
    started = time.monotonic()
    result, _ = _run(ClaudeCodeBrain(str(fake_claude)), _request(tmp_path), cancel)
    assert result.error_kind == "cancelled"
    assert time.monotonic() - started < 8


def test_iteration_limit(fake_claude, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_SCENARIO", "loop")
    result, _ = _run(ClaudeCodeBrain(str(fake_claude)), _request(tmp_path, max_tool_calls=5))
    assert result.error_kind == "iteration_limit"
    assert result.tool_calls == 6


@pytest.mark.parametrize("text,kind", [
    ("Please run /login", "auth"),
    ("Error: 401 Unauthorized", "auth"),
    ("Claude usage limit reached", "rate_limit"),
    ("API Error: 529 Overloaded", "overloaded"),
    ("getaddrinfo ENOTFOUND api.anthropic.com", "network"),
    ("something odd", "crash"),
])
def test_classify_error(text, kind):
    assert classify_error(text) == kind


def test_offline_gives_up_after_retries(fake_claude, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_SCENARIO", "offline")
    started = time.monotonic()
    result, events = _run(ClaudeCodeBrain(str(fake_claude)), _request(tmp_path))
    assert result.error_kind == "network"
    assert "интернет" in result.error_message
    assert sum(e.kind == "status" for e in events) == 4
    assert time.monotonic() - started < 10
