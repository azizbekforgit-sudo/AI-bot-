"""View, edit, export and delete what the assistant remembers."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFileDialog, QHBoxLayout, QInputDialog, QLabel, QListWidget, QListWidgetItem, QMessageBox, QPushButton,
    QVBoxLayout, QWidget,
)

from claude_assistant.memory import MemoryStore


class MemoryPage(QWidget):
    def __init__(self, memory: MemoryStore) -> None:
        super().__init__()
        self.memory = memory
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(12)
        info = QLabel(
            "Здесь только то, что вы явно попросили запомнить («Клод, запомни, что…») или добавили сами. "
            "Разговоры и звук не сохраняются. Эти записи передаются Claude в начале каждого нового разговора.\n"
            f"Файл: {memory.path}",
            objectName="Muted",
        )
        info.setWordWrap(True)
        info.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        root.addWidget(info)
        self.list = QListWidget()
        self.list.setWordWrap(True)
        self.list.itemDoubleClicked.connect(lambda _: self._edit())
        root.addWidget(self.list, 1)
        row = QHBoxLayout()
        for text, slot in (("Добавить", self._add), ("Изменить", self._edit), ("Удалить", self._delete),
                           ("Экспорт…", self._export)):
            btn = QPushButton(text)
            btn.clicked.connect(slot)
            row.addWidget(btn)
        row.addStretch(1)
        clear = QPushButton("Удалить всё")
        clear.clicked.connect(self._clear)
        row.addWidget(clear)
        root.addLayout(row)
        if memory.load_error:
            QMessageBox.warning(self, "Память", memory.load_error)

    def showEvent(self, event) -> None:
        self.refresh()
        super().showEvent(event)

    def refresh(self) -> None:
        self.list.clear()
        for item in self.memory.items():
            row = QListWidgetItem(f"{item.text}\n{datetime.fromtimestamp(item.created):%d.%m.%Y %H:%M}")
            row.setData(Qt.ItemDataRole.UserRole, item.id)
            self.list.addItem(row)

    def _selected(self) -> tuple[str, str] | None:
        row = self.list.currentItem()
        if row is None:
            return None
        return row.data(Qt.ItemDataRole.UserRole), row.text().split("\n")[0]

    def _add(self) -> None:
        text, ok = QInputDialog.getText(self, "Новая запись", "Что запомнить?")
        if ok and text.strip():
            try:
                self.memory.add(text)
            except ValueError as exc:
                QMessageBox.warning(self, "Память", str(exc))
            self.refresh()

    def _edit(self) -> None:
        sel = self._selected()
        if not sel:
            return
        text, ok = QInputDialog.getText(self, "Изменить запись", "Текст:", text=sel[1])
        if ok and text.strip():
            self.memory.update(sel[0], text)
            self.refresh()

    def _delete(self) -> None:
        sel = self._selected()
        if sel:
            self.memory.delete(sel[0])
            self.refresh()

    def _export(self) -> None:
        target, _ = QFileDialog.getSaveFileName(self, "Экспорт памяти", str(Path.home() / "claude-memory.json"),
                                                "JSON (*.json)")
        if target:
            self.memory.export(Path(target))

    def _clear(self) -> None:
        if QMessageBox.question(self, "Удалить всё", "Удалить все сохранённые записи?") == QMessageBox.StandardButton.Yes:
            self.memory.clear()
            self.refresh()
