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
        self.assertEqual(call.arguments["action"], "click")

    def test_click_again(self) -> None:
        call = fast_route("click it again")
        self.assertIsNotNone(call)
        self.assertEqual(call.name, "click_element")
        self.assertEqual(call.arguments["action"], "click_again")
        self.assertEqual(call.arguments["target"], "")

    def test_scroll_to_element(self) -> None:
        call = fast_route("scroll down until you see Settings")
        self.assertIsNotNone(call)
        self.assertEqual(call.name, "click_element")
        self.assertEqual(call.arguments["action"], "scroll_to")
        self.assertEqual(call.arguments["direction"], "down")
        self.assertEqual(call.arguments["target"], "settings")

    def test_right_click_is_structured(self) -> None:
        call = fast_route("right click the desktop")
        self.assertIsNotNone(call)
        self.assertEqual(call.name, "click_element")
        self.assertEqual(call.arguments["action"], "right_click")
        self.assertEqual(call.arguments["target"], "desktop")

    def test_close_application(self) -> None:
        call = fast_route("close Chrome")
        self.assertIsNotNone(call)
        self.assertEqual(call.name, "close_application")
        self.assertEqual(call.arguments["app_name"], "Chrome")

    def test_system_control(self) -> None:
        call = fast_route("shut down the computer")
        self.assertIsNotNone(call)
        self.assertEqual(call.name, "system_control")
        self.assertEqual(call.arguments["action"], "shutdown")

    def test_focus_window(self) -> None:
        call = fast_route("switch to Notepad")
        self.assertIsNotNone(call)
        self.assertEqual(call.name, "focus_window")
        self.assertEqual(call.arguments["title"], "Notepad")

    def test_window_manage(self) -> None:
        call = fast_route("minimize Notepad")
        self.assertIsNotNone(call)
        self.assertEqual(call.name, "window_manage")
        self.assertEqual(call.arguments["action"], "minimize")
        self.assertEqual(call.arguments["title"], "Notepad")

    def test_unknown_is_left_to_needle(self) -> None:
        self.assertIsNone(fast_route("what is the weather tomorrow"))

    def test_whatsapp_message_with_text(self) -> None:
        call = fast_route("send a whatsapp message to rahul saying I'll be late")
        self.assertIsNotNone(call)
        self.assertEqual(call.name, "send_message")
        self.assertEqual(call.arguments["recipient"], "rahul")
        self.assertEqual(call.arguments["text"], "I'll be late")

    def test_whatsapp_colon_form(self) -> None:
        call = fast_route("whatsapp rahul: on my way")
        self.assertIsNotNone(call)
        self.assertEqual(call.name, "send_message")
        self.assertEqual(call.arguments["recipient"], "rahul")
        self.assertEqual(call.arguments["text"], "on my way")

    def test_tell_on_whatsapp(self) -> None:
        call = fast_route("tell rahul on whatsapp I will be late")
        self.assertIsNotNone(call)
        self.assertEqual(call.name, "send_message")
        self.assertEqual(call.arguments["recipient"], "rahul")
        self.assertEqual(call.arguments["text"], "I will be late")

    def test_send_this_message_uses_draft(self) -> None:
        call = fast_route("send this message to rahul on whatsapp")
        self.assertIsNotNone(call)
        self.assertEqual(call.name, "send_message")
        self.assertEqual(call.arguments["recipient"], "rahul")
        self.assertEqual(call.arguments["text"], "")

    def test_send_this_message_unknown_recipient_asks(self) -> None:
        call = fast_route("send this message to someone on whatsapp")
        self.assertIsNotNone(call)
        self.assertIsNone(call.name)
        self.assertIn("who", (call.error or "").lower())

    def test_send_some_message_clarifies(self) -> None:
        call = fast_route("send some message on whatsapp to someone")
        self.assertIsNotNone(call)
        self.assertIsNone(call.name)
        self.assertEqual(call.source, "clarify")
        self.assertIn("who", (call.error or "").lower())

    def test_send_some_message_named(self) -> None:
        call = fast_route("send some message on whatsapp to rahul saying on my way")
        self.assertIsNotNone(call)
        self.assertEqual(call.name, "send_message")
        self.assertEqual(call.arguments["recipient"], "rahul")
        self.assertEqual(call.arguments["text"], "on my way")

    def test_clarify_call_carries_the_message(self) -> None:
        call = fast_route("send a whatsapp message to someone saying hello")
        self.assertIsNotNone(call)
        self.assertIsNone(call.name)
        self.assertEqual(call.source, "clarify")
        self.assertEqual(call.arguments.get("text"), "hello")

    def test_whatsapp_name_uses_draft(self) -> None:
        call = fast_route("whatsapp rahul")
        self.assertIsNotNone(call)
        self.assertEqual(call.name, "send_message")
        self.assertEqual(call.arguments["recipient"], "rahul")
        self.assertEqual(call.arguments["text"], "")

    def test_whatsapp_web_is_not_treated_as_recipient(self) -> None:
        self.assertIsNone(fast_route("whatsapp web"))

    def test_open_whatsapp_still_opens_web(self) -> None:
        call = fast_route("open whatsapp")
        self.assertIsNotNone(call)
        self.assertEqual(call.name, "browser_control")
        self.assertEqual(call.source, "fast")

    def test_first_link_keeps_explicit_browser(self) -> None:
        call = fast_route("open the first link in edge")
        self.assertIsNotNone(call)
        self.assertEqual(call.name, "browser_control")
        self.assertEqual(call.arguments["action"], "click_first_result")
        self.assertEqual(call.arguments["browser"], "edge")

    def test_new_tab_search_routes_to_browser_navigation(self) -> None:
        call = fast_route("open a new tab and search for tech burner")
        self.assertIsNotNone(call)
        self.assertEqual(call.name, "browser_control")
        self.assertEqual(call.arguments["action"], "open_url")
        self.assertIn("tech%20burner", call.arguments["url"])

    def test_remember_contact(self) -> None:
        call = fast_route("remember rahul as +91 98765 43210")
        self.assertIsNotNone(call)
        self.assertEqual(call.name, "memory")
        self.assertEqual(call.arguments["action"], "set_contact")
        self.assertEqual(call.arguments["alias"], "rahul")
        self.assertEqual(call.arguments["target"], "+91 98765 43210")

    def test_remember_draft(self) -> None:
        call = fast_route("remember the message buy milk")
        self.assertIsNotNone(call)
        self.assertEqual(call.name, "memory")
        self.assertEqual(call.arguments["action"], "set_preference")
        self.assertEqual(call.arguments["key"], "draft")
        self.assertEqual(call.arguments["value"], "buy milk")

    def test_list_contacts(self) -> None:
        call = fast_route("list contacts")
        self.assertIsNotNone(call)
        self.assertEqual(call.name, "memory")
        self.assertEqual(call.arguments["action"], "list_contacts")


