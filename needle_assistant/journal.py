from __future__ import annotations

import threading
from typing import Callable


class UndoJournal:
    """Records reversible inverse actions for 'undo that' / 'undo all'."""

    def __init__(self) -> None:
        self._entries: list[Callable[[], None]] = []
        self._lock = threading.Lock()

    def record(self, inverse: Callable[[], None] | None) -> None:
        if inverse is None:
            return
        with self._lock:
            self._entries.append(inverse)
            if len(self._entries) > 50:
                self._entries.pop(0)

    def has_entries(self) -> bool:
        with self._lock:
            return bool(self._entries)

    def undo_last(self) -> int:
        with self._lock:
            if not self._entries:
                return 0
            action = self._entries.pop()
        try:
            action()
            return 1
        except Exception:
            return 0

    def undo_all(self) -> int:
        with self._lock:
            actions = list(reversed(self._entries))
            self._entries.clear()
        done = 0
        for action in actions:
            try:
                action()
                done += 1
            except Exception:
                continue
        return done

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
