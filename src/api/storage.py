#!/usr/bin/env python3
"""
Storage API — uso de disco por serie y limpiezas seguras.

Da visibilidad de lo que ocupa `data/` (que crece a ciegas con escalados 4x y
tomos) y permite recuperar espacio SIN tocar la fuente de verdad:
  - la copia escalada (UPSCALED_DIR) es regenerable → se puede borrar por serie
    o entera;
  - la caché de streaming (data/_stream_cache) es efímera → se regenera al
    reproducir.
Nunca se ofrece borrar los originales descargados desde aquí (irreversibles:
las páginas a color no se escalan, y el escalado es una reconstrucción, no el
original).
"""
import json as _json
import os
import re
import shutil
from pathlib import Path

# Capítulos = archivos planos ch{####}_{###}.ext dentro de la carpeta de la serie
# (no subcarpetas); el número de capítulo es el primer grupo.
_CH_RE = re.compile(r"ch(\d+)_\d+", re.IGNORECASE)

from flask import Blueprint, jsonify, request

from api.runtime import manga_dir, upscaled_dir, get_library_mode, QA_DIR
from api.index_db import cached_measure, prune

storage_bp = Blueprint("storage", __name__)


def _tree_bytes(path: str) -> int:
    total = 0
    try:
        for entry in os.scandir(path):
            try:
                if entry.is_dir(follow_symlinks=False):
                    total += _tree_bytes(entry.path)
                else:
                    total += entry.stat(follow_symlinks=False).st_size
            except OSError:
                pass
    except OSError:
        pass
    return total


def _measure_series(series_dir: Path):
    """(bytes, nº de capítulos) de una serie. Capítulo = prefijo ch#### distinto
    entre los archivos ch####_###.ext (planos en la carpeta de la serie)."""
    total = 0
    chapters = set()

    def _scan(path):
        nonlocal total
        try:
            for entry in os.scandir(path):
                try:
                    if entry.is_dir(follow_symlinks=False):
                        _scan(entry.path)  # tolera estructuras anidadas si las hubiera
                    else:
                        total += entry.stat(follow_symlinks=False).st_size
                        m = _CH_RE.match(entry.name)
                        if m:
                            chapters.add(m.group(1))
                except OSError:
                    pass
        except OSError:
            pass

    _scan(str(series_dir))
    return total, len(chapters)


_VIDEO_EXTS = {".mkv", ".mp4", ".avi", ".webm", ".mov", ".m4v"}


def _anime_dirs() -> dict:
    """Carpetas de series de anime a medir, REPARTIDAS EN VARIOS DISCOS.

    Fuente autoritativa = la BIBLIOTECA de anime: cada serie guarda su propio
    `local_path`, que puede estar en cualquier disco/partición (aquí D: y E:).
    Esto es lo que arregla el bug: antes se medía solo el `download_path` como una
    sola raíz, y encima sin convertir `D:\\`→`/mnt/d`, así que `scandir` fallaba y
    NO aparecía ningún anime.

    Deliberadamente NO se escanea el `download_path`/scan-paths a lo bruto: el
    usuario los tiene apuntando a RAÍCES DE DISCO enteras (`D:\\`, `E:\\`), donde
    conviven juegos, películas y descargas sueltas — listar sus subcarpetas metía
    Steam/Genshin/Downloads como si fueran anime e inflaba el total. La biblioteca
    da exactamente las series que el usuario reconoce como anime.

    `_lib_read` ya está namespaceada por modo (normal/oculto), así que iterarla no
    cruza ni delata la biblioteca oculta. Devuelve {nombre: Path}, deduplicado por
    ruta real; convierte rutas Windows a WSL cuando hace falta.
    """
    out: dict = {}
    seen: set = set()  # rutas ya añadidas (resueltas), para deduplicar

    try:
        from api.anime import _lib_read, _is_wsl, _win_to_wsl
    except Exception:
        return out

    def _to_local(raw: str) -> Path:
        raw = raw.strip()
        if _is_wsl() and re.match(r"^[A-Za-z]:[/\\]", raw):
            raw = _win_to_wsl(raw)
        return Path(raw)

    try:
        for anime in _lib_read().values():
            lp = (anime.get("local_path") or "").strip()
            if not lp:
                continue
            p = _to_local(lp)
            try:
                if not p.is_dir():
                    continue
                rp = str(p.resolve())
            except OSError:
                continue
            if rp in seen:
                continue
            seen.add(rp)
            # Si dos discos tuvieran una serie con el MISMO nombre de carpeta, se
            # desempata con un sufijo de ruta para no pisarse. Raro, pero seguro.
            name = p.name
            if name in out:
                name = f"{name} ({p.parent.name})"
            out[name] = p
    except Exception:
        pass

    return out


