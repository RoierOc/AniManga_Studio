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

from api.runtime import MANGA_DIR, UPSCALED_DIR, QA_DIR

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


def _translated_count(title: str) -> int:
    meta = Path(MANGA_DIR) / title / ".transplant_meta.json"
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
    manga_root = Path(MANGA_DIR)
    up_root = Path(UPSCALED_DIR)

    series = {}  # name -> dict

    if manga_root.is_dir():
        for entry in os.scandir(manga_root):
            if not entry.is_dir(follow_symlinks=False) or entry.name.startswith("."):
                continue
            b, ch = _measure_series(Path(entry.path))
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
            b, ch = _measure_series(Path(entry.path))
            row = series.setdefault(entry.name, {
                "name": entry.name, "original_bytes": 0, "original_chapters": 0,
                "upscaled_bytes": 0, "upscaled_chapters": 0,
                "translated_chapters": _translated_count(entry.name),
            })
            row["upscaled_bytes"] = b
            row["upscaled_chapters"] = ch

    rows = sorted(
        series.values(),
        key=lambda r: r["original_bytes"] + r["upscaled_bytes"],
        reverse=True,
    )

    total_original = sum(r["original_bytes"] for r in rows)
    total_upscaled = sum(r["upscaled_bytes"] for r in rows)
    stream_cache = _tree_bytes(str(_stream_cache_root()))
    qa = _tree_bytes(str(QA_DIR))

    return jsonify({
        "series": rows,
        "totals": {
            "original": total_original,
            "upscaled": total_upscaled,
            "stream_cache": stream_cache,
            "qa": qa,
            "total": total_original + total_upscaled + stream_cache + qa,
        },
    })


@storage_bp.route("/purge", methods=["POST"])
def purge():
    """Limpiezas seguras. Body: {target, series?}.
      target='stream_cache' → vacía la caché de streaming (efímera).
      target='upscaled'     → borra la copia 4x (regenerable). series=<nombre>
                              para una sola, o omitido para TODAS."""
    body = request.get_json(silent=True) or {}
    target = (body.get("target") or "").strip()

    if target == "stream_cache":
        root = _stream_cache_root()
        freed = _tree_bytes(str(root))
        if root.is_dir():
            for child in root.iterdir():
                shutil.rmtree(child, ignore_errors=True) if child.is_dir() else child.unlink(missing_ok=True)
        return jsonify({"ok": True, "freed": freed})

    if target == "upscaled":
        up_root = Path(UPSCALED_DIR)
        series = (body.get("series") or "").strip()
        if series:
            path = _safe_child(up_root, series)
            if path is None or not path.is_dir():
                return jsonify({"error": "serie no encontrada"}), 404
            freed = _tree_bytes(str(path))
            shutil.rmtree(path, ignore_errors=True)
            return jsonify({"ok": True, "freed": freed})
        # todas
        freed = _tree_bytes(str(up_root))
        if up_root.is_dir():
            for child in up_root.iterdir():
                if child.is_dir() and not child.name.startswith("."):
                    shutil.rmtree(child, ignore_errors=True)
        return jsonify({"ok": True, "freed": freed})

    return jsonify({"error": "target inválido"}), 400
