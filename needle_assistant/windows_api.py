from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

SW_RESTORE = 9
SW_SHOW = 5
VK_MEDIA_NEXT_TRACK = 0xB0
VK_MEDIA_PREV_TRACK = 0xB1
VK_MEDIA_STOP = 0xB2
VK_MEDIA_PLAY_PAUSE = 0xB3
VK_VOLUME_MUTE = 0xAD
KEYEVENTF_KEYUP = 0x0002

WNDENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)


class RECT(ctypes.Structure):
    _fields_ = [("left", wt.LONG), ("top", wt.LONG), ("right", wt.LONG), ("bottom", wt.LONG)]


@dataclass(frozen=True)
class WindowInfo:
    hwnd: int
    pid: int
    title: str
    visible: bool
    rect: tuple[int, int, int, int]


def set_dpi_awareness() -> None:
    try:
        user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        return
    except Exception:
        pass
    try:
        shcore = ctypes.WinDLL("shcore")
        shcore.SetProcessDpiAwareness(2)
        return
    except Exception:
        pass
    try:
        user32.SetProcessDPIAware()
    except Exception:
        pass


def get_foreground_window() -> int:
    return int(user32.GetForegroundWindow() or 0)


def is_window(hwnd: int) -> bool:
    return bool(hwnd) and bool(user32.IsWindow(hwnd))


def is_window_visible(hwnd: int) -> bool:
    return bool(hwnd) and bool(user32.IsWindowVisible(hwnd))


def is_minimized(hwnd: int) -> bool:
    return bool(hwnd) and bool(user32.IsIconic(hwnd))


def get_window_title(hwnd: int) -> str:
    if not hwnd:
        return ""
    length = int(user32.GetWindowTextLengthW(hwnd))
    buffer = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buffer, length + 1)
    return buffer.value


def get_window_pid(hwnd: int) -> int:
    pid = wt.DWORD()
    if hwnd:
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return int(pid.value)


def get_window_rect(hwnd: int) -> tuple[int, int, int, int]:
    rect = RECT()
    if hwnd and user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return int(rect.left), int(rect.top), int(rect.right), int(rect.bottom)
    return 0, 0, 0, 0


def show_window(hwnd: int, command: int = SW_SHOW) -> bool:
    if not hwnd:
        return False
    return bool(user32.ShowWindow(hwnd, command))


def focus_window(hwnd: int) -> bool:
    """Restore and foreground a window, working around Windows focus stealing rules."""
    if not is_window(hwnd):
        return False
    try:
        if is_minimized(hwnd):
            show_window(hwnd, SW_RESTORE)
        target_thread = int(user32.GetWindowThreadProcessId(hwnd, None))
        current_thread = int(kernel32.GetCurrentThreadId())
        foreground = get_foreground_window()
        foreground_thread = int(user32.GetWindowThreadProcessId(foreground, None)) if foreground else 0
        attached: list[int] = []
        for thread in (foreground_thread, target_thread):
            if thread and thread != current_thread and thread not in attached:
                if user32.AttachThreadInput(thread, current_thread, True):
                    attached.append(thread)
        try:
            user32.BringWindowToTop(hwnd)
            user32.SetForegroundWindow(hwnd)
            user32.SetFocus(hwnd)
        finally:
            for thread in attached:
                user32.AttachThreadInput(thread, current_thread, False)
        return get_foreground_window() == hwnd or is_window_visible(hwnd)
    except Exception:
        return False


