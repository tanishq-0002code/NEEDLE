from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolCall:
    name: str | None = None
    arguments: dict[str, Any] = field(default_factory=dict)
    source: str = "needle"
    confidence: float | None = None
    reasoning: str = ""
    raw_text: str = ""
    latency: float = 0.0
    error: str | None = None
    user_text: str = ""

    @property
    def is_action(self) -> bool:
        return bool(self.name)


@dataclass
class ExecutionResult:
    success: bool
    message: str
    tool: str = ""
    path: str | None = None
    detail: str = ""
