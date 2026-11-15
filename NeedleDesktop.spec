# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for Needle Desktop.

Build onefile (default):
    pyinstaller --noconfirm --clean NeedleDesktop.spec

Build onedir (faster startup):
    set NEEDLE_ONEDIR=1
    pyinstaller --noconfirm --clean NeedleDesktop.spec
"""

from __future__ import annotations

import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files

ROOT = Path(SPECPATH)
ONEDIR = os.environ.get("NEEDLE_ONEDIR") == "1"
APP_NAME = "NeedleDesktop"

# The core app assets. Vosk's model is a directory; PyInstaller copies it
# recursively. The engine folder's static library and CLI are build-time only,
# so only libcactus.dll is shipped.
datas = [
    (str(ROOT / "assets" / "models"), "assets/models"),
    (str(ROOT / "assets" / "icons"), "assets/icons"),
    (str(ROOT / "assets" / "sounds"), "assets/sounds"),
    (str(ROOT / "assets" / "engine" / "libcactus.dll"), "assets/engine"),
]

# Vosk loads libvosk.dll from the directory that contains vosk/__init__.py,
# and libvosk.dll in turn needs its MinGW runtime DLLs beside it.
import importlib.util  # noqa: E402

_vosk_spec = importlib.util.find_spec("vosk")
if _vosk_spec is None or not _vosk_spec.submodule_search_locations:
    raise SystemExit("Vosk is not installed; run: python -m pip install -r requirements.txt")
VOSK_DIR = Path(next(iter(_vosk_spec.submodule_search_locations)))
for _dll in (
    "libvosk.dll",
    "libgcc_s_seh-1.dll",
    "libstdc++-6.dll",
    "libwinpthread-1.dll",
):
    _path = VOSK_DIR / _dll
    if _path.exists():
        datas.append((str(_path), "vosk"))

# sounddevice bundles PortAudio in _sounddevice_data.
datas += collect_data_files("_sounddevice_data")

# certifi provides the CA bundle used by requests for Telegram HTTPS.
datas += collect_data_files("certifi")

# uiautomation ships the native UIAutomation client proxy DLLs under bin/.
datas += collect_data_files("uiautomation")

hiddenimports = [
    "vosk.vosk_cffi",
    "cffi",
    "_cffi_backend",
    "srt",
    "tqdm",
    "requests",
    "certifi",
    "needle_assistant.telegram_bot",
    "sounddevice",
    "_sounddevice",
    "comtypes",
    "comtypes.client",
    "comtypes.gen",
    "comtypes.typeinfo",
    "comtypes.automation",
    "uiautomation",
    "pyautogui",
    "pyscreeze",
    "pymsgbox",
    "pytweening",
    "pynput",
    "pynput.keyboard",
    "pynput.keyboard._win32",
    "pynput.mouse",
    "pynput.mouse._win32",
    "PIL.Image",
    "PIL.ImageGrab",
    "PIL.ImageWin",
    "PyQt6.QtCore",
    "PyQt6.QtGui",
    "PyQt6.QtWidgets",
    "needle_assistant.overlay",
    "needle_assistant.waveform",
]

excludes = [
    "tkinter",
    "mouseinfo",
    "pygame",
    "cv2",
    "PyQt5",
    "PySide2",
    "PySide6",
    "matplotlib",
    "numpy",
    "scipy",
    "pandas",
    "IPython",
    "jupyter",
    "PyQt6.QtWebEngineCore",
    "PyQt6.QtWebEngineWidgets",
    "PyQt6.QtWebEngineQuick",
    "PyQt6.QtQuick",
    "PyQt6.QtQuick3D",
    "PyQt6.QtQml",
    "PyQt6.QtMultimedia",
    "PyQt6.QtMultimediaWidgets",
    "PyQt6.Qt3DCore",
    "PyQt6.QtBluetooth",
    "PyQt6.QtDesigner",
    "PyQt6.QtHelp",
    "PyQt6.QtNetworkAuth",
    "PyQt6.QtNfc",
    "PyQt6.QtPdf",
    "PyQt6.QtPdfWidgets",
    "PyQt6.QtPositioning",
    "PyQt6.QtPrintSupport",
    "PyQt6.QtRemoteObjects",
    "PyQt6.QtSensors",
    "PyQt6.QtSerialPort",
    "PyQt6.QtSpatialAudio",
    "PyQt6.QtSql",
    "PyQt6.QtStateMachine",
    "PyQt6.QtSvg",
    "PyQt6.QtSvgWidgets",
    "PyQt6.QtTest",
    "PyQt6.QtTextToSpeech",
    "PyQt6.QtWebChannel",
    "PyQt6.QtWebSockets",
    "PyQt6.QtXml",
]

a = Analysis(
    [str(ROOT / "run.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

if ONEDIR:
    exe = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name=APP_NAME,
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        console=False,
        disable_windowed_traceback=False,
        argv_emulation=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
        icon=str(ROOT / "assets" / "icons" / "needle.ico"),
        version=str(ROOT / "assets" / "version_info.txt"),
    )
    coll = COLLECT(
        exe,
        a.binaries,
        a.datas,
        strip=False,
        upx=False,
        upx_exclude=[],
        name=APP_NAME,
    )
else:
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.datas,
        [],
        name=APP_NAME,
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        upx_exclude=[],
        runtime_tmpdir=None,
        console=False,
        disable_windowed_traceback=False,
        argv_emulation=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
        icon=str(ROOT / "assets" / "icons" / "needle.ico"),
        version=str(ROOT / "assets" / "version_info.txt"),
    )
