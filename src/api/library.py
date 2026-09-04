#!/usr/bin/env python3
"""
Library API - Manage local manga library
"""

from flask import Blueprint, jsonify, request, Response, send_file
from pathlib import Path
from decimal import Decimal, InvalidOperation
from urllib.parse import quote
import json
import os
from api.resilient_http import http as _http  # retry + backoff + per-host rate limiting
import threading

from api.runtime import (manga_dir, get_library_mode, normalize_chapter,
                         cache_get, cache_set, write_json_atomic)
from api.index_db import cached_measure, prune
from api.library_cache import SnapshotCache
from api.observability import record_error


def _count_pages(path):
    """(nº de páginas, nº de capítulos, {chs}) de una carpeta de serie. Capítulo = prefijo
    chXXXX distinto entre archivos ch####_###.ext. Cacheado por mtime en index_db.

    `chs` (los prefijos, no sólo cuántos) va en el extra porque una obra puede estar REPARTIDA
    entre discos: sumar los contadores de cada raíz contaría dos veces un capítulo con páginas
    en ambos. Con los prefijos, la unión es exacta. Ver `api/roots.py`."""
    p = Path(path)
    images = list(p.glob('*.png')) + list(p.glob('*.jpg')) + list(p.glob('*.webp'))
    chapters = set()
    for img in images:
        parts = img.stem.split('_')
        if parts and parts[0].startswith('ch'):
            chapters.add(parts[0])
    return len(images), len(chapters), {'chs': sorted(chapters)}


def _count_upscaled(path):
    return len(list(Path(path).glob('*.jpg'))), 0, {}

def _cover_cache_file() -> Path:
    return Path(manga_dir()) / '.cover_cache.json'

# Portadas: lado largo máximo. Se muestran a ~225-340px en las tarjetas/rail; 1000px da
# margen retina. Sin esto, elegir una PÁGINA HD como portada (p.ej. arte local 3024x4299,
# 5.7MB) guardaba ese archivo tal cual -> carga lentísima y la tarjeta se veía gigante.
MAX_COVER_DIM = 1000


def _normalize_cover_bytes(raw: bytes):
    """Decodifica, reescala si el lado largo excede MAX_COVER_DIM y recodifica a JPEG q90.
    Devuelve bytes JPEG, o None si no se puede decodificar (el caller cae a los bytes crudos)."""
    try:
        from io import BytesIO
        from PIL import Image
        im = Image.open(BytesIO(raw)); im.load()
        w, h = im.size
        if max(w, h) > MAX_COVER_DIM:
            s = MAX_COVER_DIM / float(max(w, h))
            im = im.resize((max(1, round(w * s)), max(1, round(h * s))), Image.LANCZOS)
        out = BytesIO(); im.convert('RGB').save(out, format='JPEG', quality=90)
        return out.getvalue()
    except Exception:
        return None


def _load_cover_cache() -> dict:
    try:
        f = _cover_cache_file()
        if f.exists():
            return json.loads(f.read_text(encoding='utf-8'))
    except Exception:
        pass
    return {}


_library_snapshot_revision = 0


def _save_cover_cache(cache: dict):
    global _library_snapshot_revision
    try:
        write_json_atomic(_cover_cache_file(), cache, durable=False)
        _library_snapshot_revision += 1
    except Exception:
        pass


# ── Portadas servidas por el backend (paridad con /api/img de anime) ──────────
# Las portadas de manga de fuentes Tachiyomi/Mihon eran URLs EN VIVO de Suwayomi
# (localhost:4567/...): fallaban con la JVM apagada, id caducado o arranque en frío. Ahora
# TODA portada sin archivo local se sirve por /api/library/thumb/<folder>, que la descarga
# UNA vez (despertando Suwayomi y re-resolviendo el id si hace falta), la normaliza y la
# persiste como cover.jpg local → resiliente, cacheada e instantánea a partir de entonces.
_thumb_locks: dict = {}
_thumb_locks_guard = threading.Lock()


def _thumb_lock(folder: str) -> threading.Lock:
    with _thumb_locks_guard:
        lk = _thumb_locks.get(folder)
        if lk is None:
            lk = _thumb_locks[folder] = threading.Lock()
        return lk


def _cover_file_response(folder: Path):
    """Sirve el cover.{jpg,png,webp} local si existe (normalizando en disco los heredados
    demasiado grandes). Devuelve un Response o None si no hay archivo local."""
    for ext in ('jpg', 'png', 'webp'):
        p = folder / f'cover.{ext}'
        if p.exists():
            data = p.read_bytes()
            if len(data) > 1_200_000:
                norm = _normalize_cover_bytes(data)
                if norm is not None and len(norm) < len(data):
                    for e in ('jpg', 'png', 'webp'):
                        (folder / f'cover.{e}').unlink(missing_ok=True)
                    (folder / 'cover.jpg').write_bytes(norm)
                    return Response(norm, mimetype='image/jpeg',
                                    headers={'Cache-Control': 'public, max-age=86400'})
            mime = {'jpg': 'image/jpeg', 'png': 'image/png', 'webp': 'image/webp'}[ext]
            return Response(data, mimetype=mime,
                            headers={'Cache-Control': 'public, max-age=86400'})
    return None


def _first_page_bytes(folder: Path):
    """Bytes de la PRIMERA página descargada (ch más bajo, página más baja). Portada de
    respaldo local cuando la fuente no da miniatura (fuente rota/offline/id caduco)."""
    imgs = [p for ext in ('jpg', 'jpeg', 'png', 'webp')
            for p in folder.glob(f'ch*_*.{ext}')]
    for p in sorted(imgs, key=lambda p: p.name):
        try:
            return p.read_bytes()
        except Exception:
            continue
    return None


