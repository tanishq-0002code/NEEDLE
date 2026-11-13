from __future__ import annotations

import logging
import threading
import time
from dataclasses import replace

from PyQt6.QtCore import QObject, QTimer, pyqtSignal
from PyQt6.QtWidgets import QApplication, QInputDialog

from .audio import AudioEngine, list_input_devices
from .automation import DesktopAutomation
from .config import AppConfig, load_config, save_config
from .hotkey import HotkeyManager, OutsideClickWatcher
from .models import ExecutionResult, ToolCall
from .needler import NeedleRouter
from .overlay import OverlayWindow
from .paths import app_data_dir, sound_path
from .settings_dialog import SettingsDialog
from . import windows_api

log = logging.getLogger("needle.controller")

INTERACTIVE_TOOLS = {"type_text", "click_element", "press_hotkey", "take_screenshot"}


class AssistantController(QObject):
    route_ready = pyqtSignal(object, int)
    route_failed = pyqtSignal(str, int)
    execution_ready = pyqtSignal(object, object, int)
    notify = pyqtSignal(str, str)
    status_changed = pyqtSignal(str)
    config_changed = pyqtSignal(object)
    refresh_status = pyqtSignal()

    def __init__(self, app: QApplication, config: AppConfig) -> None:
        super().__init__()
        self.app = app
        self.config = config
        self.router = NeedleRouter(
            max_new_tokens=config.max_new_tokens,
            threads=config.threads,
            log_transcripts=config.log_transcripts,
        )
        self.overlay = OverlayWindow(config)
        self.overlay.settings_requested.connect(self.show_settings)
        self.overlay.voice_triggered.connect(lambda: self.trigger("tray"))
        self.overlay.reset_perch_requested.connect(self.overlay.reset_perch)
        self.overlay.quit_requested.connect(self.quit)
        self.audio = AudioEngine(config)
        self.hotkey = HotkeyManager()
        self.outside = OutsideClickWatcher()
        self.automation = DesktopAutomation()
        self.settings: SettingsDialog | None = None
        self._session = 0
        self._foreground_hwnd = 0
        self._last_external_hwnd = 0
        self._active = False
        self._quitting = False
        self._router_ready = False
        self._audio_error = ""

        self.hotkey.activated.connect(self.toggle_from_hotkey)
        self.hotkey.escape_pressed.connect(self.on_escape)
        self.hotkey.error.connect(self.on_hotkey_error)
        self.outside.clicked.connect(self.on_global_click)

        self.audio.wake_detected.connect(self.on_wake)
        self.audio.partial_text.connect(self.overlay.set_partial)
        self.audio.command_ready.connect(self.on_command)
        self.audio.level_changed.connect(self.overlay.set_level)
        self.audio.listening_changed.connect(self.on_listening_changed)
        self.audio.status_changed.connect(self.on_audio_status)
        self.audio.error.connect(self.on_audio_error)

        self.overlay.dismissed.connect(self.dismiss)
        self.overlay.hidden.connect(self.on_overlay_hidden)

        self.route_ready.connect(self.on_route_ready)
        self.route_failed.connect(self.on_route_failed)
        self.execution_ready.connect(self.on_execution_ready)
        self.refresh_status.connect(self._refresh_settings_status)

    # ------------------------------------------------------------------ startup
    def start(self) -> None:
        # Loading the 35 MB Needle 3 weights takes a long time on slow CPUs.
        # Keep the UI and the deterministic fast route responsive while the
        # semantic router warms up in the background.
        thread = threading.Thread(target=self._load_router_worker, name="NeedleLoad", daemon=True)
        thread.start()
        self.audio.start()
        self.hotkey.start(self.config.hotkey)
        self.outside.start()
        self._emit_status()
        self._refresh_settings_status()

    def _load_router_worker(self) -> None:
        # Let the microphone and the Qt event loop settle before a long,
        # CPU-heavy grammar initialization starts on a slow machine.
        time.sleep(6)
        try:
            self.router.load()
            self._router_ready = True
            log.info("Needle 3 router is ready.")
            self.status_changed.emit("Needle Desktop — Needle 3 ready")
        except Exception as exc:
            self._router_ready = False
            log.exception("Needle 3 router failed to load.")
            self.notify.emit("Needle 3 is unavailable", str(exc))
        finally:
            self.refresh_status.emit()

    def _emit_status(self) -> None:
        wake = "wake on" if self.config.wake_word_enabled else "wake off"
        router = "Needle 3 ready" if self.router.loaded else "warming up Needle 3"
        self.status_changed.emit(
            f"Needle Desktop — {wake} · {self.config.hotkey.upper()} · {router}"
        )

    # ------------------------------------------------------------------ trigger
    def toggle_from_hotkey(self) -> None:
        if self.overlay.isVisible():
            self.dismiss()
        else:
            self.trigger("hotkey")

    def trigger(self, source: str = "hotkey", initial_text: str | None = None,
                welcome: bool = False) -> None:
        if self._quitting:
            return
        if source == "typed":
            # The command was typed while another app (or Settings) was in front.
            # Keep the last external window as the typing target.
            self._foreground_hwnd = self._last_external_hwnd or self._foreground_hwnd
        else:
            self._capture_foreground()
        self._session += 1
        self._active = True
        if welcome:
            self.overlay.show_welcome()
            self.overlay.hide_after(4200)
            return
        if initial_text:
            self.overlay.show_listening("typed")
            self.overlay.set_partial(initial_text)
            self.process_text(initial_text)
            return
        self.overlay.show_listening(source)
        self._play_sound("listen.wav")
        if source in {"hotkey", "tray", "test"}:
            QTimer.singleShot(0, self.audio.begin_command)

    def on_wake(self, phrase: str) -> None:
        if self._quitting:
            return
        if self.overlay.isVisible():
            if self.overlay.state in {"thinking", "executing"}:
                log.debug("Ignoring wake word while a command is already in flight.")
                return
            self.overlay.hide_immediately()
        self._capture_foreground()
        self._session += 1
        self._active = True
        self.overlay.show_listening("wake")
        self._play_sound("listen.wav")

    def on_command(self, text: str) -> None:
        if not self._active or not self.overlay.isVisible():
            return
        if self.overlay.state not in {"listening", "thinking"}:
            return
        text = " ".join((text or "").split())
        if not text:
            self.overlay.show_error("I didn't catch that. Press Ctrl+Space and try again.")
            self.overlay.hide_after(2600)
            self._play_sound("error.wav")
            return
        self.overlay.set_partial(text)
        self.process_text(text)

    def process_text(self, text: str) -> None:
        text = " ".join((text or "").split())
        if not text:
            self.overlay.show_error("I didn't hear a command.")
            self.overlay.hide_after(2200)
            return
        self._active = True
        self._session += 1
        session = self._session
        self.overlay.set_thinking(text)
        thread = threading.Thread(
            target=self._route_worker,
            args=(text, session),
            name="NeedleRoute",
            daemon=True,
        )
        thread.start()

    def _route_worker(self, text: str, session: int) -> None:
        try:
            call = self.router.route(text, use_fast=self.config.fast_intent)
            if call.name and not call.user_text:
                call.user_text = text
            self.route_ready.emit(call, session)
        except Exception as exc:
            log.exception("Route worker failed.")
            self.route_failed.emit(str(exc), session)

    def on_route_ready(self, call: ToolCall, session: int) -> None:
        if session != self._session or not self.overlay.isVisible():
            return
        if not call.name:
            message = call.error or call.reasoning or "I couldn't map that to a desktop action."
            log.warning("Route failed: %s", message)
            self.overlay.show_error(message)
            self.overlay.hide_after(3000)
            self._play_sound("error.wav")
            return
        log.info(
            "Executing %s via %s (confidence=%s, latency=%.2fs)",
            call.name, call.source, call.confidence, call.latency,
        )
        interactive = call.name in INTERACTIVE_TOOLS
        self.overlay.show_tool(call, hide=interactive)
        restore = self._foreground_hwnd if interactive else 0
        if interactive:
            QTimer.singleShot(120, lambda: self._run_execution(call, restore, session))
        else:
            self._run_execution(call, restore, session)

    def on_route_failed(self, message: str, session: int) -> None:
        if session != self._session:
            return
        self.overlay.show_error(f"Needle had a problem: {message}")
        self.overlay.hide_after(3200)
        self._play_sound("error.wav")

    def _run_execution(self, call: ToolCall, restore_hwnd: int, session: int) -> None:
        if session != self._session:
            return
        thread = threading.Thread(
            target=self._execution_worker,
            args=(call, restore_hwnd, session),
            name="NeedleAction",
            daemon=True,
        )
        thread.start()

    def _execution_worker(self, call: ToolCall, restore_hwnd: int, session: int) -> None:
        started = time.monotonic()
        try:
            result = self.automation.execute(call, restore_hwnd=restore_hwnd)
        except Exception as exc:
            log.exception("Automation worker crashed.")
            result = ExecutionResult(False, f"The action failed: {exc}", call.name or "")
        log.info(
            "Action %s finished in %.2fs: success=%s",
            call.name, time.monotonic() - started, result.success,
        )
        self.execution_ready.emit(result, call, session)

    def on_execution_ready(self, result: ExecutionResult, call: ToolCall, session: int) -> None:
        if session != self._session:
            log.info("Discarding stale execution result for %s.", call.name)
            return
        self.overlay.show_result(result, call, call.source)
        self.overlay.hide_after(1200 if result.success else 2800)
        self._play_sound("done.wav" if result.success else "error.wav")
        if not result.success:
            self.notify.emit("Needle couldn't finish", result.message)

    # ------------------------------------------------------------------ dismiss
    def dismiss(self) -> None:
        self._session += 1
        self._active = False
        try:
            self.audio.cancel_command()
        except Exception:
            pass
        self.overlay.hide_animated()

    def on_global_click(self, x: int, y: int) -> None:
        if not self.overlay.isVisible():
            return
        if self.overlay.contains_global_point(x, y):
            return
        self.dismiss()

    def on_escape(self) -> None:
        if self.overlay.isVisible():
            self.dismiss()

    def on_overlay_hidden(self) -> None:
        self._active = False

    # ------------------------------------------------------------------ errors
    def on_hotkey_error(self, message: str) -> None:
        log.error("Hotkey error: %s", message)
        self.notify.emit("Hotkey unavailable", message)

    def on_audio_error(self, message: str) -> None:
        log.error("Audio error: %s", message)
        self._audio_error = message
        if self.overlay.isVisible():
            self.overlay.show_error(message)
            self.overlay.hide_after(3200)
        else:
            self.notify.emit("Microphone problem", message)
        self._refresh_settings_status()

    def on_audio_status(self, status: str) -> None:
        log.info("Audio status: %s", status)
        if status == "Ready":
            self._audio_error = ""
        self._refresh_settings_status()

    def on_listening_changed(self, listening: bool) -> None:
        if listening:
            self.status_changed.emit("Needle Desktop — listening…")
        else:
            self._emit_status()

    # ------------------------------------------------------------------ settings
    def show_settings(self) -> None:
        self._capture_foreground()
        if self.settings is None:
            self.settings = SettingsDialog(self.config)
            self.settings.saved.connect(self.apply_config)
            self.settings.test_popup.connect(self.test_popup)
            self.settings.type_command.connect(self.show_typed_command)
        else:
            self.settings.config = self.config
        self._refresh_settings_status()
        self.settings.show()
        self.settings.raise_()
        self.settings.activateWindow()

    def _refresh_settings_status(self) -> None:
        if self.settings is None:
            return
        needle = "Ready" if self.router.loaded else ("Error" if self.router.load_error else "Loading…")
        worker = self.audio.worker
        speech = "Ready" if worker is not None and getattr(worker, "_model", None) is not None else (
            "Unavailable" if self._audio_error else "Loading…"
        )
        if self.config.microphone_index is None:
            mic = "System default input"
        else:
            mic = f"Device {self.config.microphone_index}"
            for index, label in list_input_devices():
                if index == self.config.microphone_index:
                    mic = label
                    break
        wake = "On · " + (self.config.wake_phrases[0] if self.config.wake_phrases else "wake word") if self.config.wake_word_enabled else "Off"
        self.settings.set_status(needle=needle, speech=speech, microphone=mic, wake=wake)

    def apply_config(self, new_config: AppConfig) -> None:
        old = self.config
        self.config = new_config
        self.config.validate()
        save_config(self.config)
        self.overlay.config = self.config
        self.overlay.set_accent(self.config.overlay_accent)
        self.router.set_max_new_tokens(self.config.max_new_tokens)
        self.router.set_threads(self.config.threads)
        if old.hotkey != self.config.hotkey:
            self.hotkey.start(self.config.hotkey)
        if (
            old.wake_word_enabled != self.config.wake_word_enabled
            or old.wake_phrases != self.config.wake_phrases
            or old.microphone_index != self.config.microphone_index
        ):
            self.audio.restart(self.config)
        else:
            self.audio.reload(self.config)
        self.config_changed.emit(self.config)
        self._emit_status()
        self._refresh_settings_status()
        self.notify.emit("Settings saved", "Your changes are active.")

    def toggle_wake(self, enabled: bool) -> None:
        updated = replace(self.config)
        updated.wake_word_enabled = bool(enabled)
        updated.validate()
        self.config = updated
        save_config(updated)
        self.audio.set_wake_enabled(bool(enabled))
        if self.settings is not None:
            self.settings.config = updated
            self.settings.wake_check.setChecked(bool(enabled))
        self.config_changed.emit(updated)
        self._emit_status()
        self._refresh_settings_status()

    def test_popup(self) -> None:
        self.trigger("test", welcome=True)

    def show_typed_command(self) -> None:
        if self.settings is None or not self.settings.isVisible():
            self._capture_foreground()
        text, ok = QInputDialog.getText(
            self.settings or self.overlay,
            "Type a command",
            "Command (for example: open Notepad):",
        )
        if ok and str(text).strip():
            self._foreground_hwnd = self._last_external_hwnd or self._foreground_hwnd
            self.trigger("typed", initial_text=str(text).strip())

    def open_data_folder(self) -> None:
        windows_api.open_folder(app_data_dir())

    # ------------------------------------------------------------------ helpers
    def _capture_foreground(self) -> None:
        fg = windows_api.get_foreground_window()
        if not fg:
            self._foreground_hwnd = self._last_external_hwnd
            return
        own = set()
        try:
            if self.overlay.isVisible():
                own.add(int(self.overlay.winId()))
        except Exception:
            pass
        try:
            if self.settings is not None and self.settings.isVisible():
                own.add(int(self.settings.winId()))
        except Exception:
            pass
        if fg not in own:
            self._last_external_hwnd = fg
        self._foreground_hwnd = self._last_external_hwnd or fg

    def _play_sound(self, filename: str) -> None:
        try:
            import winsound

            path = sound_path(filename)
            if path.exists():
                winsound.PlaySound(
                    str(path),
                    winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT,
                )
        except Exception:
            pass

    # ------------------------------------------------------------------ shutdown
    def quit(self) -> None:
        if self._quitting:
            return
        self._quitting = True
        log.info("Shutting down Needle Desktop.")
        try:
            self.audio.shutdown()
        except Exception:
            pass
        try:
            self.hotkey.stop()
        except Exception:
            pass
        try:
            self.outside.stop()
        except Exception:
            pass
        try:
            self.overlay.hide_immediately()
        except Exception:
            pass
        if self.settings is not None:
            self.settings.close()
        try:
            if self.router.loaded:
                self.router.close()
        except Exception:
            pass
        try:
            save_config(self.config)
        except Exception:
            pass
        self.app.quit()
