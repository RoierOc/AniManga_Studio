#!/usr/bin/env python3
"""
CBZ/CBR reader API — serves local comic archives from Windows Documents/Mangas.
CBZ/ZIP: Python zipfile (stdlib)
CBR/RAR: bsdtar subprocess
"""

from flask import Blueprint, jsonify, request, Response
from pathlib import Path
from urllib.parse import quote
import os
import zipfile
import subprocess
import threading

from api.platform import first_windows_user_dir
from api.auth import guardia_origen
from api.runtime import DATA_ROOT, read_json_safe, safe_child, write_json_atomic

cbz_bp = Blueprint("cbz", __name__)


def _default_docs_manga_dir() -> Path:
    """Documents/Mangas under the first real Windows user (WSL), else ~/Documents/Mangas."""
    user = first_windows_user_dir()
    if user:
        return user / "Documents" / "Mangas"
    return Path.home() / "Documents" / "Mangas"


DOCS_MANGA_DIR = Path(os.environ.get("CBZ_LIBRARY_DIR", str(_default_docs_manga_dir())))

_ARCHIVE_EXTS = {'.cbz', '.cbr', '.zip', '.rar'}
_IMAGE_EXTS   = {'.jpg', '.jpeg', '.png', '.webp', '.gif', '.avif'}
_MIME = {'.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.png': 'image/png',
         '.webp': 'image/webp', '.gif': 'image/gif', '.avif': 'image/avif'}
# one process-wide lock is enough for this single-user desktop state; use per-key or
# cross-process locking only if concurrent server instances become a supported mode.
_progress_lock = threading.Lock()


def _progress_file() -> Path:
    return DATA_ROOT / 'cbz_progress.json'


def _progress_read() -> dict:
    data = read_json_safe(_progress_file(), default={}, component='cbz')
    return data if isinstance(data, dict) else {}


def _is_image(name: str) -> bool:
    return Path(name).suffix.lower() in _IMAGE_EXTS


def _is_archive(p: Path) -> bool:
    return not p.is_symlink() and p.suffix.lower() in _ARCHIVE_EXTS and p.is_file()


def _list_entries(arc: Path) -> list[str]:
    ext = arc.suffix.lower()
    if ext in ('.cbz', '.zip'):
        with zipfile.ZipFile(arc) as z:
            names = [n for n in z.namelist() if _is_image(n) and not Path(n).name.startswith('.')]
    else:
        result = subprocess.run(['bsdtar', 'tf', str(arc)],
                                capture_output=True, text=True, timeout=15)
        names = [ln.strip() for ln in result.stdout.splitlines()
                 if _is_image(ln.strip()) and not Path(ln.strip()).name.startswith('.')]
    return sorted(names)


def _extract(arc: Path, entry: str) -> bytes:
    ext = arc.suffix.lower()
    if ext in ('.cbz', '.zip'):
        with zipfile.ZipFile(arc) as z:
            return z.read(entry)
    result = subprocess.run(['bsdtar', '-xOf', str(arc), entry],
                            capture_output=True, timeout=30)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.decode(errors='replace'))
    return result.stdout


def _safe_component(value: str) -> str | None:
    raw = str(value or '').strip()
    normalized = raw.replace('\\', '/')
    if not raw or normalized.startswith('/') or '/' in normalized or raw in ('.', '..'):
        return None
    return raw


def _safe_manga_dir(manga: str) -> Path | None:
    component = _safe_component(manga)
    if not component:
        return None
    return safe_child(DOCS_MANGA_DIR, component)


def _safe_arc(manga: str, volume: str) -> Path | None:
    manga_dir = _safe_manga_dir(manga)
    volume = _safe_component(volume)
    if not manga_dir or not manga_dir.is_dir() or not volume:
        return None
    archive = safe_child(manga_dir, volume)
    return archive if archive and archive.exists() and _is_archive(archive) else None


# ── Endpoints ─────────────────────────────────────────────────────────────────

@cbz_bp.route("/list")
def list_manga():
    if not DOCS_MANGA_DIR.exists():
        return jsonify({"error": "Documents/Mangas not found", "path": str(DOCS_MANGA_DIR)}), 404
    items = []
    for d in sorted(DOCS_MANGA_DIR.iterdir()):
        if not d.is_dir():
            continue
        vols = sorted([f for f in d.iterdir() if _is_archive(f)], key=lambda f: f.name)
        if not vols:
            continue
        items.append({
            "title": d.name,
            "volume_count": len(vols),
            "first_volume": vols[0].name,
        })
    return jsonify(items)


