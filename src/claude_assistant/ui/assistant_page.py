"""Main screen: state, microphone/privacy status, conversation, live task steps, command input."""

from __future__ import annotations

import html
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QUrl, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QPushButton, QSplitter,
    QTextBrowser, QVBoxLayout, QWidget,
)

from claude_assistant.brain.permissions import Denial
from claude_assistant.core.controller import Controller
from claude_assistant.tools import apps, files
from claude_assistant.ui.theme import Palette, state_color
from claude_assistant.ui.widgets import StatusOrb

STATE_TITLES = {
    "idle": "Готов", "listening": "Слушаю", "working": "Работаю", "speaking": "Говорю",
    "offline": "Нет связи с Claude", "stopped": "Остановлен",
}


class AssistantPage(QWidget):
    def __init__(self, controller: Controller, pal: Palette) -> None:
        super().__init__()
        self.c = controller
        self.pal = pal
        self._task_items: dict[str, QListWidgetItem] = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(14)

        # --- header: state + controls
        header = QFrame(objectName="Card")
        h = QHBoxLayout(header)
        h.setContentsMargins(16, 12, 16, 12)
        h.setSpacing(14)
        self.orb = StatusOrb()
        h.addWidget(self.orb)
        titles = QVBoxLayout()
        titles.setSpacing(2)
        self.state_title = QLabel("Готов", objectName="StateTitle")
        self.state_detail = QLabel("", objectName="Muted")
        self.state_detail.setWordWrap(True)
        titles.addWidget(self.state_title)
        titles.addWidget(self.state_detail)
        h.addLayout(titles, 1)
        self.mic_chip = QLabel("Микрофон: …", objectName="Chip")
        self.mic_chip.setToolTip("Аудио распознаётся локально и никуда не отправляется. В Claude уходит только текст команды.")
        h.addWidget(self.mic_chip, 0, Qt.AlignmentFlag.AlignVCenter)
        self.mute_btn = QPushButton("Выключить микрофон")
        self.mute_btn.setCheckable(True)
        self.mute_btn.toggled.connect(self._toggle_mute)
        h.addWidget(self.mute_btn)
        self.hush_btn = QPushButton("Замолчать")
        self.hush_btn.setToolTip("Остановить озвучку (Esc)")
        self.hush_btn.clicked.connect(self.c.stop_speaking)
        h.addWidget(self.hush_btn)
        self.stop_btn = QPushButton("СТОП", objectName="Danger")
        self.stop_btn.setToolTip("Экстренная остановка: отменить все задачи и запретить новые действия")
        self.stop_btn.clicked.connect(self._stop_or_resume)
        h.addWidget(self.stop_btn)
        root.addWidget(header)

        # --- approval banner
        self.banner = QFrame(objectName="Banner")
        b = QVBoxLayout(self.banner)
        b.setContentsMargins(16, 12, 16, 12)
        self.banner_text = QLabel()
        self.banner_text.setWordWrap(True)
        self.banner_text.setTextFormat(Qt.TextFormat.RichText)
        b.addWidget(self.banner_text)
        row = QHBoxLayout()
        row.addStretch(1)
        reject = QPushButton("Отклонить")
        reject.clicked.connect(self.c.reject_pending)
        approve = QPushButton("Разрешить", objectName="Primary")
        approve.clicked.connect(self.c.approve_pending)
        row.addWidget(reject)
        row.addWidget(approve)
        b.addLayout(row)
        self.banner.hide()
        root.addWidget(self.banner)

        # --- conversation | task steps
        split = QSplitter(Qt.Orientation.Horizontal)
        self.chat = QTextBrowser()
        self.chat.setOpenLinks(False)
        self.chat.anchorClicked.connect(self._open_link)
        self.chat.setPlaceholderText("Скажите «Клод» или напишите команду ниже.")
        split.addWidget(self.chat)
        tasks = QWidget()
        t = QVBoxLayout(tasks)
        t.setContentsMargins(0, 0, 0, 0)
        trow = QHBoxLayout()
        self.task_title = QLabel("Шаги задачи", objectName="CardTitle")
        trow.addWidget(self.task_title, 1)
        self.cancel_btn = QPushButton("Отменить задачу")
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self.c.cancel_task)
        trow.addWidget(self.cancel_btn)
        t.addLayout(trow)
        self.task_list = QListWidget()
        self.task_list.setWordWrap(True)
        t.addWidget(self.task_list)
        split.addWidget(tasks)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        root.addWidget(split, 1)

        # --- input
        irow = QHBoxLayout()
        self.input = QLineEdit(objectName="CommandInput")
        self.input.setPlaceholderText("Напишите команду и нажмите Enter…")
        self.input.returnPressed.connect(self._send)
        irow.addWidget(self.input, 1)
        self.talk_btn = QPushButton("Говорить")
        self.talk_btn.setToolTip("Начать слушать команду без кодового слова")
        self.talk_btn.clicked.connect(self.c.push_to_talk)
        irow.addWidget(self.talk_btn)
        send = QPushButton("Отправить", objectName="Primary")
        send.clicked.connect(self._send)
        irow.addWidget(send)
        new = QPushButton("Новый разговор")
        new.clicked.connect(self.c.new_conversation)
        irow.addWidget(new)
        root.addLayout(irow)

        c = self.c
        c.state_changed.connect(self.on_state)
        c.message.connect(self.add_message)
        c.task_started.connect(self._task_started)
        c.task_event.connect(self._task_event)
        c.task_finished.connect(self._task_finished)
        c.approval_needed.connect(self._approval)
        c.voice_status.connect(self.on_voice_status)
        c.search_results.connect(self._results)
        self.on_state(c.state, c.detail)
        if c.voice is None:
            self.on_voice_status("muted", "Голос выключен в настройках")

    # ---- slots
    def on_state(self, state: str, detail: str) -> None:
        self.orb.set_state(state, state_color(self.pal, state))
        self.state_title.setText(STATE_TITLES.get(state, state))
        self.state_detail.setText(detail)
        self.stop_btn.setText("Продолжить" if state == "stopped" else "СТОП")
        self.stop_btn.setObjectName("Primary" if state == "stopped" else "Danger")
        self.stop_btn.style().unpolish(self.stop_btn)
        self.stop_btn.style().polish(self.stop_btn)
        self.hush_btn.setEnabled(state == "speaking")

    def on_voice_status(self, status: str, detail: str) -> None:
        names = {
            "idle": ("ChipLive", "Микрофон: ждёт кодовое слово"),
            "listening": ("ChipLive", "Микрофон: записывает команду"),
            "muted": ("ChipOff", "Микрофон выключен"),
            "unavailable": ("ChipOff", "Голос недоступен"),
            "error": ("ChipOff", "Микрофон: ошибка"),
        }
        obj, text = names.get(status, ("Chip", status))
        self.mic_chip.setObjectName(obj)
        self.mic_chip.setText(text)
        self.mic_chip.setToolTip(detail + "\n\nАудио распознаётся локально и никуда не отправляется.")
        self.mic_chip.style().unpolish(self.mic_chip)
        self.mic_chip.style().polish(self.mic_chip)
        self.talk_btn.setEnabled(status not in ("unavailable", "muted"))
        muted = status == "muted"
        if status in ("muted", "idle") and muted != self.mute_btn.isChecked():
            self.mute_btn.blockSignals(True)
            self.mute_btn.setChecked(muted)
            self.mute_btn.setText("Включить микрофон" if muted else "Выключить микрофон")
            self.mute_btn.blockSignals(False)
        if status in ("unavailable", "error"):
            self.add_message("system", detail)
        if status == "listening" and self.c.state != "listening":
            self.on_state("listening", detail)

    def add_message(self, role: str, text: str) -> None:
        color = {"user": self.pal.text, "assistant": self.pal.text, "system": self.pal.muted,
                 "error": self.pal.danger}.get(role, self.pal.text)
        who = {"user": "Вы", "assistant": "Клод", "system": "Система", "error": "Ошибка"}.get(role, role)
        body = html.escape(text).replace("\n", "<br>")
        stamp = datetime.now().strftime("%H:%M")
        weight = "600" if role == "user" else "400"
        self.chat.append(
            f'<div style="margin:6px 0"><span style="color:{self.pal.muted};font-size:8pt">{who} · {stamp}</span><br>'
            f'<span style="color:{color};font-weight:{weight}">{body}</span></div>'
        )
        self.chat.verticalScrollBar().setValue(self.chat.verticalScrollBar().maximum())

    def _results(self, results: list[str]) -> None:
        if not results:
            return
        links = "<br>".join(
            f'{i}. <a href="{QUrl.fromLocalFile(p).toString()}" style="color:{self.pal.accent}">{html.escape(p)}</a>'
            for i, p in enumerate(results, start=1)
        )
        self.chat.append(f'<div style="margin:2px 0 8px 0">{links}</div>')

    def _open_link(self, url: QUrl) -> None:
        if not url.isLocalFile():
            return
        path = Path(url.toLocalFile())
        if not files.is_within(path, self.c.search_roots()) and not str(path).startswith(str(Path.home() / "Pictures")):
            self.add_message("error", "Этот путь вне разрешённых папок.")
            return
        result = apps.open_path(path)
        if not result.ok:
            self.add_message("error", result.message)

    def _task_started(self, text: str) -> None:
        self.task_list.clear()
        self._task_items.clear()
        self.task_title.setText("Шаги задачи — выполняется")
        self.cancel_btn.setEnabled(True)
        self._add_step("▶", text[:160], self.pal.muted)

    def _add_step(self, mark: str, text: str, color: str, key: str | None = None) -> None:
        item = QListWidgetItem(f"{mark}  {text}")
        item.setForeground(QColor(color))
        self.task_list.addItem(item)
        self.task_list.scrollToBottom()
        if key:
            self._task_items[key] = item

    def _task_event(self, event: dict) -> None:
        kind = event.get("kind")
        if kind == "tool_use":
            self._add_step("…", event["text"], self.pal.text, key=event.get("id"))
        elif kind == "tool_result":
            item = self._task_items.get(event.get("id") or "")
            if item is not None:
                ok = event.get("ok", True)
                text = item.text()[3:]
                item.setText(f"{'✓' if ok else '✗'}  {text}" + ("" if ok else f" — {event.get('text', '')}"))
                item.setForeground(QColor(self.pal.success if ok else self.pal.danger))
        elif kind == "status":
            self._add_step("⟳", event["text"], self.pal.warning)
        elif kind == "init":
            self._add_step("·", event["text"], self.pal.muted)
        elif kind == "text":
            self._add_step("💬", event["text"][:200], self.pal.muted)

    def _task_finished(self, ok: bool, error: str) -> None:
        self.cancel_btn.setEnabled(False)
        self.task_title.setText("Шаги задачи — готово" if ok else "Шаги задачи — остановлено")
        if not ok and error:
            self._add_step("✗", error, self.pal.danger)

    def _approval(self, denials: list[Denial]) -> None:
        if not denials:
            self.banner.hide()
            return
        rows = []
        for d in denials:
            color = self.pal.danger if d.risk == "high" else self.pal.text
            rows.append(f'<div style="margin:4px 0"><b style="color:{color}">{html.escape(d.summary)}</b><br>'
                        f'<span style="color:{self.pal.muted}">{html.escape(d.reason)}</span></div>')
        self.banner_text.setText("<b>Claude просит разрешения:</b>" + "".join(rows))
        self.banner.show()

    def _send(self) -> None:
        text = self.input.text().strip()
        if text:
            self.input.clear()
            self.c.submit(text, source="text")

    def _toggle_mute(self, muted: bool) -> None:
        self.mute_btn.setText("Включить микрофон" if muted else "Выключить микрофон")
        self.c.set_muted(muted)

    def _stop_or_resume(self) -> None:
        if self.c.stopped:
            self.c.resume()
        else:
            self.c.emergency_stop()
