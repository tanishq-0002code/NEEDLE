from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from needle_assistant.config import (
    AppConfig,
    DEFAULT_WAKE_PHRASES,
    load_config,
    normalize_hotkey,
    normalize_wake_phrases,
    save_config,
)


class HotkeyTests(unittest.TestCase):
    def test_normalizes_spacing_and_aliases(self) -> None:
        self.assertEqual(normalize_hotkey("Ctrl + Space"), "ctrl+space")
        self.assertEqual(normalize_hotkey("Control+Shift+S"), "ctrl+shift+s")
        self.assertEqual(normalize_hotkey("Windows+Space"), "win+space")

    def test_rejects_modifier_only(self) -> None:
        with self.assertRaises(ValueError):
            normalize_hotkey("ctrl+alt")

    def test_rejects_unknown_key(self) -> None:
        with self.assertRaises(ValueError):
            normalize_hotkey("ctrl+banana")

    def test_rejects_too_many_keys(self) -> None:
        with self.assertRaises(ValueError):
            normalize_hotkey("ctrl+alt+shift+win+x")


class WakePhraseTests(unittest.TestCase):
    def test_normalizes_and_deduplicates(self) -> None:
        self.assertEqual(
            normalize_wake_phrases("Hey Jarvis, hey jarvis; HEY COMPUTER!"),
            ["hey jarvis", "hey computer"],
        )

    def test_empty_input(self) -> None:
        self.assertEqual(normalize_wake_phrases(""), [])


class ConfigTests(unittest.TestCase):
    def test_defaults_use_four_threads(self) -> None:
        config = AppConfig()
        self.assertEqual(config.threads, 4)
        self.assertEqual(config.wake_phrases, DEFAULT_WAKE_PHRASES)

    def test_validate_clamps_values(self) -> None:
        config = AppConfig(max_new_tokens=9999, threads=-3, overlay_width=1)
        config.validate()
        self.assertEqual(config.max_new_tokens, 512)
        self.assertEqual(config.threads, 0)
        self.assertEqual(config.overlay_width, 520)

    def test_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "config.json"
            config = AppConfig(hotkey="alt+space", wake_word_enabled=False)
            save_config(config, path)
            loaded = load_config(path)
            self.assertEqual(loaded.hotkey, "alt+space")
            self.assertFalse(loaded.wake_word_enabled)

    def test_corrupt_file_falls_back_to_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "config.json"
            path.write_text("{not json", encoding="utf-8")
            loaded = load_config(path)
            self.assertEqual(loaded.hotkey, "ctrl+space")

    def test_unknown_keys_are_ignored(self) -> None:
        config = AppConfig.from_dict({"hotkey": "ctrl+space", "surprise": 42})
        self.assertEqual(config.hotkey, "ctrl+space")


if __name__ == "__main__":
    unittest.main()
