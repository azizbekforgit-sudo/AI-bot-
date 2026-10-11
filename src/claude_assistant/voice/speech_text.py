"""Turn a chat answer into something pleasant to listen to."""

from __future__ import annotations

import re


def clean_for_speech(text: str, max_chars: int = 400) -> str:
    text = re.sub(r"```.*?```", " Код показан на экране. ", text, flags=re.S)
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"https?://\S+", "ссылка", text)
    text = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"^\s*[#>|\-*+]+\s*", "", text, flags=re.M)
    text = re.sub(r"[*_#|]+", "", text)
    text = " ".join(text.split())
    if len(text) <= max_chars:
        return text
    cut = text[:max_chars]
    end = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
    if end > max_chars // 3:
        cut = cut[: end + 1]
    return cut.rstrip() + " Подробности на экране."
