import os
import stat
import sys
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

TESTS = Path(__file__).parent


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    """Every test gets its own data directory: no test touches the real user config."""
    home = tmp_path / "assistant-home"
    monkeypatch.setenv("CLAUDE_ASSISTANT_HOME", str(home))
    return home


@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


def wait_until(app, predicate, timeout=10.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    return False


@pytest.fixture
def fake_claude(tmp_path):
    """An executable named `claude` (or claude.cmd-free wrapper) that runs tests/fake_claude.py."""
    if sys.platform == "win32":
        pytest.skip("fake claude launcher is POSIX-only")
    launcher = tmp_path / "claude"
    launcher.write_text(f"#!{sys.executable}\nimport runpy, sys\nsys.argv[0] = {str(TESTS / 'fake_claude.py')!r}\n"
                        f"runpy.run_path({str(TESTS / 'fake_claude.py')!r}, run_name='__main__')\n", encoding="utf-8")
    launcher.chmod(launcher.stat().st_mode | stat.S_IEXEC)
    return launcher
