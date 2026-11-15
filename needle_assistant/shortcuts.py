from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .paths import app_data_dir


def _path() -> Path:
    return app_data_dir() / "shortcuts.json"


# Built-in shortcuts shipped with Needle Desktop. These work without the user
# creating anything. User-created shortcuts (stored in shortcuts.json) are
# checked first, so the user can override any of these.
#
# Each step is routed through the normal local tool router, so "study time"
# becomes study_mode: open the Physics Wallah dashboard and report the classes
# in one step instead of opening a duplicate Edge tab.
_STUDY_OPEN_STEP = ["study time"]
_STUDY_PREPARE_STEP = ["get the system ready for study"]

_DEFAULT_SHORTCUTS: dict[str, list[str]] = {
    "i am gonna study": list(_STUDY_OPEN_STEP),
    "i am going to study": list(_STUDY_OPEN_STEP),
    "time to study": list(_STUDY_OPEN_STEP),
    "study time": list(_STUDY_OPEN_STEP),
    "let's study": list(_STUDY_OPEN_STEP),
    "lets study": list(_STUDY_OPEN_STEP),
    "i am gonna study now": list(_STUDY_OPEN_STEP),
    "study mode": list(_STUDY_OPEN_STEP),
    "get the system ready for study": list(_STUDY_PREPARE_STEP),
    "get system ready for study": list(_STUDY_PREPARE_STEP),
    "get ready for study": list(_STUDY_OPEN_STEP),
    "prepare the system for study": list(_STUDY_PREPARE_STEP),
    "ready for study": list(_STUDY_OPEN_STEP),
    "start study session": list(_STUDY_OPEN_STEP),
    "study session": list(_STUDY_OPEN_STEP),
}


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
    # Built-in defaults act as fallback.
    combined = {**_DEFAULT_SHORTCUTS, **shortcuts}
    if not combined:
        return None
    for trigger, steps in combined.items():
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
