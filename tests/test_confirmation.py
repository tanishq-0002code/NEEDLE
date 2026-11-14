from __future__ import annotations

import time
import unittest

from needle_assistant.confirmation import ConfirmationStore


class ConfirmationStoreTests(unittest.TestCase):
    def test_request_and_take(self) -> None:
        store = ConfirmationStore(ttl=10.0)
        self.assertTrue(store.request(1, "text", "description"))
        pending = store.take(1)
        self.assertIsNotNone(pending)
        self.assertEqual(pending.text, "text")
        self.assertIsNone(store.take(1))

    def test_duplicate_request_rejected(self) -> None:
        store = ConfirmationStore(ttl=10.0)
        self.assertTrue(store.request(1, "text", "description"))
        self.assertFalse(store.request(1, "other", "other"))
        pending = store.peek(1)
        self.assertEqual(pending.text, "text")

    def test_clear(self) -> None:
        store = ConfirmationStore(ttl=10.0)
        store.request(1, "text", "description")
        store.clear(1)
        self.assertIsNone(store.take(1))

    def test_expiry(self) -> None:
        store = ConfirmationStore(ttl=0.05)
        store.request(1, "text", "description")
        time.sleep(0.12)
        self.assertIsNone(store.take(1))
        self.assertIsNone(store.peek(1))


if __name__ == "__main__":
    unittest.main()
