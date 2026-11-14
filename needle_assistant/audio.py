from __future__ import annotations

import json
import logging
import math
import queue
import re
import threading
import time
from array import array
from typing import Iterable

from PyQt6.QtCore import QObject, pyqtSignal

from .config import AppConfig
from .paths import vosk_model_dir

log = logging.getLogger("needle.audio")

SAMPLE_RATE = 16000
BLOCK_SECONDS = 0.25
DEFAULT_BLOCK = int(SAMPLE_RATE * BLOCK_SECONDS)
WAKE_TAIL_SECONDS = 0.8
COMMAND_SILENCE_SECONDS = 1.35
COMMAND_MAX_SECONDS = 12.0
COMMAND_EMPTY_SECONDS = 7.0


def normalize_phrase(text: str) -> str:
    value = re.sub(r"[^a-z0-9 ]", " ", (text or "").lower())
    return re.sub(r"\s+", " ", value).strip()


def list_input_devices() -> list[tuple[int, str]]:
    try:
        import sounddevice as sd

        host_apis = sd.query_hostapis()
        devices: list[tuple[int, str]] = []
        for index, info in enumerate(sd.query_devices()):
            if int(info.get("max_input_channels", 0)) <= 0:
                continue
            api_index = int(info.get("hostapi", -1))
            api_name = host_apis[api_index]["name"] if 0 <= api_index < len(host_apis) else ""
            name = str(info.get("name", f"Input {index}"))
            label = f"{index}: {name}" + (f"  [{api_name}]" if api_name else "")
            devices.append((index, label))
        return devices
    except Exception:
        return []


def _resample_int16(data: bytes, source_rate: int, target_rate: int = SAMPLE_RATE) -> bytes:
    if source_rate == target_rate:
        return data
    samples = array("h")
    samples.frombytes(data)
    if not samples:
        return b""
    count = max(1, int(len(samples) * target_rate / source_rate))
    output = array("h", [0]) * count
    ratio = (len(samples) - 1) / max(1, count - 1)
    for index in range(count):
        position = index * ratio
        left = int(position)
        right = min(left + 1, len(samples) - 1)
        fraction = position - left
        output[index] = int(samples[left] * (1.0 - fraction) + samples[right] * fraction)
    return output.tobytes()


