"""Open and close user-configured applications, and verify the result."""

from __future__ import annotations

import logging
import os
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path

import psutil

from claude_assistant.tools.base import ToolResult

log = logging.getLogger(__name__)

# Processes that must never be closed by voice command.
PROTECTED = {
    "explorer.exe", "csrss.exe", "winlogon.exe", "lsass.exe", "services.exe", "svchost.exe",
    "smss.exe", "wininit.exe", "dwm.exe", "system", "msmpeng.exe", "securityhealthservice.exe",
}

# Launchers that hand off to another process (window lives under a different name).
_PROCESS_NAMES = {
    "code": ["code.exe", "code"],
    "calc.exe": ["calculatorapp.exe", "calculator.exe", "calc.exe"],
    "wt.exe": ["windowsterminal.exe", "wt.exe"],
}


def _split(command: str) -> list[str]:
    return shlex.split(command, posix=sys.platform != "win32")


def _process_names(command: str) -> list[str]:
    exe = Path(_split(command)[0]).name.lower()
    names = _PROCESS_NAMES.get(exe, [])
    stem = exe[:-4] if exe.endswith(".exe") else exe
    return list(dict.fromkeys(names + [exe, f"{stem}.exe", stem]))


def _running(names: list[str]) -> list[psutil.Process]:
    wanted = {n.lower() for n in names}
    found = []
    for proc in psutil.process_iter(["name"]):
        try:
            if (proc.info["name"] or "").lower() in wanted:
                found.append(proc)
        except psutil.Error:
            continue
    return found


def _alive(proc: psutil.Process) -> bool:
    try:
        return proc.is_running() and proc.status() != psutil.STATUS_ZOMBIE
    except psutil.Error:
        return False


def resolve_alias(spoken: str, apps: dict[str, str]) -> tuple[str, str] | None:
    spoken = spoken.lower().strip(" .,!?")
    if spoken in apps:
        return spoken, apps[spoken]
    for alias in sorted(apps, key=len, reverse=True):
        if alias in spoken:
            return alias, apps[alias]
    return None


def open_app(alias: str, command: str, verify_timeout: float = 4.0) -> ToolResult:
    try:
        argv = _split(command)
    except ValueError as exc:
        return ToolResult(False, f"Неверная команда запуска для «{alias}»: {exc}")
    if not argv:
        return ToolResult(False, f"Для «{alias}» не задана команда запуска.")
    exe = shutil.which(argv[0]) or argv[0]
    if not shutil.which(argv[0]) and not Path(argv[0]).exists():
        return ToolResult(False, f"Не нашёл программу «{alias}» ({argv[0]}). Проверьте путь в настройках.")

    names = _process_names(command)
    before = {p.pid for p in _running(names)}
    try:
        if sys.platform == "win32" and exe.lower().endswith((".cmd", ".bat")):
            # e.g. VS Code's `code.cmd`; arguments come from the user's own config only.
            proc = subprocess.Popen(["cmd.exe", "/c", exe, *argv[1:]], creationflags=subprocess.CREATE_NO_WINDOW,
                                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            kwargs = {"creationflags": subprocess.DETACHED_PROCESS} if sys.platform == "win32" else {"start_new_session": True}
            proc = subprocess.Popen([exe, *argv[1:]], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL, **kwargs)
    except OSError as exc:
        return ToolResult(False, f"Не удалось запустить «{alias}»: {exc}")

    def crashed() -> ToolResult | None:
        if proc.poll() not in (None, 0):
            return ToolResult(False, f"Программа «{alias}» завершилась с кодом {proc.returncode}.", verified=True)
        return None

    deadline = time.monotonic() + verify_timeout
    while time.monotonic() < deadline:
        if failure := crashed():
            return failure
        new = [p for p in _running(names) if p.pid not in before]
        if new:
            # A program that crashes on start is briefly visible too: confirm it survives.
            time.sleep(0.7)
            if failure := crashed():
                return failure
            alive = [p for p in new if _alive(p)]
            if alive:
                return ToolResult(True, f"Открыл {alias}.", verified=True, data={"pid": alive[0].pid})
        time.sleep(0.25)
    if proc.poll() is None:
        # The launched process is still alive after the verification window.
        return ToolResult(True, f"Открыл {alias}.", verified=True, data={"pid": proc.pid})
    if before:
        return ToolResult(True, f"{alias.capitalize()} уже был открыт — переключитесь на его окно.", verified=True)
    return ToolResult(True, f"Запустил {alias}, но не смог подтвердить, что окно открылось.", verified=False)


def close_app(alias: str, command: str, timeout: float = 5.0) -> ToolResult:
    names = [n for n in _process_names(command) if n.lower() not in PROTECTED]
    procs = _running(names)
    if not procs:
        return ToolResult(True, f"{alias.capitalize()} не запущен.", verified=True)
    for proc in procs:
        try:
            proc.terminate()  # polite close; never force-kill user apps
        except psutil.Error:
            pass
    _, alive = psutil.wait_procs(procs, timeout=timeout)
    if alive:
        return ToolResult(False, f"{alias.capitalize()} не закрылся — возможно, есть несохранённые изменения.", verified=True)
    return ToolResult(True, f"Закрыл {alias}.", verified=True)


def open_path(path: Path) -> ToolResult:
    if not path.exists():
        return ToolResult(False, f"Путь не найден: {path}")
    try:
        if sys.platform == "win32":
            os.startfile(str(path))  # type: ignore[attr-defined]
        else:
            subprocess.Popen(["xdg-open", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as exc:
        return ToolResult(False, f"Не удалось открыть {path}: {exc}")
    return ToolResult(True, f"Открыл {path.name}.", verified=False)
