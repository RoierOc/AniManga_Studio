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
import os
import re
import shutil
import subprocess
import xml.etree.ElementTree as ET
import tempfile
import threading
import time
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote
from decimal import Decimal, InvalidOperation
from pathlib import Path

import numpy as np
from flask import Blueprint, jsonify, request, send_file
from PIL import Image

from api.runtime import manga_dir, upscaled_dir, normalize_chapter, cache_get, cache_set, DATA_ROOT
from api.observability import record_error
from api.config_store import get_prefs
from api.platform import is_wsl, is_macos

_COLOR_TTL = 30 * 86400   # la firma de contenido en la clave auto-invalida; el TTL solo poda viejos

# ── Async export task tracking ────────────────────────────────────────────────
_export_tasks: dict = {}          # task_id → task dict
_export_lock = threading.Lock()
_export_semaphore = threading.Semaphore(3)  # max 3 concurrent exports
_EXPORT_TTL = 7200                # clean up temp files after 2 hours
_cancel_flags: set = set()        # task_ids requested to be cancelled

# Hilos que codifican páginas en paralelo. Tope de 4 aunque haya 12 núcleos: una página 4K de
# 44 MP ocupa ~44 MB descomprimida, y el resto de la máquina (MPV, el worker de la GPU) tiene que
# seguir usable mientras se arma un tomo. `EXPORT_WORKERS` lo sube o baja sin tocar código.
_ENC_WORKERS = max(1, int(os.environ.get('EXPORT_WORKERS') or min(4, os.cpu_count() or 4)))


def _export_tmp_root() -> Path:
    """Dónde se arma el .cbz mientras se genera. NUNCA en `/tmp`: en WSL es tmpfs = RAM, y un tomo
    de 4K en WebP q100 pesa >1 GB — con tres exportaciones a la vez se llena la RAM y el zip muere
    con ENOSPC (visto: 4,8 GB de tomos huérfanos en tmpfs). Mismo criterio que el caché de streaming.
    `EXPORT_TMP_DIR` lo cambia."""
    override = os.environ.get('EXPORT_TMP_DIR')
    if override:
        return Path(override).expanduser()
    root = Path(__file__).resolve().parents[2] / 'data' / '_export_tmp'
    if str(root).startswith(('/mnt/', '\\\\')):        # DrvFS: lento para escribir GBs
        for cand in (Path('/var/tmp'), Path(tempfile.gettempdir())):
            if cand.is_dir() and os.access(cand, os.W_OK):
                return cand / 'animanga_export'
    return root


_EXPORT_TMP = _export_tmp_root()
# Tomos huérfanos de arranques anteriores: el archivo vive hasta que el navegador lo descarga, así
# que un cierre a medias los deja para siempre. Al arrancar no hay ninguna tarea viva que los use.
shutil.rmtree(_EXPORT_TMP, ignore_errors=True)
_EXPORT_TMP.mkdir(parents=True, exist_ok=True)

