"""Fast local handling of simple Russian commands. Everything else goes to Claude Code.

Local intents answer instantly, work offline and do not use the subscription quota.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from claude_assistant.tools.apps import resolve_alias
from claude_assistant.tools.web import normalize_url


@dataclass
class Intent:
    kind: str
    args: dict[str, str] = field(default_factory=dict)


_STATUS_WORDS = re.compile(r"загруз|нагруз|температур|сколько|состояни|как |работает|занят|свободн|заряд|уровень|греет|горяч|статус|насколько")
_STATUS_TOPICS = [
    ("gpu", re.compile(r"видеокарт|видюх|gpu|графическ|видеопамят")),
    ("cpu", re.compile(r"процессор|\bcpu\b|проц\b")),
    ("ram", re.compile(r"оперативн|\bозу\b|\bram\b|\bпамят[ьи]\b")),
    ("battery", re.compile(r"батаре|аккумулятор|заряд")),
    ("disk", re.compile(r"\bдиск|места на")),
    ("all", re.compile(r"систем|компьютер|ноутбук|железо")),
]


def normalize(text: str) -> str:
    """Lowercase and drop punctuation, but keep what URLs need (dots, slashes, colons)."""
    text = text.lower().replace("ё", "е")
    text = re.sub(r"[^\w\s./:-]", " ", text)
    text = re.sub(r"(?<!\w)[.:/-]+|[.:/-]+(?!\w)", " ", text)
    return " ".join(text.split())


def parse(text: str, apps: dict[str, str], sites: dict[str, str]) -> Intent | None:
    t = normalize(text)
    if not t:
        return None
    words = t.split()

    if len(words) <= 3 and re.match(r"^(стоп|хватит|замолчи|отмена|отмени|остановись|тихо|прекрати)\b", t):
        return Intent("stop")
    if re.search(r"(который час|сколько (сейчас )?времени|какое (сейчас )?время)", t):
        return Intent("time")
    if re.search(r"(какое (сегодня )?число|какой сегодня день|какая (сегодня )?дата|какой день недели)", t):
        return Intent("date")
    if re.search(r"^(новый разговор|начни сначала|начнем сначала|новая тема|забудь (этот )?разговор)", t):
        return Intent("new_conversation")
    if re.search(r"(что ты (обо мне )?помнишь|что ты знаешь обо мне|покажи (мою )?память)", t):
        return Intent("recall")
    if m := re.match(r"^(запомни|запиши в память)( что)? (.+)", t):
        return Intent("remember", {"text": _original_tail(text) or m.group(3)})
    if m := re.match(r"^забудь( что| про)? (.+)", t):
        return Intent("forget", {"text": m.group(2)})
    if re.search(r"(выключи микрофон|не слушай|перестань слушать)", t):
        return Intent("mute")
    if re.search(r"(скриншот|снимок экрана)", t) and len(words) <= 6:
        return Intent("screenshot")
    if re.search(r"^(что ты умеешь|помощь|справка)", t):
        return Intent("help")

    if m := re.match(r"^(найди|поищи|загугли|погугли)( в (интернете|гугле|google|сети))? (.+)", t):
        if m.group(1) in ("загугли", "погугли") or m.group(2):
            return Intent("web_search", {"query": m.group(4)})
    if m := re.match(r"^(найди|поищи|где|покажи)( мой| мою| мои| моё| мое)? (файл|папк|проект|документ)\w* (.+)", t):
        query = re.sub(r"^(с названием|под названием|называется|про|по)\s+", "", m.group(4))
        return Intent("find_files", {"query": query})

    if m := re.match(r"^открой (первый|второй|третий|четвертый|пятый|1|2|3|4|5)( результат| файл| проект| вариант)?$", t):
        index = {"первый": 1, "второй": 2, "третий": 3, "четвертый": 4, "пятый": 5}.get(m.group(1)) or int(m.group(1))
        return Intent("open_result", {"index": str(index)})
    if m := re.match(r"^(открой|запусти|включи|зайди на|перейди на)( сайт)? (.+)", t):
        target = m.group(3)
        if target in sites:
            return Intent("open_site", {"url": sites[target], "name": target})
        if m.group(2) or m.group(1) in ("зайди на", "перейди на") or "." in target or " точка " in f" {target} ":
            url = normalize_url(target)
            if url:
                return Intent("open_site", {"url": url, "name": target})
        if alias := resolve_alias(target, apps):
            return Intent("open_app", {"alias": alias[0], "command": alias[1]})
        for name, url in sites.items():
            if target.startswith(name):
                return Intent("open_site", {"url": url, "name": name})
        return None  # unknown app: let Claude figure it out

    if m := re.match(r"^(закрой|выключи|заверши)( программу)? (.+)", t):
        if alias := resolve_alias(m.group(3), apps):
            return Intent("close_app", {"alias": alias[0], "command": alias[1]})
        return None

    if len(words) <= 9 and (_STATUS_WORDS.search(t + " ") or len(words) <= 2):
        for topic, pattern in _STATUS_TOPICS:
            if pattern.search(t):
                return Intent("status", {"topic": topic})
    return None


def _original_tail(original: str) -> str:
    """The text to remember exactly as the user said/typed it (casing, paths, punctuation)."""
    m = re.match(r"^\s*(запомни|запиши в память)[\s,:]*(что[\s,]+)?", original, re.IGNORECASE)
    return original[m.end():].strip().rstrip(".!") if m else ""


HELP_TEXT = (
    "Я умею: открывать программы и сайты, искать файлы, говорить время и дату, "
    "рассказывать о загрузке процессора, видеокарты, памяти и батареи, запоминать то, что вы попросите. "
    "Всё остальное — код, файлы, проекты, вопросы — я передаю Claude, и он выполняет задачу на компьютере."
)