def _try_fetch_bytes(url: str):
    try:
        r = _http.get(url, timeout=15)
        if r.status_code == 200 and r.content:
            return r.content
    except Exception:
        pass
    return None


def _remote_cover_url(title: str):
    """Mejor portada oficial remota para un título, MISMAS fuentes que "elegir portada":
    AniList (extraLarge, 1 oficial en alta) y, si no, la 1ª de MangaDex. Devuelve URL o None."""
    # AniList — portada oficial en alta resolución
    try:
        from api.anilist import _ql
        q = 'query($s:String){ Media(search:$s, type:MANGA){ coverImage{ extraLarge large } } }'
        d = _ql(q, {'s': title})
        ci = ((d or {}).get('Media') or {}).get('coverImage') or {}
        u = ci.get('extraLarge') or ci.get('large')
        if u:
            return u
    except Exception:
        pass
    # MangaDex — 1ª portada (resuelto por variantes de nombre, como el Tomo Builder)
    try:
        from api.mangadex import resolve_manga_by_title
        m = resolve_manga_by_title(title)
        mid = m['id'] if m else ''
        if mid:
            rc = _http.get('https://api.mangadex.org/cover',
                           params={'manga[]': mid, 'limit': 1, 'order[volume]': 'asc'}, timeout=10)
            if rc.ok:
                for it in rc.json().get('data', []):
                    fn = it.get('attributes', {}).get('fileName', '')
                    if fn:
                        return f"https://uploads.mangadex.org/covers/{mid}/{fn}"
    except Exception:
        pass
    return None


def _fetch_persist_cover(folder: Path) -> bool:
    """Descarga la portada de la fuente y la guarda como cover.jpg. Para URLs de Suwayomi:
    despierta la JVM y, si el mangaId está caducado, re-resuelve por título (reusa sources.py)
    y actualiza .source_meta.json. Devuelve True si se persistió un cover local."""
    meta = {}
    meta_path = folder / '.source_meta.json'
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text())
        except Exception:
            meta = {}
    url = (meta or {}).get('thumbnailUrl') or _load_cover_cache().get(folder.name.lower().strip())
    if not url or not str(url).startswith('http'):
        return False

    is_suwayomi = 'localhost:4567' in url or '127.0.0.1:4567' in url
    if is_suwayomi:
        try:
            from api.sources import ensure_suwayomi
            ensure_suwayomi()
        except Exception:
            pass

    raw = _try_fetch_bytes(url)
    # Suwayomi 404 → id caducado (DB reconstruida): re-resuelve por título y reintenta.
    if raw is None and is_suwayomi and meta.get('sourceId') and meta.get('title'):
        try:
            from api.sources import _resolve_source_manga_id, _persist_source_meta_id, SUWAYOMI_BASE
            # id 404 (fila purgada) → re-alta: strict=False permite el 1er resultado. La `url`
            # (si la hay) ancla el match exacto; el id nuevo se persiste (top-level Y pin).
            new_id, new_url, _st = _resolve_source_manga_id(str(meta['sourceId']), meta['title'],
                                                            meta.get('mangaUrl', ''), strict=False)
            if new_id and new_id != meta.get('mangaId'):
                _persist_source_meta_id(meta.get('mangaId'), new_id, meta['title'], new_url)
                raw = _try_fetch_bytes(f"{SUWAYOMI_BASE}/api/v1/manga/{new_id}/thumbnail")
        except Exception:
            pass
    # Recurso preferente cuando la fuente no da miniatura (fuente rota/offline/id caduco): la
    # portada OFICIAL de AniList/MangaDex (como "elegir portada"), mucho mejor que una página
    # interior en B/N. Solo si eso también falla, la 1ª página descargada como último recurso.
    if raw is None:
        remote = _remote_cover_url(meta.get('title') or folder.name)
        if remote:
            raw = _try_fetch_bytes(remote)
    if raw is None:
        raw = _first_page_bytes(folder)
    if raw is None:
        return False

    out = _normalize_cover_bytes(raw) or raw
    try:
        (folder / 'cover.jpg').write_bytes(out)
        return True
    except Exception:
        return False


def _chapter_sort_key(value):
    try:
        return float(Decimal(normalize_chapter(value)))
    except (InvalidOperation, ValueError):
        return -1.0

library_bp = Blueprint('library', __name__)

_LIBRARY_SNAPSHOT_TTL = 10.0
_library_snapshot = SnapshotCache(ttl=_LIBRARY_SNAPSHOT_TTL)


def _library_snapshot_key(mode: str):
    """Identidad de la foto sin hacer un stat por obra.

    Las rutas y el modo forman parte de la clave para que cambiar de raíz o de Biblioteca no
    reutilice datos ajenos. Los cambios dentro de una carpeta se detectan en el próximo ciclo de
    10 s; comprobar cada carpeta en cada petición reintroduciría el coste DrvFS que evitamos.
    """
    from api.roots import roots as _roots

    return (
        mode,
        _library_snapshot_revision,
        tuple((r.get('id'), r.get('manga'), r.get('upscaled')) for r in _roots()),
    )


def _scan_folders_in_mode(mode: str):
    """Ejecuta un refresco fuera de la petición conservando su modo de biblioteca."""
    from api.runtime import set_request_library_mode

    set_request_library_mode(mode)
    try:
        return _scan_folders_fresh()
    finally:
        set_request_library_mode(None)


def _on_library_refresh_error(error: Exception):
    record_error('library', error, op='scan_refresh')