# UN pool de codificación para TODAS las exportaciones, no uno por tarea: con 3 tomos a la vez,
# tres pools de 4 hilos saturarían la máquina y cada uno iría más lento que si fueran en fila.
# Compartido, el trabajo se reparte y el total de CPU en vuelo sigue acotado.
_enc_pool = ThreadPoolExecutor(max_workers=_ENC_WORKERS, thread_name_prefix='export-enc')

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
    around the hard edges of overlaid text the way JPEG does.

    `method=4`, NO `method=6`. MEDIDO sobre una página 4K real de 5268x8400 (Ao no Hako, 44 MP):
    m6 = 31.168 ms / 14.147 KB, m4 = 9.883 ms / 14.277 KB. El nivel más lento tarda **3,4x más
    para ahorrar un 0,9 %** — en un tomo de 60 páginas eso son 21 minutos regalados. Sobre páginas
    normales (2248x3200) la relación es la misma (1097 ms vs 463 ms, 1,5 % de diferencia)."""
    img = _normalize_mode(Image.open(img_path))
    if downscale > 1:
        img = img.resize((max(1, img.width // downscale), max(1, img.height // downscale)), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format='WEBP', quality=quality, method=4)
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
    # El tomo se arma con la obra ENTERA aunque esté repartida entre discos: `series_pages`
    # funde las raíces, así que exportar caps 1-10 de C: y 11-20 de D: da UN cbz correcto.
    # Sin esto, exportar veía media obra y el tomo salía con huecos, sin avisar.
    from api.roots import series_pages
    up_all = series_pages(title, upscaled=True)
    result = []
    exclude = exclude_pages or set()

    for ch in sorted(chapters, key=_sort_key):
        ch_norm = normalize_chapter(ch)
        prefix = _chapter_prefix(ch_norm)

        orig_files = sorted(series_pages(title, prefix=prefix).values(), key=lambda p: p.name)

        for orig in orig_files:
            if orig.name in exclude:
                continue
            # La escalada puede estar en OTRO disco que su original (no debería, pero un disco
            # que se quitó a media faena lo deja así): se busca por nombre, no por carpeta.
            chosen = up_all.get(orig.name) or up_all.get(orig.stem + ".jpg") or orig
            if chosen.exists():
                page_id = orig.stem.split('_', 1)[-1]
                arcname = f"Ch{ch_norm.zfill(4)}_{page_id}.{arc_ext}"
                result.append((arcname, chosen))

    return result


def _cover_entry(raw: bytes) -> tuple:
    """(arcname, bytes) de la portada, SIN reencodar si ya es un formato que el lector abre.

    Antes toda portada JPEG pasaba por `_encode_jpeg_bytes(raw, 95)` «para normalizar»: una
    generación de pérdida gratis sobre una página a color de 4K. Ahora sólo se reencoda cuando hace
    falta de verdad.

    El formato se detecta con PIL, NO por magic a mano: la versión anterior asumía «si no es PNG,
    es JPEG», así que una portada **WebP** se habría guardado como `000_cover.jpg` con bytes WebP
    dentro — un fichero que miente sobre lo que es. Con extensión correcta, WebP va bien en los
    lectores modernos (los mismos que ya aceptan páginas WebP); lo que rompe es la extensión falsa.
    """
    fmt = ''
    exif_rot = False
    try:
        with Image.open(io.BytesIO(raw)) as im:
            fmt = (im.format or '').lower()
            # Orientación EXIF ≠ 1 sí obliga a reescribir: si no, la portada sale girada en los
            # lectores que ignoran el EXIF.
            exif_rot = bool((im.getexif() or {}).get(274, 1) not in (1, None))
    except Exception:
        fmt = ''
    if fmt in ('jpeg', 'png', 'webp') and not exif_rot:
        return f"000_cover.{'jpg' if fmt == 'jpeg' else fmt}", raw
    return "000_cover.jpg", _encode_jpeg_bytes(raw, 95)


def _comicinfo_xml(title: str, volume_name: str, chapters: list, page_count: int, meta: dict) -> bytes:
    """ComicInfo.xml — el estándar de facto que leen Komga, Kavita, Tachiyomi/Mihon y Panels.

    Sin él, un CBZ entra en cualquier lector como «un archivo llamado X»: sin serie, sin número de
    tomo, sin autor, y ordenado alfabéticamente (Tomo 10 antes que Tomo 2). Con él, los tomos de
    una obra se agrupan solos y en orden.

    Sólo se escriben los campos que SE SABEN: un ComicInfo con `Writer` vacío es peor que sin
    campo, porque el lector deja de buscarlo por otro lado."""
    meta = meta or {}
    # El número de tomo: el que manda el front, o el último número del nombre («… - Tomo 12»).
    num = str(meta.get('volume') or '').strip()
    if not num:
        m = re.findall(r'(\d+)', volume_name)
        num = m[-1] if m else ''

    fields = [
        ('Series', meta.get('series') or title),
        ('Title', volume_name),
        ('Number', num),
        ('Volume', num),
        ('Count', str(meta.get('count') or '') if meta.get('count') else ''),
        ('Summary', (meta.get('summary') or '').strip()),
        ('Writer', ', '.join(meta.get('authors') or []) if isinstance(meta.get('authors'), list) else (meta.get('authors') or '')),
        ('Genre', ', '.join(meta.get('genres') or []) if isinstance(meta.get('genres'), list) else (meta.get('genres') or '')),
        ('Year', str(meta.get('year') or '')),
        ('Web', meta.get('web') or ''),
        ('PageCount', str(page_count)),
        ('LanguageISO', meta.get('language') or ''),
        # Yes = es manga. No se afirma la dirección de lectura: esta biblioteca también tiene
        # webtoons, y decirle a un lector «derecha a izquierda» cuando no lo es se nota mucho.
        ('Manga', 'Yes'),
        ('Notes', f'Generado por AniManga Studio · capítulos {_ch_range(chapters)}'),
    ]
    root = ET.Element('ComicInfo', {
        'xmlns:xsi': 'http://www.w3.org/2001/XMLSchema-instance',
        'xmlns:xsd': 'http://www.w3.org/2001/XMLSchema',
    })
    for k, v in fields:
        if v is None or str(v).strip() == '':
            continue
        ET.SubElement(root, k).text = str(v)
    return b'<?xml version="1.0" encoding="utf-8"?>\n' + ET.tostring(root, encoding='utf-8')


def _ch_range(chapters: list) -> str:
    """«1-5» o «1, 3, 7» — para que el CBZ diga QUÉ trae sin abrirlo."""
    ch = [str(c) for c in chapters]
    if not ch:
        return '—'
    if len(ch) == 1:
        return ch[0]
    try:
        s = sorted(ch, key=lambda c: float(c))
    except ValueError:
        s = sorted(ch)
    return f'{s[0]}-{s[-1]}' if len(s) > 2 else ', '.join(s)


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
    # `FileReader.readAsDataURL` manda `data:image/jpeg;base64,…`. Los caracteres del prefijo
    # están casi todos en el alfabeto base64, así que `b64decode` NO falla: devuelve bytes
    # corridos, el magic no casa, la reencodificación revienta y el `except` se come la portada
    # SIN decir nada (medido: el tomo salía sin portada al subirla desde archivo).
    if cover_b64.startswith("data:"):
        cover_b64 = cover_b64.split(",", 1)[-1]
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

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=f".{fmt}", dir=str(_EXPORT_TMP))
    try:
        with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_STORED) as zf:
            if cover_b64:
                try:
                    raw = base64.b64decode(cover_b64)
                    zf.writestr(*_cover_entry(raw))
                except Exception as e:
                    record_error('export', e, op='cover_b64', bytes=len(cover_b64))
            elif cover_path and Path(cover_path).exists():
                try:
                    zf.writestr("000_cover.jpg", _encode_jpeg(Path(cover_path), quality, downscale))
                except Exception:
                    zf.write(cover_path, "000_cover.jpg")

            # ComicInfo.xml antes de las páginas: algunos lectores dejan de leer entradas en
            # cuanto encuentran la primera imagen.
            try:
                zf.writestr('ComicInfo.xml', _comicinfo_xml(
                    title, volume_name, chapters, len(images), data.get('meta') or {}))
            except Exception as e:
                record_error('export', e, op='comicinfo', title=title)

            total_images = len(images)
            # Codificar en PARALELO, escribir en serie. Es 100 % CPU y Pillow suelta el GIL
            # mientras comprime, así que los hilos escalan de verdad; el zip lo sigue llenando
            # este hilo, en orden. Se va por TANDAS de `_ENC_WORKERS` (no un `map` sobre la lista
            # entera) porque cada página 4K descomprimida ocupa decenas de MB: la tanda acota la
            # memoria en vuelo y deja que `progress_cb` cancele con un retraso de una tanda.
            def _enc(item):
                arcname, img_path = item
                try:
                    return arcname, _encode_image(img_path, quality, downscale, codec), None
                except Exception:
                    return arcname, None, img_path   # el original se copia tal cual

            done = 0
            for i in range(0, total_images, _ENC_WORKERS):
                for arcname, blob, fallback in _enc_pool.map(_enc, images[i:i + _ENC_WORKERS]):
                    if blob is None:
                        zf.write(str(fallback), arcname)
                    else:
                        zf.writestr(arcname, blob)
                    done += 1
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
    # "Escalada" = está bajo ALGUNA raíz de escalados, no bajo una carpeta concreta.
    from api.roots import upscaled_roots
    _ups = tuple(str(r) for r in upscaled_roots())
    upscaled_count = sum(1 for _, p in images if str(p).startswith(_ups))
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

    color_pages = []

    for ch in sorted(chapters, key=_sort_key):
        ch_norm = normalize_chapter(ch)
        prefix = _chapter_prefix(ch_norm)

        from api.roots import series_pages as _sp
        files = sorted(_sp(title, prefix=prefix).values(), key=lambda p: p.name)

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


def tomos_dir() -> Path:
    """Carpeta donde aterrizan los tomos terminados. Por defecto, JUNTO a la biblioteca activa
    (`<raíz>/../Tomos`): el disco que ya tiene sitio para el manga lo tiene para sus tomos, y no
    en el .vhdx de WSL, que va justo de espacio. `export_dir` en prefs manda sobre esto."""
    pref = str((get_prefs().get('export_dir') or '')).strip()
    if pref:
        return Path(pref).expanduser()
    try:
        from api.roots import root_by_id, active_id
        r = root_by_id(active_id())
        if r and r.get('manga'):
            return Path(r['manga']).parent / 'Tomos'
    except Exception as e:
        record_error('export', e, op='tomos_dir')
    return Path(DATA_ROOT) / 'Tomos'


def _display_path(p) -> str:
    """`/mnt/d/Tomos` → `D:\\Tomos`. En la shell nativa el usuario vive en Windows: enseñarle una
    ruta de WSL para una carpeta que va a abrir en el Explorador no le sirve de nada."""
    s = str(p)
    if is_wsl() and s.startswith('/mnt/') and len(s) > 6 and s[6] == '/':
        return f"{s[5].upper()}:\\" + s[7:].replace('/', '\\')
    return s


def _unique_path(dest: Path) -> Path:
    """`Tomo 1.cbz` → `Tomo 1 (2).cbz` si ya existe. Reexportar no puede pisar en silencio el
    tomo anterior: quizá el de antes era el bueno."""
    if not dest.exists():
        return dest
    stem, suf = dest.stem, dest.suffix
    for n in range(2, 1000):
        cand = dest.with_name(f"{stem} ({n}){suf}")
        if not cand.exists():
            return cand
    return dest.with_name(f"{stem} ({uuid.uuid4().hex[:6]}){suf}")


@export_bp.route("/save/<task_id>", methods=["POST"])
def export_save(task_id):
    """Guarda el tomo terminado en la carpeta de tomos, SIN pasar por la descarga del navegador.
    El shell nativo enseña su propio panel de descargas de WebView2 cuando se usa un <a download>,
    y eso rompe la ilusión de app: el archivo ya está en disco, moverlo es todo lo que hace falta."""
    task = _export_tasks.get(task_id)
    if not task:
        return jsonify({'error': 'not found'}), 404
    if task.get('status') != 'complete':
        return jsonify({'error': 'not ready', 'status': task.get('status')}), 400
    tmp_path = task.get('tmp_path')
    if not tmp_path or not Path(tmp_path).exists():
        return jsonify({'error': 'file missing'}), 404
    try:
        # El destino lo decide el SERVIDOR, nunca el cuerpo de la petición. Aceptar un `dir` del
        # cliente convertía esto en una escritura de archivo en cualquier ruta: el servidor escucha
        # en localhost, y cualquier página abierta en el navegador puede hacerle POST. El front no
        # lo necesitaba — le devolvía al backend la ruta que el backend acababa de darle.
        dest_dir = tomos_dir()
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = _unique_path(dest_dir / (task.get('filename') or 'tomo.cbz'))
        # `move`, no `replace`: el temporal y el destino pueden estar en discos distintos
        # (data/ en el vhdx, los tomos en D:) y `os.replace` fallaría con EXDEV.
        shutil.move(tmp_path, str(dest))
    except Exception as e:
        record_error('export', e, op='save', task=task_id)
        return jsonify({'error': str(e)}), 500
    _set_export(task_id, {'status': 'downloaded', 'saved_path': str(dest)})
    return jsonify({'ok': True, 'path': str(dest), 'dir': str(dest.parent),
                    'name': dest.name, 'display': _display_path(dest.parent)})


@export_bp.route("/reveal", methods=["POST"])
def export_reveal():
    """Abre la carpeta de tomos en el explorador del sistema.

    SIN parámetros: la carpeta la decide el servidor. Con un `dir` del cliente esto era un «abre lo
    que te diga con el manejador del sistema» — y una ruta que empiece por `-` se cuela como
    OPCIÓN del comando. Por eso también va el `--`."""
    d = str(tomos_dir().resolve())
    try:
        Path(d).mkdir(parents=True, exist_ok=True)
        if is_wsl():
            # explorer.exe necesita la ruta en formato Windows; wslpath la traduce incluso para
            # rutas del propio WSL (\\wsl$\...).
            win = subprocess.run(['wslpath', '-w', '--', d], capture_output=True, text=True, timeout=5).stdout.strip()
            subprocess.Popen(['explorer.exe', win or d])
        else:
            subprocess.Popen(['open' if is_macos() else 'xdg-open', '--', d],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:
        record_error('export', e, op='reveal', dir=d)
        return jsonify({'error': str(e)}), 500
    return jsonify({'ok': True, 'dir': d})


@export_bp.route("/dir")
def export_dir_route():
    return jsonify({'dir': str(tomos_dir()), 'display': _display_path(tomos_dir())})


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
