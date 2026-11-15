from __future__ import annotations

import unittest

from needle_assistant.automation import DesktopAutomation, _norm


class AutomationHelpersTests(unittest.TestCase):
    def test_normalizes_app_names(self) -> None:
        self.assertEqual(_norm("Google Chrome.exe"), "google chrome")
        self.assertEqual(_norm("  Microsoft   Edge  "), "microsoft edge")
        self.assertEqual(_norm("7-Zip"), "7 zip")

    def test_empty_name(self) -> None:
        self.assertEqual(_norm(""), "")


class OcrMatchingTests(unittest.TestCase):
    def test_prefers_exact_button_over_sentence(self) -> None:
        words = [
            {"text": "Save", "x": 300, "y": 220, "width": 44, "height": 18},
            {"text": "Please", "x": 40, "y": 100, "width": 52, "height": 18},
            {"text": "save", "x": 96, "y": 100, "width": 34, "height": 18},
            {"text": "your", "x": 134, "y": 100, "width": 32, "height": 18},
            {"text": "work", "x": 170, "y": 100, "width": 36, "height": 18},
        ]
        match = DesktopAutomation._best_ocr_match("Save", words)
        self.assertIsNotNone(match)
        assert match is not None
        self.assertGreaterEqual(match[3], 0.9)
        self.assertEqual((match[0], match[1]), (322, 229))

    def test_skips_title_bar_duplicate(self) -> None:
        words = [
            {"text": "Settings", "x": 40, "y": 8, "width": 70, "height": 16},
            {"text": "Settings", "x": 240, "y": 300, "width": 70, "height": 16},
        ]
        match = DesktopAutomation._best_ocr_match("Settings", words, title_bar_y=40)
        self.assertIsNotNone(match)
        assert match is not None
        self.assertEqual(match[1], 308)

    def test_returns_none_when_nothing_matches(self) -> None:
        words = [{"text": "Cancel", "x": 10, "y": 10, "width": 40, "height": 16}]
        self.assertIsNone(DesktopAutomation._best_ocr_match("Save", words))


if __name__ == "__main__":
    unittest.main()
