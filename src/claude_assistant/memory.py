"""Explicit, local, user-controlled memory.

Only facts the user deliberately asked to remember (or added in the UI) are
stored. Conversation content and audio are never saved here.
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

from claude_assistant import paths

MAX_ITEMS = 200
MAX_TEXT = 500


@dataclass
class MemoryItem:
    text: str
    category: str = "заметка"
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    created: float = field(default_factory=time.time)


class MemoryStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or paths.memory_path()
        self._lock = threading.Lock()
        self._items: list[MemoryItem] = []
        self.load_error: str | None = None
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            self._items = [
                MemoryItem(
                    text=str(i["text"])[:MAX_TEXT],
                    category=str(i.get("category", "заметка")),
                    id=str(i.get("id") or uuid.uuid4().hex[:12]),
                    created=float(i.get("created", time.time())),
                )
                for i in raw.get("items", [])
                if isinstance(i, dict) and i.get("text")
            ]
        except (OSError, ValueError, TypeError, KeyError) as exc:
            self.load_error = f"Не удалось прочитать память: {exc}"
            broken = self.path.with_suffix(".broken.json")
            try:
                self.path.replace(broken)
            except OSError:
                pass

    def _save(self) -> None:
        tmp = self.path.with_suffix(".json.tmp")
        data = {"version": 1, "items": [asdict(i) for i in self._items]}
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)

    def items(self) -> list[MemoryItem]:
        with self._lock:
            return list(self._items)

    def add(self, text: str, category: str = "заметка") -> MemoryItem:
        text = " ".join(text.split())[:MAX_TEXT]
        if not text:
            raise ValueError("пустая запись")
        with self._lock:
            if len(self._items) >= MAX_ITEMS:
                raise ValueError(f"память заполнена (максимум {MAX_ITEMS} записей)")
            item = MemoryItem(text=text, category=category)
            self._items.append(item)
            self._save()
            return item

    def update(self, item_id: str, text: str) -> None:
        text = " ".join(text.split())[:MAX_TEXT]
        if not text:
            raise ValueError("пустая запись")
        with self._lock:
            for item in self._items:
                if item.id == item_id:
                    item.text = text
                    self._save()
                    return
        raise KeyError(item_id)

    def delete(self, item_id: str) -> bool:
        with self._lock:
            before = len(self._items)
            self._items = [i for i in self._items if i.id != item_id]
            if len(self._items) != before:
                self._save()
                return True
            return False

    def forget_matching(self, phrase: str) -> int:
        phrase = phrase.lower().strip()
        if not phrase:
            return 0
        with self._lock:
            before = len(self._items)
            self._items = [i for i in self._items if phrase not in i.text.lower()]
            removed = before - len(self._items)
            if removed:
                self._save()
            return removed

    def clear(self) -> None:
        with self._lock:
            self._items = []
            self._save()

    def export(self, target: Path) -> None:
        data = {"items": [asdict(i) for i in self.items()]}
        target.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def as_prompt_context(self) -> str:
        items = self.items()
        if not items:
            return ""
        lines = "\n".join(f"- {i.text}" for i in items)
        return f"Сохранённые пользователем предпочтения и заметки:\n{lines}"