def enumerate_windows() -> list[WindowInfo]:
    windows: list[WindowInfo] = []

    @WNDENUMPROC
    def callback(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        title = get_window_title(hwnd)
        if not title:
            return True
        windows.append(WindowInfo(
            hwnd=int(hwnd),
            pid=get_window_pid(hwnd),
            title=title,
            visible=True,
            rect=get_window_rect(hwnd),
        ))
        return True

    user32.EnumWindows(callback, 0)
    return windows


def find_window_for_pid(pid: int) -> int:
    best = 0
    best_area = 0
    for info in enumerate_windows():
        if info.pid != pid:
            continue
        left, top, right, bottom = info.rect
        area = max(0, right - left) * max(0, bottom - top)
        if area >= best_area:
            best, best_area = info.hwnd, area
    return best


def wait_for_pid_window(pid: int, timeout: float = 6.0) -> int:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        hwnd = find_window_for_pid(pid)
        if hwnd:
            return hwnd
        time.sleep(0.15)
    return 0


def wait_for_new_window(after_hwnds: set[int], timeout: float = 6.0) -> int:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for info in enumerate_windows():
            if info.hwnd not in after_hwnds:
                return info.hwnd
        time.sleep(0.15)
    return 0


def visible_window_handles() -> set[int]:
    return {info.hwnd for info in enumerate_windows()}


def send_media_key(action: str) -> None:
    mapping = {
        "play": VK_MEDIA_PLAY_PAUSE,
        "pause": VK_MEDIA_PLAY_PAUSE,
        "next": VK_MEDIA_NEXT_TRACK,
        "previous": VK_MEDIA_PREV_TRACK,
        "mute": VK_VOLUME_MUTE,
    }
    vk = mapping.get(action)
    if vk is None:
        raise ValueError(f"Unsupported media action: {action}")
    user32.keybd_event(vk, 0, 0, 0)
    time.sleep(0.04)
    user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)


def get_windows_build() -> int:
    try:
        return int(sys.getwindowsversion().build)
    except Exception:
        return 0


class ACCENTPOLICY(ctypes.Structure):
    _fields_ = [
        ("AccentState", ctypes.c_int),
        ("AccentFlags", ctypes.c_int),
        ("GradientColor", ctypes.c_uint),
        ("AnimationId", ctypes.c_int),
    ]


class WINDOWCOMPOSITIONATTRIBDATA(ctypes.Structure):
    _fields_ = [
        ("Attribute", ctypes.c_int),
        ("Data", ctypes.c_void_p),
        ("SizeOfData", ctypes.c_size_t),
    ]


def enable_acrylic(hwnd: int, tint: int = 0xCC14141C) -> bool:
    """Ask DWM for a subtle acrylic blur behind the overlay; safe to ignore on failure."""
    if not hwnd or get_windows_build() < 17134:
        return False
    try:
        accent = ACCENTPOLICY(4, 2, tint, 0)  # ACCENT_ENABLE_ACRYLICBLURBEHIND
        data = WINDOWCOMPOSITIONATTRIBDATA(19, ctypes.cast(ctypes.pointer(accent), ctypes.c_void_p),
                                           ctypes.sizeof(accent))
        setter = getattr(user32, "SetWindowCompositionAttribute", None)
        if not setter:
            return False
        setter.argtypes = [wt.HWND, ctypes.POINTER(WINDOWCOMPOSITIONATTRIBDATA)]
        setter.restype = wt.BOOL
        return bool(setter(hwnd, ctypes.byref(data)))
    except Exception:
        return False


def enable_rounded_corners(hwnd: int) -> bool:
    if not hwnd or get_windows_build() < 22000:
        return False
    try:
        dwmapi = ctypes.WinDLL("dwmapi")
        preference = ctypes.c_int(2)  # DWMWCP_ROUND
        result = dwmapi.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(preference),
                                              ctypes.sizeof(preference))
        return result == 0
    except Exception:
        return False


def startup_command() -> str:
    if getattr(sys, "frozen", False):
        return f'"{Path(sys.executable)}"'
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    executable = pythonw if pythonw.exists() else Path(sys.executable)
    entry = Path(__file__).resolve().parent.parent / "run.py"
    return f'"{executable}" "{entry}"'


def set_start_with_windows(enabled: bool) -> bool:
    try:
        import winreg

        key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE) as key:
            if enabled:
                winreg.SetValueEx(key, "NeedleDesktop", 0, winreg.REG_SZ, startup_command())
            else:
                try:
                    winreg.DeleteValue(key, "NeedleDesktop")
                except FileNotFoundError:
                    pass
        return True
    except Exception:
        return False


def is_start_with_windows() -> bool:
    try:
        import winreg

        key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_READ) as key:
            value, _ = winreg.QueryValueEx(key, "NeedleDesktop")
            return bool(value)
    except Exception:
        return False


def open_folder(path: Path) -> None:
    os.startfile(str(path))  # type: ignore[attr-defined]
