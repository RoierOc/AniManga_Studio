#!/usr/bin/env python3
"""
Reader API - Read manga chapters
"""

from flask import Blueprint, jsonify, request, send_from_directory
from pathlib import Path
import json
import random
import time
from decimal import Decimal, InvalidOperation

from api.runtime import manga_dir, upscaled_dir, normalize_chapter, write_json_atomic


def _chapter_prefix(chapter):
    chapter_norm = normalize_chapter(chapter)
    try:
        value = Decimal(chapter_norm)
        int_part = int(value.to_integral_value(rounding='ROUND_FLOOR'))
        if '.' in chapter_norm:
            return f"ch{int_part:04d}.{chapter_norm.split('.')[-1]}_"
        return f"ch{int_part:04d}_"
    except (InvalidOperation, ValueError):
        return f"ch{chapter_norm}_"

reader_bp = Blueprint('reader', __name__)

# ── Reading history ──────────────────────────────────────────────────────

def _history_path():
    return Path(manga_dir()) / 'reading_history.json'

def _history_read():
    try:
        if _history_path().exists():
            return json.loads(_history_path().read_text(encoding='utf-8'))
    except Exception:
        pass
    return []

def _history_write(history):
    try:
        write_json_atomic(_history_path(), history[:500], indent=2, keep_backup=True)
    except Exception:
        pass

@reader_bp.route('/history')
def get_history():
    return jsonify(_history_read())

@reader_bp.route('/history/record', methods=['POST'])
def record_history():
    """Record a finished chapter (called by the frontend when a chapter is marked
    read). kind/chapter_ref let the frontend re-resolve online (MangaDex/source)
    chapters that have no local folder when the user wants to continue reading."""
    data = request.get_json() or {}
    title = data.get('title')
    chapter = data.get('chapter')
    if not title or chapter is None:
        return jsonify({'error': 'title and chapter required'}), 400

    history = _history_read()
    history = [h for h in history if not (h.get('title') == title and str(h.get('chapter')) == str(chapter))]
    history.insert(0, {
        'title': title,
        'chapter': chapter,
        'cover': data.get('cover', ''),
        'source': data.get('source', 'local'),
        'kind': data.get('kind', 'local'),
        'chapter_ref': data.get('chapter_ref'),
        # Objeto de fuente completo (sourceId/mangaId/lang) para re-resolver al reanudar desde el
        # panel de historial. El campo `source` de arriba es una cadena de DISPLAY ('online'); sin
        # esto, continuar un capítulo online desde el historial no tenía con qué re-resolver.
        'source_obj': data.get('source_obj'),
        'read_at': int(time.time()),
    })
    _history_write(history)
    return jsonify({'success': True})

@reader_bp.route('/history/clear', methods=['POST'])
def clear_history():
    _history_write([])
    return jsonify({'success': True})

# ── Progreso de lectura (qué capítulos has leído y por dónde vas) ─────────
# Vivía SÓLO en el localStorage del WebView, y no es una preferencia: es el mapa de
# capítulos leídos de toda la biblioteca más la página exacta de cada obra. Ni la copia
# semanal al repo ni "Recuperar biblioteca" lo traían, y limpiar el almacenamiento del
# navegador (o cambiar de máquina) lo borraba entero. El historial NO lo reconstruye:
# `reading_history.json` son los últimos 500 capítulos terminados, no el mapa por obra.
#
# El fichero es la copia DURABLE; el localStorage sigue siendo la copia local (respuesta
# instantánea y funciona sin backend). Se reconcilian fusionando, nunca pisando.

def _progress_path():
    return Path(manga_dir()) / 'manga_progress.json'


def _progress_read() -> dict:
    from api.runtime import read_json_safe
    d = read_json_safe(_progress_path(), default={}, component='reader')
    return d if isinstance(d, dict) else {}


def merge_progress(base: dict, incoming: dict) -> dict:
    """Funde dos progresos SIN destruir (mismo criterio que `backup.apply_payload`).

    - `read` (capítulos leídos) se UNE: marcar leído es acumulativo, y perder una marca
      por sincronizar es peor que conservar una de más.
    - La POSICIÓN (lastChapter/lastPage/...) es la del `ts` más reciente: ahí sí manda
      quién leyó después, o al reanudar volverías a un punto viejo.
    """
    out = {k: dict(v) for k, v in (base or {}).items() if isinstance(v, dict)}
    for title, inc in (incoming or {}).items():
        if not isinstance(inc, dict):
            continue
        cur = out.get(title)
        if not cur:
            out[title] = dict(inc)
            continue
        merged = dict(cur)
        merged['read'] = {**(cur.get('read') or {}), **(inc.get('read') or {})}
        if int(inc.get('ts') or 0) >= int(cur.get('ts') or 0):
            for k, v in inc.items():
                if k != 'read':
                    merged[k] = v
        out[title] = merged
    return out


@reader_bp.route('/progress')
def get_progress():
    return jsonify(_progress_read())


