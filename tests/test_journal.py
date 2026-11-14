from __future__ import annotations

import unittest

from needle_assistant.journal import UndoJournal


class UndoJournalTests(unittest.TestCase):
    def test_record_and_undo_last(self) -> None:
        journal = UndoJournal()
        calls = []
        journal.record(lambda: calls.append("a"))
        journal.record(lambda: calls.append("b"))
        self.assertEqual(journal.undo_last(), 1)
        self.assertEqual(calls, ["b"])
        self.assertEqual(journal.undo_last(), 1)
        self.assertEqual(calls, ["b", "a"])
        self.assertEqual(journal.undo_last(), 0)

    def test_undo_all(self) -> None:
        journal = UndoJournal()
        calls = []
        journal.record(lambda: calls.append("a"))
        journal.record(lambda: calls.append("b"))
        self.assertEqual(journal.undo_all(), 2)
        self.assertEqual(calls, ["b", "a"])
        self.assertFalse(journal.has_entries())

    def test_clear(self) -> None:
        journal = UndoJournal()
        journal.record(lambda: None)
        journal.clear()
        self.assertFalse(journal.has_entries())


if __name__ == "__main__":
    unittest.main()
