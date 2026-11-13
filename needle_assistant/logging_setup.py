from __future__ import annotations

import logging
import sys
import traceback
from logging.handlers import RotatingFileHandler

from .paths import logs_dir


def setup_logging(verbose: bool = False) -> None:
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    if root.handlers:
        return
    formatter = logging.Formatter(
        "%(asctime)s  %(levelname)-7s  %(threadName)-14s  %(name)s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    file_handler = RotatingFileHandler(
        logs_dir() / "needle.log",
        maxBytes=2_000_000,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)
    if verbose and sys.stderr is not None:
        console = logging.StreamHandler()
        console.setFormatter(formatter)
        root.addHandler(console)


def install_excepthook() -> None:
    original = sys.excepthook

    def hook(exc_type, exc_value, exc_traceback):
        if issubclass(exc_type, KeyboardInterrupt):
            original(exc_type, exc_value, exc_traceback)
            return
        logging.getLogger("needle.crash").critical(
            "Unhandled exception:\n%s",
            "".join(traceback.format_exception(exc_type, exc_value, exc_traceback)),
        )
        original(exc_type, exc_value, exc_traceback)

    sys.excepthook = hook
