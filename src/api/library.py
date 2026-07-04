#!/usr/bin/env python3
"""
Library API - Manage local manga library
"""

from flask import Blueprint, jsonify, request, Response
from pathlib import Path
from decimal import Decimal, InvalidOperation
from urllib.parse import quote
import json
from api.resilient_http import http as _http  # retry + backoff + per-host rate limiting
import threading

from api.runtime import MANGA_DIR, UPSCALED_DIR, normalize_chapter, cache_get, cache_set
from api.index_db import cached_measure, prune


def _count_pages(path):
    """(nº de páginas, nº de capítulos, {}) de una carpeta de serie. Capítulo = prefijo
    chXXXX distinto entre archivos ch####_###.ext. Cacheado por mtime en index_db."""
    p = Path(path)
    images = list(p.glob('*.png')) + list(p.glob('*.jpg')) + list(p.glob('*.webp'))
    chapters = set()
    for img in images:
        parts = img.stem.split('_')
        if parts and parts[0].startswith('ch'):
            chapters.add(parts[0])
    return len(images), len(chapters), {}


def _count_upscaled(path):
    return len(list(Path(path).glob('*.jpg'))), 0, {}

_COVER_CACHE_FILE = Path(MANGA_DIR) / '.cover_cache.json'

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
        if _COVER_CACHE_FILE.exists():
            return json.loads(_COVER_CACHE_FILE.read_text(encoding='utf-8'))
    except Exception:
        pass
    return {}


def _save_cover_cache(cache: dict):
    try:
        _COVER_CACHE_FILE.write_text(json.dumps(cache, ensure_ascii=False), encoding='utf-8')
    except Exception:
        pass


def _chapter_sort_key(value):
    try:
        return float(Decimal(normalize_chapter(value)))
    except (InvalidOperation, ValueError):
        return -1.0

library_bp = Blueprint('library', __name__)

