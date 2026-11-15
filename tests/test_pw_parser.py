from __future__ import annotations

import unittest
from datetime import datetime

from needle_assistant.pw_parser import (
    PageControl,
    detect_subject,
    find_event,
    format_schedule,
    format_status,
    normalize_subject,
    parse_controls,
    parse_time_range,
    _section_for_heading,
)


def page(
    control_type: str,
    name: str,
    rect: tuple[int, int, int, int],
    depth: int = 0,
    value: str = "",
) -> PageControl:
    return PageControl(
        type=control_type,
        name=name,
        value=value,
        rect=rect,
        depth=depth,
    )


class TimeParsingTests(unittest.TestCase):
    def test_single_time(self) -> None:
        self.assertEqual(parse_time_range("8:00 AM"), ("8:00 AM", 480, None))

    def test_time_range(self) -> None:
        self.assertEqual(
            parse_time_range("8:00 AM - 9:30 AM"),
            ("8:00 AM to 9:30 AM", 480, 570),
        )
        self.assertEqual(
            parse_time_range("8:00 AM to 9:30 AM"),
            ("8:00 AM to 9:30 AM", 480, 570),
        )

    def test_hyphen_in_words_is_not_a_range(self) -> None:
        # The schedule card text includes "lecture-thumbnail"; a hyphen inside
        # a word must not turn two matching timestamps into a range.
        label, start, end = parse_time_range(
            "08:00 AM Statistics 1 : || Extra Recorded Lecture "
            "Lecture Maths lecture-thumbnail UPCOMING 08:00 AM"
        )
        self.assertEqual(label, "8:00 AM")
        self.assertEqual(start, 480)
        self.assertIsNone(end)

    def test_duplicate_times_collapse(self) -> None:
        label, start, end = parse_time_range("08:00 AM ... 08:00 AM")
        self.assertEqual(label, "8:00 AM")
        self.assertEqual(start, 480)
        self.assertIsNone(end)

    def test_midnight_and_noon(self) -> None:
        self.assertEqual(parse_time_range("12:00 AM")[1], 0)
        self.assertEqual(parse_time_range("12:30 PM")[1], 750)


class SubjectTests(unittest.TestCase):
    def test_detects_subject(self) -> None:
        self.assertEqual(detect_subject("Lecture: Maths, Statistics 1"), "maths")
        self.assertEqual(detect_subject("Physical Chemistry lecture"), "chemistry")
        self.assertEqual(detect_subject("Physics — Laws of Motion"), "physics")

    def test_normalizes_subject_aliases(self) -> None:
        self.assertEqual(normalize_subject("the maths class"), "maths")
        self.assertEqual(normalize_subject("mathematics"), "maths")
        self.assertEqual(normalize_subject("chem"), "chemistry")

    def test_section_headings(self) -> None:
        self.assertEqual(_section_for_heading("Live Now"), "live")
        self.assertEqual(_section_for_heading("Upcoming Events (2)"), "upcoming")
        self.assertEqual(_section_for_heading("Recorded Classes"), "recorded")
        self.assertEqual(_section_for_heading("Completed"), "completed")
        self.assertEqual(_section_for_heading("Weekly Schedule"), "schedule")
        self.assertEqual(_section_for_heading("Batch Offerings"), "")


