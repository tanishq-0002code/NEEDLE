from __future__ import annotations

import re
from typing import Iterable


_SEQUENCE_CUES = re.compile(
    r"\b(?:then|and then|after that|afterwards|next|afterwards)\b",
    re.I,
)
_NUMBERED_RE = re.compile(r"\d+[.)]\s+", re.I)


def looks_like_plan(text: str) -> bool:
    cleaned = " ".join(str(text or "").split())
    if not cleaned:
        return False
    if _NUMBERED_RE.search(cleaned) and len(_NUMBERED_RE.findall(cleaned)) >= 2:
        return True
    cues = _SEQUENCE_CUES.findall(cleaned)
    if len(cues) >= 1 and len(cleaned.split()) >= 4:
        # Avoid trivial single-command "open X and Y" where Y is not a verb.
        return True
    return False


def split_plan(text: str) -> list[str] | None:
    cleaned = " ".join(str(text or "").split())
    if not cleaned or not looks_like_plan(cleaned):
        return None

    # Numbered list: "1. open notepad 2. type hello 3. save"
    if _NUMBERED_RE.search(cleaned) and len(_NUMBERED_RE.findall(cleaned)) >= 2:
        parts = re.split(r"\s*\d+[.)]\s+", cleaned)
        parts = [part.strip(" ,.;") for part in parts if part.strip(" ,.;")]
        if len(parts) >= 2:
            return parts

    # Sequence cues.
    parts = re.split(
        r"\s+(?:and\s+then|then|after that|afterwards|next)\s+",
        cleaned,
        flags=re.I,
    )
    parts = [part.strip(" ,.;") for part in parts if part.strip(" ,.;")]
    if len(parts) >= 2:
        return parts

    # Comma-separated imperative list (only if every part starts with a verb).
    if "," in cleaned:
        parts = [part.strip(" ,.;") for part in cleaned.split(",")]
        parts = [part for part in parts if part]
        if len(parts) >= 3 and all(_starts_with_verb(part) for part in parts):
            return parts
    return None


_VERBS = {
    "open", "close", "quit", "exit", "launch", "start", "run", "type", "write",
    "enter", "click", "tap", "press", "hit", "take", "capture", "screenshot",
    "switch", "focus", "minimize", "maximize", "restore", "lock", "shut",
    "restart", "sleep", "read", "copy", "paste", "save", "refresh", "reload",
    "scroll", "move", "rename", "delete", "create", "append", "list",
}


def _starts_with_verb(part: str) -> bool:
    first = part.strip().split()[:1]
    if not first:
        return False
    word = first[0].lower().strip(".,!?")
    return word in _VERBS


def is_transient_failure(message: str) -> bool:
    text = (message or "").lower()
    markers = (
        "not ready", "try again", "timed out", "timeout", "busy",
        "window not found", "no window", "not found", "did not close",
        "unavailable",
    )
    blockers = ("blocked", "disabled", "refusing", "cancelled", "denied",
                "unknown tool", "invalid", "outside the allowed")
    if any(blocker in text for blocker in blockers):
        return False
    return any(marker in text for marker in markers)
