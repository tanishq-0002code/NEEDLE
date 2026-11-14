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
from .catalog import normalize_keys, normalize_system_action, normalize_window_action
from .config import load_config
from .models import ExecutionResult, ToolCall
from .paths import pictures_dir

user32 = ctypes.WinDLL("user32", use_last_error=True)

WM_CLOSE = 0x0010
SW_MINIMIZE = 6
SW_MAXIMIZE = 3
APP_MATCH_THRESHOLD = 0.80
_PROTECTED_PROCESSES = {
    "csrss.exe", "winlogon.exe", "services.exe", "lsass.exe", "smss.exe",
    "wininit.exe", "system", "registry", "memory compression", "idle",
    "explorer.exe", "needledesktop.exe",
}


class AutomationError(RuntimeError):
    pass


class AmbiguousAppError(AutomationError):
    """Raised when the requested app name is unclear; carries candidates."""

    def __init__(self, message: str, candidates: list[str] | None = None) -> None:
        super().__init__(message)
        self.candidates = list(candidates or [])


class _LUID(ctypes.Structure):
    _fields_ = [("LowPart", wt.DWORD), ("HighPart", wt.LONG)]


class _LUID_AND_ATTRIBUTES(ctypes.Structure):
    _fields_ = [("Luid", _LUID), ("Attributes", wt.DWORD)]


class _TOKEN_PRIVILEGES(ctypes.Structure):
    _fields_ = [("PrivilegeCount", wt.DWORD),
                ("Privileges", _LUID_AND_ATTRIBUTES * 1)]


EWX_POWEROFF = 0x00000008
EWX_REBOOT = 0x00000002
EWX_FORCEIFHUNG = 0x00000010


