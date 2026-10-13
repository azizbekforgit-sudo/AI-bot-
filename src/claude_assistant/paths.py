"""Locations of the assistant's local data (config, memory, logs, speech models)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from claude_assistant import APP_ID

HOME_ENV = "CLAUDE_ASSISTANT_HOME"


def data_dir() -> Path:
    """Per-user data directory. Override with CLAUDE_ASSISTANT_HOME (used by tests)."""
    override = os.environ.get(HOME_ENV)
    if override:
        base = Path(override)
    elif sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming") / APP_ID
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "claude-desktop-assistant"
    base.mkdir(parents=True, exist_ok=True)
    return base


def config_path() -> Path:
    return data_dir() / "config.json"


def memory_path() -> Path:
    return data_dir() / "memory.json"


def logs_dir() -> Path:
    path = data_dir() / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def models_dir() -> Path:
    path = data_dir() / "models"
    path.mkdir(parents=True, exist_ok=True)
    return path


def screenshots_dir() -> Path:
    path = Path.home() / "Pictures" / "Claude Assistant"
    path.mkdir(parents=True, exist_ok=True)
    return path


def default_vosk_model_dir() -> Path:
    return models_dir() / "vosk-model-small-ru-0.22"
