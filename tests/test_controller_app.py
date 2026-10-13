"""Controller behaviour with a fake brain, settings persistence, and full-app process tests."""

import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from claude_assistant.brain.claude_code import BrainResult, ClaudeStatus
from claude_assistant.brain.permissions import review_denial
from claude_assistant.config import AppConfig, load_config
from claude_assistant.memory import MemoryStore
from claude_assistant.voice.tts import Speaker
from conftest import wait_until


class FakeBrain:
    def __init__(self):
        self.requests = []
        self.results = []
        self.claude_path = ""
        self.use_subscription = True
        self.block = threading.Event()
        self.cancelled = threading.Event()

    def check(self, refresh=False):
        return ClaudeStatus(True, version="fake", path="claude")

    def run(self, request, on_event, cancel):
        self.requests.append(request)
        if self.block.is_set():
            while not cancel.is_set():
                time.sleep(0.01)
            return BrainResult(False, error_kind="cancelled", error_message="Задача отменена.")
        on_event(type("E", (), {"kind": "tool_use", "text": "Команда: git status", "data": {"id": "1"}})())
        return self.results.pop(0) if self.results else BrainResult(True, text="Сделано.", session_id="s1")

    def cancel(self):
        self.cancelled.set()


class FakeSpeaker(Speaker):
    def __init__(self):
        self.spoken = []
        super().__init__()

    def _say(self, text):
        self.spoken.append(text)


@pytest.fixture
def ctrl(qapp, tmp_path):
    from claude_assistant.core.controller import Controller

    brain = FakeBrain()
    speaker = FakeSpeaker()
    config = AppConfig()
    config.brain.workspaces = [str(tmp_path)]
    c = Controller(config, MemoryStore(tmp_path / "mem.json"), speaker=speaker, brain=brain, enable_voice=False)
    c.start()
    messages = []
    c.message.connect(lambda role, text: messages.append((role, text)))
    approvals = []
    c.approval_needed.connect(approvals.append)
    c.test = type("T", (), {"brain": brain, "speaker": speaker, "messages": messages, "approvals": approvals})
    wait_until(qapp, lambda: c.claude is not None)
    yield c
    c.shutdown()


def test_local_intent_answers_without_brain(ctrl, qapp):
    ctrl.submit("который час", source="voice")
    assert wait_until(qapp, lambda: any(r == "assistant" for r, _ in ctrl.test.messages))
    assert ctrl.test.brain.requests == []
    assert wait_until(qapp, lambda: ctrl.test.speaker.spoken)  # voice commands get a spoken answer
    assert ctrl.test.speaker.spoken[0].startswith("Сейчас")


def test_typed_reply_is_not_spoken_by_default(ctrl, qapp):
    ctrl.submit("какое сегодня число", source="text")
    assert wait_until(qapp, lambda: any(r == "assistant" for r, _ in ctrl.test.messages))
    time.sleep(0.2)
    assert ctrl.test.speaker.spoken == []


def test_brain_request_and_session_continuity(ctrl, qapp):
    ctrl.submit("проверь мой проект", source="text")
    assert wait_until(qapp, lambda: ("assistant", "Сделано.") in ctrl.test.messages)
    first = ctrl.test.brain.requests[0]
    assert first.resume_session is None and first.workspaces[0] == Path(ctrl.config.brain.workspaces[0])
    ctrl.submit("а теперь запусти тесты", source="text")
    assert wait_until(qapp, lambda: len(ctrl.test.brain.requests) == 2 and not ctrl.busy)
    assert ctrl.test.brain.requests[1].resume_session == "s1"
    ctrl.new_conversation()
    ctrl.submit("новая задача", source="text")
    assert wait_until(qapp, lambda: len(ctrl.test.brain.requests) == 3)
    assert ctrl.test.brain.requests[2].resume_session is None


def test_memory_reaches_brain_only_when_saved(ctrl, qapp):
    ctrl.submit("Запомни, что я пишу на Python", source="text")
    assert wait_until(qapp, lambda: ("assistant", "Запомнил.") in ctrl.test.messages)
    ctrl.submit("сделай что-нибудь полезное", source="text")
    assert wait_until(qapp, lambda: ctrl.test.brain.requests)
    assert "я пишу на Python" in ctrl.test.brain.requests[0].memory_context


