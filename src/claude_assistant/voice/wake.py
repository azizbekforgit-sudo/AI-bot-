"""Wake-word matching on recognized text (tolerant to typical recognition errors)."""

from __future__ import annotations

import re
from difflib import SequenceMatcher

# How the Russian model tends to hear "Клод".
KNOWN_VARIANTS = {
    "клод": ["клод", "клот", "клоуд", "клауд", "клёд", "claude"],
}


def normalize(text: str) -> str:
    text = text.lower().replace("ё", "е")
    text = re.sub(r"[^\w\s-]", " ", text)
    return " ".join(text.split())


def expand(words: list[str]) -> list[str]:
    result: list[str] = []
    for word in words:
        for variant in [word, *KNOWN_VARIANTS.get(normalize(word), [])]:
            v = normalize(variant)
            if v and v not in result:
                result.append(v)
    return result


class WakeMatcher:
    def __init__(self, wake_words: list[str], threshold: float = 0.75) -> None:
        self.phrases = expand(wake_words)
        self.threshold = threshold

    def _similar(self, a: str, b: str) -> bool:
        if a == b:
            return True
        # Short words need an exact (variant) match: "клод" is one letter away from "код" and "кот".
        if min(len(a), len(b)) <= 5:
            return False
        return SequenceMatcher(None, a, b).ratio() >= self.threshold

    def find(self, text: str) -> int | None:
        """Index (in tokens) just after the wake phrase, or None if absent."""
        tokens = normalize(text).split()
        for phrase in self.phrases:
            size = len(phrase.split())
            for i in range(len(tokens) - size + 1):
                if self._similar(" ".join(tokens[i:i + size]), phrase):
                    return i + size
        return None

    def matches(self, text: str) -> bool:
        return self.find(text) is not None

    def strip(self, text: str) -> str:
        """Remove a wake phrase at the start of an utterance: 'клод открой блокнот' → 'открой блокнот'."""
        tokens = normalize(text).split()
        end = self.find(" ".join(tokens[:3]))
        if end is not None:
            tokens = tokens[end:]
        return " ".join(tokens)
