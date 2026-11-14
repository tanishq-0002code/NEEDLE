from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .paths import app_data_dir


def _path() -> Path:
    return app_data_dir() / "macros.json"


def load() -> dict[str, list[str]]:
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return {str(k): [str(x) for x in v] for k, v in data.items() if isinstance(v, list)}
    except (OSError, ValueError, TypeError):
        pass
    return {}


def save(macros: dict[str, list[str]]) -> None:
    try:
        _path().write_text(json.dumps(macros, indent=2, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def list_names() -> list[str]:
    return sorted(load().keys())


def get(name: str) -> list[str] | None:
    macros = load()
    key = str(name or "").strip().lower()
    if key in macros:
        return list(macros[key])
    # case-insensitive fallback
    for stored, steps in macros.items():
        if stored.lower() == key:
            return list(steps)
    return None


def set_macro(name: str, steps: list[str]) -> None:
    macros = load()
    key = str(name or "").strip()
    if not key:
        return
    macros[key] = [str(s) for s in steps if str(s).strip()]
    save(macros)


def delete(name: str) -> bool:
    macros = load()
    key = str(name or "").strip()
    if key in macros:
        del macros[key]
        save(macros)
        return True
    for stored in list(macros):
        if stored.lower() == key.lower():
            del macros[stored]
            save(macros)
            return True
    return False
