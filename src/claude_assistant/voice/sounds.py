from __future__ import annotations

import sys


def play_activation() -> None:
    """Short system sound when the assistant starts listening (non-blocking)."""
    if sys.platform != "win32":
        return
    try:
        import winsound

        winsound.PlaySound("SystemAsterisk", winsound.SND_ALIAS | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
    except Exception:
        pass
