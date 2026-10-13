"""Validated settings, stored as JSON in the user's data directory.

Secrets are never stored here: authentication belongs to Claude Code itself
(`claude` login with the user's subscription).
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import time
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from claude_assistant import paths

log = logging.getLogger(__name__)

Autonomy = Literal["assisted", "balanced", "advanced", "full"]
Effort = Literal["low", "medium", "high", "xhigh", "max"]
CONFIG_VERSION = 2

_HOTKEY_RE = re.compile(
    r"^((ctrl|alt|shift|win)\+)+([a-z0-9]|f([1-9]|1[0-2])|space|enter|tab|esc|backspace|pause)$"
)
# Extra Claude Code permission rules supplied by the user: a tool name, optionally
# followed by a parenthesised specifier. Quotes and newlines are rejected so a rule
# can never smuggle extra command-line arguments.
_TOOL_RULE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(\([^\"'\r\n\x00]{1,200}\))?$")


def _default_search_roots() -> list[str]:
    home = Path.home()
    return [str(p) for p in (home / "Documents", home / "Desktop") if p.is_dir()]


def _default_apps() -> dict[str, str]:
    if sys.platform == "win32":
        return {
            "блокнот": "notepad.exe",
            "калькулятор": "calc.exe",
            "проводник": "explorer.exe",
            "paint": "mspaint.exe",
            "пэйнт": "mspaint.exe",
            "диспетчер задач": "taskmgr.exe",
            "терминал": "wt.exe",
            "командную строку": "cmd.exe",
            "vs code": "code",
            "вс код": "code",
            "визуал студио код": "code",
        }
    return {"терминал": "x-terminal-emulator", "калькулятор": "gnome-calculator"}


def _default_sites() -> dict[str, str]:
    return {
        "ютуб": "https://www.youtube.com",
        "youtube": "https://www.youtube.com",
        "гугл": "https://www.google.com",
        "google": "https://www.google.com",
        "гитхаб": "https://github.com",
        "github": "https://github.com",
        "почту": "https://mail.google.com",
        "переводчик": "https://translate.google.com",
        "claude": "https://claude.ai",
        "клод": "https://claude.ai",
    }


class _Section(BaseModel):
    model_config = ConfigDict(extra="ignore", validate_assignment=True)


class BrainConfig(_Section):
    claude_path: str = ""
    model: str = "opus"
    # How long the model thinks before answering. "low" = fastest replies and fewest tokens.
    effort: Effort = "low"
    # Lean mode: no MCP servers/skills, only the tools the assistant needs → faster, cheaper.
    lean_mode: bool = True
    autonomy: Autonomy = "balanced"
    workspaces: list[str] = Field(default_factory=list)
    max_tool_calls: int = Field(40, ge=1, le=200)
    timeout_s: int = Field(600, ge=30, le=3600)
    use_subscription: bool = True
    conversation_idle_reset_min: int = Field(30, ge=1, le=1440)
    extra_allowed_tools: list[str] = Field(default_factory=list)

    @field_validator("model")
    @classmethod
    def _model_name(cls, value: str) -> str:
        value = value.strip()
        if value and not re.fullmatch(r"[A-Za-z0-9._\-\[\]]{1,80}", value):
            raise ValueError("недопустимое имя модели")
        return value

    @field_validator("extra_allowed_tools")
    @classmethod
    def _tool_rules(cls, rules: list[str]) -> list[str]:
        cleaned = [r.strip() for r in rules if r.strip()]
        for rule in cleaned:
            if not _TOOL_RULE_RE.fullmatch(rule):
                raise ValueError(f"недопустимое правило инструмента: {rule!r}")
        return cleaned

    @field_validator("workspaces")
    @classmethod
    def _workspaces(cls, items: list[str]) -> list[str]:
        result: list[str] = []
        for item in items:
            item = item.strip()
            if not item:
                continue
            if not Path(item).is_absolute():
                raise ValueError(f"путь рабочей папки должен быть абсолютным: {item!r}")
            if item not in result:
                result.append(item)
        return result


class VoiceConfig(_Section):
    enabled: bool = True
    wake_word_enabled: bool = True
    wake_words: list[str] = Field(default_factory=lambda: ["клод"])
    wake_match_threshold: float = Field(0.75, ge=0.5, le=1.0)
    input_device: str = ""
    model_path: str = ""
    command_timeout_s: float = Field(10.0, ge=2.0, le=60.0)
    silence_timeout_s: float = Field(5.0, ge=1.0, le=20.0)
    clap_enabled: bool = False
    clap_threshold: float = Field(0.35, ge=0.05, le=1.0)
    tts_enabled: bool = True
    speak_typed_replies: bool = False
    jarvis_effect: bool = True
    tts_voice: str = ""
    tts_rate: int = Field(1, ge=-10, le=10)
    tts_volume: int = Field(90, ge=0, le=100)
    max_spoken_chars: int = Field(400, ge=50, le=5000)
    activation_sound: bool = True

    @field_validator("wake_words")
    @classmethod
    def _wake_words(cls, words: list[str]) -> list[str]:
        cleaned = [w.strip().lower() for w in words if w.strip()]
        if not cleaned:
            raise ValueError("нужно хотя бы одно кодовое слово")
        for word in cleaned:
            if len(word) > 40:
                raise ValueError("кодовое слово слишком длинное")
        return cleaned


class SystemConfig(_Section):
    autostart: bool = False
    start_minimized: bool = True
    theme: Literal["dark", "light"] = "dark"
    hotkey_talk: str = "ctrl+alt+space"
    hotkey_stop: str = "ctrl+alt+x"
    allow_screenshots: bool = False
    log_conversation: bool = False
    monitor_interval_s: float = Field(2.0, ge=1.0, le=60.0)
    search_roots: list[str] = Field(default_factory=_default_search_roots)

    @field_validator("hotkey_talk", "hotkey_stop")
    @classmethod
    def _hotkey(cls, value: str) -> str:
        value = value.strip().lower().replace(" ", "")
        if value and not _HOTKEY_RE.fullmatch(value):
            raise ValueError(f"неверная горячая клавиша: {value!r} (пример: ctrl+alt+space)")
        return value


class AppConfig(_Section):
    version: int = CONFIG_VERSION
    user_name: str = Field("", max_length=60)
    brain: BrainConfig = Field(default_factory=BrainConfig)
    voice: VoiceConfig = Field(default_factory=VoiceConfig)
    system: SystemConfig = Field(default_factory=SystemConfig)
    apps: dict[str, str] = Field(default_factory=_default_apps)
    sites: dict[str, str] = Field(default_factory=_default_sites)

    @field_validator("sites")
    @classmethod
    def _sites(cls, sites: dict[str, str]) -> dict[str, str]:
        for name, url in sites.items():
            if not re.match(r"^https?://", url):
                raise ValueError(f"сайт {name!r}: разрешены только адреса http(s)")
        return {k.strip().lower(): v for k, v in sites.items()}

    @field_validator("apps")
    @classmethod
    def _apps(cls, apps: dict[str, str]) -> dict[str, str]:
        return {k.strip().lower(): v.strip() for k, v in apps.items() if k.strip() and v.strip()}


class LoadResult(BaseModel):
    config: AppConfig
    warnings: list[str] = Field(default_factory=list)


def load_config(path: Path | None = None) -> LoadResult:
    """Load settings, recovering from a missing, corrupt or partially invalid file.

    Valid sections are kept; an invalid section falls back to defaults and the
    original file is backed up so the user's edits are never silently lost.
    """
    path = path or paths.config_path()
    if not path.exists():
        config = AppConfig()
        save_config(config, path)
        return LoadResult(config=config)

    warnings: list[str] = []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("корень файла должен быть объектом JSON")
    except (OSError, ValueError) as exc:
        backup = _backup(path)
        warnings.append(f"Файл настроек повреждён ({exc}). Копия: {backup.name}. Загружены настройки по умолчанию.")
        config = AppConfig()
        save_config(config, path)
        return LoadResult(config=config, warnings=warnings)

    try:
        config = AppConfig.model_validate(raw)
    except ValidationError:
        pass
    else:
        if _migrate(raw, config):
            save_config(config, path)
        return LoadResult(config=config)

    # Section-by-section recovery.
    backup = _backup(path)
    clean: dict = {}
    for key, value in raw.items():
        try:
            AppConfig.model_validate({key: value})
            clean[key] = value
        except ValidationError as exc:
            first = exc.errors()[0]
            where = ".".join(str(p) for p in first["loc"])
            warnings.append(f"Настройка «{where}» неверна ({first['msg']}); использовано значение по умолчанию.")
            if isinstance(value, dict):
                # keep the valid fields of the section
                section_model = AppConfig.model_fields[key].annotation if key in AppConfig.model_fields else None
                if isinstance(section_model, type) and issubclass(section_model, BaseModel):
                    good = {}
                    for fkey, fval in value.items():
                        try:
                            section_model.model_validate({fkey: fval})
                            good[fkey] = fval
                        except ValidationError:
                            pass
                    clean[key] = good
    config = AppConfig.model_validate(clean)
    warnings.append(f"Исходный файл сохранён как {backup.name}.")
    save_config(config, path)
    return LoadResult(config=config, warnings=warnings)


def _migrate(raw: dict, config: AppConfig) -> bool:
    """Upgrade settings written by older versions. Returns True when something changed."""
    version = raw.get("version", 1) if isinstance(raw.get("version", 1), int) else 1
    if version >= CONFIG_VERSION:
        return False
    brain = raw.get("brain") if isinstance(raw.get("brain"), dict) else {}
    if not brain.get("model"):
        config.brain.model = "opus"  # v2: Opus with low effort is the default brain
    config.version = CONFIG_VERSION
    return True


def save_config(config: AppConfig, path: Path | None = None) -> None:
    """Atomic write: a crash mid-save never leaves a truncated config behind."""
    path = path or paths.config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(config.model_dump(), ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _backup(path: Path) -> Path:
    backup = path.with_name(f"{path.stem}.invalid-{time.strftime('%Y%m%d-%H%M%S')}.json")
    try:
        path.replace(backup)
    except OSError as exc:
        log.warning("Could not back up invalid config: %s", exc)
    return backup


def effective_workspaces(config: AppConfig) -> list[Path]:
    """Existing workspace folders; creates a default one when none are configured."""
    result = [Path(p) for p in config.brain.workspaces if Path(p).is_dir()]
    if not result:
        default = Path.home() / "ClaudeAssistant"
        default.mkdir(parents=True, exist_ok=True)
        result = [default]
    return result