def _scan_folders_fresh():
    # Build a title→cover lookup from local_library.json + disk cover cache
    lib_covers: dict = _load_cover_cache()
    lib_json = Path(manga_dir()) / 'local_library.json'
    if lib_json.exists():
        try:
            for entry in json.loads(lib_json.read_text()):
                title = (entry.get('title') or '').lower().strip()
                cover = entry.get('cover')
                if title and cover:
                    lib_covers[title] = cover
        except Exception:
            pass

    # Namespaced by modo para que las mediciones de la biblioteca oculta nunca se
    # crucen (ni delaten su existencia) en la caché de la biblioteca normal.
    _mode_suffix = '' if get_library_mode() == 'normal' else f':{get_library_mode()}'
    _libcount_key = f'libcount{_mode_suffix}'
    _libup_key = f'libup{_mode_suffix}'

    from api.roots import roots as _roots, series_titles

    folders = []
    # Una obra es su NOMBRE DE CARPETA, esté en el disco que esté: `series_titles()` funde las
    # raíces, así que la misma obra repartida entre C: y D: sale UNA vez con sus cuentas sumadas
    # (los capítulos por prefijo, para no contar dos veces uno con páginas en ambos discos).
    for name, dirs in series_titles().items():
        f = dirs[0]
        image_count = 0
        chapter_prefixes: set = set()
        upscaled_count = 0
        # Per-folder page/chapter counts are cached by mtime (index_db) so the whole
        # library listing stays instant instead of globbing every folder each call.
        # La clave lleva la raíz: dos discos con la misma obra son dos mediciones distintas.
        for r in _roots():
            d = Path(r['manga']) / name
            if d.is_dir():
                n, nch, extra = cached_measure(_libcount_key, f'{name}@{r["id"]}', str(d), _count_pages)
                image_count += n
                chs = (extra or {}).get('chs')
                chapter_prefixes |= set(chs) if chs else set()
                if not chs:
                    chapter_prefixes |= {f'?{r["id"]}:{i}' for i in range(nch)}  # fila vieja sin `chs`
            u = Path(r['upscaled']) / name
            if u.is_dir():
                nu, _, _ = cached_measure(_libup_key, f'{name}@{r["id"]}', str(u), _count_upscaled)
                upscaled_count += nu
        chapters_count = len(chapter_prefixes)

        source_meta = None
        for d in dirs:
            meta_path = d / '.source_meta.json'
            if meta_path.exists():
                try:
                    source_meta = json.loads(meta_path.read_text())
                    break
                except Exception:
                    pass

        # Estado de traducción por trasplante (badge "ES" en la tarjeta). Unión por NOMBRE de
        # capítulo: si la obra está repartida, cada disco lleva su propio meta.
        translated: set = set()
        for d in dirs:
            tp_path = d / '.transplant_meta.json'
            if tp_path.exists():
                try:
                    translated |= set(json.loads(tp_path.read_text()).get('translated', []))
                except Exception:
                    pass
        translated_count = len(translated)
        meta_path = f / '.source_meta.json'

        # Portada SIEMPRE por nuestro /thumb (sirve el cover local si existe; si no lo baja de la
        # fuente/AniList/MangaDex, lo persiste y lo cachea — resiliente a Suwayomi apagado/id
        # caduco), nunca la URL en vivo de Suwayomi. La URL lleva `?v=<mtime>` para que al CAMBIAR
        # la portada (reescribe cover.jpg → mtime nuevo) la URL cambie y el navegador recargue
        # (si no, con Cache-Control de 1 día seguiría mostrando la vieja aunque el archivo cambió).
        local_cover = next(
            (p for d in dirs for p in (d / 'cover.jpg', d / 'cover.png', d / 'cover.webp')
             if p.exists()),
            None
        )
        if local_cover or (source_meta or {}).get('thumbnailUrl') or lib_covers.get(name.lower().strip()):
            try:
                stamp_src = local_cover or (meta_path if meta_path.exists() else f)
                mtime = int(stamp_src.stat().st_mtime)
            except Exception:
                mtime = 0
            cover = f"/api/library/thumb/{quote(name, safe='')}?v={mtime}"
        else:
            cover = None

        folders.append({
            'id': name,
            'name': name,
            # Sólo cuando está repartida: la tarjeta pinta un aviso, y quien no reparte nada
            # no ve un campo de más.
            **({'split_roots': len(dirs)} if len(dirs) > 1 else {}),
            'chapter_count': chapters_count,
            'image_count': image_count,
            'page_count': image_count,
            'upscaled': upscaled_count,
            'cover': cover,
            'source_meta': source_meta,
            'translated_count': translated_count,
        })

    # Las claves del índice llevan `@raíz` (una medición por disco), así que podar con los
    # nombres pelados borraría TODAS las filas y forzaría un remedido en cada carga.
    _keys = [f'{x["name"]}@{r["id"]}' for x in folders for r in _roots()]
    prune(_libcount_key, _keys)
    prune(_libup_key, _keys)
    folders.sort(key=lambda x: x['name'].lower())
    return folders


def _scan_folders():
    """Devuelve la instantánea de Biblioteca, con refresco SWR cada 10 segundos."""
    mode = get_library_mode()
    key = _library_snapshot_key(mode)
    return _library_snapshot.get(
        key,
        _scan_folders_fresh,
        refresh_loader=lambda: _scan_folders_in_mode(mode),
        on_error=_on_library_refresh_error,
    )


@library_bp.route('')
def get_library():
    return jsonify(_scan_folders())


@library_bp.route('/overview')
def get_overview():
    """Biblioteca lista para pintar: disco + seguimiento ya cruzados.

    El emparejamiento (por identidad de fuente, por id, y sólo en último recurso por título)
    lo hacía el navegador con dos peticiones; ahora es una sola y la regla vive junto al resto
    de la lógica de identidad. Ver `library_overview.py`.
    """
    from api.library_overview import build_overview, safe_tracked
    from api.mangadex import load_local_library
    return jsonify(build_overview(_scan_folders(), safe_tracked(load_local_library)))


