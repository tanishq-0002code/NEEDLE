from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any, Callable

from .paths import app_data_dir


def _path() -> Path:
    return app_data_dir() / "schedule.json"


def _load() -> list[dict[str, Any]]:
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
        if isinstance(data, list):
            return data
    except (OSError, ValueError, TypeError):
        pass
    return []


def _save(tasks: list[dict[str, Any]]) -> None:
    try:
        _path().write_text(json.dumps(tasks, indent=2, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


class Scheduler:
    """Lightweight reminder/timer runner. Fires a callback with (message, action)."""

    def __init__(self, fire: Callable[[str, str | None], None]) -> None:
        self._fire = fire
        self._tasks: list[dict[str, Any]] = _load()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="NeedleScheduler", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)
        self._thread = None

    def add_reminder(self, minutes: float, message: str, action: str | None = None) -> dict[str, Any]:
        minutes = max(0.05, min(10080.0, float(minutes)))
        task = {
            "id": int(time.time() * 1000),
            "due": time.time() + minutes * 60.0,
            "message": str(message or "Reminder"),
            "action": action,
            "repeat_minutes": None,
        }
        with self._lock:
            self._tasks.append(task)
            _save(self._tasks)
        return task

    def add_recurring(self, minutes: float, message: str, action: str | None = None) -> dict[str, Any]:
        task = self.add_reminder(minutes, message, action)
        task["repeat_minutes"] = max(1.0, float(minutes))
        with self._lock:
            for existing in self._tasks:
                if existing["id"] == task["id"]:
                    existing["repeat_minutes"] = task["repeat_minutes"]
            _save(self._tasks)
        return task

    def cancel(self, task_id: int) -> bool:
        with self._lock:
            before = len(self._tasks)
            self._tasks = [t for t in self._tasks if int(t.get("id", 0)) != int(task_id)]
            if len(self._tasks) != before:
                _save(self._tasks)
                return True
        return False

    def list_tasks(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(t) for t in self._tasks]

    def _loop(self) -> None:
        while not self._stop.is_set():
            now = time.time()
            due_tasks: list[dict[str, Any]] = []
            with self._lock:
                for task in list(self._tasks):
                    if float(task.get("due", 0)) <= now:
                        due_tasks.append(task)
            for task in due_tasks:
                try:
                    self._fire(str(task.get("message", "Reminder")), task.get("action"))
                except Exception:
                    pass
                repeat = task.get("repeat_minutes")
                with self._lock:
                    if repeat:
                        for existing in self._tasks:
                            if int(existing.get("id", 0)) == int(task.get("id", 0)):
                                existing["due"] = now + float(repeat) * 60.0
                        _save(self._tasks)
                    else:
                        self._tasks = [
                            t for t in self._tasks if int(t.get("id", 0)) != int(task.get("id", 0))
                        ]
                        _save(self._tasks)
            self._stop.wait(1.0)
