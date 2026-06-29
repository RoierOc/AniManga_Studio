#!/usr/bin/env python3
"""
Export API - Package manga chapters into CBZ/CBR comic archives.
CBZ = ZIP archive (native Python zipfile, no external tools needed).
CBR = same ZIP content with .cbr extension (accepted by Kuro Reader Pro).
Images are re-encoded as JPEG at the requested quality via Pillow + MozJPEG
lossless post-optimization (optimize=True, progressive=True, then mozjpeg pass).
"""

import base64
import io
import re
import tempfile
import threading
import time
import uuid
import zipfile
from urllib.parse import quote
from decimal import Decimal, InvalidOperation
from pathlib import Path

import numpy as np
from flask import Blueprint, jsonify, request, send_file
from PIL import Image

from api.runtime import MANGA_DIR, UPSCALED_DIR, normalize_chapter, cache_get, cache_set

_COLOR_TTL = 30 * 86400   # la firma de contenido en la clave auto-invalida; el TTL solo poda viejos

# ── Async export task tracking ────────────────────────────────────────────────
_export_tasks: dict = {}          # task_id → task dict
_export_lock = threading.Lock()
_export_semaphore = threading.Semaphore(3)  # max 3 concurrent exports
_EXPORT_TTL = 7200                # clean up temp files after 2 hours
_cancel_flags: set = set()        # task_ids requested to be cancelled

def get_export_tasks() -> dict:
    return dict(_export_tasks)

def _set_export(task_id: str, updates: dict):
    with _export_lock:
        if task_id in _export_tasks:
            # Stamp completion time once, on entering a terminal state — feeds Activity history.
            if updates.get('status') in ('complete', 'error', 'cancelled') and not _export_tasks[task_id].get('ended_at'):
                updates = {**updates, 'ended_at': time.time()}
            _export_tasks[task_id].update(updates)

def _cleanup_exports():
    now = time.time()
    with _export_lock:
        to_remove = [k for k, v in list(_export_tasks.items())
                     if now - v.get('created_at', 0) > _EXPORT_TTL]
    for k in to_remove:
        tmp = _export_tasks.get(k, {}).get('tmp_path')
        if tmp:
            try:
                Path(tmp).unlink(missing_ok=True)
            except Exception:
                pass
        _export_tasks.pop(k, None)

try:
    import mozjpeg_lossless_optimization as _mozjpeg
    _MOZJPEG = True
except ImportError:
    _MOZJPEG = False

export_bp = Blueprint("export", __name__)

_MIME = "application/zip"
_IMAGE_EXTS = ["jpg", "jpeg", "png", "webp"]

COLOR_DIFF_THRESHOLD = 15
COLOR_PIXEL_FRACTION = 0.10
DEFAULT_QUALITY = 92


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


def _mozjpeg_optimize(data: bytes) -> bytes:
    if not _MOZJPEG:
        return data
    try:
        return _mozjpeg.optimize(data)
    except Exception:
        return data


def _normalize_mode(img):
    """Flatten any input image to a JPEG/WebP-friendly mode (RGB or L)."""
    if img.mode == 'RGBA':
        bg = Image.new('RGB', img.size, (255, 255, 255))
        bg.paste(img, mask=img.split()[3])
        return bg
    if img.mode == 'P':
        return img.convert('RGB')
    if img.mode == 'LA':
        return img.convert('L')
    if img.mode not in ('RGB', 'L'):
        return img.convert('RGB')
    return img