class StudyRouteTests(unittest.TestCase):
    def assert_study(self, text: str, mode: str, **expected: object):
        call = fast_route(text)
        self.assertIsNotNone(call)
        self.assertEqual(call.name, "study_mode")
        self.assertEqual(call.arguments["mode"], mode)
        for key, value in expected.items():
            self.assertEqual(call.arguments.get(key), value)

    def test_reload_page_is_instant_browser_reload(self) -> None:
        for text in ("reload the page", "refresh the tab", "refresh browser", "reload"):
            call = fast_route(text)
            self.assertIsNotNone(call)
            self.assertEqual(call.name, "browser_control")
            self.assertEqual(call.arguments["action"], "reload")

    def test_study_reload_targets_study_tab(self) -> None:
        for text in ("reload the study page", "refresh physics wallah", "reload pw"):
            self.assert_study(text, "reload")

    def test_live_status(self) -> None:
        for text in (
            "what class is live right now?",
            "what is live",
            "is there a class live now",
            "is physics class live",
            "is the class live",
            "is there a chemistry class live",
            "show my study dashboard",
        ):
            self.assert_study(text, "status")

    def test_daily_schedule_status(self) -> None:
        self.assert_study("what is my schedule", "status")
        self.assert_study("show my schedule", "status")
        self.assert_study("what classes do i have today", "status")
        self.assert_study("what are my classes today", "status")

    def test_full_schedule(self) -> None:
        for text in ("show full schedule", "open my timetable", "weekly schedule"):
            self.assert_study(text, "schedule")

    def test_open_subject_class(self) -> None:
        self.assert_study("open maths class today", "open_class", subject="maths", kind="class")
        self.assert_study("open today's maths class", "open_class", subject="maths", kind="class")
        self.assert_study("join the live maths class", "open_class", subject="maths", kind="live")
        self.assert_study("open the class of physics", "open_class", subject="physics", kind="class")
        self.assert_study(
            "open physical chemistry class",
            "open_class",
            subject="chemistry",
            kind="class",
        )

    def test_open_recording(self) -> None:
        self.assert_study(
            "open physics recording", "open_class", subject="physics", kind="recorded"
        )
        self.assert_study(
            "open math recording", "open_class", subject="maths", kind="recorded"
        )
        self.assert_study(
            "open organic chemistry recording",
            "open_class",
            subject="chemistry",
            kind="recorded",
        )
        self.assert_study(
            "play the recorded chemistry lecture",
            "open_class",
            subject="chemistry",
            kind="recorded",
        )

    def test_wait_until_live(self) -> None:
        self.assert_study("open the class as it turns live", "wait_live", kind="live")
        self.assert_study(
            "open maths class when it goes live",
            "wait_live",
            subject="maths",
            kind="live",
        )
        self.assert_study("wait for the class to go live", "wait_live")
        self.assert_study("wait until the class goes live", "wait_live")

    def test_stop_watching(self) -> None:
        for text in ("stop watching", "cancel the live watch", "stop waiting"):
            self.assert_study(text, "stop_watch")

    def test_study_open_and_prepare(self) -> None:
        self.assert_study("study time", "open")
        self.assert_study("open physics wallah", "open")
        self.assert_study("get the system ready for study", "prepare")

    def test_class_10_is_not_study_route(self) -> None:
        call = fast_route("open class 10")
        self.assertTrue(call is None or call.name != "study_mode")


