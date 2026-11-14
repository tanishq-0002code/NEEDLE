from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
import traceback
from dataclasses import asdict
from pathlib import Path

from . import __app_name__, __version__
from .config import AppConfig, load_config
from .logging_setup import install_excepthook, setup_logging
from .paths import (
    app_data_dir,
    engine_dll,
    needle_weights,
    preview_path,
    selftest_path,
    vosk_model_dir,
)
from .theme import app_icon, apply_app_style
from . import windows_api

log = logging.getLogger("needle.app")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="NeedleDesktop", add_help=True)
    parser.add_argument("--settings", action="store_true", help="Open settings on launch.")
    parser.add_argument("--no-wake", action="store_true", help="Disable wake-word listening for this run.")
    parser.add_argument("--route", metavar="TEXT", help="Route a command through Needle and print the parsed action.")
    parser.add_argument("--no-fast", action="store_true", help="Disable the deterministic fast route.")
    parser.add_argument("--selftest", action="store_true", help="Verify assets, engine, model, and UI; write a JSON report.")
    parser.add_argument("--render-preview", action="store_true", help="Render the overlay to a PNG and exit.")
    parser.add_argument("--verbose", action="store_true", help="Enable console logging.")
    parser.add_argument("--version", action="version", version=f"{__app_name__} {__version__}")
    return parser


def _safe_text(value: object) -> str:
    text = str(value or "")
    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    try:
        return text.encode(encoding, "replace").decode(encoding)
    except (LookupError, UnicodeError):
        return text.encode("utf-8", "replace").decode("utf-8")


def _check(results: list[dict], name: str, function) -> bool:
    started = time.monotonic()
    try:
        detail = function()
        duration = time.monotonic() - started
        text = str(detail or "")
        results.append({"name": name, "ok": True, "detail": text, "seconds": round(duration, 2)})
        print(f"[ok]   {name} ({duration:.2f}s) {_safe_text(text)}")
        return True
    except Exception as exc:
        duration = time.monotonic() - started
        text = f"{type(exc).__name__}: {exc}"
        results.append({
            "name": name,
            "ok": False,
            "detail": text,
            "traceback": traceback.format_exc(),
            "seconds": round(duration, 2),
        })
        print(f"[fail] {name} ({duration:.2f}s): {_safe_text(text)}")
        return False


