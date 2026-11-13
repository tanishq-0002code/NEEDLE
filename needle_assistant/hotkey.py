from __future__ import annotations

import logging
import threading

from PyQt6.QtCore import QObject, pyqtSignal

from .config import normalize_hotkey

log = logging.getLogger("needle.hotkey")

_MODIFIER_MAP = {
    "ctrl": "<ctrl>",
    "alt": "<alt>",
    "shift": "<shift>",
    "win": "<cmd>",
}

_SPECIAL_MAP = {
    "space": "<space>",
    "enter": "<enter>",
    "tab": "<tab>",
    "esc": "<esc>",
    "backspace": "<backspace>",
    "delete": "<delete>",
    "insert": "<insert>",
    "home": "<home>",
    "end": "<end>",
    "pageup": "<page_up>",
    "pagedown": "<page_down>",
    "up": "<up>",
    "down": "<down>",
    "left": "<left>",
    "right": "<right>",
    "capslock": "<caps_lock>",
    "printscreen": "<print_screen>",
    "pause": "<pause>",
    "menu": "<menu>",
    "plus": "<+>",
    "minus": "<->",
    **{f"f{i}": f"<f{i}>" for i in range(1, 25)},
}


def to_pynput_hotkey(value: str) -> str:
    normalized = normalize_hotkey(value)
    parts: list[str] = []
    for part in normalized.split("+"):
        if part in _MODIFIER_MAP:
            parts.append(_MODIFIER_MAP[part])
        elif part in _SPECIAL_MAP:
            parts.append(_SPECIAL_MAP[part])
        elif len(part) == 1:
            parts.append(part)
        else:
            parts.append(f"<{part}>")
    return "+".join(parts)


class HotkeyManager(QObject):
    activated = pyqtSignal()
    escape_pressed = pyqtSignal()
    error = pyqtSignal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._hotkey_listener = None
        self._escape_listener = None
        self._current = ""
        self._lock = threading.Lock()

    def start(self, hotkey: str) -> None:
        self.stop()
        try:
            from pynput import keyboard

            pynput_hotkey = to_pynput_hotkey(hotkey)
            self._current = normalize_hotkey(hotkey)
            listener = keyboard.GlobalHotKeys({pynput_hotkey: self._on_hotkey})
            listener.daemon = True
            listener.start()
            self._hotkey_listener = listener
            escape = keyboard.Listener(on_press=self._on_press)
            escape.daemon = True
            escape.start()
            self._escape_listener = escape
            log.info("Global hotkey registered: %s (%s)", self._current, pynput_hotkey)
        except Exception as exc:
            log.exception("Could not register the global hotkey.")
            self.error.emit(f"Could not register {hotkey}: {exc}")

    def stop(self) -> None:
        for listener in (self._hotkey_listener, self._escape_listener):
            if listener is not None:
                try:
                    listener.stop()
                except Exception:
                    pass
        self._hotkey_listener = None
        self._escape_listener = None

    def _on_hotkey(self) -> None:
        self.activated.emit()

    def _on_press(self, key) -> None:
        try:
            from pynput import keyboard

            if key == keyboard.Key.esc:
                self.escape_pressed.emit()
        except Exception:
            pass


class OutsideClickWatcher(QObject):
    clicked = pyqtSignal(int, int)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._listener = None

    def start(self) -> None:
        if self._listener is not None:
            return
        try:
            from pynput import mouse

            listener = mouse.Listener(on_click=self._on_click)
            listener.daemon = True
            listener.start()
            self._listener = listener
        except Exception:
            log.exception("Could not start the outside-click watcher.")

    def stop(self) -> None:
        if self._listener is not None:
            try:
                self._listener.stop()
            except Exception:
                pass
            self._listener = None

    def _on_click(self, x, y, button, pressed) -> None:
        if pressed:
            try:
                self.clicked.emit(int(x), int(y))
            except Exception:
                pass
