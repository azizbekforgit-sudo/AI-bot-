"""Download the offline Russian speech model (Vosk, ~45 MB, Apache 2.0) into the assistant's data folder.

Usage: python scripts/download_model.py [--force]
"""

from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from claude_assistant import paths  # noqa: E402

MODEL = "vosk-model-small-ru-0.22"
URL = f"https://alphacephei.com/vosk/models/{MODEL}.zip"


def _progress(done: int, total: int) -> None:
    if total > 0:
        pct = done * 100 // total
        print(f"\r  {pct:3d}%  {done // 1_048_576} / {total // 1_048_576} МБ", end="", flush=True)


def download(force: bool = False) -> Path:
    target = paths.default_vosk_model_dir()
    if (target / "am").is_dir() and not force:
        print(f"Модель уже установлена: {target}")
        return target
    print(f"Скачиваю модель распознавания речи: {URL}")
    with tempfile.TemporaryDirectory() as tmp:
        archive = Path(tmp) / f"{MODEL}.zip"
        with urllib.request.urlopen(URL, timeout=60) as response, archive.open("wb") as out:
            total = int(response.headers.get("Content-Length") or 0)
            done = 0
            while chunk := response.read(1 << 16):
                out.write(chunk)
                done += len(chunk)
                _progress(done, total)
        print()
        with zipfile.ZipFile(archive) as zf:
            bad = zf.testzip()
            if bad:
                raise RuntimeError(f"Архив повреждён: {bad}")
            extract_root = Path(tmp) / "x"
            for member in zf.namelist():  # refuse path traversal
                dest = (extract_root / member).resolve()
                if not str(dest).startswith(str(extract_root.resolve())):
                    raise RuntimeError(f"Подозрительный путь в архиве: {member}")
            zf.extractall(extract_root)
        extracted = extract_root / MODEL
        if not (extracted / "am").is_dir():
            raise RuntimeError("В архиве нет ожидаемой модели")
        if target.exists():
            shutil.rmtree(target)
        shutil.move(str(extracted), str(target))
    print(f"Готово: {target}")
    return target


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="скачать заново")
    try:
        download(parser.parse_args().force)
    except Exception as exc:
        print(f"\nОшибка: {exc}\nМожно скачать вручную: {URL} и распаковать в {paths.models_dir()}", file=sys.stderr)
        sys.exit(1)