@library_bp.route('/cache_cover', methods=['POST'])
def cache_cover():
    """Save a cover URL discovered by the frontend into the disk cache."""
    data = request.get_json(silent=True) or {}
    title = (data.get('title') or '').strip().lower()
    url = (data.get('url') or '').strip()
    if not title or not url:
        return jsonify({'ok': False}), 400
    cache = _load_cover_cache()
    cache[title] = url
    _save_cover_cache(cache)
    return jsonify({'ok': True})


@library_bp.route('/search-covers')
def search_covers():
    """Search MangaDex for covers of library manga"""
    from api.resilient_http import http as http_requests

    from api.roots import series_titles
    results = {}
    for _name, _dirs in series_titles().items():
        folder = _dirs[0]

        # Skip if already has local cover
        local_covers = [p for d in _dirs for p in list(d.glob('cover.*')) + list(d.glob('folder.*'))]
        if local_covers:
            results[folder.name] = f"/uploads/{quote(folder.name, safe='')}/{local_covers[0].name}"
            continue
        
        # Search MangaDex for this manga
        try:
            r = http_requests.get('https://api.mangadex.org/manga', params={
                'title': folder.name,
                'limit': 1,
                'includes[]': 'cover_art'
            })
            data = r.json()
            items = data.get('data', [])
            if items:
                m = items[0]
                manga_id = m['id']
                
                # Get cover from relationships
                cover_url = None
                for rel in m.get('relationships', []):
                    if rel.get('type') == 'cover_art':
                        filename = rel.get('attributes', {}).get('fileName')
                        if filename:
                            # Use uploads.mangadex.org for direct access
                            # Original, NO la variante .256: esto se guarda como `cover.jpg` y es la portada
                            # definitiva de la obra. Se bajaban 256 px y el disco acabó con
                            # portadas de 175-256 px de ancho (mediana 371) que salen borrosas en
                            # cuanto la tarjeta pasa de ese tamaño. `_normalize_cover_bytes` ya la
                            # acota a MAX_COVER_DIM=1000 y la recodifica, así que el original no
                            # se guarda entero: se paga una vez al descargar, no en cada pintado.
                            cover_url = f"https://uploads.mangadex.org/covers/{manga_id}/{filename}"
                        break
                
                # Fallback to cover endpoint
                if not cover_url:
                    cover_response = http_requests.get('https://api.mangadex.org/cover', params={
                        'manga[]': manga_id,
                        'limit': 1
                    })
                    cover_data = cover_response.json()
                    cover_items = cover_data.get('data', [])
                    if cover_items:
                        filename = cover_items[0].get('attributes', {}).get('fileName')
                        if filename:
                            # Original, NO la variante .256: esto se guarda como `cover.jpg` y es la portada
                            # definitiva de la obra. Se bajaban 256 px y el disco acabó con
                            # portadas de 175-256 px de ancho (mediana 371) que salen borrosas en
                            # cuanto la tarjeta pasa de ese tamaño. `_normalize_cover_bytes` ya la
                            # acota a MAX_COVER_DIM=1000 y la recodifica, así que el original no
                            # se guarda entero: se paga una vez al descargar, no en cada pintado.
                            cover_url = f"https://uploads.mangadex.org/covers/{manga_id}/{filename}"
                
                if cover_url:
                    results[folder.name] = cover_url
        except Exception as e:
            print(f"Error searching cover for {folder.name}: {e}")
    
    return jsonify(results)

@library_bp.route('/chapter_status/<path:title>')
def get_chapter_status(title):
    return get_manga(title)

@library_bp.route('/<path:title>')
@library_bp.route('/chapters/<path:title>')
def get_manga(title):
    from api.roots import series_dirs, series_pages

    # La obra puede vivir en VARIOS discos: aquí se ve como una sola, con sus capítulos unidos.
    folders = series_dirs(title) or series_dirs(title.replace('_', ' '))
    if not folders:
        return jsonify({'error': 'Manga not found'}), 404
    name = folders[0].name

    # Capítulos: la lista de cada disco, fundida por número (un capítulo con páginas en dos
    # discos suma sus páginas, no aparece dos veces).
    by_ch: dict = {}
    for d in folders:
        for c in get_chapters_from_folder(d):
            ch = c['chapter']
            if ch in by_ch:
                by_ch[ch]['page_count'] = by_ch[ch].get('page_count', 0) + c.get('page_count', 0)
            else:
                by_ch[ch] = dict(c)
    chapters = list(by_ch.values())
    upscaled = {}

    # Páginas descargadas / escaladas por prefijo de capítulo, ya fundidas entre discos.
    dl_by_ch: dict = {}
    for fname in series_pages(name):
        parts = fname.rsplit('.', 1)[0].split('_')
        if parts and parts[0].startswith('ch'):
            dl_by_ch.setdefault(parts[0], set()).add(fname.rsplit('.', 1)[0])

    up_by_ch: dict = {}
    for fname in series_pages(name, upscaled=True):
        parts = fname.rsplit('.', 1)[0].split('_')
        if parts and parts[0].startswith('ch'):
            up_by_ch.setdefault(parts[0], set()).add(fname.rsplit('.', 1)[0])

    # True = fully upscaled, 'partial' = some pages missing
    for ch_key in set(dl_by_ch) | set(up_by_ch):
        dl_count = len(dl_by_ch.get(ch_key, set()))
        up_count = len(up_by_ch.get(ch_key, set()))
        ch_norm = normalize_chapter(ch_key)
        if up_count == 0:
            pass  # not upscaled at all — omit key
        elif dl_count > 0 and up_count < dl_count:
            upscaled[ch_norm] = 'partial'
        else:
            upscaled[ch_norm] = True
    
    # Los metadatos viven junto a las páginas, así que en una obra repartida hay uno por disco:
    # el primero que se lea manda para la identidad, y los capítulos traducidos se UNEN.
    source_meta = None
    transplant_meta = None
    for d in folders:
        meta_path = d / '.source_meta.json'
        if source_meta is None and meta_path.exists():
            try:
                source_meta = json.loads(meta_path.read_text())
            except Exception:
                pass
        tp_path = d / '.transplant_meta.json'
        if tp_path.exists():
            try:
                tp = json.loads(tp_path.read_text())
                if transplant_meta is None:
                    transplant_meta = tp
                else:
                    transplant_meta['translated'] = sorted(
                        set(transplant_meta.get('translated', [])) | set(tp.get('translated', [])))
            except Exception:
                pass

    return jsonify({
        'id': title,
        'name': title,
        'chapters': chapters,
        'upscaled': upscaled,
        'source_meta': source_meta,
        'transplant_meta': transplant_meta,
    })

