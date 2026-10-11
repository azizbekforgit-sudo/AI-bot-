from __future__ import annotations

import re
import webbrowser
from urllib.parse import quote_plus, urlparse

from claude_assistant.tools.base import ToolResult

_DOMAIN_RE = re.compile(r"^(?:[a-z0-9-]{1,63}\.)+[a-z]{2,24}$", re.I)


def normalize_url(target: str) -> str | None:
    target = target.strip().strip(".,!?").replace(" точка ", ".").replace(" ", "")
    if not target:
        return None
    if not re.match(r"^https?://", target, re.I):
        if not _DOMAIN_RE.match(target.split("/")[0]):
            return None
        target = "https://" + target
    parsed = urlparse(target)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return None
    return target


def open_url(url: str) -> ToolResult:
    normalized = normalize_url(url)
    if not normalized:
        return ToolResult(False, f"Это не похоже на адрес сайта: {url}")
    try:
        opened = webbrowser.open(normalized, new=2)
    except webbrowser.Error as exc:
        return ToolResult(False, f"Не удалось открыть браузер: {exc}")
    if not opened:
        return ToolResult(False, "Браузер по умолчанию не найден.")
    host = urlparse(normalized).hostname
    return ToolResult(True, f"Открываю {host}.", verified=False, data={"url": normalized})


def web_search(query: str) -> ToolResult:
    query = query.strip()
    if not query:
        return ToolResult(False, "Пустой поисковый запрос.")
    return open_url("https://www.google.com/search?q=" + quote_plus(query))
