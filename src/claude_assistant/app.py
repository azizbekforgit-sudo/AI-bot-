"""Entry point: single instance, logging, config, controller, window, tray, hotkeys."""

from __future__ import annotations

import argparse
import logging
import sys
import traceback

from claude_assistant import APP_ID, APP_NAME, __version__

log = logging.getLogger("claude_assistant")


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="claude-assistant", description=APP_NAME)
    parser.add_argument("--minimized", action="store_true", help="запуск в трее без окна")
    parser.add_argument("--debug", action="store_true", help="подробный лог")
    parser.add_argument("--no-voice", action="store_true", help="не включать микрофон")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--enable-autostart", action="store_true",
                        help="включить запуск при входе в Windows и выйти")
    parser.add_argument("--disable-autostart", action="store_true",
                        help="удалить запись автозапуска и выйти")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)

    from claude_assistant.integration.startup import StartupManager
    from claude_assistant.logging_setup import setup_logging

    setup_logging(args.debug)

    if args.enable_autostart or args.disable_autostart:
        from claude_assistant.config import load_config, save_config

        manager = StartupManager()
        if not manager.supported:
            print("Автозапуск поддерживается только в Windows.")
            return 1
        enabled = bool(args.enable_autostart)
        manager.apply(enabled)
        config = load_config().config
        config.system.autostart = enabled
        save_config(config)
        print("Автозапуск включён." if enabled else "Автозапуск отключён.")
        return 0

    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
        except Exception:
            pass

    from PySide6.QtWidgets import QApplication, QMessageBox

    app = QApplication(sys.argv[:1])
    app.setApplicationName(APP_NAME)
    app.setQuitOnLastWindowClosed(False)

    from claude_assistant.integration.single_instance import SingleInstance

    instance = SingleInstance()
    if instance.notify_existing():
        log.info("Another instance is running; asked it to show itself")
        return 0
    if not instance.listen():
        log.warning("Single-instance server failed to start; continuing")

    from claude_assistant.config import load_config
    from claude_assistant.core.controller import Controller
    from claude_assistant.integration.hotkeys import HotkeyManager
    from claude_assistant.memory import MemoryStore
    from claude_assistant.ui.main_window import MainWindow
    from claude_assistant.voice.tts import create_speaker

    loaded = load_config()
    config = loaded.config
    memory = MemoryStore()
    speaker = create_speaker()
    controller = Controller(config, memory, speaker=speaker, enable_voice=not args.no_voice)
    startup = StartupManager()
    hotkeys = HotkeyManager()
    window = MainWindow(controller, hotkeys, startup)
    instance.activated.connect(window.bring_to_front)

    def excepthook(exc_type, exc, tb) -> None:
        log.error("Unhandled error:\n%s", "".join(traceback.format_exception(exc_type, exc, tb)))
        controller.message.emit("error", f"Внутренняя ошибка: {exc}. Подробности в логе.")

    sys.excepthook = excepthook

    for warning in loaded.warnings:
        controller.message.emit("system", warning)
    if startup.supported and config.system.autostart and not startup.is_enabled():
        try:
            startup.enable()
        except OSError as exc:
            controller.message.emit("error", f"Не удалось включить автозапуск: {exc}")

    if not speaker.has_natural_russian_voice():
        controller.message.emit("system", "Нет русского голоса для озвучки. Скачайте нейроголоса: «Настройки → Голос → "
                                          "Скачать нейроголоса» (или установите русский голос Windows).")

    def shutdown() -> None:
        log.info("Shutting down")
        hotkeys.stop()
        controller.shutdown()
        instance.close()

    app.aboutToQuit.connect(shutdown)
    controller.start()
    if not (args.minimized and config.system.start_minimized and window.tray is not None):
        window.show()
    log.info("%s %s started", APP_NAME, __version__)
    try:
        return app.exec()
    except Exception as exc:  # last resort
        log.exception("Fatal error")
        QMessageBox.critical(None, APP_NAME, f"Критическая ошибка: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
