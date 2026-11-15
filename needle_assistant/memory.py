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
    return {"aliases": {}, "preferences": {}, "history": [], "contacts": {}}


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


def _norm_contact(name: str) -> str:
    return " ".join(str(name or "").split()).lower()


def get_contact(name: str) -> str | None:
    data = load()
    contacts = data.get("contacts", {})
    if not isinstance(contacts, dict):
        return None
    key = _norm_contact(name)
    if not key:
        return None
    value = contacts.get(key)
    if isinstance(value, str) and value.strip():
        return value.strip()
    # Unique partial match, e.g. "rahul" -> "rahul kumar". Ambiguous matches lose.
    partials = [
        v for k, v in contacts.items()
        if isinstance(k, str) and isinstance(v, str) and v.strip()
        and (key in k or k in key)
    ]
    if len(partials) == 1:
        return partials[0].strip()
    return None


def set_contact(name: str, number: str) -> None:
    data = load()
    contacts = data.setdefault("contacts", {})
    if not isinstance(contacts, dict):
        contacts = {}
        data["contacts"] = contacts
    key = _norm_contact(name)
    if key and number:
        contacts[key] = str(number).strip()
        save(data)


def delete_contact(name: str) -> bool:
    data = load()
    contacts = data.get("contacts", {})
    if not isinstance(contacts, dict):
        return False
    key = _norm_contact(name)
    if key in contacts:
        del contacts[key]
        save(data)
        return True
    return False


def list_contacts() -> dict[str, str]:
    data = load()
    contacts = data.get("contacts", {})
    if not isinstance(contacts, dict):
        return {}
    return {str(k): str(v) for k, v in contacts.items()}


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
