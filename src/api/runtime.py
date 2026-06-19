#!/usr/bin/env python3
"""
Shared runtime configuration and helpers.
"""

from __future__ import annotations

from collections import deque
from decimal import Decimal, InvalidOperation
from pathlib import Path
import os
import re
import sys
import threading


# ── SSE event bus ─────────────────────────────────────────────────────────────
# Push-based notification queue. Components call push_sse_event(); the SSE
# stream generator calls get_sse_events_since(seq) to drain only new events.
# deque(maxlen=500) acts as a ring buffer so memory never grows unbounded.

_sse_deque: deque = deque(maxlen=500)
_sse_seq: int = 0
_sse_lock = threading.Lock()


def push_sse_event(event_type: str, **payload) -> None:
    global _sse_seq
    with _sse_lock:
        _sse_seq += 1
        _sse_deque.append({"seq": _sse_seq, "type": event_type, **payload})


def get_sse_events_since(seq: int) -> list:
    with _sse_lock:
        return [e for e in _sse_deque if e["seq"] > seq]


def get_current_seq() -> int:
    with _sse_lock:
        return _sse_seq


PROJECT_ROOT = Path(__file__).resolve().parents[2]

# All user data (downloaded/upscaled manga) defaults under the repo itself so a
# fresh clone works with zero configuration. Override via env vars to point
# at any other location (e.g. a bigger disk) exactly like before.
# `or` (not a plain dict .get default) so a blank value in .env.example-derived
# .env files — MANGA_DIR= with nothing after it — falls through to the default
# instead of resolving to Path("") (cwd).
DATA_ROOT = Path(os.environ.get("DATA_ROOT") or str(PROJECT_ROOT / "data")).expanduser()
MANGA_DIR = Path(os.environ.get("MANGA_DIR") or str(DATA_ROOT / "MangaLibrary")).expanduser()
UPSCALED_DIR = Path(os.environ.get("UPSCALED_DIR") or str(DATA_ROOT / "MangaLibrary_Upscaled")).expanduser()

# Upscale model weights live here by default (gitignored — see docs/MODELS.md
# for download links and the registry.json format that makes them pluggable).
# Per-model height thresholds for adaptive registries live in registry.json
# itself (each sub-model's "height_max"), not here.
MODELS_DIR = Path(os.environ.get("MODELS_DIR") or str(PROJECT_ROOT / "models")).expanduser()

PYTHON_EXECUTABLE = os.environ.get("PYTHON_EXECUTABLE", sys.executable)


def normalize_chapter(chapter) -> str:
    """Normalize chapter values so different sources can be compared reliably."""
    if chapter is None:
        return ""

    raw = str(chapter).strip()
    if not raw:
        return ""

    if raw.lower() == "one_shot":
        return "one_shot"

    if raw.lower().startswith("ch"):
        raw = raw[2:]

    try:
        value = Decimal(raw)
        if value == value.to_integral():
            return str(int(value))
        normalized = format(value.normalize(), "f").rstrip("0").rstrip(".")
        return normalized or "0"
    except InvalidOperation:
        trimmed = raw.lstrip("0")
        return trimmed or "0"


def sanitize_title_for_id(title: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", (title or "").strip())
    cleaned = re.sub(r"_+", "_", cleaned).strip("_")
    return cleaned or "manga"


def build_task_id(title: str, chapter, task_type: str) -> str:
    chapter_part = normalize_chapter(chapter) or str(chapter)
    return f"{sanitize_title_for_id(title)}_{task_type}_ch{chapter_part}"