def _encode_jpeg(img_path: Path, quality: int, downscale: int = 1) -> bytes:
    """Load any image and return JPEG bytes at the requested quality.
    Preserves grayscale mode (L) for B&W pages.
    downscale=2 halves width and height before encoding (for tablet-sized exports).
    Post-processed with MozJPEG lossless optimization when available."""
    img = _normalize_mode(Image.open(img_path))
    if downscale > 1:
        img = img.resize((max(1, img.width // downscale), max(1, img.height // downscale)), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format='JPEG', quality=quality, optimize=True, progressive=True)
    return _mozjpeg_optimize(buf.getvalue())


def _encode_webp(img_path: Path, quality: int, downscale: int = 1) -> bytes:
    """Load any image and return WebP bytes at the requested quality.
    For manga (line art + screentone + sharp translated text), WebP at a matched perceived
    quality is meaningfully smaller than JPEG and degrades far more gracefully — it doesn't ring
    around the hard edges of overlaid text the way JPEG does. method=6 = slowest/best ratio."""
    img = _normalize_mode(Image.open(img_path))
    if downscale > 1:
        img = img.resize((max(1, img.width // downscale), max(1, img.height // downscale)), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format='WEBP', quality=quality, method=6)
    return buf.getvalue()


# Codec registry: maps the request `codec` to (file extension, encoder fn). JPEG stays the default
# so existing exports and readers are unaffected; WebP is opt-in for users whose reader supports it.
_CODECS = {
    'jpeg': ('jpg',  _encode_jpeg),
    'webp': ('webp', _encode_webp),
}


def _encode_image(img_path: Path, quality: int, downscale: int, codec: str) -> bytes:
    _, fn = _CODECS.get(codec, _CODECS['jpeg'])
    return fn(img_path, quality, downscale)


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
    return _mozjpeg_optimize(buf.getvalue())


def _quality_size_factor(quality: int) -> float:
    """Rough estimate of output/input size ratio when re-encoding 4K q=95 JPEG at `quality`."""
    # Calibrated against typical 4K manga upscale output
    points = [(95, 0.92), (90, 0.73), (85, 0.60), (80, 0.50), (75, 0.42), (70, 0.35)]
    for i, (q1, f1) in enumerate(points[:-1]):
        q2, f2 = points[i + 1]
        if quality >= q2:
            return round(f1 + (f2 - f1) * (q1 - quality) / (q1 - q2), 3)
    return 0.35


def _collect_images(title: str, chapters: list, exclude_pages: set = None, arc_ext: str = "jpg") -> list:
    """Return ordered list of (arcname, abs_path) for requested chapters.
    Prefers upscaled images; falls back to originals per-file.
    arcname uses arc_ext (matches the codec the pages are re-encoded with).
    exclude_pages: set of original filenames to skip."""
    manga_root = Path(MANGA_DIR) / title
    up_root = Path(UPSCALED_DIR) / title
    result = []
    exclude = exclude_pages or set()

    for ch in sorted(chapters, key=_sort_key):
        ch_norm = normalize_chapter(ch)
        prefix = _chapter_prefix(ch_norm)

        orig_files = []
        for ext in _IMAGE_EXTS:
            orig_files.extend(sorted(manga_root.glob(f"{prefix}*.{ext}")))
        orig_files.sort(key=lambda p: p.name)

        for orig in orig_files:
            if orig.name in exclude:
                continue
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
                arcname = f"Ch{ch_norm.zfill(4)}_{page_id}.{arc_ext}"
                result.append((arcname, chosen))

    return result


def build_archive(data, progress_cb=None) -> tuple:
    """Build CBZ/CBR archive from request data. Returns (tmp_path, filename) or raises.
    progress_cb(done, total) is called after each image if provided."""
    title = (data.get("title") or "").strip()
    chapters = data.get("chapters") or []
    volume_name = (data.get("volume_name") or title or "tomo").strip()
    fmt = "cbr" if str(data.get("format", "cbz")).lower() == "cbr" else "cbz"
    codec = str(data.get("codec", "jpeg")).lower()
    if codec not in _CODECS:
        codec = "jpeg"
    arc_ext, _ = _CODECS[codec]
    # WebP stays visually clean at lower quality than JPEG, so its sensible floor is lower.
    q_floor = 70 if codec == "webp" else 85
    quality = max(q_floor, min(100, int(data.get("quality", DEFAULT_QUALITY))))
    downscale = 2 if data.get("downscale_half") else 1
    cover_path = (data.get("cover_path") or "").strip()
    cover_b64 = (data.get("cover_data") or "").strip()
    exclude_pages = set(data.get("exclude_pages") or [])

    if not title:
        raise ValueError("title required")
    if not chapters:
        raise ValueError("select at least one chapter")

    images = _collect_images(title, chapters, exclude_pages, arc_ext)
    if not images:
        raise ValueError("No images found for selected chapters")

    safe_name = re.sub(r"[^\w\s\-]", "", volume_name).strip() or "tomo"
    filename = f"{safe_name}.{fmt}"

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=f".{fmt}")
    try:
        with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_STORED) as zf:
            if cover_b64:
                try:
                    raw = base64.b64decode(cover_b64)
                    # Detect format from magic bytes — keep original to avoid re-encoding loss
                    ext = "png" if raw[:8] == b'\x89PNG\r\n\x1a\n' else "jpg"
                    if ext == "png":
                        # PNG cover: embed as-is at full quality
                        zf.writestr(f"000_cover.{ext}", raw)
                    else:
                        # JPEG cover: re-encode only to normalize orientation/metadata
                        zf.writestr("000_cover.jpg", _encode_jpeg_bytes(raw, 95))
                except Exception:
                    pass
            elif cover_path and Path(cover_path).exists():
                try:
                    zf.writestr("000_cover.jpg", _encode_jpeg(Path(cover_path), quality, downscale))
                except Exception:
                    zf.write(cover_path, "000_cover.jpg")

            total_images = len(images)
            for done, (arcname, img_path) in enumerate(images, 1):
                try:
                    zf.writestr(arcname, _encode_image(img_path, quality, downscale, codec))
                except Exception:
                    zf.write(str(img_path), arcname)
                if progress_cb:
                    progress_cb(done, total_images)

        tmp.flush()
        tmp_path = tmp.name
    finally:
        tmp.close()

    return tmp_path, filename


@export_bp.route("/cbz", methods=["POST"])
def create_cbz():
    data = request.get_json(silent=True) or {}
    try:
        tmp_path, filename = build_archive(data)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    return send_file(tmp_path, mimetype=_MIME, as_attachment=True,
                     download_name=filename, max_age=0)


@export_bp.route("/preview", methods=["POST"])
def preview_cbz():
    data = request.get_json(silent=True) or {}
    title = (data.get("title") or "").strip()
    chapters = data.get("chapters") or []
    codec = str(data.get("codec", "jpeg")).lower()
    if codec not in _CODECS:
        codec = "jpeg"
    quality = max(70 if codec == "webp" else 85, min(100, int(data.get("quality", DEFAULT_QUALITY))))
    exclude_pages = set(data.get("exclude_pages") or [])

    if not title or not chapters:
        return jsonify({"pages": 0, "size_mb": 0, "est_mb": 0, "chapters": []})

    images = _collect_images(title, chapters, exclude_pages)
    raw_bytes = sum(p.stat().st_size for _, p in images if p.exists())
    up_root = Path(UPSCALED_DIR) / title
    upscaled_count = sum(1 for _, p in images if str(p).startswith(str(up_root)))
    original_count = len(images) - upscaled_count

    factor = _quality_size_factor(quality)
    # WebP runs ~20% smaller than JPEG at a matched perceived quality (more on text-heavy pages).
    if codec == "webp":
        factor *= 0.80
    est_bytes = int(raw_bytes * factor)

    return jsonify({
        "pages": len(images),
        "size_mb": round(raw_bytes / 1_048_576, 1),
        "est_mb": round(est_bytes / 1_048_576, 1),
        "upscaled_pages": upscaled_count,
        "original_pages": original_count,
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

        # Caché por capítulo: la detección de color es determinista para unos archivos dados.
        # La firma (nombre+tamaño+mtime) va en la CLAVE, así que reescribir páginas (traducción/
        # upscale) cambia la firma → recalcula solo ese capítulo; si no, se reusa al instante.
        sig = "|".join(f"{p.name}:{s.st_size}:{int(s.st_mtime)}"
                       for p in files for s in (p.stat(),))
        ckey = f"{title}|{ch_norm}|{sig}"
        cached = cache_get("color", ckey, _COLOR_TTL)
        if cached is not None:
            color_pages.extend(cached)
            continue

        ch_pages = []
        for img_path in files:
            try:
                img = Image.open(img_path)
                if _is_color_image(img):
                    ch_pages.append({
                        "chapter": ch_norm,
                        "filename": img_path.name,
                        "path": str(img_path),
                        "url": f"/uploads/{quote(title, safe='')}/{img_path.name}",
                        "label": f"Cap. {ch_norm} · {img_path.name}",
                    })
            except Exception:
                pass
        cache_set("color", ckey, ch_pages, ttl=_COLOR_TTL, max_entries=400)
        color_pages.extend(ch_pages)

    return jsonify(color_pages)


# ── Async export endpoints ────────────────────────────────────────────────────

class _Cancelled(Exception):
    pass


def _run_export(task_id: str, data: dict):
    tmp_path = None
    with _export_semaphore:
        if task_id in _cancel_flags:
            _cancel_flags.discard(task_id)
            _set_export(task_id, {'status': 'cancelled'})
            return
        _set_export(task_id, {'status': 'running'})
        try:
            def on_progress(done, total):
                _set_export(task_id, {'progress': done, 'total': total})
                if task_id in _cancel_flags:
                    raise _Cancelled()

            tmp_path, filename = build_archive(data, progress_cb=on_progress)
            _set_export(task_id, {
                'status': 'complete',
                'tmp_path': tmp_path,
                'filename': filename,
                'progress': _export_tasks[task_id].get('total', 0),
            })
        except _Cancelled:
            _cancel_flags.discard(task_id)
            if tmp_path:
                try:
                    Path(tmp_path).unlink(missing_ok=True)
                except Exception:
                    pass
            _set_export(task_id, {'status': 'cancelled'})
        except Exception as e:
            if tmp_path:
                try:
                    Path(tmp_path).unlink(missing_ok=True)
                except Exception:
                    pass
            _set_export(task_id, {'status': 'error', 'error': str(e)})


@export_bp.route("/start", methods=["POST"])
def start_export():
    """Start an async export. Returns {task_id} immediately; poll /export/status/<id>."""
    _cleanup_exports()
    data = request.get_json(silent=True) or {}
    title = (data.get("title") or "").strip()
    if not title:
        return jsonify({"error": "title required"}), 400
    if not data.get("chapters"):
        return jsonify({"error": "select at least one chapter"}), 400

    task_id = uuid.uuid4().hex[:10]
    with _export_lock:
        _export_tasks[task_id] = {
            'task_id': task_id,
            'status': 'queued',
            'title': title,
            'volume_name': (data.get("volume_name") or title).strip(),
            'progress': 0,
            'total': 0,
            'created_at': time.time(),
            'tmp_path': None,
            'filename': None,
            'error': None,
        }
    threading.Thread(target=_run_export, args=(task_id, data), daemon=True).start()
    return jsonify({'task_id': task_id, 'status': 'queued'})


@export_bp.route("/status/<task_id>")
def export_status(task_id):
    task = _export_tasks.get(task_id)
    if not task:
        return jsonify({'error': 'not found'}), 404
    return jsonify({k: v for k, v in task.items() if k != 'tmp_path'})


@export_bp.route("/file/<task_id>")
def export_file(task_id):
    """Download the completed export file. Schedules temp file deletion after send."""
    task = _export_tasks.get(task_id)
    if not task:
        return jsonify({'error': 'not found'}), 404
    if task.get('status') != 'complete':
        return jsonify({'error': 'not ready', 'status': task.get('status')}), 400
    tmp_path = task.get('tmp_path')
    filename = task.get('filename', 'export.cbz')
    if not tmp_path or not Path(tmp_path).exists():
        return jsonify({'error': 'file missing'}), 404

    fmt = filename.rsplit('.', 1)[-1].lower()
    mime = 'application/x-cbr' if fmt == 'cbr' else 'application/zip'
    resp = send_file(tmp_path, mimetype=mime, as_attachment=True,
                     download_name=filename, max_age=0)

    # Mark consumed so it gets cleaned up on next request
    _set_export(task_id, {'status': 'downloaded'})
    return resp


@export_bp.route("/cancel/<task_id>", methods=["POST"])
def export_cancel(task_id):
    """Request cancellation of a running/queued export."""
    task = _export_tasks.get(task_id)
    if not task:
        return jsonify({'error': 'not found'}), 404
    status = task.get('status')
    if status in ('complete', 'error', 'cancelled'):
        return jsonify({'ok': True, 'status': status})
    _cancel_flags.add(task_id)
    return jsonify({'ok': True, 'status': 'cancelling'})


@export_bp.route("/task/<task_id>", methods=["DELETE"])
def export_dismiss(task_id):
    """Dismiss a finished export task from the queue and delete its temp file."""
    with _export_lock:
        task = _export_tasks.pop(task_id, None)
    if task and task.get('tmp_path'):
        try:
            Path(task['tmp_path']).unlink(missing_ok=True)
        except Exception:
            pass
    return jsonify({'ok': True})
