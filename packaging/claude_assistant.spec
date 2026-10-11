# PyInstaller spec: one-folder, windowed build.
# Build on Windows with:  powershell -ExecutionPolicy Bypass -File scripts\build_exe.ps1
# The speech model is not bundled (it is downloaded into %APPDATA% by scripts\download_model.py).
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs

ROOT = Path(SPECPATH).parent

hidden = ["claude_assistant.ui.main_window", "pynvml"]
if sys.platform == "win32":
    hidden += ["win32com.client", "pythoncom", "win32pdh", "winreg"]

binaries = collect_dynamic_libs("vosk") + collect_dynamic_libs("sounddevice")
datas = collect_data_files("vosk") + collect_data_files("_sounddevice_data")
try:  # optional neural voices (piper-tts bundles espeak-ng data and needs onnxruntime)
    import piper  # noqa: F401

    binaries += collect_dynamic_libs("piper") + collect_dynamic_libs("onnxruntime")
    datas += collect_data_files("piper")
    hidden += ["piper", "onnxruntime"]
except ImportError:
    pass

a = Analysis(
    [str(ROOT / "packaging" / "entry.py")],
    pathex=[str(ROOT / "src")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hidden,
    excludes=["tkinter", "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.Qt3DCore",
              "PySide6.QtQuick", "PySide6.QtQml", "PySide6.QtPdf", "PySide6.QtMultimedia"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="ClaudeDesktopAssistant",
    console=False,
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="ClaudeDesktopAssistant")
