"""Start at sign-in via the per-user Run key (HKCU\\...\\Run).

No administrator rights are needed, the entry is visible and can be turned
off in Task Manager → Startup apps or Settings → Apps → Startup, and it only
runs after the user signs in.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Protocol

from claude_assistant import APP_ID

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


class RegistryBackend(Protocol):
    def get(self, name: str) -> str | None: ...
    def set(self, name: str, value: str) -> None: ...
    def delete(self, name: str) -> None: ...


class WinRegistry:
    def get(self, name: str) -> str | None:
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
                return str(winreg.QueryValueEx(key, name)[0])
        except FileNotFoundError:
            return None

    def set(self, name: str, value: str) -> None:
        import winreg

        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)

    def delete(self, name: str) -> None:
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
                winreg.DeleteValue(key, name)
        except FileNotFoundError:
            pass


def launch_command() -> str:
    """Command line that starts this installation minimized, without a console window."""
    if getattr(sys, "frozen", False):  # PyInstaller build
        return f'"{sys.executable}" --minimized'
    exe = Path(sys.executable)
    pythonw = exe.with_name("pythonw.exe")
    if pythonw.exists():
        exe = pythonw
    return f'"{exe}" -m claude_assistant --minimized'


class StartupManager:
    def __init__(self, backend: RegistryBackend | None = None, name: str = APP_ID) -> None:
        if backend is None and sys.platform == "win32":
            backend = WinRegistry()
        self.backend = backend
        self.name = name

    @property
    def supported(self) -> bool:
        return self.backend is not None

    def is_enabled(self) -> bool:
        return bool(self.backend and self.backend.get(self.name))

    def enable(self) -> None:
        if not self.backend:
            raise RuntimeError("Автозапуск поддерживается только в Windows")
        self.backend.set(self.name, launch_command())

    def disable(self) -> None:
        if self.backend:
            self.backend.delete(self.name)

    def apply(self, enabled: bool) -> None:
        if enabled:
            self.enable()
        else:
            self.disable()
