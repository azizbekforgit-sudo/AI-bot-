import pytest

from claude_assistant.core.intents import parse

APPS = {"блокнот": "notepad.exe", "vs code": "code", "вс код": "code", "калькулятор": "calc.exe"}
SITES = {"ютуб": "https://www.youtube.com", "гитхаб": "https://github.com"}


@pytest.mark.parametrize("text,kind,args", [
    ("Открой блокнот", "open_app", {"alias": "блокнот", "command": "notepad.exe"}),
    ("запусти vs code", "open_app", {"alias": "vs code", "command": "code"}),
    ("закрой калькулятор", "close_app", {"alias": "калькулятор", "command": "calc.exe"}),
    ("открой ютуб", "open_site", {"url": "https://www.youtube.com", "name": "ютуб"}),
    ("открой сайт habr.com", "open_site", {"url": "https://habr.com", "name": "habr.com"}),
    ("перейди на example.org/path", "open_site", {"url": "https://example.org/path", "name": "example.org/path"}),
    ("Который час?", "time", {}),
    ("сколько сейчас времени", "time", {}),
    ("какое сегодня число", "date", {}),
    ("стоп", "stop", {}),
    ("хватит", "stop", {}),
    ("как там видеокарта?", "status", {"topic": "gpu"}),
    ("насколько загружен процессор", "status", {"topic": "cpu"}),
    ("сколько свободно оперативной памяти", "status", {"topic": "ram"}),
    ("какой заряд батареи", "status", {"topic": "battery"}),
    ("сколько места на диске", "status", {"topic": "disk"}),
    ("как состояние системы", "status", {"topic": "all"}),
    ("найди мой проект бот", "find_files", {"query": "бот"}),
    ("найди файл с названием отчёт", "find_files", {"query": "отчет"}),
    ("загугли погода в москве", "web_search", {"query": "погода в москве"}),
    ("найди в интернете рецепт плова", "web_search", {"query": "рецепт плова"}),
    ("Запомни, что мой проект лежит в D:\\code", "remember", {"text": "мой проект лежит в D:\\code"}),
    ("что ты помнишь обо мне", "recall", {}),
    ("забудь про кофе", "forget", {"text": "кофе"}),
    ("новый разговор", "new_conversation", {}),
    ("сделай скриншот", "screenshot", {}),
    ("открой второй", "open_result", {"index": "2"}),
    ("выключи микрофон", "mute", {}),
    ("что ты умеешь", "help", {}),
])
def test_local_intents(text, kind, args):
    intent = parse(text, APPS, SITES)
    assert intent is not None, text
    assert intent.kind == kind
    assert intent.args == args


@pytest.mark.parametrize("text", [
    "напиши скрипт, который делает бэкап папки документы",
    "почему не запускается мой проект",
    "открой фотошоп",  # unknown app: Claude decides
    "закрой все вкладки браузера",
    "расскажи, как работает видеокарта в нейросетях и почему ей нужна память",
    "стоп-кран в поезде это что такое и где он находится",
    "",
])
def test_everything_else_goes_to_claude(text):
    assert parse(text, APPS, SITES) is None


def test_javascript_url_not_opened():
    intent = parse("открой сайт javascript:alert(1)", APPS, SITES)
    assert intent is None or not intent.args.get("url", "").startswith("javascript")


@pytest.mark.parametrize("text", ["смени голос", "Клод поменяй голос", "поменяй свой голос на мужской", "другой голос"])
def test_next_voice_intent(text):
    from claude_assistant.voice.wake import WakeMatcher

    intent = parse(WakeMatcher(["клод"]).strip(text), APPS, SITES)
    assert intent is not None and intent.kind == "next_voice"
