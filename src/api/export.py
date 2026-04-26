#!/usr/bin/env python3
"""
Export API - Package manga chapters into CBZ/CBR comic archives.
CBZ = ZIP archive (native Python zipfile, no external tools needed).
CBR = same ZIP content with .cbr extension (accepted by Kuro Reader Pro).
Images are re-encoded as JPEG at the requested quality (default 85) so that
4K upscaled files (saved at q=95) are compressed to a distribution-friendly size
without any visible quality loss at 1080p–1440p viewing.
"""

import base64
import io
import re
import tempfile
import zipfile
from decimal import Decimal, InvalidOperation
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
DEFAULT_QUALITY = 85


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
        value = Decimal(ch_norm)
        int_part = int(value.to_integral_value(rounding='ROUND_FLOOR'))
        if '.' in ch_norm:
            return f"ch{int_part:04d}.{ch_norm.split('.')[-1]}_"
        return f"ch{int_part:04d}_"
    except (InvalidOperation, ValueError, TypeError):
        return f"ch{ch_norm}_"


def _sort_key(ch):
    try:
        return float(ch)
    except (ValueError, TypeError):
        return 0.0


def _encode_jpeg(img_path: Path, quality: int) -> bytes:
    """Load any image and return JPEG bytes at the requested quality.
    Preserves grayscale mode (L) for B&W pages — 1-channel JPEG is ~3× smaller than RGB."""
    img = Image.open(img_path)
    if img.mode == 'RGBA':
        bg = Image.new('RGB', img.size, (255, 255, 255))
        bg.paste(img, mask=img.split()[3])
        img = bg
    elif img.mode == 'P':
        img = img.convert('RGB')
    elif img.mode == 'LA':
        img = img.convert('L')
    elif img.mode not in ('RGB', 'L'):
        img = img.convert('RGB')
    buf = io.BytesIO()
    img.save(buf, format='JPEG', quality=quality, optimize=True, progressive=True)
    return buf.getvalue()


def _encode_jpeg_bytes(raw_bytes: bytes, quality: int) -> bytes:
    """Re-encode existing image bytes (e.g. base64-decoded cover) as JPEG."""
    img = Image.open(io.BytesIO(raw_bytes))
    if img.mode == 'RGBA':
        bg = Image.new('RGB', img.size, (255, 255, 255))
        bg.paste(img, mask=img.split()[3])
        img = bg
    elif img.mode == 'P':
        img = img.convert('RGB')
    elif img.mode not in ('RGB', 'L'):
        img = img.convert('RGB')
    buf = io.BytesIO()
    img.save(buf, format='JPEG', quality=quality, optimize=True, progressive=True)
    return buf.getvalue()


def _quality_size_factor(quality: int) -> float:
    """Rough estimate of output/input size ratio when re-encoding 4K q=95 JPEG at `quality`."""
    # Calibrated against typical 4K manga upscale output
    points = [(95, 0.92), (90, 0.73), (85, 0.60), (80, 0.50), (75, 0.42), (70, 0.35)]
    for i, (q1, f1) in enumerate(points[:-1]):
        q2, f2 = points[i + 1]
        if quality >= q2:
            return round(f1 + (f2 - f1) * (q1 - quality) / (q1 - q2), 3)
    return 0.35


def _collect_images(title: str, chapters: list) -> list:
    """Return ordered list of (arcname, abs_path) for requested chapters.
    Prefers upscaled images; falls back to originals per-file.
    arcname always uses .jpg extension (images are re-encoded as JPEG on export)."""
    manga_root = Path(MANGA_DIR) / title
    up_root = Path(UPSCALED_DIR) / title
    result = []

    for ch in sorted(chapters, key=_sort_key):
        ch_norm = normalize_chapter(ch)
        prefix = _chapter_prefix(ch_norm)

        orig_files = []
        for ext in _IMAGE_EXTS:
            orig_files.extend(sorted(manga_root.glob(f"{prefix}*.{ext}")))
        orig_files.sort(key=lambda p: p.name)

        for orig in orig_files:
            up_candidate = up_root / orig.name
            up_candidate_jpg = up_root / (orig.stem + ".jpg")
            if up_candidate.exists():
                chosen = up_candidate
            elif up_candidate_jpg.exists():
                chosen = up_candidate_jpg
            else:
                chosen = orig
            if chosen.exists():
                page_id = orig.stem.split('_', 1)[-1]
                arcname = f"Ch{ch_norm.zfill(4)}_{page_id}.jpg"
                result.append((arcname, chosen))

    return result


@export_bp.route("/cbz", methods=["POST"])
def create_cbz():
    data = request.get_json(silent=True) or {}
    title = (data.get("title") or "").strip()
    chapters = data.get("chapters") or []
    volume_name = (data.get("volume_name") or title or "tomo").strip()
    fmt = "cbr" if str(data.get("format", "cbz")).lower() == "cbr" else "cbz"
    quality = max(70, min(95, int(data.get("quality", DEFAULT_QUALITY))))
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
            # Cover — re-encoded at same quality for consistency
            if cover_b64:
                try:
                    raw = base64.b64decode(cover_b64)
                    zf.writestr("000_cover.jpg", _encode_jpeg_bytes(raw, quality))
                except Exception:
                    pass
            elif cover_path and Path(cover_path).exists():
                try:
                    zf.writestr("000_cover.jpg", _encode_jpeg(Path(cover_path), quality))
                except Exception:
                    zf.write(cover_path, "000_cover.jpg")

            # Pages — re-encode at requested quality
            for arcname, img_path in images:
                try:
                    zf.writestr(arcname, _encode_jpeg(img_path, quality))
                except Exception:
                    # Fallback: store raw bytes rather than silently drop the page
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
    quality = max(70, min(95, int(data.get("quality", DEFAULT_QUALITY))))

    if not title or not chapters:
        return jsonify({"pages": 0, "size_mb": 0, "est_mb": 0, "chapters": []})

    images = _collect_images(title, chapters)
    raw_bytes = sum(p.stat().st_size for _, p in images if p.exists())
    upscaled_count = sum(1 for _, p in images if Path(UPSCALED_DIR) in p.parents)

    factor = _quality_size_factor(quality)
    est_bytes = int(raw_bytes * factor)

    return jsonify({
        "pages": len(images),
        "size_mb": round(raw_bytes / 1_048_576, 1),
        "est_mb": round(est_bytes / 1_048_576, 1),
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

        files = []
        for ext in _IMAGE_EXTS:
            files.extend(sorted(manga_root.glob(f"{prefix}*.{ext}")))
        files.sort(key=lambda p: p.name)

        for img_path in files:
            try:
                img = Image.open(img_path)
                if _is_color_image(img):
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
