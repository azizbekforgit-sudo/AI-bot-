from __future__ import annotations

import logging

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAction, QCloseEvent, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication, QHBoxLayout, QListWidget, QMainWindow, QMenu, QMessageBox, QStackedWidget, QSystemTrayIcon, QWidget,
)

from claude_assistant import APP_NAME
from claude_assistant.config import AppConfig
from claude_assistant.core.controller import Controller
from claude_assistant.integration.hotkeys import HotkeyManager
from claude_assistant.integration.startup import StartupManager
from claude_assistant.ui.assistant_page import AssistantPage
from claude_assistant.ui.hardware_page import HardwarePage
from claude_assistant.ui.memory_page import MemoryPage
from claude_assistant.ui.settings_page import SettingsPage
from claude_assistant.ui.theme import palette, state_color, stylesheet
from claude_assistant.ui.widgets import app_icon

log = logging.getLogger(__name__)


class MainWindow(QMainWindow):
    _invoke = Signal(object)  # thread-safe hop to the GUI thread

    def __init__(self, controller: Controller, hotkeys: HotkeyManager, startup: StartupManager) -> None:
        super().__init__()
        self.c = controller
        self.hotkeys = hotkeys
        self.startup = startup
        self._quitting = False
        self._invoke.connect(lambda fn: fn())
        self._tray_hint_shown = False
        self.pal = palette(controller.config.system.theme)
        QApplication.instance().setStyleSheet(stylesheet(self.pal))

        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(app_icon())
        self.resize(1080, 720)
        self.setMinimumSize(760, 520)

        central = QWidget()
        layout = QHBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.nav = QListWidget(objectName="Sidebar")
        self.nav.setFixedWidth(190)
        self.nav.addItems(["Ассистент", "Железо", "Память", "Настройки"])
        layout.addWidget(self.nav)
        self.stack = QStackedWidget()
        self.assistant = AssistantPage(controller, self.pal)
        self.hardware = HardwarePage(controller)
        self.memory = MemoryPage(controller.memory)
        self.settings = SettingsPage(controller)
        for page in (self.assistant, self.hardware, self.memory, self.settings):
            self.stack.addWidget(page)
        layout.addWidget(self.stack, 1)
        self.setCentralWidget(central)
        self.nav.currentRowChanged.connect(self.stack.setCurrentIndex)
        self.nav.setCurrentRow(0)

        QShortcut(QKeySequence(Qt.Key.Key_Escape), self, activated=self.c.stop_speaking)
        QShortcut(QKeySequence("Ctrl+L"), self, activated=lambda: self.assistant.input.setFocus())

        self.settings.saved.connect(self.apply_settings)
        controller.show_window.connect(self.bring_to_front)
        controller.state_changed.connect(self._on_state)
        self._build_tray()
        self._register_hotkeys()

    # ---- tray ----------------------------------------------------------------
    def _build_tray(self) -> None:
        self.tray: QSystemTrayIcon | None = None
        if not QSystemTrayIcon.isSystemTrayAvailable():
            log.info("System tray unavailable; closing the window will exit")
            return
        self.tray = QSystemTrayIcon(app_icon(), self)
        self.tray.setToolTip(APP_NAME)
        menu = QMenu()
        menu.addAction("Показать", self.bring_to_front)
        self.mute_action = QAction("Выключить микрофон", menu, checkable=True)
        self.mute_action.toggled.connect(self.c.set_muted)
        menu.addAction(self.mute_action)
        menu.addAction("Экстренная остановка", self.c.emergency_stop)
        menu.addAction("Продолжить работу", self.c.resume)
        menu.addSeparator()
        menu.addAction("Выход", self.quit)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(
            lambda reason: self.bring_to_front() if reason in (QSystemTrayIcon.ActivationReason.Trigger,
                                                                 QSystemTrayIcon.ActivationReason.DoubleClick) else None)
        self.c.voice_status.connect(self._sync_tray_mute)
        self.tray.show()

    def _sync_tray_mute(self, status: str, _detail: str) -> None:
        if self.tray and status in ("muted", "idle"):
            self.mute_action.blockSignals(True)
            self.mute_action.setChecked(status == "muted")
            self.mute_action.blockSignals(False)

    def _on_state(self, state: str, detail: str) -> None:
        if self.tray:
            self.tray.setIcon(app_icon(state_color(self.pal, state)))
            self.tray.setToolTip(f"{APP_NAME} — {self.assistant.state_title.text()}")

    def bring_to_front(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._quitting or self.tray is None:
            event.accept()
            QApplication.instance().quit()
            return
        event.ignore()
        self.hide()
        if not self._tray_hint_shown:
            self._tray_hint_shown = True
            self.tray.showMessage(APP_NAME, "Я продолжаю работать в трее. Выход — через меню значка.",
                                  QSystemTrayIcon.MessageIcon.Information, 4000)

    def quit(self) -> None:
        self._quitting = True
        self.close()
        QApplication.instance().quit()

    # ---- settings ----------------------------------------------------------------
    def _register_hotkeys(self) -> None:
        cfg = self.c.config.system
        bindings = {}
        if cfg.hotkey_talk:
            bindings[cfg.hotkey_talk] = self.c.push_to_talk
        if cfg.hotkey_stop:
            bindings[cfg.hotkey_stop] = self.c.emergency_stop
        # Hotkey callbacks run on the hotkey thread; a queued signal moves them to the GUI thread.
        safe = {combo: (lambda fn=fn: self._invoke.emit(fn)) for combo, fn in bindings.items()}
        for error in self.hotkeys.start(safe):
            if self.hotkeys.supported:
                self.c.message.emit("system", error)

    def apply_settings(self, config: AppConfig) -> None:
        old = self.c.config
        self.c.apply_config(config)
        if self.startup.supported and config.system.autostart != self.startup.is_enabled():
            try:
                self.startup.apply(config.system.autostart)
            except OSError as exc:
                QMessageBox.warning(self, "Автозапуск", f"Не удалось изменить автозапуск: {exc}")
        elif not self.startup.supported and config.system.autostart:
            self.c.message.emit("system", "Автозапуск доступен только в Windows.")
        if (config.system.hotkey_talk, config.system.hotkey_stop) != (old.system.hotkey_talk, old.system.hotkey_stop):
            self._register_hotkeys()
        if config.system.theme != old.system.theme:
            self.c.message.emit("system", "Тема применится после перезапуска ассистента.")
        self.c.message.emit("system", "Настройки сохранены.")
        self.assistant.sync_full_access()
        self.settings.load()
