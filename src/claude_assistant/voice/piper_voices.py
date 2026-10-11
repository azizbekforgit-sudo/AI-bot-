"""Offline neural Russian voices (Piper): catalogue, download and playback.

Each voice is an ONNX model (~60 MB) synthesised locally on the CPU; nothing is
sent over the network while speaking.
"""

from __future__ import annotations

import logging
import threading
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

from claude_assistant import paths
from claude_assistant.voice.tts import Speaker

log = logging.getLogger(__name__)

_HF = "https://huggingface.co/rhasspy/piper-voices/resolve/main/ru/ru_RU"


@dataclass(frozen=True)
class VoiceInfo:
    id: str
    label: str
    urls: tuple[str, ...]  # base URLs without the .onnx / .onnx.json suffix, tried in order


CATALOG: tuple[VoiceInfo, ...] = (
    VoiceInfo("ru_RU-dmitri-medium", "Нейро: Дмитрий (мужской)", (f"{_HF}/dmitri/medium/ru_RU-dmitri-medium",)),
    VoiceInfo("ru_RU-denis-medium", "Нейро: Денис (мужской)", (f"{_HF}/denis/medium/ru_RU-denis-medium",)),
    VoiceInfo("ru_RU-ruslan-medium", "Нейро: Руслан (мужской)", (f"{_HF}/ruslan/medium/ru_RU-ruslan-medium",)),
    VoiceInfo("ru_RU-irina-medium", "Нейро: Ирина (женский)", (
        f"{_HF}/irina/medium/ru_RU-irina-medium",
        # Mirror of an earlier Irina release on GitHub, used if Hugging Face is unreachable.
        "https://github.com/rhasspy/piper/releases/download/v0.0.2/voice-ru-irinia-medium.tar.gz#ru-irinia-medium",
    )),
)


def voices_dir() -> Path:
    path = paths.models_dir() / "piper"
    path.mkdir(parents=True, exist_ok=True)
    return path


def piper_available() -> bool:
    import importlib.util

    try:
        return importlib.util.find_spec("piper") is not None
    except (ImportError, ValueError):
        return False


def installed(directory: Path | None = None) -> list[VoiceInfo]:
    directory = directory or voices_dir()
    return [v for v in CATALOG if (directory / f"{v.id}.onnx").is_file() and (directory / f"{v.id}.onnx.json").is_file()]


# ---- download --------------------------------------------------------------------------

def _fetch(url: str, target: Path, progress: Callable[[int, int], None] | None) -> None:
    part = target.with_suffix(target.suffix + ".part")
    with urllib.request.urlopen(url, timeout=60) as response, part.open("wb") as out:
        total = int(response.headers.get("Content-Length") or 0)
        done = 0
        while chunk := response.read(1 << 16):
            out.write(chunk)
            done += len(chunk)
            if progress:
                progress(done, total)
    part.replace(target)


def _fetch_from_tarball(url: str, voice: VoiceInfo, directory: Path) -> None:
    import tarfile
    import tempfile

    archive_url, _, inner = url.partition("#")
    with tempfile.TemporaryDirectory() as tmp:
        archive = Path(tmp) / "voice.tar.gz"
        _fetch(archive_url, archive, None)
        with tarfile.open(archive) as tar:
            for suffix in (".onnx", ".onnx.json"):
                member = tar.getmember(f"{inner}{suffix}")
                if not member.isfile():
                    raise RuntimeError(f"Неожиданный файл в архиве: {member.name}")
                source = tar.extractfile(member)
                assert source is not None
                (directory / f"{voice.id}{suffix}").write_bytes(source.read())


def download(voice: VoiceInfo, directory: Path | None = None,
             progress: Callable[[int, int], None] | None = None) -> Path:
    """Download one voice; tries each source in turn. Returns the .onnx path."""
    directory = directory or voices_dir()
    errors = []
    for url in voice.urls:
        try:
            if "#" in url:
                _fetch_from_tarball(url, voice, directory)
            else:
                _fetch(url + ".onnx.json", directory / f"{voice.id}.onnx.json", None)
                _fetch(url + ".onnx", directory / f"{voice.id}.onnx", progress)
            return directory / f"{voice.id}.onnx"
        except Exception as exc:
            errors.append(f"{url.split('/')[2]}: {exc}")
            log.warning("Voice %s download failed from %s: %s", voice.id, url, exc)
    raise RuntimeError("; ".join(errors))


# ---- playback --------------------------------------------------------------------------

class Player(Protocol):
    def play(self, samples, sample_rate: int, stop: threading.Event) -> None: ...


class SoundDevicePlayer:
    """Writes audio in ~50 ms blocks so stop() takes effect almost immediately."""

    def play(self, samples, sample_rate: int, stop: threading.Event) -> None:
        import sounddevice as sd

        block = max(256, sample_rate // 20)
        with sd.OutputStream(samplerate=sample_rate, channels=1, dtype="int16") as stream:
            for start in range(0, len(samples), block):
                if stop.is_set():
                    stream.abort()
                    return
                stream.write(samples[start:start + block])


class PiperSpeaker(Speaker):
    name = "Piper (нейроголоса)"

    def __init__(self, directory: Path | None = None, player: Player | None = None, loader=None) -> None:
        self.directory = directory or voices_dir()
        self.player = player or SoundDevicePlayer()
        self._loader = loader or _load_piper_voice
        self._voice_id: str | None = None
        self._loaded: tuple[str, object] | None = None
        self._length_scale = 1.0
        self._volume = 0.9
        super().__init__()

    def voices(self) -> list[str]:
        return [v.label for v in installed(self.directory)]

    def owns(self, label: str) -> bool:
        return label in self.voices()

    def configure(self, voice: str, rate: int, volume: int) -> None:
        available = installed(self.directory)
        chosen = next((v for v in available if voice and voice in (v.label, v.id)), None)
        if chosen is None and available:
            chosen = available[0]
        self._voice_id = chosen.id if chosen else None
        # Piper speeds up when length_scale shrinks: rate +10 → 0.6, rate -10 → 1.4.
        self._length_scale = max(0.5, min(1.6, 1.0 - rate * 0.04))
        self._volume = max(0.0, min(1.0, volume / 100))

    def _say(self, text: str) -> None:
        if self._voice_id is None:
            self.configure("", 0, int(self._volume * 100))
            if self._voice_id is None:
                raise RuntimeError("нейроголоса не установлены")
        if self._loaded is None or self._loaded[0] != self._voice_id:
            self._loaded = None  # free the previous model before loading another (~60 MB each)
            self._loaded = (self._voice_id, self._loader(self.directory / f"{self._voice_id}.onnx"))
        voice = self._loaded[1]
        from piper import SynthesisConfig

        config = SynthesisConfig(length_scale=self._length_scale, volume=self._volume)
        for chunk in voice.synthesize(text, config):  # one chunk per sentence
            if self._stop.is_set():
                return
            self.player.play(chunk.audio_int16_array, chunk.sample_rate, self._stop)


def _load_piper_voice(path: Path):
    from piper import PiperVoice

    return PiperVoice.load(str(path))
