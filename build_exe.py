from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SPEC = ROOT / "NeedleDesktop.spec"
EXPECTED_WEIGHTS_SHA256 = "c9d915eca282ed42d1a09b143b592adb4cc6744ffe2d294adf5cfc5548170c38"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def check_assets() -> None:
    required = {
        "assets/engine/libcactus.dll": 1_000_000,
        "assets/models/needle3/needle3.cact": 1_000_000,
        "assets/models/vosk-en-us/am/final.mdl": 1_000_000,
        "assets/models/vosk-en-us/conf/model.conf": 1,
        "assets/icons/needle.ico": 1_000,
    }
    for relative, minimum in required.items():
        path = ROOT / relative
        if not path.exists():
            raise SystemExit(f"Missing required asset: {path}")
        if path.stat().st_size < minimum:
            raise SystemExit(f"Asset looks incomplete: {path} ({path.stat().st_size} bytes)")
    weights = ROOT / "assets/models/needle3/needle3.cact"
    print(f"Verifying Needle 3 weights ({weights.stat().st_size:,} bytes)...")
    actual = sha256(weights)
    if actual != EXPECTED_WEIGHTS_SHA256:
        raise SystemExit(
            "needle3.cact checksum mismatch.\n"
            f"  expected: {EXPECTED_WEIGHTS_SHA256}\n"
            f"  actual:   {actual}"
        )
    print("Needle 3 weights verified (SHA-256 matches the official release).")


def run_tests() -> None:
    print("Running unit tests...")
    result = subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
        cwd=ROOT,
    )
    if result.returncode != 0:
        raise SystemExit("Unit tests failed; build aborted.")


def build(onedir: bool, clean: bool) -> Path:
    try:
        import PyInstaller.__main__ as pyinstaller
    except ImportError as exc:
        raise SystemExit(
            "PyInstaller is not installed. Run: python -m pip install -r requirements.txt"
        ) from exc

    env = os.environ.copy()
    env["NEEDLE_ONEDIR"] = "1" if onedir else "0"
    args = [
        str(SPEC),
        "--noconfirm",
        "--distpath",
        str(ROOT / "dist"),
        "--workpath",
        str(ROOT / "build"),
    ]
    if clean:
        args.append("--clean")

    print("Building", "onedir" if onedir else "onefile", "with PyInstaller...")
    started = time.monotonic()
    old_env = os.environ.copy()
    os.environ.update(env)
    try:
        pyinstaller.run(args)
    finally:
        os.environ.clear()
        os.environ.update(old_env)

    elapsed = time.monotonic() - started
    output = ROOT / "dist" / "NeedleDesktop"
    exe = (output / "NeedleDesktop.exe") if onedir else (ROOT / "dist" / "NeedleDesktop.exe")
    if not exe.exists():
        raise SystemExit(f"Build finished but executable was not found: {exe}")
    size_mb = exe.stat().st_size / (1024 * 1024)
    print(f"Build complete in {elapsed:.1f}s")
    print(f"Executable: {exe} ({size_mb:.1f} MB)")
    if onedir:
        print(f"Folder:     {output}")
    return exe


def verify(exe: Path) -> None:
    print("Running frozen selftest (this can take a minute on slow CPUs)...")
    result = subprocess.run([str(exe), "--selftest"], cwd=ROOT, timeout=600)
    report_path = Path(os.environ.get("APPDATA", "")) / "NeedleDesktop" / "selftest.json"
    if report_path.exists():
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
            print(f"Selftest report: {report_path}")
            print(f"Selftest success: {report.get('success')}")
            for item in report.get("checks", []):
                print(f"  [{'ok' if item.get('ok') else 'fail'}] {item.get('name')}: {item.get('detail')}")
        except Exception as exc:
            print(f"Could not read selftest report: {exc}")
    if result.returncode != 0:
        raise SystemExit(f"Frozen selftest failed with exit code {result.returncode}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the standalone Needle Desktop app.")
    parser.add_argument("--onedir", action="store_true", help="Build a folder instead of a onefile exe.")
    parser.add_argument("--no-tests", action="store_true", help="Skip unit tests.")
    parser.add_argument("--no-clean", action="store_true", help="Reuse the PyInstaller work cache.")
    parser.add_argument("--no-verify", action="store_true", help="Do not run the frozen selftest after building.")
    args = parser.parse_args(argv)

    if sys.platform != "win32":
        raise SystemExit("Needle Desktop is a Windows application; build it on Windows x86-64.")
    if sys.version_info < (3, 10):
        raise SystemExit("Python 3.10 or newer is required.")

    print(f"Python {sys.version.split()[0]} ({sys.executable})")
    check_assets()
    if not args.no_tests:
        run_tests()
    exe = build(args.onedir, clean=not args.no_clean)
    if not args.no_verify:
        verify(exe)
    print("Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
