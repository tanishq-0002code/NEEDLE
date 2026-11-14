from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

from .paths import config_path

DEFAULT_WAKE_PHRASES = ["hey needle", "hey jarvis", "hey computer"]
DEFAULT_HOTKEY = "ctrl+space"

_MODIFIERS = {"ctrl", "control", "alt", "shift", "win", "windows", "cmd", "super", "meta"}
_SPECIAL_KEYS = {
    "space", "enter", "return", "tab", "esc", "escape", "backspace", "delete", "del",
    "insert", "ins", "home", "end", "pageup", "pagedown", "up", "down", "left", "right",
    "capslock", "printscreen", "pause", "menu",
    *(f"f{i}" for i in range(1, 25)),
}


def normalize_hotkey(value: str) -> str:
    """Normalize a shortcut such as ``Ctrl + Space`` to ``ctrl+space``."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("A hotkey is required.")
    raw = value.strip().lower().replace("<", "").replace(">", "")
    raw = raw.replace("control", "ctrl").replace("windows", "win").replace("super", "win")
    raw = raw.replace("meta", "win").replace("cmd", "win")
    parts = [part.strip() for part in raw.split("+") if part.strip()]
    if not parts or len(parts) > 4:
        raise ValueError("Use a shortcut with up to four keys, for example ctrl+space.")
    normalized: list[str] = []
    for part in parts:
        part = part.replace("escape", "esc").replace("return", "enter")
        part = part.replace("page_up", "pageup").replace("page_down", "pagedown")
        if part in _MODIFIERS:
            part = "win" if part in {"win", "cmd"} else "ctrl" if part == "control" else part
        elif part not in _SPECIAL_KEYS and not (len(part) == 1 and part.isprintable()):
            raise ValueError(f"Unsupported key in shortcut: {part!r}")
        if part not in normalized:
            normalized.append(part)
    if len(normalized) > 4:
        raise ValueError("Use a shortcut with up to four keys.")
    if not any(part not in _MODIFIERS for part in normalized):
        raise ValueError("A shortcut needs a non-modifier key, for example ctrl+space.")
    return "+".join(normalized)


def normalize_wake_phrases(values: list[str] | str) -> list[str]:
    if isinstance(values, str):
        values = re.split(r"[,\n;]+", values)
    cleaned: list[str] = []
    for value in values:
        text = re.sub(r"\s+", " ", str(value).strip().lower())
        text = re.sub(r"[^a-z0-9 ]", "", text)
        if text and text not in cleaned:
            cleaned.append(text)
    return cleaned[:8]


def normalize_chat_ids(values: list[Any] | str | int | float | None) -> list[int]:
    """Normalize Telegram chat IDs from a list or a comma/space separated string.

    Telegram uses positive IDs for private chats and negative IDs for groups, so
    both signs are preserved.  Duplicates and invalid entries are dropped.
    """
    if values is None:
        return []
    if isinstance(values, bool):
        return []
    if isinstance(values, (int, float)):
        values = [values]
    elif isinstance(values, str):
        values = re.split(r"[,\s;]+", values)
    elif not isinstance(values, (list, tuple, set)):
        return []

    cleaned: list[int] = []
    for value in values:
        if isinstance(value, bool):
            continue
        if isinstance(value, float):
            if not value.is_integer():
                continue
            number = int(value)
        elif isinstance(value, int):
            number = value
        else:
            text = str(value).strip()
            if not text:
                continue
            try:
                number = int(text)
            except (TypeError, ValueError):
                continue
        if number == 0 or number in cleaned:
            continue
        cleaned.append(number)
        if len(cleaned) >= 32:
            break
    return cleaned


@dataclass
class AppConfig:
    hotkey: str = DEFAULT_HOTKEY
    enabled: bool = True
    wake_word_enabled: bool = True
    wake_phrases: list[str] = field(default_factory=lambda: list(DEFAULT_WAKE_PHRASES))
    microphone_index: int | None = None
    mic_gain: float = 3.0
    mic_energy_threshold: float = 0.012
    start_with_windows: bool = False
    fast_intent: bool = True
    max_new_tokens: int = 128
    threads: int = 4
    overlay_accent: str = "#8B7CFF"
    overlay_width: int = 680
    acrylic_blur: bool = True
    show_on_start: bool = True
    first_run_done: bool = False
    log_transcripts: bool = False
    confirm_destructive: bool = True
    allow_power_actions: bool = True
    allow_file_access: bool = True
    allow_any_path: bool = False
    allow_shell: bool = False
    telegram_enabled: bool = False
    telegram_bot_token: str = ""
    telegram_allowed_chat_ids: list[int] = field(default_factory=list)

    def validate(self) -> "AppConfig":
        self.hotkey = normalize_hotkey(self.hotkey)
        self.wake_phrases = normalize_wake_phrases(self.wake_phrases)
        if self.wake_word_enabled and not self.wake_phrases:
            self.wake_phrases = list(DEFAULT_WAKE_PHRASES)
        self.telegram_enabled = bool(self.telegram_enabled)
        self.confirm_destructive = bool(self.confirm_destructive)
        self.allow_power_actions = bool(self.allow_power_actions)
        self.allow_file_access = bool(self.allow_file_access)
        self.allow_any_path = bool(self.allow_any_path)
        self.allow_shell = bool(self.allow_shell)
        self.telegram_bot_token = str(self.telegram_bot_token or "").strip()[:512]
        self.telegram_allowed_chat_ids = normalize_chat_ids(self.telegram_allowed_chat_ids)
        if self.microphone_index is not None:
            try:
                self.microphone_index = int(self.microphone_index)
            except (TypeError, ValueError):
                self.microphone_index = None
            if self.microphone_index is not None and self.microphone_index < 0:
                self.microphone_index = None
        try:
            self.mic_gain = max(1.0, min(10.0, float(self.mic_gain)))
        except (TypeError, ValueError):
            self.mic_gain = 3.0
        try:
            self.mic_energy_threshold = max(0.003, min(0.10, float(self.mic_energy_threshold)))
        except (TypeError, ValueError):
            self.mic_energy_threshold = 0.012
        self.max_new_tokens = max(32, min(512, int(self.max_new_tokens)))
        self.threads = max(0, min(64, int(self.threads)))
        self.overlay_width = max(520, min(1000, int(self.overlay_width)))
        if not re.fullmatch(r"#[0-9a-fA-F]{6}", self.overlay_accent or ""):
            self.overlay_accent = "#8B7CFF"
        return self

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "AppConfig":
        if not isinstance(data, dict):
            return cls()
        known = {item.name for item in fields(cls)}
        values = {key: value for key, value in data.items() if key in known}
        config = cls(**values)
        return config.validate()


def load_config(path: Path | None = None) -> AppConfig:
    target = path or config_path()
    try:
        with open(target, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        config = AppConfig.from_dict(data)
        # Older installs defaulted to Jarvis/Computer only.  Add the
        # product's own wake phrase so "Hey Needle" works out of the box.
        if config.wake_phrases == ["hey jarvis", "hey computer"]:
            config.wake_phrases = list(DEFAULT_WAKE_PHRASES)
            try:
                save_config(config, target)
            except OSError:
                pass
        return config
    except FileNotFoundError:
        return AppConfig()
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return AppConfig()


def save_config(config: AppConfig, path: Path | None = None) -> Path:
    target = path or config_path()
    config.validate()
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".needle-", suffix=".json", dir=str(target.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(config.to_dict(), handle, indent=2, ensure_ascii=False)
            handle.write("\n")
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return target
