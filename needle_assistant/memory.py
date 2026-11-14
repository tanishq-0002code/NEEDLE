from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from .paths import app_data_dir


def _path() -> Path:
    return app_data_dir() / "memory.json"


def load() -> dict[str, Any]:
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except (OSError, ValueError, TypeError):
        pass
    return {"aliases": {}, "preferences": {}, "history": []}


def save(data: dict[str, Any]) -> None:
    try:
        _path().write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def get_alias(name: str) -> str | None:
    data = load()
    aliases = data.get("aliases", {})
    if not isinstance(aliases, dict):
        return None
    key = " ".join(str(name or "").split()).lower()
    value = aliases.get(key)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def set_alias(alias: str, target: str) -> None:
    data = load()
    aliases = data.setdefault("aliases", {})
    if not isinstance(aliases, dict):
        aliases = {}
        data["aliases"] = aliases
    key = " ".join(str(alias or "").split()).lower()
    if key and target:
        aliases[key] = str(target).strip()
        save(data)


def delete_alias(alias: str) -> bool:
    data = load()
    aliases = data.get("aliases", {})
    if not isinstance(aliases, dict):
        return False
    key = " ".join(str(alias or "").split()).lower()
    if key in aliases:
        del aliases[key]
        save(data)
        return True
    return False


def list_aliases() -> dict[str, str]:
    data = load()
    aliases = data.get("aliases", {})
    if not isinstance(aliases, dict):
        return {}
    return {str(k): str(v) for k, v in aliases.items()}


def record_preference(key: str, value: Any) -> None:
    data = load()
    prefs = data.setdefault("preferences", {})
    if not isinstance(prefs, dict):
        prefs = {}
        data["preferences"] = prefs
    prefs[str(key)] = value
    save(data)


def get_preference(key: str, default: Any = None) -> Any:
    data = load()
    prefs = data.get("preferences", {})
    if not isinstance(prefs, dict):
        return default
    return prefs.get(key, default)


def record_history(text: str, tool: str | None = None) -> None:
    data = load()
    history = data.setdefault("history", [])
    if not isinstance(history, list):
        history = []
        data["history"] = history
    entry = {"text": str(text or "")[:200]}
    if tool:
        entry["tool"] = str(tool)
    history.append(entry)
    if len(history) > 100:
        del history[:-100]
    save(data)


def clear() -> None:
    try:
        _path().unlink(missing_ok=True)
    except OSError:
        pass