def run_selftest() -> int:
    results: list[dict] = []
    report_path = selftest_path()

    def assets() -> str:
        for label, path, minimum in (
            ("libcactus.dll", engine_dll(), 100_000),
            ("needle3.cact", needle_weights(), 1_000_000),
            ("vosk-en-us", vosk_model_dir(), 1),
        ):
            if not path.exists():
                raise FileNotFoundError(f"{label} is missing at {path}")
            if path.is_file() and path.stat().st_size < minimum:
                raise ValueError(f"{label} looks incomplete ({path.stat().st_size} bytes)")
        return f"engine={engine_dll().stat().st_size} bytes, weights={needle_weights().stat().st_size} bytes"

    _check(results, "bundled assets", assets)

    def qt_ui() -> str:
        from PyQt6.QtWidgets import QApplication

        from .overlay import OverlayWindow

        app = QApplication.instance() or QApplication(["NeedleDesktop", "--selftest"])
        apply_app_style(app)
        overlay = OverlayWindow(AppConfig())
        overlay.show_welcome()
        app.processEvents()
        time.sleep(0.2)
        app.processEvents()
        image = overlay.grab()
        output = app_data_dir() / "selftest-overlay.png"
        if not image.isNull():
            image.save(str(output), "PNG")
        overlay.hide_immediately()
        overlay.deleteLater()
        app.processEvents()
        return f"Qt overlay rendered ({image.width()}x{image.height()})"

    _check(results, "PyQt6 overlay", qt_ui)

    def speech_model() -> str:
        import vosk

        vosk.SetLogLevel(-1)
        started = time.monotonic()
        model = vosk.Model(str(vosk_model_dir()))
        recognizer = vosk.KaldiRecognizer(model, 16000, json.dumps(["hey jarvis", "hey computer", "[unk]"]))
        if recognizer is None:
            raise RuntimeError("recognizer was not created")
        return f"Vosk model loaded in {time.monotonic() - started:.2f}s"

    _check(results, "offline speech model", speech_model)

    router_holder: dict[str, object] = {}

    def needle_load() -> str:
        from .needler import NeedleRouter

        router = NeedleRouter(max_new_tokens=128, threads=4)
        router.load()
        router_holder["router"] = router
        return f"engine loaded, sha256={router.weights_sha256[:16]}…"

    _check(results, "Needle 3 engine", needle_load)

    def needle_route() -> str:
        router = router_holder.get("router")
        if router is None:
            raise RuntimeError("router was not loaded")
        call = router.route("open notepad", use_fast=False)  # type: ignore[attr-defined]
        if call.name != "open_application":
            raise RuntimeError(f"expected open_application, got {call.name!r}: {call.error or call.reasoning}")
        return f"{call.name} {call.arguments} in {call.latency:.2f}s"

    _check(results, "Needle 3 route", needle_route)

    def automation_imports() -> str:
        import pynput  # noqa: F401
        import pyautogui  # noqa: F401
        import uiautomation as auto
        import sounddevice as sd
        import comtypes

        devices = [d for d in sd.query_devices() if int(d.get("max_input_channels", 0)) > 0]
        uia = "UIA unavailable"
        try:
            comtypes.CoInitialize()
            root = auto.GetRootControl()
            uia = f"UIA root: {root.Name or 'Desktop'}"
        except Exception as exc:
            uia = f"UIA check failed: {exc}"
        from .automation import DesktopAutomation

        automation = DesktopAutomation()
        resolved = automation._resolver.resolve("notepad")  # noqa: SLF001
        return f"{len(devices)} input device(s), notepad -> {resolved.kind}:{resolved.target}, {uia}"

    _check(results, "Windows automation", automation_imports)

    def screenshot() -> str:
        from .automation import DesktopAutomation
        from .models import ToolCall

        automation = DesktopAutomation()
        result = automation.execute(ToolCall(name="take_screenshot", arguments={}))
        if not result.success:
            raise RuntimeError(result.message)
        return result.message

    _check(results, "screenshot capture", screenshot)

    def hotkey_parse() -> str:
        from .hotkey import to_pynput_hotkey

        value = to_pynput_hotkey("ctrl+space")
        if "ctrl" not in value or "space" not in value:
            raise RuntimeError(f"unexpected hotkey parse: {value}")
        return value

    _check(results, "global hotkey", hotkey_parse)

    def telegram_module() -> str:
        import certifi
        import requests

        from .telegram_bot import TelegramBot  # noqa: F401

        bundle = Path(certifi.where())
        if not bundle.exists():
            raise FileNotFoundError(f"certifi CA bundle is missing: {bundle}")
        return f"requests {requests.__version__}, Telegram module and CA bundle present"

    _check(results, "Telegram bot module", telegram_module)

    router = router_holder.get("router")
    if router is not None:
        try:
            router.close()  # type: ignore[attr-defined]
        except Exception:
            pass

    success = all(item["ok"] for item in results)
    report = {
        "success": success,
        "version": __version__,
        "frozen": bool(getattr(sys, "frozen", False)),
        "executable": sys.executable,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "checks": results,
    }
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    (app_data_dir() / "selftest.log").write_text(
        "\n".join(
            f"{'OK' if item['ok'] else 'FAIL'}  {item['name']}: {item['detail']}"
            for item in results
        ) + f"\n\nReport: {report_path}\n",
        encoding="utf-8",
    )
    print(f"\nSelftest report: {report_path}")
    print("RESULT:", "PASS" if success else "FAIL")
    return 0 if success else 1


