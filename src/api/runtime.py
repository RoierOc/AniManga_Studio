#!/usr/bin/env python3
"""
Shared runtime configuration and helpers.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from pathlib import Path
import os
import re
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MANGA_DIR = Path.home() / "MangaLibrary"
DEFAULT_UPSCALED_DIR = Path.home() / "MangaLibrary_Upscaled"

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

PYTHON_EXECUTABLE = os.environ.get("PYTHON_EXECUTABLE", sys.executable)


def normalize_chapter(chapter) -> str:
    """Normalize chapter values so different sources can be compared reliably."""
    if chapter is None:
        return ""

    raw = str(chapter).strip()
    if not raw:
        return ""

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
