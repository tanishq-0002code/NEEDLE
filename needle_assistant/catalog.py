from __future__ import annotations

import re
from typing import Any

from .models import ToolCall

# Keep the catalog short and explicit: Needle 3 pays for every schema token.
TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "name": "open_application",
        "description": "Launch application",
        "triggers": [r"^(?:please\s+)?(?:open|launch|start|run)\b"],
        "parameters": {
            "type": "object",
            "properties": {"app_name": {"type": "string"}},
            "required": ["app_name"],
        },
    },
    {
        "name": "type_text",
        "description": "Type text",
        "triggers": [r"^(?:please\s+)?(?:type|write|enter|dictate)\b"],
        "parameters": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
    },
    {
        "name": "click_element",
        "description": "Click element",
        "triggers": [r"^(?:please\s+)?(?:click|tap)\b"],
        "parameters": {
            "type": "object",
            "properties": {"target": {"type": "string"}},
            "required": ["target"],
        },
    },
    {
        "name": "press_hotkey",
        "description": "Press keys",
        "triggers": [
            r"^(?:please\s+)?(?:press|hit)\b",
            r"\b(?:copy|paste|cut|undo|redo|save|select all|select everything|"
            r"switch windows?|close window|"
            r"new tab|close tab|reload|refresh|find|print|task manager|"
            r"lock (?:computer|screen))\b",
        ],
        "parameters": {
            "type": "object",
            "properties": {
                "keys": {
                    "type": "array",
                    "items": {"type": "string"},
                }
            },
            "required": ["keys"],
        },
    },
    {
        "name": "take_screenshot",
        "description": "Capture screen",
        "triggers": [r"\b(?:screenshot|screen shot|capture (?:the )?screen|grab (?:the )?screen)\b"],
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "control_media",
        "description": "Control media",
        "triggers": [
            r"\b(?:play|pause|resume|next|skip|previous|mute|unmute)\b"
            r"(?:\s+(?:the\s+)?(?:music|song|track|media|video|audio|playback))?"
        ],
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["play", "pause", "next", "previous", "mute"],
                }
            },
            "required": ["action"],
        },
    },
]

SYSTEM_PROMPT = "device: Windows desktop"

ALLOWED_TOOLS = frozenset(schema["name"] for schema in TOOL_SCHEMAS)

_MODIFIERS = {"ctrl", "alt", "shift", "win"}
_KEY_ALIASES = {
    "control": "ctrl",
    "cmd": "win",
    "command": "win",
    "windows": "win",
    "super": "win",
    "meta": "win",
    "escape": "esc",
    "return": "enter",
    "del": "delete",
    "ins": "insert",
    "page_up": "pageup",
    "page_down": "pagedown",
    "plus": "+",
    "minus": "-",
}
_KEYS = {
    "ctrl", "alt", "shift", "win", "space", "enter", "tab", "esc", "backspace",
    "delete", "insert", "home", "end", "pageup", "pagedown", "up", "down", "left",
    "right", "capslock", "printscreen", "pause", "menu", "plus", "minus",
    *(f"f{i}" for i in range(1, 25)),
}
_DANGEROUS_COMBOS = {
    frozenset({"ctrl", "alt", "delete"}),
    frozenset({"ctrl", "alt", "del"}),
}

_OPEN_RE = re.compile(r"^(?:please\s+)?(?:open|launch|start|run)\s+(.+?)[.!?]*$", re.I)
_TYPE_RE = re.compile(r"^(?:please\s+)?(?:type|write|enter|dictate)\s+(.+?)[.!?]*$", re.I)
_CLICK_RE = re.compile(r"^(?:please\s+)?(?:click|tap)\s+(.+?)[.!?]*$", re.I)
_PRESS_RE = re.compile(r"^(?:please\s+)?(?:press|hit)\s+(.+?)[.!?]*$", re.I)
_MEDIA_RE = re.compile(
    r"^(?:please\s+)?(play|pause|resume|next|skip|previous|prev|mute|unmute|stop)"
    r"(?:\s+(?:the\s+)?(?:music|song|track|media|video|audio|playback))?[.!?]*$",
    re.I,
)
_SCREENSHOT_RE = re.compile(
    r"^(?:please\s+)?(?:take|grab|capture)?\s*(?:a\s+)?screenshot"
    r"(?:\s+of\s+(?:the\s+)?(?:screen|display|desktop))?[.!?]*$",
    re.I,
)