@library_bp.route('')
def get_library():
    # Build a title→cover lookup from local_library.json + disk cover cache
    lib_covers: dict = _load_cover_cache()
    lib_json = Path(MANGA_DIR) / 'local_library.json'
    if lib_json.exists():
        try:
            for entry in json.loads(lib_json.read_text()):
                title = (entry.get('title') or '').lower().strip()
                cover = entry.get('cover')
                if title and cover:
                    lib_covers[title] = cover
        except Exception:
            pass

    folders = []
    for f in Path(MANGA_DIR).iterdir():
        if not f.is_dir() or f.name.startswith('.'):
            continue   # salta carpetas internas: .cache, .import_staging, etc.

        # Per-folder page/chapter counts are cached by mtime (index_db) so the whole
        # library listing stays instant instead of globbing every folder each call.
        image_count, chapters_count, _ = cached_measure('libcount', f.name, str(f), _count_pages)
        upscaled_count, _, _ = cached_measure(
            'libup', f.name, str(Path(UPSCALED_DIR) / f.name), _count_upscaled)

        source_meta = None
        meta_path = f / '.source_meta.json'
        if meta_path.exists():
            try:
                source_meta = json.loads(meta_path.read_text())
            except Exception:
                pass

        # Estado de traducción por trasplante (badge "ES" en la tarjeta)
        translated_count = 0
        tp_path = f / '.transplant_meta.json'
        if tp_path.exists():
            try:
                translated_count = len(json.loads(tp_path.read_text()).get('translated', []))
            except Exception:
                pass

        # Cover priority: local file → source_meta thumbnail → URL cache
        local_cover = next(
            (p for p in (f / 'cover.jpg', f / 'cover.png', f / 'cover.webp') if p.exists()),
            None
        )
        if local_cover:
            cover = f"/api/library/cover/{quote(f.name, safe='')}"
        else:
            cover = (source_meta or {}).get('thumbnailUrl')
            if not cover:
                cover = lib_covers.get(f.name.lower().strip())

        folders.append({
            'id': f.name,
            'name': f.name,
            'chapter_count': chapters_count,
            'image_count': image_count,
            'page_count': image_count,
            'upscaled': upscaled_count,
            'cover': cover,
            'source_meta': source_meta,
            'translated_count': translated_count,
        })

    prune('libcount', [x['name'] for x in folders])
    prune('libup', [x['name'] for x in folders])
    folders.sort(key=lambda x: x['name'].lower())
    return jsonify(folders)


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

    results = {}
    for folder in Path(MANGA_DIR).iterdir():
        if not folder.is_dir():
            continue
        
        # Skip if already has local cover
        local_covers = list(folder.glob('cover.*')) + list(folder.glob('folder.*'))
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
                            cover_url = f"https://uploads.mangadex.org/covers/{manga_id}/{filename}.256.jpg"
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
                            cover_url = f"https://uploads.mangadex.org/covers/{manga_id}/{filename}.256.jpg"
                
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
    folder = Path(MANGA_DIR) / title
    if not folder.exists():
        # Try with spaces
        folder = Path(MANGA_DIR) / title.replace('_', ' ')
        if not folder.exists():
            return jsonify({'error': 'Manga not found'}), 404
    
    chapters = get_chapters_from_folder(folder)
    upscaled_folder = Path(UPSCALED_DIR) / title.replace('_', ' ')
    upscaled = {}

    _IMAGE_EXTS = ['jpg', 'jpeg', 'png', 'webp']

    # Count downloaded pages per chapter prefix
    dl_by_ch: dict = {}
    for ext in _IMAGE_EXTS:
        for f in folder.glob(f'*.{ext}'):
            parts = f.stem.split('_')
            if parts and parts[0].startswith('ch'):
                dl_by_ch.setdefault(parts[0], set()).add(f.stem)

    # Count upscaled pages per chapter prefix
    up_by_ch: dict = {}
    if upscaled_folder.exists():
        for ext in _IMAGE_EXTS:
            for f in upscaled_folder.glob(f'*.{ext}'):
                parts = f.stem.split('_')
                if parts and parts[0].startswith('ch'):
                    up_by_ch.setdefault(parts[0], set()).add(f.stem)

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
    
    source_meta = None
    meta_path = folder / '.source_meta.json'
    if meta_path.exists():
        try:
            source_meta = json.loads(meta_path.read_text())
        except Exception:
            pass

    transplant_meta = None
    tp_path = folder / '.transplant_meta.json'
    if tp_path.exists():
        try:
            transplant_meta = json.loads(tp_path.read_text())
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
    
    has_subdirs = any(f.is_dir() for f in folder.iterdir())
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
            if not ch_folder.is_dir():
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


