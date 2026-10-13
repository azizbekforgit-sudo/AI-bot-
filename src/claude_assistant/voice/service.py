"""Voice loop: wait for the wake word (or clap / hotkey), then capture one command.

Runs on a background thread and reports through callbacks. The microphone is
ignored while the assistant speaks so it never answers its own voice.
"""

from __future__ import annotations

import collections
import logging
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

from claude_assistant.config import VoiceConfig
from claude_assistant.voice.audio import BLOCK, SAMPLE_RATE, MicrophoneError, MicrophoneSource
from claude_assistant.voice.clap import ClapDetector
from claude_assistant.voice.recognizer import Recognizer, VoskEngine
from claude_assistant.voice.wake import WakeMatcher

log = logging.getLogger(__name__)

PRE_ROLL_CHUNKS = 5  # 0.5 s of audio kept so a command spoken right after the wake word is not cut
ECHO_GUARD_S = 0.5


class AudioSource(Protocol):
    def start(self) -> None: ...
    def read(self, timeout: float = 0.5) -> bytes | None: ...
    def healthy(self, stall_s: float = 3.0) -> bool: ...
    def drain(self) -> None: ...
    def stop(self) -> None: ...


class Engine(Protocol):
    def wake_recognizer(self, phrases: list[str]) -> tuple[Recognizer, bool]: ...
    def command_recognizer(self) -> Recognizer: ...


@dataclass
class VoiceCallbacks:
    on_status: Callable[[str, str], None]  # (status, detail): idle|listening|muted|unavailable|error
    on_wake: Callable[[str], None]  # activation source: wake|clap|hotkey
    on_command: Callable[[str], None]
    on_no_speech: Callable[[], None]