def test_denial_requires_approval_then_reruns_with_exact_rule(ctrl, qapp, tmp_path):
    install = review_denial("Bash", {"command": "pip install requests"}, [tmp_path])
    forbidden = review_denial("Bash", {"command": "netsh advfirewall set allprofiles state off"}, [tmp_path])
    ctrl.test.brain.results.append(BrainResult(True, text="Нужен пакет.", session_id="s1", denials=[install, forbidden]))
    ctrl.submit("установи зависимости", source="text")
    assert wait_until(qapp, lambda: ctrl.test.approvals and ctrl.test.approvals[-1])
    assert [d.summary for d in ctrl.test.approvals[-1]] == [install.summary]  # forbidden one is never offered
    assert any(r == "error" and "Заблокировано" in t for r, t in ctrl.test.messages)
    assert len(ctrl.test.brain.requests) == 1  # nothing ran without approval
    ctrl.approve_pending()
    assert wait_until(qapp, lambda: len(ctrl.test.brain.requests) == 2)
    rerun = ctrl.test.brain.requests[1]
    assert "Bash(pip install requests)" in rerun.extra_rules
    assert not any("netsh" in r for r in rerun.extra_rules)


def test_reject_pending(ctrl, qapp, tmp_path):
    denial = review_denial("Bash", {"command": "git push"}, [tmp_path])
    ctrl.test.brain.results.append(BrainResult(True, text="", session_id="s1", denials=[denial]))
    ctrl.submit("отправь изменения", source="text")
    assert wait_until(qapp, lambda: ctrl.test.approvals and ctrl.test.approvals[-1])
    ctrl.reject_pending()
    assert wait_until(qapp, lambda: ("assistant", "Хорошо, не буду этого делать.") in ctrl.test.messages)
    time.sleep(0.2)
    assert len(ctrl.test.brain.requests) == 1


def test_emergency_stop_cancels_and_blocks(ctrl, qapp):
    ctrl.test.brain.block.set()
    ctrl.submit("долгая задача", source="text")
    assert wait_until(qapp, lambda: ctrl.busy)
    ctrl.emergency_stop()
    assert wait_until(qapp, lambda: not ctrl.busy)
    assert ctrl.state == "stopped"
    ctrl.test.brain.block.clear()
    ctrl.submit("ещё одна задача", source="text")
    qapp.processEvents()
    assert len(ctrl.test.brain.requests) == 1  # blocked while stopped
    ctrl.resume()
    ctrl.submit("ещё одна задача", source="text")
    assert wait_until(qapp, lambda: len(ctrl.test.brain.requests) == 2)


def test_voice_stop_cancels_running_task(ctrl, qapp):
    ctrl.test.brain.block.set()
    ctrl.submit("долгая задача", source="voice")
    assert wait_until(qapp, lambda: ctrl.busy)
    ctrl.submit("стоп", source="voice")
    assert wait_until(qapp, lambda: not ctrl.busy)
    assert any("отменена" in t for _, t in ctrl.test.messages)


def test_busy_rejects_second_task(ctrl, qapp):
    ctrl.test.brain.block.set()
    ctrl.submit("задача один", source="text")
    assert wait_until(qapp, lambda: ctrl.busy)
    ctrl.submit("задача два", source="text")
    assert wait_until(qapp, lambda: any("ещё работаю" in t for _, t in ctrl.test.messages))
    ctrl.cancel_task()
    assert wait_until(qapp, lambda: not ctrl.busy)


def test_brain_error_shown_and_recovers(ctrl, qapp):
    ctrl.test.brain.results.append(BrainResult(False, error_kind="network", error_message="Нет связи с серверами Claude."))
    ctrl.submit("что-нибудь", source="text")
    assert wait_until(qapp, lambda: ("error", "Нет связи с серверами Claude.") in ctrl.test.messages)
    ctrl.submit("ещё раз", source="text")
    assert wait_until(qapp, lambda: ("assistant", "Сделано.") in ctrl.test.messages)


def test_screenshot_disabled_by_default(ctrl, qapp):
    ctrl.submit("сделай скриншот", source="text")
    assert wait_until(qapp, lambda: any("Скриншоты выключены" in t for _, t in ctrl.test.messages))


def test_settings_page_persists(ctrl, qapp, tmp_path):
    from claude_assistant.ui.settings_page import SettingsPage

    page = SettingsPage(ctrl)
    page.user_name.setText("Азиз")
    page.autonomy.setCurrentIndex(page.autonomy.findData("assisted"))
    page.wake_words.setText("клод, джарвис")
    saved = []
    page.saved.connect(saved.append)
    page._save()
    assert saved
    ctrl.apply_config(saved[0])
    reloaded = load_config().config
    assert reloaded.user_name == "Азиз"
    assert reloaded.brain.autonomy == "assisted"
    assert reloaded.voice.wake_words == ["клод", "джарвис"]


