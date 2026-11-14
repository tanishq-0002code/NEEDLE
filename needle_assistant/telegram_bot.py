from __future__ import annotations

import logging
import re
import threading
from pathlib import Path
from typing import Callable, Sequence

import requests
from PyQt6.QtCore import QObject, pyqtSignal

from . import __version__
from .catalog import destructive_description
from .confirmation import ConfirmationStore
from .models import ExecutionResult, ToolCall

log = logging.getLogger("needle.telegram")

API_ROOT = "https://api.telegram.org/bot{token}/{method}"
POLL_TIMEOUT = 10
MAX_TEXT = 4000
MAX_CAPTION = 1000

CommandHandler = Callable[[str], tuple[ToolCall, ExecutionResult]]
StatusProvider = Callable[[], str]


class TelegramError(RuntimeError):
    pass


class TelegramAuthError(TelegramError):
    pass


class TelegramConflict(TelegramError):
    pass


class TelegramAPIError(TelegramError):
    pass


class TelegramBot(QObject):
    """Long-polling Telegram bridge for the local Needle controller.

    The bot owns a small worker thread.  Command execution is delegated to the
    supplied handler, which runs on that worker thread and is expected to be
    thread-safe (NeedleRouter and DesktopAutomation both are).
    """

    status_changed = pyqtSignal(str)

    def __init__(
        self,
        token: str,
        allowed_chat_ids: Sequence[int],
        handler: CommandHandler,
        status_provider: StatusProvider,
        on_pair: Callable[[int], None] | None = None,
        *,
        classify: Callable[[str], ToolCall | None] | None = None,
        confirmation_required: Callable[[ToolCall], bool] | None = None,
        executor: Callable[[str], tuple[ToolCall, ExecutionResult]] | None = None,
        confirmation_store: ConfirmationStore | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._token = str(token or "").strip()
        self._allowed = {int(value) for value in allowed_chat_ids}
        self._handler = handler
        self._executor = executor or handler
        self._status_provider = status_provider
        self._on_pair = on_pair
        self._classify = classify
        self._confirmation_required = confirmation_required
        self._confirmation_store = confirmation_store
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": f"NeedleDesktop/{__version__}"})
        self._offset = 0
        self._username = ""
        self._last_status = ""
        self._network_warned = False

    # ------------------------------------------------------------------ state
    @property
    def username(self) -> str:
        return self._username

    @property
    def running(self) -> bool:
        thread = self._thread
        return thread is not None and thread.is_alive() and not self._stop_event.is_set()

    def update_allowed_chat_ids(self, allowed_chat_ids: Sequence[int]) -> None:
        self._allowed = {int(value) for value in allowed_chat_ids}
        if self._username:
            self._set_status(self._connected_status())

    def start(self) -> None:
        if not self._token:
            self._set_status("Token missing")
            return
        if self.running:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, name="TelegramBot", daemon=True)
        self._thread.start()

    def stop(self, wait: float = 2.0) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=max(0.0, wait))
        self._thread = None
        self._set_status("Off")

    # ------------------------------------------------------------------ loop
    def _run(self) -> None:
        log.info("Telegram bot starting.")
        self._set_status("Connecting…")
        backlog_skipped = False
        while not self._stop_event.is_set():
            try:
                if not self._username:
                    me = self._api("getMe", http_timeout=15.0)
                    if not isinstance(me, dict) or not me.get("username"):
                        raise TelegramAPIError("Telegram did not return a bot username.")
                    self._username = str(me["username"])
                    log.info("Telegram bot authenticated as @%s.", self._username)

                if not backlog_skipped:
                    self._skip_backlog()
                    backlog_skipped = True

                self._set_status(self._connected_status())
                updates = self._get_updates()
                if self._stop_event.is_set():
                    break
                self._network_warned = False
                for update in updates:
                    if self._stop_event.is_set():
                        break
                    if not isinstance(update, dict):
                        continue
                    update_id = int(update.get("update_id", 0) or 0)
                    if update_id:
                        self._offset = max(self._offset, update_id + 1)
                    try:
                        self._handle_update(update)
                    except Exception:
                        log.exception("Telegram update handling failed.")
            except TelegramAuthError as exc:
                self._set_status("Invalid bot token")
                log.error("Telegram authentication failed: %s", self._safe_error(exc))
                return
            except TelegramConflict as exc:
                self._set_status("Another instance is polling")
                log.warning("Telegram conflict: %s", self._safe_error(exc))
                self._stop_event.wait(8.0)
            except requests.RequestException as exc:
                self._set_status("Connection error — retrying")
                if not self._network_warned:
                    log.warning("Telegram network error: %s", self._safe_error(exc))
                    self._network_warned = True
                else:
                    log.debug("Telegram network error: %s", self._safe_error(exc))
                self._stop_event.wait(3.0)
            except TelegramAPIError as exc:
                self._set_status("Telegram error — retrying")
                log.warning("Telegram API error: %s", self._safe_error(exc))
                self._stop_event.wait(5.0)
            except Exception as exc:
                self._set_status("Telegram error — retrying")
                # Do not log the traceback: requests exceptions can contain the
                # bot token inside the API URL.
                log.error("Telegram bot loop crashed: %s", self._safe_error(exc))
                self._stop_event.wait(5.0)
        self._set_status("Off")
        log.info("Telegram bot stopped.")

    def _skip_backlog(self) -> None:
        result = self._api(
            "getUpdates",
            http_timeout=15.0,
            offset=-1,
            limit=1,
            allowed_updates=["message"],
        )
        if isinstance(result, list) and result:
            last = result[-1]
            if isinstance(last, dict):
                self._offset = max(self._offset, int(last.get("update_id", 0) or 0) + 1)

    def _get_updates(self) -> list[dict]:
        result = self._api(
            "getUpdates",
            http_timeout=POLL_TIMEOUT + 20,
            offset=self._offset,
            timeout=POLL_TIMEOUT,
            allowed_updates=["message"],
        )
        if not isinstance(result, list):
            return []
        return [item for item in result if isinstance(item, dict)]

    # ------------------------------------------------------------------ update
    def _handle_update(self, update: dict) -> None:
        message = update.get("message")
        if not isinstance(message, dict):
            return
        text = str(message.get("text") or "").strip()
        if not text:
            return
        chat = message.get("chat") or {}
        chat_id = chat.get("id")
        if chat_id is None:
            return
        try:
            chat_id = int(chat_id)
        except (TypeError, ValueError):
            return

        command = ""
        if text.startswith("/"):
            command = text.split()[0].split("@", 1)[0].lower()

        if command in {"/start", "/help"}:
            self._send_help(chat_id)
            return
        if command == "/id":
            self._send_html(chat_id, f"Your Telegram chat ID is:\n<code>{chat_id}</code>")
            return
        if not self._authorized(chat_id):
            if not self._allowed:
                # Zero-friction auto-pairing for the bot owner
                self._allowed.add(chat_id)
                log.info("Telegram bot auto-paired with chat ID %s.", chat_id)
                if self._on_pair is not None:
                    try:
                        self._on_pair(chat_id)
                    except Exception:
                        pass
                self._send(chat_id, f"Needle Desktop paired with chat ID {chat_id}. Processing your command…")
            else:
                self._send(
                    chat_id,
                    "This Needle bot is not paired with this chat yet.\n\n"
                    f"Your chat ID is: {chat_id}\n\n"
                    "Open Needle Desktop → Settings → Telegram and add this ID to "
                    "Allowed chat IDs. Until then, commands are ignored.",
                )
                return

        if command == "/confirm":
            pending = (
                self._confirmation_store.take(chat_id)
                if self._confirmation_store is not None
                else None
            )
            if pending is None:
                self._send(chat_id, "Nothing to confirm. Send a command first.")
                return
            self._send(chat_id, f"Confirmed: {pending.description}")
            self._send_typing(chat_id)
            self._execute_remote(pending.text, chat_id)
            return
        if command == "/cancel":
            if self._confirmation_store is not None:
                self._confirmation_store.clear(chat_id)
            self._send(chat_id, "Cancelled. Nothing was changed.")
            return

        if command == "/status":
            try:
                status = self._status_provider() or "No status available."
            except Exception:
                log.exception("Telegram status provider failed.")
                status = "Status is unavailable right now."
            self._send(chat_id, status)
            return
        if command == "/screenshot":
            text = "take a screenshot"
        elif command in {"/camera", "/photo"}:
            text = "capture camera"
        elif command:
            self._send(chat_id, "Unknown command. Send /help for the list.")
            return

        if (
            self._classify is not None
            and self._confirmation_required is not None
            and command == ""
        ):
            try:
                call = self._classify(text)
            except Exception:
                call = None
            if call is not None and self._confirmation_required(call):
                if self._confirmation_store is None:
                    self._send(
                        chat_id,
                        "This action needs confirmation on the PC. It was not run.",
                    )
                    return
                if not self._confirmation_store.request(
                    chat_id, text, destructive_description(call)
                ):
                    self._send(
                        chat_id,
                        "You already have an action waiting. Reply /confirm or /cancel.",
                    )
                    return
                self._send(
                    chat_id,
                    f"⚠️ {destructive_description(call)}.\n"
                    "Reply /confirm to proceed or /cancel to stop.",
                )
                return

        self._send_typing(chat_id)
        self._execute_remote(text, chat_id)

    def _execute_remote(self, text: str, chat_id: int) -> None:
        try:
            call, result = self._handler(text)
        except Exception as exc:
            log.exception("Telegram command failed.")
            self._send(chat_id, f"Not done: {self._safe_error(exc)}")
            return

        if call is None or not call.name:
            reason = "I couldn't map that to a desktop action."
            if call is not None and (call.error or call.reasoning):
                reason = call.error or call.reasoning
            self._send(chat_id, f"Not done: {reason}")
            return
        if result is None:
            self._send(chat_id, f"Not done: {call.name} did not return a result.")
            return

        screenshot_path: Path | None = None
        if result.tool in {"take_screenshot", "capture_camera"} and result.path:
            try:
                candidate = Path(result.path)
                if candidate.is_file():
                    screenshot_path = candidate
            except OSError:
                screenshot_path = None
        if screenshot_path is not None:
            caption = (
                f"Done: {result.message or 'Captured image'}"
                if result.success
                else f"Not done: {result.message}"
            )
            self._send_photo(chat_id, screenshot_path, caption)
            return

        if result.success:
            self._send(chat_id, f"Done: {result.message or call.name}")
        else:
            self._send(chat_id, f"Not done: {result.message or 'Action failed'}")

    def _authorized(self, chat_id: int) -> bool:
        return bool(self._allowed) and int(chat_id) in self._allowed

    # ------------------------------------------------------------------ send
    def _send_help(self, chat_id: int) -> None:
        paired = "paired" if self._authorized(chat_id) else "not paired yet"
        text = (
            "<b>Needle Desktop</b>\n"
            f"Pairing: {paired}\n\n"
            "Send a plain-text command and Needle will run it on the desktop:\n"
            "• <code>open Notepad</code>\n"
            "• <code>type hello world</code>\n"
            "• <code>take a screenshot</code>\n\n"
            "<b>Commands</b>\n"
            "/status — app, router, and bot status\n"
            "/screenshot — capture the screen and send it here\n"
            "/id — show this chat ID\n"
            "/help — this message\n"
        )
        if not self._authorized(chat_id):
            text += (
                "\n<b>Pair this chat</b>\n"
                f"Add chat ID <code>{chat_id}</code> in Needle Settings → Telegram → "
                "Allowed chat IDs. The bot runs in pairing mode until then."
            )
        self._send_html(chat_id, text)

    def _send(self, chat_id: int, text: str) -> None:
        text = str(text or "").strip()
        if not text:
            return
        if len(text) > MAX_TEXT:
            text = text[: MAX_TEXT - 1] + "…"
        try:
            self._api(
                "sendMessage",
                chat_id=chat_id,
                text=text,
                disable_web_page_preview=True,
            )
        except Exception as exc:
            log.warning("Could not send Telegram message: %s", self._safe_error(exc))

    def _send_html(self, chat_id: int, text: str) -> None:
        text = str(text or "").strip()
        if not text:
            return
        if len(text) > MAX_TEXT:
            text = text[: MAX_TEXT - 1] + "…"
        try:
            self._api(
                "sendMessage",
                chat_id=chat_id,
                text=text,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
        except TelegramAPIError as exc:
            log.warning("Telegram HTML message failed; falling back to text: %s",
                        self._safe_error(exc))
            self._send(chat_id, text)
        except Exception as exc:
            log.warning("Could not send Telegram message: %s", self._safe_error(exc))

    def _send_typing(self, chat_id: int) -> None:
        try:
            self._api("sendChatAction", chat_id=chat_id, action="typing")
        except Exception as exc:
            log.debug("Could not send Telegram typing action: %s", self._safe_error(exc))

    def _send_photo(self, chat_id: int, path: Path, caption: str) -> None:
        caption = str(caption or "").strip()
        if len(caption) > MAX_CAPTION:
            caption = caption[: MAX_CAPTION - 1] + "…"
        try:
            with path.open("rb") as handle:
                self._api(
                    "sendPhoto",
                    files={"photo": handle},
                    chat_id=chat_id,
                    caption=caption or "Screenshot",
                )
        except Exception as exc:
            log.warning("Could not send Telegram photo: %s", self._safe_error(exc))
            self._send(chat_id, "The screenshot was saved locally, but I could not upload it.")

    # ------------------------------------------------------------------ api
    def _api(
        self,
        method: str,
        *,
        http_timeout: float = 20.0,
        files: dict[str, object] | None = None,
        **params,
    ):
        url = API_ROOT.format(token=self._token, method=method)
        if files:
            response = self._session.post(
                url, data=params, files=files, timeout=(10.0, http_timeout)
            )
        else:
            response = self._session.post(
                url, json=params, timeout=(10.0, http_timeout)
            )
        if response.status_code == 401:
            raise TelegramAuthError("Telegram rejected the bot token.")
        if response.status_code == 409:
            raise TelegramConflict("Another Telegram client is polling this bot token.")
        try:
            payload = response.json()
        except ValueError as exc:
            raise TelegramAPIError(
                f"Telegram returned HTTP {response.status_code}."
            ) from exc
        if not isinstance(payload, dict):
            raise TelegramAPIError("Telegram returned an unexpected response.")
        if response.status_code >= 400 or not payload.get("ok"):
            description = str(
                payload.get("description") or f"HTTP {response.status_code}"
            )
            lowered = description.lower()
            if "unauthorized" in lowered or ("token" in lowered and "not found" in lowered):
                raise TelegramAuthError(description)
            if "conflict" in lowered or "terminated by other" in lowered:
                raise TelegramConflict(description)
            raise TelegramAPIError(description)
        return payload.get("result")

    def _safe_error(self, exc: BaseException) -> str:
        text = str(exc) or exc.__class__.__name__
        if self._token:
            text = text.replace(self._token, "<bot-token>")
        return re.sub(
            r"(https?://api\.telegram\.org/bot)[^/\s]+",
            r"\1<bot-token>",
            text,
        )

    def _connected_status(self) -> str:
        if not self._username:
            return "Connecting…"
        suffix = " · pairing mode" if not self._allowed else ""
        return f"Connected as @{self._username}{suffix}"

    def _set_status(self, status: str) -> None:
        status = str(status or "Off")
        if status == self._last_status:
            return
        self._last_status = status
        try:
            self.status_changed.emit(status)
        except RuntimeError:
            # The Qt object may already be gone during interpreter shutdown.
            pass
