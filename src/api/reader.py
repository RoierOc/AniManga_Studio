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
    """Las entradas recientes. El histórico completo vive archivado por meses.

    Igual que el de anime, esto truncaba a 500 y por tanto BORRABA lo más antiguo en cada
    escritura. Ahora `history_store` lo archiva en `reading_history_archive/YYYY-MM.json`.
    """
    from api import history_store
    return history_store.read_live('reading')

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

    from api import history_store
    history_store.append('reading', {
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
    }, dedup_keys=('title', 'chapter'), dedup_window=0, dedup_scan=0)
    return jsonify({'success': True})

@reader_bp.route('/history/clear', methods=['POST'])
def clear_history():
    from api import history_store
    history_store.clear('reading')
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


# ── Lápidas (tombstones) ─────────────────────────────────────────────────────
# Sin esto, la unión de `read` es una trampa en cuanto hay DOS dispositivos: desmarcas un
# capítulo en el móvil, el PC todavía lo tiene marcado, y en la siguiente fusión vuelve. Un
# borrado tiene que viajar igual que una lectura, así que se guarda como dato en vez de
# desaparecer: `del: {capítulo: ts}` para capítulos, y la obra entera se sustituye por
# `{deleted_ts: ts}` en lugar de quitar la clave.
#
# Guardar la lápida DENTRO de la propia entrada (y no en un mapa aparte tipo `_deleted`) evita
# inventar un espacio de nombres que podría chocar con una obra que se llame igual.
#
# la resolución es el `ts` de la OBRA, no el del capítulo — los marcados legados son
# `true` y no llevan hora propia. Basta para el caso real (desmarcar y volver a marcar), y si
# algún día hiciera falta más fino, el sitio es guardar la hora en `read[cap]` en vez de `true`.

_LAPIDA_TTL_MS = 90 * 24 * 3600 * 1000   # pasado ese tiempo, todos los dispositivos se enteraron


def es_lapida(entry) -> bool:
    """Una obra borrada: sólo lleva la hora del borrado, ningún progreso."""
    return isinstance(entry, dict) and 'deleted_ts' in entry and not entry.get('read')


def podar_lapidas(data: dict, ahora_ms: int = 0) -> dict:
    """Quita las lápidas ya caducadas para que el fichero no crezca sin fin."""
    ahora = ahora_ms or int(time.time() * 1000)
    fuera = []
    for title, entry in (data or {}).items():
        if not isinstance(entry, dict):
            continue
        if es_lapida(entry) and ahora - int(entry.get('deleted_ts') or 0) > _LAPIDA_TTL_MS:
            fuera.append(title)
            continue
        dels = entry.get('del')
        if isinstance(dels, dict):
            vivas = {c: t for c, t in dels.items() if ahora - int(t or 0) <= _LAPIDA_TTL_MS}
            if vivas:
                entry['del'] = vivas
            else:
                entry.pop('del', None)
    for t in fuera:
        data.pop(t, None)
    return data


def merge_progress(base: dict, incoming: dict) -> dict:
    """Funde dos progresos SIN destruir (mismo criterio que `backup.apply_payload`).

    - `read` (capítulos leídos) se UNE: marcar leído es acumulativo, y perder una marca
      por sincronizar es peor que conservar una de más.
    - La POSICIÓN (lastChapter/lastPage/...) es la del `ts` más reciente: ahí sí manda
      quién leyó después, o al reanudar volverías a un punto viejo.
    - …salvo que haya una LÁPIDA más nueva: un borrado explícito gana a una marca vieja, o
      desmarcar un capítulo no serviría de nada con dos dispositivos.
    """
    out = {k: dict(v) for k, v in (base or {}).items() if isinstance(v, dict)}
    for title, inc in (incoming or {}).items():
        if not isinstance(inc, dict):
            continue
        cur = out.get(title)
        if not cur:
            out[title] = dict(inc)
            continue

        # Obra borrada en algún lado: gana el borrado, salvo que el otro lado haya SEGUIDO
        # leyendo después (entonces la resucita: leer es una intención más reciente que borrar).
        borrado = max(int(cur.get('deleted_ts') or 0), int(inc.get('deleted_ts') or 0))
        if borrado:
            actividad = max(int(cur.get('ts') or 0), int(inc.get('ts') or 0))
            if borrado >= actividad:
                out[title] = {'deleted_ts': borrado}
                continue

        merged = dict(cur)
        merged.pop('deleted_ts', None)
        merged['read'] = {**(cur.get('read') or {}), **(inc.get('read') or {})}

        # Lápidas de capítulo: se quedan las dos, con la hora más alta de cada una.
        dels = dict(cur.get('del') or {})
        for c, t in (inc.get('del') or {}).items():
            dels[str(c)] = max(int(t or 0), int(dels.get(str(c)) or 0))

        if int(inc.get('ts') or 0) >= int(cur.get('ts') or 0):
            for k, v in inc.items():
                if k not in ('read', 'del'):
                    merged[k] = v

        # Aplicar las lápidas de capítulo: cae la marca de leído si el borrado es al menos tan
        # nuevo como la última actividad del lado que lo tenía marcado.
        for c, t in list(dels.items()):
            lado = inc if c in (inc.get('read') or {}) else cur
            if int(t or 0) >= int(lado.get('ts') or 0):
                merged['read'].pop(c, None)
            else:
                dels.pop(c)   # se volvió a leer después: la lápida ya no pinta nada

        if dels:
            merged['del'] = dels
        else:
            merged.pop('del', None)
        out[title] = merged
    return podar_lapidas(out)


