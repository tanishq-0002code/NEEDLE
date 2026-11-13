from __future__ import annotations

import ctypes
import hashlib
import json
import logging
import os
import threading
import time
from collections import OrderedDict
from dataclasses import replace
from pathlib import Path
from typing import Any

from .catalog import ALLOWED_TOOLS, SYSTEM_PROMPT, TOOL_SCHEMAS, fast_route, validate_call
from .models import ToolCall
from .paths import engine_dll, needle_weights

log = logging.getLogger("needle.router")

MAX_BUFFER = 256 * 1024
CACHE_LIMIT = 96


class NeedleUnavailable(RuntimeError):
    pass


def _as_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_envelope(raw: str, user_text: str, latency: float, source: str = "needle") -> ToolCall:
    try:
        envelope = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as exc:
        return ToolCall(name=None, user_text=user_text, latency=latency, source=source,
                        raw_text=raw, error=f"Needle returned an unreadable response: {exc}")
    if not isinstance(envelope, dict):
        return ToolCall(name=None, user_text=user_text, latency=latency, source=source,
                        raw_text=raw, error="Needle returned an unexpected response.")

    if envelope.get("success") is False:
        message = envelope.get("error") or envelope.get("reason") or "Needle could not route that command."
        return ToolCall(name=None, user_text=user_text, latency=latency, source=source,
                        raw_text=raw, error=str(message))

    calls = envelope.get("function_calls") or []
    if not calls:
        message = envelope.get("response") or envelope.get("reason") or (
            "I couldn't map that to one of the desktop actions."
        )
        return ToolCall(name=None, user_text=user_text, latency=latency, source=source,
                        raw_text=raw, reasoning=str(message), error=str(message))

    call = calls[0] or {}
    name = str(call.get("name", "")).strip()
    arguments = call.get("arguments") or {}
    if name not in ALLOWED_TOOLS:
        return ToolCall(name=None, user_text=user_text, latency=latency, source=source,
                        raw_text=raw, error=f"Needle selected an unknown action: {name or 'none'}")
    try:
        name, arguments = validate_call(name, arguments, user_text)
    except ValueError as exc:
        return ToolCall(name=None, user_text=user_text, latency=latency, source=source,
                        raw_text=raw, error=str(exc))

    return ToolCall(
        name=name,
        arguments=arguments,
        source=source,
        confidence=_as_float(envelope.get("confidence")),
        reasoning=str(envelope.get("reasoning") or ""),
        raw_text=raw,
        latency=latency,
        user_text=user_text,
    )


