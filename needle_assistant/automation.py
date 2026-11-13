from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import difflib
import glob
import os
import re
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import psutil

from . import windows_api as win
from .catalog import normalize_keys
from .models import ExecutionResult, ToolCall
from .paths import pictures_dir

user32 = ctypes.WinDLL("user32", use_last_error=True)


class AutomationError(RuntimeError):
    pass


@dataclass(frozen=True)
class ResolvedApp:
    kind: str  # exe, lnk, uri
    target: str
    display_name: str


_KNOWN_EXES: dict[str, list[str]] = {
    "chrome": ["chrome.exe"],
    "google chrome": ["chrome.exe"],
    "edge": ["msedge.exe"],
    "microsoft edge": ["msedge.exe"],
    "firefox": ["firefox.exe"],
    "brave": ["brave.exe"],
    "opera": ["opera.exe"],
    "notepad": ["notepad.exe"],
    "wordpad": ["write.exe"],
    "calculator": ["calc.exe"],
    "calc": ["calc.exe"],
    "explorer": ["explorer.exe"],
    "file explorer": ["explorer.exe"],
    "files": ["explorer.exe"],
    "cmd": ["cmd.exe"],
    "command prompt": ["cmd.exe"],
    "powershell": ["powershell.exe"],
    "terminal": ["wt.exe", "powershell.exe"],
    "windows terminal": ["wt.exe"],
    "task manager": ["taskmgr.exe"],
    "control panel": ["control.exe"],
    "paint": ["mspaint.exe"],
    "snipping tool": ["SnippingTool.exe"],
    "word": ["winword.exe"],
    "microsoft word": ["winword.exe"],
    "excel": ["excel.exe"],
    "microsoft excel": ["excel.exe"],
    "powerpoint": ["powerpnt.exe"],
    "outlook": ["outlook.exe"],
    "microsoft outlook": ["outlook.exe"],
    "code": ["Code.exe"],
    "vs code": ["Code.exe"],
    "visual studio code": ["Code.exe"],
    "spotify": ["Spotify.exe"],
    "discord": ["Discord.exe"],
    "slack": ["slack.exe"],
    "zoom": ["Zoom.exe"],
    "telegram": ["Telegram.exe"],
    "whatsapp": ["WhatsApp.exe"],
    "teams": ["ms-teams.exe", "Teams.exe"],
    "microsoft teams": ["ms-teams.exe", "Teams.exe"],
    "steam": ["steam.exe"],
    "vlc": ["vlc.exe"],
    "obs": ["obs64.exe"],
    "obs studio": ["obs64.exe"],
    "photos": ["ms-photos:"],
    "camera": ["microsoft.windows.camera:"],
    "settings": ["ms-settings:"],
    "windows settings": ["ms-settings:"],
    "store": ["ms-windows-store:"],
    "microsoft store": ["ms-windows-store:"],
    "mail": ["ms-mail:"],
    "calendar": ["outlookcal:"],
    "clock": ["ms-clock:"],
    "weather": ["msnweather:"],
}

_URI_PREFIXES = ("ms-settings:", "ms-windows-store:", "ms-photos:", "ms-screenclip:",
                 "microsoft.windows.camera:", "ms-mail:", "outlookcal:", "ms-clock:",
                 "msnweather:")