def sin_lapidas(data: dict) -> dict:
    """Lo que se le enseña al cliente. Las lápidas son fontanería de sincronización: si viajaran
    a la interfaz, una obra borrada seguiría apareciendo (vacía) en «continuar leyendo»."""
    return {k: v for k, v in (data or {}).items() if not es_lapida(v)}


@reader_bp.route('/progress')
def get_progress():
    return jsonify(sin_lapidas(_progress_read()))


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
    return jsonify(sin_lapidas(merged))


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
    ahora = int(time.time() * 1000)

    # Se deja LÁPIDA en vez de quitar la clave: si desapareciera sin más, el otro dispositivo
    # seguiría teniéndolo y la siguiente fusión lo devolvería. Ver `merge_progress`.
    for t in (body.get('titles') or []):
        data[str(t)] = {'deleted_ts': ahora}

    for t, chapters in (body.get('chapters') or {}).items():
        entry = data.get(str(t))
        if not isinstance(entry, dict):
            continue
        lapidas = dict(entry.get('del') or {})
        for c in (chapters or []):
            (entry.get('read') or {}).pop(str(c), None)
            lapidas[str(c)] = ahora
            # Si era el capítulo por el que ibas, la posición deja de tener sentido.
            if str(entry.get('lastChapter')) == str(c):
                for k in ('lastChapter', 'lastPage', 'lastTotal', 'ts',
                          'lastKind', 'lastRef', 'lastSource'):
                    entry.pop(k, None)
        if lapidas:
            entry['del'] = lapidas
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

    # Páginas del capítulo FUNDIDAS entre discos: leer no debe notar que la obra está
    # repartida. La URL sigue siendo `<obra>/<página>` (sin disco) y `app.serve_upload` la
    # resuelve contra todas las raíces. Ver `api/roots.py`.
    from api.roots import series_pages
    up_pages = series_pages(actual_folder, upscaled=True, prefix=chapter_prefix)
    orig_pages = series_pages(actual_folder, prefix=chapter_prefix)

    if source == 'original':
        chosen, resolved = orig_pages, 'original'
    elif source == 'upscaled':
        chosen, resolved = up_pages, 'upscaled'
    elif up_pages:
        # Auto: escalado sólo si ESTE capítulo tiene páginas ahí
        chosen, resolved = up_pages, 'upscaled'
    else:
        chosen, resolved = orig_pages, 'original'

    if not chosen:
        return jsonify({'pages': [], 'error': 'Folder not found'})

    pages = sorted(chosen)
    # Cache-bust por mtime: las páginas se sirven con max_age de 7 días ("inmutables"), pero la
    # TRADUCCIÓN reescribe el archivo MANTENIENDO el nombre -> el navegador seguía mostrando la
    # versión vieja (inglés) de su caché tras re-traducir. Anexar ?v=<mtime> cambia la URL sólo
    # cuando el contenido cambia (re-traducción/upscale) y la deja cacheable si no. Los consumidores
    # del path (pageUrl, qaFlagPage con split('?')) ya toleran el query.
    out = []
    for p in pages:
        try:
            v = int(chosen[p].stat().st_mtime)
            out.append(f"{actual_folder}/{p}?v={v}")
        except OSError:
            out.append(f"{actual_folder}/{p}")
    return jsonify({'pages': out, 'folder': actual_folder, 'source': resolved})


@reader_bp.route('/random')
def random_chapter():
    all_chapters = []
    
    from api.roots import series_pages, series_titles

    for name, dirs in series_titles().items():
        if any(next(d.glob('*.jpg'), None) or next(d.glob('*.png'), None) for d in dirs):
            all_chapters.append(name)

    if not all_chapters:
        return jsonify({'error': 'No manga in library'})

    import random
    title = random.choice(all_chapters)

    images = sorted(series_pages(title))
    if images:
        ch = images[0].split('_')[0][2:]
    else:
        ch = '001'
    
    return jsonify({'title': title, 'chapter': ch})