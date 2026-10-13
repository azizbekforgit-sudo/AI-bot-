"""Voice loop, wake word and clap detection with scripted audio (no microphone needed)."""

import threading
import time

import numpy as np
import pytest

from claude_assistant.config import VoiceConfig
from claude_assistant.voice.audio import MicrophoneError
from claude_assistant.voice.clap import ClapDetector
from claude_assistant.voice.service import VoiceCallbacks, VoiceService
from claude_assistant.voice.speech_text import clean_for_speech
from claude_assistant.voice.wake import WakeMatcher

RATE = 16000


# ---- wake word ------------------------------------------------------------------

@pytest.mark.parametrize("heard", ["клод", "Клод!", "эй клод", "клот", "claude", "клауд открой"])
def test_wake_variants(heard):
    assert WakeMatcher(["клод"]).matches(heard)


@pytest.mark.parametrize("heard", ["код", "кот", "холод", "колода", "открой блокнот", ""])
def test_wake_rejects_similar_words(heard):
    assert not WakeMatcher(["клод"]).matches(heard)


def test_wake_strip():
    m = WakeMatcher(["клод"])
    assert m.strip("клод открой блокнот") == "открой блокнот"
    assert m.strip("открой блокнот") == "открой блокнот"
    assert m.strip("ну клод который час") == "который час"


def test_multiword_wake_phrase_fuzzy():
    m = WakeMatcher(["привет компьютер"], threshold=0.8)
    assert m.matches("привет компютер")
    assert not m.matches("привет мир")


# ---- clap detector ----------------------------------------------------------------

def _silence(seconds, level=0.002, seed=0):
    rng = np.random.default_rng(seed)
    return rng.normal(0, level, int(RATE * seconds))


def _clap(amplitude=0.8):
    t = np.arange(int(RATE * 0.12))
    rng = np.random.default_rng(1)
    return amplitude * rng.uniform(-1, 1, len(t)) * np.exp(-t / (RATE * 0.008))


def _pcm(samples):
    return (np.clip(samples, -1, 1) * 32767).astype(np.int16).tobytes()


def _feed(detector, samples):
    pcm = _pcm(samples)
    hits = 0
    for i in range(0, len(pcm), 3200):  # 100 ms chunks like the microphone
        hits += detector.process(pcm[i:i + 3200])
    return hits


def test_double_clap_triggers_once():
    audio = np.concatenate([_silence(1), _clap(), _silence(0.3), _clap(), _silence(1)])
    assert _feed(ClapDetector(0.35), audio) == 1


def test_single_clap_does_not_trigger():
    assert _feed(ClapDetector(0.35), np.concatenate([_silence(1), _clap(), _silence(2)])) == 0


def test_claps_too_far_apart():
    audio = np.concatenate([_silence(1), _clap(), _silence(1.5), _clap(), _silence(1)])
    assert _feed(ClapDetector(0.35), audio) == 0


def test_sustained_loud_sound_is_not_a_clap():
    t = np.arange(RATE * 2) / RATE
    tone = 0.8 * np.sin(2 * np.pi * 440 * t)
    audio = np.concatenate([_silence(1), tone, _silence(0.3), tone])
    assert _feed(ClapDetector(0.35), audio) == 0


def test_quiet_claps_below_threshold():
    audio = np.concatenate([_silence(1), _clap(0.2), _silence(0.3), _clap(0.2), _silence(1)])
    assert _feed(ClapDetector(0.5), audio) == 0


# ---- speech text ------------------------------------------------------------------

def test_clean_for_speech_strips_markdown_and_code():
    text = "## Итог\n**Готово.** Вот код:\n```python\nprint(1)\n```\nСм. https://example.com"
    spoken = clean_for_speech(text)
    assert "```" not in spoken and "**" not in spoken and "#" not in spoken
    assert "Код показан на экране" in spoken and "ссылка" in spoken


def test_clean_for_speech_truncates_at_sentence():
    text = "Первое предложение. " * 50
    spoken = clean_for_speech(text, max_chars=100)
    assert len(spoken) < 140 and spoken.endswith("Подробности на экране.")


# ---- voice service with scripted audio ---------------------------------------------

class ScriptedSource:
    """Chunks are text tokens encoded as bytes; b"" means silence."""

    def __init__(self, script, fail_first=0):
        self.script = list(script)
        self.fail_first = fail_first
        self.started = 0

    def start(self):
        self.started += 1
        if self.started <= self.fail_first:
            raise MicrophoneError("Микрофон отключён")

    def read(self, timeout=0.5):
        time.sleep(0.005)
        if self.script:
            return self.script.pop(0)
        time.sleep(0.05)
        return b""

    def healthy(self, stall_s=3.0):
        return True

    def drain(self):
        pass

    def stop(self):
        pass


class FakeRecognizer:
    """Utterance ends at an empty chunk; tokens are joined into the text."""

    def __init__(self, vocabulary=None):
        self.vocabulary = vocabulary
        self.words = []

    def accept(self, pcm):
        if pcm:
            word = pcm.decode()
            if self.vocabulary is None or word in self.vocabulary:
                self.words.append(word)
            return None
        if self.words:
            text, self.words = " ".join(self.words), []
            return text
        return None

    def partial(self):
        return " ".join(self.words)

    def flush(self):
        text, self.words = " ".join(self.words), []
        return text

    def reset(self):
        self.words = []


class FakeEngine:
    def __init__(self, grammar=True):
        self.grammar = grammar

    def wake_recognizer(self, phrases):
        return (FakeRecognizer(set(phrases)) if self.grammar else FakeRecognizer()), self.grammar

    def command_recognizer(self):
        return FakeRecognizer()


