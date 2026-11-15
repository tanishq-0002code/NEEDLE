from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from needle_assistant.automation import DesktopAutomation


class StudyAutomationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.automation = DesktopAutomation()
        self.snapshot = SimpleNamespace(
            batch="Prayas 2027",
            page_title="Study - Physics Wallah",
            live=[],
            upcoming=[],
            recorded=[],
            completed=[],
            all_events=[],
        )

    def test_status_reports_dashboard(self) -> None:
        with patch(
            "needle_assistant.pw_parser.ensure_study_page",
            return_value=(123, [], self.snapshot),
        ), patch(
            "needle_assistant.pw_parser.format_status", return_value="STATUS"
        ):
            result = self.automation._study_mode("status")
        self.assertTrue(result.success)
        self.assertEqual(result.message, "STATUS")

    def test_open_reports_after_opening(self) -> None:
        with patch(
            "needle_assistant.pw_parser.ensure_study_page",
            return_value=(123, [], self.snapshot),
        ), patch(
            "needle_assistant.pw_parser.format_status", return_value="STATUS"
        ):
            result = self.automation._study_mode("open")
        self.assertTrue(result.success)
        self.assertEqual(result.message, "Opened the study dashboard. STATUS")

    def test_reload_study_page(self) -> None:
        with patch(
            "needle_assistant.pw_parser.reload_study_page",
            return_value=(123, [], self.snapshot),
        ), patch(
            "needle_assistant.pw_parser.format_status", return_value="STATUS"
        ):
            result = self.automation._study_mode("reload")
        self.assertTrue(result.success)
        self.assertEqual(result.message, "Reloaded the study page. STATUS")

    def test_schedule_page(self) -> None:
        with patch(
            "needle_assistant.pw_parser.open_schedule_page",
            return_value=(123, [], self.snapshot),
        ), patch(
            "needle_assistant.pw_parser.format_schedule", return_value="SCHEDULE"
        ):
            result = self.automation._study_mode("schedule")
        self.assertTrue(result.success)
        self.assertEqual(result.message, "SCHEDULE")

    def test_open_class_activates_matching_event(self) -> None:
        event = SimpleNamespace(title="Limits", subject="maths")
        with patch(
            "needle_assistant.pw_parser.ensure_study_page",
            return_value=(123, [], self.snapshot),
        ), patch(
            "needle_assistant.pw_parser.find_event", return_value=event
        ), patch(
            "needle_assistant.pw_parser.activate_event",
            return_value=(True, "Opened Maths — Limits."),
        ):
            result = self.automation._study_mode(
                "open_class", subject="maths", kind="class"
            )
        self.assertTrue(result.success)
        self.assertEqual(result.message, "Opened Maths — Limits.")

    def test_recorded_falls_back_to_all_classes(self) -> None:
        with patch(
            "needle_assistant.pw_parser.ensure_study_page",
            return_value=(123, [], self.snapshot),
        ), patch(
            "needle_assistant.pw_parser.find_event", return_value=None
        ), patch(
            "needle_assistant.pw_parser.open_recorded_subject",
            return_value=(True, "Opened the latest physics recording."),
        ):
            result = self.automation._study_mode(
                "open_class", subject="physics", kind="recorded"
            )
        self.assertTrue(result.success)
        self.assertEqual(result.message, "Opened the latest physics recording.")

    def test_live_class_request_reports_when_not_live(self) -> None:
        with patch(
            "needle_assistant.pw_parser.ensure_study_page",
            return_value=(123, [], self.snapshot),
        ), patch(
            "needle_assistant.pw_parser.find_event", return_value=None
        ), patch(
            "needle_assistant.pw_parser.format_status", return_value="STATUS"
        ):
            result = self.automation._study_mode("open_class", kind="live")
        self.assertTrue(result.success)
        self.assertIn("No class is live right now.", result.message)

    def test_wait_live_starts_and_stops_watcher(self) -> None:
        class FakeWatcher:
            def __init__(self, **kwargs) -> None:
                self.kwargs = kwargs
                self.started = False
                self.stopped = False

            def start(self) -> None:
                self.started = True

            def stop(self) -> None:
                self.stopped = True

        with patch(
            "needle_assistant.pw_parser.ensure_study_page",
            return_value=(123, [], self.snapshot),
        ), patch(
            "needle_assistant.pw_parser.find_event", return_value=None
        ), patch(
            "needle_assistant.pw_parser.StudyWatcher", FakeWatcher
        ):
            result = self.automation._study_mode(
                "wait_live", subject="maths", timeout=60
            )
        self.assertTrue(result.success)
        self.assertIn("I'll watch for Maths", result.message)
        watcher = self.automation._study_watcher
        self.assertIsInstance(watcher, FakeWatcher)
        self.assertTrue(watcher.started)
        self.assertEqual(watcher.kwargs["subject"], "maths")

        result = self.automation._study_mode("stop_watch")
        self.assertTrue(result.success)
        self.assertEqual(result.message, "Stopped watching for the live class.")
        self.assertTrue(watcher.stopped)

    def test_wait_live_without_upcoming_class(self) -> None:
        with patch(
            "needle_assistant.pw_parser.ensure_study_page",
            return_value=(123, [], self.snapshot),
        ), patch(
            "needle_assistant.pw_parser.find_event", return_value=None
        ), patch(
            "needle_assistant.pw_parser.format_status", return_value="STATUS"
        ):
            result = self.automation._study_mode("wait_live", timeout=60)
        self.assertTrue(result.success)
        self.assertIn("couldn't find an upcoming class", result.message)
        self.assertIsNone(self.automation._study_watcher)

    def test_study_notifier_callback(self) -> None:
        notices: list[tuple[str, bool]] = []
        self.automation.set_study_notifier(
            lambda message, is_error: notices.append((message, is_error))
        )
        self.automation._emit_study_notice("Your class is live.", False)
        self.assertEqual(notices, [("Your class is live.", False)])


if __name__ == "__main__":
    unittest.main()
