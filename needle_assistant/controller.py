from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import replace

from PyQt6.QtCore import QObject, QTimer, pyqtSignal
from PyQt6.QtWidgets import QApplication, QInputDialog, QMessageBox

from . import __app_name__, __version__
from .audit import record as audit_record
from .audio import AudioEngine, list_input_devices
from .automation import AmbiguousAppError, AutomationError, DesktopAutomation
from .catalog import destructive_description, fast_route, is_destructive
from .confirmation import ConfirmationStore
from .config import AppConfig, load_config, save_config
from .hotkey import HotkeyManager, OutsideClickWatcher
from .journal import UndoJournal
from .macros import get as get_macro
from .memory import record_history
from .models import ExecutionResult, ToolCall
from .needler import NeedleRouter
from .overlay import OverlayWindow
from .paths import app_data_dir, sound_path
from .planner import is_transient_failure, looks_like_plan, split_plan
from .scheduler import Scheduler
from .settings_dialog import SettingsDialog
from .telegram_bot import TelegramBot
from . import windows_api

log = logging.getLogger("needle.controller")

INTERACTIVE_TOOLS = {"type_text", "click_element", "press_hotkey", "take_screenshot"}


class AssistantController(QObject):
    route_ready = pyqtSignal(object, int)
    route_failed = pyqtSignal(str, int)
    execution_ready = pyqtSignal(object, object, int)
    plan_progress = pyqtSignal(str, int, int)
    plan_step_done = pyqtSignal(object, object, int, int)
    plan_done = pyqtSignal(bool, str, int)
    scheduled_fire = pyqtSignal(str, object)
    notify = pyqtSignal(str, str)
    study_notice = pyqtSignal(str, bool)
    status_changed = pyqtSignal(str)
    config_changed = pyqtSignal(object)
    refresh_status = pyqtSignal()
    confirm_needed = pyqtSignal(str, object, object)

    def __init__(self, app: QApplication, config: AppConfig) -> None:
        super().__init__()
        self.app = app
        self.config = config
        self.enabled = bool(getattr(config, "enabled", True))
        self.router = NeedleRouter(
            max_new_tokens=config.max_new_tokens,
            threads=config.threads,
            log_transcripts=config.log_transcripts,
        )
        self.overlay = OverlayWindow(config)
        self.overlay.enabled = self.enabled
        self.overlay.settings_requested.connect(self.show_settings)
        self.overlay.voice_triggered.connect(lambda: self.trigger("tray"))
        self.overlay.toggle_enabled_requested.connect(lambda: self.toggle_enabled(not self.enabled))
        self.overlay.reset_perch_requested.connect(self.overlay.reset_perch)
        self.overlay.quit_requested.connect(self.quit)
        self.overlay.command_submitted.connect(self.process_text)
        self.audio = AudioEngine(config)
        self.hotkey = HotkeyManager()
        self.outside = OutsideClickWatcher()
        self.automation = DesktopAutomation()
        self.undo = UndoJournal()
        self.scheduler = Scheduler(fire=self._on_scheduled_fire)
        self.automation.set_scheduler(self.scheduler)
        self.telegram: TelegramBot | None = None
        self.settings: SettingsDialog | None = None
        self._session = 0
        self._foreground_hwnd = 0
        self._last_external_hwnd = 0
        self._active = False
        self._quitting = False
        self._router_ready = False
        self._router_error = ""
        self._audio_error = ""
        self._telegram_status = "Off"
        self._telegram_token = ""
        self._confirmation_store = ConfirmationStore()
        self._pending_clarification: dict | None = None

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
        self.plan_progress.connect(self.on_plan_progress)
        self.plan_step_done.connect(self.on_plan_step_done)
        self.plan_done.connect(self.on_plan_done)
        self.scheduled_fire.connect(self._handle_scheduled_fire)
        self.study_notice.connect(self.on_study_notice)
        self.refresh_status.connect(self._refresh_settings_status)
        self.confirm_needed.connect(self._on_confirm_needed)
        try:
            self.automation.set_study_notifier(
                lambda message, is_error: self.study_notice.emit(message, is_error)
            )
        except Exception:
            pass

    # ------------------------------------------------------------------ startup
    def start(self) -> None:
        # Loading the 35 MB Needle 3 weights takes a long time on slow CPUs.
        # Keep the UI and the deterministic fast route responsive while the
        # semantic router warms up in the background.
        thread = threading.Thread(target=self._load_router_worker, name="NeedleLoad", daemon=True)
        thread.start()
        if self.enabled:
            self.audio.start()
            self.hotkey.start(self.config.hotkey)
            self.outside.start()
            self._start_telegram()
            self.scheduler.start()
        else:
            self._stop_telegram("Disabled with Needle")
        self._emit_status()
        self._refresh_settings_status()

    def _load_router_worker(self) -> None:
        # Let the microphone and the Qt event loop settle before a long,
        # CPU-heavy grammar initialization starts on a slow machine.
        time.sleep(6)
        try:
            self.router.load()
            self._router_ready = True
            self._router_error = ""
            log.info("Needle 3 router is ready.")
            self.status_changed.emit("Needle Desktop — Needle 3 ready")
        except Exception as exc:
            self._router_ready = False
            self._router_error = str(exc)
            log.exception("Needle 3 router failed to load.")
            self.notify.emit("Needle 3 is unavailable", str(exc))
        finally:
            self.refresh_status.emit()

    def _emit_status(self) -> None:
        wake = "wake on" if self.config.wake_word_enabled else "wake off"
        router = "Needle 3 ready" if self.router.loaded else "warming up Needle 3"
        telegram = ""
        if self.config.telegram_enabled:
            telegram = " · Telegram on" if (
                self.telegram is not None and self.telegram.running
            ) else " · Telegram off"
        self.status_changed.emit(
            f"Needle Desktop — {wake} · {self.config.hotkey.upper()} · {router}{telegram}"
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
        if self._take_clarification(text):
            return
        if self._handle_undo(text):
            return
        # Phrase shortcuts: "i am gonna study" -> stored step list.
        try:
            from . import shortcuts as _shortcuts

            match = _shortcuts.match(text)
        except Exception:
            match = None
        if match is not None:
            trigger, steps = match
            self._active = True
            self._session += 1
            session = self._session
            self.overlay.set_thinking(f"Shortcut '{trigger}': {len(steps)} step(s)…")
            thread = threading.Thread(
                target=self._plan_worker, args=(steps, session), name="NeedlePlan", daemon=True
            )
            thread.start()
            return
        if looks_like_plan(text):
            self._start_plan(text)
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
            if call.name:
                problem = self._check_app_resolution(call)
                if problem is not None:
                    self.route_failed.emit(problem, session)
                    return
                confidence = call.confidence
                if (
                    call.source not in ("fast", "cache")
                    and confidence is not None
                    and confidence < 0.60
                ):
                    message = call.reasoning or (
                        "I'm not sure what you mean. Try rephrasing that."
                    )
                    log.info("Low-confidence route discarded (%.2f): %s", confidence, message)
                    self.route_failed.emit(message, session)
                    return
            self.route_ready.emit(call, session)
        except Exception as exc:
            log.exception("Route worker failed.")
            self.route_failed.emit(str(exc), session)

    def _check_app_resolution(self, call: ToolCall) -> str | None:
        """Fail early with a clarification when an app name is ambiguous."""
        if call.name not in {"open_application", "close_application"}:
            return None
        raw = str(call.arguments.get("app_name", ""))
        if not raw:
            return None
        try:
            self.automation._resolver.resolve(raw)  # noqa: SLF001 - pre-flight check
        except AmbiguousAppError as exc:
            candidates = list(exc.candidates)
            self._pending_clarification = {
                "kind": "app",
                "tool": call.name,
                "candidates": candidates,
                "created": time.monotonic(),
                "text": call.user_text or "",
            }
            return str(exc)
        except AutomationError as exc:
            return str(exc)
        return None

    @staticmethod
    def _match_candidate(text: str, candidates: list[str]) -> str | None:
        tokens = re.findall(r"[a-z0-9]+", text.lower())
        ordinal_tokens = [
            ("first", 0), ("1st", 0), ("second", 1), ("2nd", 1), ("third", 2),
            ("3rd", 2), ("fourth", 3), ("fifth", 4),
            ("one", 0), ("two", 1), ("three", 2), ("four", 3), ("five", 4),
            ("1", 0), ("2", 1), ("3", 2), ("4", 3), ("5", 4),
        ]
        for token, index in ordinal_tokens:
            if token in tokens and 0 <= index < len(candidates):
                return candidates[index]
        cleaned = " ".join(tokens)
        for candidate in candidates:
            lowered = candidate.lower()
            if lowered in cleaned or cleaned in lowered:
                return candidate
        return None

    def _on_scheduled_fire(self, message: str, action: object) -> None:
        try:
            self.scheduled_fire.emit(str(message or "Reminder"), action)
        except Exception:
            pass

    def _handle_scheduled_fire(self, message: str, action: object) -> None:
        self.notify.emit("Reminder", str(message))
        if action:
            text = str(action)
            thread = threading.Thread(
                target=self._run_scheduled_action, args=(text,), name="NeedleScheduled", daemon=True
            )
            thread.start()

    def _run_scheduled_action(self, text: str) -> None:
        try:
            call = self.router.route(text, use_fast=self.config.fast_intent)
            if not call.name:
                return
            result = self.automation.execute(call, restore_hwnd=0)
            try:
                audit_record(call.name or "", getattr(call, "arguments", {}), result.success, result.message)
            except Exception:
                pass
            if not result.success:
                self.notify.emit("Scheduled action failed", result.message)
        except Exception:
            log.debug("Scheduled action failed.", exc_info=True)

    def _handle_undo(self, text: str) -> bool:
        cleaned = " ".join(str(text or "").split()).lower()
        if not cleaned:
            return False
        if cleaned in {"undo", "undo that", "undo last", "undo last action"}:
            count = self.undo.undo_last()
            if count:
                self.overlay.show_result(
                    ExecutionResult(True, "Undone the last action.", "undo"), None, "needle"
                )
                self.overlay.hide_after(1400)
                self._play_sound("done.wav")
            else:
                self.overlay.show_error("There is nothing to undo.")
                self.overlay.hide_after(2200)
                self._play_sound("error.wav")
            return True
        if cleaned in {"undo all", "undo everything", "undo all actions"}:
            count = self.undo.undo_all()
            self.overlay.show_result(
                ExecutionResult(True, f"Undone {count} action(s).", "undo"), None, "needle"
            )
            self.overlay.hide_after(1600)
            self._play_sound("done.wav")
            return True
        return False

    def _launch_clarified_call(self, call: ToolCall, original: str) -> None:
        self._active = True
        self._session += 1
        session = self._session
        self.overlay.set_thinking(original or call.name or "Working…")
        interactive = call.name in INTERACTIVE_TOOLS
        restore = self._foreground_hwnd if interactive else 0
        if interactive:
            QTimer.singleShot(120, lambda: self._run_execution(call, restore, session))
        else:
            self._run_execution(call, restore, session)

    def _take_clarification(self, text: str) -> bool:
        pending = self._pending_clarification
        if not pending:
            return False
        self._pending_clarification = None
        if time.monotonic() - float(pending.get("created", 0)) > 90:
            return False
        kind = str(pending.get("kind") or "app")
        original = str(pending.get("original") or pending.get("text") or "")
        reply = " ".join(str(text or "").split()).strip()
        if not reply:
            self._pending_clarification = pending
            return True
        if reply.lower() in {"cancel", "cancel that", "never mind", "stop"}:
            self.overlay.show_error("Okay, cancelled.")
            self.overlay.hide_after(1400)
            self._play_sound("done.wav")
            return True
        if kind == "send_message":
            # First missing slot: whom to message.
            from . import memory as _memory

            recipient = reply.strip("\"'").rstrip(".!?")
            digits = re.sub(r"\D", "", recipient)
            is_number = bool(re.fullmatch(r"\s*\+?[0-9][0-9\s().-]{6,}[0-9]\s*", recipient))
            if (
                _memory.get_contact(recipient) is None
                and not is_number
                and len(recipient.split()) > 3
            ):
                # Not a name or number; fall back to treating it as a new command.
                return False
            message = str(pending.get("message") or "")
            if not message:
                self._pending_clarification = {
                    "kind": "send_message_text",
                    "recipient": recipient,
                    "created": time.monotonic(),
                    "original": original,
                }
                self.overlay.show_error("What should the message say?")
                self.overlay.hide_after(3200)
                return True
            call = ToolCall(
                name="send_message",
                arguments={"recipient": recipient, "text": message},
                source="clarified",
                confidence=1.0,
                user_text=f"{original} (clarified: {recipient})".strip(),
            )
            self._launch_clarified_call(call, original)
            return True
        if kind == "send_message_text":
            # Second missing slot: the message body.
            message = reply.strip()
            recipient = str(pending.get("recipient") or "")
            call = ToolCall(
                name="send_message",
                arguments={"recipient": recipient, "text": message},
                source="clarified",
                confidence=1.0,
                user_text=f"{original} (clarified: {recipient})".strip(),
            )
            self._launch_clarified_call(call, original)
            return True
        # kind == "app": pick one of the ambiguous candidates.
        candidates = [str(item) for item in pending.get("candidates", [])]
        if not candidates:
            return False
        choice = self._match_candidate(reply, candidates)
        if choice is None:
            log.info("Clarification answer did not match; treating as a new command.")
            return False
        tool = str(pending.get("tool") or "open_application")
        call = ToolCall(
            name=tool,
            arguments={"app_name": choice},
            source="clarified",
            confidence=1.0,
            user_text=f"{original} (clarified: {choice})".strip(),
        )
        self._launch_clarified_call(call, original)
        return True

    def on_route_ready(self, call: ToolCall, session: int) -> None:
        if session != self._session or not self.overlay.isVisible():
            return
        if call.name == "run_plan":
            steps = call.arguments.get("steps", [])
            if not isinstance(steps, list) or len(steps) < 2:
                self.overlay.show_error("The plan didn't contain usable steps.")
                self.overlay.hide_after(2600)
                self._play_sound("error.wav")
                return
            self.overlay.set_thinking(f"Planning {len(steps)} steps…")
            thread = threading.Thread(
                target=self._plan_worker, args=(steps, session), name="NeedlePlan", daemon=True
            )
            thread.start()
            return
        if call.name == "macro" and str(call.arguments.get("action", "")).lower() == "run":
            from .macros import get as get_macro

            name = str(call.arguments.get("name", "")).strip()
            steps = get_macro(name)
            if not steps:
                self.overlay.show_error(f"No macro named '{name}'.")
                self.overlay.hide_after(2600)
                self._play_sound("error.wav")
                return
            self.overlay.set_thinking(f"Running macro '{name}' ({len(steps)} steps)…")
            thread = threading.Thread(
                target=self._plan_worker, args=(steps, session), name="NeedlePlan", daemon=True
            )
            thread.start()
            return
        if call.name == "memory" and str(call.arguments.get("action", "")).lower() == "set_alias":
            # Persist immediately so later app resolution uses it.
            try:
                from . import memory as _memory

                alias = str(call.arguments.get("alias", "")).strip()
                target = str(call.arguments.get("target", "")).strip()
                if alias and target:
                    _memory.set_alias(alias, target)
            except Exception:
                pass
        if not call.name:
            message = call.error or call.reasoning or "I couldn't map that to a desktop action."
            if call.source == "clarify":
                # The user asked for a message without naming a contact;
                # ask for the missing slot instead of failing.
                self._pending_clarification = {
                    "kind": "send_message",
                    "message": str(call.arguments.get("text") or ""),
                    "created": time.monotonic(),
                    "original": call.user_text or "",
                }
                self.overlay.show_error("Who should I send it to?")
                self.overlay.hide_after(3200)
                self._play_sound("error.wav")
                log.info("Asking for the message recipient.")
                return
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
            if call.name == "system_control" and not self.config.allow_power_actions:
                result = ExecutionResult(
                    False, "Power actions are disabled in Settings.", call.name or ""
                )
            elif is_destructive(call) and self.config.confirm_destructive:
                if not self._confirm_local(destructive_description(call)):
                    result = ExecutionResult(
                        False, "Cancelled — no changes were made.", call.name or ""
                    )
                else:
                    result = self.automation.execute(call, restore_hwnd=restore_hwnd)
            else:
                result = self.automation.execute(call, restore_hwnd=restore_hwnd)
        except Exception as exc:
            log.exception("Automation worker crashed.")
            result = ExecutionResult(False, f"The action failed: {exc}", call.name or "")
        log.info(
            "Action %s finished in %.2fs: success=%s",
            call.name, time.monotonic() - started, result.success,
        )
        self.execution_ready.emit(result, call, session)

    # ------------------------------------------------------------------ planning
    def _start_plan(self, text: str) -> None:
        steps = split_plan(text)
        if not steps or len(steps) < 2:
            self._active = True
            self._session += 1
            session = self._session
            self.overlay.set_thinking(text)
            thread = threading.Thread(
                target=self._route_worker, args=(text, session), name="NeedleRoute", daemon=True
            )
            thread.start()
            return
        self._active = True
        self._session += 1
        session = self._session
        self.overlay.set_thinking(f"Planning {len(steps)} steps…")
        thread = threading.Thread(
            target=self._plan_worker, args=(steps, session), name="NeedlePlan", daemon=True
        )
        thread.start()

    def _plan_worker(self, steps: list[str], session: int) -> None:
        total = len(steps)
        overall_success = True
        final_message = f"Completed {total} step(s)."
        last_result_message = ""
        for index, step_text in enumerate(steps, 1):
            if session != self._session:
                return
            self.plan_progress.emit(f"Step {index}/{total}: {step_text}", index, total)
            try:
                call = self.router.route(step_text, use_fast=self.config.fast_intent)
            except Exception as exc:
                final_message = f"Step {index} failed: {exc}"
                overall_success = False
                break
            if call.name and not call.user_text:
                call.user_text = step_text
            if not call.name:
                message = call.error or call.reasoning or "I couldn't map that to an action."
                final_message = f"Step {index} failed: {message}"
                overall_success = False
                break
            if call.name == "run_plan":
                final_message = f"Step {index} failed: nested plans are not allowed."
                overall_success = False
                break
            # App-name ambiguity / confidence handling is skipped for plan steps
            # to keep the flow moving; the resolver still raises on no match.
            interactive = call.name in INTERACTIVE_TOOLS
            restore = self._foreground_hwnd if interactive else 0
            if interactive:
                time.sleep(0.08)
            # Confirmation gate for destructive steps.
            if is_destructive(call) and self.config.confirm_destructive:
                if not self._confirm_local(destructive_description(call)):
                    final_message = f"Step {index} cancelled."
                    overall_success = False
                    break
            if call.name == "system_control" and not self.config.allow_power_actions:
                final_message = f"Step {index} failed: power actions are disabled."
                overall_success = False
                break
            result = self.automation.execute(call, restore_hwnd=restore)
            # One retry for transient failures.
            if (not result.success) and is_transient_failure(result.message):
                time.sleep(0.8)
                result = self.automation.execute(call, restore_hwnd=restore)
            try:
                audit_record(call.name or "", getattr(call, "arguments", {}), result.success, result.message)
            except Exception:
                pass
            # Record undo inverse when possible.
            inverse = self._inverse_for(call, result)
            if inverse is not None:
                self.undo.record(inverse)
            self.plan_step_done.emit(call, result, index, total)
            last_result_message = result.message
            if not result.success:
                final_message = f"Step {index} failed: {result.message}"
                overall_success = False
                break
        if overall_success and total == 1 and last_result_message:
            # Study/report steps produce useful text; don't replace it with
            # a generic "Completed 1 step(s)" summary.
            final_message = last_result_message
        if overall_success:
            self.automation.set_last_plan_steps(steps)
            try:
                from . import memory as _memory

                _memory.record_history(" | ".join(steps), "run_plan")
            except Exception:
                pass
        self.plan_done.emit(overall_success, final_message, session)

    def _inverse_for(self, call: ToolCall, result: ExecutionResult) -> Callable[[], None] | None:
        import functools

        if call.name == "window_manage":
            action = str(call.arguments.get("action", ""))
            title = str(call.arguments.get("title", ""))
            if action == "minimize":
                return lambda: self.automation.execute(
                    ToolCall(name="window_manage", arguments={"title": title, "action": "restore"})
                )
            if action == "maximize":
                return lambda: self.automation.execute(
                    ToolCall(name="window_manage", arguments={"title": title, "action": "restore"})
                )
            return None
        if call.name == "focus_window":
            previous = self._last_external_hwnd
            if previous:
                return lambda: windows_api.focus_window(previous)
            return None
        if call.name == "open_application":
            app_name = str(call.arguments.get("app_name", ""))
            if app_name:
                return lambda: self.automation.execute(
                    ToolCall(name="close_application", arguments={"app_name": app_name})
                )
            return None
        if call.name == "close_application":
            app_name = str(call.arguments.get("app_name", ""))
            if app_name:
                return lambda: self.automation.execute(
                    ToolCall(name="open_application", arguments={"app_name": app_name})
                )
            return None
        if call.name == "file_operation":
            op = str(call.arguments.get("op", ""))
            path = str(call.arguments.get("path", ""))
            new_path = str(call.arguments.get("new_path", ""))
            if op == "rename" and new_path:
                return lambda: self.automation.execute(
                    ToolCall(name="file_operation", arguments={"op": "rename", "path": new_path, "new_path": path})
                )
            if op == "move" and new_path:
                return lambda: self.automation.execute(
                    ToolCall(name="file_operation", arguments={"op": "move", "path": new_path, "new_path": path})
                )
            if op == "create":
                return lambda: self.automation.execute(
                    ToolCall(name="file_operation", arguments={"op": "delete", "path": path})
                )
            return None
        if call.name == "clipboard" and str(call.arguments.get("action", "")) == "set":
            previous = getattr(self, "_clipboard_before", "")
            text = str(call.arguments.get("text", ""))
            self._clipboard_before = text
            return lambda: self.automation.execute(
                ToolCall(name="clipboard", arguments={"action": "set", "text": previous})
            )
        return None

    def on_plan_progress(self, text: str, index: int, total: int) -> None:
        if not self.overlay.isVisible():
            return
        self.overlay.set_thinking(text)

    def on_plan_step_done(self, call: ToolCall, result: ExecutionResult, index: int, total: int) -> None:
        if not self.overlay.isVisible():
            return
        self.overlay.show_result(result, call, call.source)

    def on_plan_done(self, success: bool, message: str, session: int) -> None:
        if session != self._session:
            return
        if success:
            self.overlay.show_result(
                ExecutionResult(True, message, "run_plan"), None, "needle"
            )
            self._play_sound("done.wav")
        else:
            self.overlay.show_error(message)
            self._play_sound("error.wav")
        self.overlay.hide_after(self._result_hide_ms(message, success))
        self._active = False
        try:
            self.undo.clear() if not success else None
        except Exception:
            pass

    # ------------------------------------------------------------------ confirmation
    def _confirm_local(self, description: str) -> bool:
        """Ask on the PC before a destructive action. Safe from worker threads."""
        if threading.current_thread() is threading.main_thread():
            return self._show_confirmation_dialog(description)
        done = threading.Event()
        outcome = [False]
        try:
            self.confirm_needed.emit(description, done, outcome)
        except Exception:
            return False
        done.wait(30.0)
        return bool(outcome[0])

    def _on_confirm_needed(self, description: str, done: object, outcome: list) -> None:
        accepted = self._show_confirmation_dialog(str(description))
        try:
            outcome[0] = accepted
        except Exception:
            pass
        event = done
        if isinstance(event, threading.Event):
            event.set()

    def _show_confirmation_dialog(self, description: str) -> bool:
        try:
            box = QMessageBox(self.overlay)
            box.setIcon(QMessageBox.Icon.Warning)
            box.setWindowTitle("Needle Desktop — confirm")
            box.setText(f"{description}?")
            box.setInformativeText(
                "This can be disruptive. Only continue if you really meant it."
            )
            box.setStandardButtons(
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            box.setDefaultButton(QMessageBox.StandardButton.No)
            yes_button = box.button(QMessageBox.StandardButton.Yes)
            no_button = box.button(QMessageBox.StandardButton.No)
            if yes_button:
                yes_button.setText("Do it")
            if no_button:
                no_button.setText("Cancel")
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(lambda: box.reject())
            timer.start(20000)
            try:
                answer = box.exec()
            finally:
                timer.stop()
            return answer == QMessageBox.StandardButton.Yes
        except Exception:
            log.debug("Could not show the confirmation dialog.", exc_info=True)
            return False

    @staticmethod
    def _result_hide_ms(message: str, success: bool) -> int:
        if not success:
            return 3200
        return max(1400, min(9000, 1000 + len(str(message or "")) * 40))

    def on_study_notice(self, message: str, is_error: bool) -> None:
        if self._quitting:
            return
        text = str(message or "").strip()
        if not text:
            return
        if is_error:
            self.overlay.show_error(text)
            self._play_sound("error.wav")
        else:
            self.overlay.show_result(
                ExecutionResult(True, text, "study_mode"), None, "needle"
            )
            self._play_sound("done.wav")
        self.overlay.hide_after(self._result_hide_ms(text, not is_error))
        self.notify.emit("Class is live" if not is_error else "Study watch", text)

    def on_execution_ready(self, result: ExecutionResult, call: ToolCall, session: int) -> None:
        if session != self._session:
            log.info("Discarding stale execution result for %s.", call.name)
            return
        self.overlay.show_result(result, call, call.source)
        self.overlay.hide_after(self._result_hide_ms(result.message, result.success))
        self._play_sound("done.wav" if result.success else "error.wav")
        if not result.success:
            self.notify.emit("Needle couldn't finish", result.message)
        try:
            from . import memory as _memory

            _memory.record_history(getattr(call, "user_text", "") or "", call.name or "")
        except Exception:
            pass
        try:
            audit_record(call.name or "", getattr(call, "arguments", {}), result.success, result.message)
        except Exception:
            pass

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
        if status in ("Ready", "Listening"):
            self._audio_error = ""
        self._refresh_settings_status()

    def on_listening_changed(self, listening: bool) -> None:
        if listening:
            self.status_changed.emit("Needle Desktop — listening…")
        else:
            self._emit_status()

    def toggle_enabled(self, enabled: bool) -> None:
        enabled = bool(enabled)
        if enabled == self.enabled and self.config.enabled == enabled:
            self.overlay.enabled = enabled
            return
        self.enabled = enabled
        self.config.enabled = enabled
        self.config.validate()
        save_config(self.config)
        if enabled:
            try:
                self.audio.start()
            except Exception:
                log.exception("Could not start audio.")
            try:
                self.hotkey.start(self.config.hotkey)
            except Exception:
                log.exception("Could not start hotkey.")
            try:
                self.outside.start()
            except Exception:
                log.exception("Could not start outside watcher.")
            self.overlay.enabled = True
            self.overlay.show_welcome()
            self._start_telegram()
        else:
            try:
                self.hotkey.stop()
            except Exception:
                pass
            try:
                self.outside.stop()
            except Exception:
                pass
            try:
                self.audio.stop()
            except Exception:
                pass
            self.scheduler.stop()
            self.overlay.enabled = False
            self.overlay.hide_immediately()
            self._stop_telegram("Disabled with Needle")
        if self.settings is not None:
            self.settings.config = self.config
            try:
                self.settings.enable_check.setChecked(bool(enabled))
            except Exception:
                pass
        self.config_changed.emit(self.config)
        self._emit_status()
        self._refresh_settings_status()

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
        self.settings.set_status(
            needle=needle,
            speech=speech,
            microphone=mic,
            wake=wake,
            telegram=self._telegram_status,
        )

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
        telegram_changed = (
            old.telegram_enabled != self.config.telegram_enabled
            or old.telegram_bot_token != self.config.telegram_bot_token
            or old.telegram_allowed_chat_ids != self.config.telegram_allowed_chat_ids
        )
        if old.enabled != self.config.enabled:
            self.toggle_enabled(self.config.enabled)
        else:
            if telegram_changed:
                self._start_telegram()
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

    # ------------------------------------------------------------------ telegram
    def _start_telegram(self) -> None:
        if not self.enabled:
            self._stop_telegram("Disabled with Needle")
            return
        if not self.config.telegram_enabled:
            self._stop_telegram("Off")
            return
        token = (self.config.telegram_bot_token or "").strip()
        if not token:
            self._stop_telegram("Token missing")
            return
        bot = self.telegram
        if bot is not None and bot.running and self._telegram_token == token:
            bot.update_allowed_chat_ids(self.config.telegram_allowed_chat_ids)
            return
        self._stop_telegram("Off")
        bot = TelegramBot(
            token=token,
            allowed_chat_ids=self.config.telegram_allowed_chat_ids,
            handler=self._execute_telegram_command,
            status_provider=self._telegram_status_text,
            on_pair=self._on_telegram_paired,
            classify=self._classify_for_telegram,
            confirmation_required=self._confirmation_required_for_telegram,
            executor=self._execute_telegram_command,
            confirmation_store=self._confirmation_store,
            undo_handler=self._telegram_undo,
        )
        self.telegram = bot
        self._telegram_token = token
        bot.status_changed.connect(self.on_telegram_status)
        bot.start()

    def _on_telegram_paired(self, chat_id: int) -> None:
        if chat_id not in self.config.telegram_allowed_chat_ids:
            self.config.telegram_allowed_chat_ids.append(chat_id)
            save_config(self.config)
            log.info("Persisted auto-paired Telegram chat ID %s to config.", chat_id)
            if self.settings is not None:
                self.settings.config = self.config
                self.settings.telegram_ids_edit.setText(", ".join(str(c) for c in self.config.telegram_allowed_chat_ids))

    def _stop_telegram(self, status: str = "Off") -> None:
        bot = self.telegram
        self.telegram = None
        self._telegram_token = ""
        if bot is not None:
            try:
                bot.status_changed.disconnect(self.on_telegram_status)
            except (TypeError, RuntimeError):
                pass
            try:
                bot.stop(wait=2.0)
            except Exception:
                log.debug("Could not stop the Telegram bot cleanly.", exc_info=True)
        self._set_telegram_status(status)

    def on_telegram_status(self, status: str) -> None:
        self._set_telegram_status(status)

    def _set_telegram_status(self, status: str) -> None:
        status = str(status or "Off")
        if status == self._telegram_status:
            return
        self._telegram_status = status
        log.info("Telegram: %s", status)
        if not self._quitting:
            self._refresh_settings_status()

    def _telegram_status_text(self) -> str:
        enabled = "enabled" if self.enabled else "disabled"
        router = "ready" if self.router.loaded else (
            "error" if self._router_error else "warming up"
        )
        wake = "on" if self.config.wake_word_enabled else "off"
        mic = (
            "system default"
            if self.config.microphone_index is None
            else f"device {self.config.microphone_index}"
        )
        return (
            f"{__app_name__} {__version__}\n"
            f"Enabled: {enabled}\n"
            f"Needle 3: {router}\n"
            f"Wake word: {wake}\n"
            f"Microphone: {mic}\n"
            f"Telegram: {self._telegram_status}"
        )

    def _classify_for_telegram(self, text: str) -> ToolCall | None:
        try:
            call = self.router.route(text, use_fast=self.config.fast_intent)
            if call.name and not call.user_text:
                call.user_text = text
            return call
        except Exception:
            log.debug("Telegram classification failed.", exc_info=True)
            return None

    def _confirmation_required_for_telegram(self, call: ToolCall) -> bool:
        if call is None or not call.name:
            return False
        if not self.config.confirm_destructive:
            return False
        return is_destructive(call)

    def _telegram_undo(self) -> tuple[int, str]:
        count = self.undo.undo_last()
        if count:
            return count, "Undone the last action."
        return 0, "There is nothing to undo."

    def _execute_telegram_command(self, text: str) -> tuple[ToolCall, ExecutionResult]:
        text = " ".join((text or "").split())
        if not text:
            message = "I didn't receive a command."
            return (
                ToolCall(name=None, error=message, user_text=text),
                ExecutionResult(False, message),
            )
        if not self.enabled:
            message = "Needle Desktop is disabled. Enable it in Settings."
            return (
                ToolCall(name=None, error=message, user_text=text),
                ExecutionResult(False, message),
            )

        if not self.router.loaded:
            quick = fast_route(text) if self.config.fast_intent else None
            if quick is not None:
                call = quick
            elif self._router_error:
                message = f"Needle 3 is unavailable: {self._router_error}"
                return (
                    ToolCall(name=None, error=message, user_text=text),
                    ExecutionResult(False, message),
                )
            else:
                message = "Needle 3 is still warming up. Try again in a moment."
                return (
                    ToolCall(name=None, error=message, user_text=text),
                    ExecutionResult(False, message),
                )
        else:
            call = self.router.route(text, use_fast=self.config.fast_intent)

        if not call.name:
            message = call.error or call.reasoning or "I couldn't map that to a desktop action."
            return call, ExecutionResult(False, message, call.name or "")
        if call.name == "system_control" and not self.config.allow_power_actions:
            message = "Power actions are disabled in Settings."
            return call, ExecutionResult(False, message, call.name or "")
        if self.config.log_transcripts:
            log.info("Telegram command: %r -> %s", text, call.name)
        else:
            log.info("Telegram command routed to %s", call.name)
        try:
            # Interactive actions target the window that was in front when the
            # remote command arrived, matching the local command flow.
            restore = windows_api.get_foreground_window()
        except Exception:
            restore = 0
        try:
            result = self.automation.execute(call, restore_hwnd=restore)
        except Exception as exc:
            log.exception("Telegram automation failed.")
            result = ExecutionResult(False, f"The action failed: {exc}", call.name or "")
        log.info("Telegram action %s finished: success=%s", call.name, result.success)
        return call, result

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
            self.automation.stop_study_watcher()
        except Exception:
            pass
        try:
            self._stop_telegram("Off")
        except Exception:
            pass
        try:
            self.scheduler.stop()
        except Exception:
            pass
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