class Recorder:
    def __init__(self):
        self.commands, self.wakes, self.statuses, self.no_speech = [], [], [], 0
        self.done = threading.Event()

    def callbacks(self):
        return VoiceCallbacks(
            on_status=lambda s, d: self.statuses.append((s, d)),
            on_wake=self.wakes.append,
            on_command=lambda t: (self.commands.append(t), self.done.set()),
            on_no_speech=lambda: (setattr(self, "no_speech", self.no_speech + 1), self.done.set()),
        )


def _service(script, rec, config=None, grammar=True, tmp_path=None, **kw):
    source = ScriptedSource(script, **kw)
    svc = VoiceService(config or VoiceConfig(silence_timeout_s=1, command_timeout_s=3), tmp_path,
                       rec.callbacks(), source_factory=lambda _dev: source,
                       engine_factory=lambda _p: FakeEngine(grammar))
    return svc, source


def _words(*tokens):
    return [t.encode() for t in tokens]


def test_wake_then_command(tmp_path):
    rec = Recorder()
    svc, _ = _service(_words("шум", "клод", "", "открой", "блокнот", ""), rec, tmp_path=tmp_path)
    svc.start()
    assert rec.done.wait(5)
    svc.stop()
    assert rec.wakes == ["wake"]
    assert rec.commands == ["открой блокнот"]
    assert ("listening", "Слушаю…") in rec.statuses


def test_wake_and_command_in_one_utterance_full_recognition(tmp_path):
    rec = Recorder()
    svc, _ = _service(_words("клод", "который", "час", ""), rec, grammar=False, tmp_path=tmp_path)
    svc.start()
    assert rec.done.wait(5)
    svc.stop()
    assert rec.commands == ["который час"]


def test_silence_after_wake_reports_no_speech(tmp_path):
    rec = Recorder()
    svc, _ = _service(_words("клод", "") + [b""] * 30, rec, tmp_path=tmp_path)
    svc.start()
    assert rec.done.wait(6)
    svc.stop()
    assert rec.no_speech == 1 and rec.commands == []


def test_ignores_audio_while_speaking(tmp_path):
    """Echo prevention: the assistant's own voice saying 'клод' must not trigger it."""
    rec = Recorder()
    svc, _ = _service(_words("клод", "", "открой", "") * 3, rec, tmp_path=tmp_path)
    svc.set_speaking(True)
    svc.start()
    time.sleep(0.6)
    svc.stop()
    assert rec.wakes == [] and rec.commands == []


def test_muted_ignores_everything(tmp_path):
    rec = Recorder()
    svc, _ = _service(_words("клод", "", "открой", "блокнот", ""), rec, tmp_path=tmp_path)
    svc.set_muted(True)
    svc.start()
    time.sleep(0.5)
    svc.stop()
    assert rec.wakes == [] and rec.commands == []
    assert ("muted", "Микрофон выключен") in rec.statuses


def test_push_to_talk_skips_wake_word(tmp_path):
    rec = Recorder()
    svc, _ = _service([b""] * 3 + _words("сколько", "времени", ""), rec, tmp_path=tmp_path)
    svc.activate()
    svc.start()
    assert rec.done.wait(5)
    svc.stop()
    assert rec.wakes == ["hotkey"] and rec.commands == ["сколько времени"]


def test_wake_disabled_ignores_wake_word(tmp_path):
    rec = Recorder()
    cfg = VoiceConfig(wake_word_enabled=False, silence_timeout_s=1)
    svc, _ = _service(_words("клод", "", "открой", ""), rec, config=cfg, tmp_path=tmp_path)
    svc.start()
    time.sleep(0.5)
    svc.stop()
    assert rec.wakes == []


def test_microphone_failure_retries(tmp_path, monkeypatch):
    monkeypatch.setattr(VoiceService, "_sleep", lambda self, s: time.sleep(0.05))
    rec = Recorder()
    svc, source = _service(_words("клод", "", "стоп", ""), rec, tmp_path=tmp_path, fail_first=2)
    svc.start()
    assert rec.done.wait(5)
    svc.stop()
    assert source.started == 3
    assert any(s == "error" for s, _ in rec.statuses)
    assert rec.commands == ["стоп"]


def test_missing_model_reports_unavailable(tmp_path):
    rec = Recorder()
    svc = VoiceService(VoiceConfig(), tmp_path / "no-model", rec.callbacks(),
                       source_factory=lambda _d: ScriptedSource([]))
    svc.start()
    time.sleep(0.5)
    svc.stop()
    assert rec.statuses and rec.statuses[-1][0] == "unavailable"
    assert "setup.ps1" in rec.statuses[-1][1]


def test_clap_activation(tmp_path):
    audio = np.concatenate([_silence(0.5), _clap(), _silence(0.3), _clap(), _silence(0.5)])
    pcm = _pcm(audio)
    chunks = [pcm[i:i + 3200] for i in range(0, len(pcm), 3200)]

    class ClapEngine(FakeEngine):
        def command_recognizer(self):
            class R(FakeRecognizer):
                def accept(self, pcm):  # after the claps, the user says "время"
                    self.words = ["время"]
                    return "время"
            return R()

    rec = Recorder()
    source = ScriptedSource(chunks + [b"x"])
    svc = VoiceService(VoiceConfig(clap_enabled=True, wake_word_enabled=False), tmp_path, rec.callbacks(),
                       source_factory=lambda _d: source, engine_factory=lambda _p: ClapEngine())
    svc.start()
    assert rec.done.wait(5)
    svc.stop()
    assert rec.wakes == ["clap"]