class VoiceService:
    def __init__(self, config: VoiceConfig, model_dir: Path, callbacks: VoiceCallbacks,
                 source_factory: Callable[[str], AudioSource] | None = None,
                 engine_factory: Callable[[Path], Engine] | None = None) -> None:
        self.config = config
        self.model_dir = model_dir
        self.cb = callbacks
        self._source_factory = source_factory or MicrophoneSource
        self._engine_factory = engine_factory or VoskEngine
        self._thread: threading.Thread | None = None
        self._running = threading.Event()
        self._muted = threading.Event()
        self._tts_active = threading.Event()
        self._activate = threading.Event()
        self._cancel_command = threading.Event()
        self._reconfigure = threading.Event()
        self._echo_until = 0.0
        self.status = "idle"
        self.detail = ""

    # ---- public API (any thread) ---------------------------------------
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._running.set()
        self._thread = threading.Thread(target=self._run, daemon=True, name="voice")
        self._thread.start()

    def stop(self, timeout: float = 3.0) -> None:
        self._running.clear()
        if self._thread:
            self._thread.join(timeout)
        self._thread = None

    def set_muted(self, muted: bool) -> None:
        if muted:
            self._muted.set()
            self._set_status("muted", "Микрофон выключен")
        else:
            self._muted.clear()
            self._set_status("idle", self._idle_detail())

    @property
    def muted(self) -> bool:
        return self._muted.is_set()

    def activate(self) -> None:
        """Push-to-talk / hotkey: skip the wake word and listen for a command now."""
        self._activate.set()

    def cancel_listening(self) -> None:
        self._cancel_command.set()

    def set_speaking(self, speaking: bool) -> None:
        if speaking:
            self._tts_active.set()
        else:
            self._echo_until = time.monotonic() + ECHO_GUARD_S
            self._tts_active.clear()

    def update_config(self, config: VoiceConfig) -> None:
        self.config = config
        self._reconfigure.set()

    # ---- internals -------------------------------------------------------
    def _set_status(self, status: str, detail: str = "") -> None:
        if (status, detail) != (self.status, self.detail):
            self.status, self.detail = status, detail
            self.cb.on_status(status, detail)

    def _idle_detail(self) -> str:
        if self.config.wake_word_enabled:
            return f"Жду кодовое слово «{self.config.wake_words[0]}»"
        return "Кодовое слово выключено — используйте горячую клавишу"

    def _run(self) -> None:
        try:
            engine = self._engine_factory(self.model_dir)
        except FileNotFoundError as exc:
            self._set_status("unavailable", f"{exc}. Запустите scripts\\setup.ps1, чтобы скачать модель.")
            return
        except Exception as exc:
            log.exception("Speech engine failed to load")
            self._set_status("unavailable", f"Распознавание речи недоступно: {exc}")
            return

        while self._running.is_set():
            source = self._source_factory(self.config.input_device)
            try:
                source.start()
            except MicrophoneError as exc:
                self._set_status("error", f"{exc}. Повторная попытка через 3 с…")
                self._sleep(3.0)
                continue
            try:
                self._listen(engine, source)
            except Exception:
                log.exception("Voice loop crashed; restarting")
                self._set_status("error", "Сбой голосового модуля, перезапуск…")
                self._sleep(2.0)
            finally:
                source.stop()

    def _sleep(self, seconds: float) -> None:
        end = time.monotonic() + seconds
        while self._running.is_set() and time.monotonic() < end:
            time.sleep(0.1)

    def _listen(self, engine: Engine, source: AudioSource) -> None:
        self._reconfigure.clear()
        matcher = WakeMatcher(self.config.wake_words, self.config.wake_match_threshold)
        wake_rec, grammar_mode = engine.wake_recognizer(matcher.phrases)
        clap = ClapDetector(self.config.clap_threshold) if self.config.clap_enabled else None
        pre_roll: collections.deque[bytes] = collections.deque(maxlen=PRE_ROLL_CHUNKS)
        if not self._muted.is_set():
            self._set_status("idle", self._idle_detail())

        while self._running.is_set():
            if self._reconfigure.is_set():
                return  # rebuild recognizers with the new settings
            chunk = source.read(timeout=0.5)
            if chunk is None:
                if not source.healthy():
                    self._set_status("error", "Микрофон перестал отвечать (отключён?). Переподключаюсь…")
                    return
                continue
            if self._muted.is_set() or self._tts_active.is_set() or time.monotonic() < self._echo_until:
                wake_rec.reset()
                pre_roll.clear()
                if self._muted.is_set():
                    self._activate.clear()
                continue

            source_name = None
            if self._activate.is_set():
                self._activate.clear()
                source_name = "hotkey"
            elif clap is not None and clap.process(chunk):
                source_name = "clap"
            elif self.config.wake_word_enabled:
                pre_roll.append(chunk)
                final = wake_rec.accept(chunk)
                heard = final if final is not None else wake_rec.partial()
                end = matcher.find(heard) if heard else None
                if end is not None:
                    trailing = " ".join(heard.split()[end:]).strip() if not grammar_mode else ""
                    wake_rec.reset()
                    if final is not None and trailing:
                        # Full-recognition mode: "клод открой блокнот" arrived as one utterance.
                        self.cb.on_wake("wake")
                        self.cb.on_command(trailing)
                        pre_roll.clear()
                        continue
                    source_name = "wake"
            if source_name is None:
                continue

            self.cb.on_wake(source_name)
            preload = list(pre_roll) if source_name == "wake" else []
            pre_roll.clear()
            text = self._capture_command(engine, source, matcher, preload)
            wake_rec.reset()
            if text:
                self.cb.on_command(text)
            elif text is not None:
                self.cb.on_no_speech()
            if not self._muted.is_set():
                self._set_status("idle", self._idle_detail())

    def _capture_command(self, engine: Engine, source: AudioSource, matcher: WakeMatcher,
                         preload: list[bytes]) -> str | None:
        """Text of the command, '' when nothing was said, None when cancelled."""
        self._cancel_command.clear()
        self._set_status("listening", "Слушаю…")
        rec = engine.command_recognizer()
        for chunk in preload:
            rec.accept(chunk)
        started = time.monotonic()
        heard_speech = False
        parts: list[str] = []
        while self._running.is_set():
            if self._cancel_command.is_set() or self._muted.is_set():
                return None
            chunk = source.read(timeout=0.5)
            elapsed = time.monotonic() - started
            if chunk is None:
                if not source.healthy():
                    return None
            else:
                final = rec.accept(chunk)
                if final is not None:
                    cleaned = matcher.strip(final)
                    if cleaned:
                        parts.append(cleaned)
                        return " ".join(parts)
                if not heard_speech and matcher.strip(rec.partial()):
                    heard_speech = True
            if not heard_speech and elapsed > self.config.silence_timeout_s:
                return ""
            if elapsed > self.config.command_timeout_s:
                return matcher.strip(rec.flush())
        return None


def chunk_seconds() -> float:
    return BLOCK / SAMPLE_RATE