def _measure_anime(series_dir: Path):
    """(bytes, nº de episodios) de una carpeta de anime."""
    total = 0
    episodes = 0
    def _scan(path):
        nonlocal total, episodes
        try:
            for entry in os.scandir(path):
                if entry.is_dir(follow_symlinks=False):
                    _scan(entry.path)
                elif entry.is_file(follow_symlinks=False):
                    total += entry.stat().st_size
                    if os.path.splitext(entry.name)[1].lower() in _VIDEO_EXTS:
                        episodes += 1
        except OSError:
            pass
    _scan(series_dir)
    return total, episodes


def _translated_count(title: str) -> int:
    meta = Path(manga_dir()) / title / ".transplant_meta.json"
    if not meta.exists():
        return 0
    try:
        data = _json.loads(meta.read_text())
        return len(data.get("translated", []) or [])
    except Exception:
        return 0


def _stream_cache_root() -> Path:
    # Importa perezoso: stream.py ya resolvió la raíz (con la guarda de WSL/drvfs).
    try:
        from api.stream import _SESS_ROOT
        return Path(_SESS_ROOT)
    except Exception:
        from api.runtime import DATA_ROOT
        return Path(DATA_ROOT) / "_stream_cache"


def _safe_child(root: Path, name: str):
    """Resuelve root/name y confirma que queda DENTRO de root (anti path-traversal).
    Devuelve el Path o None si el nombre escapa del árbol."""
    root = root.resolve()
    try:
        target = (root / name).resolve()
        target.relative_to(root)
    except (ValueError, OSError):
        return None
    if target == root:
        return None
    return target


@storage_bp.route("/summary", methods=["GET"])
def summary():
    """Desglose de disco: por serie (original vs escalado) + totales + cachés."""
    manga_root = Path(manga_dir())
    up_root = Path(upscaled_dir())

    # Namespacea las claves de caché por modo para que las mediciones de la
    # biblioteca oculta nunca se crucen (ni delaten su existencia) en la normal.
    _suf = '' if get_library_mode() == 'normal' else f':{get_library_mode()}'
    _k_manga, _k_up, _k_anime = f"manga{_suf}", f"upscaled{_suf}", f"anime{_suf}"

    series = {}  # name -> dict

    manga_names, up_names, anime_names = [], [], []

    if manga_root.is_dir():
        for entry in os.scandir(manga_root):
            if not entry.is_dir(follow_symlinks=False) or entry.name.startswith("."):
                continue
            manga_names.append(entry.name)
            b, ch, _ = cached_measure(_k_manga, entry.name, entry.path,
                                      lambda p: (*_measure_series(Path(p)), {}))
            series[entry.name] = {
                "name": entry.name,
                "original_bytes": b,
                "original_chapters": ch,
                "upscaled_bytes": 0,
                "upscaled_chapters": 0,
                "translated_chapters": _translated_count(entry.name),
            }

    if up_root.is_dir():
        for entry in os.scandir(up_root):
            if not entry.is_dir(follow_symlinks=False) or entry.name.startswith("."):
                continue
            up_names.append(entry.name)
            b, ch, _ = cached_measure(_k_up, entry.name, entry.path,
                                      lambda p: (*_measure_series(Path(p)), {}))
            row = series.setdefault(entry.name, {
                "name": entry.name, "original_bytes": 0, "original_chapters": 0,
                "upscaled_bytes": 0, "upscaled_chapters": 0,
                "translated_chapters": _translated_count(entry.name),
            })
            row["upscaled_bytes"] = b
            row["upscaled_chapters"] = ch

    for r in series.values():
        r["kind"] = "manga"

    # Anime: vídeos repartidos en VARIOS discos (download_path + local_path de cada
    # serie de la biblioteca), aparte de MANGA_DIR.
    total_anime = 0
    for name, adir in _anime_dirs().items():
        anime_names.append(name)
        b, _, extra = cached_measure(
            _k_anime, name, str(adir),
            lambda p: (lambda r: (r[0], 0, {"episodes": r[1]}))(_measure_anime(Path(p))),
        )
        eps = extra.get("episodes", 0)
        if b <= 0:
            continue
        total_anime += b
        series[f"\x00anime\x00{name}"] = {
            "name": name,
            "kind": "anime",
            "original_bytes": b,      # tamaño total de la serie (para orden/rowsize en la UI)
            "upscaled_bytes": 0,
            "original_chapters": 0,
            "upscaled_chapters": 0,
            "translated_chapters": 0,
            "episodes": eps,
        }

    # Drop cached rows for series that no longer exist on disk.
    prune(_k_manga, manga_names)
    prune(_k_up, up_names)
    prune(_k_anime, anime_names)

    rows = sorted(
        series.values(),
        key=lambda r: r["original_bytes"] + r["upscaled_bytes"],
        reverse=True,
    )

    total_original = sum(r["original_bytes"] for r in rows if r.get("kind") != "anime")
    total_upscaled = sum(r["upscaled_bytes"] for r in rows)
    stream_cache = _tree_bytes(str(_stream_cache_root()))
    qa = _tree_bytes(str(QA_DIR))

    disk = {}
    try:
        du = shutil.disk_usage(str(manga_root if manga_root.exists() else Path(manga_dir()).parent))
        disk = {"disk_total": du.total, "disk_free": du.free, "disk_used": du.used}
    except Exception:
        pass

    return jsonify({
        "series": rows,
        "totals": {
            "original": total_original,
            "upscaled": total_upscaled,
            "anime": total_anime,
            "stream_cache": stream_cache,
            "qa": qa,
            "total": total_original + total_upscaled + total_anime + stream_cache + qa,
            **disk,
        },
    })


