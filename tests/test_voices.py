"""Neural voices: catalogue, download fallbacks, playback/interrupt, engine switching."""

import io
import os
import tarfile
import threading
import time
from pathlib import Path

import numpy as np
import pytest

from claude_assistant.voice import piper_voices
from claude_assistant.voice.piper_voices import CATALOG, PiperSpeaker, VoiceInfo
from claude_assistant.voice.tts import MultiSpeaker, NullSpeaker, Speaker


def _install(directory: Path, *voice_ids: str) -> None:
    for vid in voice_ids:
        (directory / f"{vid}.onnx").write_bytes(b"model")
        (directory / f"{vid}.onnx.json").write_text("{}", encoding="utf-8")


class FakeChunk:
    def __init__(self, n):
        self.audio_int16_array = np.zeros(n, dtype=np.int16)
        self.sample_rate = 22050


class FakeVoice:
    def __init__(self, path):
        self.path = path
        self.configs = []

    def synthesize(self, text, config):
        self.configs.append(config)
        for _sentence in text.split("."):
            if _sentence.strip():
                yield FakeChunk(2205)


class RecordingPlayer:
    def __init__(self, delay=0.0):
        self.played = []
        self.delay = delay

    def play(self, samples, rate, stop):
        end = time.monotonic() + self.delay
        while time.monotonic() < end and not stop.is_set():
            time.sleep(0.005)
        self.played.append((len(samples), rate))


def _wait(predicate, timeout=5):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def test_installed_requires_model_and_config(tmp_path):
    (tmp_path / "ru_RU-denis-medium.onnx").write_bytes(b"x")  # config missing
    _install(tmp_path, "ru_RU-irina-medium")
    assert [v.id for v in piper_voices.installed(tmp_path)] == ["ru_RU-irina-medium"]


def test_piper_speaker_plays_and_picks_voice(tmp_path):
    _install(tmp_path, "ru_RU-dmitri-medium", "ru_RU-irina-medium")
    loaded = []
    player = RecordingPlayer()
    speaker = PiperSpeaker(tmp_path, player=player, loader=lambda p: loaded.append(p) or FakeVoice(p))
    assert speaker.voices() == ["Нейро: Дмитрий (мужской)", "Нейро: Ирина (женский)"]
    speaker.configure("Нейро: Ирина (женский)", rate=5, volume=50)
    done = threading.Event()
    speaker.on_end = done.set
    speaker.speak("Первое предложение. Второе предложение.")
    assert done.wait(5)
    assert [p.name for p in loaded] == ["ru_RU-irina-medium.onnx"]
    assert len(player.played) == 2 and player.played[0][1] == 22050
    speaker.shutdown()


def test_piper_default_voice_and_rate_mapping(tmp_path):
    _install(tmp_path, "ru_RU-denis-medium")
    speaker = PiperSpeaker(tmp_path, player=RecordingPlayer(), loader=FakeVoice)
    speaker.configure("", rate=10, volume=100)
    assert speaker._voice_id == "ru_RU-denis-medium"
    assert speaker._length_scale == pytest.approx(0.6)
    speaker.configure("неизвестный", rate=-10, volume=0)
    assert speaker._voice_id == "ru_RU-denis-medium" and speaker._length_scale == pytest.approx(1.4)
    speaker.shutdown()


def test_piper_stop_interrupts(tmp_path):
    _install(tmp_path, "ru_RU-dmitri-medium")
    player = RecordingPlayer(delay=0.5)
    speaker = PiperSpeaker(tmp_path, player=player, loader=FakeVoice)
    speaker.configure("", 0, 90)
    speaker.speak("Раз. Два. Три. Четыре. Пять.")
    assert _wait(lambda: speaker.speaking)
    started = time.monotonic()
    speaker.stop()
    assert _wait(lambda: not speaker.speaking, 2)
    assert time.monotonic() - started < 1.0
    assert len(player.played) <= 1  # remaining sentences were not played
    speaker.shutdown()


