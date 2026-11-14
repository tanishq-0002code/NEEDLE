from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field


@dataclass
class PendingConfirmation:
    chat_id: int
    text: str
    description: str
    created: float = field(default_factory=time.monotonic)


class ConfirmationStore:
    """Tracks remote (Telegram) actions that are waiting for /confirm."""

    def __init__(self, ttl: float = 120.0) -> None:
        self._ttl = float(ttl) if ttl > 0 else 120.0
        self._pending: dict[int, PendingConfirmation] = {}
        self._lock = threading.Lock()

    def request(self, chat_id: int, text: str, description: str) -> bool:
        """Register a pending action. Returns False when one is already waiting."""
        with self._lock:
            existing = self._pending.get(chat_id)
            if existing is not None and time.monotonic() - existing.created < self._ttl:
                return False
            self._pending[chat_id] = PendingConfirmation(chat_id, text, description)
            return True

    def take(self, chat_id: int) -> PendingConfirmation | None:
        with self._lock:
            item = self._pending.pop(chat_id, None)
        if item is None:
            return None
        if time.monotonic() - item.created > self._ttl:
            return None
        return item

    def peek(self, chat_id: int) -> PendingConfirmation | None:
        with self._lock:
            item = self._pending.get(chat_id)
        if item is None or time.monotonic() - item.created > self._ttl:
            return None
        return item

    def clear(self, chat_id: int | None = None) -> None:
        with self._lock:
            if chat_id is None:
                self._pending.clear()
            else:
                self._pending.pop(chat_id, None)

    def purge_expired(self) -> None:
        now = time.monotonic()
        with self._lock:
            stale = [
                key for key, item in self._pending.items()
                if now - item.created > self._ttl
            ]
            for key in stale:
                self._pending.pop(key, None)
