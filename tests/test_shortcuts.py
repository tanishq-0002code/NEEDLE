from __future__ import annotations

import unittest

from needle_assistant.catalog import fast_route
from needle_assistant.shortcuts import _DEFAULT_SHORTCUTS


class StudyShortcutTests(unittest.TestCase):
    def test_builtin_study_steps_route_to_study_mode(self) -> None:
        for trigger, steps in _DEFAULT_SHORTCUTS.items():
            self.assertEqual(len(steps), 1, trigger)
            call = fast_route(steps[0])
            self.assertIsNotNone(call, trigger)
            self.assertEqual(call.name, "study_mode", trigger)
            self.assertIn(call.arguments["mode"], {"open", "prepare"}, trigger)

    def test_builtin_study_steps_do_not_open_duplicate_tabs(self) -> None:
        for steps in _DEFAULT_SHORTCUTS.values():
            self.assertNotIn(
                "open edge and go to pw.live/study-v2/study",
                steps,
            )

    def test_prepare_shortcuts_use_prepare_mode(self) -> None:
        for trigger in (
            "get the system ready for study",
            "get system ready for study",
            "prepare the system for study",
        ):
            call = fast_route(_DEFAULT_SHORTCUTS[trigger][0])
            self.assertEqual(call.arguments["mode"], "prepare")


if __name__ == "__main__":
    unittest.main()