_HOTKEY_PHRASES = {
    "copy": ["ctrl", "c"],
    "paste": ["ctrl", "v"],
    "cut": ["ctrl", "x"],
    "undo": ["ctrl", "z"],
    "redo": ["ctrl", "y"],
    "save": ["ctrl", "s"],
    "select all": ["ctrl", "a"],
    "select everything": ["ctrl", "a"],
    "switch window": ["alt", "tab"],
    "switch windows": ["alt", "tab"],
    "next window": ["alt", "tab"],
    "close window": ["alt", "f4"],
    "minimize window": ["win", "down"],
    "maximize window": ["win", "up"],
    "new tab": ["ctrl", "t"],
    "close tab": ["ctrl", "w"],
    "reload page": ["ctrl", "r"],
    "refresh": ["ctrl", "r"],
    "find": ["ctrl", "f"],
    "print": ["ctrl", "p"],
    "task manager": ["ctrl", "shift", "esc"],
    "lock computer": ["win", "l"],
    "lock screen": ["win", "l"],
}

_MEDIA_ACTIONS = {
    "play": "play",
    "resume": "play",
    "pause": "pause",
    "stop": "pause",
    "next": "next",
    "skip": "next",
    "previous": "previous",
    "prev": "previous",
    "mute": "mute",
    "unmute": "mute",
}


