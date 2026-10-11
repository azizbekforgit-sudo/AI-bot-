"""Text-to-speech: Windows SAPI voices (offline), espeak-ng on Linux, or silent fallback."""

from __future__ import annotations

import logging
import queue
import shutil
import subprocess
import sys
import threading
from typing import Callable

log = logging.getLogger(__name__)


class Speaker:
    """Speaks on its own thread. stop() interrupts immediately."""

    name = "Без озвучки"

    def __init__(self) -> None:
        self.on_start: Callable[[], None] = lambda: None
        self.on_end: Callable[[], None] = lambda: None
        self._queue: queue.Queue[str | None] = queue.Queue()
        self._stop = threading.Event()
        self._speaking = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="tts")
        self._thread.start()

    @property
    def available(self) -> bool:
        return True

    @property
    def speaking(self) -> bool:
        return self._speaking.is_set()

    def voices(self) -> list[str]:
        return []

    def configure(self, voice: str, rate: int, volume: int) -> None:
        pass

    def speak(self, text: str) -> None:
        if text.strip():
            self._stop.clear()
            self._queue.put(text)

    def stop(self) -> None:
        self._stop.set()
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break
        self._interrupt()

    def shutdown(self) -> None:
        self.stop()
        self._queue.put(None)

    # -- implementation hooks --
    def _init_thread(self) -> None:
        pass

    def _say(self, text: str) -> None:
        pass

    def _interrupt(self) -> None:
        pass

    def _loop(self) -> None:
        try:
            self._init_thread()
        except Exception as exc:
            log.error("TTS init failed: %s", exc)
        while True:
            text = self._queue.get()
            if text is None:
                return
            if self._stop.is_set():
                continue
            self._speaking.set()
            try:
                self.on_start()
                self._say(text)
            except Exception as exc:
                log.warning("TTS failed: %s", exc)
            finally:
                self._speaking.clear()
                if self._queue.empty():
                    self.on_end()


class NullSpeaker(Speaker):
    pass


class SapiSpeaker(Speaker):
    name = "Windows SAPI"
    _ASYNC, _PURGE = 1, 2

    def __init__(self) -> None:
        self._voice = None
        self._settings = ("", 1, 90)
        self._pending_settings = True
        self._voice_names: list[str] = []
        self._ready = threading.Event()
        super().__init__()
        self._ready.wait(5)

    def _init_thread(self) -> None:
        import pythoncom
        import win32com.client

        pythoncom.CoInitialize()
        self._voice = win32com.client.Dispatch("SAPI.SpVoice")
        tokens = self._voice.GetVoices()
        self._voice_names = [tokens.Item(i).GetDescription() for i in range(tokens.Count)]
        self._ready.set()

    @property
    def available(self) -> bool:
        return self._voice is not None

    def voices(self) -> list[str]:
        return list(self._voice_names)

    def configure(self, voice: str, rate: int, volume: int) -> None:
        self._settings = (voice, rate, volume)
        self._pending_settings = True

    def _apply_settings(self) -> None:
        voice, rate, volume = self._settings
        tokens = self._voice.GetVoices()
        chosen = None
        for i in range(tokens.Count):
            token = tokens.Item(i)
            desc = token.GetDescription()
            if voice and voice.lower() in desc.lower():
                chosen = token
                break
            # default: first Russian voice (LCID 0x419), e.g. "Microsoft Irina"
            if not voice and chosen is None and "419" in str(token.GetAttribute("Language")).split(";"):
                chosen = token
        if chosen is not None:
            self._voice.Voice = chosen
        self._voice.Rate = max(-10, min(10, rate))
        self._voice.Volume = max(0, min(100, volume))
        self._pending_settings = False

    def has_russian_voice(self) -> bool:
        return any(w in n.lower() for n in self._voice_names for w in ("irina", "pavel", "russian", "русск"))

    def _say(self, text: str) -> None:
        if self._pending_settings:
            self._apply_settings()
        self._voice.Speak(text, self._ASYNC | self._PURGE)
        while not self._voice.WaitUntilDone(100):
            if self._stop.is_set():
                self._voice.Speak("", self._ASYNC | self._PURGE)
                return

    def _interrupt(self) -> None:
        pass  # _say polls the stop flag every 100 ms; COM objects must stay on their own thread


class EspeakSpeaker(Speaker):
    name = "espeak-ng"

    def __init__(self, binary: str) -> None:
        self._binary = binary
        self._proc: subprocess.Popen | None = None
        self._rate = 175
        self._volume = 90
        self._voice = "ru"
        super().__init__()

    def configure(self, voice: str, rate: int, volume: int) -> None:
        self._voice = voice or "ru"
        self._rate = 175 + rate * 12
        self._volume = volume

    def _say(self, text: str) -> None:
        self._proc = subprocess.Popen([self._binary, "-v", self._voice, "-s", str(self._rate),
                                       "-a", str(int(self._volume * 2)), "--", text],
                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self._proc.wait()

    def _interrupt(self) -> None:
        proc = self._proc
        if proc and proc.poll() is None:
            proc.terminate()


def create_speaker() -> Speaker:
    if sys.platform == "win32":
        try:
            speaker = SapiSpeaker()
            if speaker.available:
                return speaker
            speaker.shutdown()
        except Exception as exc:
            log.warning("SAPI unavailable: %s", exc)
    binary = shutil.which("espeak-ng") or shutil.which("espeak")
    if binary:
        return EspeakSpeaker(binary)
    return NullSpeaker()