def get_chapters_from_folder(folder):
    chapter_data = []
    
    # Los directorios OCULTOS (.original_art con el arte pre-traducción, etc.) son metadatos,
    # no capítulos: si contaran como subdirectorio, un manga de páginas planas se tomaría por
    # uno de carpeta-por-capítulo y su lista de capítulos saldría vacía.
    has_subdirs = any(f.is_dir() and not f.name.startswith('.') for f in folder.iterdir())
    has_files = any(f.is_file() for f in folder.glob('*'))
    
    if has_files and not has_subdirs:
        files = list(folder.glob('*.jpg')) + list(folder.glob('*.png')) + list(folder.glob('*.webp'))
        chapters_found = {}
        for f in files:
            parts = f.stem.split('_')
            if len(parts) > 0 and parts[0].startswith('ch'):
                ch = normalize_chapter(parts[0])
                if ch not in chapters_found:
                    chapters_found[ch] = []
                chapters_found[ch].append(f)
        
        for ch, pages in chapters_found.items():
            chapter_data.append({
                'chapter': ch,
                'page_count': len(pages)
            })
    else:
        for ch_folder in sorted(folder.iterdir(), reverse=True):
            if not ch_folder.is_dir() or ch_folder.name.startswith('.'):
                continue
            ch_name = ch_folder.name
            if ch_name.startswith('ch'):
                ch_num = normalize_chapter(ch_name)
            else:
                ch_num = normalize_chapter(ch_name)
            
            pages = list(ch_folder.glob('*.jpg')) + list(ch_folder.glob('*.png')) + list(ch_folder.glob('*.webp'))
            
            if pages:
                chapter_data.append({
                    'chapter': ch_num,
                    'page_count': len(pages)
                })
    
    chapter_data.sort(key=lambda x: _chapter_sort_key(x['chapter']), reverse=True)
    return chapter_data


@library_bp.route('/pages/<path:title>')
def get_chapter_pages(title):
    """Las páginas de un capítulo, EN ORDEN y como rutas de `/uploads/`.

    Existe por el cliente de Android: una obra descargada puede estar en dos formatos —páginas
    planas `ch0036_001.png` en la carpeta de la serie, o una subcarpeta por capítulo— y además
    repartida entre discos. Reproducir esa lógica en el móvil sería copiarla, copiarla mal y
    descubrirlo el día que abra una obra del otro formato. Aquí ya está resuelta.

    Devuelve rutas relativas para que el cliente sólo tenga que anteponer el host: `/uploads/`
    resuelve el disco por su cuenta y **da la versión escalada si existe**, que es justo lo que
    esta biblioteca tiene y ninguna fuente de internet puede dar.
    """
    from api.roots import series_dirs

    ch = normalize_chapter(request.args.get('chapter') or '')
    if not ch:
        return jsonify({'error': 'chapter required'}), 400

    folders = series_dirs(title) or series_dirs(title.replace('_', ' '))
    if not folders:
        return jsonify({'error': 'Manga not found'}), 404
    name = folders[0].name

    # ⚠️ `os.scandir` y **filtrando por el NOMBRE antes de tocar el disco**. La carpeta de una obra
    # larga tiene ~1900 ficheros y vive en /mnt/d (DrvFS): a ~0,7 ms por `stat`, preguntar
    # «¿es fichero?» por cada uno costaba 8 SEGUNDOS por capítulo, medido. El nombre ya dice la
    # extensión y el capítulo; sólo los treinta que quedan necesitan mirarse.
    pages = []
    for d in folders:
        try:
            entradas = list(os.scandir(d))
        except OSError:
            continue
        subdirs = [e for e in entradas if not e.name.startswith('.') and e.is_dir()]
        if subdirs:
            for sub in subdirs:
                if normalize_chapter(sub.name) != ch:
                    continue
                try:
                    hijos = os.scandir(sub.path)
                except OSError:
                    continue
                pages += [
                    f'{name}/{sub.name}/{e.name}' for e in hijos
                    if e.name.rsplit('.', 1)[-1].lower() in _PAGE_EXTS
                ]
        else:
            for e in entradas:
                nombre = e.name
                if nombre.rsplit('.', 1)[-1].lower() not in _PAGE_EXTS:
                    continue
                if normalize_chapter(nombre.rsplit('.', 1)[0].split('_')[0]) == ch:
                    pages.append(f'{name}/{nombre}')

    # Por nombre: las páginas van numeradas con ceros a la izquierda justo para esto. Sin ordenar,
    # `os.scandir` devuelve lo que le da el sistema de ficheros y el capítulo se lee desordenado.
    pages.sort()
    if not pages:
        # «No hay ese capítulo» tiene que distinguirse de «el capítulo está vacío», que aquí no
        # existe: una lista vacía con 200 haría que el lector se abriera en blanco sin decir nada.
        return jsonify({'error': 'chapter not found'}), 404
    return jsonify({'title': name, 'chapter': ch, 'pages': pages})