@cbz_bp.route("/volumes")
def list_volumes():
    manga = (request.args.get("manga") or "").strip()
    d = _safe_manga_dir(manga)
    if not d:
        return jsonify({"error": "invalid manga"}), 400
    if not d.is_dir():
        return jsonify({"error": "not found"}), 404
    vols = sorted([f for f in d.iterdir() if _is_archive(f)], key=lambda f: f.name)
    result = []
    progress = _progress_read().get(manga, {})
    if not isinstance(progress, dict):
        progress = {}
    for v in vols:
        try:
            count = len(_list_entries(v))
        except Exception:
            count = 0
        saved = progress.get(v.name, {})
        page = saved.get('page') if isinstance(saved, dict) else None
        valid = (isinstance(saved, dict) and saved.get('page_count') == count
                 and type(page) is int and 0 <= page < count)
        result.append({"name": v.name, "page_count": count, "ext": v.suffix.lower(),
                       "progress_page": page if valid else 0,
                       "in_progress": valid and not bool(saved.get('read')) if isinstance(saved, dict) else False,
                       "read": bool(saved.get('read')) and valid if isinstance(saved, dict) else False})
    return jsonify(result)


@cbz_bp.route("/progress", methods=["POST"])
def save_progress():
    blocked = guardia_origen()
    if blocked:
        return blocked
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify({"error": "se esperaba un objeto de progreso"}), 400

    manga = _safe_component(body.get('manga'))
    volume = _safe_component(body.get('volume'))
    if not manga or not volume:
        return jsonify({"error": "manga o volumen inválido"}), 400
    if _safe_arc(manga, volume) is None:
        return jsonify({"error": "volumen no encontrado"}), 404

    page = body.get('page')
    page_count = body.get('page_count')
    read = body.get('read', False)
    if (type(page) is not int or type(page_count) is not int or
            not 1 <= page_count <= 100_000 or not 0 <= page < page_count or
            type(read) is not bool):
        return jsonify({"error": "progreso inválido"}), 400

    # Un único archivo por biblioteca local; el nombre del manga y el tomo son
    # componentes, nunca rutas del sistema suministradas por el cliente.
    with _progress_lock:
        data = _progress_read()
        manga_progress = data.get(manga)
        if not isinstance(manga_progress, dict):
            manga_progress = {}
            data[manga] = manga_progress
        previous = manga_progress.get(volume)
        was_read = (isinstance(previous, dict) and previous.get('page_count') == page_count
                    and type(previous.get('page')) is int
                    and 0 <= previous['page'] < page_count and bool(previous.get('read')))
        manga_progress[volume] = {
            'page': page,
            'page_count': page_count,
            'read': was_read or read,
        }
        write_json_atomic(_progress_file(), data, indent=2, keep_backup=True)
    return jsonify({"success": True})


@cbz_bp.route("/pages")
def list_pages():
    manga  = (request.args.get("manga")  or "").strip()
    volume = (request.args.get("volume") or "").strip()
    arc = _safe_arc(manga, volume)
    if arc is None:
        return jsonify({"error": "not found"}), 404
    try:
        entries = _list_entries(arc)
        pages = [f"/api/cbz/page?manga={quote(manga)}&volume={quote(volume)}&idx={i}"
                 for i in range(len(entries))]
        return jsonify({"pages": pages, "count": len(pages)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@cbz_bp.route("/page")
def get_page():
    manga  = (request.args.get("manga")  or "").strip()
    volume = (request.args.get("volume") or "").strip()
    idx    = int(request.args.get("idx", 0))
    arc = _safe_arc(manga, volume)
    if arc is None:
        return "not found", 404
    try:
        entries = _list_entries(arc)
        if not (0 <= idx < len(entries)):
            return "index out of range", 404
        data = _extract(arc, entries[idx])
        mime = _MIME.get(Path(entries[idx]).suffix.lower(), 'image/jpeg')
        return Response(data, mimetype=mime,
                        headers={'Cache-Control': 'public, max-age=3600'})
    except Exception as e:
        return str(e), 500


@cbz_bp.route("/cover")
def get_cover():
    manga = (request.args.get("manga") or "").strip()
    d = _safe_manga_dir(manga)
    if not d:
        return "invalid", 400
    if not d.is_dir():
        return "not found", 404
    vols = sorted([f for f in d.iterdir() if _is_archive(f)], key=lambda f: f.name)
    if not vols:
        return "no volumes", 404
    try:
        entries = _list_entries(vols[0])
        if not entries:
            return "no pages", 404
        data = _extract(vols[0], entries[0])
        mime = _MIME.get(Path(entries[0]).suffix.lower(), 'image/jpeg')
        return Response(data, mimetype=mime,
                        headers={'Cache-Control': 'public, max-age=86400'})
    except Exception as e:
        return str(e), 500