@reader_bp.route('/progress', methods=['POST'])
def put_progress():
    """Funde lo que manda el cliente con lo que hay en disco y devuelve el resultado.

    Fusionar (en vez de sustituir) es lo que hace seguro tener la app abierta en dos
    sitios y lo que permite restaurar en una máquina nueva sin perder lo de aquí.
    """
    incoming = request.get_json(silent=True)
    if not isinstance(incoming, dict):
        return jsonify({'error': 'se esperaba un objeto {obra: progreso}'}), 400
    merged = merge_progress(_progress_read(), incoming)
    write_json_atomic(_progress_path(), merged, indent=2, keep_backup=True)
    return jsonify(merged)


@reader_bp.route('/progress/forget', methods=['POST'])
def forget_progress():
    """Borra progreso A PROPÓSITO (desmarcar un capítulo, borrar una obra).

    Hace falta una ruta aparte porque la fusión de `/progress` UNE los capítulos leídos: sin
    esto, desmarcar un capítulo o borrar un manga volvería a aparecer en la siguiente
    sincronización, que es justo el fallo que hace que la gente deje de fiarse del sync.
    Un borrado es una intención explícita; una fusión, no.

    Cuerpo: {"titles": ["Obra"], "chapters": {"Obra": ["12", "13"]}}
    """
    body = request.get_json(silent=True) or {}
    data = _progress_read()
    for t in (body.get('titles') or []):
        data.pop(str(t), None)
    for t, chapters in (body.get('chapters') or {}).items():
        entry = data.get(str(t))
        if not isinstance(entry, dict):
            continue
        for c in (chapters or []):
            (entry.get('read') or {}).pop(str(c), None)
            # Si era el capítulo por el que ibas, la posición deja de tener sentido.
            if str(entry.get('lastChapter')) == str(c):
                for k in ('lastChapter', 'lastPage', 'lastTotal', 'ts',
                          'lastKind', 'lastRef', 'lastSource'):
                    entry.pop(k, None)
    write_json_atomic(_progress_path(), data, indent=2, keep_backup=True)
    return jsonify({'ok': True})


@reader_bp.route('/read_chapter', methods=['POST'])
def read_chapter():
    data = request.get_json()
    title = data.get('title')
    chapter = data.get('chapter')
    source = data.get('source', 'auto')  # 'original' | 'upscaled' | 'auto'

    if not title or not chapter:
        return jsonify({'error': 'title and chapter required'}), 400

    from api.library import find_manga_folder
    actual_folder = find_manga_folder(title)

    chapter_prefix = _chapter_prefix(chapter)

    def chapter_pages(folder_path):
        pages = []
        for ext in ['jpg', 'png', 'webp']:
            pages.extend(sorted(f.name for f in folder_path.glob(f'{chapter_prefix}*.{ext}')))
        return sorted(pages)

    upscaled_folder = Path(upscaled_dir()) / actual_folder
    original_folder = Path(manga_dir()) / actual_folder

    if source == 'original':
        folder = original_folder
        resolved = 'original'
    elif source == 'upscaled':
        folder = upscaled_folder
        resolved = 'upscaled'
    else:
        # Auto: use upscaled only if this specific chapter has pages there
        if upscaled_folder.exists() and chapter_pages(upscaled_folder):
            folder = upscaled_folder
            resolved = 'upscaled'
        else:
            folder = original_folder
            resolved = 'original'

    if not folder.exists():
        return jsonify({'pages': [], 'error': 'Folder not found'})

    pages = chapter_pages(folder)
    # Cache-bust por mtime: las páginas se sirven con max_age de 7 días ("inmutables"), pero la
    # TRADUCCIÓN reescribe el archivo MANTENIENDO el nombre -> el navegador seguía mostrando la
    # versión vieja (inglés) de su caché tras re-traducir. Anexar ?v=<mtime> cambia la URL sólo
    # cuando el contenido cambia (re-traducción/upscale) y la deja cacheable si no. Los consumidores
    # del path (pageUrl, qaFlagPage con split('?')) ya toleran el query.
    out = []
    for p in pages:
        try:
            v = int((folder / p).stat().st_mtime)
            out.append(f"{actual_folder}/{p}?v={v}")
        except OSError:
            out.append(f"{actual_folder}/{p}")
    return jsonify({'pages': out, 'folder': actual_folder, 'source': resolved})


@reader_bp.route('/random')
def random_chapter():
    all_chapters = []
    
    for folder in Path(manga_dir()).iterdir():
        if not folder.is_dir():
            continue
        
        images = list(folder.glob('*.jpg')) + list(folder.glob('*.png'))
        if images:
            all_chapters.append(folder.name)
    
    if not all_chapters:
        return jsonify({'error': 'No manga in library'})
    
    import random
    title = random.choice(all_chapters)
    
    images = list((Path(manga_dir()) / title).glob('*.jpg'))
    if images:
        ch = images[0].stem.split('_')[0][2:]
    else:
        ch = '001'
    
    return jsonify({'title': title, 'chapter': ch})