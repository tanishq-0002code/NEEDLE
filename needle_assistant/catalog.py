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
        "name": "close_application",
        "description": "Close or quit an application that is already running",
        "triggers": [r"^(?:please\s+)?(?:close|quit|exit)\b"],
        "parameters": {
            "type": "object",
            "properties": {
                "app_name": {"type": "string"},
                "force": {"type": "boolean"},
            },
            "required": ["app_name"],
        },
    },
    {
        "name": "type_text",
        "description": "Type text",
        "triggers": [r"^(?:please\s+)?(?:type|write|enter|dictate)\b"],
        "parameters": {
            "type": "object",
            "properties": {
                "text": {"type": "string"},
                "window": {"type": "string"},
            },
            "required": ["text"],
        },
    },
    {
        "name": "click_element",
        "description": "Click element",
        "triggers": [r"^(?:please\s+)?(?:click|tap)\b"],
        "parameters": {
            "type": "object",
            "properties": {
                "target": {"type": "string"},
                "window": {"type": "string"},
            },
            "required": ["target"],
        },
    },
    {
        "name": "focus_window",
        "description": "Bring a specific window to the front by its title",
        "triggers": [r"^(?:please\s+)?(?:switch to|focus|bring up|go to)\b"],
        "parameters": {
            "type": "object",
            "properties": {"title": {"type": "string"}},
            "required": ["title"],
        },
    },
    {
        "name": "window_manage",
        "description": "Minimize, maximize, or restore a window",
        "triggers": [r"^(?:please\s+)?(?:minimi[sz]e|maximi[sz]e|restore|unmini[sz]e)\b"],
        "parameters": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "action": {"type": "string", "enum": ["minimize", "maximize", "restore"]},
            },
            "required": ["title", "action"],
        },
    },
    {
        "name": "system_control",
        "description": "Lock, shut down, restart, sleep, or hibernate the PC",
        "triggers": [
            r"\b(?:shut\s*down|shutdown|turn\s*off|power\s*off|restart|reboot|"
            r"sleep|hibernate|lock)\b"
        ],
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["lock", "shutdown", "restart", "sleep", "hibernate"],
                }
            },
            "required": ["action"],
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
    {
        "name": "read_screen",
        "description": "Read text from the screen using offline OCR",
        "triggers": [
            r"^(?:please\s+)?(?:read|extract|ocr)\s+(?:the\s+)?(?:screen|display|window)\b",
            r"\bwhat(?:'s|\s+is)\s+on\s+(?:my\s+|the\s+)?(?:screen|display)\b",
        ],
        "parameters": {
            "type": "object",
            "properties": {
                "scope": {"type": "string", "enum": ["foreground", "full"]},
            },
        },
    },
    {
        "name": "clipboard",
        "description": "Get or set the Windows clipboard",
        "triggers": [r"\bclipboard\b"],
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["get", "set"]},
                "text": {"type": "string"},
            },
            "required": ["action"],
        },
    },
    {
        "name": "file_operation",
        "description": "Create, read, append, list, rename, move, or delete a file or folder",
        "triggers": [r"\b(?:file|folder|directory)\b"],
        "parameters": {
            "type": "object",
            "properties": {
                "op": {
                    "type": "string",
                    "enum": ["create", "read", "append", "list", "rename", "move", "delete"],
                },
                "path": {"type": "string"},
                "new_path": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["op", "path"],
        },
    },
    {
        "name": "run_plan",
        "description": "Run several desktop commands in order",
        "triggers": [r"^(?:please\s+)?(?:run|do)\s+(?:these|the following|a plan|steps)\b"],
        "parameters": {
            "type": "object",
            "properties": {
                "steps": {"type": "string"},
            },
            "required": ["steps"],
        },
    },
    {
        "name": "run_command",
        "description": "Run a shell command and return its output",
        "triggers": [r"^(?:please\s+)?(?:run|execute|shell)\b"],
        "parameters": {
            "type": "object",
            "properties": {
                "command": {"type": "string"},
                "timeout": {"type": "integer"},
            },
            "required": ["command"],
        },
    },
    {
        "name": "browser_control",
        "description": "Open a URL or control browser tabs",
        "triggers": [
            r"\b(?:open (?:the )?(?:url|website|page|link)|go to (?:the )?(?:url|website|page|link))\b",
            r"\b(?:new tab|close tab|next tab|previous tab)\b",
        ],
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["open_url", "tab_next", "tab_prev", "tab_close", "tab_new", "read_page", "click_first_result", "scroll_up", "scroll_down"],
                },
                "url": {"type": "string"},
                "browser": {"type": "string"},
                "target": {"type": "string"},
                "amount": {"type": "integer"},
            },
            "required": ["action"],
        },
    },
    {
        "name": "memory",
        "description": "Remember an app alias, a phone contact, or a preference",
        "triggers": [r"\b(?:remember|alias|preference|contact)\b"],
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["set_alias", "delete_alias", "list_aliases", "set_preference", "set_contact", "delete_contact", "list_contacts"]},
                "alias": {"type": "string"},
                "target": {"type": "string"},
                "key": {"type": "string"},
                "value": {"type": "string"},
            },
            "required": ["action"],
        },
    },
    {
        "name": "schedule",
        "description": "Schedule a reminder or recurring task",
        "triggers": [r"\b(?:remind|reminder|schedule|timer)\b"],
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["add", "list", "cancel"]},
                "minutes": {"type": "number"},
                "message": {"type": "string"},
                "id": {"type": "integer"},
                "recurring": {"type": "boolean"},
            },
            "required": ["action"],
        },
    },
    {
        "name": "macro",
        "description": "Save or run a named macro (sequence of commands)",
        "triggers": [r"\b(?:macro|routine)\b"],
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["save", "run", "list", "delete"]},
                "name": {"type": "string"},
                "steps": {"type": "string"},
            },
            "required": ["action"],
        },
    },
    {
        "name": "shortcut",
        "description": "Save, delete, or list trigger-phrase shortcuts",
        "triggers": [r"^(?:please\s+)?(?:shortcut|remember shortcut|save shortcut)\b"],
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["save", "delete", "list"]},
                "trigger": {"type": "string"},
                "steps": {"type": "string"},
            },
            "required": ["action"],
        },
    },
    {
        "name": "send_message",
        "description": "Send a WhatsApp message to a saved contact or phone number. "
                       "If no text is given, the saved draft is used.",
        "triggers": [
            r"\bwhatsapp\b",
            r"\b(?:send|message|text|tell)\b.*\b(?:message|msg|text)\b",
        ],
        "parameters": {
            "type": "object",
            "properties": {
                "recipient": {"type": "string"},
                "text": {"type": "string"},
            },
            "required": ["recipient"],
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
    r"^(?:please\s+)?(?:(?:take|grab|capture)?\s*(?:a\s+)?screenshot"
    r"|(?:see|look at|view|show|inspect)\s+(?:my\s+|the\s+)?(?:screen|display|desktop)"
    r"|what(?:'s|\s+is)\s+on\s+(?:my\s+|the\s+)?(?:screen|display|desktop)"
    r"|capture\s+(?:the\s+)?(?:screen|display|desktop))"
    r"(?:\s+of\s+(?:the\s+)?(?:screen|display|desktop))?[.!?]*$",
    re.I,
)
_CURSOR_MOVE_RE = re.compile(
    r"^(?:please\s+)?(?:move\s+(?:the\s+)?(?:cursor|mouse)\s+(?:to\s+)?|hover\s+(?:over\s+)?)(.+?)[.!?]*$",
    re.I,
)
_RIGHT_CLICK_RE = re.compile(
    r"^(?:please\s+)?(?:right[\s-]click|do\s+a\s+right[\s-]click)(?:\s+(?:(?:on|at)\s+)?(.+?))?[.!?]*$",
    re.I,
)
_DOUBLE_CLICK_RE = re.compile(
    r"^(?:please\s+)?(?:double[\s-]click|do\s+a\s+double[\s-]click)(?:\s+(?:(?:on|at)\s+)?(.+?))?[.!?]*$",
    re.I,
)
_SCROLL_RE = re.compile(
    r"^(?:please\s+)?scroll\s+(up|down)(?:\s+(\d+))?[.!?]*$",
    re.I,
)
_CLOSE_RE = re.compile(
    r"^(?:please\s+)?(?:close|quit|exit)\s+(.+?)[.!?]*$",
    re.I,
)
_FOCUS_RE = re.compile(
    r"^(?:please\s+)?(?:switch to|focus(?:\s+on)?|bring up|go to)\s+(.+?)[.!?]*$",
    re.I,
)
_WINDOW_MANAGE_RE = re.compile(
    r"^(?:please\s+)?(minimi[sz]e|maximi[sz]e|restore|unmini[sz]e)\s+(.+?)[.!?]*$",
    re.I,
)
_SYSTEM_VERB = r"(shut\s*down|shutdown|turn\s*off|power\s*off|restart|reboot|sleep|hibernate|lock)"
_SYSTEM_RE = re.compile(
    rf"^(?:please\s+)?{_SYSTEM_VERB}(?:\s+(?:my|the))?"
    rf"(?:\s+(?:computer|pc|machine|laptop|screen|display))?[.!?]*$",
    re.I,
)
_SLEEP_RE = re.compile(
    r"^(?:please\s+)?(?:put\s+(?:the\s+)?(?:computer|pc|machine|laptop)\s+to\s+sleep"
    r"|sleep(?:\s+(?:the\s+)?(?:computer|pc|machine|laptop))?"
    r"|go\s+to\s+sleep)[.!?]*$",
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


_GENERIC_WINDOW_TARGETS = {"window", "it", "this", "that", "app", "current window"}
_SYSTEM_VERBS = {
    "shut down": "shutdown",
    "shutdown": "shutdown",
    "turn off": "shutdown",
    "power off": "shutdown",
    "restart": "restart",
    "reboot": "restart",
    "lock": "lock",
    "sleep": "sleep",
    "hibernate": "hibernate",
}
_SYSTEM_ACTIONS = frozenset(_SYSTEM_VERBS.values())
_WINDOW_ACTIONS = {
    "minimize": "minimize",
    "minimise": "minimize",
    "maximize": "maximize",
    "maximise": "maximize",
    "restore": "restore",
    "unminimize": "restore",
    "unminimise": "restore",
}


def _clean_window_title(value: str) -> str:
    title = _compact(value).strip("\"'`")
    title = re.sub(r"^(?:the|my)\s+", "", title, flags=re.I)
    title = re.sub(r"\s+(?:window|app|application)$", "", title, flags=re.I)
    return title.strip()


def normalize_system_action(value: Any, user_text: str = "") -> str:
    raw = re.sub(r"\s+", " ", str(value or "").strip().lower())
    if raw in _SYSTEM_VERBS:
        return _SYSTEM_VERBS[raw]
    if user_text:
        match = _SYSTEM_RE.fullmatch(_strip_polite(user_text))
        if match:
            verb = re.sub(r"\s+", " ", match.group(1).lower())
            return _SYSTEM_VERBS.get(verb, "")
    return ""


def normalize_window_action(value: Any) -> str:
    raw = str(value or "").strip().lower()
    return _WINDOW_ACTIONS.get(raw, "")


def _close_app_from_request(text: str) -> str | None:
    match = _CLOSE_RE.fullmatch(_strip_polite_keepcase(text))
    if not match:
        return None
    name = _clean_app_name(match.group(1))
    return name or None


def _window_manage_from_request(text: str) -> tuple[str, str] | None:
    match = _WINDOW_MANAGE_RE.fullmatch(_strip_polite_keepcase(text))
    if not match:
        return None
    action = normalize_window_action(match.group(1))
    title = _clean_window_title(match.group(2))
    if not action or not title:
        return None
    return action, title


def _focus_from_request(text: str) -> str | None:
    match = _FOCUS_RE.fullmatch(_strip_polite_keepcase(text))
    if not match:
        return None
    title = _clean_window_title(match.group(1))
    return title or None


def _shortcut_from_request(text: str) -> tuple[str, str] | None:
    cleaned = _strip_polite_keepcase(text)
    # Forms: "shortcut <trigger> = <steps>", "shortcut <trigger>: <steps>",
    # "remember shortcut <trigger> -> <steps>".
    match = re.search(
        r"(?:shortcut|remember shortcut|save shortcut)\s+(.+?)\s*(?:=|:|->|→)\s*(.+)$",
        cleaned,
        re.I,
    )
    if not match:
        return None
    trigger = match.group(1).strip().strip("\"'")
    steps = match.group(2).strip().strip("\"'")
    if not trigger or not steps:
        return None
    return trigger, steps


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


_WA_TEXT_PATTERNS = [
    # "send a whatsapp message to rahul saying I'm late" /
    # "send a message to rahul that says ..." / "send a message to rahul: ..."
    r"^(?:send|write|compose)\s+(?:an?\s+)?(?:whatsapp\s+)?(?:message|msg|text)\s+to\s+(.+?)\s+(?:saying|that\s+says|with\s+(?:the\s+)?(?:message|text)|:)\s*(.+)$",
    # "send rahul a whatsapp message saying ..." / "message rahul a whatsapp message: ..."
    r"^(?:send|message|text|whatsapp)\s+(.+?)\s+(?:an?\s+)?whatsapp\s+(?:message|msg|text)\s+(?:saying|that\s+says|:)\s*(.+)$",
    # "whatsapp rahul saying ..." / "whatsapp rahul: ..." / "send whatsapp to rahul: ..."
    r"^(?:send\s+)?whatsapp\s+(?:to\s+)?(.+?)\s*(?::\s*|\s+saying\s+|\s+that\s+says\s+)\s*(.+)$",
    # "tell rahul on whatsapp I'm late" / "tell rahul on whatsapp that ..."
    r"^tell\s+(.+?)\s+on\s+whatsapp\s+(?:that\s+|saying\s+)?(.+)$",
    # "message rahul saying ..." / "message rahul on whatsapp saying ..." / "message rahul: ..."
    r"^message\s+(.+?)\s+(?:on\s+whatsapp\s+)?(?:saying|that\s+says|:)\s*(.+)$",
    # "send rahul a message saying ..." / "send rahul message: ..."
    r"^send\s+(.+?)\s+(?:an?\s+)?(?:whatsapp\s+)?(?:message|msg|text)\s+(?:saying|that\s+says|:)\s*(.+)$",
    # "text rahul saying ..." / "text rahul ..." with explicit separator
    r"^(?:text|message)\s+(.+?)\s+saying\s+(.+)$",
]

_WA_DRAFT_PATTERNS = [
    # "send this message to rahul (on whatsapp)" / "send the message to rahul" / "send that message to rahul"
    r"^(?:send|deliver)\s+(?:this|the|that)\s+(?:message|msg|text)\s+to\s+(.+?)(?:\s+on\s+whatsapp)?[.!?]*$",
    # "send this to rahul (on whatsapp)"
    r"^(?:send|deliver)\s+this\s+to\s+(.+?)(?:\s+on\s+whatsapp)?[.!?]*$",
    # "send a (whatsapp) message to rahul" with no explicit text -> saved draft
    r"^(?:send|write|compose)\s+(?:an?\s+)?(?:whatsapp\s+)?(?:message|msg|text)\s+to\s+(.+?)(?:\s+on\s+whatsapp)?[.!?]*$",
    # "tell rahul on whatsapp" -> draft
    r"^tell\s+(.+?)\s+on\s+whatsapp[.!?]*$",
]


def _strip_whatsapp_suffix(value: str) -> str:
    result = _compact(value).strip(" \"'“”‘’")
    result = re.sub(r"^(?:to|the)\s+", "", result, flags=re.I)
    result = re.sub(r"\s+(?:on|in)\s+(?:whatsapp|wa)$", "", result, flags=re.I)
    return result.strip()


def _whatsapp_call(text: str, recipient: str, message: str) -> ToolCall:
    try:
        name, arguments = validate_call("send_message", {"recipient": recipient, "text": message}, text)
    except ValueError as exc:
        return ToolCall(name=None, user_text=text, source="fast",
                        error=str(exc), reasoning=str(exc))
    return ToolCall(name=name, arguments=arguments, source="fast", confidence=1.0,
                    reasoning="Matched WhatsApp-message phrase.", user_text=text)


def _send_message_from_request(text: str) -> ToolCall | None:
    original = _strip_polite_keepcase(text)
    if not original:
        return None
    for pattern in _WA_TEXT_PATTERNS:
        match = re.match(pattern, original, re.I)
        if not match:
            continue
        recipient = _strip_whatsapp_suffix(match.group(1))
        message = match.group(2).strip().strip("\"'“”‘’")
        if recipient and message:
            return _whatsapp_call(text, recipient, message)
    for pattern in _WA_DRAFT_PATTERNS:
        match = re.match(pattern, original, re.I)
        if not match:
            continue
        recipient = _strip_whatsapp_suffix(match.group(1))
        if recipient:
            # Text resolved at execution time: the saved draft, or a clear error.
            return _whatsapp_call(text, recipient, "")
    return None


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
        window = _clean_window_title(arguments.get("window")) or ""
        result = {"text": text[:5000]}
        if window:
            result["window"] = window[:200]
        return name, result
    if name == "click_element":
        target = _clean_click_target(str(arguments.get("target", "")))
        if not target:
            raise ValueError("A click target is required.")
        if len(target) > 200 or re.search(r"[\x00-\x1f]", target):
            raise ValueError("The click target is invalid.")
        window = _clean_window_title(arguments.get("window")) or ""
        result = {"target": target}
        if window:
            result["window"] = window[:200]
        return name, result
    if name == "close_application":
        app_name = _clean_app_name(str(arguments.get("app_name", "")))
        if not app_name:
            app_name = _close_app_from_request(user_text) or ""
        app_name = re.sub(r"\s+(?:please|for me)$", "", app_name, flags=re.I).strip()
        if not app_name:
            raise ValueError("Which app should I close?")
        lowered = app_name.lower()
        if lowered in _GENERIC_WINDOW_TARGETS or lowered in {"window", "tab"}:
            raise ValueError(
                "Name the app to close (for example: close Chrome), or say 'close window'."
            )
        if len(app_name) > 120:
            raise ValueError("The application name is too long.")
        if re.search(r"[\\/:;|<>`$%^\x00-\x1f]", app_name):
            raise ValueError("The application name contains unsupported characters.")
        force = bool(arguments.get("force", False))
        return name, {"app_name": app_name, "force": force}
    if name == "focus_window":
        title = _clean_window_title(str(arguments.get("title", "")))
        if not title and user_text:
            title = _focus_from_request(user_text) or ""
        if not title:
            raise ValueError("Which window should I switch to?")
        if title.lower() in _GENERIC_WINDOW_TARGETS:
            raise ValueError("Tell me which window by name, for example: switch to Notepad.")
        if len(title) > 200 or re.search(r"[\x00-\x1f]", title):
            raise ValueError("The window title is invalid.")
        return name, {"title": title}
    if name == "window_manage":
        action = normalize_window_action(arguments.get("action", ""))
        if not action and user_text:
            repaired = _window_manage_from_request(user_text)
            if repaired:
                action = repaired[0]
        if not action:
            raise ValueError("Use minimize, maximize, or restore.")
        title = _clean_window_title(str(arguments.get("title", "")))
        if not title and user_text:
            repaired = _window_manage_from_request(user_text)
            if repaired:
                title = repaired[1]
        if not title:
            raise ValueError("Which window should I change?")
        if title.lower() in _GENERIC_WINDOW_TARGETS:
            raise ValueError("Tell me which window by name, for example: minimize Notepad.")
        if len(title) > 200 or re.search(r"[\x00-\x1f]", title):
            raise ValueError("The window title is invalid.")
        return name, {"title": title, "action": action}
    if name == "system_control":
        action = normalize_system_action(arguments.get("action", ""), user_text)
        if not action:
            raise ValueError(
                "Use lock, shutdown, restart, sleep, or hibernate."
            )
        return name, {"action": action}
    if name == "read_screen":
        scope = str(arguments.get("scope", "foreground")).strip().lower()
        if scope not in {"foreground", "full"}:
            scope = "foreground"
        return name, {"scope": scope}
    if name == "clipboard":
        action = str(arguments.get("action", "")).strip().lower()
        if action not in {"get", "set"}:
            action = "get" if not str(arguments.get("text", "")).strip() else "set"
        result = {"action": action}
        text = str(arguments.get("text", ""))
        if action == "set":
            if not text and user_text:
                match = re.search(r"(?:to|with)\s+(.+)$", user_text, re.I)
                if match:
                    text = match.group(1).strip().strip("\"'")
            if not text:
                raise ValueError("There is no text to put on the clipboard.")
            result["text"] = text[:10000]
        return name, result
    if name == "file_operation":
        op = str(arguments.get("op", "")).strip().lower()
        if op not in {"create", "read", "append", "list", "rename", "move", "delete"}:
            raise ValueError("Unsupported file operation.")
        path = str(arguments.get("path", "")).strip()
        if not path:
            raise ValueError("A file path is required.")
        if len(path) > 500:
            raise ValueError("The file path is too long.")
        result = {"op": op, "path": path}
        new_path = str(arguments.get("new_path", "")).strip()
        if new_path:
            result["new_path"] = new_path
        content = arguments.get("content")
        if content is not None:
            result["content"] = str(content)[:200000]
        if op in {"rename", "move"} and not new_path:
            raise ValueError("A destination path is required.")
        return name, result
    if name == "run_plan":
        raw = str(arguments.get("steps", "")).strip()
        if not raw:
            raise ValueError("A step list is required.")
        try:
            import json as _json

            parsed = _json.loads(raw)
        except Exception:
            # Allow newline-separated steps as a fallback.
            parsed = [line.strip() for line in raw.splitlines() if line.strip()]
        if not isinstance(parsed, list) or not parsed:
            raise ValueError("Steps must be a list of commands.")
        steps = [str(item).strip() for item in parsed if str(item).strip()]
        if not steps or len(steps) > 10:
            raise ValueError("A plan needs between 1 and 10 steps.")
        return name, {"steps": steps}
    if name == "run_command":
        command = str(arguments.get("command", "")).strip()
        if not command:
            raise ValueError("A command is required.")
        if len(command) > 2000:
            raise ValueError("The command is too long.")
        timeout = arguments.get("timeout", 15)
        try:
            timeout = int(timeout)
        except (TypeError, ValueError):
            timeout = 15
        timeout = max(1, min(60, timeout))
        return name, {"command": command, "timeout": timeout}
    if name == "browser_control":
        action = str(arguments.get("action", "")).strip().lower()
        if action not in {"open_url", "tab_next", "tab_prev", "tab_close", "tab_new", "read_page", "click_first_result", "scroll_up", "scroll_down"}:
            action = "open_url" if str(arguments.get("url", "")).strip() else "read_page"
        result = {"action": action}
        url = str(arguments.get("url", "")).strip()
        if url:
            result["url"] = url
        browser = str(arguments.get("browser", "")).strip()
        if browser:
            result["browser"] = browser
        target = str(arguments.get("target", "")).strip()
        if target:
            result["target"] = target
        amount = arguments.get("amount")
        if amount is not None:
            try:
                result["amount"] = int(amount)
            except Exception:
                result["amount"] = 5
        if action == "open_url" and not url:
            raise ValueError("A URL is required.")
        return name, result
    if name == "memory":
        action = str(arguments.get("action", "")).strip().lower()
        if action not in {"set_alias", "delete_alias", "list_aliases", "set_preference",
                          "set_contact", "delete_contact", "list_contacts"}:
            action = "list_aliases"
        result = {"action": action}
        if arguments.get("alias"):
            result["alias"] = str(arguments.get("alias")).strip()[:80]
        if arguments.get("target"):
            result["target"] = str(arguments.get("target")).strip()[:120]
        if arguments.get("key"):
            result["key"] = str(arguments.get("key")).strip()[:80]
        if arguments.get("value") is not None:
            result["value"] = str(arguments.get("value"))[:500]
        if action == "set_contact":
            if not result.get("alias") or not result.get("target"):
                raise ValueError("Provide a name and a number, e.g. 'remember rahul as +91 98765 43210'.")
            if not re.search(r"\d{7,}", re.sub(r"\D", "", result["target"])):
                raise ValueError("That does not look like a phone number.")
        if action == "delete_contact" and not result.get("alias"):
            raise ValueError("Which contact should I forget?")
        return name, result
    if name == "schedule":
        action = str(arguments.get("action", "")).strip().lower()
        if action not in {"add", "list", "cancel"}:
            action = "list"
        result = {"action": action}
        if arguments.get("minutes") is not None:
            try:
                result["minutes"] = float(arguments.get("minutes"))
            except (TypeError, ValueError):
                result["minutes"] = 5.0
        if arguments.get("message"):
            result["message"] = str(arguments.get("message")).strip()[:300]
        if arguments.get("id") is not None:
            try:
                result["id"] = int(arguments.get("id"))
            except (TypeError, ValueError):
                pass
        if arguments.get("recurring") is not None:
            result["recurring"] = bool(arguments.get("recurring"))
        if action == "add" and "minutes" not in result:
            result["minutes"] = 5.0
        if action == "add" and "message" not in result:
            result["message"] = "Reminder"
        return name, result
    if name == "macro":
        action = str(arguments.get("action", "")).strip().lower()
        if action not in {"save", "run", "list", "delete"}:
            action = "list"
        result = {"action": action}
        if arguments.get("name"):
            result["name"] = str(arguments.get("name")).strip()[:60]
        if arguments.get("steps") is not None:
            result["steps"] = str(arguments.get("steps"))[:4000]
        if action in {"run", "delete"} and "name" not in result:
            raise ValueError("A macro name is required.")
        if action == "save" and "name" not in result:
            raise ValueError("A macro name is required.")
        return name, result
    if name == "shortcut":
        action = str(arguments.get("action", "")).strip().lower()
        if action not in {"save", "delete", "list"}:
            action = "list"
        result = {"action": action}
        trigger = str(arguments.get("trigger", "")).strip()
        if trigger:
            result["trigger"] = trigger[:120]
        steps = str(arguments.get("steps", "")).strip()
        if steps:
            result["steps"] = steps[:2000]
        if action in {"save", "delete"} and "trigger" not in result:
            parsed = _shortcut_from_request(user_text)
            if parsed:
                result["trigger"] = parsed[0]
                if parsed[1]:
                    result["steps"] = parsed[1]
        if action == "save" and ("trigger" not in result or "steps" not in result):
            raise ValueError(
                "Provide a trigger phrase and steps, e.g. 'shortcut study = open edge, go to pw.live/...'."
            )
        if action == "delete" and "trigger" not in result:
            raise ValueError("Which shortcut should I delete?")
        return name, result
    if name == "send_message":
        recipient = _compact(str(arguments.get("recipient", ""))).strip(" \"'")
        recipient = re.sub(r"^(?:to|the)\s+", "", recipient, flags=re.I)
        recipient = re.sub(r"\s+(?:on|in)\s+(?:whatsapp|wa)$", "", recipient, flags=re.I).strip()
        if not recipient:
            raise ValueError("Who should I message? For example: 'send a message to rahul saying I'm late'.")
        if recipient.lower() in {"someone", "somebody", "anyone", "a contact", "friend", "them", "him", "her"}:
            raise ValueError("Tell me who to message by name, e.g. 'send this message to rahul on whatsapp'.")
        if len(recipient) > 80 or re.search(r"[\x00-\x1f]", recipient):
            raise ValueError("The recipient is invalid.")
        text = str(arguments.get("text", "")).strip()
        if "\x00" in text:
            raise ValueError("The message text contains an invalid character.")
        return name, {"recipient": recipient, "text": text[:3000]}
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

    read_screen_match = re.fullmatch(
        r"(?:please\s+)?(?:read|extract|ocr)\s+(?:the\s+)?(foreground|full|screen|display|window)(?:\s+(screen|display|window))?",
        cleaned,
        re.I,
    )
    if read_screen_match or re.search(r"\b(?:read|ocr|extract)\b.*\b(?:screen|display)\b", cleaned):
        scope = "full" if "full" in cleaned or "entire" in cleaned or "whole" in cleaned else "foreground"
        try:
            name, arguments = validate_call("read_screen", {"scope": scope})
        except ValueError:
            return None
        return ToolCall(name=name, arguments=arguments, source="fast", confidence=1.0,
                        reasoning="Matched read-screen phrase.", user_text=text)

    media = _MEDIA_RE.fullmatch(cleaned)
    if media:
        action = _MEDIA_ACTIONS[media.group(1).lower()]
        return ToolCall(name="control_media", arguments={"action": action}, source="fast",
                        confidence=1.0, reasoning="Matched media phrase.", user_text=text)

    # "make X the main window" / "bring X to the front"
    main_match = re.fullmatch(
        r"(?:please\s+)?(?:make|set)\s+(.+?)\s+(?:the\s+)?(?:main|primary|active)\s+window",
        cleaned,
        re.I,
    )
    if not main_match:
        main_match = re.fullmatch(
            r"(?:please\s+)?bring\s+(.+?)\s+to\s+(?:the\s+)?(?:front|foreground)",
            cleaned,
            re.I,
        )
    if main_match:
        title = _clean_window_title(main_match.group(1))
        if title and title.lower() not in _GENERIC_WINDOW_TARGETS:
            try:
                name, arguments = validate_call("focus_window", {"title": title})
            except ValueError:
                return None
            return ToolCall(name=name, arguments=arguments, source="fast", confidence=1.0,
                            reasoning="Matched focus-window phrase.", user_text=text)

    # "open <browser> and search for X" -> navigate X in <browser>
    browser_search = re.search(
        r"(?:please\s+)?(?:open|launch|start)\s+(edge|chrome|firefox|brave|opera)\s+(?:and\s+)?(?:search(?:\s+for)?|go\s+to|navigate\s+to|open)\s+(.+)$",
        cleaned,
        re.I,
    )
    if browser_search:
        browser = browser_search.group(1).lower()
        browser = {"edge": "edge", "chrome": "chrome", "firefox": "firefox", "brave": "brave", "opera": "opera"}.get(browser, browser)
        query = browser_search.group(2).strip().strip(".!?")
        if query:
            if re.match(r"^[a-z0-9][a-z0-9.-]*\.[a-z]{2,}", query, re.I):
                url = query if "://" in query else "https://" + query
            else:
                import urllib.parse

                url = "https://www.google.com/search?q=" + urllib.parse.quote(query)
            try:
                name, arguments = validate_call("browser_control", {"action": "open_url", "url": url, "browser": browser})
            except ValueError:
                return None
            return ToolCall(name=name, arguments=arguments, source="fast", confidence=1.0,
                            reasoning="Matched browser-search phrase.", user_text=text)

    # "search for X" / "google X" -> browser navigation
    search_match = re.search(
        r"(?:please\s+)?(?:search (?:for)?|google|look up)\s+(.+)$",
        cleaned,
        re.I,
    )
    if search_match:
        query = search_match.group(1).strip().strip(".!?")
        if query:
            if re.match(r"^[a-z0-9][a-z0-9.-]*\.[a-z]{2,}", query, re.I):
                url = query if "://" in query else "https://" + query
            else:
                import urllib.parse

                url = "https://www.google.com/search?q=" + urllib.parse.quote(query)
            try:
                name, arguments = validate_call("browser_control", {"action": "open_url", "url": url})
            except ValueError:
                return None
            return ToolCall(name=name, arguments=arguments, source="fast", confidence=1.0,
                            reasoning="Matched search phrase.", user_text=text)

    # Browser scrolling.
    if re.search(r"\b(?:scroll|page)\s+(up|down)\b", cleaned, re.I):
        direction = re.search(r"\b(?:scroll|page)\s+(up|down)\b", cleaned, re.I).group(1).lower()
        action = "scroll_up" if direction == "up" else "scroll_down"
        try:
            name, arguments = validate_call("browser_control", {"action": action, "amount": 5})
        except ValueError:
            return None
        return ToolCall(name=name, arguments=arguments, source="fast", confidence=1.0,
                        reasoning="Matched browser-scroll phrase.", user_text=text)

    # "play X" / "play the video of X" -> click element named X.
    play_match = re.fullmatch(
        r"(?:please\s+)?play\s+(?:the\s+)?(?:video|clip|movie)?\s*(?:of\s+)?(.+)",
        cleaned,
        re.I,
    )
    if play_match:
        target = _clean_click_target(play_match.group(1))
        if target:
            try:
                name, arguments = validate_call("click_element", {"target": target})
            except ValueError:
                return None
            return ToolCall(name=name, arguments=arguments, source="fast", confidence=1.0,
                            reasoning="Matched play phrase.", user_text=text)

    # "send (a whatsapp) message to X saying Y" / "send this message to X"
    whatsapp_call = _send_message_from_request(text)
    if whatsapp_call is not None:
        return whatsapp_call

    # Opening WhatsApp defaults to WhatsApp Web in Edge, not the standalone app.
    if re.fullmatch(r"(?:please\s+)?(?:open|launch|start)\s+(?:the\s+)?whatsapp(?:\s+(?:web|desktop))?", original, re.I):
        try:
            name, arguments = validate_call(
                "browser_control", {"action": "open_url", "url": "https://web.whatsapp.com/", "browser": "edge"}
            )
        except ValueError:
            return None
        return ToolCall(name=name, arguments=arguments, source="fast", confidence=1.0,
                        reasoning="Matched WhatsApp open phrase.", user_text=text)

    sleep_match = _SLEEP_RE.fullmatch(cleaned)
    # "open new tab" / "new tab" / "close tab" / "next tab"
    if re.fullmatch(r"(?:please\s+)?(?:open\s+)?(?:a\s+)?new\s+tab", original, re.I):
        try:
            name, arguments = validate_call("browser_control", {"action": "tab_new"})
        except ValueError:
            return None
        return ToolCall(name=name, arguments=arguments, source="fast", confidence=1.0,
                        reasoning="Matched new-tab phrase.", user_text=text)
    if re.fullmatch(r"(?:please\s+)?(?:close|close the)\s+(?:this\s+)?(?:browser\s+)?tab", original, re.I):
        try:
            name, arguments = validate_call("browser_control", {"action": "tab_close"})
        except ValueError:
            return None
        return ToolCall(name=name, arguments=arguments, source="fast", confidence=1.0,
                        reasoning="Matched close-tab phrase.", user_text=text)
    if re.fullmatch(r"(?:please\s+)?next\s+tab", original, re.I):
        try:
            name, arguments = validate_call("browser_control", {"action": "tab_next"})
        except ValueError:
            return None
        return ToolCall(name=name, arguments=arguments, source="fast", confidence=1.0,
                        reasoning="Matched next-tab phrase.", user_text=text)
    if re.fullmatch(r"(?:please\s+)?previous\s+tab", original, re.I):
        try:
            name, arguments = validate_call("browser_control", {"action": "tab_prev"})
        except ValueError:
            return None
        return ToolCall(name=name, arguments=arguments, source="fast", confidence=1.0,
                        reasoning="Matched previous-tab phrase.", user_text=text)
    # "shortcut <trigger> = <steps>" management
    shortcut_parsed = _shortcut_from_request(text)
    if shortcut_parsed and re.search(r"\b(?:shortcut|remember shortcut|save shortcut)\b", cleaned, re.I):
        trigger, steps = shortcut_parsed
        step_list = [s.strip() for s in steps.replace(";", ",").split(",") if s.strip()]
        if not step_list:
            step_list = [steps.strip()]
        try:
            name, arguments = validate_call(
                "shortcut",
                {"action": "save", "trigger": trigger, "steps": ",".join(step_list)},
            )
        except ValueError:
            return None
        return ToolCall(name=name, arguments=arguments, source="fast", confidence=1.0,
                        reasoning="Matched shortcut-save phrase.", user_text=text)
    # "remember rahul as +91 98765 43210" / "save contact rahul 9876543210"
    contact_match = re.match(
        r"^(?:remember|save|add)\s+(?:the\s+)?(?:contact\s+)?(.+?)\s+(?:as|is|=|->|→)\s*(\+?[0-9][0-9\s().-]{6,}[0-9])\s*[.!?]*$",
        original,
        re.I,
    ) or re.match(
        r"^(?:save|add)\s+contact\s+(.+?)\s+(\+?[0-9][0-9\s().-]{6,}[0-9])\s*[.!?]*$",
        original,
        re.I,
    )
    if contact_match:
        contact_name = contact_match.group(1).strip().strip("\"'")
        contact_number = contact_match.group(2).strip()
        if contact_name and contact_name.lower() not in {"the message", "a message", "this message", "the draft", "a draft"}:
            try:
                name, arguments = validate_call(
                    "memory", {"action": "set_contact", "alias": contact_name, "target": contact_number}, text
                )
            except ValueError:
                return None
            return ToolCall(name=name, arguments=arguments, source="fast", confidence=1.0,
                            reasoning="Matched contact-save phrase.", user_text=text)

    # "remember the message buy milk" / "save draft: pick up groceries" -> saved draft
    draft_match = re.match(
        r"^(?:remember|save|set|note)\s+(?:this\s+|the\s+)?(?:draft|message|text)\s*(?::|saying|that\s+says)?\s*(.+)$",
        original,
        re.I,
    )
    if draft_match and draft_match.group(1).strip().strip(".!?"):
        draft_text = draft_match.group(1).strip().strip("\"'")
        try:
            name, arguments = validate_call(
                "memory", {"action": "set_preference", "key": "draft", "value": draft_text}, text
            )
        except ValueError:
            return None
        return ToolCall(name=name, arguments=arguments, source="fast", confidence=1.0,
                        reasoning="Matched draft-save phrase.", user_text=text)

    # "list contacts" / "show my contacts" / "forget the contact rahul"
    if re.fullmatch(r"(?:list|show|what\s+are|who\s+are)\s+(?:my\s+)?(?:saved\s+)?contacts[.!?]*", cleaned, re.I):
        try:
            name, arguments = validate_call("memory", {"action": "list_contacts"}, text)
        except ValueError:
            return None
        return ToolCall(name=name, arguments=arguments, source="fast", confidence=1.0,
                        reasoning="Matched list-contacts phrase.", user_text=text)
    forget_contact = re.fullmatch(r"(?:forget|delete|remove)\s+(?:the\s+)?contact\s+(.+?)[.!?]*", cleaned, re.I)
    if forget_contact:
        try:
            name, arguments = validate_call(
                "memory", {"action": "delete_contact", "alias": forget_contact.group(1).strip()}, text
            )
        except ValueError as exc:
            return ToolCall(name=None, user_text=text, source="fast", error=str(exc), reasoning=str(exc))
        return ToolCall(name=name, arguments=arguments, source="fast", confidence=1.0,
                        reasoning="Matched delete-contact phrase.", user_text=text)

    if sleep_match:
        try:
            name, arguments = validate_call("system_control", {"action": "sleep"})
        except ValueError:
            return None
        return ToolCall(name=name, arguments=arguments, source="fast", confidence=1.0,
                        reasoning="Matched sleep phrase.", user_text=text)

    system = _SYSTEM_RE.fullmatch(cleaned)
    if system:
        verb = re.sub(r"\s+", " ", system.group(1).lower())
        action = _SYSTEM_VERBS.get(verb)
        if action:
            try:
                name, arguments = validate_call("system_control", {"action": action})
            except ValueError:
                return None
            return ToolCall(name=name, arguments=arguments, source="fast", confidence=1.0,
                            reasoning="Matched system-control phrase.", user_text=text)

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

    closed = _CLOSE_RE.fullmatch(original)
    if re.fullmatch(r"(?:please\s+)?close(?:\s+the)?\s+window", cleaned):
        try:
            name, arguments = validate_call("press_hotkey", {"keys": ["alt", "f4"]})
        except ValueError:
            return None
        return ToolCall(name=name, arguments=arguments, source="fast", confidence=1.0,
                        reasoning="Matched close-window shortcut.", user_text=text)
    if closed:
        app_name = _close_app_from_request(text)
        if app_name:
            try:
                name, arguments = validate_call("close_application", {"app_name": app_name})
            except ValueError:
                return None
            return ToolCall(name=name, arguments=arguments, source="fast", confidence=1.0,
                            reasoning="Matched close-app phrase.", user_text=text)

    managed = _window_manage_from_request(text)
    if managed:
        action, title = managed
        try:
            name, arguments = validate_call(
                "window_manage", {"action": action, "title": title}
            )
        except ValueError:
            return None
        return ToolCall(name=name, arguments=arguments, source="fast", confidence=1.0,
                        reasoning="Matched window-manage phrase.", user_text=text)

    focused = _focus_from_request(text)
    if focused:
        if re.match(r"^[a-z0-9][a-z0-9.-]*\.[a-z]{2,}", focused, re.I):
            url = focused if "://" in focused else "https://" + focused
            try:
                name, arguments = validate_call("browser_control", {"action": "open_url", "url": url})
            except ValueError:
                return None
            return ToolCall(name=name, arguments=arguments, source="fast", confidence=1.0,
                            reasoning="Matched open-url phrase.", user_text=text)
        try:
            name, arguments = validate_call("focus_window", {"title": focused})
        except ValueError:
            return None
        return ToolCall(name=name, arguments=arguments, source="fast", confidence=1.0,
                        reasoning="Matched focus-window phrase.", user_text=text)

    first_link = re.fullmatch(
        r"(?:please\s+)?(?:open|click|tap|follow|select)\s+(?:the\s+)?(?:first|top)\s+(?:link|result|page|one)(?:\s+(?:in|on)\s+[^.]+)?",
        cleaned,
        re.I,
    )
    if first_link:
        try:
            name, arguments = validate_call("browser_control", {"action": "click_first_result"})
        except ValueError:
            return None
        return ToolCall(name=name, arguments=arguments, source="fast", confidence=1.0,
                        reasoning="Matched first-link phrase.", user_text=text)

    opened = _OPEN_RE.fullmatch(original)
    if opened:
        app_name = _open_app_from_request(text)
        if (
            app_name
            and len(app_name) <= 120
            and not re.search(r"\b(?:and|then|after|plus)\b", app_name.lower())
        ):
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

    cursor_move = _CURSOR_MOVE_RE.fullmatch(cleaned)
    if cursor_move:
        dest = cursor_move.group(1).strip()
        return ToolCall(name="click_element", arguments={"target": f"move cursor to {dest}"},
                        source="fast", confidence=1.0, reasoning="Matched cursor movement.", user_text=text)

    right_click = _RIGHT_CLICK_RE.fullmatch(cleaned)
    if right_click:
        target = (right_click.group(1) or "").strip()
        arg = f"right click {target}".strip() if target else "right click"
        return ToolCall(name="click_element", arguments={"target": arg},
                        source="fast", confidence=1.0, reasoning="Matched right-click phrase.", user_text=text)

    double_click = _DOUBLE_CLICK_RE.fullmatch(cleaned)
    if double_click:
        target = (double_click.group(1) or "").strip()
        arg = f"double click {target}".strip() if target else "double click"
        return ToolCall(name="click_element", arguments={"target": arg},
                        source="fast", confidence=1.0, reasoning="Matched double-click phrase.", user_text=text)

    scrolled = _SCROLL_RE.fullmatch(cleaned)
    if scrolled:
        direction = scrolled.group(1).lower()
        amount = scrolled.group(2) or "5"
        return ToolCall(name="click_element", arguments={"target": f"scroll {direction} {amount}"},
                        source="fast", confidence=1.0, reasoning="Matched scroll phrase.", user_text=text)

    clicked = _CLICK_RE.fullmatch(original)
    if clicked:
        target = _clean_click_target(clicked.group(1))
        if target and len(target) <= 200:
            return ToolCall(name="click_element", arguments={"target": target}, source="fast",
                            confidence=1.0, reasoning="Matched click phrase.", user_text=text)

    return None


def is_destructive(call: ToolCall) -> bool:
    """Whether a call can lose data or end the session, so it needs confirmation."""
    if not call.name:
        return False
    if call.name == "system_control":
        return str(call.arguments.get("action", "")) in {
            "shutdown", "restart", "sleep", "hibernate",
        }
    if call.name == "close_application":
        return bool(call.arguments.get("force"))
    if call.name == "file_operation":
        return str(call.arguments.get("op", "")).lower() in {"delete", "rename", "move"}
    if call.name == "run_command":
        return True
    if call.name == "run_plan":
        return True
    return False


def destructive_description(call: ToolCall) -> str:
    if call.name == "system_control":
        action = str(call.arguments.get("action", ""))
        labels = {
            "shutdown": "shut down the PC (unsaved work may be lost)",
            "restart": "restart the PC (unsaved work may be lost)",
            "sleep": "put the PC to sleep",
            "hibernate": "hibernate the PC",
        }
        return labels.get(action, f"{action} the PC")
    if call.name == "close_application":
        app_name = str(call.arguments.get("app_name", "the app"))
        if call.arguments.get("force"):
            return f"force close {app_name} (unsaved work will be lost)"
        return f"close {app_name}"
    if call.name == "file_operation":
        op = str(call.arguments.get("op", ""))
        path = str(call.arguments.get("path", "the file"))
        return f"{op} {path}"
    if call.name == "run_command":
        return f"run shell command: {str(call.arguments.get('command', ''))[:80]}"
    if call.name == "run_plan":
        steps = call.arguments.get("steps", [])
        n = len(steps) if isinstance(steps, list) else 0
        return f"run a {n}-step plan"
    return "run this action"


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
    elif call.name == "close_application":
        detail = str(call.arguments.get("app_name", "")) + (" (force)" if call.arguments.get("force") else "")
    elif call.name in {"focus_window", "window_manage"}:
        detail = str(call.arguments.get("title", ""))
        action = str(call.arguments.get("action", ""))
        if action:
            detail = f"{action} {detail}".strip()
    elif call.name == "system_control":
        detail = str(call.arguments.get("action", ""))
    elif call.name == "read_screen":
        detail = str(call.arguments.get("scope", "foreground"))
    elif call.name == "clipboard":
        detail = str(call.arguments.get("action", ""))
    elif call.name == "file_operation":
        detail = f"{call.arguments.get('op', '')} {call.arguments.get('path', '')}".strip()
    elif call.name == "run_command":
        detail = str(call.arguments.get("command", ""))
    elif call.name == "browser_control":
        detail = str(call.arguments.get("action", ""))
        if call.arguments.get("url"):
            detail = f"{detail} {call.arguments['url']}"
    elif call.name == "send_message":
        detail = f"to {call.arguments.get('recipient', '')}"
    elif call.name == "memory":
        detail = str(call.arguments.get("action", ""))
    elif call.name == "schedule":
        detail = str(call.arguments.get("action", ""))
        if call.arguments.get("message"):
            detail = f"{detail} {call.arguments['message']}"
    elif call.name == "macro":
        detail = f"{call.arguments.get('action', '')} {call.arguments.get('name', '')}".strip()
    elif call.name == "run_plan":
        steps = call.arguments.get("steps", [])
        detail = f"{len(steps) if isinstance(steps, list) else '?'} steps"
    if call.name in {"type_text", "click_element"} and call.arguments.get("window"):
        detail = f"{detail} → {call.arguments['window']}"
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
    if call.name == "close_application":
        suffix = " (forced)" if call.arguments.get("force") else ""
        return f"Closing {call.arguments.get('app_name', 'the app')}{suffix}"
    if call.name == "focus_window":
        return f"Switching to {call.arguments.get('title', 'the window')}"
    if call.name == "window_manage":
        return f"{str(call.arguments.get('action', '')).title()}ing {call.arguments.get('title', 'the window')}"
    if call.name == "system_control":
        labels = {
            "lock": "Locking the PC",
            "shutdown": "Shutting down the PC",
            "restart": "Restarting the PC",
            "sleep": "Putting the PC to sleep",
            "hibernate": "Hibernating the PC",
        }
        return labels.get(str(call.arguments.get("action", "")), "Controlling the PC")
    if call.name == "read_screen":
        return f"Reading the {call.arguments.get('scope', 'foreground')} screen"
    if call.name == "clipboard":
        action = str(call.arguments.get("action", "get"))
        if action == "set":
            return f"Copying to the clipboard: {str(call.arguments.get('text', ''))[:40]}"
        return "Reading the clipboard"
    if call.name == "file_operation":
        op = str(call.arguments.get("op", "file"))
        path = str(call.arguments.get("path", "file"))
        return f"{op.title()}ing {path}"
    if call.name == "run_command":
        return f"Running: {str(call.arguments.get('command', ''))[:60]}"
    if call.name == "browser_control":
        action = str(call.arguments.get("action", ""))
        url = str(call.arguments.get("url", ""))
        if url:
            return f"Browser: {action} {url}"
        return f"Browser: {action}"
    if call.name == "send_message":
        recipient = str(call.arguments.get("recipient", "the contact"))
        text = str(call.arguments.get("text", "")).strip()
        if not text:
            return f"Opening WhatsApp for {recipient} with your saved draft"
        short = text if len(text) <= 60 else text[:57] + "..."
        return f"Opening WhatsApp for {recipient}: \"{short}\""
    if call.name == "memory":
        return f"Memory: {call.arguments.get('action', 'action')}"
    if call.name == "schedule":
        action = str(call.arguments.get("action", ""))
        message = str(call.arguments.get("message", ""))
        if message:
            return f"Schedule: {action} — {message}"
        return f"Schedule: {action}"
    if call.name == "macro":
        action = str(call.arguments.get("action", ""))
        name = str(call.arguments.get("name", ""))
        if name:
            return f"Macro: {action} {name}"
        return f"Macro: {action}"
    if call.name == "run_plan":
        steps = call.arguments.get("steps", [])
        n = len(steps) if isinstance(steps, list) else 0
        return f"Running a {n}-step plan"
    if call.name in {"type_text", "click_element"} and call.arguments.get("window"):
        target = call.arguments.get("text") if call.name == "type_text" else call.arguments.get("target")
        return f"{'Typing' if call.name == 'type_text' else 'Clicking'} {target} in {call.arguments['window']}"
    return "Running the action"
