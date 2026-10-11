"""Download the offline neural Russian voices (Piper, ~60 MB each).

Usage: python scripts/download_voices.py            # all voices
       python scripts/download_voices.py dmitri     # only voices whose id contains "dmitri"
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from claude_assistant.voice import piper_voices  # noqa: E402


def main(argv: list[str]) -> int:
    wanted = [v for v in piper_voices.CATALOG if not argv or any(a.lower() in v.id for a in argv)]
    have = {v.id for v in piper_voices.installed()}
    failed = 0
    for voice in wanted:
        if voice.id in have:
            print(f"Уже установлен: {voice.label}")
            continue
        print(f"Скачиваю {voice.label}…")
        try:
            piper_voices.download(voice, progress=lambda d, t: print(
                f"\r  {d * 100 // t if t else 0:3d}%  {d // 1_048_576} МБ", end="", flush=True))
            print(f"\r  готово{' ' * 20}")
        except Exception as exc:
            failed += 1
            print(f"\n  не удалось: {exc}", file=sys.stderr)
    print(f"Нейроголоса: {piper_voices.voices_dir()}")
    return 1 if failed == len(wanted) and wanted else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
