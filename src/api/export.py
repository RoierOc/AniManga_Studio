#!/usr/bin/env python3
"""
Export API - Package manga chapters into CBZ/CBR comic archives.
CBZ = ZIP archive (native Python zipfile, no external tools needed).
CBR = same ZIP content with .cbr extension (accepted by Kuro Reader Pro and most readers).
"""

import io
import re
import tempfile
import zipfile
from pathlib import Path

from flask import Blueprint, jsonify, request, send_file

from api.runtime import MANGA_DIR, UPSCALED_DIR, normalize_chapter

export_bp = Blueprint("export", __name__)

_MIME = "application/zip"
_IMAGE_EXTS = ["jpg", "jpeg", "png", "webp"]


def _chapter_prefix(ch_norm: str) -> str:
    try:
        return f"ch{int(float(ch_norm)):04d}_"
    except (ValueError, TypeError):
        return f"ch{ch_norm}_"


def _collect_images(title: str, chapters: list[str]) -> list[tuple[str, Path]]:
    """Return ordered list of (arcname, abs_path) for requested chapters.
    Prefers upscaled images; falls back to originals per-file."""
    manga_root = Path(MANGA_DIR) / title
    up_root = Path(UPSCALED_DIR) / title

    result: list[tuple[str, Path]] = []

    def _sort_key(ch):
        try:
            return float(ch)
        except (ValueError, TypeError):
            return 0.0

    for ch in sorted(chapters, key=_sort_key):
        ch_norm = normalize_chapter(ch)
        prefix = _chapter_prefix(ch_norm)

        # Gather filenames from original dir (ground truth for what exists)
        orig_files: list[Path] = []
        for ext in _IMAGE_EXTS:
            orig_files.extend(sorted(manga_root.glob(f"{prefix}*.{ext}")))
        orig_files.sort(key=lambda p: p.name)

        for orig in orig_files:
            up_candidate = up_root / orig.name
            chosen = up_candidate if up_candidate.exists() else orig
            if chosen.exists():
                # Arcname inside ZIP: Ch0091_001.jpg  →  readable in any reader
                arcname = f"Ch{ch_norm.zfill(4)}_{orig.stem.split('_', 1)[-1]}{orig.suffix}"
                result.append((arcname, chosen))

    return result


@export_bp.route("/cbz", methods=["POST"])
def create_cbz():
    data = request.get_json(silent=True) or {}
    title = (data.get("title") or "").strip()
    chapters = data.get("chapters") or []
    volume_name = (data.get("volume_name") or title or "tomo").strip()
    fmt = "cbr" if str(data.get("format", "cbz")).lower() == "cbr" else "cbz"

    if not title:
        return jsonify({"error": "title required"}), 400
    if not chapters:
        return jsonify({"error": "select at least one chapter"}), 400

    images = _collect_images(title, chapters)
    if not images:
        return jsonify({"error": "No images found for selected chapters"}), 404

    # Sanitize filename
    safe_name = re.sub(r"[^\w\s\-]", "", volume_name).strip().replace(" ", "_") or "tomo"
    filename = f"{safe_name}.{fmt}"

    # Build ZIP in a temp file (images are already compressed — use STORED)
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=f".{fmt}")
    try:
        with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_STORED) as zf:
            for arcname, img_path in images:
                zf.write(str(img_path), arcname)
        tmp.flush()
        tmp_path = tmp.name
    finally:
        tmp.close()

    return send_file(
        tmp_path,
        mimetype=_MIME,
        as_attachment=True,
        download_name=filename,
        max_age=0,
    )


@export_bp.route("/preview", methods=["POST"])
def preview_cbz():
    """Return image list + size estimate before creating the archive."""
    data = request.get_json(silent=True) or {}
    title = (data.get("title") or "").strip()
    chapters = data.get("chapters") or []

    if not title or not chapters:
        return jsonify({"pages": 0, "size_mb": 0, "chapters": []})

    images = _collect_images(title, chapters)
    total_bytes = sum(p.stat().st_size for _, p in images if p.exists())
    upscaled_count = sum(1 for _, p in images if Path(UPSCALED_DIR) in p.parents)

    return jsonify({
        "pages": len(images),
        "size_mb": round(total_bytes / 1_048_576, 1),
        "upscaled_pages": upscaled_count,
        "chapters": chapters,
    })