def test_switching_voice_loads_new_model(tmp_path):
    _install(tmp_path, "ru_RU-dmitri-medium", "ru_RU-ruslan-medium")
    loaded = []
    speaker = PiperSpeaker(tmp_path, player=RecordingPlayer(), loader=lambda p: loaded.append(p.stem) or FakeVoice(p))
    done = threading.Event()
    speaker.on_end = done.set
    for label in ("Нейро: Дмитрий (мужской)", "Нейро: Руслан (мужской)"):
        done.clear()
        speaker.configure(label, 0, 90)
        speaker.speak("Тест.")
        assert done.wait(5)
    assert loaded == ["ru_RU-dmitri-medium", "ru_RU-ruslan-medium"]
    speaker.shutdown()


def test_download_falls_back_to_mirror(tmp_path):
    payload = tmp_path / "mirror"
    payload.mkdir()
    archive = payload / "voice.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        for name, data in (("ru-test.onnx", b"onnx"), ("ru-test.onnx.json", b"{}")):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    voice = VoiceInfo("ru_RU-test-medium", "Нейро: Тест", (
        (tmp_path / "missing" / "ru_RU-test-medium").as_uri(),
        archive.as_uri() + "#ru-test",
    ))
    target = tmp_path / "voices"
    target.mkdir()
    path = piper_voices.download(voice, target)
    assert path.read_bytes() == b"onnx"
    assert (target / "ru_RU-test-medium.onnx.json").read_text() == "{}"
    assert not list(target.glob("*.part"))


def test_download_reports_all_failures(tmp_path):
    voice = VoiceInfo("ru_RU-x-medium", "x", ((tmp_path / "nope").as_uri(),))
    with pytest.raises(RuntimeError):
        piper_voices.download(voice, tmp_path)
    assert not list(tmp_path.glob("ru_RU-x-medium*"))


def test_catalog_sources_are_https():
    for voice in CATALOG:
        assert voice.urls and all(u.startswith("https://") for u in voice.urls)


class LabelSpeaker(Speaker):
    def __init__(self, name, voices):
        self.name = name
        self._voices = voices
        self.configured = None
        self.said = []
        super().__init__()

    def voices(self):
        return list(self._voices)

    def configure(self, voice, rate, volume):
        self.configured = voice

    def _say(self, text):
        self.said.append(text)


def test_multispeaker_routes_by_voice():
    neural = LabelSpeaker("Piper (нейроголоса)", ["Нейро: Дмитрий (мужской)"])
    system = LabelSpeaker("Windows SAPI", ["Microsoft Irina Desktop"])
    multi = MultiSpeaker([neural, system])
    assert multi.voices() == ["Нейро: Дмитрий (мужской)", "Microsoft Irina Desktop"]
    multi.configure("Microsoft Irina Desktop", 0, 90)
    assert multi.active is system and system.configured == "Microsoft Irina Desktop"
    multi.configure("", 0, 90)  # automatic: neural voices preferred
    assert multi.active is neural
    events = []
    multi.on_start = lambda: events.append("start")
    multi.speak("Привет")
    assert _wait(lambda: neural.said == ["Привет"])
    assert _wait(lambda: events == ["start"])
    assert multi.has_natural_russian_voice()
    multi.shutdown()


def test_multispeaker_falls_back_to_system_without_neural():
    multi = MultiSpeaker([LabelSpeaker("Piper (нейроголоса)", []), NullSpeaker()])
    multi.configure("Нейро: Дмитрий (мужской)", 0, 90)  # selected but not downloaded
    assert isinstance(multi.active, NullSpeaker)
    assert not multi.has_natural_russian_voice()
    multi.shutdown()


@pytest.mark.skipif(not os.environ.get("PIPER_TEST_VOICE"), reason="set PIPER_TEST_VOICE=/path/voice.onnx")
def test_real_piper_synthesis(tmp_path):
    source = Path(os.environ["PIPER_TEST_VOICE"])
    vid = "ru_RU-irina-medium"
    (tmp_path / f"{vid}.onnx").write_bytes(source.read_bytes())
    (tmp_path / f"{vid}.onnx.json").write_bytes(Path(str(source) + ".json").read_bytes())
    player = RecordingPlayer()
    speaker = PiperSpeaker(tmp_path, player=player)
    done = threading.Event()
    speaker.on_end = done.set
    speaker.configure("", 0, 90)
    speaker.speak("Привет! Я Клод, ваш голосовой ассистент.")
    assert done.wait(60)
    total = sum(n for n, _ in player.played)
    assert total > 22050  # more than a second of real audio
    speaker.shutdown()