@library_bp.route('/chapter_health/<path:title>')
def chapter_health(title):
    """Scan a manga's chapters for upscale completeness and download gaps.
    Returns per-chapter status so the UI can show repair indicators.
    """
    actual = find_manga_folder(title)

    dl_folder = Path(MANGA_DIR) / actual
    up_folder = Path(UPSCALED_DIR) / actual

    if not dl_folder.exists():
        return jsonify({'error': 'not found'}), 404

    # Collect all downloaded pages grouped by chapter prefix (ch####)
    _IMAGE_EXTS = ('jpg', 'jpeg', 'png', 'webp')
    dl_by_ch: dict = {}
    for ext in _IMAGE_EXTS:
        for f in dl_folder.glob(f'*.{ext}'):
            parts = f.stem.split('_')
            if parts and parts[0].startswith('ch'):
                ch_key = parts[0]
                dl_by_ch.setdefault(ch_key, set()).add(f.stem)

    # Collect all upscaled pages grouped by chapter prefix
    up_by_ch: dict = {}
    if up_folder.exists():
        for ext in _IMAGE_EXTS:
            for f in up_folder.glob(f'*.{ext}'):
                parts = f.stem.split('_')
                if parts and parts[0].startswith('ch'):
                    ch_key = parts[0]
                    up_by_ch.setdefault(ch_key, set()).add(f.stem)

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

    folder = Path(MANGA_DIR) / title
    if not folder.exists():
        return jsonify({'error': 'not found'}), 404

    # Update cover image — quita primero cualquier cover.* viejo para que la prioridad
    # jpg→png→webp del lector no deje una portada anterior "ensombreciendo" a la nueva.
    def _clear_covers():
        for e in ('jpg', 'png', 'webp'):
            (folder / f'cover.{e}').unlink(missing_ok=True)

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

    # Rename folder
    if new_title and new_title != title:
        new_folder = Path(MANGA_DIR) / new_title
        if new_folder.exists():
            return jsonify({'error': 'Ya existe una carpeta con ese nombre'}), 409
        folder.rename(new_folder)
        up_folder = Path(UPSCALED_DIR) / title
        if up_folder.exists():
            up_folder.rename(Path(UPSCALED_DIR) / new_title)
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

    out = {'current': None, 'anilist': [], 'mangadex': []}
    folder = Path(MANGA_DIR) / title
    if folder.is_dir() and any((folder / f'cover.{e}').exists() for e in ('jpg', 'png', 'webp')):
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
    from PIL import Image, UnidentifiedImageError

    folder = Path(MANGA_DIR) / title
    if not folder.exists():
        return jsonify({'error': 'not found'}), 404

    _IMAGE_EXTS = ('jpg', 'jpeg', 'png', 'webp')
    all_images = []
    for ext in _IMAGE_EXTS:
        all_images.extend(folder.glob(f'*.{ext}'))
    all_images.sort()

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
    query_norm = query.lower().replace('-', ' ').replace('_', ' ')
    candidates = []
    
    for f in Path(MANGA_DIR).iterdir():
        if not f.is_dir():
            continue
        
        folder_norm = f.name.lower().replace('-', ' ').replace('_', ' ')
        has_images = len(list(f.glob('*.jpg'))) + len(list(f.glob('*.png'))) + len(list(f.glob('*.webp')))
        
        if f.name == query:
            candidates.append((10, f.name))
            continue
        
        if f.name.lower() == query.lower() or folder_norm == query_norm:
            candidates.append((9, f.name))
            continue
        
        if query_norm in folder_norm or folder_norm in query_norm:
            if has_images:
                candidates.append((5, f.name))
            else:
                candidates.append((1, f.name))
    
    if candidates:
        candidates.sort(key=lambda x: -x[0])
        return candidates[0][1]
    
    return query

# ── Offline cover cache ───────────────────────────────────────────────────────

_offline_cover_status = {"running": False, "done": 0, "total": 0, "errors": 0}


@library_bp.route('/cover/<path:title>')
def serve_local_cover(title):
    """Serve a locally-downloaded cover image."""
    folder = Path(MANGA_DIR) / title
    for ext in ('jpg', 'png', 'webp'):
        p = folder / f'cover.{ext}'
        if p.exists():
            data = p.read_bytes()
            # Normaliza UNA vez en disco las portadas heredadas demasiado grandes (una página
            # HD elegida como portada): reescala y reescribe como cover.jpg para que las
            # siguientes lecturas sean rápidas y la tarjeta no se vea gigante.
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
    return 'not found', 404


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
    lib_json = Path(MANGA_DIR) / 'local_library.json'
    if lib_json.exists():
        try:
            for entry in json.loads(lib_json.read_text()):
                t = (entry.get('title') or '').lower().strip()
                c = entry.get('cover')
                if t and c:
                    lib_covers[t] = c
        except Exception:
            pass

    targets = []
    for folder in Path(MANGA_DIR).iterdir():
        if not folder.is_dir():
            continue
        # Skip if local cover already exists
        if any((folder / f'cover.{e}').exists() for e in ('jpg', 'png', 'webp')):
            continue
        url = lib_covers.get(folder.name.lower().strip())
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

