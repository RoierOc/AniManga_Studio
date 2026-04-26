#!/usr/bin/env python3
"""
Export API - Package manga chapters into CBZ/CBR comic archives.
CBZ = ZIP archive (native Python zipfile, no external tools needed).
CBR = same ZIP content with .cbr extension (accepted by Kuro Reader Pro).
"""

import base64
import io
import re
import tempfile
import zipfile
from pathlib import Path

import numpy as np
from flask import Blueprint, jsonify, request, send_file
from PIL import Image

from api.runtime import MANGA_DIR, UPSCALED_DIR, normalize_chapter

export_bp = Blueprint("export", __name__)

_MIME = "application/zip"
_IMAGE_EXTS = ["jpg", "jpeg", "png", "webp"]

COLOR_DIFF_THRESHOLD = 15
COLOR_PIXEL_FRACTION = 0.10


def _is_color_image(img_pil, threshold=COLOR_DIFF_THRESHOLD, min_fraction=COLOR_PIXEL_FRACTION):
    """Returns True if the image has significant non-grayscale content."""
    thumb = img_pil.copy()
    thumb.thumbnail((256, 256))
    arr = np.asarray(thumb.convert('RGB'), dtype=np.int16)
    r, g, b = arr[:, :, 0], arr[:, :, 1], arr[:, :, 2]
    max_diff = np.maximum(np.maximum(np.abs(r - g), np.abs(r - b)), np.abs(g - b))
    return float(np.mean(max_diff > threshold)) > min_fraction


def _chapter_prefix(ch_norm: str) -> str:
    try:
        return f"ch{int(float(ch_norm)):04d}_"
    except (ValueError, TypeError):
        return f"ch{ch_norm}_"


def _sort_key(ch):
    try:
        return float(ch)
    except (ValueError, TypeError):
        return 0.0


def _collect_images(title: str, chapters: list[str]) -> list[tuple[str, Path]]:
    """Return ordered list of (arcname, abs_path) for requested chapters.
    Prefers upscaled images; falls back to originals per-file."""
    manga_root = Path(MANGA_DIR) / title
    up_root = Path(UPSCALED_DIR) / title
    result: list[tuple[str, Path]] = []

    for ch in sorted(chapters, key=_sort_key):
        ch_norm = normalize_chapter(ch)
        prefix = _chapter_prefix(ch_norm)

        orig_files: list[Path] = []
        for ext in _IMAGE_EXTS:
            orig_files.extend(sorted(manga_root.glob(f"{prefix}*.{ext}")))
        orig_files.sort(key=lambda p: p.name)

        for orig in orig_files:
            up_candidate = up_root / orig.name
            # Also check with .jpg extension (upscaler saves as jpg)
            up_candidate_jpg = up_root / (orig.stem + ".jpg")
            if up_candidate.exists():
                chosen = up_candidate
            elif up_candidate_jpg.exists():
                chosen = up_candidate_jpg
            else:
                chosen = orig
            if chosen.exists():
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
    cover_path = (data.get("cover_path") or "").strip()
    cover_b64 = (data.get("cover_data") or "").strip()

    if not title:
        return jsonify({"error": "title required"}), 400
    if not chapters:
        return jsonify({"error": "select at least one chapter"}), 400

    images = _collect_images(title, chapters)
    if not images:
        return jsonify({"error": "No images found for selected chapters"}), 404

    safe_name = re.sub(r"[^\w\s\-]", "", volume_name).strip().replace(" ", "_") or "tomo"
    filename = f"{safe_name}.{fmt}"

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=f".{fmt}")
    try:
        with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_STORED) as zf:
            # Cover image — sorted first (000_cover sorts before Ch0001...)
            if cover_b64:
                try:
                    cover_bytes = base64.b64decode(cover_b64)
                    zf.writestr("000_cover.jpg", cover_bytes)
                except Exception:
                    pass
            elif cover_path and Path(cover_path).exists():
                zf.write(cover_path, "000_cover.jpg")

            for arcname, img_path in images:
                zf.write(str(img_path), arcname)
        tmp.flush()
        tmp_path = tmp.name
    finally:
        tmp.close()

    return send_file(tmp_path, mimetype=_MIME, as_attachment=True,
                     download_name=filename, max_age=0)


@export_bp.route("/preview", methods=["POST"])
def preview_cbz():
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


@export_bp.route("/color_pages", methods=["POST"])
def get_color_pages():
    """Scan original chapter images and return those with significant color content."""
    data = request.get_json(silent=True) or {}
    title = (data.get("title") or "").strip()
    chapters = data.get("chapters") or []

    if not title or not chapters:
        return jsonify([])

    manga_root = Path(MANGA_DIR) / title
    color_pages = []

    for ch in sorted(chapters, key=_sort_key):
        ch_norm = normalize_chapter(ch)
        prefix = _chapter_prefix(ch_norm)

        files: list[Path] = []
        for ext in _IMAGE_EXTS:
            files.extend(sorted(manga_root.glob(f"{prefix}*.{ext}")))
        files.sort(key=lambda p: p.name)

        for img_path in files:
            try:
                img = Image.open(img_path)
                if _is_color_image(img):
                    # URL served via /uploads/<title>/<filename>
                    url = f"/uploads/{title}/{img_path.name}"
                    color_pages.append({
                        "chapter": ch_norm,
                        "filename": img_path.name,
                        "path": str(img_path),
                        "url": url,
                        "label": f"Cap. {ch_norm} · {img_path.name}",
                    })
            except Exception:
                pass

    return jsonify(color_pages)