class CatalogTests(unittest.TestCase):
    def test_tools_are_declared(self) -> None:
        self.assertEqual(len(TOOL_SCHEMAS), 32)
        self.assertEqual(
            ALLOWED_TOOLS,
            {
                "open_application",
                "close_application",
                "type_text",
                "click_element",
                "focus_window",
                "window_manage",
                "press_hotkey",
                "take_screenshot",
                "control_media",
                "system_control",
                "read_screen",
                "clipboard",
                "file_operation",
                "run_command",
                "browser_control",
                "run_plan",
                "memory",
                "schedule",
                "macro",
                "shortcut",
                "send_message",
                "audio_control",
                "display_control",
                "connectivity_control",
                "weather_report",
                "notes",
                "knowledge",
                "file_search",
                "navigate_screen",
                "email",
                "manage_browser_tabs",
                "study_mode",
            },
        )

    def test_system_prompt_is_facts_not_instructions(self) -> None:
        self.assertIn("device:", SYSTEM_PROMPT)
        self.assertNotIn("You route", SYSTEM_PROMPT)

    def test_send_message_validation(self) -> None:
        name, arguments = validate_call(
            "send_message", {"recipient": "rahul on whatsapp", "text": "hi"}
        )
        self.assertEqual(arguments["recipient"], "rahul")
        self.assertEqual(arguments["text"], "hi")
        with self.assertRaises(ValueError):
            validate_call("send_message", {"recipient": "someone"})
        with self.assertRaises(ValueError):
            validate_call("send_message", {"recipient": ""})

    def test_set_contact_validation(self) -> None:
        name, arguments = validate_call(
            "memory", {"action": "set_contact", "alias": "mom", "target": "+91 9812345678"}
        )
        self.assertEqual(name, "memory")
        self.assertEqual(arguments["action"], "set_contact")
        with self.assertRaises(ValueError):
            validate_call("memory", {"action": "set_contact", "alias": "mom", "target": "not a number"})
        with self.assertRaises(ValueError):
            validate_call("memory", {"action": "set_contact", "alias": "", "target": "+91 9812345678"})

    def test_study_mode_validation(self) -> None:
        name, arguments = validate_call(
            "study_mode",
            {
                "mode": "open_class",
                "subject": "Maths",
                "kind": "recording",
                "timeout": 5,
            },
        )
        self.assertEqual(name, "study_mode")
        self.assertEqual(arguments["mode"], "open_class")
        self.assertEqual(arguments["subject"], "maths")
        self.assertEqual(arguments["kind"], "recorded")
        self.assertEqual(arguments["timeout"], 60)

        _name, arguments = validate_call(
            "study_mode", {"mode": "open_class", "subject": "Math"}
        )
        self.assertEqual(arguments["subject"], "maths")

        _name, arguments = validate_call("study_mode", {"mode": "bogus"})
        self.assertEqual(arguments["mode"], "open")
        self.assertEqual(arguments["kind"], "auto")
        self.assertEqual(arguments["timeout"], 7200)

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

    def test_close_validation(self) -> None:
        name, arguments = validate_call("close_application", {"app_name": "Chrome"})
        self.assertEqual(name, "close_application")
        self.assertEqual(arguments["app_name"], "Chrome")
        self.assertFalse(arguments.get("force"))

    def test_system_control_validation(self) -> None:
        name, arguments = validate_call("system_control", {"action": "lock"})
        self.assertEqual(name, "system_control")
        self.assertEqual(arguments["action"], "lock")
        # Repair from user text.
        name, arguments = validate_call("system_control", {}, "shut down the computer")
        self.assertEqual(arguments["action"], "shutdown")

    def test_window_manage_validation(self) -> None:
        name, arguments = validate_call(
            "window_manage", {"title": "Notepad", "action": "minimise"}
        )
        self.assertEqual(arguments["title"], "Notepad")
        self.assertEqual(arguments["action"], "minimize")

    def test_targeted_type_window(self) -> None:
        name, arguments = validate_call(
            "type_text", {"text": "hello", "window": "Notepad"}
        )
        self.assertEqual(arguments["text"], "hello")
        self.assertEqual(arguments["window"], "Notepad")

    def test_destructive_helpers(self) -> None:
        from needle_assistant.catalog import is_destructive, destructive_description

        call = fast_route("shut down the computer")
        self.assertTrue(is_destructive(call))
        self.assertIn("shut down", destructive_description(call).lower())
        call = fast_route("close Chrome")
        self.assertFalse(is_destructive(call))

    def test_click_action_validation(self) -> None:
        name, arguments = validate_call(
            "click_element", {"target": "Save", "action": "double-click"}
        )
        self.assertEqual(name, "click_element")
        self.assertEqual(arguments["action"], "double_click")
        self.assertEqual(arguments["target"], "Save")
        self.assertEqual(arguments["clicks"], 2)

        name, arguments = validate_call(
            "click_element", {"target": "right click Save"}
        )
        self.assertEqual(arguments["action"], "right_click")
        self.assertEqual(arguments["target"], "Save")
        self.assertEqual(arguments["button"], "right")

        name, arguments = validate_call(
            "click_element", {"target": "", "action": "click_again"}
        )
        self.assertEqual(arguments["action"], "click_again")
        self.assertEqual(arguments["target"], "")

        name, arguments = validate_call(
            "click_element", {"target": "", "x": 120, "y": 240}
        )
        self.assertEqual(arguments["x"], 120)
        self.assertEqual(arguments["y"], 240)

    def test_click_requires_target_unless_repeat_or_coordinates(self) -> None:
        with self.assertRaises(ValueError):
            validate_call("click_element", {"target": ""})


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
