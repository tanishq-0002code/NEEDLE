from __future__ import annotations

import argparse
import os
import subprocess
import sys
import wave
from pathlib import Path

import numpy as np
import sounddevice as sd

ROOT = Path(__file__).resolve().parent
WHISPER_CLI = ROOT / "whispercpp" / "extracted" / "Release" / "whisper-cli.exe"
MODEL = ROOT / "models" / "ggml-tiny.en-q5_1.bin"
SAMPLE_RATE = 16000
DURATION = 3.0


def record_wav(path: Path, seconds: float = DURATION) -> None:
    print(f"Recording {seconds:.1f} seconds from the default microphone...", flush=True)
    frames = int(seconds * SAMPLE_RATE)
    audio = sd.rec(frames, samplerate=SAMPLE_RATE, channels=1, dtype="int16", blocking=True)
    audio = np.asarray(audio).reshape(-1)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(SAMPLE_RATE)
        wav.writeframes(audio.tobytes())
    print(f"Saved recording to {path}", flush=True)


def run_whisper(audio_path: Path) -> str:
    if not WHISPER_CLI.exists():
        raise FileNotFoundError(f"whisper-cli not found: {WHISPER_CLI}")
    if not MODEL.exists():
        raise FileNotFoundError(f"model not found: {MODEL}")
    cmd = [
        str(WHISPER_CLI),
        "-m", str(MODEL),
        "-f", str(audio_path),
        "-nt",
        "-t", "4",
        "--no-prints",
    ]
    # --no-prints may not exist in this build; fall back gracefully.
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    except Exception:
        cmd = [c for c in cmd if c != "--no-prints"]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)

    stdout = (proc.stdout or "").strip()
    stderr = (proc.stderr or "").strip()
    print("\n--- whisper.cpp stderr (logs) ---")
    print(stderr[-1500:] if stderr else "(none)")
    print("\n--- whisper.cpp stdout (transcription) ---")
    print(stdout if stdout else "(no stdout transcription)")
    if proc.returncode != 0:
        print(f"\nwhisper-cli exited with code {proc.returncode}")
    return stdout


def main() -> int:
    parser = argparse.ArgumentParser(description="Standalone whisper.cpp test for Needle.")
    parser.add_argument("--audio", type=Path, help="Use an existing wav file instead of recording.")
    parser.add_argument("--seconds", type=float, default=DURATION, help="Recording duration.")
    args = parser.parse_args()

    if args.audio:
        audio_path = args.audio.resolve()
        if not audio_path.exists():
            raise SystemExit(f"Audio file not found: {audio_path}")
    else:
        audio_path = ROOT / "test_recording.wav"
        record_wav(audio_path, args.seconds)

    print(f"\nRunning whisper.cpp on: {audio_path}")
    text = run_whisper(audio_path)
    print("\nDetected transcription:")
    print(text if text else "(empty)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())