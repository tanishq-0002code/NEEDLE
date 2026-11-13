from __future__ import annotations

import json
import unittest

from needle_assistant.catalog import (
    ALLOWED_TOOLS,
    SYSTEM_PROMPT,
    TOOL_SCHEMAS,
    badge_text,
    fast_route,
    validate_call,
)
from needle_assistant.needler import parse_envelope


class FastRouteTests(unittest.TestCase):
    def test_opens_application(self) -> None:
        call = fast_route("open Chrome")
        self.assertIsNotNone(call)
        self.assertEqual(call.name, "open_application")
        self.assertEqual(call.arguments["app_name"], "Chrome")

    def test_types_text_and_strips_destination(self) -> None:
        call = fast_route("type hello world into the search box")
        self.assertIsNotNone(call)
        self.assertEqual(call.name, "type_text")
        self.assertEqual(call.arguments["text"], "hello world")

    def test_preserves_text_case(self) -> None:
        call = fast_route("type Hello World!")
        self.assertIsNotNone(call)
        self.assertEqual(call.arguments["text"], "Hello World")

    def test_copy_shortcut(self) -> None:
        call = fast_route("copy this")
        self.assertIsNotNone(call)
        self.assertEqual(call.name, "press_hotkey")
        self.assertEqual(call.arguments["keys"], ["ctrl", "c"])

    def test_press_chord(self) -> None:
        call = fast_route("press ctrl+shift+s")
        self.assertIsNotNone(call)
        self.assertEqual(call.name, "press_hotkey")
        self.assertEqual(call.arguments["keys"], ["ctrl", "shift", "s"])

    def test_screenshot(self) -> None:
        call = fast_route("take a screenshot of the screen")
        self.assertIsNotNone(call)
        self.assertEqual(call.name, "take_screenshot")

    def test_media(self) -> None:
        call = fast_route("pause the music")
        self.assertIsNotNone(call)
        self.assertEqual(call.name, "control_media")
        self.assertEqual(call.arguments["action"], "pause")

    def test_click(self) -> None:
        call = fast_route("click the save button")
        self.assertIsNotNone(call)
        self.assertEqual(call.name, "click_element")
        self.assertEqual(call.arguments["target"], "save")

    def test_unknown_is_left_to_needle(self) -> None:
        self.assertIsNone(fast_route("what is the weather tomorrow"))


class CatalogTests(unittest.TestCase):
    def test_six_tools_are_declared(self) -> None:
        self.assertEqual(len(TOOL_SCHEMAS), 6)
        self.assertEqual(
            ALLOWED_TOOLS,
            {
                "open_application",
                "type_text",
                "click_element",
                "press_hotkey",
                "take_screenshot",
                "control_media",
            },
        )

    def test_system_prompt_is_facts_not_instructions(self) -> None:
        self.assertIn("device:", SYSTEM_PROMPT)
        self.assertNotIn("You route", SYSTEM_PROMPT)

    def test_json_string_arguments_are_accepted(self) -> None:
        name, arguments = validate_call(
            "open_application", json.dumps({"app_name": "Notepad"})
        )
        self.assertEqual(name, "open_application")
        self.assertEqual(arguments["app_name"], "Notepad")

    def test_type_repair_from_request(self) -> None:
        name, arguments = validate_call(
            "type_text", {"text": "search box"}, "write a note in the search box"
        )
        self.assertEqual(name, "type_text")
        self.assertEqual(arguments["text"], "a note")

    def test_hotkey_repair_from_request(self) -> None:
        name, arguments = validate_call(
            "press_hotkey", {"keys": []}, "copy this"
        )
        self.assertEqual(name, "press_hotkey")
        self.assertEqual(arguments["keys"], ["ctrl", "c"])

    def test_app_repair_from_request(self) -> None:
        name, arguments = validate_call(
            "open_application", {"app_name": ""}, "open Notepad"
        )
        self.assertEqual(name, "open_application")
        self.assertEqual(arguments["app_name"], "Notepad")

    def test_bad_hotkey_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            validate_call("press_hotkey", {"keys": ["ctrl", "alt", "delete"]})

    def test_badge_text(self) -> None:
        call = fast_route("open notepad")
        self.assertIsNotNone(call)
        self.assertIn("OPEN APPLICATION", badge_text(call))
        self.assertIn("notepad", badge_text(call))


class EnvelopeTests(unittest.TestCase):
    def test_valid_envelope(self) -> None:
        raw = json.dumps(
            {
                "type": "call",
                "success": True,
                "function_calls": [
                    {"name": "open_application", "arguments": {"app_name": "Notepad"}}
                ],
                "confidence": 0.9,
            }
        )
        call = parse_envelope(raw, "open notepad", 1.2)
        self.assertEqual(call.name, "open_application")
        self.assertEqual(call.arguments["app_name"], "Notepad")
        self.assertAlmostEqual(call.confidence or 0.0, 0.9)

    def test_unknown_tool_is_rejected(self) -> None:
        raw = json.dumps(
            {
                "type": "call",
                "success": True,
                "function_calls": [{"name": "format_drive", "arguments": {}}],
            }
        )
        call = parse_envelope(raw, "format the drive", 1.0)
        self.assertIsNone(call.name)
        self.assertIn("unknown", (call.error or "").lower())

    def test_refusal_is_an_error(self) -> None:
        raw = json.dumps({"type": "respond", "success": True, "function_calls": []})
        call = parse_envelope(raw, "what is the weather", 1.0)
        self.assertIsNone(call.name)
        self.assertTrue(call.error)

    def test_low_level_arguments_are_repaired(self) -> None:
        raw = json.dumps(
            {
                "type": "call",
                "success": True,
                "function_calls": [{"name": "press_hotkey", "arguments": {"keys": []}}],
                "confidence": 0.02,
            }
        )
        call = parse_envelope(raw, "copy this", 1.0)
        self.assertEqual(call.name, "press_hotkey")
        self.assertEqual(call.arguments["keys"], ["ctrl", "c"])


if __name__ == "__main__":
    unittest.main()