def _enable_shutdown_privilege() -> bool:
    try:
        advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

        token = wt.HANDLE()
        if not advapi32.OpenProcessToken(
            kernel32.GetCurrentProcess(), 0x0020 | 0x0008, ctypes.byref(token)
        ):
            return False
        luid = _LUID()
        if not advapi32.LookupPrivilegeValueW(None, "SeShutdownPrivilege", ctypes.byref(luid)):
            return False
        privileges = _TOKEN_PRIVILEGES()
        privileges.PrivilegeCount = 1
        privileges.Privileges[0].Luid = luid
        privileges.Privileges[0].Attributes = 0x00000002
        return bool(advapi32.AdjustTokenPrivileges(
            token, False, ctypes.byref(privileges), 0, None, None
        ))
    except Exception:
        return False


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

        scored = self.candidates(name)
        if scored and scored[0][0] >= APP_MATCH_THRESHOLD:
            best_score, _key, best_path = scored[0]
            return ResolvedApp("lnk", str(best_path), self._display(best_path.stem))

        names = ", ".join(key for _score, key, _path in scored[:3])
        message = f"I couldn't find an app called \"{raw_name}\"."
        if names:
            message += f" Did you mean: {names}?"
        else:
            message += " Try the exact name shown in the Start menu."
        raise AmbiguousAppError(message, [key for _score, key, _path in scored[:3]])

    def candidates(self, raw_name: str, limit: int = 5) -> list[tuple[float, str, Path]]:
        """Ranked Start-menu matches (score, menu key, path) for clarification."""
        name = _norm(raw_name)
        if not name:
            return []
        menu = self._start_menu_index()
        scored: list[tuple[float, str, Path]] = []
        for key, path in menu.items():
            if name == key:
                scored.append((1.0, key, path))
        if scored:
            scored.sort(key=lambda item: (-item[0], len(item[1])))
            return scored[:limit]
        for key, path in menu.items():
            if name in key or key in name:
                score = 0.92
            else:
                score = difflib.SequenceMatcher(None, name, key).ratio()
            if score >= 0.64:
                scored.append((score, key, path))
        scored.sort(key=lambda item: (-item[0], len(item[1])))
        return scored[:limit]

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

            pyautogui.FAILSAFE = False
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
                if call.name == "close_application":
                    return self._close_application(
                        str(call.arguments.get("app_name", "")),
                        bool(call.arguments.get("force", False)),
                    )
                if call.name == "focus_window":
                    return self._focus_window(str(call.arguments.get("title", "")))
                if call.name == "window_manage":
                    return self._window_manage(
                        str(call.arguments.get("title", "")),
                        str(call.arguments.get("action", "")),
                    )
                if call.name == "system_control":
                    return self._system_control(str(call.arguments.get("action", "")))
                if call.name == "read_screen":
                    return self._read_screen(str(call.arguments.get("scope", "foreground")))
                if call.name == "clipboard":
                    return self._clipboard(
                        str(call.arguments.get("action", "get")),
                        str(call.arguments.get("text", "")),
                    )
                if call.name == "file_operation":
                    return self._file_operation(
                        str(call.arguments.get("op", "")),
                        str(call.arguments.get("path", "")),
                        str(call.arguments.get("new_path", "")),
                        call.arguments.get("content"),
                    )
                if call.name == "run_command":
                    return self._run_command(
                        str(call.arguments.get("command", "")),
                        int(call.arguments.get("timeout", 15)),
                    )
                if call.name == "browser_control":
                    return self._browser_control(
                        str(call.arguments.get("action", "")),
                        str(call.arguments.get("url", "")),
                        str(call.arguments.get("browser", "")),
                    )
                if call.name == "memory":
                    return self._memory(
                        str(call.arguments.get("action", "")),
                        str(call.arguments.get("alias", "")),
                        str(call.arguments.get("target", "")),
                        str(call.arguments.get("key", "")),
                        str(call.arguments.get("value", "")),
                    )
                if call.name == "schedule":
                    return self._schedule(
                        str(call.arguments.get("action", "")),
                        float(call.arguments.get("minutes", 5) or 5),
                        str(call.arguments.get("message", "")),
                        call.arguments.get("id"),
                        bool(call.arguments.get("recurring", False)),
                    )
                if call.name == "macro":
                    return self._macro(
                        str(call.arguments.get("action", "")),
                        str(call.arguments.get("name", "")),
                        str(call.arguments.get("steps", "")),
                    )
                if call.name == "type_text":
                    target_hwnd = self._resolve_target_window(
                        call.arguments.get("window"), restore_hwnd
                    )
                    self._restore(target_hwnd)
                    return self._type_text(str(call.arguments.get("text", "")))
                if call.name == "click_element":
                    target_hwnd = self._resolve_target_window(
                        call.arguments.get("window"), restore_hwnd
                    )
                    self._restore(target_hwnd)
                    return self._click_element(
                        str(call.arguments.get("target", "")), target_hwnd
                    )
                if call.name == "press_hotkey":
                    self._restore(restore_hwnd)
                    return self._press_hotkey(list(call.arguments.get("keys", [])))
                if call.name == "take_screenshot":
                    self._restore(restore_hwnd)
                    time.sleep(0.22)
                    return self._screenshot()
                if call.name == "capture_camera":
                    return self._capture_camera()
                if call.name == "control_media":
                    return self._control_media(str(call.arguments.get("action", "")))
                return ExecutionResult(False, f"Unknown action: {call.name}", call.name)
            except AutomationError as exc:
                return ExecutionResult(False, str(exc), call.name or "")
            except Exception as exc:
                return ExecutionResult(False, f"The action failed: {exc}", call.name or "", detail=repr(exc))

    def _open_application(self, app_name: str) -> ExecutionResult:
        app_name = self._resolve_alias(app_name)
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
        import pyautogui

        raw = (target or "").strip()
        lower = raw.lower()

        # 1. Cursor movement without clicking
        move_match = re.search(r"^(?:move\s+(?:the\s+)?(?:cursor|mouse)\s+(?:to\s+)?|hover\s+(?:over\s+)?)(.+)$", lower)
        if move_match:
            sub = move_match.group(1).strip()
            coord = re.search(r"(-?\d{1,5})\s*[, ]\s*(-?\d{1,5})", sub)
            if coord:
                x, y = int(coord.group(1)), int(coord.group(2))
                self._check_point(x, y)
                pyautogui.moveTo(x, y, duration=0.2)
                return ExecutionResult(True, f"Moved cursor to {x}, {y}.", "click_element")
            screen = self._screen_size()
            aliases = self._screen_aliases(screen)
            if sub in aliases:
                x, y = aliases[sub]
                pyautogui.moveTo(x, y, duration=0.2)
                return ExecutionResult(True, f"Moved cursor to {sub}.", "click_element")

        # 2. Scrolling
        scroll_match = re.search(r"\bscroll\s+(up|down)(?:\s+(\d+))?\b", lower)
        if scroll_match:
            direction = scroll_match.group(1).lower()
            amount = int(scroll_match.group(2)) if scroll_match.group(2) else 5
            clicks = amount * 120 if direction == "up" else -amount * 120
            pyautogui.scroll(clicks)
            return ExecutionResult(True, f"Scrolled {direction} {amount} units.", "click_element")

        # 3. Mouse button variants (right click, double click, middle click)
        btn = "left"
        clicks_count = 1
        sub_target = raw
        if re.search(r"\bright[\s-]click\b", lower):
            btn = "right"
            sub_target = re.sub(r"(?i)\b(?:please\s+)?(?:do\s+a\s+)?right[\s-]click(?:\s+(?:on|at))?\s*", "", raw).strip()
        elif re.search(r"\bdouble[\s-]click\b", lower):
            btn = "left"
            clicks_count = 2
            sub_target = re.sub(r"(?i)\b(?:please\s+)?(?:do\s+a\s+)?double[\s-]click(?:\s+(?:on|at))?\s*", "", raw).strip()
        elif re.search(r"\bmiddle[\s-]click\b", lower):
            btn = "middle"
            sub_target = re.sub(r"(?i)\b(?:please\s+)?(?:do\s+a\s+)?middle[\s-]click(?:\s+(?:on|at))?\s*", "", raw).strip()
        else:
            sub_target = re.sub(r"(?i)\b(?:please\s+)?(?:click|tap)(?:\s+(?:on|at))?\s*", "", raw).strip()

        # If sub_target is empty, click at the current mouse cursor location
        if not sub_target or sub_target.lower() in {"here", "this", "it"}:
            pos = pyautogui.position()
            pyautogui.click(pos.x, pos.y, clicks=clicks_count, button=btn)
            action_desc = "Right-clicked" if btn == "right" else ("Double-clicked" if clicks_count == 2 else "Clicked")
            return ExecutionResult(True, f"{action_desc} at current position ({pos.x}, {pos.y}).", "click_element")

        coordinate = re.fullmatch(
            r"\s*(?:at\s+|coordinates?\s+)?(-?\d{1,5})\s*[, ]\s*(-?\d{1,5})\s*",
            sub_target,
            flags=re.I,
        )
        if coordinate:
            x, y = int(coordinate.group(1)), int(coordinate.group(2))
            self._check_point(x, y)
            pyautogui.moveTo(x, y, duration=0.12)
            pyautogui.click(x, y, clicks=clicks_count, button=btn)
            action_desc = "Right-clicked" if btn == "right" else ("Double-clicked" if clicks_count == 2 else "Clicked")
            return ExecutionResult(True, f"{action_desc} at {x}, {y}.", "click_element")

        screen = self._screen_size()
        semantic = sub_target.lower().strip().strip(".")
        aliases = self._screen_aliases(screen)
        if semantic in aliases:
            x, y = aliases[semantic]
            pyautogui.moveTo(x, y, duration=0.12)
            pyautogui.click(x, y, clicks=clicks_count, button=btn)
            action_desc = "Right-clicked" if btn == "right" else ("Double-clicked" if clicks_count == 2 else "Clicked")
            return ExecutionResult(True, f"{action_desc} the {semantic} of the screen.", "click_element")

        self._click_uia(sub_target, restore_hwnd, button=btn, clicks=clicks_count)
        action_desc = "Right-clicked" if btn == "right" else ("Double-clicked" if clicks_count == 2 else "Clicked")
        return ExecutionResult(True, f"{action_desc} {sub_target}.", "click_element")

    @staticmethod
    def _screen_aliases(screen: tuple[int, int]) -> dict[str, tuple[int, int]]:
        w, h = screen
        return {
            "center": (w // 2, h // 2),
            "middle": (w // 2, h // 2),
            "top left": (w // 6, h // 6),
            "top right": (w * 5 // 6, h // 6),
            "bottom left": (w // 6, h * 5 // 6),
            "bottom right": (w * 5 // 6, h * 5 // 6),
            "top center": (w // 2, h // 6),
            "bottom center": (w // 2, h * 5 // 6),
            "left center": (w // 6, h // 2),
            "right center": (w * 5 // 6, h // 2),
        }

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

    def _mouse_click(self, x: int, y: int, button: str = "left", clicks: int = 1) -> None:
        self._failsafe()
        import pyautogui

        pyautogui.moveTo(x, y, duration=0.12)
        pyautogui.click(x, y, clicks=clicks, button=button)

    def _click_uia(self, target: str, hwnd: int, button: str = "left", clicks: int = 1) -> None:
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
            if button == "left" and clicks == 1:
                try:
                    found.Click(simulateMove=False)
                    time.sleep(0.1)
                    return
                except Exception:
                    pass
            try:
                point = found.GetClickablePoint()
                self._mouse_click(int(point.x), int(point.y), button=button, clicks=clicks)
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

    def _grab_screen_image(self):
        """Captures the screen using Windows GDI BitBlt, with PyQt6 and PIL fallbacks."""
        # Method 1: Direct Windows GDI BitBlt (100% reliable across all sessions)
        try:
            from PIL import Image

            gdi32 = ctypes.windll.gdi32
            w = user32.GetSystemMetrics(0)
            h = user32.GetSystemMetrics(1)
            hdc_screen = user32.GetDC(0)
            if hdc_screen:
                try:
                    hdc_mem = gdi32.CreateCompatibleDC(hdc_screen)
                    if hdc_mem:
                        try:
                            hbm = gdi32.CreateCompatibleBitmap(hdc_screen, w, h)
                            if hbm:
                                try:
                                    gdi32.SelectObject(hdc_mem, hbm)
                                    gdi32.BitBlt(hdc_mem, 0, 0, w, h, hdc_screen, 0, 0, 0x00CC0020)
                                    bi = ctypes.c_buffer(40)
                                    ctypes.c_ulong.from_buffer(bi, 0).value = 40
                                    ctypes.c_long.from_buffer(bi, 4).value = w
                                    ctypes.c_long.from_buffer(bi, 8).value = -h
                                    ctypes.c_ushort.from_buffer(bi, 12).value = 1
                                    ctypes.c_ushort.from_buffer(bi, 14).value = 32
                                    buf = ctypes.create_string_buffer(w * h * 4)
                                    gdi32.GetDIBits(hdc_mem, hbm, 0, h, buf, bi, 0)
                                    return Image.frombuffer("RGBA", (w, h), buf, "raw", "BGRA", 0, 1).convert("RGB")
                                finally:
                                    gdi32.DeleteObject(hbm)
                        finally:
                            gdi32.DeleteDC(hdc_mem)
                finally:
                    user32.ReleaseDC(0, hdc_screen)
        except Exception:
            pass

        # Method 2: PyQt6 screen capture
        try:
            import io
            from PIL import Image
            from PyQt6.QtWidgets import QApplication
            from PyQt6.QtGui import QGuiApplication
            from PyQt6.QtCore import QBuffer, QIODevice

            app = QApplication.instance()
            if app is not None:
                screen = QGuiApplication.primaryScreen()
                if screen is not None:
                    pix = screen.grabWindow(0)
                    if not pix.isNull():
                        qbuf = QBuffer()
                        qbuf.open(QIODevice.OpenModeFlag.ReadWrite)
                        pix.save(qbuf, "PNG")
                        data = bytes(qbuf.data())
                        qbuf.close()
                        return Image.open(io.BytesIO(data))
        except Exception:
            pass

        # Method 3: Pillow ImageGrab fallback
        from PIL import ImageGrab
        return ImageGrab.grab(all_screens=False)

    def _screenshot(self) -> ExecutionResult:
        folder = pictures_dir()
        stamp = time.strftime("%Y-%m-%d_%H-%M-%S")
        path = folder / f"Needle_Screenshot_{stamp}.png"
        image = self._grab_screen_image()
        image.save(path, "PNG")

        fg = win.get_foreground_window()
        title = win.get_window_title(fg) if fg else ""
        msg = f"Saved a screenshot to {path.name}."
        if title:
            msg += f" (Active window: {title})"
        return ExecutionResult(True, msg, "take_screenshot", path=str(path))

    def _capture_camera(self) -> ExecutionResult:
        from PIL import Image

        folder = pictures_dir()
        stamp = time.strftime("%Y-%m-%d_%H-%M-%S")
        path = folder / f"Needle_Camera_{stamp}.jpg"

        try:
            import cv2

            cap = cv2.VideoCapture(0, cv2.CAP_DSHOW)
            if not cap.isOpened():
                cap = cv2.VideoCapture(0)
            if not cap.isOpened():
                raise AutomationError("No camera device could be opened.")
            try:
                for _ in range(4):
                    ret, frame = cap.read()
                    if not ret:
                        break
                    time.sleep(0.04)
                if not ret or frame is None:
                    raise AutomationError("Camera capture returned empty frame.")
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                img = Image.fromarray(rgb)
                img.save(path, "JPEG", quality=92)
                return ExecutionResult(True, f"Captured photo to {path.name}.", "capture_camera", path=str(path))
            finally:
                cap.release()
        except Exception as exc:
            try:
                os.startfile("microsoft.windows.camera:")
                return ExecutionResult(True, "Opened Windows Camera application.", "capture_camera")
            except Exception:
                raise AutomationError(f"Could not access camera: {exc}") from exc

    # ------------------------------------------------------------------ windows & power
    @staticmethod
    def _resolve_alias(name: str) -> str:
        try:
            from .memory import get_alias

            alias = get_alias(name)
            if alias:
                return alias
        except Exception:
            pass
        return name

    @staticmethod
    def _windows_by_title(title: str) -> list[win.WindowInfo]:
        wanted = re.sub(r"\s+", " ", str(title or "").strip().lower())
        if not wanted:
            return []
        return [info for info in win.enumerate_windows() if wanted in info.title.lower()]

    @staticmethod
    def _pids_for_exe(exe_name: str) -> set[int]:
        wanted = str(exe_name or "").strip().lower()
        pids: set[int] = set()
        if not wanted:
            return pids
        try:
            for proc in psutil.process_iter(["pid", "name", "exe"]):
                try:
                    info = proc.info or {}
                    name = str(info.get("name") or "").lower()
                    path = Path(str(info.get("exe") or "")).name.lower()
                    if wanted in {name, path}:
                        pids.add(int(info["pid"]))
                except (psutil.NoSuchProcess, psutil.AccessDenied, KeyError, TypeError):
                    continue
        except Exception:
            return pids
        return pids

    def _windows_for_pids(self, pids: set[int]) -> list[win.WindowInfo]:
        return [info for info in win.enumerate_windows() if info.pid in pids]

    def _resolve_target_window(self, window: object, fallback: int) -> int:
        title = str(window or "").strip()
        if not title:
            return fallback
        matches = self._windows_by_title(title)
        if not matches:
            raise AutomationError(f"I couldn't find a window titled \"{title}\".")
        if len(matches) > 1:
            titles = ", ".join(info.title for info in matches[:4])
            raise AutomationError(f"Several windows match \"{title}\": {titles}.")
        return matches[0].hwnd

    def _close_application(self, app_name: str, force: bool = False) -> ExecutionResult:
        app_name = self._resolve_alias(app_name)
        resolved = self._resolver.resolve(app_name)
        if resolved.kind != "exe":
            raise AutomationError(
                f"I can't close {resolved.display_name} this way. Name a running application."
            )
        exe_name = Path(resolved.target).name
        if exe_name.lower() in _PROTECTED_PROCESSES:
            raise AutomationError(f"Refusing to close {exe_name}: it is a system process.")
        if exe_name.lower() == "explorer.exe":
            raise AutomationError(
                "Use 'close window' (Alt+F4) to close a File Explorer window."
            )
        pids = self._pids_for_exe(exe_name)
        if not pids:
            raise AutomationError(f"{resolved.display_name} doesn't appear to be running.")
        windows = self._windows_for_pids(pids)
        if not windows and not force:
            raise AutomationError(f"{resolved.display_name} has no open window to close.")

        hwnds = [info.hwnd for info in windows]
        for hwnd in hwnds:
            try:
                user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
            except Exception:
                continue
        deadline = time.monotonic() + 2.5
        while time.monotonic() < deadline:
            remaining = [hwnd for hwnd in hwnds if win.is_window(hwnd)]
            if not remaining:
                break
            time.sleep(0.15)
        remaining = [hwnd for hwnd in hwnds if win.is_window(hwnd)]
        closed = len(hwnds) - len(remaining)

        if remaining and force:
            for pid in sorted(pids):
                try:
                    process = psutil.Process(pid)
                    if process.name().lower() in _PROTECTED_PROCESSES:
                        continue
                    process.terminate()
                except psutil.Error:
                    continue
            time.sleep(1.2)
            survivors: list[int] = []
            for pid in sorted(pids):
                try:
                    process = psutil.Process(pid)
                except psutil.Error:
                    continue
                if not process.is_running():
                    continue
                try:
                    process.kill()
                except psutil.Error:
                    pass
                survivors.append(pid)
            if survivors:
                raise AutomationError(
                    f"{resolved.display_name} did not fully close (pid(s) {survivors})."
                )
            remaining = []
        elif remaining:
            raise AutomationError(
                f"{resolved.display_name} did not close. Say 'force close {app_name}' to end it."
            )

        if closed:
            message = f"Closed {resolved.display_name} ({closed} window{'s' if closed != 1 else ''})."
        else:
            message = f"Ended {resolved.display_name}."
        return ExecutionResult(True, message, "close_application",
                               detail=f"pids={sorted(pids)}")

    def _focus_window(self, title: str) -> ExecutionResult:
        title = self._resolve_alias(title)
        matches = self._windows_by_title(title)
        if not matches:
            raise AutomationError(f"I couldn't find a window titled \"{title}\".")
        if len(matches) > 1:
            titles = ", ".join(info.title for info in matches[:4])
            raise AutomationError(f"Several windows match \"{title}\": {titles}.")
        info = matches[0]
        win.focus_window(info.hwnd)
        time.sleep(0.16)
        return ExecutionResult(True, f"Switched to {info.title}.", "focus_window")

    def _window_manage(self, title: str, action: str) -> ExecutionResult:
        title = self._resolve_alias(title)
        normalized = normalize_window_action(action)
        if not normalized:
            raise AutomationError("Use minimize, maximize, or restore.")
        matches = self._windows_by_title(title)
        if not matches:
            raise AutomationError(f"I couldn't find a window titled \"{title}\".")
        if len(matches) > 1:
            titles = ", ".join(info.title for info in matches[:4])
            raise AutomationError(f"Several windows match \"{title}\": {titles}.")
        info = matches[0]
        commands = {
            "minimize": SW_MINIMIZE,
            "maximize": SW_MAXIMIZE,
            "restore": win.SW_RESTORE,
        }
        win.show_window(info.hwnd, commands[normalized])
        time.sleep(0.18)
        labels = {"minimize": "Minimized", "maximize": "Maximized", "restore": "Restored"}
        return ExecutionResult(True, f"{labels[normalized]} {info.title}.", "window_manage")

    def _system_control(self, action: str) -> ExecutionResult:
        normalized = normalize_system_action(action)
        if not normalized:
            raise AutomationError("Use lock, shutdown, restart, sleep, or hibernate.")
        if normalized == "lock":
            if not bool(ctypes.windll.user32.LockWorkStation()):
                raise AutomationError("Windows refused to lock the workstation.")
            return ExecutionResult(True, "Locked the PC.", "system_control")
        if normalized in {"shutdown", "restart"}:
            flags = EWX_FORCEIFHUNG | (EWX_POWEROFF if normalized == "shutdown" else EWX_REBOOT)
            if not _enable_shutdown_privilege():
                raise AutomationError("Windows did not grant shutdown permission.")
            if not bool(user32.ExitWindowsEx(flags, 0)):
                raise AutomationError(f"Windows refused to {normalized}.")
            return ExecutionResult(True, f"The PC is going to {normalized}.", "system_control")
        powrprof = ctypes.WinDLL("powrprof")
        hibernate = normalized == "hibernate"
        if not bool(powrprof.SetSuspendState(bool(hibernate), False, False)):
            raise AutomationError(f"Windows refused to {normalized} the PC.")
        return ExecutionResult(True, f"The PC is going to {normalized}.", "system_control")

    def _read_screen(self, scope: str = "foreground") -> ExecutionResult:
        try:
            from .vision import crop_to_rect, ocr_image
        except Exception as exc:
            raise AutomationError(f"Screen reading is unavailable: {exc}") from exc
        image = self._grab_screen_image()
        scope = scope if scope in {"foreground", "full"} else "foreground"
        title = ""
        if scope == "foreground":
            hwnd = win.get_foreground_window()
            title = win.get_window_title(hwnd) if hwnd else ""
            rect = win.get_window_rect(hwnd) if hwnd else (0, 0, *image.size)
            image = crop_to_rect(image, rect)
        text = ocr_image(image)
        folder = pictures_dir()
        stamp = time.strftime("%Y-%m-%d_%H-%M-%S")
        path = folder / f"Needle_Screen_{stamp}.png"
        try:
            self._grab_screen_image().save(path, "PNG")
        except Exception:
            path = None
        if not text.strip():
            raise AutomationError("I couldn't read any text from the screen.")
        preview = text.strip()
        if len(preview) > 1500:
            preview = preview[:1497] + "..."
        message = f"Read {len(text)} characters from the {scope} screen."
        if title:
            message += f" (Window: {title})"
        message += f"\n\n{preview}"
        return ExecutionResult(True, message, "read_screen", path=str(path) if path else None)

    def _clipboard(self, action: str, text: str = "") -> ExecutionResult:
        action = action.strip().lower()
        if action not in {"get", "set"}:
            action = "get"
        if action == "get":
            try:
                import pyperclip

                value = pyperclip.paste()
            except Exception as exc:
                raise AutomationError(f"Could not read the clipboard: {exc}") from exc
            value = str(value or "")
            if not value.strip():
                return ExecutionResult(True, "The clipboard is empty.", "clipboard")
            preview = value if len(value) <= 1500 else value[:1497] + "..."
            return ExecutionResult(True, f"Clipboard contents:\n{preview}", "clipboard")
        try:
            import pyperclip

            pyperclip.copy(text)
        except Exception as exc:
            raise AutomationError(f"Could not write the clipboard: {exc}") from exc
        return ExecutionResult(True, f"Copied {len(text)} characters to the clipboard.", "clipboard")

    def _file_operation(self, op: str, path: str, new_path: str = "", content: Any = None) -> ExecutionResult:
        from .config import load_config
        from .filesystem import (
            delete_path,
            format_listing,
            list_directory,
            move_path,
            read_text,
            resolve_path,
        )

        cfg = load_config()
        if not getattr(cfg, "allow_file_access", True):
            raise AutomationError("File access is disabled in Settings.")
        allow_any = bool(getattr(cfg, "allow_any_path", False))
        source = resolve_path(path, allow_any=allow_any)
        op = op.strip().lower()
        if op == "create":
            source.parent.mkdir(parents=True, exist_ok=True)
            payload = "" if content is None else str(content)
            source.write_text(payload, encoding="utf-8")
            return ExecutionResult(True, f"Created {source} ({len(payload)} bytes).", "file_operation")
        if op == "read":
            if not source.exists():
                raise AutomationError(f"File not found: {source}")
            if source.is_dir():
                raise AutomationError("That path is a folder. Use list instead of read.")
            data = read_text(source)
            return ExecutionResult(True, f"Contents of {source.name}:\n{data}", "file_operation")
        if op == "append":
            payload = "" if content is None else str(content)
            source.parent.mkdir(parents=True, exist_ok=True)
            with source.open("a", encoding="utf-8") as handle:
                handle.write(payload)
            return ExecutionResult(True, f"Appended {len(payload)} characters to {source}.", "file_operation")
        if op == "list":
            if not source.exists():
                raise AutomationError(f"Folder not found: {source}")
            if not source.is_dir():
                raise AutomationError("That path is a file. Use read instead of list.")
            entries = list_directory(source)
            return ExecutionResult(True, format_listing(source, entries), "file_operation")
        if op == "rename":
            destination = resolve_path(new_path, allow_any=allow_any)
            if destination.exists():
                raise AutomationError(f"Destination already exists: {destination}")
            source.rename(destination)
            return ExecutionResult(True, f"Renamed {source} to {destination}.", "file_operation")
        if op == "move":
            destination = resolve_path(new_path, allow_any=allow_any)
            result_path = move_path(source, destination)
            return ExecutionResult(True, f"Moved {source} to {result_path}.", "file_operation")
        if op == "delete":
            if not source.exists():
                raise AutomationError(f"Path not found: {source}")
            delete_path(source)
            return ExecutionResult(True, f"Deleted {source}.", "file_operation")
        raise AutomationError(f"Unsupported file operation: {op}")

    def _run_command(self, command: str, timeout: int = 15) -> ExecutionResult:
        from .config import load_config

        cfg = load_config()
        if not getattr(cfg, "allow_shell", False):
            raise AutomationError("Shell commands are disabled in Settings.")
        command = str(command or "").strip()
        if not command:
            raise AutomationError("No command was provided.")
        if any(ch in command for ch in ("|", ">", "<", "&", ";", "`", "$(")):
            raise AutomationError(
                "Shell metacharacters (pipes, redirects, &) are not allowed."
            )
        try:
            import shlex

            argv = shlex.split(command, posix=False)
        except ValueError as exc:
            raise AutomationError(f"Could not parse the command: {exc}") from exc
        if not argv:
            raise AutomationError("No command was provided.")
        exe_name = Path(argv[0]).name.lower()
        if exe_name.endswith(".exe"):
            exe_name = exe_name[:-4]
        allowed = {
            "ipconfig", "ping", "tracert", "nslookup", "hostname", "whoami",
            "systeminfo", "tasklist", "where", "find", "findstr", "more", "sort",
            "fc", "python", "pip", "node", "npm", "git", "winget", "dotnet",
            "java", "javac", "gcc", "g++", "make", "cmake", "docker", "kubectl",
            "curl", "wget",
        }
        if exe_name not in allowed:
            raise AutomationError(
                f"'{exe_name}' is not in the allowed command list."
            )
        lowered = command.lower()
        blocked = [
            "format ", "del /", "rd /s", "rmdir /s", "shutdown", "restart-computer",
            "stop-computer", "reg delete", "reg add", "net user", "net localgroup",
            "takeown", "icacls", "cipher /w", "diskpart", "bcdedit", "bootrec",
            "invoke-expression", "iex ", "downloadstring", "downloadfile",
            "start-bitstransfer", "certutil -decode", "bitsadmin",
            "wmic process call create", "remove-item -recurse", "rm -rf", "rm -r /",
        ]
        for pattern in blocked:
            if pattern in lowered:
                raise AutomationError(f"Blocked dangerous command pattern: {pattern}")
        if exe_name in {"python", "node"}:
            for arg in argv[1:]:
                if arg.lower() in {"-c", "-e", "--eval", "--exec"}:
                    raise AutomationError(
                        "Inline code execution via the shell is not allowed."
                    )
        timeout = max(1, min(60, int(timeout or 15)))
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            proc = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                timeout=timeout,
                shell=False,
                creationflags=creationflags,
            )
        except subprocess.TimeoutExpired:
            raise AutomationError(f"Command timed out after {timeout}s.")
        except FileNotFoundError:
            raise AutomationError(f"Command not found: {argv[0]}")
        output = (proc.stdout or "")
        if proc.stderr:
            output = output + ("\n" if output else "") + proc.stderr
        output = output.strip()
        if len(output) > 3000:
            output = output[:2997] + "..."
        success = proc.returncode == 0
        if output:
            message = f"Exit code {proc.returncode}.\n{output}"
        else:
            message = f"Exit code {proc.returncode} (no output)."
        return ExecutionResult(success, message, "run_command", detail=f"argv={argv}")

    _BROWSER_PROCESSES = {"chrome.exe", "msedge.exe", "firefox.exe", "brave.exe", "opera.exe"}

    def _find_browser_window(self, browser: str | None) -> int:
        wanted_proc = None
        if browser:
            name = browser.strip().lower()
            known = {
                "chrome": "chrome.exe",
                "google chrome": "chrome.exe",
                "edge": "msedge.exe",
                "microsoft edge": "msedge.exe",
                "firefox": "firefox.exe",
                "brave": "brave.exe",
                "opera": "opera.exe",
            }
            wanted_proc = known.get(name, name if name.endswith(".exe") else name + ".exe")
        for info in win.enumerate_windows():
            if not info.title.strip():
                continue
            if wanted_proc:
                try:
                    proc_name = psutil.Process(info.pid).name().lower()
                except psutil.Error:
                    proc_name = ""
                if proc_name != wanted_proc.lower():
                    continue
                return info.hwnd
            try:
                proc_name = psutil.Process(info.pid).name().lower()
            except psutil.Error:
                proc_name = ""
            if proc_name in self._BROWSER_PROCESSES:
                return info.hwnd
        return 0

    def _browser_control(self, action: str, url: str = "", browser: str = "") -> ExecutionResult:
        action = (action or "").strip().lower()
        if action not in {"open_url", "tab_next", "tab_prev", "tab_close", "tab_new", "read_page"}:
            raise AutomationError("Unsupported browser action.")
        if action == "open_url":
            url = str(url or "").strip()
            if not url:
                raise AutomationError("A URL is required.")
            if not re.match(r"^(https?|file|mailto)://", url, re.I):
                if re.match(r"^[a-z0-9][a-z0-9.-]*\.[a-z]{2,}", url, re.I):
                    url = "https://" + url
                else:
                    raise AutomationError("Only http, https, file, or mailto URLs are allowed.")
            if browser:
                resolved = self._resolver.resolve(browser)
                if resolved.kind == "exe" and Path(resolved.target).exists():
                    subprocess.Popen(
                        [resolved.target, url],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                    return ExecutionResult(True, f"Opened {url} in {resolved.display_name}.", "browser_control")
            try:
                os.startfile(url)  # type: ignore[attr-defined]
            except OSError as exc:
                raise AutomationError(f"Could not open the URL: {exc}") from exc
            return ExecutionResult(True, f"Opened {url}.", "browser_control")

        hwnd = self._find_browser_window(browser or None)
        if not hwnd:
            raise AutomationError("No browser window was found. Open a browser first.")
        win.focus_window(hwnd)
        time.sleep(0.2)
        from pynput.keyboard import Controller, Key

        controller = Controller()
        if action == "tab_next":
            with controller.pressed(Key.ctrl):
                controller.press(Key.tab)
                controller.release(Key.tab)
            return ExecutionResult(True, "Switched to the next tab.", "browser_control")
        if action == "tab_prev":
            with controller.pressed(Key.ctrl, Key.shift):
                controller.press(Key.tab)
                controller.release(Key.tab)
            return ExecutionResult(True, "Switched to the previous tab.", "browser_control")
        if action == "tab_close":
            with controller.pressed(Key.ctrl):
                controller.press("w")
                controller.release("w")
            return ExecutionResult(True, "Closed the current tab.", "browser_control")
        if action == "tab_new":
            with controller.pressed(Key.ctrl):
                controller.press("t")
                controller.release("t")
            return ExecutionResult(True, "Opened a new tab.", "browser_control")

        # read_page
        text = ""
        try:
            import comtypes
            import uiautomation as auto

            comtypes.CoInitialize()
            try:
                root = auto.ControlFromHandle(hwnd)
                if root is not None:
                    doc = root.Control(searchDepth=8, ControlType=auto.ControlType.DocumentControl)
                    if doc is not None and doc.Exists(0.5):
                        try:
                            pattern = doc.GetValuePattern()
                            if pattern is not None:
                                text = str(pattern.Value or "")
                        except Exception:
                            text = ""
                        if not text:
                            try:
                                text = str(doc.Name or "")
                            except Exception:
                                text = ""
            finally:
                comtypes.CoUninitialize()
        except Exception:
            text = ""
        if not text.strip():
            try:
                rect = win.get_window_rect(hwnd)
                from PIL import Image

                full = self._grab_screen_image()
                cropped = full.crop((rect[0], rect[1], rect[2], rect[3]))
                from .vision import ocr_image

                text = ocr_image(cropped)
            except Exception as exc:
                raise AutomationError(f"Could not read the page: {exc}") from exc
        text = text.strip()
        if not text:
            raise AutomationError("No readable text was found on the page.")
        if len(text) > 4000:
            text = text[:3997] + "..."
        return ExecutionResult(True, f"Page text:\n{text}", "browser_control")

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._resolver = AppResolver()
        self._gui_ready = False
        self._scheduler = None
        self._last_plan_steps: list[str] = []

    def set_scheduler(self, scheduler) -> None:
        self._scheduler = scheduler

    def set_last_plan_steps(self, steps: list[str]) -> None:
        self._last_plan_steps = list(steps or [])

    def _memory(self, action: str, alias: str = "", target: str = "",
                key: str = "", value: str = "") -> ExecutionResult:
        from . import memory

        action = (action or "").strip().lower()
        if action == "set_alias":
            if not alias or not target:
                raise AutomationError("Both an alias and a target app are required.")
            memory.set_alias(alias, target)
            return ExecutionResult(True, f"Remembered '{alias}' -> {target}.", "memory")
        if action == "delete_alias":
            if not alias:
                raise AutomationError("Which alias should I forget?")
            if memory.delete_alias(alias):
                return ExecutionResult(True, f"Forgot the alias '{alias}'.", "memory")
            return ExecutionResult(False, f"No alias named '{alias}'.", "memory")
        if action == "list_aliases":
            aliases = memory.list_aliases()
            if not aliases:
                return ExecutionResult(True, "No aliases are stored.", "memory")
            lines = [f"{k} -> {v}" for k, v in sorted(aliases.items())]
            return ExecutionResult(True, "Aliases:\n" + "\n".join(lines), "memory")
        if action == "set_preference":
            if not key:
                raise AutomationError("A preference key is required.")
            memory.record_preference(key, value)
            return ExecutionResult(True, f"Saved preference {key}.", "memory")
        raise AutomationError("Unsupported memory action.")

    def _schedule(self, action: str, minutes: float = 5.0, message: str = "",
                  task_id: int | None = None, recurring: bool = False) -> ExecutionResult:
        scheduler = self._scheduler
        if scheduler is None:
            raise AutomationError("The scheduler is not running.")
        action = (action or "").strip().lower()
        if action == "list":
            tasks = scheduler.list_tasks()
            if not tasks:
                return ExecutionResult(True, "No reminders are scheduled.", "schedule")
            lines = []
            for task in tasks:
                due = time.strftime("%H:%M", time.localtime(float(task.get("due", 0))))
                lines.append(f"#{task.get('id')} at {due}: {task.get('message')}")
            return ExecutionResult(True, "Scheduled tasks:\n" + "\n".join(lines), "schedule")
        if action == "cancel":
            if task_id is None:
                raise AutomationError("Which reminder ID should I cancel?")
            if scheduler.cancel(int(task_id)):
                return ExecutionResult(True, f"Cancelled reminder #{task_id}.", "schedule")
            return ExecutionResult(False, f"No reminder #{task_id}.", "schedule")
        if action == "add":
            task = scheduler.add_recurring(minutes, message or "Reminder", None) if recurring else scheduler.add_reminder(minutes, message or "Reminder", None)
            return ExecutionResult(
                True,
                f"Reminder set for {minutes:g} minute(s): {message or 'Reminder'} (id #{task['id']}).",
                "schedule",
            )
        raise AutomationError("Unsupported schedule action.")

    def _macro(self, action: str, name: str = "", steps: str = "") -> ExecutionResult:
        from . import macros

        action = (action or "").strip().lower()
        if action == "list":
            names = macros.list_names()
            if not names:
                return ExecutionResult(True, "No macros are saved.", "macro")
            return ExecutionResult(True, "Macros: " + ", ".join(names), "macro")
        if action == "delete":
            if not name:
                raise AutomationError("Which macro should I delete?")
            if macros.delete(name):
                return ExecutionResult(True, f"Deleted macro '{name}'.", "macro")
            return ExecutionResult(False, f"No macro named '{name}'.", "macro")
        if action == "run":
            if not name:
                raise AutomationError("Which macro should I run?")
            steps_list = macros.get(name)
            if steps_list is None:
                return ExecutionResult(False, f"No macro named '{name}'.", "macro")
            # Store for the controller to execute as a plan.
            self.set_last_plan_steps(steps_list)
            return ExecutionResult(
                True,
                f"Macro '{name}' has {len(steps_list)} step(s). Run it via 'run plan' or say 'run macro {name}'.",
                "macro",
            )
        if action == "save":
            if not name:
                raise AutomationError("A macro name is required.")
            steps_list = self._last_plan_steps
            if not steps_list:
                raise AutomationError("There is no recent plan to save as a macro.")
            macros.set_macro(name, steps_list)
            return ExecutionResult(True, f"Saved macro '{name}' with {len(steps_list)} step(s).", "macro")
        raise AutomationError("Unsupported macro action.")

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
