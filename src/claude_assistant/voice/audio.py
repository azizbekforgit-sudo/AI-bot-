"""Microphone capture (16 kHz mono PCM16) via PortAudio/sounddevice."""

from __future__ import annotations

import logging
import queue
import time

log = logging.getLogger(__name__)

SAMPLE_RATE = 16000
BLOCK = 1600  # 100 ms


class MicrophoneError(RuntimeError):
    pass


def list_input_devices() -> list[str]:
    try:
        import sounddevice as sd

        devices = sd.query_devices()
    except Exception as exc:  # PortAudio missing or no audio subsystem
        log.info("Audio devices unavailable: %s", exc)
        return []
    names = []
    for dev in devices:
        if dev.get("max_input_channels", 0) > 0 and dev["name"] not in names:
            names.append(dev["name"])
    return names


class MicrophoneSource:
    """Opens the stream on start(); read() returns None while no audio arrives."""

    def __init__(self, device_name: str = "") -> None:
        self.device_name = device_name
        self._queue: queue.Queue[bytes] = queue.Queue(maxsize=200)
        self._stream = None
        self._last_audio = 0.0

    def _device_index(self, sd) -> int | None:
        if not self.device_name:
            return None
        for index, dev in enumerate(sd.query_devices()):
            if dev.get("max_input_channels", 0) > 0 and self.device_name.lower() in dev["name"].lower():
                return index
        raise MicrophoneError(f"Микрофон «{self.device_name}» не найден")

    def start(self) -> None:
        try:
            import sounddevice as sd
        except OSError as exc:
            raise MicrophoneError(f"Аудиосистема недоступна: {exc}") from exc

        def callback(indata, frames, time_info, status) -> None:  # PortAudio thread
            if status:
                log.debug("audio status: %s", status)
            try:
                self._queue.put_nowait(bytes(indata))
            except queue.Full:
                pass  # consumer is behind; dropping audio is better than growing memory

        try:
            self._stream = sd.RawInputStream(samplerate=SAMPLE_RATE, blocksize=BLOCK, dtype="int16", channels=1,
                                             device=self._device_index(sd), callback=callback)
            self._stream.start()
        except MicrophoneError:
            raise
        except Exception as exc:
            raise MicrophoneError(f"Не удалось открыть микрофон: {exc}") from exc
        self._last_audio = time.monotonic()

    def read(self, timeout: float = 0.5) -> bytes | None:
        try:
            data = self._queue.get(timeout=timeout)
        except queue.Empty:
            return None
        self._last_audio = time.monotonic()
        return data

    def healthy(self, stall_s: float = 3.0) -> bool:
        """False when the device vanished (unplugged, sleep/resume) and stopped delivering audio."""
        stream = self._stream
        if stream is None:
            return False
        try:
            active = bool(stream.active)
        except Exception:
            active = False
        return active and time.monotonic() - self._last_audio < stall_s

    def drain(self) -> None:
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break

    def stop(self) -> None:
        stream, self._stream = self._stream, None
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception:
                pass
