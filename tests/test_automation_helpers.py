from __future__ import annotations

import unittest

from needle_assistant.automation import _norm


class AutomationHelpersTests(unittest.TestCase):
    def test_normalizes_app_names(self) -> None:
        self.assertEqual(_norm("Google Chrome.exe"), "google chrome")
        self.assertEqual(_norm("  Microsoft   Edge  "), "microsoft edge")
        self.assertEqual(_norm("7-Zip"), "7 zip")

    def test_empty_name(self) -> None:
        self.assertEqual(_norm(""), "")


if __name__ == "__main__":
    unittest.main()