_PAGE_EXTS = ('jpg', 'jpeg', 'png', 'webp')


@library_bp.route('/chapter_health/<path:title>')
def chapter_health(title):
    """Scan a manga's chapters for upscale completeness and download gaps.
    Returns per-chapter status so the UI can show repair indicators.
    """
    from api.roots import series_dirs, series_pages
    actual = find_manga_folder(title)

    if not series_dirs(actual):
        return jsonify({'error': 'not found'}), 404

    # Páginas por prefijo de capítulo, unidas entre discos: una obra repartida NO puede
    # reportar "falta el escalado" sólo porque esas páginas están en el otro disco.
    def _by_chapter(up: bool) -> dict:
        out: dict = {}
        for fname in series_pages(actual, upscaled=up):
            stem = fname.rsplit('.', 1)[0]
            parts = stem.split('_')
            if parts and parts[0].startswith('ch'):
                out.setdefault(parts[0], set()).add(stem)
        return out

    dl_by_ch = _by_chapter(False)
    up_by_ch = _by_chapter(True)

    all_chapters = sorted(set(dl_by_ch) | set(up_by_ch))
    result = []

    for ch_key in all_chapters:
        dl_stems = dl_by_ch.get(ch_key, set())
        up_stems = up_by_ch.get(ch_key, set())
        dl_count = len(dl_stems)
        up_count = len(up_stems)

        missing_up = sorted(dl_stems - up_stems)

        # Detect page number gaps in downloaded pages
        page_nums = []
        for stem in dl_stems:
            parts = stem.split('_')
            if len(parts) >= 2:
                try:
                    page_nums.append(int(parts[-1]))
                except ValueError:
                    pass
        page_nums.sort()
        gaps = []
        for i in range(len(page_nums) - 1):
            if page_nums[i + 1] - page_nums[i] > 1:
                gaps.extend(range(page_nums[i] + 1, page_nums[i + 1]))

        ch_norm = normalize_chapter(ch_key)
        status = 'ok'
        if dl_count == 0 and up_count > 0:
            status = 'orphan'
        elif dl_count > 0 and up_count == 0:
            status = 'not_upscaled'
        elif missing_up:
            status = 'partial'

        result.append({
            'chapter': ch_norm,
            'ch_key': ch_key,
            'downloaded': dl_count,
            'upscaled': up_count,
            'missing_upscaled': len(missing_up),
            'missing_pages': missing_up[:20],
            'download_gaps': gaps[:10],
            'status': status,
        })

    result.sort(key=lambda x: _chapter_sort_key(x['chapter']), reverse=True)
    return jsonify(result)

@library_bp.route('/meta/<path:title>', methods=['PUT'])
def edit_metadata(title):
    """Edit manga folder name and/or cover image."""
    data = request.get_json(silent=True) or {}
    new_title = (data.get('new_title') or '').strip()
    cover_b64 = (data.get('cover_b64') or '').strip()
    cover_url = (data.get('cover_url') or '').strip()

    from api.roots import roots as _roots, series_dir, series_dirs

    all_folders = series_dirs(title)
    if not all_folders:
        return jsonify({'error': 'not found'}), 404
    folder = series_dir(title)   # la portada nueva va al disco principal de la obra

    # Update cover image — quita primero cualquier cover.* viejo para que la prioridad
    # jpg→png→webp del lector no deje una portada anterior "ensombreciendo" a la nueva.
    # En TODOS los discos: si quedara una portada vieja en el otro, seguiría ganando al leerla.
    def _clear_covers():
        for d in all_folders:
            for e in ('jpg', 'png', 'webp'):
                (d / f'cover.{e}').unlink(missing_ok=True)

    if cover_b64:
        import base64 as _b64
        raw = cover_b64.split(',')[-1]  # strip "data:image/...;base64," prefix
        data = _b64.b64decode(raw)
        _clear_covers()
        (folder / 'cover.jpg').write_bytes(_normalize_cover_bytes(data) or data)
    elif cover_url and cover_url.startswith('http'):
        try:
            # NO mandar Referer: el CDN de MangaDex (uploads.mangadex.org) devuelve un
            # placeholder "you can read this at mangadex.org" si ve un Referer vacío/ajeno.
            # Sin header Referer entrega el original a resolución completa.
            r = _http.get(cover_url, timeout=15)
            if r.status_code == 200:
                ct = r.headers.get('content-type', '')
                norm = _normalize_cover_bytes(r.content)
                _clear_covers()
                if norm is not None:
                    (folder / 'cover.jpg').write_bytes(norm)   # reescalado -> siempre JPEG
                else:
                    ext = 'webp' if 'webp' in ct else 'png' if 'png' in ct else 'jpg'
                    (folder / f'cover.{ext}').write_bytes(r.content)
            else:
                return jsonify({'error': f'no se pudo descargar la portada ({r.status_code})'}), 502
        except Exception as e:
            return jsonify({'error': str(e)}), 500

    # Renombrar: la obra es su nombre de carpeta, así que hay que renombrarla EN TODOS los
    # discos donde viva. Si sólo se renombrara en uno, la obra se partiría en dos.
    if new_title and new_title != title:
        for r in _roots():
            for key in ('manga', 'upscaled'):
                if (Path(r[key]) / new_title).exists():
                    return jsonify({'error': 'Ya existe una carpeta con ese nombre'}), 409
        renamed = []
        try:
            for r in _roots():
                for key in ('manga', 'upscaled'):
                    old = Path(r[key]) / title
                    if old.exists():
                        new = Path(r[key]) / new_title
                        old.rename(new)
                        renamed.append((new, old))
        except OSError as e:
            for new, old in reversed(renamed):   # a medio renombrar es peor que sin renombrar
                try:
                    new.rename(old)
                except OSError:
                    pass
            return jsonify({'error': f'No se pudo renombrar: {e}'}), 500
        return jsonify({'ok': True, 'new_title': new_title})

    return jsonify({'ok': True})