class AudioWorker(threading.Thread):
    def __init__(self, engine: "AudioEngine", config: AppConfig) -> None:
        super().__init__(name="NeedleAudio", daemon=True)
        self.engine = engine
        self.config = config
        self.commands: queue.Queue[str] = queue.Queue()
        self._stop = threading.Event()
        self._stream = None
        self._stream_lock = threading.Lock()
        self._model = None
        self._wake_enabled = bool(config.wake_word_enabled)
        self._phrases = list(config.wake_phrases)
        self._mode = "idle"
        self._source_rate = SAMPLE_RATE
        self._read_frames = DEFAULT_BLOCK
        self._device_index = config.microphone_index
        self._noise_floor = 180.0
        self._reset_command_state()

    def _reset_command_state(self) -> None:
        self._command_started = 0.0
        self._last_voice = 0.0
        self._heard_voice = False
        self._command_parts: list[str] = []

    def stop(self) -> None:
        self._stop.set()
        self.commands.put("stop")
        self._close_stream()

    def update_config(self, config: AppConfig) -> None:
        self.config = config
        self._phrases = list(config.wake_phrases)
        self._device_index = config.microphone_index
        self.commands.put("wake_on" if config.wake_word_enabled else "wake_off")

    def _emit(self, signal, *args) -> None:
        try:
            signal.emit(*args)
        except Exception:
            pass

    def _close_stream(self) -> None:
        with self._stream_lock:
            stream = self._stream
            self._stream = None
        if stream is not None:
            try:
                stream.stop()
            except Exception:
                pass
            try:
                stream.close()
            except Exception:
                pass

    def _open_stream(self) -> bool:
        self._close_stream()
        try:
            import sounddevice as sd

            device = self._device_index
            # Use the microphone's native rate and resample to 16 kHz for
            # Vosk.  Forcing 16 kHz on MME can silently capture distorted or
            # empty audio on some Realtek and Bluetooth devices.
            try:
                default_input = sd.default.device[0]
                info = sd.query_devices(device if device is not None else default_input, "input")
                source_rate = int(info.get("default_samplerate") or SAMPLE_RATE)
            except Exception:
                source_rate = SAMPLE_RATE
            try:
                sd.check_input_settings(device=device, samplerate=source_rate, channels=1, dtype="int16")
            except Exception:
                source_rate = SAMPLE_RATE
                try:
                    sd.check_input_settings(device=device, samplerate=source_rate, channels=1, dtype="int16")
                except Exception:
                    pass
            frames = int(source_rate * BLOCK_SECONDS)
            stream = sd.RawInputStream(
                samplerate=source_rate,
                blocksize=frames,
                dtype="int16",
                channels=1,
                device=device,
                latency="low",
            )
            stream.start()
            with self._stream_lock:
                self._stream = stream
            self._source_rate = source_rate
            self._read_frames = frames
            log.info("Microphone opened at %s Hz (device=%s).", source_rate, device)
            return True
        except Exception as exc:
            self._close_stream()
            log.warning("Microphone unavailable: %s", exc)
            self._emit(self.engine.error,
                       "Microphone unavailable. Check Windows microphone privacy settings and the selected input device.")
            return False

    def _new_wake_recognizer(self):
        import vosk

        if not self._model:
            return None
        try:
            grammar = json.dumps(list(self._phrases) + ["[unk]"])
            return vosk.KaldiRecognizer(self._model, SAMPLE_RATE, grammar)
        except Exception:
            log.exception("Could not create the restricted wake-word recognizer; using open vocabulary.")
            return vosk.KaldiRecognizer(self._model, SAMPLE_RATE)

    def _matches(self, text: str) -> str | None:
        normalized = normalize_phrase(text)
        if not normalized:
            return None
        for phrase in self._phrases:
            if phrase and phrase in normalized:
                return phrase
        return None

    def _set_mode(self, mode: str) -> None:
        self._mode = mode
        if mode == "command":
            self._reset_command_state()
            self._command_started = time.monotonic()
            self._emit(self.engine.listening_changed, True)
            self._emit(self.engine.status_changed, "Listening")
        else:
            self._emit(self.engine.listening_changed, False)

    def run(self) -> None:
        try:
            import vosk

            vosk.SetLogLevel(-1)
            self._emit(self.engine.status_changed, "Loading speech model")
            model_dir = vosk_model_dir()
            if not model_dir.exists():
                self._emit(self.engine.error, "Offline speech model is missing. Place vosk-en-us in assets/models.")
                return
            started = time.monotonic()
            self._model = vosk.Model(str(model_dir))
            log.info("Vosk model loaded in %.2fs.", time.monotonic() - started)
            self._emit(self.engine.status_changed, "Ready")
        except Exception as exc:
            log.exception("Vosk model failed to load.")
            self._emit(self.engine.error, f"Offline speech model failed to load: {exc}")
            return

        wake_recognizer = None
        command_recognizer = None
        try:
            while not self._stop.is_set():
                try:
                    command = self.commands.get(timeout=0.12)
                except queue.Empty:
                    command = None
                if command:
                    if command == "stop":
                        break
                    if command == "wake_on":
                        self._wake_enabled = True
                        self._phrases = list(self.config.wake_phrases)
                        if self._mode != "command":
                            if not self._open_stream():
                                self._wake_enabled = False
                                continue
                            wake_recognizer = self._new_wake_recognizer()
                            self._set_mode("wake")
                    elif command == "wake_off":
                        self._wake_enabled = False
                        if self._mode != "command":
                            self._close_stream()
                            self._set_mode("idle")
                    elif command == "command":
                        if self._stream is None and not self._open_stream():
                            continue
                        command_recognizer = self._new_command_recognizer()
                        self._set_mode("command")
                    elif command == "cancel":
                        if self._mode == "command":
                            self._close_stream()
                            if self._wake_enabled:
                                if not self._open_stream():
                                    self._wake_enabled = False
                                    self._set_mode("idle")
                                    continue
                                wake_recognizer = self._new_wake_recognizer()
                                self._set_mode("wake")
                            else:
                                self._set_mode("idle")
                        else:
                            self._emit(self.engine.listening_changed, False)

                if self._stop.is_set():
                    break
                if self._stream is None:
                    if self._wake_enabled and self._mode != "command":
                        if not self._open_stream():
                            self._wake_enabled = False
                            self._set_mode("idle")
                            time.sleep(0.5)
                            continue
                        wake_recognizer = self._new_wake_recognizer()
                        self._set_mode("wake")
                    else:
                        time.sleep(0.08)
                        continue

                try:
                    with self._stream_lock:
                        stream = self._stream
                    if stream is None:
                        continue
                    data, overflowed = stream.read(self._read_frames)
                    audio = bytes(data)
                except Exception as exc:
                    if self._stop.is_set():
                        break
                    log.warning("Microphone read failed: %s", exc)
                    self._close_stream()
                    self._set_mode("idle")
                    self._emit(self.engine.error, "The microphone stream stopped. I'll try again when you trigger me.")
                    self._wake_enabled = False
                    continue

                if overflowed:
                    log.debug("Microphone input overflow.")
                if self._source_rate != SAMPLE_RATE:
                    audio = _resample_int16(audio, self._source_rate, SAMPLE_RATE)
                if not audio:
                    continue
                level = self._level(audio)
                self._emit(self.engine.level_changed, level)

                if self._mode == "wake":
                    if wake_recognizer is None:
                        wake_recognizer = self._new_wake_recognizer()
                    if wake_recognizer is None:
                        continue
                    if wake_recognizer.AcceptWaveform(audio):
                        try:
                            text = json.loads(wake_recognizer.Result()).get("text", "")
                        except Exception:
                            text = ""
                        phrase = self._matches(text)
                        if phrase:
                            self._trigger_wake(phrase)
                            command_recognizer = self._new_command_recognizer()
                            self._set_mode("command")
                        elif text:
                            log.debug("Wake recognizer heard %r (no phrase match).", text)
                    else:
                        try:
                            partial = json.loads(wake_recognizer.PartialResult()).get("partial", "")
                        except Exception:
                            partial = ""
                        phrase = self._matches(partial)
                        if phrase:
                            self._trigger_wake(phrase)
                            command_recognizer = self._new_command_recognizer()
                            self._set_mode("command")
                        elif partial:
                            log.debug("Wake partial: %r", partial)
                elif self._mode == "command":
                    if command_recognizer is None:
                        command_recognizer = self._new_command_recognizer()
                    if command_recognizer is None:
                        continue
                    if command_recognizer.AcceptWaveform(audio):
                        try:
                            part = json.loads(command_recognizer.Result()).get("text", "")
                        except Exception:
                            part = ""
                        if part:
                            self._command_parts.append(part)
                    try:
                        partial = json.loads(command_recognizer.PartialResult()).get("partial", "")
                    except Exception:
                        partial = ""
                    if partial:
                        self._emit(self.engine.partial_text, partial)
                    now = time.monotonic()
                    if level > 0.045:
                        self._last_voice = now
                        self._heard_voice = True
                    elapsed = now - self._command_started
                    silence = now - self._last_voice if self._heard_voice else elapsed
                    timed_out = elapsed >= COMMAND_MAX_SECONDS
                    empty_timeout = not self._heard_voice and elapsed >= COMMAND_EMPTY_SECONDS
                    finished = (self._heard_voice and silence >= COMMAND_SILENCE_SECONDS) or timed_out or empty_timeout
                    if finished:
                        try:
                            tail = json.loads(command_recognizer.FinalResult()).get("text", "")
                        except Exception:
                            tail = ""
                        parts = list(self._command_parts)
                        if tail:
                            parts.append(tail)
                        text = " ".join(" ".join(parts).split()).strip()
                        self._emit(self.engine.command_ready, text)
                        self._reset_command_state()
                        command_recognizer = None
                        if self._wake_enabled:
                            wake_recognizer = self._new_wake_recognizer()
                            self._set_mode("wake")
                        else:
                            self._close_stream()
                            self._set_mode("idle")
        finally:
            self._close_stream()
            self._emit(self.engine.listening_changed, False)

    def _new_command_recognizer(self):
        import vosk

        if not self._model:
            return None
        try:
            recognizer = vosk.KaldiRecognizer(self._model, SAMPLE_RATE)
            recognizer.SetWords(False)
            return recognizer
        except Exception:
            log.exception("Could not create the command recognizer.")
            return None

    def _trigger_wake(self, phrase: str) -> None:
        log.info("Wake word detected: %s", phrase)
        self._emit(self.engine.wake_detected, phrase)

    def _level(self, audio: bytes) -> float:
        try:
            samples = array("h")
            samples.frombytes(audio)
            if not samples:
                return 0.0
            total = 0
            peak = 0
            for sample in samples:
                total += sample * sample
                magnitude = abs(sample)
                if magnitude > peak:
                    peak = magnitude
            rms = math.sqrt(total / len(samples))
            self._noise_floor = max(40.0, self._noise_floor * 0.97 + rms * 0.03)
            return min(1.0, max(0.0, (rms - self._noise_floor) / 7000.0))
        except Exception:
            return 0.0