def run_route(text: str, use_fast: bool) -> int:
    from .needler import NeedleRouter

    router = NeedleRouter(max_new_tokens=128, threads=4)
    try:
        router.load()
        call = router.route(text, use_fast=use_fast)
        print(json.dumps(asdict(call), indent=2, ensure_ascii=False, default=str))
        return 0 if call.name else 2
    finally:
        router.close()


def run_preview() -> int:
    from PyQt6.QtCore import QTimer
    from PyQt6.QtGui import QGuiApplication
    from PyQt6.QtWidgets import QApplication

    from .overlay import OverlayWindow

    windows_api.set_dpi_awareness()
    app = QApplication(["NeedleDesktop", "--render-preview"])
    apply_app_style(app)
    overlay = OverlayWindow(AppConfig())
    overlay.show_welcome()

    def capture() -> None:
        screen = QGuiApplication.primaryScreen()
        target = preview_path()
        if screen is not None:
            screen.grabWindow(0).save(str(target), "PNG")
        widget_image = overlay.grab()
        widget_image.save(str(app_data_dir() / "overlay-widget.png"), "PNG")
        overlay.hide_immediately()
        print(f"Preview: {target}")
        app.quit()

    QTimer.singleShot(900, capture)
    return app.exec()


def run_app(args: argparse.Namespace) -> int:
    from PyQt6.QtCore import QTimer, Qt
    from PyQt6.QtGui import QGuiApplication
    from PyQt6.QtWidgets import QApplication, QSystemTrayIcon

    from .controller import AssistantController
    from .single_instance import SingleInstance
    from .tray import TrayIcon

    if sys.platform == "win32":
        windows_api.set_dpi_awareness()
        try:
            QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
                Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
            )
        except Exception:
            pass

    app = QApplication([sys.argv[0]])
    app.setApplicationName(__app_name__)
    app.setApplicationDisplayName(__app_name__)
    app.setOrganizationName("Needle")
    app.setApplicationVersion(__version__)
    app.setQuitOnLastWindowClosed(False)
    apply_app_style(app)
    app.setWindowIcon(app_icon())

    instance = SingleInstance()
    if not instance.acquire():
        return 0

    config = load_config()
    if args.no_wake:
        config.wake_word_enabled = False
    controller = AssistantController(app, config)
    tray = TrayIcon(app_icon(), config.wake_word_enabled, config.hotkey)
    tray.set_hotkey(config.hotkey)

    def show_from_tray() -> None:
        if controller.overlay.isVisible():
            controller.overlay.raise_()
        else:
            controller.trigger("tray")

    controller.notify.connect(tray.show_message)
    controller.status_changed.connect(tray.set_status)
    controller.config_changed.connect(
        lambda cfg: (tray.set_wake_checked(cfg.wake_word_enabled), tray.set_hotkey(cfg.hotkey))
    )
    tray.show_requested.connect(show_from_tray)
    tray.type_requested.connect(controller.show_typed_command)
    tray.settings_requested.connect(controller.show_settings)
    tray.open_folder_requested.connect(controller.open_data_folder)
    tray.wake_toggled.connect(controller.toggle_wake)
    tray.quit_requested.connect(controller.quit)
    instance.show_requested.connect(show_from_tray)
    app.aboutToQuit.connect(instance.close)
    app.aboutToQuit.connect(controller.quit)

    controller.start()

    if not QSystemTrayIcon.isSystemTrayAvailable():
        log.warning("No system tray is available; opening settings.")
        controller.show_settings()
    elif not config.first_run_done or args.settings:
        controller.show_settings()
    elif config.show_on_start:
        QTimer.singleShot(
            900,
            lambda: tray.show_message(
                "Needle is ready",
                "Press Ctrl+Space or say “Hey Jarvis” to give a command.",
            ),
        )
    return app.exec()


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    setup_logging(args.verbose)
    install_excepthook()
    log.info("Needle Desktop %s starting (frozen=%s)", __version__, bool(getattr(sys, "frozen", False)))

    if args.selftest:
        return run_selftest()
    if args.render_preview:
        return run_preview()
    if args.route:
        return run_route(args.route, use_fast=not args.no_fast)
    return run_app(args)
