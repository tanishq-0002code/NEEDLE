from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from .paths import app_data_dir


def _redact(arguments: Any) -> Any:
    if not isinstance(arguments, dict):
        return arguments
    sensitive = {"token", "password", "secret", "key", "credential",
                 "text", "message", "content", "steps"}
    result: dict[str, Any] = {}
    for key, value in arguments.items():
        if any(word in str(key).lower() for word in sensitive):
            result[key] = "<redacted>"
        elif isinstance(value, str) and len(value) > 500:
            result[key] = value[:497] + "..."
        else:
            result[key] = value
    return result


def record(tool: str, arguments: Any, success: bool, message: str = "") -> None:
    """Append one JSON line to the local action audit log."""
    try:
        folder = app_data_dir() / "logs"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / "actions.jsonl"
        entry = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "tool": str(tool or ""),
            "arguments": _redact(arguments),
            "success": bool(success),
            "message": str(message or "")[:300],
        }
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        # Audit logging must never break command execution.
        pass
