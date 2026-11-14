from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

from .paths import app_data_dir


def _allowed_roots() -> list[Path]:
    home = Path.home()
    roots = [
        home / "Desktop",
        home / "Documents",
        home / "Downloads",
        home / "Pictures",
        app_data_dir(),
    ]
    return [root for root in roots if root.exists()]


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def resolve_path(raw: Any, allow_any: bool = False) -> Path:
    text = str(raw or "").strip().strip("\"'")
    if not text:
        raise ValueError("A file path is required.")
    path = Path(text).expanduser()
    if not path.is_absolute():
        path = (Path.home() / "Documents" / path)
    path = path.resolve()
    if allow_any:
        return path
    roots = _allowed_roots()
    if not roots or not any(_is_relative_to(path, root.resolve()) for root in roots):
        raise ValueError(
            "That path is outside the allowed folders (Desktop, Documents, "
            "Downloads, Pictures, or the Needle data folder)."
        )
    return path


def read_text(path: Path, limit: int = 4000) -> str:
    data = path.read_text(encoding="utf-8", errors="replace")
    if len(data) > limit:
        return data[:limit] + "\n... (truncated)"
    return data


def list_directory(path: Path) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    for child in sorted(path.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower())):
        entries.append(
            {
                "name": child.name,
                "type": "dir" if child.is_dir() else "file",
                "size": str(child.stat().st_size) if child.is_file() else "",
            }
        )
        if len(entries) >= 80:
            break
    return entries


def delete_path(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink()


def move_path(source: Path, destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    return Path(shutil.move(str(source), str(destination)))


def format_listing(path: Path, entries: list[dict[str, str]]) -> str:
    lines = [f"{path}:"]
    for entry in entries:
        suffix = "/" if entry["type"] == "dir" else ""
        size = f" ({entry['size']} bytes)" if entry["size"] else ""
        lines.append(f"  {entry['name']}{suffix}{size}")
    if not entries:
        lines.append("  (empty)")
    return "\n".join(lines)