@storage_bp.route("/series", methods=["GET"])
def series_size():
    """Desglose de disco de UNA serie (para el modal de la serie). ?title=<nombre>."""
    title = (request.args.get("title") or "").strip()
    if not title:
        return jsonify({"error": "falta title"}), 400
    orig = _safe_child(Path(manga_dir()), title)
    up = _safe_child(Path(upscaled_dir()), title)
    ob, oc = _measure_series(orig) if (orig and orig.is_dir()) else (0, 0)
    ub, uc = _measure_series(up) if (up and up.is_dir()) else (0, 0)
    return jsonify({
        "name": title,
        "original_bytes": ob, "original_chapters": oc,
        "upscaled_bytes": ub, "upscaled_chapters": uc,
        "translated_chapters": _translated_count(title),
    })


@storage_bp.route("/series/delete", methods=["POST"])
def series_delete():
    """Borra los archivos descargados de un manga para liberar espacio, como en el
    anime. Body: {title, scope}. scope:
      - 'upscaled' → solo la copia 4K (regenerable) — recomendado.
      - 'original' → solo los originales descargados.
      - 'all'      → ambos (deja la serie sin páginas en disco).
    El registro de la biblioteca/seguimiento NO se toca aquí; el usuario puede
    re-descargar/re-escalar. Devuelve los bytes liberados."""
    body = request.get_json(silent=True) or {}
    title = (body.get("title") or "").strip()
    scope = (body.get("scope") or "upscaled").strip()
    if not title:
        return jsonify({"error": "falta title"}), 400
    if scope not in ("upscaled", "original", "all"):
        return jsonify({"error": "scope inválido"}), 400

    targets = []
    if scope in ("upscaled", "all"):
        targets.append(_safe_child(Path(upscaled_dir()), title))
    if scope in ("original", "all"):
        targets.append(_safe_child(Path(manga_dir()), title))

    freed = 0
    for d in targets:
        if d and d.is_dir():
            freed += _tree_bytes(str(d))
            shutil.rmtree(d, ignore_errors=True)
    # Invalidar la medición cacheada por mtime (index_db) para que summary/series
    # reflejen el borrado sin esperar a un cambio de mtime.
    try:
        prune()
    except Exception:
        pass
    return jsonify({"ok": True, "freed": freed, "scope": scope})


@storage_bp.route("/purge", methods=["POST"])
def purge():
    """Limpieza segura. Body: {target='stream_cache'} → vacía la caché de
    streaming (efímera, se regenera al reproducir). NO se ofrece borrar el
    escalado 4K: es imprescindible para el comparador original/4K."""
    body = request.get_json(silent=True) or {}
    target = (body.get("target") or "").strip()

    if target == "stream_cache":
        root = _stream_cache_root()
        freed = _tree_bytes(str(root))
        if root.is_dir():
            for child in root.iterdir():
                shutil.rmtree(child, ignore_errors=True) if child.is_dir() else child.unlink(missing_ok=True)
        return jsonify({"ok": True, "freed": freed})

    return jsonify({"error": "target inválido"}), 400
