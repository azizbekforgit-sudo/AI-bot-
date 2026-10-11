"""Offline Russian speech recognition with Vosk (audio never leaves the computer)."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Protocol

from claude_assistant.voice.audio import SAMPLE_RATE

log = logging.getLogger(__name__)


class Recognizer(Protocol):
    def accept(self, pcm16: bytes) -> str | None:
        """Feed audio. Returns the final text of an utterance when one ends, else None."""

    def partial(self) -> str: ...

    def flush(self) -> str: ...

    def reset(self) -> None: ...


class VoskRecognizer:
    def __init__(self, model, grammar: list[str] | None = None) -> None:
        from vosk import KaldiRecognizer

        if grammar:
            self._rec = KaldiRecognizer(model, SAMPLE_RATE, json.dumps(grammar, ensure_ascii=False))
        else:
            self._rec = KaldiRecognizer(model, SAMPLE_RATE)

    def accept(self, pcm16: bytes) -> str | None:
        if self._rec.AcceptWaveform(pcm16):
            return json.loads(self._rec.Result()).get("text", "")
        return None

    def partial(self) -> str:
        return json.loads(self._rec.PartialResult()).get("partial", "")

    def flush(self) -> str:
        return json.loads(self._rec.FinalResult()).get("text", "")

    def reset(self) -> None:
        self._rec.Reset()


class VoskEngine:
    def __init__(self, model_path: Path) -> None:
        if not (model_path / "am").is_dir() and not (model_path / "conf").is_dir():
            raise FileNotFoundError(f"Модель распознавания речи не найдена: {model_path}")
        from vosk import Model, SetLogLevel

        SetLogLevel(-1)
        self.model = Model(str(model_path))
        self._vocab = self._load_vocab(model_path)

    @staticmethod
    def _load_vocab(model_path: Path) -> set[str] | None:
        for candidate in (model_path / "graph" / "words.txt", model_path / "words.txt"):
            if candidate.is_file():
                with candidate.open(encoding="utf-8", errors="ignore") as fh:
                    return {line.split()[0] for line in fh if line.strip()}
        return None

    def wake_recognizer(self, phrases: list[str]) -> tuple[VoskRecognizer, bool]:
        """Grammar-restricted recognizer when the vocabulary allows (much cheaper); else full recognition."""
        if self._vocab is not None:
            known = [p for p in phrases if all(w in self._vocab for w in p.split())]
            if known:
                return VoskRecognizer(self.model, known + ["[unk]"]), True
            log.warning("Wake words %s are not in the model vocabulary; using full recognition", phrases)
        return VoskRecognizer(self.model), False

    def command_recognizer(self) -> VoskRecognizer:
        return VoskRecognizer(self.model)