_SPECIAL_PATHS = {
    "chrome.exe": [
        r"%ProgramFiles%\Google\Chrome\Application\chrome.exe",
        r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe",
        r"%LocalAppData%\Google\Chrome\Application\chrome.exe",
    ],
    "msedge.exe": [
        r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe",
        r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe",
    ],
    "firefox.exe": [
        r"%ProgramFiles%\Mozilla Firefox\firefox.exe",
        r"%ProgramFiles(x86)%\Mozilla Firefox\firefox.exe",
    ],
    "brave.exe": [
        r"%ProgramFiles%\BraveSoftware\Brave-Browser\Application\brave.exe",
        r"%LocalAppData%\BraveSoftware\Brave-Browser\Application\brave.exe",
    ],
    "opera.exe": [
        r"%LocalAppData%\Programs\Opera\opera.exe",
        r"%ProgramFiles%\Opera\opera.exe",
    ],
    "Code.exe": [
        r"%LocalAppData%\Programs\Microsoft VS Code\Code.exe",
        r"%ProgramFiles%\Microsoft VS Code\Code.exe",
    ],
    "Spotify.exe": [
        r"%AppData%\Spotify\Spotify.exe",
        r"%LocalAppData%\Microsoft\WindowsApps\Spotify.exe",
    ],
    "Discord.exe": [],
    "slack.exe": [
        r"%LocalAppData%\slack\slack.exe",
    ],
    "Zoom.exe": [
        r"%AppData%\Zoom\bin\Zoom.exe",
    ],
    "Telegram.exe": [
        r"%AppData%\Telegram Desktop\Telegram.exe",
    ],
    "WhatsApp.exe": [
        r"%LocalAppData%\WhatsApp\WhatsApp.exe",
    ],
    "ms-teams.exe": [
        r"%LocalAppData%\Microsoft\WindowsApps\ms-teams.exe",
    ],
    "Teams.exe": [
        r"%LocalAppData%\Microsoft\Teams\current\Teams.exe",
    ],
    "steam.exe": [
        r"%ProgramFiles(x86)%\Steam\steam.exe",
        r"%ProgramFiles%\Steam\steam.exe",
    ],
    "vlc.exe": [
        r"%ProgramFiles%\VideoLAN\VLC\vlc.exe",
        r"%ProgramFiles(x86)%\VideoLAN\VLC\vlc.exe",
    ],
    "obs64.exe": [
        r"%ProgramFiles%\obs-studio\bin\64bit\obs64.exe",
    ],
    "SnippingTool.exe": [
        r"%LocalAppData%\Microsoft\WindowsApps\SnippingTool.exe",
        r"%SystemRoot%\System32\SnippingTool.exe",
    ],
}

