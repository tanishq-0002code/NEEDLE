from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from needle_assistant.models import ExecutionResult, ToolCall
from needle_assistant.telegram_bot import TelegramBot


class FakeTelegramBot(TelegramBot):
    """TelegramBot with the network calls replaced by captured output."""

    def __init__(self, allowed_chat_ids, handler=None) -> None:
        super().__init__(
            token="123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZ",
            allowed_chat_ids=allowed_chat_ids,
            handler=handler or self._open_notepad,
            status_provider=lambda: "Needle Desktop\nTelegram: connected",
        )
        self.sent: list[tuple[int, str]] = []
        self.photos: list[tuple[int, str]] = []
        self.typing: list[int] = []

    @staticmethod
    def _open_notepad(text: str) -> tuple[ToolCall, ExecutionResult]:
        return (
            ToolCall(name="open_application", arguments={"app_name": "Notepad"}),
            ExecutionResult(True, "Opened Notepad.", "open_application"),
        )

    def _send(self, chat_id: int, text: str) -> None:
        self.sent.append((chat_id, text))

    def _send_html(self, chat_id: int, text: str) -> None:
        self.sent.append((chat_id, text))

    def _send_typing(self, chat_id: int) -> None:
        self.typing.append(chat_id)

    def _send_photo(self, chat_id: int, path: Path, caption: str) -> None:
        self.photos.append((chat_id, str(path)))

    def handle(self, text: str, chat_id: int = 123) -> None:
        self._handle_update({"message": {"chat": {"id": chat_id}, "text": text}})


class TelegramBotTests(unittest.TestCase):
    def test_unpaired_chat_gets_its_id_but_cannot_run_commands(self) -> None:
        bot = FakeTelegramBot([])
        bot.handle("open notepad")
        self.assertEqual(len(bot.sent), 1)
        self.assertIn("123", bot.sent[0][1])
        self.assertIn("not paired", bot.sent[0][1].lower())
        self.assertEqual(bot.typing, [])

    def test_paired_chat_runs_a_command(self) -> None:
        bot = FakeTelegramBot([123])
        bot.handle("open notepad")
        self.assertEqual(bot.typing, [123])
        self.assertTrue(any("done" in text.lower() for _, text in bot.sent))

    def test_id_and_help_work_before_pairing(self) -> None:
        bot = FakeTelegramBot([])
        bot.handle("/id")
        self.assertIn("123", bot.sent[0][1])
        bot.handle("/help")
        self.assertIn("Pair this chat", bot.sent[-1][1])

    def test_unknown_slash_command(self) -> None:
        bot = FakeTelegramBot([123])
        bot.handle("/banana")
        self.assertIn("Unknown command", bot.sent[-1][1])

    def test_unmapped_command_returns_the_reason(self) -> None:
        def handler(text: str) -> tuple[ToolCall, ExecutionResult]:
            return ToolCall(name=None, error="No matching action"), ExecutionResult(False, "")

        bot = FakeTelegramBot([123], handler=handler)
        bot.handle("do a barrel roll")
        self.assertIn("No matching action", bot.sent[-1][1])

    def test_failed_action_is_prefixed(self) -> None:
        def handler(text: str) -> tuple[ToolCall, ExecutionResult]:
            return (
                ToolCall(name="open_application", arguments={"app_name": "Nope"}),
                ExecutionResult(False, "Could not find Nope.", "open_application"),
            )

        bot = FakeTelegramBot([123], handler=handler)
        bot.handle("open nope")
        self.assertTrue("not done" in bot.sent[-1][1].lower())
        self.assertIn("Could not find Nope", bot.sent[-1][1])

    def test_screenshot_is_sent_as_a_photo(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "shot.png"
            path.write_bytes(b"\x89PNG\r\n\x1a\n")

            def handler(text: str) -> tuple[ToolCall, ExecutionResult]:
                return (
                    ToolCall(name="take_screenshot", arguments={}),
                    ExecutionResult(
                        True, "Saved a screenshot.", "take_screenshot", path=str(path)
                    ),
                )

            bot = FakeTelegramBot([123], handler=handler)
            bot.handle("/screenshot")
            self.assertEqual(bot.photos, [(123, str(path))])

    def test_allowed_ids_can_be_updated_in_place(self) -> None:
        bot = FakeTelegramBot([])
        self.assertFalse(bot._authorized(123))
        bot.update_allowed_chat_ids(["123", 456])
        self.assertTrue(bot._authorized(123))
        self.assertTrue(bot._authorized(456))
        self.assertFalse(bot._authorized(789))


if __name__ == "__main__":
    unittest.main()