class AudioEngine(QObject):
    wake_detected = pyqtSignal(str)
    partial_text = pyqtSignal(str)
    command_ready = pyqtSignal(str)
    level_changed = pyqtSignal(float)
    listening_changed = pyqtSignal(bool)
    status_changed = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(self, config: AppConfig, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.config = config
        self._worker: AudioWorker | None = None

    @property
    def worker(self) -> AudioWorker | None:
        return self._worker

    def start(self) -> None:
        if self._worker is not None and self._worker.is_alive():
            return
        self._worker = AudioWorker(self, self.config)
        self._worker.start()
        if self.config.wake_word_enabled:
            self._worker.commands.put("wake_on")
        else:
            self._worker.commands.put("wake_off")

    def begin_command(self) -> None:
        if self._worker is None or not self._worker.is_alive():
            self.start()
        if self._worker is not None:
            self._worker.commands.put("command")

    def cancel_command(self) -> None:
        if self._worker is not None and self._worker.is_alive():
            self._worker.commands.put("cancel")

    def set_wake_enabled(self, enabled: bool) -> None:
        self.config.wake_word_enabled = bool(enabled)
        if self._worker is None or not self._worker.is_alive():
            if enabled:
                self.start()
            return
        self._worker.commands.put("wake_on" if enabled else "wake_off")

    def reload(self, config: AppConfig) -> None:
        self.config = config
        if self._worker is not None and self._worker.is_alive():
            self._worker.update_config(config)

    def restart(self, config: AppConfig) -> None:
        """Restart the worker after a microphone or model-affecting change."""
        self.shutdown()
        self.config = config
        self.start()

    def shutdown(self) -> None:
        worker = self._worker
        self._worker = None
        if worker is not None:
            worker.stop()
            worker.join(timeout=2.0)
