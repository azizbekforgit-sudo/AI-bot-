"""Orchestrates voice, local tools, the Claude Code brain, speech output and UI state."""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, Signal, Slot
from PySide6.QtGui import QGuiApplication

from claude_assistant import paths
from claude_assistant.brain.claude_code import BrainEvent, BrainRequest, BrainResult, ClaudeCodeBrain, ClaudeStatus
from claude_assistant.brain.permissions import Denial
from claude_assistant.config import AppConfig, effective_workspaces, save_config
from claude_assistant.core.intents import HELP_TEXT, Intent, parse
from claude_assistant.memory import MemoryStore
from claude_assistant.monitor import hardware
from claude_assistant.monitor.hardware import HardwareMonitor
from claude_assistant.tools import apps, clock, files, web
from claude_assistant.tools.base import ToolResult
from claude_assistant.voice.service import VoiceCallbacks, VoiceService
from claude_assistant.voice.sounds import play_activation
from claude_assistant.voice.speech_text import clean_for_speech
from claude_assistant.voice.tts import NullSpeaker, Speaker

log = logging.getLogger(__name__)

# Assistant states shown in the UI.
OFFLINE, IDLE, LISTENING, WORKING, SPEAKING, STOPPED = "offline", "idle", "listening", "working", "speaking", "stopped"


