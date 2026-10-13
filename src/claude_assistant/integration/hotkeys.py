"""System-wide hotkeys via the Win32 RegisterHotKey API.

RegisterHotKey only receives the exact key combinations registered — it is not
a keyboard hook and cannot see any other typing.
"""

from __future__ import annotations

import logging
import sys
import threading
from typing import Callable

log = logging.getLogger(__name__)

_MODS = {"alt": 0x1, "ctrl": 0x2, "shift": 0x4, "win": 0x8}
_MOD_NOREPEAT = 0x4000
_KEYS = {"space": 0x20, "enter": 0x0D, "tab": 0x09, "esc": 0x1B, "backspace": 0x08, "pause": 0x13}
_WM_HOTKEY, _WM_QUIT = 0x0312, 0x0012


def parse_hotkey(text: str) -> tuple[int, int]:
    parts = [p for p in text.lower().replace(" ", "").split("+") if p]
    if len(parts) < 2:
        raise ValueError(f"Нужна комбинация с модификатором: {text!r}")
    mods = 0
    for part in parts[:-1]:
        if part not in _MODS:
            raise ValueError(f"Неизвестный модификатор {part!r}")
        mods |= _MODS[part]
    key = parts[-1]
    if key in _KEYS:
        vk = _KEYS[key]
    elif len(key) == 1 and key.isalnum():
        vk = ord(key.upper())
    elif key.startswith("f") and key[1:].isdigit() and 1 <= int(key[1:]) <= 12:
        vk = 0x70 + int(key[1:]) - 1
    else:
        raise ValueError(f"Неизвестная клавиша {key!r}")
    return mods, vk


class HotkeyManager:
    """Registers hotkeys on a dedicated thread with its own Win32 message loop."""

    def __init__(self) -> None:
        self._thread: threading.Thread | None = None
        self._thread_id = 0
        self.errors: list[str] = []

    @property
    def supported(self) -> bool:
        return sys.platform == "win32"

    def start(self, bindings: dict[str, Callable[[], None]]) -> list[str]:
        """bindings: {"ctrl+alt+space": callback}. Returns human-readable errors."""
        self.stop()
        self.errors = []
        if not self.supported:
            self.errors.append("Глобальные горячие клавиши поддерживаются только в Windows.")
            return self.errors
        parsed = []
        for combo, callback in bindings.items():
            if not combo:
                continue
            try:
                parsed.append((combo, *parse_hotkey(combo), callback))
            except ValueError as exc:
                self.errors.append(str(exc))
        ready = threading.Event()
        self._thread = threading.Thread(target=self._loop, args=(parsed, ready), daemon=True, name="hotkeys")
        self._thread.start()
        ready.wait(2)
        return self.errors

    def _loop(self, parsed, ready: threading.Event) -> None:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        self._thread_id = kernel32.GetCurrentThreadId()
        callbacks = {}
        for index, (combo, mods, vk, callback) in enumerate(parsed, start=1):
            if user32.RegisterHotKey(None, index, mods | _MOD_NOREPEAT, vk):
                callbacks[index] = callback
            else:
                self.errors.append(f"Сочетание {combo} уже занято другой программой.")
        ready.set()
        msg = wintypes.MSG()
        try:
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                if msg.message == _WM_HOTKEY and msg.wParam in callbacks:
                    try:
                        callbacks[msg.wParam]()
                    except Exception:
                        log.exception("Hotkey callback failed")
        finally:
            for index in callbacks:
                user32.UnregisterHotKey(None, index)

    def stop(self) -> None:
        if self._thread and self._thread.is_alive() and self._thread_id:
            import ctypes

            ctypes.windll.user32.PostThreadMessageW(self._thread_id, _WM_QUIT, 0, 0)
            self._thread.join(2)
        self._thread = None
        self._thread_id = 0