class NeedleRouter:
    """Thin, offline ctypes binding for the official Cactus C ABI."""

    def __init__(self, max_new_tokens: int = 128, threads: int = 4,
                 log_transcripts: bool = False) -> None:
        self.max_new_tokens = max(32, min(512, int(max_new_tokens)))
        self.threads = max(0, min(64, int(threads)))
        self.log_transcripts = log_transcripts
        self._lock = threading.RLock()
        self._dll: ctypes.CDLL | None = None
        self._weights_blob: bytes | None = None
        self._loaded = False
        self._load_error = ""
        self._cache: OrderedDict[str, ToolCall] = OrderedDict()
        self._weights_sha256 = ""
        self._dll_dir_handle = None

    @property
    def loaded(self) -> bool:
        return self._loaded

    @property
    def load_error(self) -> str:
        return self._load_error

    @property
    def weights_sha256(self) -> str:
        return self._weights_sha256

    def load(self) -> None:
        with self._lock:
            if self._loaded:
                return
            dll_path = engine_dll()
            weights_path = needle_weights()
            if not dll_path.exists():
                raise NeedleUnavailable(f"Missing Cactus engine: {dll_path}")
            if not weights_path.exists():
                raise NeedleUnavailable(f"Missing Needle 3 weights: {weights_path}")
            if weights_path.stat().st_size < 1_000_000:
                raise NeedleUnavailable("The Needle 3 weights file is incomplete.")
            try:
                if hasattr(os, "add_dll_directory"):
                    self._dll_dir_handle = os.add_dll_directory(str(dll_path.parent))
                dll = ctypes.CDLL(str(dll_path))
            except OSError as exc:
                raise NeedleUnavailable(f"Could not load the Cactus engine: {exc}") from exc

            text = ctypes.c_char_p
            integer = ctypes.c_int
            samples = ctypes.POINTER(ctypes.c_float)
            dll.cactus_load.argtypes = [text, ctypes.c_uint64]
            dll.cactus_load.restype = integer
            dll.cactus_init.argtypes = [text, text, text]
            dll.cactus_init.restype = integer
            dll.cactus_complete.argtypes = [text, samples, integer, integer, text, integer]
            dll.cactus_complete.restype = integer
            dll.cactus_reset.argtypes = []
            dll.cactus_reset.restype = None
            dll.cactus_set_threads.argtypes = [integer]
            dll.cactus_set_threads.restype = None
            dll.cactus_last_error.argtypes = []
            dll.cactus_last_error.restype = text

            blob = weights_path.read_bytes()
            self._weights_sha256 = hashlib.sha256(blob).hexdigest()
            log.info("Loading Needle 3 weights: %s bytes sha256=%s", len(blob), self._weights_sha256)
            if dll.cactus_load(blob, len(blob)) < 0:
                error = dll.cactus_last_error()
                raise NeedleUnavailable(
                    "Cactus could not load needle3.cact: "
                    + (error.decode("utf-8", "replace") if error else "unknown error")
                )
            thread_count = self.threads or max(4, min(8, os.cpu_count() or 4))
            dll.cactus_set_threads(int(thread_count))
            tools_json = json.dumps(TOOL_SCHEMAS, separators=(",", ":")).encode("utf-8")
            rc = dll.cactus_init(SYSTEM_PROMPT.encode("utf-8"), tools_json, None)
            if rc < 0:
                error = dll.cactus_last_error()
                raise NeedleUnavailable(
                    "Cactus could not initialize the Needle 3 router: "
                    + (error.decode("utf-8", "replace") if error else f"code {rc}")
                )
            self._dll = dll
            self._weights_blob = blob
            self._loaded = True
            self._load_error = ""
            log.info("Needle 3 ready with %s CPU thread(s).", thread_count)

    def close(self) -> None:
        with self._lock:
            self._loaded = False
            self._dll = None
            self._weights_blob = None
            if self._dll_dir_handle is not None:
                try:
                    self._dll_dir_handle.close()
                except Exception:
                    pass
                self._dll_dir_handle = None

    def set_threads(self, threads: int) -> None:
        self.threads = max(0, min(64, int(threads)))
        if self._dll is not None and self._loaded:
            count = self.threads or max(4, min(8, os.cpu_count() or 4))
            # Never block the UI thread while a route is running.
            if self._lock.acquire(blocking=False):
                try:
                    self._dll.cactus_set_threads(int(count))
                finally:
                    self._lock.release()
            else:
                log.info("Thread count will apply after the current route finishes.")

    def set_max_new_tokens(self, value: int) -> None:
        self.max_new_tokens = max(32, min(512, int(value)))

    def clear_cache(self) -> None:
        with self._lock:
            self._cache.clear()

    def route(self, text: str, use_fast: bool = True) -> ToolCall:
        cleaned = " ".join(str(text or "").split())
        if not cleaned:
            return ToolCall(name=None, error="I didn't hear a command.", user_text=text)
        if self.log_transcripts:
            log.info("Routing transcript: %r", cleaned)
        if use_fast:
            shortcut = fast_route(cleaned)
            if shortcut is not None:
                log.info("Fast route: %s", shortcut.name)
                return shortcut

        cache_key = cleaned.lower()
        with self._lock:
            cached = self._cache.get(cache_key)
            if cached is not None:
                self._cache.move_to_end(cache_key)
                log.info("Needle cache hit: %s", cached.name)
                return replace(cached, source="cache", latency=0.0)

        with self._lock:
            if not self._loaded or self._dll is None:
                try:
                    self.load()
                except NeedleUnavailable as exc:
                    self._load_error = str(exc)
                    return ToolCall(name=None, user_text=cleaned, error=str(exc))
            assert self._dll is not None
            started = time.monotonic()
            try:
                self._dll.cactus_reset()
                buffer = ctypes.create_string_buffer(MAX_BUFFER)
                rc = self._dll.cactus_complete(
                    cleaned.encode("utf-8"),
                    None,
                    0,
                    int(self.max_new_tokens),
                    buffer,
                    len(buffer),
                )
                elapsed = time.monotonic() - started
                if rc < 0:
                    error = self._dll.cactus_last_error()
                    message = error.decode("utf-8", "replace") if error else f"Cactus returned code {rc}"
                    log.warning("Needle route failed: %s", message)
                    return ToolCall(name=None, user_text=cleaned, latency=elapsed, error=message)
                raw = buffer.value.decode("utf-8", "replace")
            finally:
                try:
                    self._dll.cactus_reset()
                except Exception:
                    pass

        call = parse_envelope(raw, cleaned, elapsed)
        log.info(
            "Needle route: tool=%s confidence=%s latency=%.2fs",
            call.name or "none", call.confidence, call.latency,
        )
        if call.name and not call.error:
            self._cache[cache_key] = replace(call)
            self._cache.move_to_end(cache_key)
            while len(self._cache) > CACHE_LIMIT:
                self._cache.popitem(last=False)
        return call
