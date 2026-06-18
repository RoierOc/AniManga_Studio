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
_PROJECT_BASE = Path("/Manga_Upscaler_project")
DEFAULT_MANGA_DIR = _PROJECT_BASE / "MangaLibrary"
DEFAULT_UPSCALED_DIR = _PROJECT_BASE / "MangaLibrary_Upscaled"

MANGA_DIR = Path(os.environ.get("MANGA_DIR", str(DEFAULT_MANGA_DIR))).expanduser()
UPSCALED_DIR = Path(os.environ.get("UPSCALED_DIR", str(DEFAULT_UPSCALED_DIR))).expanduser()

DEFAULT_MODELS_DIR = PROJECT_ROOT.parent / "MangaJaNai" / "models"
MODEL_PATH_2X = Path(
    os.environ.get(
        "MODEL_PATH_2X",
        str(DEFAULT_MODELS_DIR / "2x_IllustrationJaNai_V2standard_FDAT_M_unshuffle_40k.safetensors"),
    )
).expanduser()
MODEL_PATH_4X = Path(
    os.environ.get(
        "MODEL_PATH_4X",
        str(DEFAULT_MODELS_DIR / "4x_IllustrationJaNai_V2standard_FDAT_M_52k.safetensors"),
    )
).expanduser()
MODEL_PATH_EULA_4X = Path(
    os.environ.get(
        "MODEL_PATH_EULA_4X",
        "/Manga_Upscaler_project/MODELS/4x-eula-digimanga-bw-v2-nc1.pth",
    )
).expanduser()

MODEL_PATH_DWTP_4X = Path(
    os.environ.get(
        "MODEL_PATH_DWTP_4X",
        "/Manga_Upscaler_project/MODELS/4x-DWTP-ds-esrgan-5.pth",
    )
).expanduser()

_MANGAJANAI_DIR = Path("/Manga_Upscaler_project/MODEL_TEST/models")
MODEL_PATH_MANGAJANAI_1200 = Path(
    os.environ.get("MODEL_PATH_MANGAJANAI_1200",
                   str(_MANGAJANAI_DIR / "4x_MangaJaNai_1200p_V1_ESRGAN_70k.pth"))
)
MODEL_PATH_MANGAJANAI_1400 = Path(
    os.environ.get("MODEL_PATH_MANGAJANAI_1400",
                   str(_MANGAJANAI_DIR / "4x_MangaJaNai_1400p_V1_ESRGAN_105k.pth"))
)
# Height threshold: pages shorter than this use 1200p model, taller use 1400p
MANGAJANAI_HEIGHT_THRESHOLD = int(os.environ.get("MANGAJANAI_HEIGHT_THRESHOLD", "1290"))

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