def test_main_window_builds(ctrl, qapp):
    from claude_assistant.integration.hotkeys import HotkeyManager
    from claude_assistant.integration.startup import StartupManager
    from claude_assistant.ui.main_window import MainWindow

    window = MainWindow(ctrl, HotkeyManager(), StartupManager(backend=None))
    window.show()
    for row in range(4):
        window.nav.setCurrentRow(row)
        qapp.processEvents()
    ctrl.message.emit("assistant", "<b>не HTML</b>")
    qapp.processEvents()
    assert "&lt;b&gt;" in window.assistant.chat.toHtml()  # messages are escaped
    window.quit()


# ---- whole application in separate processes ----------------------------------------------------

def _env(home):
    env = dict(os.environ, CLAUDE_ASSISTANT_HOME=str(home), QT_QPA_PLATFORM="offscreen")
    env["PATH"] = os.environ.get("PATH", "")
    return env


def test_app_version():
    out = subprocess.run([sys.executable, "-m", "claude_assistant", "--version"], capture_output=True, text=True,
                         timeout=60)
    assert out.returncode == 0 and out.stdout.strip() == "0.1.0"


def test_single_instance_and_clean_shutdown(tmp_path):
    home = tmp_path / "home"
    first = subprocess.Popen([sys.executable, "-m", "claude_assistant", "--no-voice"], env=_env(home),
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        log = home / "logs" / "assistant.log"
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline and not (log.exists() and "started" in log.read_text(encoding="utf-8")):
            time.sleep(0.2)
        assert first.poll() is None, first.stdout.read()
        second = subprocess.run([sys.executable, "-m", "claude_assistant", "--no-voice"], env=_env(home),
                                capture_output=True, text=True, timeout=30)
        assert second.returncode == 0
        assert "Another instance is running" in log.read_text(encoding="utf-8")
        assert first.poll() is None  # the original keeps running
    finally:
        first.terminate()
        first.wait(15)


def test_change_voice_by_command(ctrl, qapp):
    class Voices(FakeSpeaker):
        def voices(self):
            return ["Нейро: Дмитрий (мужской)", "Нейро: Ирина (женский)"]

    ctrl.speaker.shutdown()
    ctrl.speaker = Voices()
    ctrl.speaker.on_start = lambda: None
    ctrl.speaker.on_end = lambda: None
    ctrl.submit("смени голос", source="voice")
    assert wait_until(qapp, lambda: any("Теперь я говорю так" in t for _, t in ctrl.test.messages))
    assert load_config().config.voice.tts_voice == "Нейро: Ирина (женский)"
    ctrl.submit("смени голос", source="text")
    assert wait_until(qapp, lambda: load_config().config.voice.tts_voice == "Нейро: Дмитрий (мужской)")


def test_full_access_toggle_and_auto_approve(ctrl, qapp, tmp_path):
    ctrl.set_full_access(True)
    assert load_config().config.brain.autonomy == "full"
    denial = review_denial("Bash", {"command": "pip install requests"}, [tmp_path])
    ctrl.test.brain.results += [BrainResult(True, text="", session_id="s1", denials=[denial]),
                                BrainResult(True, text="Установил.", session_id="s1")]
    ctrl.submit("поставь requests", source="text")
    assert wait_until(qapp, lambda: ("assistant", "Установил.") in ctrl.test.messages)
    assert any("Разрешаю автоматически" in t for _, t in ctrl.test.messages)
    assert ctrl.test.approvals == [] or not any(ctrl.test.approvals)  # no approval banner was needed
    assert "Bash(pip install requests)" in ctrl.test.brain.requests[1].extra_rules
    assert ctrl.test.brain.requests[0].autonomy == "full"
    ctrl.set_full_access(False)
    assert load_config().config.brain.autonomy == "balanced"


def test_auto_approve_is_bounded(ctrl, qapp, tmp_path):
    ctrl.set_full_access(True)
    denial = review_denial("Bash", {"command": "pip install x"}, [tmp_path])
    ctrl.test.brain.results += [BrainResult(True, text="", session_id="s1", denials=[denial]) for _ in range(4)]
    ctrl.submit("сделай", source="text")
    assert wait_until(qapp, lambda: ctrl.test.approvals and ctrl.test.approvals[-1], timeout=10)
    assert len(ctrl.test.brain.requests) == 3  # original + 2 automatic retries, then it asks the user


def test_brain_request_carries_speed_settings(ctrl, qapp):
    ctrl.submit("что-нибудь", source="text")
    assert wait_until(qapp, lambda: ctrl.test.brain.requests)
    request = ctrl.test.brain.requests[0]
    assert request.model == "opus" and request.effort == "low" and request.lean is True