@library_bp.route('/cover_options')
def cover_options():
    """Portadas candidatas para CAMBIAR la de un manga (corrupta / baja calidad):
    AniList (1 oficial en alta resolución) + MangaDex (varias, por tomo). El frontend
    muestra el grid y al elegir una se aplica vía PUT /meta (cover_url). Params: title, mdId?."""
    title = (request.args.get('title') or '').strip()
    md_id = (request.args.get('mdId') or '').strip()
    refresh = request.args.get('refresh') in ('1', 'true')

    # La lista de candidatas (AniList + MangaDex) se cachea en DISCO 7 días: salvo "refrescar",
    # reabrir el selector es instantáneo y no repega a las APIs. La portada ACTUAL se calcula
    # siempre fresca (puede haber cambiado al aplicar otra).
    ck = f"{title}|{md_id}"
    cached = None if refresh else cache_get('cover_options', ck, 604800)

    from api.roots import series_dirs
    out = {'current': None, 'anilist': [], 'mangadex': []}
    if any((d / f'cover.{e}').exists() for d in series_dirs(title) for e in ('jpg', 'png', 'webp')):
        out['current'] = f"/api/library/cover/{quote(title, safe='')}"

    if cached is not None:
        out['anilist'] = cached.get('anilist', [])
        out['mangadex'] = cached.get('mangadex', [])
        out['cached'] = True
        return jsonify(out)

    # AniList: una portada oficial en alta resolución (extraLarge)
    try:
        from api.anilist import _ql
        q = 'query($s:String){ Media(search:$s, type:MANGA){ coverImage{ extraLarge large } } }'
        d = _ql(q, {'s': title})
        ci = ((d or {}).get('Media') or {}).get('coverImage') or {}
        url = ci.get('extraLarge') or ci.get('large')
        if url:
            out['anilist'].append({'url': url, 'thumb': ci.get('large') or url, 'label': 'AniList'})
    except Exception:
        pass

    # MangaDex: todas las portadas (por tomo/idioma)
    try:
        mid = md_id
        if not mid:
            # Auto-resuelve por VARIANTES de nombre (sinónimos AniList), no sólo por el título
            # principal — así una carpeta nombrada en romaji encuentra su entrada aunque MangaDex
            # la indexe en inglés (y viceversa). Misma ruta compartida que el Tomo Builder.
            try:
                from api.mangadex import resolve_manga_by_title
                m = resolve_manga_by_title(title)
                mid = m['id'] if m else ''
            except Exception:
                mid = ''
        if mid:
            offset = 0
            while True:
                rc = _http.get('https://api.mangadex.org/cover',
                               params={'manga[]': mid, 'limit': 100, 'offset': offset, 'order[volume]': 'asc'},
                               timeout=10)
                if not rc.ok:
                    break
                dd = rc.json()
                for it in dd.get('data', []):
                    a = it.get('attributes', {})
                    fn = a.get('fileName', '')
                    if not fn:
                        continue
                    out['mangadex'].append({
                        'volume': a.get('volume') or '?', 'locale': a.get('locale', ''),
                        'url': f"https://uploads.mangadex.org/covers/{mid}/{fn}",
                        'thumb': f"https://uploads.mangadex.org/covers/{mid}/{fn}.256.jpg",
                    })
                offset += 100
                if offset >= dd.get('total', 0):
                    break
    except Exception:
        pass

    # cachea en disco solo si encontramos algo (no cachear fallos transitorios), 7 días
    if out['anilist'] or out['mangadex']:
        cache_set('cover_options', ck, {'anilist': out['anilist'], 'mangadex': out['mangadex']},
                  ttl=604800, max_entries=300)
    return jsonify(out)


@library_bp.route('/scan_corrupt/<path:title>')
def scan_corrupt(title):
    """Check all downloaded images in a manga folder for corruption."""
    from PIL import Image

    from api.roots import series_pages
    # Todas las páginas de la obra, vivan en el disco que vivan: una revisión de integridad
    # que sólo mirase un disco daría "todo bien" sobre media obra.
    all_images = sorted(series_pages(title).values(), key=lambda p: p.name)
    if not all_images:
        return jsonify({'error': 'not found'}), 404

    corrupt = []
    checked = 0
    for img_path in all_images:
        checked += 1
        try:
            if img_path.stat().st_size < 64:
                corrupt.append({'file': img_path.name, 'error': 'Archivo demasiado pequeño (truncado)'})
                continue
            with Image.open(img_path) as img:
                img.verify()
        except Exception as e:
            corrupt.append({'file': img_path.name, 'error': str(e)[:120]})

    return jsonify({'checked': checked, 'corrupt': corrupt})


