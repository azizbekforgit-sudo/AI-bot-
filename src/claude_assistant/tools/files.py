"""File search restricted to permitted folders."""

from __future__ import annotations

import os
import time
from pathlib import Path

from claude_assistant.tools.base import ToolResult

_SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "AppData", "$Recycle.Bin", ".cache"}


def is_within(path: Path, roots: list[Path]) -> bool:
    try:
        resolved = path.resolve()
    except OSError:
        return False
    for root in roots:
        try:
            resolved.relative_to(root.resolve())
            return True
        except (ValueError, OSError):
            continue
    return False


def search(query: str, roots: list[Path], limit: int = 10, time_budget_s: float = 4.0,
           max_entries: int = 200_000) -> ToolResult:
    """Case-insensitive name search. Bounded by result count, time and entries visited."""
    words = [w for w in query.lower().split() if w]
    if not words:
        return ToolResult(False, "Не указано, что искать.")
    roots = [r for r in roots if r.is_dir()]
    if not roots:
        return ToolResult(False, "Не настроены папки для поиска (Настройки → Папки для поиска).")

    deadline = time.monotonic() + time_budget_s
    matches: list[tuple[int, Path]] = []
    visited = 0
    truncated = False
    for root in roots:
        for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
            dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS and not d.startswith(".")]
            for name in dirnames + filenames:
                visited += 1
                lower = name.lower()
                if all(w in lower for w in words):
                    full = Path(dirpath) / name
                    # Prefer exact-ish names, then directories (projects), then shorter paths.
                    score = (0 if lower == " ".join(words) else 1) * 10 + (0 if name in dirnames else 1)
                    matches.append((score * 1000 + len(str(full)), full))
            if visited > max_entries or time.monotonic() > deadline:
                truncated = True
                break
        if truncated:
            break
    matches.sort(key=lambda m: m[0])
    found = [p for _, p in matches[:limit]]
    if not found:
        note = " (поиск прерван по времени)" if truncated else ""
        return ToolResult(True, f"Ничего не нашёл по запросу «{query}»{note}.", verified=True, data={"paths": []})
    head = ", ".join(p.name for p in found[:3])
    more = f" и ещё {len(found) - 3}" if len(found) > 3 else ""
    return ToolResult(True, f"Нашёл: {head}{more}. Полный список на экране.", verified=True,
                      data={"paths": [str(p) for p in found], "truncated": truncated})


def read_text(path: Path, roots: list[Path], max_bytes: int = 200_000) -> ToolResult:
    if not is_within(path, roots):
        return ToolResult(False, "Файл вне разрешённых папок.")
    if not path.is_file():
        return ToolResult(False, f"Файл не найден: {path}")
    if path.stat().st_size > max_bytes:
        return ToolResult(False, "Файл слишком большой для чтения целиком.")
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        try:
            text = path.read_text(encoding="cp1251")
        except UnicodeDecodeError:
            return ToolResult(False, "Это не текстовый файл.")
    return ToolResult(True, f"Прочитал {path.name}.", verified=True, data={"text": text})