class Controller(QObject):
    state_changed = Signal(str, str)  # state, detail
    message = Signal(str, str)  # role (user|assistant|system|error), text
    task_event = Signal(object)  # dict: kind, text, id, ok
    task_started = Signal(str)
    task_finished = Signal(bool, str)
    approval_needed = Signal(object)  # list[Denial]
    voice_status = Signal(str, str)
    snapshot_ready = Signal(object)
    claude_status = Signal(object)  # ClaudeStatus
    show_window = Signal()
    search_results = Signal(object)  # list[str]
    voices_changed = Signal(bool)

    _voice_command = Signal(str)
    _local_done = Signal(object, str)  # ToolResult, source
    _brain_done = Signal(object, str)  # BrainResult, source
    _speech_state = Signal(bool)
    _wake = Signal(str)
    _voices_ready = Signal(bool)

    def __init__(self, config: AppConfig, memory: MemoryStore, speaker: Speaker | None = None,
                 brain: ClaudeCodeBrain | None = None, monitor: HardwareMonitor | None = None,
                 enable_voice: bool = True) -> None:
        super().__init__()
        self.config = config
        self.memory = memory
        self.speaker = speaker or NullSpeaker()
        self.brain = brain or ClaudeCodeBrain(config.brain.claude_path, config.brain.use_subscription)
        self._monitor = monitor
        self._pool = ThreadPoolExecutor(max_workers=3, thread_name_prefix="assistant")
        self._cancel = threading.Event()
        self._busy = False
        self._stopped = False
        self._snapshot_pending = False
        self.session_id: str | None = None
        self._last_activity = 0.0
        self._pending: tuple[list[Denial], str] | None = None
        self._last_results: list[str] = []
        self.state, self.detail = IDLE, ""
        self.claude: ClaudeStatus | None = None

        self._voice_command.connect(self._on_voice_command)
        self._local_done.connect(self._on_local_done)
        self._brain_done.connect(self._on_brain_done)
        self._speech_state.connect(self._on_speech_state)
        self._wake.connect(self._on_wake_main)
        self._voices_ready.connect(self._on_voices_ready)
        self.speaker.on_start = lambda: self._speech_state.emit(True)
        self.speaker.on_end = lambda: self._speech_state.emit(False)
        self.speaker.configure(config.voice.tts_voice, config.voice.tts_rate, config.voice.tts_volume)

        self.voice: VoiceService | None = None
        if enable_voice and config.voice.enabled:
            self.voice = self._make_voice()

    # ---- lifecycle -------------------------------------------------------
    def start(self) -> None:
        if self.voice:
            self.voice.start()
        self._pool.submit(self._check_claude)

    def shutdown(self) -> None:
        self._cancel.set()
        self.brain.cancel()
        self.speaker.shutdown()
        if self.voice:
            self.voice.stop()
        self._pool.shutdown(wait=False, cancel_futures=True)
        if self._monitor:
            self._monitor.close()

    def _make_voice(self) -> VoiceService:
        model = Path(self.config.voice.model_path) if self.config.voice.model_path else paths.default_vosk_model_dir()
        return VoiceService(self.config.voice, model, VoiceCallbacks(
            on_status=lambda s, d: self.voice_status.emit(s, d),
            on_wake=self._on_wake,
            on_command=lambda text: self._voice_command.emit(text),
            on_no_speech=lambda: self._voice_command.emit(""),
        ))

    def _check_claude(self) -> None:
        status = self.brain.check(refresh=True)
        self.claude = status
        self.claude_status.emit(status)
        if not status.available:
            self._set_state(OFFLINE, status.message)
        elif self.state == OFFLINE:
            self._set_state(IDLE)

    def recheck_claude(self) -> None:
        self.brain.claude_path = self.config.brain.claude_path
        self.brain.use_subscription = self.config.brain.use_subscription
        self._pool.submit(self._check_claude)

    def apply_config(self, config: AppConfig) -> None:
        voice_changed = config.voice != self.config.voice
        self.config = config
        save_config(config)
        self.speaker.configure(config.voice.tts_voice, config.voice.tts_rate, config.voice.tts_volume)
        if voice_changed:
            if self.voice and not config.voice.enabled:
                self.voice.stop()
                self.voice = None
                self.voice_status.emit("muted", "Голос выключен в настройках")
            elif self.voice:
                self.voice.update_config(config.voice)
            elif config.voice.enabled:
                self.voice = self._make_voice()
                self.voice.start()
        self.recheck_claude()

    # ---- voices ----------------------------------------------------------
    def download_voices(self) -> None:
        """Fetch the neural voices that are not installed yet (background thread)."""
        from claude_assistant.voice import piper_voices

        if not piper_voices.piper_available():
            self.message.emit("error", "Не установлена библиотека нейроголосов. Выполните в папке ассистента: "
                                       ".venv\\Scripts\\python -m pip install piper-tts — и перезапустите ассистента.")
            self.voices_changed.emit(False)
            return

        def job() -> None:
            have = {v.id for v in piper_voices.installed()}
            missing = [v for v in piper_voices.CATALOG if v.id not in have]
            failed = []
            for index, voice in enumerate(missing, start=1):
                self.message.emit("system", f"Скачиваю голос {voice.label} ({index} из {len(missing)})…")
                try:
                    piper_voices.download(voice)
                except Exception as exc:
                    failed.append(f"{voice.label}: {exc}")
            if failed:
                self.message.emit("error", "Не удалось скачать: " + "; ".join(failed))
            total = len(piper_voices.installed())
            self.message.emit("system", f"Нейроголосов установлено: {total}. Выберите голос в настройках "
                                        "или скажите «Клод, смени голос».")
            self._voices_ready.emit(not failed)

        self._pool.submit(job)

    @Slot(bool)
    def _on_voices_ready(self, ok: bool) -> None:
        voice = self.config.voice
        self.speaker.configure(voice.tts_voice, voice.tts_rate, voice.tts_volume)
        self.voices_changed.emit(ok)

    def next_voice(self) -> str | None:
        """Switch to the next installed voice and remember the choice."""
        voices = self.speaker.voices()
        if not voices:
            return None
        current = self.config.voice.tts_voice
        index = (voices.index(current) + 1) % len(voices) if current in voices else (1 % len(voices))
        config = self.config.model_copy(deep=True)
        config.voice.tts_voice = voices[index]
        self.apply_config(config)
        return voices[index]

    # ---- state -----------------------------------------------------------
    def _set_state(self, state: str, detail: str = "") -> None:
        self.state, self.detail = state, detail
        self.state_changed.emit(state, detail)

    def _settle_state(self) -> None:
        if self._stopped:
            self._set_state(STOPPED, "Экстренная остановка. Нажмите «Продолжить».")
        elif self._busy:
            self._set_state(WORKING, "Выполняю задачу…")
        elif self.speaker.speaking:
            self._set_state(SPEAKING)
        elif self.claude is not None and not self.claude.available:
            self._set_state(OFFLINE, self.claude.message)
        else:
            self._set_state(IDLE)

    @property
    def busy(self) -> bool:
        return self._busy

    @property
    def stopped(self) -> bool:
        return self._stopped

    # ---- voice -------------------------------------------------------------
    def _on_wake(self, source: str) -> None:  # voice thread
        if self.config.voice.activation_sound:
            play_activation()
        self._wake.emit(source)

    @Slot(str)
    def _on_wake_main(self, source: str) -> None:
        detail = {"wake": "Услышал кодовое слово — говорите", "clap": "Хлопок — говорите"}.get(source, "Говорите")
        self._set_state(LISTENING, detail)

    @Slot(str)
    def _on_voice_command(self, text: str) -> None:
        if not text:
            self._settle_state()
            self.message.emit("system", "Не расслышал команду.")
            return
        self.submit(text, source="voice")

    @Slot(bool)
    def _on_speech_state(self, speaking: bool) -> None:
        if self.voice:
            self.voice.set_speaking(speaking)
        self._settle_state()

    def push_to_talk(self) -> None:
        if self.speaker.speaking:
            self.speaker.stop()
        if self.voice and not self.voice.muted:
            self.voice.activate()
        else:
            self.show_window.emit()

    def set_muted(self, muted: bool) -> None:
        if self.voice:
            self.voice.set_muted(muted)

    def stop_speaking(self) -> None:
        self.speaker.stop()

    def say(self, text: str, source: str) -> None:
        voice_cfg = self.config.voice
        if voice_cfg.tts_enabled and (source == "voice" or voice_cfg.speak_typed_replies):
            self.speaker.speak(clean_for_speech(text, voice_cfg.max_spoken_chars))

    # ---- emergency stop ------------------------------------------------------
    def emergency_stop(self) -> None:
        """Cancel everything in flight and refuse new tool execution until resumed."""
        self._stopped = True
        self._cancel.set()
        self.brain.cancel()
        self.speaker.stop()
        if self.voice:
            self.voice.cancel_listening()
        self._pending = None
        self.approval_needed.emit([])
        log.warning("Emergency stop")
        self.message.emit("system", "Экстренная остановка: все задачи отменены, новые действия заблокированы.")
        self._settle_state()

    def resume(self) -> None:
        self._stopped = False
        self._cancel = threading.Event()
        self.message.emit("system", "Работа возобновлена.")
        self._settle_state()

    def new_conversation(self) -> None:
        self.session_id = None
        self.message.emit("system", "Начат новый разговор.")

    # ---- input -----------------------------------------------------------------
    def submit(self, text: str, source: str = "text") -> None:
        text = text.strip()
        if not text:
            return
        self.message.emit("user", text)
        if self.config.system.log_conversation:
            log.info("User (%s): %s", source, text)
        else:
            log.info("User request via %s (%d chars)", source, len(text))

        intent = parse(text, self.config.apps, self.config.sites)
        if intent and intent.kind == "stop":
            self.speaker.stop()
            if self._busy:
                self.cancel_task()
            return
        if self._stopped:
            self._reply("Я на паузе после экстренной остановки. Нажмите «Продолжить».", source)
            return
        if intent:
            self._handle_intent(intent, source)
            return
        if self._busy:
            self._reply("Я ещё работаю над предыдущей задачей. Скажите «стоп», чтобы её отменить.", source)
            return
        self._run_brain(text, source)

    def _reply(self, text: str, source: str, role: str = "assistant") -> None:
        self.message.emit(role, text)
        self.say(text, source)
        self._settle_state()

    # ---- local intents ---------------------------------------------------------
    def _handle_intent(self, intent: Intent, source: str) -> None:
        kind = intent.kind
        if kind == "time":
            return self._reply(clock.time_phrase(), source)
        if kind == "date":
            return self._reply(clock.date_phrase(), source)
        if kind == "help":
            return self._reply(HELP_TEXT, source)
        if kind == "new_conversation":
            self.session_id = None
            return self._reply("Хорошо, начинаем новый разговор.", source)
        if kind == "next_voice":
            self.speaker.stop()
            name = self.next_voice()
            if name is None:
                return self._reply("Других голосов нет. Скачайте нейроголоса в настройках.", source)
            spoken = name.replace("Нейро: ", "")
            self.voices_changed.emit(True)
            return self._reply(f"Теперь я говорю так. Голос: {spoken}.", source)
        if kind == "mute":
            self.set_muted(True)
            return self._reply("Микрофон выключен. Включить его можно кнопкой или из меню в трее.", source)
        if kind == "remember":
            try:
                self.memory.add(intent.args["text"])
                return self._reply("Запомнил.", source)
            except ValueError as exc:
                return self._reply(f"Не получилось запомнить: {exc}.", source, "error")
        if kind == "recall":
            items = self.memory.items()
            if not items:
                return self._reply("Пока я ничего не запоминал. Скажите «запомни, что…».", source)
            listing = "; ".join(i.text for i in items[:10])
            return self._reply(f"Я помню: {listing}.", source)
        if kind == "forget":
            removed = self.memory.forget_matching(intent.args["text"])
            return self._reply(f"Удалил записей: {removed}." if removed else "Не нашёл такой записи в памяти.", source)
        if kind == "screenshot":
            return self._reply(self._screenshot(), source)
        if kind == "open_result":
            index = int(intent.args["index"]) - 1
            if not 0 <= index < len(self._last_results):
                return self._reply("Сначала попросите меня что-нибудь найти.", source)
            return self._run_local(lambda: apps.open_path(Path(self._last_results[index])), source)
        if kind == "status":
            return self._run_local(lambda: self._status_tool(intent.args["topic"]), source)
        if kind == "open_app":
            return self._run_local(lambda: apps.open_app(intent.args["alias"], intent.args["command"]), source)
        if kind == "close_app":
            return self._run_local(lambda: apps.close_app(intent.args["alias"], intent.args["command"]), source)
        if kind == "open_site":
            return self._run_local(lambda: web.open_url(intent.args["url"]), source)
        if kind == "web_search":
            return self._run_local(lambda: web.web_search(intent.args["query"]), source)
        if kind == "find_files":
            roots = self.search_roots()
            return self._run_local(lambda: files.search(intent.args["query"], roots), source)
        log.error("Unhandled intent %s", kind)

    def search_roots(self) -> list[Path]:
        roots = effective_workspaces(self.config) + [Path(p) for p in self.config.system.search_roots]
        unique: list[Path] = []
        for root in roots:
            if root not in unique:
                unique.append(root)
        return unique

    def _run_local(self, fn, source: str) -> None:
        self._set_state(WORKING, "Выполняю…")

        def job() -> None:
            try:
                result = fn()
            except Exception as exc:
                log.exception("Local tool failed")
                result = ToolResult(False, f"Ошибка: {exc}")
            self._local_done.emit(result, source)

        self._pool.submit(job)

    @Slot(object, str)
    def _on_local_done(self, result: ToolResult, source: str) -> None:
        if result.data.get("paths") is not None:
            self._last_results = list(result.data["paths"])
            self.search_results.emit(self._last_results)
        self._reply(result.message, source, "assistant" if result.ok else "error")

    def _status_tool(self, topic: str) -> ToolResult:
        snap = self.monitor.snapshot()
        self.snapshot_ready.emit((snap, None))
        text = {
            "cpu": hardware.describe_cpu, "gpu": hardware.describe_gpu, "ram": hardware.describe_ram,
            "battery": hardware.describe_battery, "disk": hardware.describe_disks,
        }.get(topic, hardware.describe_all)(snap)
        return ToolResult(True, text, verified=True)

    def _screenshot(self) -> str:
        if not self.config.system.allow_screenshots:
            return "Скриншоты выключены. Разрешить их можно в «Настройки → Система»."
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            return "Не удалось получить доступ к экрану."
        target = paths.screenshots_dir() / f"screenshot-{datetime.now():%Y%m%d-%H%M%S}.png"
        if not screen.grabWindow(0).save(str(target)):
            return "Не удалось сохранить скриншот."
        self._last_results = [str(target)]
        return f"Скриншот сохранён: {target.name}. Скажите «открой первый», чтобы посмотреть."

    # ---- hardware monitor --------------------------------------------------------
    @property
    def monitor(self) -> HardwareMonitor:
        if self._monitor is None:
            self._monitor = HardwareMonitor()
        return self._monitor

    def request_snapshot(self, with_processes: bool = False) -> None:
        if self._snapshot_pending:
            return
        self._snapshot_pending = True

        def job() -> None:
            try:
                snap = self.monitor.snapshot()
                procs = self.monitor.top_processes() if with_processes else None
                self.snapshot_ready.emit((snap, procs))
            except Exception:
                log.exception("Snapshot failed")
            finally:
                self._snapshot_pending = False

        self._pool.submit(job)

    # ---- brain -------------------------------------------------------------------
    def _run_brain(self, text: str, source: str, extra_rules: list[str] | None = None,
                   autonomy: str | None = None, resume: bool = True) -> None:
        if self.claude is not None and not self.claude.available:
            self.recheck_claude()
        idle_limit = self.config.brain.conversation_idle_reset_min * 60
        if self.session_id and time.monotonic() - self._last_activity > idle_limit:
            self.session_id = None
        cfg = self.config.brain
        request = BrainRequest(
            text=text,
            autonomy=autonomy or cfg.autonomy,
            workspaces=effective_workspaces(self.config),
            resume_session=self.session_id if resume else None,
            extra_rules=list(cfg.extra_allowed_tools) + list(extra_rules or []),
            memory_context=self.memory.as_prompt_context(),
            user_name=self.config.user_name,
            model=cfg.model,
            max_tool_calls=cfg.max_tool_calls,
            timeout_s=cfg.timeout_s,
        )
        self._busy = True
        self._cancel = threading.Event()
        cancel = self._cancel
        self._set_state(WORKING, "Claude думает…")
        self.task_started.emit(text)

        def job() -> None:
            def on_event(event: BrainEvent) -> None:
                self.task_event.emit({"kind": event.kind, "text": event.text, **event.data})

            try:
                result = self.brain.run(request, on_event, cancel)
            except Exception as exc:
                log.exception("Brain crashed")
                result = BrainResult(False, error_kind="crash", error_message=f"Внутренняя ошибка: {exc}")
            self._brain_done.emit(result, source)

        self._pool.submit(job)

    def cancel_task(self) -> None:
        self._cancel.set()
        self.brain.cancel()

    @Slot(object, str)
    def _on_brain_done(self, result: BrainResult, source: str) -> None:
        self._busy = False
        self._last_activity = time.monotonic()
        if result.session_id:
            self.session_id = result.session_id
        log.info("Brain finished ok=%s kind=%s tools=%d in %.1fs", result.ok, result.error_kind,
                 result.tool_calls, result.duration_s)
        self.task_finished.emit(result.ok, result.error_message)

        if not result.ok:
            if result.error_kind in ("auth", "not_installed"):
                self.recheck_claude()
            text = result.error_message
            if result.text and result.error_kind in ("iteration_limit", "timeout", "cancelled"):
                self.message.emit("assistant", result.text)
            self._reply(text, source, "error")
            return

        if result.text:
            self.message.emit("assistant", result.text)
            if self.config.system.log_conversation:
                log.info("Assistant: %s", result.text)
        approvable = [d for d in result.denials if d.approvable]
        forbidden = [d for d in result.denials if not d.approvable]
        for denial in forbidden:
            self.message.emit("error", f"Заблокировано: {denial.summary}\n{denial.reason}")
        if approvable:
            self._pending = (approvable, source)
            self.approval_needed.emit(approvable)
            spoken = (result.text + " " if result.text else "") + "Нужно ваше разрешение — посмотрите на экран."
            self.say(spoken, source)
        else:
            self.say(result.text or "Готово.", source)
        self._settle_state()

    def approve_pending(self) -> None:
        if not self._pending or self._busy or self._stopped:
            return
        denials, source = self._pending
        self._pending = None
        self.approval_needed.emit([])
        rules = [d.allow_rule for d in denials if d.allow_rule and d.allow_rule != "__execute_plan__"]
        execute_plan = any(d.allow_rule == "__execute_plan__" for d in denials)
        summary = "; ".join(d.summary.splitlines()[0] for d in denials)
        log.info("User approved %d action(s)", len(denials))
        if execute_plan:
            self._run_brain("Пользователь одобрил план. Выполни его и проверь результат.", source,
                            extra_rules=rules, autonomy="balanced")
        else:
            self._run_brain(f"Пользователь разрешил: {summary}. Продолжи задачу и проверь результат.", source,
                            extra_rules=rules)

    def reject_pending(self) -> None:
        if not self._pending:
            return
        _, source = self._pending
        self._pending = None
        self.approval_needed.emit([])
        self._reply("Хорошо, не буду этого делать.", source)
