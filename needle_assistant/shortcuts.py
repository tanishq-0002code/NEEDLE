from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .paths import app_data_dir


def _path() -> Path:
    return app_data_dir() / "shortcuts.json"


def load() -> dict[str, list[str]]:
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
        if isinstance(data, dict):
            result: dict[str, list[str]] = {}
            for key, value in data.items():
                if isinstance(value, list):
                    result[str(key)] = [str(x) for x in value if str(x).strip()]
                elif isinstance(value, str) and value.strip():
                    result[str(key)] = [value.strip()]
            return result
    except (OSError, ValueError, TypeError):
        pass
    return {}


def save(shortcuts: dict[str, list[str]]) -> None:
    try:
        _path().write_text(json.dumps(shortcuts, indent=2, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def match(text: str) -> tuple[str, list[str]] | None:
    """Return (trigger, steps) for the best matching stored shortcut, or None."""
    cleaned = " ".join(str(text or "").split()).lower()
    if not cleaned:
        return None
    shortcuts = load()
    if not shortcuts:
        return None
    best_score = 0.0
    best: tuple[str, list[str]] | None = None
    for trigger, steps in shortcuts.items():
        trigger_clean = " ".join(str(trigger or "").split()).lower()
        if not trigger_clean or not steps:
            continue
        # Substring containment either way.
        if trigger_clean in cleaned or cleaned in trigger_clean:
            return trigger, list(steps)
        t_tokens = set(trigger_clean.split())
        c_tokens = set(cleaned.split())
        if t_tokens and c_tokens:
            # Containment: all trigger words present in input.
            if t_tokens.issubset(c_tokens):
                return trigger, list(steps)
            overlap = len(t_tokens & c_tokens) / max(len(t_tokens), len(c_tokens))
            if overlap >= 0.6:
                return trigger, list(steps)


def set_shortcut(trigger: str, steps: list[str]) -> None:
    shortcuts = load()
    key = " ".join(str(trigger or "").split())
    if not key or not steps:
        return
    shortcuts[key] = [str(s) for s in steps if str(s).strip()]
    save(shortcuts)


def delete(trigger: str) -> bool:
    shortcuts = load()
    key = " ".join(str(trigger or "").split())
    if key in shortcuts:
        del shortcuts[key]
        save(shortcuts)
        return True
    for stored in list(shortcuts):
        if stored.lower() == key.lower():
            del shortcuts[stored]
            save(shortcuts)
            return True
    return False


def list_shortcuts() -> dict[str, list[str]]:
    return load()