def _compact(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def _strip_polite(value: str) -> str:
    text = _compact(value).lower()
    text = re.sub(r"^(please|kindly|can you|could you|would you)\s+", "", text)
    text = re.sub(r"\s+(please|for me)$", "", text)
    return text


def _strip_polite_keepcase(value: str) -> str:
    text = _compact(value)
    text = re.sub(r"^(please|kindly|can you|could you|would you)\s+", "", text, flags=re.I)
    text = re.sub(r"\s+(please|for me)$", "", text, flags=re.I)
    return text


def _clean_app_name(value: str) -> str:
    name = _compact(value).strip(" \t\r\n\"'`.,!?")
    name = re.sub(r"^(the|my)\s+", "", name, flags=re.I)
    name = re.sub(r"\s+(app|application|program)$", "", name, flags=re.I)
    name = re.sub(r"\s+(browser|window)$", "", name, flags=re.I)
    return name.strip()


def normalize_keys(keys: Any) -> list[str]:
    if isinstance(keys, str):
        raw = keys.strip()
        values = raw.split("+") if "+" in raw else raw.split()
        if len(values) == 1 and values[0] and len(values[0]) > 1:
            values = [values[0]]
    elif isinstance(keys, (list, tuple)):
        values = list(keys)
    else:
        values = []
    normalized: list[str] = []
    for value in values:
        key = str(value).strip().lower().replace("<", "").replace(">", "")
        key = _KEY_ALIASES.get(key, key)
        if not key:
            continue
        if key not in _KEYS and not (len(key) == 1 and key.isprintable()):
            raise ValueError(f"Unsupported key: {value!r}")
        if key not in normalized:
            normalized.append(key)
    if not normalized:
        raise ValueError("A hotkey needs at least one key.")
    if len(normalized) > 4:
        raise ValueError("A hotkey can contain at most four keys.")
    non_modifiers = [key for key in normalized if key not in _MODIFIERS]
    if len(non_modifiers) != 1:
        raise ValueError("A hotkey needs exactly one non-modifier key.")
    if frozenset(normalized) in _DANGEROUS_COMBOS:
        raise ValueError("That system shortcut is blocked for safety.")
    return normalized


def _clean_click_target(value: str) -> str:
    target = _compact(value)
    target = re.sub(r"^(?:the|a|an)\s+", "", target, flags=re.I)
    target = re.sub(r"\s+(?:button|link|menu item|tab|icon)$", "", target, flags=re.I)
    return target.strip()


def _hotkey_from_text(text: str) -> list[str] | None:
    cleaned = _strip_polite(text)
    phrase = re.sub(r"\s+(this|that|it|the file|the document|the page|the window)$", "", cleaned)
    if phrase in _HOTKEY_PHRASES:
        return list(_HOTKEY_PHRASES[phrase])
    for known, keys in _HOTKEY_PHRASES.items():
        if re.search(rf"\b{re.escape(known)}\b", cleaned):
            return list(keys)
    match = re.search(
        r"\b((?:ctrl|alt|shift|win)(?:\s*\+\s*(?:ctrl|alt|shift|win))*\s*\+\s*[a-z0-9]+)\b",
        cleaned,
    )
    if match:
        try:
            return normalize_keys(match.group(1))
        except ValueError:
            return None
    return None


def _type_text_from_request(text: str) -> str | None:
    match = re.match(
        r"^(?:please\s+)?(?:type|write|enter|dictate)\s+(.+?)(?:\s+(?:into|in|on)\s+(?:the\s+)?(.+))?[.!?]*$",
        _strip_polite_keepcase(text),
        re.I,
    )
    if not match:
        return None
    candidate = match.group(1).strip().strip("\"'“”‘’")
    candidate = re.sub(r"^(?:the\s+word|the\s+text|the\s+phrase)\s+", "", candidate, flags=re.I)
    if not candidate:
        return None
    locations = {
        "search box", "search bar", "search field", "text box", "text field",
        "input", "input field", "address bar", "url bar", "message box",
        "chat box", "compose box", "editor", "document",
    }
    if candidate.lower() in locations:
        return None
    return candidate[:5000]


def _open_app_from_request(text: str) -> str | None:
    match = re.match(
        r"^(?:please\s+)?(?:open|launch|start|run)\s+(.+?)[.!?]*$",
        _strip_polite_keepcase(text),
        re.I,
    )
    if not match:
        return None
    name = _clean_app_name(match.group(1))
    return name or None


def validate_call(name: str, arguments: dict[str, Any] | None,
                  user_text: str = "") -> tuple[str, dict[str, Any]]:
    if isinstance(arguments, str):
        try:
            import json as _json

            arguments = _json.loads(arguments)
        except Exception:
            arguments = {}
    if not isinstance(arguments, dict):
        arguments = {}
    arguments = dict(arguments)
    if name not in ALLOWED_TOOLS:
        raise ValueError(f"Unknown tool: {name!r}")
    if name == "open_application":
        app_name = _clean_app_name(str(arguments.get("app_name", "")))
        if not app_name:
            app_name = _open_app_from_request(user_text) or ""
        if not app_name:
            raise ValueError("An application name is required.")
        if len(app_name) > 120:
            raise ValueError("The application name is too long.")
        if re.search(r"[\\/:;|<>`$%^\x00-\x1f]", app_name):
            raise ValueError("The application name contains unsupported characters.")
        return name, {"app_name": app_name}
    if name == "type_text":
        text = str(arguments.get("text", ""))
        repaired = _type_text_from_request(user_text) if user_text else None
        if repaired:
            text = repaired
        if not text:
            raise ValueError("There is no text to type.")
        if "\x00" in text:
            raise ValueError("The text contains an invalid character.")
        return name, {"text": text[:5000]}
    if name == "click_element":
        target = _clean_click_target(str(arguments.get("target", "")))
        if not target:
            raise ValueError("A click target is required.")
        if len(target) > 200 or re.search(r"[\x00-\x1f]", target):
            raise ValueError("The click target is invalid.")
        return name, {"target": target}
    if name == "press_hotkey":
        raw_keys = arguments.get("keys", [])
        try:
            return name, {"keys": normalize_keys(raw_keys)}
        except ValueError:
            repaired = _hotkey_from_text(user_text) if user_text else None
            if repaired:
                return name, {"keys": repaired}
            raise
    if name == "take_screenshot":
        return name, {}
    if name == "control_media":
        action = str(arguments.get("action", "")).strip().lower()
        if action not in _MEDIA_ACTIONS:
            media = _MEDIA_RE.fullmatch(_strip_polite(user_text)) if user_text else None
            if media:
                action = media.group(1).lower()
        if action not in _MEDIA_ACTIONS:
            raise ValueError(f"Unsupported media action: {action!r}")
        return name, {"action": _MEDIA_ACTIONS[action]}
    raise ValueError(f"Unknown tool: {name!r}")


def fast_route(text: str) -> ToolCall | None:
    """A conservative, deterministic shortcut for the most common commands.

    Needle 3 remains the semantic router for everything that is not an exact,
    unambiguous match here. This keeps the assistant responsive on slow CPUs.
    """
    cleaned = _strip_polite(text)
    original = _strip_polite_keepcase(text)
    if not cleaned:
        return None

    if _SCREENSHOT_RE.fullmatch(cleaned):
        return ToolCall(name="take_screenshot", arguments={}, source="fast", confidence=1.0,
                        reasoning="Matched screenshot phrase.", user_text=text)

    media = _MEDIA_RE.fullmatch(cleaned)
    if media:
        action = _MEDIA_ACTIONS[media.group(1).lower()]
        return ToolCall(name="control_media", arguments={"action": action}, source="fast",
                        confidence=1.0, reasoning="Matched media phrase.", user_text=text)

    hotkey_phrase = re.sub(r"\s+(this|that|it|the file|the document)$", "", cleaned)
    if hotkey_phrase in _HOTKEY_PHRASES:
        return ToolCall(name="press_hotkey", arguments={"keys": list(_HOTKEY_PHRASES[hotkey_phrase])},
                        source="fast", confidence=1.0, reasoning="Matched shortcut phrase.",
                        user_text=text)

    press = _PRESS_RE.fullmatch(cleaned)
    if press:
        candidate = press.group(1).strip()
        if candidate in _HOTKEY_PHRASES:
            return ToolCall(name="press_hotkey", arguments={"keys": list(_HOTKEY_PHRASES[candidate])},
                            source="fast", confidence=1.0, reasoning="Matched shortcut phrase.",
                            user_text=text)
        if re.fullmatch(r"(?:ctrl|alt|shift|win)(?:\+(?:ctrl|alt|shift|win))*\+[a-z0-9]+", candidate):
            try:
                keys = normalize_keys(candidate)
            except ValueError:
                keys = []
            if keys:
                return ToolCall(name="press_hotkey", arguments={"keys": keys}, source="fast",
                                confidence=1.0, reasoning="Matched key chord.", user_text=text)

    opened = _OPEN_RE.fullmatch(original)
    if opened:
        app_name = _open_app_from_request(text)
        if app_name and len(app_name) <= 120:
            try:
                name, arguments = validate_call("open_application", {"app_name": app_name})
            except ValueError:
                return None
            return ToolCall(name=name, arguments=arguments, source="fast", confidence=1.0,
                            reasoning="Matched open-app phrase.", user_text=text)

    typed = _TYPE_RE.fullmatch(original)
    if typed:
        text_to_type = _type_text_from_request(text)
        if text_to_type:
            try:
                name, arguments = validate_call("type_text", {"text": text_to_type})
            except ValueError:
                return None
            return ToolCall(name=name, arguments=arguments, source="fast", confidence=1.0,
                            reasoning="Matched type-text phrase.", user_text=text)

    clicked = _CLICK_RE.fullmatch(original)
    if clicked:
        target = _clean_click_target(clicked.group(1))
        if target and len(target) <= 200:
            return ToolCall(name="click_element", arguments={"target": target}, source="fast",
                            confidence=1.0, reasoning="Matched click phrase.", user_text=text)

    return None


def badge_text(call: ToolCall) -> str:
    if not call.name:
        return ""
    name = call.name.replace("_", " ").upper()
    detail = ""
    if call.name == "open_application":
        detail = str(call.arguments.get("app_name", ""))
    elif call.name == "type_text":
        detail = str(call.arguments.get("text", ""))
    elif call.name == "click_element":
        detail = str(call.arguments.get("target", ""))
    elif call.name == "press_hotkey":
        detail = "+".join(str(k) for k in call.arguments.get("keys", []))
    elif call.name == "control_media":
        detail = str(call.arguments.get("action", ""))
    detail = _compact(detail)
    if len(detail) > 38:
        detail = detail[:35] + "..."
    return f"{name}  {detail}".strip()


def describe_call(call: ToolCall) -> str:
    if not call.name:
        return call.error or "I couldn't map that to an action."
    if call.name == "open_application":
        return f"Opening {call.arguments.get('app_name', 'the app')}"
    if call.name == "type_text":
        text = str(call.arguments.get("text", ""))
        short = text if len(text) <= 60 else text[:57] + "..."
        return f"Typing \"{short}\""
    if call.name == "click_element":
        return f"Clicking {call.arguments.get('target', 'the element')}"
    if call.name == "press_hotkey":
        return "Pressing " + "+".join(str(k) for k in call.arguments.get("keys", []))
    if call.name == "take_screenshot":
        return "Taking a screenshot"
    if call.name == "control_media":
        return f"Media: {call.arguments.get('action', 'action')}"
    return "Running the action"
