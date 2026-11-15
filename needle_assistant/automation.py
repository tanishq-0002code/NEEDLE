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
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

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

# Virtual-screen metrics so secondary monitors (including negative coordinates)
# are captured and clicked correctly.
_SM_XVIRTUALSCREEN = 76
_SM_YVIRTUALSCREEN = 77
_SM_CXVIRTUALSCREEN = 78
_SM_CYVIRTUALSCREEN = 79
_CLICK_RETRY_SECONDS = 3.0


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
        self._scheduler = None
        self._last_plan_steps: list[str] = []
        self._last_browser_exe = ""
        self._last_browser_hwnd = 0
        self._last_click: dict[str, Any] | None = None
        self._last_grab_origin: tuple[int, int] = (0, 0)

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
                        width=call.arguments.get("width"),
                        height=call.arguments.get("height"),
                        x=call.arguments.get("x"),
                        y=call.arguments.get("y"),
                        value=call.arguments.get("value"),
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
                        str(call.arguments.get("target", "")),
                        int(call.arguments.get("amount", 5) or 5),
                    )
                if call.name == "memory":
                    return self._memory(
                        str(call.arguments.get("action", "")),
                        str(call.arguments.get("alias", "")),
                        str(call.arguments.get("target", "")),
                        str(call.arguments.get("key", "")),
                        str(call.arguments.get("value", "")),
                    )
                if call.name == "send_message":
                    return self._send_message(
                        str(call.arguments.get("recipient", "")),
                        str(call.arguments.get("text", "")),
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
                if call.name == "shortcut":
                    return self._shortcut(
                        str(call.arguments.get("action", "")),
                        str(call.arguments.get("trigger", "")),
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
                        str(call.arguments.get("target", "")),
                        target_hwnd,
                        action=str(call.arguments.get("action", "") or ""),
                        direction=str(call.arguments.get("direction", "") or ""),
                        max_steps=self._int_arg(call.arguments.get("max_steps"), 8),
                        button=str(call.arguments.get("button", "") or ""),
                        clicks=self._int_arg(call.arguments.get("clicks"), 1),
                        x=call.arguments.get("x"),
                        y=call.arguments.get("y"),
                        amount=self._int_arg(call.arguments.get("amount"), 5),
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
                if call.name == "audio_control":
                    return self._audio_control(
                        str(call.arguments.get("action", "")),
                        call.arguments.get("value"),
                    )
                if call.name == "display_control":
                    return self._display_control(
                        str(call.arguments.get("action", "")),
                        call.arguments.get("value"),
                    )
                if call.name == "connectivity_control":
                    return self._connectivity_control(str(call.arguments.get("action", "")))
                if call.name == "weather_report":
                    return self._weather_report(str(call.arguments.get("city", "")))
                if call.name == "notes":
                    return self._notes(
                        str(call.arguments.get("action", "")),
                        str(call.arguments.get("title", "")),
                        str(call.arguments.get("content", "")),
                        str(call.arguments.get("query", "")),
                    )
                if call.name == "knowledge":
                    return self._knowledge(
                        str(call.arguments.get("action", "")),
                        str(call.arguments.get("fact", "")),
                        str(call.arguments.get("query", "")),
                    )
                if call.name == "file_search":
                    return self._file_search(
                        str(call.arguments.get("query", "")),
                        str(call.arguments.get("path", "")),
                    )
                if call.name == "navigate_screen":
                    return self._navigate_screen(str(call.arguments.get("action", "")))
                if call.name == "email":
                    return self._email(
                        str(call.arguments.get("action", "")),
                        str(call.arguments.get("to", "")),
                        str(call.arguments.get("subject", "")),
                        str(call.arguments.get("body", "")),
                        str(call.arguments.get("cc", "")),
                        call.arguments.get("max_results"),
                        bool(call.arguments.get("unread_only", True)),
                        str(call.arguments.get("query", "")),
                    )
                if call.name == "manage_browser_tabs":
                    return self._manage_browser_tabs(
                        str(call.arguments.get("action", "")),
                        str(call.arguments.get("target", "")),
                    )
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

    @staticmethod
    def _int_arg(value: object, default: int) -> int:
        try:
            return int(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return default

    def _click_element(
        self,
        target: str,
        restore_hwnd: int,
        action: str = "",
        direction: str = "",
        max_steps: int = 8,
        button: str = "",
        clicks: int = 1,
        x: int | None = None,
        y: int | None = None,
        amount: int = 5,
    ) -> ExecutionResult:
        self._prepare_gui()
        self._failsafe()
        import pyautogui

        raw = (target or "").strip()
        lower = raw.lower()
        act = re.sub(r"[\s-]+", "_", (action or "").strip().lower())
        btn = (button or "").strip().lower()
        if btn not in {"left", "right", "middle"}:
            btn = ""
        count = self._int_arg(clicks, 1)
        if count < 1:
            count = 1
        count = min(count, 3)
        if act == "double_click":
            count = 2
            btn = btn or "left"
        elif act == "right_click":
            btn = "right"
        elif act == "click_again" and not btn:
            btn = "left"
        elif not btn:
            btn = "left"

        # 1. Explicit coordinates take priority.
        if x is not None and y is not None:
            try:
                px, py = int(x), int(y)
            except (TypeError, ValueError):
                raise AutomationError("The click coordinates are invalid.")
            self._check_point(px, py)
            pyautogui.moveTo(px, py, duration=0.12)
            pyautogui.click(px, py, clicks=count, button=btn)
            self._remember_click(raw or f"{px}, {py}", px, py, btn, count, restore_hwnd)
            return ExecutionResult(True, f"Clicked at {px}, {py}.", "click_element")

        # 2. Replay the previous successful target.
        if act == "click_again":
            return self._click_last_target(button=btn, clicks=count)

        # 3. Accept legacy free-text targets like "right click Save" or
        #    "move cursor to center" when the model does not fill in action.
        if act in {"", "click"}:
            match = re.match(r"^(?:please\s+)?right[\s-]click\b", lower)
            if match:
                act, btn = "right_click", "right"
                raw = re.sub(
                    r"(?i)^(?:please\s+)?right[\s-]click(?:\s+(?:on|at))?\s*", "", raw
                ).strip()
            else:
                match = re.match(r"^(?:please\s+)?double[\s-]click\b", lower)
                if match:
                    act, count = "double_click", 2
                    raw = re.sub(
                        r"(?i)^(?:please\s+)?double[\s-]click(?:\s+(?:on|at))?\s*", "", raw
                    ).strip()
                else:
                    match = re.match(r"^(?:please\s+)?middle[\s-]click\b", lower)
                    if match:
                        btn = "middle"
                        raw = re.sub(
                            r"(?i)^(?:please\s+)?middle[\s-]click(?:\s+(?:on|at))?\s*", "", raw
                        ).strip()
                    else:
                        match = re.match(
                            r"^(?:please\s+)?(?:move\s+(?:the\s+)?(?:cursor|mouse)|hover\b)",
                            lower,
                        )
                        if match:
                            act = "move"
                            raw = re.sub(
                                r"(?i)^(?:please\s+)?(?:move\s+(?:the\s+)?(?:cursor|mouse)"
                                r"\s*(?:to\s+)?|hover\s+(?:over\s+)?)",
                                "",
                                raw,
                            ).strip()
                        else:
                            match = re.match(
                                r"^(?:please\s+)?scroll\s+(up|down)(?:\s+(\d+))?\s*$",
                                lower,
                            )
                            if match:
                                act = f"scroll_{match.group(1).lower()}"
                                amount = int(match.group(2) or amount)
                                raw = ""
                            else:
                                raw = re.sub(
                                    r"(?i)^(?:please\s+)?(?:click|tap)(?:\s+(?:on|at))?\s*",
                                    "",
                                    raw,
                                ).strip()

        # 4. Plain scrolling.
        if act in {"scroll_up", "scroll_down"}:
            steps = max(1, min(50, self._int_arg(amount, 5)))
            wheel = steps * 120
            if act == "scroll_up":
                wheel = -wheel
            anchor_x, anchor_y = self._focus_anchor_point(restore_hwnd)
            pyautogui.moveTo(anchor_x, anchor_y, duration=0.1)
            pyautogui.scroll(wheel)
            label = "up" if act == "scroll_up" else "down"
            return ExecutionResult(
                True, f"Scrolled {label} {steps} steps.", "click_element"
            )

        # 5. Scroll until the element becomes visible, then click it.
        if act == "scroll_to":
            if not raw:
                raise AutomationError("Tell me which element to scroll to.")
            return self._scroll_to_element(
                raw,
                restore_hwnd,
                direction=direction or "down",
                max_steps=max_steps,
                button=btn,
                clicks=count,
            )

        # 6. Explicit coordinates embedded in the target text.
        coordinate = re.fullmatch(
            r"\s*(?:at\s+|coordinates?\s+)?(-?\d{1,6})\s*[, ]\s*(-?\d{1,6})\s*",
            raw,
            flags=re.I,
        )
        if coordinate:
            px, py = int(coordinate.group(1)), int(coordinate.group(2))
            self._check_point(px, py)
            pyautogui.moveTo(px, py, duration=0.12)
            if act != "move":
                pyautogui.click(px, py, clicks=count, button=btn)
                self._remember_click(raw, px, py, btn, count, restore_hwnd)
                prefix = self._click_verb(btn, count)
                return ExecutionResult(True, f"{prefix} at {px}, {py}.", "click_element")
            return ExecutionResult(True, f"Moved cursor to {px}, {py}.", "click_element")

        # 7. Screen aliases (center, top left, ...).
        semantic = raw.lower().strip().strip(".")
        aliases = self._screen_aliases()
        if semantic in aliases:
            px, py = aliases[semantic]
            pyautogui.moveTo(px, py, duration=0.2 if act == "move" else 0.12)
            if act == "move":
                return ExecutionResult(True, f"Moved cursor to {semantic}.", "click_element")
            pyautogui.click(px, py, clicks=count, button=btn)
            self._remember_click(raw, px, py, btn, count, restore_hwnd)
            return ExecutionResult(
                True, f"{self._click_verb(btn, count)} the {semantic} of the screen.",
                "click_element",
            )

        # 8. Empty/"it" target: click where the cursor already is.
        if not raw or semantic in {"here", "this", "it"}:
            pos = pyautogui.position()
            if act == "move":
                return ExecutionResult(
                    True, f"The cursor is already at {pos.x}, {pos.y}.", "click_element"
                )
            pyautogui.click(pos.x, pos.y, clicks=count, button=btn)
            self._remember_click(raw or "current position", pos.x, pos.y, btn, count, restore_hwnd)
            return ExecutionResult(
                True,
                f"{self._click_verb(btn, count)} at the current position ({pos.x}, {pos.y}).",
                "click_element",
            )

        # 9. Locate by UI Automation, then OCR, with a short retry window so
        #    windows and menus that are still rendering get a chance to appear.
        px, py, how = self._locate_target(
            raw, restore_hwnd, button=btn, clicks=count, move_only=(act == "move")
        )
        if act == "move":
            return ExecutionResult(True, f"Moved cursor to {raw} ({how}).", "click_element")
        where = f" at {px}, {py}" if px is not None else ""
        return ExecutionResult(
            True, f"{self._click_verb(btn, count)} {raw}{where} ({how}).", "click_element"
        )

    @staticmethod
    def _click_verb(button: str, clicks: int) -> str:
        if button == "right":
            return "Right-clicked"
        if button == "middle":
            return "Middle-clicked"
        return "Double-clicked" if clicks == 2 else "Clicked"

    def _remember_click(
        self, target: str, x: int, y: int, button: str, clicks: int, hwnd: int
    ) -> None:
        self._last_click = {
            "target": target or "",
            "x": int(x),
            "y": int(y),
            "button": button or "left",
            "clicks": max(1, min(3, int(clicks or 1))),
            "hwnd": int(hwnd or 0),
            "at": time.time(),
        }

    def _click_last_target(self, button: str = "", clicks: int = 0) -> ExecutionResult:
        import pyautogui

        last = self._last_click
        if not last:
            raise AutomationError(
                "I don't have a previous target yet. Tell me what to click first."
            )
        target = str(last.get("target") or "")
        btn = button or str(last.get("button") or "left")
        count = max(1, min(3, self._int_arg(clicks or last.get("clicks"), 1)))
        hwnd = self._int_arg(last.get("hwnd"), 0)
        px = self._int_arg(last.get("x"), 0)
        py = self._int_arg(last.get("y"), 0)
        fg = win.get_foreground_window()
        same_app = True
        if hwnd and fg:
            try:
                same_app = win.get_window_pid(fg) == win.get_window_pid(hwnd)
            except Exception:
                same_app = fg == hwnd
        if same_app and self._point_on_screen(px, py):
            pyautogui.moveTo(px, py, duration=0.12)
            pyautogui.click(px, py, clicks=count, button=btn)
            self._remember_click(target, px, py, btn, count, hwnd or fg)
            label = target or "the last element"
            return ExecutionResult(
                True, f"Clicked {label} again at {px}, {py}.", "click_element"
            )
        if not target:
            raise AutomationError(
                "The previous target is no longer on screen. Tell me what to click."
            )
        nx, ny, how = self._locate_target(
            target, hwnd or fg, button=btn, clicks=count
        )
        where = f" at {nx}, {ny}" if nx is not None else ""
        return ExecutionResult(
            True, f"{self._click_verb(btn, count)} {target} again{where} ({how}).",
            "click_element",
        )

    def _scroll_to_element(
        self,
        target: str,
        hwnd: int,
        direction: str = "down",
        max_steps: int = 8,
        button: str = "left",
        clicks: int = 1,
    ) -> ExecutionResult:
        import pyautogui

        direction = "up" if str(direction or "").lower().startswith("up") else "down"
        try:
            steps = max(1, min(15, int(max_steps or 8)))
        except (TypeError, ValueError):
            steps = 8
        count = max(1, min(3, int(clicks or 1)))
        anchor_x, anchor_y = self._focus_anchor_point(hwnd)
        for attempt in range(steps + 1):
            located = self._ocr_locate(target, hwnd)
            if located is None:
                point = None
                try:
                    point = self._uia_point(target, hwnd)
                except AutomationError:
                    point = None
                if point is not None:
                    located = (point[0], point[1], "uia")
            if located is not None:
                px, py, how = located
                pyautogui.moveTo(px, py, duration=0.12)
                pyautogui.click(px, py, clicks=count, button=button)
                self._remember_click(target, px, py, button, count, hwnd)
                return ExecutionResult(
                    True,
                    f"Found and clicked {target} at {px}, {py} after scrolling ({how}).",
                    "click_element",
                )
            if attempt >= steps:
                break
            pyautogui.moveTo(anchor_x, anchor_y, duration=0.08)
            pyautogui.scroll(-360 if direction == "down" else 360)
            time.sleep(0.35)
        raise AutomationError(f"I couldn't find '{target}' after scrolling {direction}.")

    def _focus_anchor_point(self, hwnd: int) -> tuple[int, int]:
        if hwnd and win.is_window(hwnd):
            try:
                left, top, right, bottom = win.get_window_rect(hwnd)
                if right - left >= 80 and bottom - top >= 80:
                    return (left + right) // 2, (top + bottom) // 2
            except Exception:
                pass
        origin_x, origin_y, width, height = self._virtual_screen_rect()
        return origin_x + width // 2, origin_y + height // 2

    def _locate_target(
        self,
        target: str,
        hwnd: int,
        button: str = "left",
        clicks: int = 1,
        move_only: bool = False,
    ) -> tuple[int | None, int | None, str]:
        """Find a target with a short retry window, UIA first then OCR."""
        import pyautogui

        deadline = time.monotonic() + _CLICK_RETRY_SECONDS
        last_error = ""
        uia_unavailable = False
        while True:
            if not uia_unavailable:
                try:
                    if move_only:
                        point = self._uia_point(target, hwnd)
                        if point is not None:
                            pyautogui.moveTo(point[0], point[1], duration=0.2)
                            return point[0], point[1], "uia"
                    else:
                        found, px, py = self._uia_click(
                            target, hwnd, button=button, clicks=clicks
                        )
                        if found:
                            if px is not None and py is not None:
                                self._remember_click(target, px, py, button, clicks, hwnd)
                            return px, py, "uia"
                except AutomationError as exc:
                    last_error = str(exc)
                    if "unavailable" in last_error.lower():
                        uia_unavailable = True
            located = self._ocr_locate(target, hwnd)
            if located is not None:
                px, py, how = located
                pyautogui.moveTo(px, py, duration=0.12)
                if move_only:
                    return px, py, how
                pyautogui.click(px, py, clicks=clicks, button=button)
                self._remember_click(target, px, py, button, clicks, hwnd)
                return px, py, how
            if time.monotonic() >= deadline:
                break
            time.sleep(0.25)
        detail = f" ({last_error})" if last_error else ""
        raise AutomationError(f"I couldn't find '{target}' on the screen{detail}.")

    @contextmanager
    def _uia_session(self):
        try:
            import comtypes
            import uiautomation as auto
        except Exception as exc:
            raise AutomationError("UI element search is unavailable on this system.") from exc
        comtypes.CoInitialize()
        try:
            auto.SetGlobalSearchTimeout(1.0)
            yield auto
        finally:
            try:
                comtypes.CoUninitialize()
            except Exception:
                pass

    def _uia_candidate_handles(self, hwnd: int) -> list[int]:
        """Target window plus its popups/siblings and the active browser."""
        handles: list[int] = []

        def add(value: int) -> None:
            if value and value not in handles:
                handles.append(int(value))

        add(hwnd)
        try:
            add(win.get_foreground_window())
        except Exception:
            pass
        try:
            _exe, browser_hwnd = self._active_browser()
            add(browser_hwnd)
        except Exception:
            pass
        if hwnd:
            try:
                pid = win.get_window_pid(hwnd)
                siblings: list[tuple[int, int]] = []
                for candidate in win.visible_windows_for_pid(pid):
                    if candidate in handles:
                        continue
                    left, top, right, bottom = win.get_window_rect(candidate)
                    area = max(0, right - left) * max(0, bottom - top)
                    siblings.append((area, candidate))
                # Small popups/menus/dialogs first, capped for responsiveness.
                for _area, candidate in sorted(siblings)[:6]:
                    add(candidate)
            except Exception:
                pass
        return handles

    def _find_uia_in_windows(self, auto, target: str, hwnd: int):
        for candidate_hwnd in self._uia_candidate_handles(hwnd):
            try:
                root = auto.ControlFromHandle(candidate_hwnd)
            except Exception:
                root = None
            if root is None:
                continue
            is_browser = self._process_name_for_hwnd(candidate_hwnd) in self._BROWSER_PROCESSES
            found = self._find_uia_control(root, target, interactive_only=is_browser)
            if found is not None:
                return found
        return None

    def _uia_click(
        self, target: str, hwnd: int, button: str = "left", clicks: int = 1
    ) -> tuple[bool, int | None, int | None]:
        with self._uia_session() as auto:
            control = self._find_uia_in_windows(auto, target, hwnd)
            if control is None:
                return False, None, None
            point = self._uia_clickable_point(control)
            self._activate_uia_control_for(control, button=button, clicks=clicks)
            return True, (point[0] if point else None), (point[1] if point else None)

    def _uia_point(self, target: str, hwnd: int) -> tuple[int, int] | None:
        with self._uia_session() as auto:
            control = self._find_uia_in_windows(auto, target, hwnd)
            if control is None:
                return None
            return self._uia_clickable_point(control)

    @staticmethod
    def _uia_clickable_point(control) -> tuple[int, int] | None:
        try:
            point = control.GetClickablePoint()
            return int(point.x), int(point.y)
        except Exception:
            return None

    def _activate_uia_control_for(
        self, control, button: str = "left", clicks: int = 1
    ) -> None:
        try:
            control.SetFocus()
        except Exception:
            pass
        if button == "left" and clicks == 1 and self._activate_uia_control(control):
            time.sleep(0.08)
            return
        point = self._uia_clickable_point(control)
        if point is None:
            raise AutomationError(
                "I found the control but couldn't determine where to click it."
            )
        self._mouse_click(point[0], point[1], button=button, clicks=clicks)
        time.sleep(0.08)

    def _ocr_locate(self, target: str, hwnd: int = 0) -> tuple[int, int, str] | None:
        try:
            from .vision import ocr_words
        except Exception:
            return None
        image, origin_x, origin_y = self._grab_screen_with_origin()
        crop = image
        crop_x = crop_y = 0
        title_bar_y: int | None = None
        window_center: tuple[int, int] | None = None
        if hwnd and win.is_window(hwnd):
            try:
                left, top, right, bottom = win.get_window_rect(hwnd)
                if right - left >= 140 and bottom - top >= 100:
                    c_left = max(0, left - origin_x)
                    c_top = max(0, top - origin_y)
                    c_right = min(image.width, right - origin_x)
                    c_bottom = min(image.height, bottom - origin_y)
                    if c_right - c_left >= 100 and c_bottom - c_top >= 80:
                        crop = image.crop((c_left, c_top, c_right, c_bottom))
                        crop_x, crop_y = c_left, c_top
                        title_bar_y = max(0, (top - origin_y) - crop_y + 34)
                        window_center = (crop.width // 2, crop.height // 2)
            except Exception:
                crop = image
                crop_x = crop_y = 0
                title_bar_y = None
                window_center = None
        try:
            words = ocr_words(crop)
        except Exception:
            return None
        found = self._best_ocr_match(
            target, words, title_bar_y=title_bar_y, window_center=window_center
        )
        if found is None:
            return None
        rel_x, rel_y, label, score = found
        return (
            origin_x + crop_x + rel_x,
            origin_y + crop_y + rel_y,
            f"ocr:{label} {score:.2f}",
        )

    @classmethod
    def _cluster_ocr_lines(
        cls, items: list[dict[str, Any]]
    ) -> list[list[dict[str, Any]]]:
        if not items:
            return []
        ordered = sorted(items, key=lambda item: (item["yc"], item["x"]))
        heights = sorted(item["h"] for item in ordered) or [12.0]
        tolerance = max(8.0, heights[len(heights) // 2] * 1.6)
        lines: list[list[dict[str, Any]]] = []
        current: list[dict[str, Any]] = []
        for item in ordered:
            if current and abs(item["yc"] - current[-1]["yc"]) > tolerance:
                lines.append(current)
                current = []
            current.append(item)
        if current:
            lines.append(current)
        for line in lines:
            line.sort(key=lambda item: item["x"])
        return lines

    @classmethod
    def _best_ocr_match(
        cls,
        target: str,
        words: list[dict[str, Any]],
        title_bar_y: int | None = None,
        window_center: tuple[int, int] | None = None,
    ) -> tuple[int, int, str, float] | None:
        """Line-aware, ranked OCR match returning the best click point."""
        normalized_target = _norm(target)
        tokens = [token for token in normalized_target.split() if token]
        if not tokens:
            return None
        items: list[dict[str, Any]] = []
        for word in words:
            text = _norm(str(word.get("text", "")))
            if not text:
                continue
            try:
                left = float(word.get("x", 0))
                top = float(word.get("y", 0))
                width = float(word.get("width", 0))
                height = float(word.get("height", 0))
            except (TypeError, ValueError):
                continue
            if width <= 0 or height <= 0:
                continue
            items.append(
                {
                    "text": text,
                    "x": left,
                    "y": top,
                    "w": width,
                    "h": height,
                    "xc": left + width / 2,
                    "yc": top + height / 2,
                }
            )
        if not items:
            return None
        max_distance = 1.0
        if window_center is not None:
            max_distance = max(
                1.0,
                abs(window_center[0]) + abs(window_center[1]) + 1.0,
            )
        best: tuple[float, int, int, str] | None = None
        for line in cls._cluster_ocr_lines(items):
            line_words = [item["text"] for item in line]
            line_text = " ".join(line_words).strip()
            if not line_text:
                continue
            score = 0.0
            if normalized_target == line_text:
                score = 1.0
            elif normalized_target in line_text:
                coverage = len(normalized_target) / max(1, len(line_text))
                score = 0.70 + 0.30 * min(1.0, coverage * 2.0)
            else:
                present = 0
                for token in tokens:
                    if token in line_words:
                        present += 1
                        continue
                    if len(token) >= 4 and any(
                        len(candidate) >= 4
                        and difflib.SequenceMatcher(None, token, candidate).ratio() >= 0.8
                        for candidate in line_words
                    ):
                        present += 1
                if present == len(tokens):
                    score = 0.82
                elif present:
                    score = 0.45 + 0.30 * (present / len(tokens))
                else:
                    ratio = difflib.SequenceMatcher(
                        None, normalized_target, line_text
                    ).ratio()
                    if ratio >= 0.72:
                        score = ratio * 0.70
                if len(tokens) == 1:
                    for item in line:
                        if item["text"] == tokens[0]:
                            score = max(score, 0.95)
                            break
            if score <= 0:
                continue
            left = min(item["x"] for item in line)
            top = min(item["y"] for item in line)
            right = max(item["x"] + item["w"] for item in line)
            bottom = max(item["y"] + item["h"] for item in line)
            width, height = right - left, bottom - top
            center_x = int((left + right) / 2)
            center_y = int((top + bottom) / 2)
            # Paragraph-like or tiny OCR boxes are unlikely to be controls.
            if width > 700 or height > 90:
                score *= 0.82
            if height < 8:
                score *= 0.90
            # Never prefer the window title bar when a real control also matches.
            if title_bar_y is not None and center_y < title_bar_y:
                score *= 0.70
            if window_center is not None:
                distance = abs(center_x - window_center[0]) + abs(center_y - window_center[1])
                score += 0.03 * (1.0 - min(1.0, distance / max_distance))
            if best is None or score > best[0]:
                best = (score, center_x, center_y, line_text[:60])
        if best is None or best[0] < 0.55:
            return None
        return best[1], best[2], best[3], best[0]

    def _virtual_screen_rect(self) -> tuple[int, int, int, int]:
        """Return (x, y, width, height) covering every monitor."""
        try:
            x = int(user32.GetSystemMetrics(_SM_XVIRTUALSCREEN))
            y = int(user32.GetSystemMetrics(_SM_YVIRTUALSCREEN))
            width = int(user32.GetSystemMetrics(_SM_CXVIRTUALSCREEN))
            height = int(user32.GetSystemMetrics(_SM_CYVIRTUALSCREEN))
        except Exception:
            x = y = 0
            width = height = 0
        if width <= 0 or height <= 0:
            width, height = self._screen_size()
            x = y = 0
        return x, y, width, height

    def _screen_aliases(self) -> dict[str, tuple[int, int]]:
        origin_x, origin_y, width, height = self._virtual_screen_rect()

        def point(fx: float, fy: float) -> tuple[int, int]:
            return origin_x + int(width * fx), origin_y + int(height * fy)

        return {
            "center": point(0.5, 0.5),
            "centre": point(0.5, 0.5),
            "middle": point(0.5, 0.5),
            "top left": point(1 / 6, 1 / 6),
            "top-left": point(1 / 6, 1 / 6),
            "top right": point(5 / 6, 1 / 6),
            "top-right": point(5 / 6, 1 / 6),
            "upper-left": point(1 / 6, 1 / 6),
            "upper-right": point(5 / 6, 1 / 6),
            "bottom left": point(1 / 6, 5 / 6),
            "bottom-left": point(1 / 6, 5 / 6),
            "bottom right": point(5 / 6, 5 / 6),
            "bottom-right": point(5 / 6, 5 / 6),
            "lower-left": point(1 / 6, 5 / 6),
            "lower-right": point(5 / 6, 5 / 6),
            "top center": point(0.5, 1 / 6),
            "bottom center": point(0.5, 5 / 6),
            "left center": point(1 / 6, 0.5),
            "right center": point(5 / 6, 0.5),
            "left side": point(1 / 6, 0.5),
            "right side": point(5 / 6, 0.5),
            "top": point(0.5, 1 / 6),
            "bottom": point(0.5, 5 / 6),
        }

    @staticmethod
    def _screen_size() -> tuple[int, int]:
        try:
            import pyautogui

            size = pyautogui.size()
            return int(size.width), int(size.height)
        except Exception:
            return int(user32.GetSystemMetrics(0)), int(user32.GetSystemMetrics(1))

    def _point_on_screen(self, x: int, y: int) -> bool:
        origin_x, origin_y, width, height = self._virtual_screen_rect()
        return (
            origin_x <= x < origin_x + width
            and origin_y <= y < origin_y + height
        )

    def _check_point(self, x: int, y: int) -> None:
        if not self._point_on_screen(x, y):
            raise AutomationError(f"The point {x}, {y} is outside the screen.")

    def _mouse_click(self, x: int, y: int, button: str = "left", clicks: int = 1) -> None:
        self._failsafe()
        import pyautogui

        pyautogui.moveTo(x, y, duration=0.12)
        pyautogui.click(x, y, clicks=clicks, button=button)

    def _click_uia(self, target: str, hwnd: int, button: str = "left", clicks: int = 1) -> None:
        found, _px, _py = self._uia_click(target, hwnd, button=button, clicks=clicks)
        if not found:
            raise AutomationError(
                f"I couldn't find a visible control named \"{target}\" in the foreground window."
            )

    @staticmethod
    def _control_name_score(name: str, wanted: str, tokens: list[str]) -> float:
        if not name or not wanted:
            return 0.0
        if name == wanted:
            return 1.0
        if wanted in name:
            return 0.92
        if name in wanted and len(name) >= 4:
            return 0.86
        if not tokens:
            return 0.0
        present = sum(1 for token in tokens if token in name)
        if present == len(tokens):
            token_score = 0.82
        elif present:
            token_score = 0.55 * (present / len(tokens))
        else:
            token_score = 0.0
        ratio = difflib.SequenceMatcher(None, wanted, name).ratio()
        return max(token_score, ratio * 0.8)

    @classmethod
    def _find_uia_control(
        cls,
        root,
        target: str,
        max_depth: int = 30,
        max_nodes: int = 20000,
        interactive_only: bool = False,
    ):
        try:
            import uiautomation as auto
        except Exception:
            return None
        wanted = _norm(target)
        tokens = [token for token in wanted.split() if token]
        if not wanted:
            return None
        interactive_types = {
            "HyperlinkControl", "ButtonControl", "MenuItemControl", "TabItemControl",
            "ListItemControl", "CheckBoxControl", "RadioButtonControl",
            "SplitButtonControl", "TreeItemControl", "DataItemControl",
        }
        best = None
        best_score = 0.0
        seen = 0
        try:
            for control, _depth in auto.WalkControl(root, includeTop=True, maxDepth=max_depth):
                seen += 1
                if seen > max_nodes:
                    break
                try:
                    name = _norm(control.Name or "")
                except Exception:
                    continue
                if not name:
                    continue
                try:
                    control_type = control.ControlTypeName or ""
                except Exception:
                    control_type = ""
                if interactive_only and control_type and control_type not in interactive_types:
                    continue
                score = cls._control_name_score(name, wanted, tokens)
                if score < 0.55:
                    continue
                if control_type == "HyperlinkControl":
                    score += 0.10
                elif control_type in {
                    "ButtonControl", "MenuItemControl", "TabItemControl",
                    "ListItemControl", "CheckBoxControl", "RadioButtonControl",
                }:
                    score += 0.05
                elif control_type not in {"TextControl"}:
                    score -= 0.02
                try:
                    if control.IsOffscreen:
                        score -= 0.60
                except Exception:
                    pass
                try:
                    if not control.IsEnabled:
                        score -= 0.60
                except Exception:
                    pass
                if score > best_score:
                    best_score = score
                    best = control
        except Exception:
            return best
        return best if best_score >= 0.55 else None

    @staticmethod
    def _activate_uia_control(control) -> bool:
        try:
            pattern = control.GetInvokePattern()
            if pattern is not None:
                pattern.Invoke()
                return True
        except Exception:
            pass
        try:
            control.Click(simulateMove=False)
            return True
        except Exception:
            pass
        try:
            point = control.GetClickablePoint()
            import pyautogui

            pyautogui.click(int(point.x), int(point.y))
            return True
        except Exception:
            return False

    @staticmethod
    def _is_browser_chrome_link(name: str, rect: tuple[int, int, int, int],
                                window_rect: tuple[int, int, int, int]) -> bool:
        left, top, right, bottom = rect
        wx0, wy0, wx1, wy1 = window_rect
        if right <= left or bottom <= top:
            return True
        if right <= wx0 or left >= wx1 or bottom <= wy0 or top >= wy1:
            return True
        # Skip the tab strip / toolbar / address bar region.
        if top < wy0 + 150:
            return True
        lowered = (name or "").strip().lower()
        if len(lowered) < 6:
            return True
        blocked = {
            "all", "web", "web mode", "ai mode", "copilot", "copilot search branding",
            "shopping", "images", "videos", "maps", "news", "more", "sign in",
            "rewards", "settings", "manage favorites now", "back to bing search",
            "explore this image", "all images", "web page related images",
            "related searches", "show all", "show more", "skip to main content",
            "accessibility feedback", "go to google home", "overview",
            "usage examples", "similar and opposite words", "pronunciation",
            "learn more", "oxford languages", "forums", "short videos", "books",
            "flights", "finance", "tools", "search tools", "more results",
            "try without personalisation",
        }
        if lowered in blocked:
            return True
        if lowered.startswith(("see results only from", "see more", "show more", "more results")):
            return True
        # Short toolbar/filter pills and tab labels are not search results.
        if (right - left) < 220 and (bottom - top) < 55:
            return True
        return False

    def _first_visible_result_link(self, hwnd: int):
        try:
            import comtypes
            import uiautomation as auto
        except Exception:
            return None
        comtypes.CoInitialize()
        try:
            auto.SetGlobalSearchTimeout(1.5)
            root = auto.ControlFromHandle(hwnd)
            if root is None:
                return None
            window_rect = win.get_window_rect(hwnd)
            preferred: list[tuple[int, int, object]] = []
            seen = 0
            for control, _depth in auto.WalkControl(root, includeTop=True, maxDepth=30):
                seen += 1
                if seen > 20000:
                    break
                try:
                    if control.ControlTypeName != "HyperlinkControl":
                        continue
                    name = str(control.Name or "").strip()
                    if not name or control.IsOffscreen:
                        continue
                    r = control.BoundingRectangle
                    rect = (int(r.left), int(r.top), int(r.right), int(r.bottom))
                except Exception:
                    continue
                if self._is_browser_chrome_link(name, rect, window_rect):
                    continue

                is_preferred = False
                skip = False
                try:
                    parent = control
                    for _ in range(8):
                        parent = parent.GetParentControl()
                        if parent is None:
                            break
                        parent_name = str(parent.Name or "").strip().lower()
                        if parent_name in {"additional results", "related searches"}:
                            skip = True
                            break
                        if "copilot search" in parent_name:
                            skip = True
                            break
                        if parent_name == "search results":
                            is_preferred = True
                except Exception:
                    pass
                if skip or not is_preferred:
                    continue
                preferred.append((rect[1], rect[0], control))
            # Only trust links that sit inside the engine's search-results
            # region. This avoids clicking AI-overview cards, tab labels, or
            # related-search suggestions when the real results are below them.
            if not preferred:
                return None
            preferred.sort(key=lambda item: (item[0], item[1]))
            return preferred[0][2]
        except Exception:
            return None
        finally:
            try:
                comtypes.CoUninitialize()
            except Exception:
                pass

    def _grab_screen_image(self):
        """Captures the full virtual desktop (all monitors) with GDI/PyQt/PIL fallbacks."""
        origin_x, origin_y, width, height = self._virtual_screen_rect()
        # Method 1: Direct Windows GDI BitBlt (works across sessions and monitors)
        try:
            from PIL import Image

            gdi32 = ctypes.windll.gdi32
            hdc_screen = user32.GetDC(0)
            if hdc_screen:
                try:
                    hdc_mem = gdi32.CreateCompatibleDC(hdc_screen)
                    if hdc_mem:
                        try:
                            hbm = gdi32.CreateCompatibleBitmap(hdc_screen, width, height)
                            if hbm:
                                try:
                                    gdi32.SelectObject(hdc_mem, hbm)
                                    gdi32.BitBlt(
                                        hdc_mem, 0, 0, width, height,
                                        hdc_screen, origin_x, origin_y, 0x00CC0020,
                                    )
                                    bi = ctypes.c_buffer(40)
                                    ctypes.c_ulong.from_buffer(bi, 0).value = 40
                                    ctypes.c_long.from_buffer(bi, 4).value = width
                                    ctypes.c_long.from_buffer(bi, 8).value = -height
                                    ctypes.c_ushort.from_buffer(bi, 12).value = 1
                                    ctypes.c_ushort.from_buffer(bi, 14).value = 32
                                    buf = ctypes.create_string_buffer(width * height * 4)
                                    gdi32.GetDIBits(hdc_mem, hbm, 0, height, buf, bi, 0)
                                    self._last_grab_origin = (origin_x, origin_y)
                                    return Image.frombuffer(
                                        "RGBA", (width, height), buf, "raw", "BGRA", 0, 1
                                    ).convert("RGB")
                                finally:
                                    gdi32.DeleteObject(hbm)
                        finally:
                            gdi32.DeleteDC(hdc_mem)
                finally:
                    user32.ReleaseDC(0, hdc_screen)
        except Exception:
            pass

        # Method 2: PyQt6 screen capture (primary screen only)
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
                        self._last_grab_origin = (0, 0)
                        return Image.open(io.BytesIO(data))
        except Exception:
            pass

        # Method 3: Pillow ImageGrab fallback
        from PIL import ImageGrab

        self._last_grab_origin = (origin_x, origin_y)
        try:
            return ImageGrab.grab(all_screens=True)
        except Exception:
            self._last_grab_origin = (0, 0)
            return ImageGrab.grab(all_screens=False)

    def _grab_screen_with_origin(self):
        """Capture the virtual desktop and report where its (0, 0) sits."""
        image = self._grab_screen_image()
        origin_x, origin_y = self._last_grab_origin
        return image, int(origin_x), int(origin_y)

    def _screenshot(self) -> ExecutionResult:
        # Capture to a temporary file only, so screenshots are never left
        # sitting in Pictures/Needle. Telegram uploads the file and deletes it;
        # any leftover temp file is swept up shortly afterwards.
        import os as _os
        import tempfile as _tempfile

        image = self._grab_screen_image()
        fd, temp_name = _tempfile.mkstemp(suffix=".png", prefix="Needle_Screenshot_")
        _os.close(fd)
        path = Path(temp_name)
        image.save(path, "PNG")

        # Sweep up the temp file if nobody (e.g. Telegram) deletes it first.
        def _cleanup() -> None:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass

        timer = threading.Timer(120.0, _cleanup)
        timer.daemon = True
        timer.start()

        fg = win.get_foreground_window()
        title = win.get_window_title(fg) if fg else ""
        msg = "Captured a screenshot (not saved to disk)."
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

    def _window_manage(self, title: str, action: str, width=None, height=None, x=None, y=None, value=None) -> ExecutionResult:
        action = (action or "").strip().lower()
        import pygetwindow as gw

        if action == "list":
            wins = [w.title for w in gw.getAllWindows() if w.title.strip()]
            return ExecutionResult(True, "\n".join(wins) if wins else "No windows.", "window_manage")

        if action == "minimize_all":
            for w in gw.getAllWindows():
                try:
                    w.minimize()
                except Exception:
                    pass
            return ExecutionResult(True, "Minimized all windows.", "window_manage")

        if action == "restore_all":
            for w in gw.getAllWindows():
                try:
                    w.restore()
                except Exception:
                    pass
            return ExecutionResult(True, "Restored all windows.", "window_manage")

        if action in {"snap_left", "snap_right"}:
            w = gw.getActiveWindow()
            if w is None:
                raise AutomationError("No active window.")
            try:
                w.restore()
                time.sleep(0.1)
                import pyautogui
                pyautogui.hotkey("win", "left" if action == "snap_left" else "right")
            except Exception as exc:
                raise AutomationError(f"Snap failed: {exc}") from exc
            return ExecutionResult(True, f"Snapped window {'left' if action == 'snap_left' else 'right'}.", "window_manage")

        title = self._resolve_alias(title)
        matches = self._windows_by_title(title)
        if not matches:
            raise AutomationError(f"I couldn't find a window titled \"{title}\".")
        if len(matches) > 1:
            titles = ", ".join(info.title for info in matches[:4])
            raise AutomationError(f"Several windows match \"{title}\": {titles}.")
        info = matches[0]
        hwnd = info.hwnd

        if action == "focus":
            win.focus_window(hwnd)
            time.sleep(0.18)
            return ExecutionResult(True, f"Focused {info.title}.", "window_manage")
        if action == "close":
            try:
                win.show_window(hwnd, 0)  # SW_HIDE? use close via WM_CLOSE
            except Exception:
                pass
            import ctypes
            ctypes.windll.user32.PostMessageW(hwnd, 0x0010, 0, 0)  # WM_CLOSE
            time.sleep(0.2)
            return ExecutionResult(True, f"Closed {info.title}.", "window_manage")
        if action in {"minimize", "maximize", "restore"}:
            commands = {"minimize": SW_MINIMIZE, "maximize": SW_MAXIMIZE, "restore": win.SW_RESTORE}
            win.show_window(hwnd, commands[action])
            time.sleep(0.18)
            labels = {"minimize": "Minimized", "maximize": "Maximized", "restore": "Restored"}
            return ExecutionResult(True, f"{labels[action]} {info.title}.", "window_manage")
        if action == "resize":
            rect = win.get_window_rect(hwnd)
            try:
                new_w = int(width) if width is not None else (rect[2] - rect[0])
                new_h = int(height) if height is not None else (rect[3] - rect[1])
            except (TypeError, ValueError):
                raise AutomationError("width and height are required for resize.")
            import ctypes
            ctypes.windll.user32.MoveWindow(hwnd, rect[0], rect[1], new_w, new_h, True)
            time.sleep(0.18)
            return ExecutionResult(True, f"Resized {info.title}.", "window_manage")
        if action == "move":
            rect = win.get_window_rect(hwnd)
            try:
                new_x = int(x) if x is not None else rect[0]
                new_y = int(y) if y is not None else rect[1]
            except (TypeError, ValueError):
                raise AutomationError("x and y are required for move.")
            import ctypes
            ctypes.windll.user32.MoveWindow(hwnd, new_x, new_y, rect[2]-rect[0], rect[3]-rect[1], True)
            time.sleep(0.18)
            return ExecutionResult(True, f"Moved {info.title}.", "window_manage")
        if action == "always_on_top":
            import ctypes
            HWND_TOPMOST = -1
            HWND_NOTOPMOST = -2
            SWP_NOMOVE = 0x0002
            SWP_NOSIZE = 0x0001
            ctypes.windll.user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE)
            return ExecutionResult(True, f"Pinned {info.title} on top.", "window_manage")
        if action == "unpin":
            import ctypes
            HWND_NOTOPMOST = -2
            SWP_NOMOVE = 0x0002
            SWP_NOSIZE = 0x0001
            ctypes.windll.user32.SetWindowPos(hwnd, HWND_NOTOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE)
            return ExecutionResult(True, f"Unpinned {info.title}.", "window_manage")
        raise AutomationError(f"Unsupported window action: {action}")

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
        image, origin_x, origin_y = self._grab_screen_with_origin()
        scope = scope if scope in {"foreground", "full"} else "foreground"
        title = ""
        if scope == "foreground":
            hwnd = win.get_foreground_window()
            title = win.get_window_title(hwnd) if hwnd else ""
            if hwnd:
                left, top, right, bottom = win.get_window_rect(hwnd)
                rect = (
                    left - origin_x,
                    top - origin_y,
                    right - origin_x,
                    bottom - origin_y,
                )
            else:
                rect = (0, 0, image.width, image.height)
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

    def _audio_control(self, action: str, value) -> ExecutionResult:
        action = (action or "").strip().lower()
        try:
            from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
            from comtypes import CLSCTX_ALL
            from ctypes import cast, POINTER

            speakers = AudioUtilities.GetSpeakers()
            endpoint = getattr(speakers, "EndpointVolume", None)
            if endpoint is None:
                interface = speakers.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
                endpoint = cast(interface, POINTER(IAudioEndpointVolume))
            volume = endpoint.QueryInterface(IAudioEndpointVolume)
        except Exception as exc:
            raise AutomationError(f"Audio control unavailable: {exc}") from exc

        if action == "mute":
            volume.SetMute(1, None)
            return ExecutionResult(True, "Sound muted.", "audio_control")
        if action == "unmute":
            volume.SetMute(0, None)
            return ExecutionResult(True, "Sound unmuted.", "audio_control")
        if action in {"mic_mute", "mic_unmute"}:
            try:
                mic = AudioUtilities.GetMicrophone()
                ep = getattr(mic, "EndpointVolume", None)
                if ep is None:
                    interface = mic.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
                    ep = cast(interface, POINTER(IAudioEndpointVolume))
                mv = ep.QueryInterface(IAudioEndpointVolume)
                mv.SetMute(1 if action == "mic_mute" else 0, None)
                return ExecutionResult(
                    True,
                    "Microphone muted." if action == "mic_mute" else "Microphone unmuted.",
                    "audio_control",
                )
            except Exception as exc:
                raise AutomationError(f"Microphone control failed: {exc}") from exc
        current = round(volume.GetMasterVolumeLevelScalar() * 100)
        if action == "get_volume":
            return ExecutionResult(True, f"Volume is {current}%.", "audio_control")
        if action == "volume_up":
            new = min(100, current + 10)
            volume.SetMasterVolumeLevelScalar(new / 100.0, None)
            return ExecutionResult(True, f"Volume set to {new}%.", "audio_control")
        if action == "volume_down":
            new = max(0, current - 10)
            volume.SetMasterVolumeLevelScalar(new / 100.0, None)
            return ExecutionResult(True, f"Volume set to {new}%.", "audio_control")
        if action == "set_volume":
            try:
                lvl = max(0, min(100, int(value)))
            except (TypeError, ValueError):
                raise AutomationError("A volume level (0-100) is required.")
            volume.SetMasterVolumeLevelScalar(lvl / 100.0, None)
            return ExecutionResult(True, f"Volume set to {lvl}%.", "audio_control")
        raise AutomationError(f"Unknown audio action: {action}")

    def _display_control(self, action: str, value) -> ExecutionResult:
        action = (action or "").strip().lower()
        import screen_brightness_control as sbc

        if action == "get_brightness":
            cur = sbc.get_brightness()
            cur = cur[0] if isinstance(cur, (list, tuple)) and cur else cur
            return ExecutionResult(True, f"Brightness is {cur}%.", "display_control")
        if action in {"brightness_up", "brightness_down", "set_brightness"}:
            try:
                cur = sbc.get_brightness()
                cur = cur[0] if isinstance(cur, (list, tuple)) and cur else cur
                cur = int(cur)
            except Exception:
                cur = 50
            if action == "brightness_up":
                new = min(100, cur + 10)
            elif action == "brightness_down":
                new = max(0, cur - 10)
            else:
                try:
                    new = max(0, min(100, int(value)))
                except (TypeError, ValueError):
                    raise AutomationError("A brightness level (0-100) is required.")
            sbc.set_brightness(new)
            return ExecutionResult(True, f"Brightness set to {new}%.", "display_control")
        if action in {"screen_off", "screen_on"}:
            import ctypes
            if action == "screen_off":
                ctypes.windll.user32.SendMessageW(0xFFFF, 0x0112, 0xF170, 2)
                return ExecutionResult(True, "Screen turned off.", "display_control")
            ctypes.windll.user32.mouse_event(0x0001, 1, 1, 0, 0)
            return ExecutionResult(True, "Screen woken.", "display_control")
        if action in {"night_mode_on", "night_mode_off"}:
            # Toggle Windows Night Light via the quick-settings URI (best effort).
            try:
                import os
                os.startfile("ms-settings:nightlight")
            except Exception:
                pass
            state = "on" if action == "night_mode_on" else "off"
            return ExecutionResult(
                True,
                f"Opened Night light settings (turn it {state} there).",
                "display_control",
            )
        raise AutomationError(f"Unknown display action: {action}")

    def _connectivity_control(self, action: str) -> ExecutionResult:
        action = (action or "").strip().lower()
        import subprocess

        def _run(args):
            return subprocess.run(args, capture_output=True, text=True, timeout=20)

        if action == "wifi_status":
            res = _run(["netsh", "wlan", "show", "interfaces"])
            text = (res.stdout or res.stderr or "").strip()
            return ExecutionResult(True, text or "No Wi-Fi info.", "connectivity_control")
        if action == "wifi_on":
            # Enable the Wi-Fi adapter (best effort; may require admin).
            res = _run(["powershell", "-Command",
                        "Enable-NetAdapter -Name 'Wi-Fi' -Confirm:$false -ErrorAction SilentlyContinue"])
            if res.returncode != 0:
                res = _run(["netsh", "interface", "set", "interface", "Wi-Fi", "admin=enabled"])
            msg = (res.stdout or "").strip() or "Wi-Fi enable requested."
            if res.returncode != 0 and not msg:
                msg = (res.stderr or "").strip() or "Failed to enable Wi-Fi (try as admin)."
            return ExecutionResult(res.returncode == 0, msg, "connectivity_control")
        if action == "wifi_off":
            res = _run(["powershell", "-Command",
                        "Disable-NetAdapter -Name 'Wi-Fi' -Confirm:$false -ErrorAction SilentlyContinue"])
            if res.returncode != 0:
                res = _run(["netsh", "interface", "set", "interface", "Wi-Fi", "admin=disabled"])
            msg = (res.stdout or "").strip() or "Wi-Fi disable requested."
            if res.returncode != 0 and not msg:
                msg = (res.stderr or "").strip() or "Failed to disable Wi-Fi (try as admin)."
            return ExecutionResult(res.returncode == 0, msg, "connectivity_control")
        if action == "bluetooth_status":
            res = _run(["powershell", "-Command",
                        "Get-PnpDevice -Class Bluetooth | Select-Object -ExpandProperty Status"])
            text = (res.stdout or res.stderr or "").strip()
            return ExecutionResult(True, text or "No Bluetooth info.", "connectivity_control")
        if action in {"bluetooth_on", "bluetooth_off"}:
            # Toggle via the radio management PowerShell module (best effort).
            state = "On" if action == "bluetooth_on" else "Off"
            cmd = (
                "Get-PnpDevice -Class Bluetooth | ForEach-Object { "
                f"if ('{state}' -eq 'On') {{ Enable-PnpDevice -InstanceId $_.InstanceId -Confirm:$false }} "
                f"else {{ Disable-PnpDevice -InstanceId $_.InstanceId -Confirm:$false }} "
                "}"
            )
            res = _run(["powershell", "-Command", cmd])
            msg = (res.stdout or "").strip() or f"Bluetooth {state.lower()} requested."
            if res.returncode != 0 and not msg:
                msg = (res.stderr or "").strip() or f"Failed to toggle Bluetooth (try as admin)."
            return ExecutionResult(res.returncode == 0, msg, "connectivity_control")
        if action in {"airplane_on", "airplane_off"}:
            # Airplane mode is a quick-action; open the network flyout as a fallback.
            try:
                import os
                os.startfile("ms-settings:network-airplanemode")
            except Exception:
                pass
            state = "on" if action == "airplane_on" else "off"
            return ExecutionResult(True, f"Opened Airplane mode settings (turn it {state} there).",
                                  "connectivity_control")
        if action == "connected_devices":
            res = _run(["arp", "-a"])
            text = (res.stdout or "").strip()
            if not text:
                text = "No device list available."
            return ExecutionResult(True, text[:3000], "connectivity_control")
        raise AutomationError(f"Unknown connectivity action: {action}")

    def _weather_report(self, city: str) -> ExecutionResult:
        import requests

        city = (city or "").strip()
        if not city:
            raise AutomationError("A city is required.")
        try:
            resp = requests.get(f"https://wttr.in/{city}?format=3", timeout=10)
            text = (resp.text or "").strip()
            if not text or "Unknown location" in text:
                raise AutomationError(f"Could not get weather for {city}.")
            return ExecutionResult(True, text, "weather_report")
        except Exception as exc:
            raise AutomationError(f"Weather lookup failed: {exc}") from exc

    def _notes_path(self) -> Path:
        base = Path(__file__).resolve().parent.parent
        folder = base / "data"
        folder.mkdir(parents=True, exist_ok=True)
        return folder / "notes.json"

    def _load_notes(self) -> list[dict[str, Any]]:
        import json as _json
        path = self._notes_path()
        if not path.exists():
            return []
        try:
            with open(path, encoding="utf-8") as fh:
                data = _json.load(fh)
            return data if isinstance(data, list) else []
        except Exception:
            return []

    def _save_notes(self, notes: list[dict[str, Any]]) -> None:
        import json as _json
        with open(self._notes_path(), "w", encoding="utf-8") as fh:
            _json.dump(notes, fh, indent=2, ensure_ascii=False)

    def _notes(self, action: str, title: str, content: str, query: str) -> ExecutionResult:
        action = (action or "").strip().lower() or "list"
        notes = self._load_notes()
        if action == "save":
            title = (title or "Untitled").strip()[:120]
            notes = [n for n in notes if n.get("title") != title]
            notes.append({"title": title, "content": content or ""})
            self._save_notes(notes)
            return ExecutionResult(True, f"Saved note '{title}'.", "notes")
        if action == "list":
            if not notes:
                return ExecutionResult(True, "No notes saved.", "notes")
            titles = ", ".join(n.get("title", "?") for n in notes)
            return ExecutionResult(True, f"Notes: {titles}", "notes")
        if action == "read":
            needle = (query or title or "").strip().lower()
            matches = [n for n in notes if not needle or needle in str(n.get("title", "")).lower()
                       or needle in str(n.get("content", "")).lower()]
            if not matches:
                return ExecutionResult(True, "No matching note found.", "notes")
            parts = [f"{n.get('title')}: {n.get('content')}" for n in matches]
            return ExecutionResult(True, "\n".join(parts), "notes")
        if action == "delete":
            needle = (title or query or "").strip().lower()
            before = len(notes)
            notes = [n for n in notes if needle and needle not in str(n.get("title", "")).lower()]
            self._save_notes(notes)
            removed = before - len(notes)
            return ExecutionResult(True, f"Deleted {removed} note(s).", "notes")
        raise AutomationError(f"Unknown notes action: {action}")

    def _knowledge_path(self) -> Path:
        base = Path(__file__).resolve().parent.parent
        folder = base / "data"
        folder.mkdir(parents=True, exist_ok=True)
        return folder / "knowledge.json"

    def _load_knowledge(self) -> list[str]:
        import json as _json
        path = self._knowledge_path()
        if not path.exists():
            return []
        try:
            with open(path, encoding="utf-8") as fh:
                data = _json.load(fh)
            return [str(x) for x in data] if isinstance(data, list) else []
        except Exception:
            return []

    def _save_knowledge(self, facts: list[str]) -> None:
        import json as _json
        with open(self._knowledge_path(), "w", encoding="utf-8") as fh:
            _json.dump(facts, fh, indent=2, ensure_ascii=False)

    def _knowledge(self, action: str, fact: str, query: str) -> ExecutionResult:
        action = (action or "").strip().lower() or "list"
        facts = self._load_knowledge()
        if action == "store":
            fact = (fact or "").strip()
            if not fact:
                raise AutomationError("Nothing to store.")
            facts.append(fact)
            self._save_knowledge(facts)
            return ExecutionResult(True, "Noted and stored.", "knowledge")
        if action == "list":
            if not facts:
                return ExecutionResult(True, "No stored facts.", "knowledge")
            return ExecutionResult(True, "\n".join(f"- {f}" for f in facts), "knowledge")
        if action == "recall":
            needle = (query or "").strip().lower()
            matches = [f for f in facts if not needle or needle in f.lower()]
            if not matches:
                return ExecutionResult(True, "Nothing matched.", "knowledge")
            return ExecutionResult(True, "\n".join(f"- {f}" for f in matches), "knowledge")
        if action == "forget":
            needle = (query or fact or "").strip().lower()
            before = len(facts)
            facts = [f for f in facts if needle and needle not in f.lower()]
            self._save_knowledge(facts)
            return ExecutionResult(True, f"Removed {before - len(facts)} fact(s).", "knowledge")
        raise AutomationError(f"Unknown knowledge action: {action}")

    def _file_search(self, query: str, path: str) -> ExecutionResult:
        import fnmatch
        query = (query or "").strip()
        if not query:
            raise AutomationError("A search query is required.")
        root = Path(path).expanduser() if path.strip() else Path.home()
        if not root.exists():
            raise AutomationError(f"Folder not found: {root}")
        results: list[str] = []
        try:
            for p in root.rglob("*"):
                if fnmatch.fnmatch(p.name.lower(), f"*{query.lower()}*"):
                    results.append(str(p))
                    if len(results) >= 50:
                        break
        except Exception as exc:
            raise AutomationError(f"Search failed: {exc}") from exc
        if not results:
            return ExecutionResult(True, f"No files matching '{query}' found.", "file_search")
        return ExecutionResult(True, "\n".join(results[:50]), "file_search")

    def _navigate_screen(self, action: str) -> ExecutionResult:
        action = (action or "").strip().lower()
        import pyautogui
        mapping = {
            "scroll_up": lambda: pyautogui.scroll(500),
            "scroll_down": lambda: pyautogui.scroll(-500),
            "next_tab": lambda: pyautogui.hotkey("ctrl", "tab"),
            "prev_tab": lambda: pyautogui.hotkey("ctrl", "shift", "tab"),
            "switch_app": lambda: pyautogui.hotkey("alt", "tab"),
            "minimize_all": lambda: pyautogui.hotkey("win", "d"),
            "close_tab": lambda: pyautogui.hotkey("ctrl", "w"),
        }
        if action not in mapping:
            raise AutomationError(f"Unknown navigation action: {action}")
        try:
            mapping[action]()
        except Exception as exc:
            raise AutomationError(f"Navigation failed: {exc}") from exc
        labels = {
            "scroll_up": "Scrolled up", "scroll_down": "Scrolled down",
            "next_tab": "Switched to next tab", "prev_tab": "Switched to previous tab",
            "switch_app": "Switched application", "minimize_all": "Minimized all windows",
            "close_tab": "Closed the current tab",
        }
        return ExecutionResult(True, labels.get(action, "Done."), "navigate_screen")

    def _email(self, action: str, to: str = "", subject: str = "", body: str = "",
               cc: str = "", max_results=None, unread_only: bool = True, query: str = "") -> ExecutionResult:
        action = (action or "").strip().lower()
        import os, smtplib, imaplib, email
        from email.message import EmailMessage

        if action == "send":
            to = (to or "").strip()
            if not to:
                raise AutomationError("A recipient (to) is required.")
            smtp_host = os.environ.get("NEEDLE_SMTP_HOST", "")
            smtp_port = int(os.environ.get("NEEDLE_SMTP_PORT", "587"))
            smtp_user = os.environ.get("NEEDLE_SMTP_USER", "")
            smtp_pass = os.environ.get("NEEDLE_SMTP_PASS", "")
            if not (smtp_host and smtp_user and smtp_pass):
                raise AutomationError(
                    "Email is not configured. Set NEEDLE_SMTP_HOST/PORT/USER/PASS env vars."
                )
            msg = EmailMessage()
            msg["From"] = smtp_user
            msg["To"] = to
            if cc:
                msg["Cc"] = cc
            msg["Subject"] = subject or "(no subject)"
            msg.set_content(body or "")
            try:
                with smtplib.SMTP(smtp_host, smtp_port, timeout=20) as server:
                    server.starttls()
                    server.login(smtp_user, smtp_pass)
                    server.send_message(msg)
            except Exception as exc:
                raise AutomationError(f"Failed to send email: {exc}") from exc
            return ExecutionResult(True, f"Email sent to {to}.", "email")

        if action == "read":
            imap_host = os.environ.get("NEEDLE_IMAP_HOST", "")
            imap_user = os.environ.get("NEEDLE_IMAP_USER", os.environ.get("NEEDLE_SMTP_USER", ""))
            imap_pass = os.environ.get("NEEDLE_IMAP_PASS", os.environ.get("NEEDLE_SMTP_PASS", ""))
            if not (imap_host and imap_user and imap_pass):
                raise AutomationError(
                    "Email is not configured. Set NEEDLE_IMAP_HOST/USER/PASS env vars."
                )
            limit = 5
            try:
                if max_results is not None:
                    limit = max(1, min(50, int(max_results)))
            except (TypeError, ValueError):
                pass
            try:
                mail = imaplib.IMAP4_SSL(imap_host)
                mail.login(imap_user, imap_pass)
                mail.select("INBOX")
                criteria = "UNSEEN" if unread_only else "ALL"
                if query:
                    criteria = f'({criteria} SUBJECT "{query}")'
                status, data = mail.search(None, criteria)
                ids = data[0].split() if data and data[0] else []
                ids = ids[-limit:]
                lines: list[str] = []
                for mid in ids:
                    status, msg_data = mail.fetch(mid, "(RFC822)")
                    if not msg_data or not msg_data[0]:
                        continue
                    raw = msg_data[0][1]
                    parsed = email.message_from_bytes(raw)
                    subj = parsed.get("Subject", "")
                    sender = parsed.get("From", "")
                    lines.append(f"From: {sender}\nSubject: {subj}")
                mail.logout()
            except Exception as exc:
                raise AutomationError(f"Failed to read email: {exc}") from exc
            if not lines:
                return ExecutionResult(True, "No matching emails.", "email")
            return ExecutionResult(True, "\n\n".join(lines), "email")

        raise AutomationError(f"Unknown email action: {action}")

    def _manage_browser_tabs(self, action: str, target: str = "") -> ExecutionResult:
        action = (action or "").strip().lower()
        import pyautogui
        from pynput.keyboard import Controller, Key

        controller = Controller()
        if action == "new_tab":
            with controller.pressed(Key.ctrl):
                controller.press("t")
                controller.release("t")
            return ExecutionResult(True, "Opened a new tab.", "manage_browser_tabs")
        if action == "new_chat":
            with controller.pressed(Key.ctrl, Key.shift):
                controller.press("o")
                controller.release("o")
            return ExecutionResult(True, "Opened a new chat.", "manage_browser_tabs")
        if action == "detach_tab":
            try:
                import pygetwindow as gw
                active = gw.getActiveWindow()
                if active is not None:
                    left, top, width = int(active.left), int(active.top), int(active.width)
                    sx, sy = left + width // 2, top + 30
                    pyautogui.moveTo(sx, sy)
                    pyautogui.mouseDown()
                    pyautogui.moveTo(sx, sy + 190, duration=0.4)
                    pyautogui.mouseUp()
                    return ExecutionResult(True, "Detached the active tab.", "manage_browser_tabs")
            except Exception:
                pass
            with controller.pressed(Key.alt):
                controller.press("f")
                controller.release("f")
            return ExecutionResult(True, "Moved tab via Alt+F.", "manage_browser_tabs")
        if action == "merge_windows":
            wins = []
            try:
                import pygetwindow as gw
                for w in gw.getAllWindows():
                    if w.title and any(b in w.title.lower() for b in ("chrome", "edge", "firefox", "brave")):
                        wins.append(w)
            except Exception:
                wins = []
            if not wins:
                return ExecutionResult(False, "No browser windows found to merge.", "manage_browser_tabs")
            for i, w in enumerate(wins):
                try:
                    if w.isMinimized:
                        w.restore()
                        time.sleep(0.2)
                    w.activate()
                    time.sleep(0.35)
                    pyautogui.hotkey("win", "left" if i % 2 == 0 else "right")
                    time.sleep(0.35)
                except Exception:
                    continue
            return ExecutionResult(True, f"Snapped {len(wins)} windows side by side.", "manage_browser_tabs")
        raise AutomationError(f"Unknown browser tab action: {action}")

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

    def _windows_by_title(self, title: str) -> list[win.WindowInfo]:
        """Match windows for an app/window description.

        Prefers matching by executable when the name resolves to a known app,
        falling back to a case-insensitive title substring.
        """
        wanted = re.sub(r"\s+", " ", str(title or "").strip()).lower()
        if not wanted:
            return []

        # 1. Try to resolve as an app and match its windows by process.
        try:
            resolved = self._resolver.resolve(wanted)
        except Exception:
            resolved = None
        exe_names: list[str] = []
        if resolved is not None:
            if resolved.kind == "exe":
                exe_names.append(Path(resolved.target).name.lower())
            else:
                stem = Path(resolved.target).stem.lower()
                if stem:
                    exe_names.append(stem if stem.endswith(".exe") else stem + ".exe")
        # Also best-effort known mapping for common short names.
        short = {"edge": "msedge.exe", "chrome": "chrome.exe",
                 "firefox": "firefox.exe", "brave": "brave.exe", "opera": "opera.exe"}
        if wanted in short:
            exe_names.insert(0, short[wanted])

        for exe_name in dict.fromkeys(exe_names):
            pids = self._pids_for_exe(exe_name)
            if pids:
                matches = [info for info in win.enumerate_windows() if info.pid in pids]
                if matches:
                    return matches

        # 2. Title-substring fallback.
        return [info for info in win.enumerate_windows() if wanted in info.title.lower()]

    _BROWSER_EXE_NAMES = {
        "chrome": "chrome.exe",
        "google chrome": "chrome.exe",
        "edge": "msedge.exe",
        "microsoft edge": "msedge.exe",
        "firefox": "firefox.exe",
        "brave": "brave.exe",
        "opera": "opera.exe",
    }
    _BROWSER_DISPLAY_NAMES = {
        "chrome.exe": "Google Chrome",
        "msedge.exe": "Microsoft Edge",
        "firefox.exe": "Firefox",
        "brave.exe": "Brave",
        "opera.exe": "Opera",
    }

    @staticmethod
    def _process_name_for_hwnd(hwnd: int) -> str:
        if not hwnd:
            return ""
        try:
            pid = win.get_window_pid(hwnd)
            if not pid:
                return ""
            return psutil.Process(pid).name().lower()
        except (psutil.Error, OSError):
            return ""

    def _browser_exe_path(self, exe_name: str) -> str:
        name = str(exe_name or "").strip().lower()
        if not name:
            return ""
        if not name.endswith(".exe"):
            name = self._BROWSER_EXE_NAMES.get(name, name + ".exe")
        for candidate in _candidate_paths(name):
            try:
                if candidate.exists():
                    return str(candidate)
            except OSError:
                continue
        return ""

    def _active_browser(self) -> tuple[str, int]:
        """Return the browser the user is most likely using.

        The foreground window wins when it is a browser; otherwise the topmost
        visible browser window in z-order is used. This keeps commands on Edge
        when the user is in Edge even if another browser is also running.
        """
        foreground = win.get_foreground_window()
        proc_name = self._process_name_for_hwnd(foreground)
        if proc_name in self._BROWSER_PROCESSES:
            self._remember_browser(proc_name, foreground)
            return proc_name, foreground

        for info in win.enumerate_windows():
            proc_name = self._process_name_for_hwnd(info.hwnd)
            if proc_name not in self._BROWSER_PROCESSES:
                continue
            if win.is_minimized(info.hwnd):
                continue
            self._remember_browser(proc_name, info.hwnd)
            return proc_name, info.hwnd
        return self._last_browser_exe, 0

    def _remember_browser(self, exe_name: str, hwnd: int = 0) -> None:
        name = str(exe_name or "").strip().lower()
        if name:
            self._last_browser_exe = name
        if hwnd:
            self._last_browser_hwnd = hwnd

    def _find_browser_window(self, browser: str | None) -> int:
        wanted_proc = None
        if browser:
            name = browser.strip().lower()
            wanted_proc = self._BROWSER_EXE_NAMES.get(
                name, name if name.endswith(".exe") else name + ".exe"
            )
        if wanted_proc:
            for info in win.enumerate_windows():
                if self._process_name_for_hwnd(info.hwnd) != wanted_proc:
                    continue
                if win.is_minimized(info.hwnd):
                    continue
                if not info.title.strip():
                    continue
                self._remember_browser(wanted_proc, info.hwnd)
                return info.hwnd
            return 0

        proc_name, hwnd = self._active_browser()
        if hwnd:
            self._remember_browser(proc_name, hwnd)
            return hwnd

        # Last-resort: any visible browser window.
        for info in win.enumerate_windows():
            if self._process_name_for_hwnd(info.hwnd) in self._BROWSER_PROCESSES:
                return info.hwnd
        return 0

    def _browser_control(self, action: str, url: str = "", browser: str = "", target: str = "", amount: int = 5) -> ExecutionResult:
        action = (action or "").strip().lower()
        if action not in {"open_url", "tab_next", "tab_prev", "tab_close", "tab_new", "read_page", "click_first_result", "scroll_up", "scroll_down", "reload", "back", "forward", "fullscreen", "zoom_in", "zoom_out", "zoom_reset", "close_all", "status", "incognito", "new_chat", "detach_tab", "merge_windows", "list_windows"}:
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
            requested = str(browser or "").strip().lower()
            exe_name = ""
            display_name = ""
            if requested:
                exe_name = self._BROWSER_EXE_NAMES.get(
                    requested,
                    requested if requested.endswith(".exe") else requested + ".exe",
                )
                display_name = self._BROWSER_DISPLAY_NAMES.get(exe_name, requested.title())
            else:
                active_exe, active_hwnd = self._active_browser()
                if active_exe:
                    exe_name = active_exe
                    display_name = self._BROWSER_DISPLAY_NAMES.get(active_exe, active_exe)
                    self._remember_browser(active_exe, active_hwnd)
                elif self._last_browser_exe:
                    exe_name = self._last_browser_exe
                    display_name = self._BROWSER_DISPLAY_NAMES.get(exe_name, exe_name)

            exe_path = self._browser_exe_path(exe_name) if exe_name else ""
            if not exe_path and requested:
                try:
                    resolved = self._resolver.resolve(requested)
                except Exception:
                    resolved = None
                if resolved is not None and resolved.kind == "exe" and Path(resolved.target).exists():
                    exe_path = resolved.target
                    display_name = resolved.display_name
            if exe_path:
                try:
                    subprocess.Popen(
                        [exe_path, url],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                except OSError as exc:
                    raise AutomationError(f"Could not open the URL in {display_name}: {exc}") from exc
                self._remember_browser(Path(exe_path).name.lower())
                return ExecutionResult(True, f"Opened {url} in {display_name}.", "browser_control")
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
        if action == "reload":
            controller.press(Key.f5)
            controller.release(Key.f5)
            return ExecutionResult(True, "Reloaded the page.", "browser_control")
        if action == "back":
            with controller.pressed(Key.alt):
                controller.press(Key.left)
                controller.release(Key.left)
            return ExecutionResult(True, "Went back.", "browser_control")
        if action == "forward":
            with controller.pressed(Key.alt):
                controller.press(Key.right)
                controller.release(Key.right)
            return ExecutionResult(True, "Went forward.", "browser_control")
        if action == "fullscreen":
            controller.press(Key.f11)
            controller.release(Key.f11)
            return ExecutionResult(True, "Toggled fullscreen.", "browser_control")
        if action == "zoom_in":
            with controller.pressed(Key.ctrl):
                controller.press("=")
                controller.release("=")
            return ExecutionResult(True, "Zoomed in.", "browser_control")
        if action == "zoom_out":
            with controller.pressed(Key.ctrl):
                controller.press("-")
                controller.release("-")
            return ExecutionResult(True, "Zoomed out.", "browser_control")
        if action == "zoom_reset":
            with controller.pressed(Key.ctrl):
                controller.press("0")
                controller.release("0")
            return ExecutionResult(True, "Zoom reset.", "browser_control")
        if action == "close_all":
            import psutil
            exe = self._process_name_for_hwnd(hwnd) or "chrome.exe"
            for proc in psutil.process_iter(["name", "pid"]):
                try:
                    if proc.info["name"] and proc.info["name"].lower() == exe.lower():
                        proc.kill()
                except Exception:
                    pass
            return ExecutionResult(True, "Closed all browser windows.", "browser_control")
        if action == "status":
            title = win.get_window_title(hwnd)
            return ExecutionResult(True, f"Browser open: {title}", "browser_control")
        if action == "list_windows":
            import pygetwindow as gw
            wins = [w.title for w in gw.getAllWindows() if w.title and
                    any(b in w.title.lower() for b in ("chrome", "edge", "firefox", "brave"))]
            return ExecutionResult(True, "\n".join(wins) if wins else "No browser windows.", "browser_control")
        if action == "incognito":
            exe_path = self._browser_exe_path(
                self._BROWSER_EXE_NAMES.get("chrome", "chrome.exe")
            )
            if not exe_path:
                raise AutomationError("Browser executable not found.")
            subprocess.Popen([exe_path, "--incognito"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return ExecutionResult(True, "Opened an incognito window.", "browser_control")
        if action == "new_chat":
            win.focus_window(hwnd)
            time.sleep(0.2)
            with controller.pressed(Key.ctrl, Key.shift):
                controller.press("o")
                controller.release("o")
            return ExecutionResult(True, "Opened a new chat.", "browser_control")
        if action == "detach_tab":
            # Drag the active tab out of the strip.
            try:
                import pygetwindow as gw
                active = gw.getActiveWindow()
                if active is not None:
                    left, top, width = int(active.left), int(active.top), int(active.width)
                    sx, sy = left + width // 2, top + 30
                    import pyautogui
                    pyautogui.moveTo(sx, sy)
                    pyautogui.mouseDown()
                    pyautogui.moveTo(sx, sy + 190, duration=0.4)
                    pyautogui.mouseUp()
                    return ExecutionResult(True, "Detached the active tab.", "browser_control")
            except Exception:
                pass
            with controller.pressed(Key.alt):
                controller.press("f")
                controller.release("f")
            return ExecutionResult(True, "Moved tab via Alt+F.", "browser_control")
        if action == "merge_windows":
            wins = []
            try:
                import pygetwindow as gw
                for w in gw.getAllWindows():
                    if w.title and any(b in w.title.lower() for b in ("chrome", "edge", "firefox", "brave")):
                        wins.append(w)
            except Exception:
                wins = []
            if not wins:
                return ExecutionResult(False, "No browser windows found to merge.", "browser_control")
            for i, w in enumerate(wins):
                try:
                    if w.isMinimized:
                        w.restore()
                        time.sleep(0.2)
                    w.activate()
                    time.sleep(0.35)
                    import pyautogui
                    pyautogui.hotkey("win", "left" if i % 2 == 0 else "right")
                    time.sleep(0.35)
                except Exception:
                    continue
            return ExecutionResult(True, f"Snapped {len(wins)} windows side by side.", "browser_control")

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
        if action in {"scroll_up", "scroll_down"}:
            hwnd = self._find_browser_window(browser or None)
            if not hwnd:
                raise AutomationError("No browser window was found. Open a browser first.")
            win.focus_window(hwnd)
            time.sleep(0.12)
            amount_raw = max(1, min(100, int(amount)))
            try:
                import pyautogui

                rect = win.get_window_rect(hwnd)
                width = max(1, rect[2] - rect[0])
                height = max(1, rect[3] - rect[1])
                center_x = rect[0] + width // 2
                center_y = rect[1] + height // 2
                pyautogui.moveTo(center_x, center_y, duration=0.12)
                clicks = 120 * amount_raw if action == "scroll_up" else -120 * amount_raw
                pyautogui.scroll(clicks)
            except Exception as exc:
                raise AutomationError(f"Could not scroll: {exc}") from exc
            direction = "up" if action == "scroll_up" else "down"
            return ExecutionResult(True, f"Scrolled {direction} {amount_raw} units.", "browser_control")

        if action == "click_first_result":
            hwnd = self._find_browser_window(browser or None)
            if not hwnd:
                raise AutomationError("No browser window was found. Open a browser first.")
            win.focus_window(hwnd)
            time.sleep(0.35)

            import pyperclip, urllib.parse
            from pynput.keyboard import Controller, Key

            controller = Controller()

            def _read_address() -> str:
                previous_clipboard = pyperclip.paste()
                with controller.pressed(Key.ctrl):
                    controller.press("l")
                    controller.release("l")
                time.sleep(0.25)
                with controller.pressed(Key.ctrl):
                    controller.press("a")
                    controller.release("a")
                time.sleep(0.1)
                with controller.pressed(Key.ctrl):
                    controller.press("c")
                    controller.release("c")
                time.sleep(0.25)
                value = pyperclip.paste() or ""
                pyperclip.copy(previous_clipboard)
                try:
                    controller.press(Key.esc)
                    controller.release(Key.esc)
                except Exception:
                    pass
                return str(value)

            # Prefer ranking the query with a text-friendly engine. Google
            # serves a JS-only page to anonymous requests, so Bing's HTML
            # results are the reliable source of organic links.
            scrape_error = ""
            address = _read_address()
            if address and re.match(r"^https?://", address, re.I):
                parsed = urllib.parse.urlparse(address)
                query_dict = urllib.parse.parse_qs(parsed.query)
                query = ""
                for key in ("q", "query", "p", "text", "search"):
                    values = query_dict.get(key) or []
                    if values and str(values[0]).strip():
                        query = str(values[0]).strip()
                        break
                if query:
                    try:
                        import requests
                        from bs4 import BeautifulSoup

                        search_url = "https://www.bing.com/search?q=" + urllib.parse.quote(query)
                        response = requests.get(
                            search_url,
                            headers={
                                "User-Agent": (
                                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                                    "Chrome/120.0 Safari/537.36"
                                )
                            },
                            timeout=(10.0, 30.0),
                        )
                        response.raise_for_status()
                        soup = BeautifulSoup(response.text, "html.parser")
                        result = soup.select_one("li.b_algo h2 a")
                        if not result:
                            raise AutomationError("No result link was found on the search page.")
                        first_link = result.get("href")
                        if not first_link:
                            raise AutomationError("No result link was found on the search page.")
                        # Bing wraps results in a redirect URL with the destination
                        # base64-encoded in a `u=a1` query parameter. Decode it so
                        # the link opens directly instead of going through Bing.
                        from urllib.parse import urlparse as _urlparse, parse_qs as _parse_qs
                        import base64 as _base64

                        try:
                            redirect_query = _parse_qs(_urlparse(first_link).query)
                            encoded = (redirect_query.get("u") or [""])[0]
                            if encoded.startswith("a1"):
                                raw = encoded[2:]
                                padding = "=" * (-len(raw) % 4)
                                destination = _base64.b64decode(raw + padding).decode("utf-8", "ignore")
                                if destination.startswith("http"):
                                    first_link = destination
                        except Exception:
                            pass

                        target_exe = self._browser_exe_path(browser) if browser else ""
                        if not target_exe:
                            active_exe, _ = self._active_browser()
                            target_exe = self._browser_exe_path(active_exe) if active_exe else ""
                        if target_exe:
                            subprocess.Popen(
                                [target_exe, first_link],
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL,
                            )
                        else:
                            try:
                                os.startfile(first_link)
                            except AttributeError:
                                subprocess.Popen([first_link], close_fds=True)
                        return ExecutionResult(
                            True, f"Opened the first result: {first_link}", "browser_control"
                        )
                    except Exception as exc:
                        scrape_error = str(exc)

            # Fallback: click the first visible link inside the page itself.
            link = self._first_visible_result_link(hwnd)
            if link is not None:
                try:
                    link_name = str(link.Name or "").strip()
                except Exception:
                    link_name = ""
                if self._activate_uia_control(link):
                    return ExecutionResult(
                        True,
                        f"Opened the first result: {link_name or 'link'}",
                        "browser_control",
                    )

            if scrape_error:
                raise AutomationError(f"Could not open the first result: {scrape_error}")
            raise AutomationError(
                "Could not find a search query in the address bar. "
                "Search for something first, then ask again."
            )
        if action == "tab_close":
            target = str(target or "").strip()
            if target:
                # Select the named tab first, then close it.
                try:
                    import comtypes
                    import uiautomation as auto

                    comtypes.CoInitialize()
                    try:
                        hwnd = self._find_browser_window(browser or None)
                        root = auto.ControlFromHandle(hwnd) if hwnd else auto.GetRootControl()
                        if root is not None:
                            tab = root.Control(searchDepth=12, SubName=target)
                            if tab is None or not tab.Exists(0.5):
                                tab = root.Control(searchDepth=12, Name=target)
                            if (tab is None or not tab.Exists(0.5)):
                                # Fallback: enumerate tab controls and match by substring.
                                try:
                                    candidates = root.GetChildren()
                                    for cand in candidates:
                                        try:
                                            ctype = getattr(cand, "ControlTypeName", "") or ""
                                            name = (cand.Name or "").lower()
                                            if "tab" in ctype.lower() and target.lower() in name:
                                                cand.SetFocus()
                                                break
                                        except Exception:
                                            continue
                                except Exception:
                                    pass
                            elif tab is not None and tab.Exists(0.5):
                                try:
                                    tab.SetFocus()
                                except Exception:
                                    pass
                                time.sleep(0.15)
                    finally:
                        comtypes.CoUninitialize()
                except Exception:
                    pass
            with controller.pressed(Key.ctrl):
                controller.press("w")
                controller.release("w")
            if target:
                return ExecutionResult(True, f"Closed the tab matching '{target}'.", "browser_control")
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
        if action == "set_contact":
            if not alias or not target:
                raise AutomationError("Provide a name and a number, e.g. 'remember rahul as +91 98765 43210'.")
            digits = re.sub(r"\D", "", target)
            if len(digits) < 7 or len(digits) > 15:
                raise AutomationError("That does not look like a phone number (7–15 digits).")
            memory.set_contact(alias, target)
            return ExecutionResult(True, f"Saved contact '{alias}' -> {target}.", "memory")
        if action == "delete_contact":
            if not alias:
                raise AutomationError("Which contact should I forget?")
            if memory.delete_contact(alias):
                return ExecutionResult(True, f"Forgot the contact '{alias}'.", "memory")
            return ExecutionResult(False, f"No contact named '{alias}'.", "memory")
        if action == "list_contacts":
            contacts = memory.list_contacts()
            if not contacts:
                return ExecutionResult(True, "No contacts are stored. Add one with 'remember rahul as +91 98765 43210'.", "memory")
            lines = [f"{k} -> {v}" for k, v in sorted(contacts.items())]
            return ExecutionResult(True, "Contacts:\n" + "\n".join(lines), "memory")
        if action == "set_preference":
            if not key:
                raise AutomationError("A preference key is required.")
            memory.record_preference(key, value)
            return ExecutionResult(True, f"Saved preference {key}.", "memory")
        raise AutomationError("Unsupported memory action.")

    def _send_message(self, recipient: str, text: str) -> ExecutionResult:
        """Open WhatsApp with the message prefilled (wa.me deep link).

        WhatsApp has no offline/local API, so this opens the chat with the
        text ready; the user presses Enter (or the send button) to deliver it.
        """
        import urllib.parse

        from . import memory

        recipient = " ".join((recipient or "").split()).strip()
        if not recipient:
            raise AutomationError("Who should I message?")
        label = re.sub(r"\s+(?:on|in)\s+(?:whatsapp|wa)$", "", recipient, flags=re.I).strip() or recipient

        # A raw phone number is accepted directly; otherwise look up the contacts store.
        digits = re.sub(r"\D", "", recipient)
        phone_like = bool(re.fullmatch(r"\s*\+?[0-9][0-9\s().-]{6,}[0-9]\s*", recipient))
        used_draft = False
        if phone_like and 7 <= len(digits) <= 15:
            number = digits
        else:
            contact = memory.get_contact(label)
            if contact is None:
                known = sorted(memory.list_contacts())
                hint = f"No contact named '{label}'. Say e.g. 'remember {label} as +91 98765 43210' to add one."
                if known:
                    hint += " Known contacts: " + ", ".join(known[:10]) + "."
                raise AutomationError(hint)
            number = re.sub(r"\D", "", contact)
            if len(number) < 7 or len(number) > 15:
                raise AutomationError(f"The stored number for '{label}' looks wrong: {contact}")

        text = (text or "").strip()
        if not text:
            draft = memory.get_preference("draft")
            if isinstance(draft, str) and draft.strip():
                text = draft.strip()
                used_draft = True
            else:
                raise AutomationError(
                    "What should the message say? Include it, e.g. "
                    "'send a message to " + label + " saying I'll be late', "
                    "or save a draft with 'remember the message ...'."
                )
        if len(text) > 3000:
            text = text[:3000]
        url = "https://wa.me/" + number + "?text=" + urllib.parse.quote(text, safe="")
        try:
            self._browser_control("open_url", url, browser="edge")
        except AutomationError as exc:
            raise AutomationError(f"Could not open WhatsApp in Edge: {exc}") from exc
        suffix = " (used your saved draft)" if used_draft else ""
        return ExecutionResult(
            True,
            f"Opened WhatsApp for {label} with your message prefilled{suffix} — "
            "press Enter (or the send button) to deliver it.",
            "send_message",
        )

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

    def _shortcut(self, action: str, trigger: str = "", steps: str = "") -> ExecutionResult:
        from . import shortcuts

        action = (action or "").strip().lower()
        if action == "list":
            items = shortcuts.list_shortcuts()
            if not items:
                return ExecutionResult(True, "No shortcuts are saved.", "shortcut")
            lines = [f"{name}: {'; '.join(steps)}" for name, steps in sorted(items.items())]
            return ExecutionResult(True, "Shortcuts:\n" + "\n".join(lines), "shortcut")
        if action == "delete":
            if not trigger:
                raise AutomationError("Which shortcut should I delete?")
            if shortcuts.delete(trigger):
                return ExecutionResult(True, f"Deleted shortcut '{trigger}'.", "shortcut")
            return ExecutionResult(False, f"No shortcut named '{trigger}'.", "shortcut")
        if action == "save":
            if not trigger or not steps:
                raise AutomationError("A trigger phrase and steps are required.")
            step_list = [part.strip() for part in steps.replace(";", ",").split(",") if part.strip()]
            if not step_list:
                step_list = [steps.strip()]
            shortcuts.set_shortcut(trigger, step_list)
            return ExecutionResult(
                True,
                f"Saved shortcut '{trigger}' with {len(step_list)} step(s).",
                "shortcut",
            )
        raise AutomationError("Unsupported shortcut action.")

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
