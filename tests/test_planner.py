from __future__ import annotations

import unittest

from needle_assistant.planner import looks_like_plan, split_plan


class PlannerTests(unittest.TestCase):
    def test_simple_sequence(self) -> None:
        steps = split_plan("open Notepad then type hello world")
        self.assertEqual(steps, ["open Notepad", "type hello world"])

    def test_and_then(self) -> None:
        steps = split_plan("open Chrome and then go to example.com")
        self.assertEqual(steps, ["open Chrome", "go to example.com"])

    def test_numbered(self) -> None:
        steps = split_plan("1. open Notepad 2. type hello 3. save")
        self.assertEqual(steps, ["open Notepad", "type hello", "save"])

    def test_after_that(self) -> None:
        steps = split_plan("open Notepad after that type hello")
        self.assertEqual(steps, ["open Notepad", "type hello"])

    def test_single_command_not_plan(self) -> None:
        self.assertIsNone(split_plan("open Notepad"))
        self.assertFalse(looks_like_plan("open Notepad"))

    def test_empty(self) -> None:
        self.assertIsNone(split_plan(""))
        self.assertIsNone(split_plan("   "))

    def test_looks_like_plan(self) -> None:
        self.assertTrue(looks_like_plan("open Notepad then type hello"))


if __name__ == "__main__":
    unittest.main()
