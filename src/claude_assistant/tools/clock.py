from __future__ import annotations

from datetime import datetime

_MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа",
           "сентября", "октября", "ноября", "декабря"]
_WEEKDAYS = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]


def _plural(n: int, one: str, few: str, many: str) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def time_phrase(now: datetime | None = None) -> str:
    now = now or datetime.now()
    hours = _plural(now.hour, "час", "часа", "часов")
    minutes = _plural(now.minute, "минута", "минуты", "минут")
    return f"Сейчас {now.hour} {hours} {now.minute} {minutes}."


def date_phrase(now: datetime | None = None) -> str:
    now = now or datetime.now()
    return f"Сегодня {_WEEKDAYS[now.weekday()]}, {now.day} {_MONTHS[now.month - 1]} {now.year} года."