class StudyPageParsingTests(unittest.TestCase):
    def snapshot(self):
        controls = [
            page("DocumentControl", "Study - Physics Wallah", (200, 0, 1200, 900), 1),
            page(
                "ButtonControl",
                "Select batch, current: Prayas 2027",
                (300, 100, 600, 140),
                4,
            ),
            page("TextControl", "Live Now", (300, 260, 500, 285), 8),
            page(
                "ButtonControl",
                "Maths — Limits and Derivatives",
                (300, 300, 800, 400),
                10,
            ),
            page("TextControl", "10:00 AM", (310, 350, 400, 375), 12),
            page("TextControl", "Join Now", (310, 370, 410, 395), 12),
            page("TextControl", "Upcoming Events", (300, 480, 600, 505), 8),
            page("ButtonControl", "Physics — Laws of Motion", (300, 520, 800, 620), 10),
            page("TextControl", "Upcoming", (310, 540, 410, 565), 12),
            page("TextControl", "11:00 AM", (310, 590, 410, 615), 12),
            page("TextControl", "Recorded Classes", (300, 700, 600, 725), 8),
            page(
                "ButtonControl",
                "Chemistry — Chemical Bonding",
                (300, 740, 800, 840),
                10,
            ),
            page("TextControl", "Recorded", (310, 760, 410, 785), 12),
            page("ButtonControl", "Watch Again", (310, 800, 430, 830), 12),
        ]
        return parse_controls(controls, now=datetime(2026, 11, 16, 9, 0))

    def test_sections_and_fields(self) -> None:
        snapshot = self.snapshot()
        self.assertEqual(snapshot.page_title, "Study - Physics Wallah")
        self.assertEqual(snapshot.batch, "Prayas 2027")
        self.assertEqual([event.subject for event in snapshot.live], ["maths"])
        self.assertEqual(snapshot.live[0].status, "live")
        self.assertEqual(snapshot.live[0].title, "Limits and Derivatives")
        self.assertEqual(snapshot.live[0].time_text, "10:00 AM")
        self.assertEqual([event.subject for event in snapshot.upcoming], ["physics"])
        self.assertEqual(snapshot.upcoming[0].title, "Laws of Motion")
        self.assertEqual([event.subject for event in snapshot.recorded], ["chemistry"])
        self.assertTrue(snapshot.recorded[0].recorded)
        self.assertIn("Watch Again", snapshot.recorded[0].actions)

    def test_status_and_schedule_formatting(self) -> None:
        snapshot = self.snapshot()
        status = format_status(snapshot)
        self.assertIn("Batch Prayas 2027", status)
        self.assertIn("Live right now: Maths", status)
        self.assertIn("Limits and Derivatives", status)
        self.assertIn("Next up: Physics", status)
        self.assertIn("Laws of Motion", status)
        self.assertIn("Recorded: Chemistry", status)
        schedule = format_schedule(snapshot)
        self.assertIn("10:00 AM — Maths — Limits and Derivatives (live)", schedule)
        self.assertNotIn("at 10:00 AM (live)", schedule)

    def test_find_event_by_subject_and_kind(self) -> None:
        snapshot = self.snapshot()
        self.assertEqual(find_event(snapshot, "math", "live").subject, "maths")
        self.assertEqual(find_event(snapshot, "physics", "upcoming").title, "Laws of Motion")
        self.assertEqual(
            find_event(snapshot, "chem", "recorded").title, "Chemical Bonding"
        )
        self.assertIsNone(find_event(snapshot, "biology", "auto"))


class SchedulePageParsingTests(unittest.TestCase):
    def test_timetable_card_is_upcoming_without_duplicate_time(self) -> None:
        controls = [
            page(
                "DocumentControl",
                "Weekly Schedule - Physics Wallah",
                (200, 0, 1200, 900),
                1,
            ),
            page("TextControl", "Weekly Schedule", (300, 200, 500, 225), 8),
            page("TextControl", "08:00 AM", (300, 500, 400, 525), 9),
            page(
                "ButtonControl",
                "Statistics 1 : || Extra Recorded Lecture",
                (300, 530, 800, 650),
                10,
            ),
            page("TextControl", "UPCOMING", (320, 560, 420, 585), 12),
            page("TextControl", "Lecture", (320, 590, 420, 615), 12),
            page("TextControl", "Maths", (320, 620, 420, 645), 12),
            page("TextControl", "08:00 AM", (700, 620, 790, 645), 12),
        ]
        snapshot = parse_controls(
            controls,
            page_title="Weekly Schedule - Physics Wallah",
            batch="Prayas 2027",
            now=datetime(2026, 11, 16, 7, 30),
        )
        self.assertEqual(len(snapshot.all_events), 1)
        event = snapshot.all_events[0]
        self.assertEqual(event.subject, "maths")
        self.assertEqual(event.status, "upcoming")
        self.assertEqual(event.time_text, "8:00 AM")
        self.assertTrue(event.recorded)
        self.assertEqual(event.title, "Statistics 1 : || Extra Recorded Lecture")
        line = format_schedule(snapshot)
        self.assertIn("8:00 AM — Maths — Statistics 1", line)
        self.assertEqual(line.count("8:00 AM"), 1)

    def test_join_now_button_becomes_live_event(self) -> None:
        controls = [
            page("DocumentControl", "Study - Physics Wallah", (200, 0, 1200, 900), 1),
            page("TextControl", "Physics", (300, 300, 400, 325), 8),
            page("TextControl", "Thermodynamics", (300, 330, 500, 355), 8),
            page("TextControl", "9:00 AM", (300, 360, 400, 385), 8),
            page("ButtonControl", "Join Now", (300, 400, 420, 440), 8),
        ]
        snapshot = parse_controls(controls, now=datetime(2026, 11, 16, 8, 50))
        self.assertEqual(len(snapshot.live), 1)
        event = snapshot.live[0]
        self.assertEqual(event.subject, "physics")
        self.assertEqual(event.title, "Thermodynamics")
        self.assertEqual(event.time_text, "9:00 AM")
        self.assertEqual(event.status, "live")


if __name__ == "__main__":
    unittest.main()