def find_manga_folder(query):
    """Nombre de carpeta real a partir de un título aproximado.

    Es el resolutor por el que pasan lector, escalado y trasplante, así que mira TODAS las
    raíces: si la obra está sólo en el segundo disco, buscarla nada más en el primero devolvía
    el query tal cual y todo lo de después fallaba con un "no encontrado" que no lo era.
    """
    from api.roots import series_titles
    query_norm = query.lower().replace('-', ' ').replace('_', ' ')
    candidates = []

    for name, dirs in series_titles().items():
        folder_norm = name.lower().replace('-', ' ').replace('_', ' ')

        if name == query:
            candidates.append((10, name))
            continue

        if name.lower() == query.lower() or folder_norm == query_norm:
            candidates.append((9, name))
            continue

        if query_norm in folder_norm or folder_norm in query_norm:
            has_images = any(
                next(d.glob('*.jpg'), None) or next(d.glob('*.png'), None) or next(d.glob('*.webp'), None)
                for d in dirs)
            candidates.append((5 if has_images else 1, name))


    if candidates:
        candidates.sort(key=lambda x: -x[0])
        return candidates[0][1]
    
    return query

# ── Offline cover cache ───────────────────────────────────────────────────────

_offline_cover_status = {"running": False, "done": 0, "total": 0, "errors": 0}


def _cover_variant(path: Path, w: int):
    """Rendición de la portada al ancho pedido, cacheada en disco. Delega en `imgproxy` para que
    la escalera de anchos y la calidad estén definidas UNA vez (antes esto topaba en 96 px y
    calidad 70: sólo servía para el blur-up, no para la imagen real de la tarjeta).

    La clave de caché lleva el mtime → cambiar la portada invalida sus rendiciones."""
    try:
        from api.imgproxy import render_cached
        return render_cached(path, f'{path}:{path.stat().st_mtime_ns}', w)
    except Exception:
        return None


@library_bp.route('/cover/<path:title>')
def serve_local_cover(title):
    """Serve a locally-downloaded cover image."""
    from api.roots import series_dirs
    for d in series_dirs(title):
        resp = _cover_file_response(d)
        if resp is not None:
            return resp
    return 'not found', 404


@library_bp.route('/thumb/<path:folder>')
def serve_manga_thumb(folder):
    """Portada de un manga servida SIEMPRE por el backend (paridad con /api/img de anime):
    sirve el cover local si existe; si no, lo descarga UNA vez de la fuente (despierta Suwayomi
    y re-resuelve el id si está caducado), lo persiste como cover.jpg y lo sirve. Resistente a
    JVM apagada / id caducado / arranque lento. `?w=` → micro-thumb para el blur-up."""
    from api.roots import series_dir, series_dirs
    dirs = series_dirs(folder)
    if not dirs:
        return 'not found', 404
    # La portada puede estar en cualquiera de los discos de la obra; si hay que descargarla,
    # se persiste en el principal.
    base = next((d for d in dirs
                 if any((d / f'cover.{e}').exists() for e in ('jpg', 'png', 'webp'))),
                series_dir(folder))
    w = request.args.get('w', type=int)

    resp = _cover_file_response(base)
    if resp is None:
        # Descarga+persiste una sola vez, con guarda anti-estampida por carpeta.
        with _thumb_lock(folder):
            resp = _cover_file_response(base)   # otro hilo pudo escribirla mientras esperábamos
            if resp is None and _fetch_persist_cover(base):
                resp = _cover_file_response(base)
    if resp is None:
        return 'not found', 404

    # `?w=` sirve tanto el blur-up (28 px) como la imagen real de la tarjeta (640): una portada
    # de 91 KB se pinta en una caja de ~250 px, así que servirla entera es tirar decodificación.
    if w:
        p = next((base / f'cover.{e}' for e in ('jpg', 'png', 'webp') if (base / f'cover.{e}').exists()), None)
        if p:
            variant = _cover_variant(p, w)
            if variant is not None:
                return send_file(str(variant), max_age=86400, conditional=True)
    return resp


@library_bp.route('/offline_covers_status')
def offline_covers_status():
    return jsonify(_offline_cover_status)


@library_bp.route('/download_covers_offline', methods=['POST'])
def download_covers_offline():
    """Download all missing cover images to disk so the library works offline."""
    if _offline_cover_status["running"]:
        return jsonify({"ok": False, "message": "Ya en progreso"}), 409

    # Collect manga folders + their current cover URL
    lib_covers = _load_cover_cache()
    lib_json = Path(manga_dir()) / 'local_library.json'
    if lib_json.exists():
        try:
            for entry in json.loads(lib_json.read_text()):
                t = (entry.get('title') or '').lower().strip()
                c = entry.get('cover')
                if t and c:
                    lib_covers[t] = c
        except Exception:
            pass

    from api.roots import series_titles
    targets = []
    for _name, _dirs in series_titles().items():
        folder = _dirs[0]
        # Skip if local cover already exists (en cualquiera de sus discos)
        if any((d / f'cover.{e}').exists() for d in _dirs for e in ('jpg', 'png', 'webp')):
            continue
        url = lib_covers.get(_name.lower().strip())
        if url and url.startswith('http'):
            targets.append((folder, url))

    if not targets:
        return jsonify({"ok": True, "message": "Todas las portadas ya están descargadas", "done": 0})

    def _run():
        _offline_cover_status.update({"running": True, "done": 0, "total": len(targets), "errors": 0})
        for folder, url in targets:
            try:
                r = _http.get(url, timeout=15)
                if r.status_code == 200:
                    ct = r.headers.get('content-type', '')
                    ext = 'webp' if 'webp' in ct else 'png' if 'png' in ct else 'jpg'
                    (folder / f'cover.{ext}').write_bytes(r.content)
                    _offline_cover_status["done"] += 1
                else:
                    _offline_cover_status["errors"] += 1
            except Exception:
                _offline_cover_status["errors"] += 1
        _offline_cover_status["running"] = False

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"ok": True, "total": len(targets), "message": f"Descargando {len(targets)} portadas..."})