_APP_PATH_KEYS = [
    ("HKEY_CURRENT_USER", r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths"),
    ("HKEY_CURRENT_USER", r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\App Paths"),
    ("HKEY_LOCAL_MACHINE", r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths"),
    ("HKEY_LOCAL_MACHINE", r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\App Paths"),
]


def _norm(value: str) -> str:
    text = value.lower().strip()
    text = re.sub(r"\.exe$", "", text)
    text = re.sub(r"[^a-z0-9 ]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _candidate_paths(exe_name: str) -> list[Path]:
    candidates: list[Path] = []
    for raw in _SPECIAL_PATHS.get(exe_name, []):
        expanded = os.path.expandvars(raw)
        candidates.append(Path(expanded))
    found = shutil.which(exe_name)
    if found:
        candidates.append(Path(found))
    # Discord keeps versioned app folders.
    local = os.environ.get("LOCALAPPDATA", "")
    if exe_name.lower() == "discord.exe" and local:
        candidates.extend(Path(p) for p in glob.glob(os.path.join(local, "Discord", "app-*", "Discord.exe")))
    return candidates


class AppResolver:
    def __init__(self) -> None:
        self._start_menu: dict[str, Path] | None = None
        self._app_paths: dict[str, Path] | None = None

    def _build_start_menu(self) -> dict[str, Path]:
        index: dict[str, Path] = {}
        roots = [
            Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs",
            Path(os.environ.get("ProgramData", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs",
        ]
        ignored = ("uninstall", "readme", "help", "website", "release notes", "documentation")
        for root in roots:
            if not root.exists():
                continue
            try:
                for path in root.rglob("*.lnk"):
                    stem = path.stem.strip()
                    lower = stem.lower()
                    if any(word in lower for word in ignored):
                        continue
                    key = _norm(stem)
                    if key and key not in index:
                        index[key] = path
            except OSError:
                continue
        return index

    def _start_menu_index(self) -> dict[str, Path]:
        if self._start_menu is None:
            self._start_menu = self._build_start_menu()
        return self._start_menu

    def _build_app_paths(self) -> dict[str, Path]:
        index: dict[str, Path] = {}
        try:
            import winreg
        except Exception:
            return index
        for hkey_name, key_path in _APP_PATH_KEYS:
            hkey = getattr(winreg, hkey_name, None)
            if hkey is None:
                continue
            try:
                with winreg.OpenKey(hkey, key_path) as root:
                    count = winreg.QueryInfoKey(root)[0]
                    for i in range(count):
                        try:
                            sub_name = winreg.EnumKey(root, i)
                            with winreg.OpenKey(root, sub_name) as sub:
                                value, _ = winreg.QueryValueEx(sub, "")
                                if value:
                                    path = Path(os.path.expandvars(str(value).strip('"')))
                                    index[_norm(sub_name)] = path
                                    index[_norm(path.stem)] = path
                        except OSError:
                            continue
            except OSError:
                continue
        return index

    def _app_paths_index(self) -> dict[str, Path]:
        if self._app_paths is None:
            self._app_paths = self._build_app_paths()
        return self._app_paths

    def resolve(self, raw_name: str) -> ResolvedApp:
        name = _norm(raw_name)
        if not name:
            raise AutomationError("I need an application name.")
        known = _KNOWN_EXES.get(name)
        if known:
            for candidate in known:
                for path in _candidate_paths(candidate):
                    if path.exists():
                        return ResolvedApp("exe", str(path), self._display(raw_name))
                if candidate.endswith(":"):
                    return ResolvedApp("uri", candidate, self._display(raw_name))
                if candidate.lower().endswith(".exe"):
                    # Last chance: let Windows resolve it from PATH or App Paths.
                    resolved = self._app_paths_index().get(_norm(candidate))
                    if resolved and resolved.exists():
                        return ResolvedApp("exe", str(resolved), self._display(raw_name))
                    found = shutil.which(candidate)
                    if found:
                        return ResolvedApp("exe", found, self._display(raw_name))

        direct = self._app_paths_index().get(name)
        if direct and direct.exists():
            return ResolvedApp("exe", str(direct), self._display(raw_name))

        if re.fullmatch(r"[a-z0-9][a-z0-9 ._+-]*", name):
            found = shutil.which(name) or shutil.which(name + ".exe")
            if found:
                return ResolvedApp("exe", found, self._display(raw_name))

        menu = self._start_menu_index()
        if name in menu:
            return ResolvedApp("lnk", str(menu[name]), self._display(menu[name].stem))

        scored: list[tuple[float, str, Path]] = []
        for key, path in menu.items():
            if name in key or key in name:
                score = 0.92
            else:
                score = difflib.SequenceMatcher(None, name, key).ratio()
            if score >= 0.64:
                scored.append((score, key, path))
        if scored:
            scored.sort(key=lambda item: (-item[0], len(item[1])))
            best_score, _key, best_path = scored[0]
            if best_score >= 0.64:
                return ResolvedApp("lnk", str(best_path), self._display(best_path.stem))

        raise AutomationError(
            f"I couldn't find an app called \"{raw_name}\". Try the exact name shown in the Start menu."
        )

    @staticmethod
    def _display(value: str) -> str:
        cleaned = re.sub(r"\s+", " ", value).strip().strip("\"'")
        cleaned = re.sub(r"\.lnk$", "", cleaned, flags=re.I)
        return cleaned or "the application"


class DesktopAutomation:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._resolver = AppResolver()
        self._gui_ready = False

    def _prepare_gui(self) -> None:
        if self._gui_ready:
            return
        try:
            import pyautogui

            pyautogui.FAILSAFE = True
            pyautogui.PAUSE = 0.05
        except Exception:
            pass
        self._gui_ready = True

    def _failsafe(self) -> None:
        point = wt.POINT()
        if user32.GetCursorPos(ctypes.byref(point)):
            if point.x <= 2 and point.y <= 2:
                raise AutomationError("Emergency stop: the mouse was moved to the top-left corner.")

    def _restore(self, hwnd: int) -> None:
        if hwnd and win.is_window(hwnd):
            win.focus_window(hwnd)
            time.sleep(0.16)

    def execute(self, call: ToolCall, restore_hwnd: int = 0) -> ExecutionResult:
        with self._lock:
            try:
                if not call.name:
                    return ExecutionResult(False, call.error or "No action was selected.", call.name or "")
                if call.name == "open_application":
                    return self._open_application(str(call.arguments.get("app_name", "")))
                if call.name == "type_text":
                    self._restore(restore_hwnd)
                    return self._type_text(str(call.arguments.get("text", "")))
                if call.name == "click_element":
                    self._restore(restore_hwnd)
                    return self._click_element(str(call.arguments.get("target", "")), restore_hwnd)
                if call.name == "press_hotkey":
                    self._restore(restore_hwnd)
                    return self._press_hotkey(list(call.arguments.get("keys", [])))
                if call.name == "take_screenshot":
                    self._restore(restore_hwnd)
                    time.sleep(0.22)
                    return self._screenshot()
                if call.name == "control_media":
                    return self._control_media(str(call.arguments.get("action", "")))
                return ExecutionResult(False, f"Unknown action: {call.name}", call.name)
            except AutomationError as exc:
                return ExecutionResult(False, str(exc), call.name or "")
            except Exception as exc:
                return ExecutionResult(False, f"The action failed: {exc}", call.name or "", detail=repr(exc))

    def _open_application(self, app_name: str) -> ExecutionResult:
        resolved = self._resolver.resolve(app_name)
        before = win.visible_window_handles()
        hwnd = 0
        pid = 0
        if resolved.kind == "uri":
            os.startfile(resolved.target)  # type: ignore[attr-defined]
            hwnd = win.wait_for_new_window(before, timeout=5.0)
        elif resolved.kind == "lnk":
            os.startfile(resolved.target)  # type: ignore[attr-defined]
            hwnd = win.wait_for_new_window(before, timeout=5.0)
        else:
            target = Path(resolved.target)
            if not target.exists():
                raise AutomationError(f"The app path no longer exists: {target}")
            creationflags = 0
            if target.suffix.lower() in {".exe", ".com", ".bat", ".cmd"}:
                creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            try:
                process = subprocess.Popen(
                    [str(target)],
                    cwd=str(target.parent),
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    close_fds=True,
                    creationflags=creationflags,
                )
                pid = int(process.pid)
            except OSError as exc:
                raise AutomationError(f"Windows could not start {resolved.display_name}: {exc}") from exc
            hwnd = win.wait_for_pid_window(pid, timeout=5.5)
            if not hwnd:
                hwnd = win.wait_for_new_window(before, timeout=2.0)
        if hwnd:
            win.focus_window(hwnd)
            time.sleep(0.18)
        display = resolved.display_name
        return ExecutionResult(True, f"Opened {display}.", "open_application",
                               detail=f"pid={pid} hwnd={hwnd} target={resolved.target}")

    def _type_text(self, text: str) -> ExecutionResult:
        if not text:
            raise AutomationError("There was no text to type.")
        self._prepare_gui()
        self._failsafe()
        time.sleep(0.05)
        if text.isascii() and len(text) <= 800 and not any(ch in text for ch in "\t"):
            self._type_ascii(text)
        else:
            self._paste_text(text)
        return ExecutionResult(True, f"Typed {len(text)} characters.", "type_text")

    def _type_ascii(self, text: str) -> None:
        from pynput.keyboard import Controller, Key

        controller = Controller()
        buffer: list[str] = []

        def flush() -> None:
            if buffer:
                controller.type("".join(buffer))
                buffer.clear()

        for char in text:
            if char == "\n":
                flush()
                controller.press(Key.enter)
                controller.release(Key.enter)
            elif char == "\r":
                continue
            elif char == "\t":
                flush()
                controller.press(Key.tab)
                controller.release(Key.tab)
            else:
                buffer.append(char)
                if len(buffer) >= 64:
                    flush()
        flush()
        time.sleep(0.12)

    def _paste_text(self, text: str) -> None:
        import pyperclip
        from pynput.keyboard import Controller, Key

        previous: str | None = None
        try:
            previous = pyperclip.paste()
        except Exception:
            previous = None
        pyperclip.copy(text)
        time.sleep(0.12)
        controller = Controller()
        with controller.pressed(Key.ctrl):
            controller.press("v")
            controller.release("v")
        time.sleep(0.4)
        if previous is not None:
            try:
                pyperclip.copy(previous)
            except Exception:
                pass

    def _press_hotkey(self, keys: Iterable[str]) -> ExecutionResult:
        self._prepare_gui()
        self._failsafe()
        normalized = normalize_keys(list(keys))
        from pynput.keyboard import Controller, Key

        key_map = {
            "ctrl": Key.ctrl,
            "alt": Key.alt,
            "shift": Key.shift,
            "win": Key.cmd,
            "space": Key.space,
            "enter": Key.enter,
            "tab": Key.tab,
            "esc": Key.esc,
            "backspace": Key.backspace,
            "delete": Key.delete,
            "insert": Key.insert,
            "home": Key.home,
            "end": Key.end,
            "pageup": Key.page_up,
            "pagedown": Key.page_down,
            "up": Key.up,
            "down": Key.down,
            "left": Key.left,
            "right": Key.right,
            "capslock": Key.caps_lock,
            "printscreen": Key.print_screen,
            "pause": Key.pause,
            "menu": Key.menu,
        }
        for i in range(1, 25):
            key_map[f"f{i}"] = getattr(Key, f"f{i}", Key.f1)
        mapped = [key_map.get(key, key) for key in normalized]
        controller = Controller()
        pressed: list[object] = []
        try:
            for key in mapped:
                controller.press(key)
                pressed.append(key)
                time.sleep(0.035)
        finally:
            for key in reversed(pressed):
                try:
                    controller.release(key)
                except Exception:
                    pass
        time.sleep(0.08)
        return ExecutionResult(True, "Pressed " + "+".join(normalized) + ".", "press_hotkey")

    def _click_element(self, target: str, restore_hwnd: int) -> ExecutionResult:
        self._prepare_gui()
        self._failsafe()
        coordinate = re.fullmatch(
            r"\s*(?:at\s+|coordinates?\s+)?(-?\d{1,5})\s*[, ]\s*(-?\d{1,5})\s*",
            target,
            flags=re.I,
        )
        if coordinate:
            x, y = int(coordinate.group(1)), int(coordinate.group(2))
            self._check_point(x, y)
            self._mouse_click(x, y)
            return ExecutionResult(True, f"Clicked {x}, {y}.", "click_element")

        screen = self._screen_size()
        semantic = target.lower().strip().strip(".")
        aliases = {
            "center": (screen[0] // 2, screen[1] // 2),
            "middle": (screen[0] // 2, screen[1] // 2),
            "top left": (screen[0] // 6, screen[1] // 6),
            "top right": (screen[0] * 5 // 6, screen[1] // 6),
            "bottom left": (screen[0] // 6, screen[1] * 5 // 6),
            "bottom right": (screen[0] * 5 // 6, screen[1] * 5 // 6),
            "top center": (screen[0] // 2, screen[1] // 6),
            "bottom center": (screen[0] // 2, screen[1] * 5 // 6),
            "left center": (screen[0] // 6, screen[1] // 2),
            "right center": (screen[0] * 5 // 6, screen[1] // 2),
        }
        if semantic in aliases:
            x, y = aliases[semantic]
            self._mouse_click(x, y)
            return ExecutionResult(True, f"Clicked the {semantic} of the screen.", "click_element")

        self._click_uia(target, restore_hwnd)
        return ExecutionResult(True, f"Clicked {target}.", "click_element")

    @staticmethod
    def _screen_size() -> tuple[int, int]:
        try:
            import pyautogui

            size = pyautogui.size()
            return int(size.width), int(size.height)
        except Exception:
            return int(user32.GetSystemMetrics(0)), int(user32.GetSystemMetrics(1))

    def _check_point(self, x: int, y: int) -> None:
        width, height = self._screen_size()
        if not (0 <= x < width and 0 <= y < height):
            raise AutomationError(f"The point {x}, {y} is outside the screen.")

    def _mouse_click(self, x: int, y: int) -> None:
        self._failsafe()
        import pyautogui

        pyautogui.moveTo(x, y, duration=0.12)
        pyautogui.click()

    def _click_uia(self, target: str, hwnd: int) -> None:
        try:
            import comtypes
            import uiautomation as auto
        except Exception as exc:
            raise AutomationError("UI element search is unavailable on this system.") from exc
        comtypes.CoInitialize()
        try:
            auto.SetGlobalSearchTimeout(2.0)
            root = auto.ControlFromHandle(hwnd) if hwnd else auto.GetRootControl()
            if root is None:
                root = auto.GetRootControl()
            wanted = target.strip().lower()
            found = None
            for kwargs in ({"SubName": target}, {"Name": target}):
                try:
                    candidate = root.Control(searchDepth=12, **kwargs)
                    if candidate and candidate.Exists(0.2):
                        found = candidate
                        break
                except Exception:
                    continue
            if found is None:
                found = self._walk_uia(root, wanted, max_depth=10, max_nodes=600)
            if found is None:
                raise AutomationError(
                    f"I couldn't find a visible control named \"{target}\" in the foreground window."
                )
            try:
                found.SetFocus()
            except Exception:
                pass
            try:
                found.Click(simulateMove=False)
            except Exception:
                try:
                    point = found.GetClickablePoint()
                    self._mouse_click(int(point.x), int(point.y))
                except Exception as exc:
                    raise AutomationError(f"I found \"{target}\" but couldn't click it.") from exc
            time.sleep(0.1)
        finally:
            try:
                comtypes.CoUninitialize()
            except Exception:
                pass

    @staticmethod
    def _walk_uia(root, wanted: str, max_depth: int, max_nodes: int):
        queue = [(root, 0)]
        seen = 0
        while queue and seen < max_nodes:
            control, depth = queue.pop(0)
            seen += 1
            try:
                name = (control.Name or "").strip().lower()
                automation_id = (control.AutomationId or "").strip().lower()
                if wanted and (wanted in name or wanted in automation_id):
                    return control
            except Exception:
                pass
            if depth >= max_depth:
                continue
            try:
                children = control.GetChildren()
            except Exception:
                children = []
            for child in children:
                queue.append((child, depth + 1))
        return None

    def _screenshot(self) -> ExecutionResult:
        from PIL import ImageGrab

        folder = pictures_dir()
        stamp = time.strftime("%Y-%m-%d_%H-%M-%S")
        path = folder / f"Needle_Screenshot_{stamp}.png"
        image = ImageGrab.grab(all_screens=False)
        image.save(path, "PNG")
        return ExecutionResult(True, f"Saved a screenshot to {path.name}.", "take_screenshot",
                               path=str(path))

    def _control_media(self, action: str) -> ExecutionResult:
        self._prepare_gui()
        win.send_media_key(action)
        labels = {
            "play": "Toggled playback.",
            "pause": "Toggled playback.",
            "next": "Skipped to the next track.",
            "previous": "Went to the previous track.",
            "mute": "Toggled mute.",
        }
        return ExecutionResult(True, labels.get(action, "Media command sent."), "control_media")
