"""Settings: brain (Claude Code), autonomy & workspaces, voice, system integration, privacy."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QMessageBox, QPlainTextEdit, QPushButton, QScrollArea, QSlider, QSpinBox, QVBoxLayout, QWidget,
)
from pydantic import ValidationError

from claude_assistant.brain.claude_code import ClaudeStatus
from claude_assistant.config import AppConfig
from claude_assistant.core.controller import Controller
from claude_assistant.voice.audio import list_input_devices

AUTONOMY = [
    ("assisted", "С подтверждением", "Claude только читает и предлагает план; выполняет после вашего «Разрешить»."),
    ("balanced", "Сбалансированный", "Сам правит файлы в рабочих папках, запускает тесты и git status; "
                                     "остальное — с вашего разрешения."),
    ("advanced", "Расширенный", "Дополнительно запускает python/node-скрипты, git commit и сборку. Удаление, "
                               "установка программ и сеть всё равно только с разрешения."),
    ("full", "Разрешать всё", "Выполняет любые команды без вопросов. Страж всё равно блокирует опасное: отключение "
                             "антивируса и брандмауэра, админ-права, системный реестр, пароли и ключи, удаление диска."),
]

MODELS = [
    ("opus", "Opus 5.5 — самый умный"),
    ("sonnet", "Sonnet — быстрее, экономнее"),
    ("haiku", "Haiku — самый быстрый и дешёвый"),
    ("", "По умолчанию для подписки"),
]

EFFORTS = [
    ("low", "Низкий — быстрые ответы, меньше токенов (рекомендуется)"),
    ("medium", "Средний"),
    ("high", "Высокий — думает дольше"),
    ("xhigh", "Очень высокий"),
    ("max", "Максимальный — медленно и затратно"),
]


def _label(text: str) -> QLabel:
    label = QLabel(text, objectName="Muted")
    label.setWordWrap(True)
    return label


def confirm_full_access(parent: QWidget) -> bool:
    answer = QMessageBox.question(
        parent, "Разрешать всё",
        "Claude будет выполнять любые команды и менять файлы без вопросов.\n\n"
        "Всегда запрещено (даже в этом режиме): отключение антивируса и брандмауэра, права администратора, "
        "системный реестр, чтение паролей и ключей, форматирование и удаление диска.\n\n"
        "Остановить всё можно кнопкой «СТОП» или Ctrl+Alt+X. Включить?")
    return answer == QMessageBox.StandardButton.Yes


class PathList(QWidget):
    def __init__(self, title: str) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.list = QListWidget()
        self.list.setMaximumHeight(110)
        layout.addWidget(self.list)
        row = QHBoxLayout()
        add = QPushButton("Добавить папку…")
        add.clicked.connect(lambda: self._add(title))
        remove = QPushButton("Убрать")
        remove.clicked.connect(lambda: self.list.takeItem(self.list.currentRow()))
        row.addWidget(add)
        row.addWidget(remove)
        row.addStretch(1)
        layout.addLayout(row)

    def _add(self, title: str) -> None:
        folder = QFileDialog.getExistingDirectory(self, title)
        if folder and folder not in self.values():
            self.list.addItem(folder)

    def values(self) -> list[str]:
        return [self.list.item(i).text() for i in range(self.list.count())]

    def set_values(self, values: list[str]) -> None:
        self.list.clear()
        self.list.addItems(values)


class SettingsPage(QWidget):
    saved = Signal(object)  # AppConfig

    def __init__(self, controller: Controller) -> None:
        super().__init__()
        self.c = controller
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        outer.addWidget(scroll)
        body = QWidget()
        scroll.setWidget(body)
        root = QVBoxLayout(body)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(6)

        # --- general
        g = QGroupBox("Общие")
        f = QFormLayout(g)
        self.user_name = QLineEdit()
        self.user_name.setPlaceholderText("Как к вам обращаться")
        f.addRow("Ваше имя", self.user_name)
        self.theme = QComboBox()
        self.theme.addItem("Тёмная", "dark")
        self.theme.addItem("Светлая", "light")
        f.addRow("Тема", self.theme)
        self.autostart = QCheckBox("Запускать при входе в Windows")
        f.addRow(self.autostart)
        self.start_minimized = QCheckBox("Запускаться свёрнутым в трей")
        f.addRow(self.start_minimized)
        f.addRow(_label("Автозапуск использует стандартный раздел «Автозагрузка» вашей учётной записи, без прав "
                        "администратора. Его видно и можно отключить в Диспетчере задач → Автозагрузка."))
        root.addWidget(g)

        # --- brain
        g = QGroupBox("Мозг — Claude Code")
        f = QFormLayout(g)
        self.claude_state = _label("Проверка…")
        f.addRow("Статус", self.claude_state)
        row = QHBoxLayout()
        self.claude_path = QLineEdit()
        self.claude_path.setPlaceholderText("Определяется автоматически")
        browse = QPushButton("Обзор…")
        browse.clicked.connect(self._browse_claude)
        check = QPushButton("Проверить")
        check.clicked.connect(self._check_claude)
        row.addWidget(self.claude_path, 1)
        row.addWidget(browse)
        row.addWidget(check)
        f.addRow("Путь к claude", row)
        self.model = QComboBox()
        self.model.setEditable(True)
        for key, title in MODELS:
            self.model.addItem(title, key)
        self.model.setToolTip("Можно вписать точное имя модели, например claude-opus-5-5")
        f.addRow("Модель", self.model)
        self.effort = QComboBox()
        for key, title in EFFORTS:
            self.effort.addItem(title, key)
        f.addRow("Сколько думать", self.effort)
        self.lean = QCheckBox("Быстрый режим: без MCP-серверов и навыков, только нужные инструменты")
        f.addRow(self.lean)
        f.addRow(_label("Opus тратит лимит подписки быстрее других моделей. Низкий уровень размышлений и быстрый "
                        "режим заметно ускоряют ответы и экономят лимит. Если Opus перегружен, ответит Sonnet."))
        self.use_subscription = QCheckBox("Использовать подписку Claude Pro (игнорировать ANTHROPIC_API_KEY)")
        f.addRow(self.use_subscription)
        f.addRow(_label("Если отключить, а в системе задан ANTHROPIC_API_KEY, Claude Code будет списывать деньги "
                        "с API-аккаунта по тарифам API. С подпиской запросы входят в её лимиты."))
        self.autonomy = QComboBox()
        for key, title, _ in AUTONOMY:
            self.autonomy.addItem(title, key)
        f.addRow("Автономия", self.autonomy)
        self.autonomy_hint = _label("")
        f.addRow(self.autonomy_hint)
        self.autonomy.currentIndexChanged.connect(
            lambda i: self.autonomy_hint.setText(AUTONOMY[i][2] if 0 <= i < len(AUTONOMY) else ""))
        self.workspaces = PathList("Рабочая папка")
        f.addRow("Рабочие папки", self.workspaces)
        f.addRow(_label("Claude может изменять файлы только в рабочих папках. Первая папка — текущая для команд. "
                        "Если список пуст, используется ~/ClaudeAssistant."))
        self.max_tools = QSpinBox()
        self.max_tools.setRange(1, 200)
        f.addRow("Максимум шагов на задачу", self.max_tools)
        self.timeout = QSpinBox()
        self.timeout.setRange(30, 3600)
        self.timeout.setSuffix(" с")
        f.addRow("Лимит времени на задачу", self.timeout)
        self.extra_rules = QLineEdit()
        self.extra_rules.setPlaceholderText("Например: Bash(npm run dev), PowerShell(Get-Service *)")
        f.addRow("Доп. разрешения", self.extra_rules)
        root.addWidget(g)

        # --- voice
        g = QGroupBox("Голос")
        f = QFormLayout(g)
        self.voice_enabled = QCheckBox("Голосовое управление")
        f.addRow(self.voice_enabled)
        self.wake_enabled = QCheckBox("Реагировать на кодовое слово")
        f.addRow(self.wake_enabled)
        self.wake_words = QLineEdit()
        self.wake_words.setPlaceholderText("клод")
        f.addRow("Кодовые слова (через запятую)", self.wake_words)
        self.mic = QComboBox()
        self.mic.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        f.addRow("Микрофон", self.mic)
        self.silence = QDoubleSpinBox()
        self.silence.setRange(1, 20)
        self.silence.setSuffix(" с")
        f.addRow("Ждать начала команды", self.silence)
        self.cmd_timeout = QDoubleSpinBox()
        self.cmd_timeout.setRange(2, 60)
        self.cmd_timeout.setSuffix(" с")
        f.addRow("Максимальная длина команды", self.cmd_timeout)
        self.activation_sound = QCheckBox("Звуковой сигнал, когда начинаю слушать")
        f.addRow(self.activation_sound)
        self.clap = QCheckBox("Активация двойным хлопком (экспериментально)")
        f.addRow(self.clap)
        self.clap_threshold = QSlider(Qt.Orientation.Horizontal)
        self.clap_threshold.setRange(5, 100)
        f.addRow("Порог хлопка (выше — строже)", self.clap_threshold)
        f.addRow(_label("Хлопок определяется по громкому короткому звуку и может срабатывать на стук. "
                        "Надёжные способы — кодовое слово и горячая клавиша."))
        self.tts = QCheckBox("Озвучивать ответы на голосовые команды")
        f.addRow(self.tts)
        self.speak_typed = QCheckBox("Озвучивать и ответы на текстовые команды")
        f.addRow(self.speak_typed)
        self.jarvis = QCheckBox("Эффект JARVIS: голос ниже и с лёгким эхом (для нейроголосов)")
        f.addRow(self.jarvis)
        self.voice_name = QComboBox()
        self.voice_name.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        f.addRow("Голос", self.voice_name)
        self.rate = QSlider(Qt.Orientation.Horizontal)
        self.rate.setRange(-10, 10)
        f.addRow("Скорость речи", self.rate)
        self.volume = QSlider(Qt.Orientation.Horizontal)
        self.volume.setRange(0, 100)
        f.addRow("Громкость", self.volume)
        self.max_spoken = QSpinBox()
        self.max_spoken.setRange(50, 5000)
        self.max_spoken.setSuffix(" символов")
        f.addRow("Максимум озвучки", self.max_spoken)
        row = QHBoxLayout()
        test = QPushButton("Прослушать")
        test.setToolTip("Произнести фразу выбранным голосом (без сохранения настроек)")
        test.clicked.connect(self._preview_voice)
        row.addWidget(test)
        self.download_voices_btn = QPushButton("Скачать нейроголоса")
        self.download_voices_btn.setToolTip("4 естественных русских голоса Piper (~60 МБ каждый), работают без интернета")
        self.download_voices_btn.clicked.connect(self._download_voices)
        row.addWidget(self.download_voices_btn)
        row.addStretch(1)
        f.addRow(row)
        self.voices_hint = _label("")
        f.addRow(self.voices_hint)
        root.addWidget(g)

        # --- system
        g = QGroupBox("Система и приватность")
        f = QFormLayout(g)
        self.hotkey_talk = QLineEdit()
        f.addRow("Клавиша «говорить»", self.hotkey_talk)
        self.hotkey_stop = QLineEdit()
        f.addRow("Клавиша экстренной остановки", self.hotkey_stop)
        self.screenshots = QCheckBox("Разрешить скриншоты по команде")
        f.addRow(self.screenshots)
        self.log_conv = QCheckBox("Записывать текст команд в лог (для отладки)")
        f.addRow(self.log_conv)
        self.interval = QDoubleSpinBox()
        self.interval.setRange(1, 60)
        self.interval.setSuffix(" с")
        f.addRow("Обновление мониторинга", self.interval)
        self.search_roots = PathList("Папка для поиска")
        f.addRow("Папки для поиска файлов", self.search_roots)
        f.addRow(_label(
            "Что уходит в интернет: текст ваших команд и содержимое файлов, которые Claude читает для задачи, "
            "отправляются в Anthropic через Claude Code. Звук с микрофона распознаётся на компьютере и никуда "
            "не отправляется. Скриншоты сохраняются только локально."))
        root.addWidget(g)

        # --- shortcuts
        g = QGroupBox("Программы и сайты для быстрых команд")
        f = QFormLayout(g)
        self.apps = QPlainTextEdit()
        self.apps.setPlaceholderText("блокнот = notepad.exe")
        self.apps.setMinimumHeight(120)
        f.addRow("Программы (название = команда)", self.apps)
        self.sites = QPlainTextEdit()
        self.sites.setMinimumHeight(100)
        f.addRow("Сайты (название = адрес)", self.sites)
        root.addWidget(g)

        row = QHBoxLayout()
        row.addStretch(1)
        reset = QPushButton("Отменить изменения")
        reset.clicked.connect(self.load)
        save = QPushButton("Сохранить", objectName="Primary")
        save.clicked.connect(self._save)
        row.addWidget(reset)
        row.addWidget(save)
        root.addLayout(row)
        root.addStretch(1)

        self.c.claude_status.connect(self._on_claude_status)
        self.c.voices_changed.connect(self.on_voices_downloaded)
        self.c.config_applied.connect(self.load)
        self.load()

    # ---- load / save
    def load(self) -> None:
        cfg = self.c.config
        self.user_name.setText(cfg.user_name)
        self.theme.setCurrentIndex(max(0, self.theme.findData(cfg.system.theme)))
        self.autostart.setChecked(cfg.system.autostart)
        self.start_minimized.setChecked(cfg.system.start_minimized)
        b = cfg.brain
        self.claude_path.setText(b.claude_path)
        index = self.model.findData(b.model)
        if index < 0:
            self.model.addItem(b.model, b.model)
            index = self.model.count() - 1
        self.model.setCurrentIndex(index)
        self.effort.setCurrentIndex(max(0, self.effort.findData(b.effort)))
        self.lean.setChecked(b.lean_mode)
        self.use_subscription.setChecked(b.use_subscription)
        self.autonomy.setCurrentIndex(max(0, self.autonomy.findData(b.autonomy)))
        self.autonomy_hint.setText(AUTONOMY[self.autonomy.currentIndex()][2])
        self.workspaces.set_values(b.workspaces)
        self.max_tools.setValue(b.max_tool_calls)
        self.timeout.setValue(b.timeout_s)
        self.extra_rules.setText(", ".join(b.extra_allowed_tools))
        v = cfg.voice
        self.voice_enabled.setChecked(v.enabled)
        self.wake_enabled.setChecked(v.wake_word_enabled)
        self.wake_words.setText(", ".join(v.wake_words))
        self.mic.clear()
        self.mic.addItem("Микрофон по умолчанию", "")
        for name in list_input_devices():
            self.mic.addItem(name, name)
        if v.input_device and self.mic.findData(v.input_device) < 0:
            self.mic.addItem(f"{v.input_device} (не подключён)", v.input_device)
        self.mic.setCurrentIndex(max(0, self.mic.findData(v.input_device)))
        self.silence.setValue(v.silence_timeout_s)
        self.cmd_timeout.setValue(v.command_timeout_s)
        self.activation_sound.setChecked(v.activation_sound)
        self.clap.setChecked(v.clap_enabled)
        self.clap_threshold.setValue(int(v.clap_threshold * 100))
        self.tts.setChecked(v.tts_enabled)
        self.speak_typed.setChecked(v.speak_typed_replies)
        self.jarvis.setChecked(v.jarvis_effect)
        self._fill_voices(v.tts_voice)
        self.rate.setValue(v.tts_rate)
        self.volume.setValue(v.tts_volume)
        self.max_spoken.setValue(v.max_spoken_chars)
        s = cfg.system
        self.hotkey_talk.setText(s.hotkey_talk)
        self.hotkey_stop.setText(s.hotkey_stop)
        self.screenshots.setChecked(s.allow_screenshots)
        self.log_conv.setChecked(s.log_conversation)
        self.interval.setValue(s.monitor_interval_s)
        self.search_roots.set_values(s.search_roots)
        self.apps.setPlainText("\n".join(f"{k} = {val}" for k, val in cfg.apps.items()))
        self.sites.setPlainText("\n".join(f"{k} = {val}" for k, val in cfg.sites.items()))

    def _model_value(self) -> str:
        text = self.model.currentText().strip()
        for key, title in MODELS:
            if text == title:
                return key
        return text

    @staticmethod
    def _pairs(text: str) -> dict[str, str]:
        result = {}
        for line in text.splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                if key.strip() and value.strip():
                    result[key.strip().lower()] = value.strip()
        return result

    def build_config(self) -> AppConfig:
        data = self.c.config.model_dump()
        data["user_name"] = self.user_name.text().strip()
        data["brain"].update(
            claude_path=self.claude_path.text().strip(),
            model=self._model_value(),
            effort=self.effort.currentData(),
            lean_mode=self.lean.isChecked(),
            use_subscription=self.use_subscription.isChecked(),
            autonomy=self.autonomy.currentData(),
            workspaces=self.workspaces.values(),
            max_tool_calls=self.max_tools.value(),
            timeout_s=self.timeout.value(),
            extra_allowed_tools=[r.strip() for r in self.extra_rules.text().split(",") if r.strip()],
        )
        data["voice"].update(
            enabled=self.voice_enabled.isChecked(),
            wake_word_enabled=self.wake_enabled.isChecked(),
            wake_words=[w.strip() for w in self.wake_words.text().split(",") if w.strip()],
            input_device=self.mic.currentData() or "",
            silence_timeout_s=self.silence.value(),
            command_timeout_s=self.cmd_timeout.value(),
            activation_sound=self.activation_sound.isChecked(),
            clap_enabled=self.clap.isChecked(),
            clap_threshold=self.clap_threshold.value() / 100,
            tts_enabled=self.tts.isChecked(),
            speak_typed_replies=self.speak_typed.isChecked(),
            jarvis_effect=self.jarvis.isChecked(),
            tts_voice=self.voice_name.currentData() or "",
            tts_rate=self.rate.value(),
            tts_volume=self.volume.value(),
            max_spoken_chars=self.max_spoken.value(),
        )
        data["system"].update(
            autostart=self.autostart.isChecked(),
            start_minimized=self.start_minimized.isChecked(),
            theme=self.theme.currentData(),
            hotkey_talk=self.hotkey_talk.text(),
            hotkey_stop=self.hotkey_stop.text(),
            allow_screenshots=self.screenshots.isChecked(),
            log_conversation=self.log_conv.isChecked(),
            monitor_interval_s=self.interval.value(),
            search_roots=self.search_roots.values(),
        )
        data["apps"] = self._pairs(self.apps.toPlainText())
        data["sites"] = self._pairs(self.sites.toPlainText())
        return AppConfig.model_validate(data)

    def _save(self) -> None:
        try:
            config = self.build_config()
        except ValidationError as exc:
            lines = [f"• {'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()]
            QMessageBox.warning(self, "Неверные настройки", "Исправьте:\n" + "\n".join(lines))
            return
        if config.brain.autonomy == "full" and self.c.config.brain.autonomy != "full":
            if not confirm_full_access(self):
                return
        elif config.brain.autonomy == "advanced" and self.c.config.brain.autonomy != "advanced":
            answer = QMessageBox.question(
                self, "Расширенная автономия",
                "В этом режиме Claude сможет без вопросов запускать python- и node-скрипты в рабочих папках. "
                "Опасные действия (удаление, установка, сеть) по-прежнему требуют разрешения. Включить?")
            if answer != QMessageBox.StandardButton.Yes:
                return
        self.saved.emit(config)

    # ---- voices
    def _fill_voices(self, selected: str) -> None:
        self.voice_name.clear()
        self.voice_name.addItem("Автоматически (лучший доступный русский голос)", "")
        voices = self.c.speaker.voices()
        for name in voices:
            self.voice_name.addItem(name, name)
        if selected and selected not in voices:
            self.voice_name.addItem(f"{selected} (не установлен)", selected)
        self.voice_name.setCurrentIndex(max(0, self.voice_name.findData(selected)))
        neural = [n for n in voices if n.startswith("Нейро")]
        self.voices_hint.setText(
            f"Установлено нейроголосов: {len(neural)} из 4. Переключать голос можно и командой «Клод, смени голос»."
            if neural else "Нейроголоса не установлены — нажмите «Скачать нейроголоса» (нужен интернет один раз).")

    def _preview_voice(self) -> None:
        self.c.speaker.stop()
        self.c.speaker.configure(self.voice_name.currentData() or "", self.rate.value(), self.volume.value())
        if hasattr(self.c.speaker, "set_effect"):
            self.c.speaker.set_effect(self.jarvis.isChecked())
        self.c.speaker.speak("Привет! Я Клод, ваш голосовой ассистент. Чем могу помочь?")

    def _download_voices(self) -> None:
        self.download_voices_btn.setEnabled(False)
        self.download_voices_btn.setText("Скачиваю…")
        self.c.download_voices()

    def on_voices_downloaded(self, ok: bool) -> None:
        self.download_voices_btn.setEnabled(True)
        self.download_voices_btn.setText("Скачать нейроголоса")
        self._fill_voices(self.voice_name.currentData() or self.c.config.voice.tts_voice)

    # ---- claude status
    def _browse_claude(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Где находится claude?", "", "Программы (*.exe *.cmd);;Все файлы (*)")
        if path:
            self.claude_path.setText(path)

    def _check_claude(self) -> None:
        self.claude_state.setText("Проверка…")
        self.c.config.brain.claude_path = self.claude_path.text().strip()
        self.c.recheck_claude()

    def _on_claude_status(self, status: ClaudeStatus) -> None:
        if status.available:
            self.claude_state.setText(f"✓ Найден: {status.version} ({status.path}). Вход в аккаунт проверяется при "
                                      "первом запросе.")
        else:
            self.claude_state.setText(f"✗ {status.message}")
