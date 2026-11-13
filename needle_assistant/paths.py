from __future__ import annotations

import os
import sys
from pathlib import Path

APP_DIR_NAME = "NeedleDesktop"


def project_root() -> Path:
    """Return the source checkout root (only used in development)."""
    return Path(__file__).resolve().parent.parent


def resource_path(*parts: str) -> Path:
    """Resolve a bundled resource in both source and PyInstaller builds."""
    base = Path(getattr(sys, "_MEIPASS", project_root()))
    return base.joinpath(*parts)


def app_data_dir() -> Path:
    base = os.environ.get("APPDATA")
    root = Path(base) if base else Path.home() / "AppData" / "Roaming"
    path = root / APP_DIR_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def logs_dir() -> Path:
    path = app_data_dir() / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def config_path() -> Path:
    return app_data_dir() / "config.json"


def selftest_path() -> Path:
    return app_data_dir() / "selftest.json"


def preview_path() -> Path:
    return app_data_dir() / "overlay-preview.png"


def pictures_dir() -> Path:
    home = Path.home()
    candidates = [home / "Pictures", home / "My Pictures"]
    for candidate in candidates:
        if candidate.exists():
            path = candidate / "Needle"
            path.mkdir(parents=True, exist_ok=True)
            return path
    path = app_data_dir() / "Screenshots"
    path.mkdir(parents=True, exist_ok=True)
    return path


def engine_dll() -> Path:
    return resource_path("assets", "engine", "libcactus.dll")


def engine_cli() -> Path:
    return resource_path("assets", "engine", "windows-x86_64", "cactus.exe")


def needle_weights() -> Path:
    return resource_path("assets", "models", "needle3", "needle3.cact")


def vosk_model_dir() -> Path:
    return resource_path("assets", "models", "vosk-en-us")


def icon_path() -> Path:
    return resource_path("assets", "icons", "needle.ico")


def sound_path(name: str) -> Path:
    return resource_path("assets", "sounds", name)
