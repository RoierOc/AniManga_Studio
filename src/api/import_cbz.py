#!/usr/bin/env python3
"""
Import API — trae CBZ/CBR locales a la biblioteca como un manga normal
(MANGA_DIR/<title>/ch####_###.ext) para que upscale/lector/export/transplant
funcionen sin cambios.

División de un TOMO en sus capítulos reales por CASCADA (de más fiable a último
recurso), evitando el tedioso "declara qué capítulos abarca" y el frágil reparto
por suma de páginas:

  N1  Nombres/carpetas        — `Ch0010_001`, `c010-001`, `Chapter 10/01.jpg`, …
  N2  ComicInfo.xml           — `<Pages><Page Bookmark="…">` (marcadores de capítulo)
  N3  Anclado por contenido   — dHash: ubica la 1ª página de cada capítulo (de una
                                fuente) dentro del archivo; inmune al desfase de páginas
  N4  Manual asistido         — el usuario ajusta los cortes sobre la previsualización

Flujo: POST /analyze (sube y detecta, NO escribe) → el usuario confirma/ajusta →
POST /commit (escribe el split confirmado). /thumb sirve miniaturas del staging.
"""

from flask import Blueprint, jsonify, request, Response
from pathlib import Path
from decimal import Decimal, InvalidOperation
from urllib.parse import quote
import io
import json
import re
import time
import uuid
import xml.etree.ElementTree as ET

from api.runtime import MANGA_DIR, UPSCALED_DIR, manga_dir, upscaled_dir, normalize_chapter
from api.cbz import _list_entries, _extract, _ARCHIVE_EXTS, _MIME

import_bp = Blueprint('import_cbz', __name__)

_STAGING = Path(MANGA_DIR).parent / '.import_staging'
_STAGING_TTL = 3600   # s: limpia archivos en staging más viejos que esto

# token de capítulo en un nombre/carpeta: chapter/chap/ch/cap(itulo)/c/ep(isode) + número.
# El (?:^|[^a-z0-9]) exige límite de palabra → 'c' suelto solo casa en `c010`, no dentro de
# 'scene'/'comic'. Alternativas largas primero para que casen preferentemente.
_CH_RE = re.compile(r'(?i)(?:^|[^a-z0-9])(?:chapter|chap|ch|cap(?:itulo)?|c|episode|ep)[\s._\-#]*0*(\d+(?:\.\d+)?)')
_PURE_NUM_RE = re.compile(r'^0*(\d{1,4}(?:\.\d+)?)$')


# ── helpers de capítulo / meta ────────────────────────────────────────────────

def _chapter_file_prefix(chapter):
    """Mismo esquema que download.py — ch{####} (parte entera, sub-capítulos .x)."""
    chapter_norm = normalize_chapter(chapter)
    try:
        value = Decimal(chapter_norm)
        int_part = int(value.to_integral_value(rounding='ROUND_FLOOR'))
        if '.' in chapter_norm:
            return f"ch{int_part:04d}.{chapter_norm.split('.')[-1]}"
        return f"ch{int_part:04d}"
    except (InvalidOperation, ValueError):
        return f"ch{chapter_norm}"


def _read_meta(folder: Path) -> dict:
    meta_path = folder / '.source_meta.json'
    if meta_path.exists():
        try:
            return json.loads(meta_path.read_text(encoding='utf-8'))
        except Exception:
            pass
    return {}


def _write_meta(folder: Path, meta: dict):
    try:
        (folder / '.source_meta.json').write_text(json.dumps(meta, ensure_ascii=False), encoding='utf-8')
    except Exception:
        pass


# ── staging (subida temporal para analizar antes de escribir) ─────────────────

def _purge_staging():
    """Borra archivos de staging viejos (subidas abandonadas)."""
    if not _STAGING.is_dir():
        return
    now = time.time()
    for d in _STAGING.iterdir():
        try:
            if now - d.stat().st_mtime > _STAGING_TTL:
                for f in d.iterdir():
                    f.unlink(missing_ok=True)
                d.rmdir()
        except Exception:
            pass


def _staged_arc(token: str) -> Path | None:
    if not token or not re.fullmatch(r'[a-f0-9]{32}', token):
        return None
    d = _STAGING / token
    if not d.is_dir():
        return None
    arcs = [f for f in d.iterdir() if f.suffix.lower() in _ARCHIVE_EXTS]
    return arcs[0] if arcs else None


# ── N1: detección por nombres/carpetas ────────────────────────────────────────

def _chapter_of(entry: str):
    """Número de capítulo detectado en la RUTA de una entrada (carpeta o nombre), o None."""
    parts = entry.replace('\\', '/').split('/')
    folders, fname = parts[:-1], parts[-1]
    for seg in folders:                       # carpeta-por-capítulo con token (Chapter 10/…)
        m = _CH_RE.search(seg)
        if m:
            return m.group(1)
    m = _CH_RE.search(fname)                   # token en el nombre (Ch0010_001)
    if m:
        return m.group(1)
    for seg in folders:                        # carpeta numérica pura (10/001.jpg)
        m = _PURE_NUM_RE.match(seg.strip())
        if m:
            return m.group(1)
    return None


def _is_cover_name(entry: str) -> bool:
    nm = Path(entry).name.lower()
    return 'cover' in nm or 'portada' in nm


def _detect_by_names(entries: list):
    """Agrupa entradas (ya ordenadas) por el capítulo embebido en su nombre/carpeta.
    Devuelve {method, chapters:[{chapter,start,count}], cover} o None si no hay señal."""
    chmap = [_chapter_of(e) for e in entries]
    if not any(chmap):
        return None
    chapters, cover, last = {}, None, None
    for i, (e, c) in enumerate(zip(entries, chmap)):
        if c is None:
            if _is_cover_name(e) or last is None:   # portada/extras antes del 1er capítulo
                if cover is None:
                    cover = i
                continue
            c = last                                 # página sin token en medio → continúa el cap actual
        chapters.setdefault(c, []).append(i)
        last = c
    if not chapters:
        return None

    def keyf(c):
        try:
            return float(c)
        except ValueError:
            return 1e9
    out = [{'chapter': normalize_chapter(c), 'start': chapters[c][0], 'count': len(chapters[c])}
           for c in sorted(chapters, key=keyf)]
    return {'method': 'filenames', 'chapters': out, 'cover': cover}


# ── N2: detección por ComicInfo.xml ───────────────────────────────────────────

def _read_comicinfo(arc: Path) -> str | None:
    """Lee ComicInfo.xml del archivo (no es imagen → _list_entries lo ignora)."""
    ext = arc.suffix.lower()
    try:
        if ext in ('.cbz', '.zip'):
            import zipfile
            with zipfile.ZipFile(arc) as z:
                name = next((n for n in z.namelist() if Path(n).name.lower() == 'comicinfo.xml'), None)
                return z.read(name).decode('utf-8', 'replace') if name else None
        # cbr/rar
        import subprocess
        listing = subprocess.run(['bsdtar', 'tf', str(arc)], capture_output=True, text=True, timeout=15)
        name = next((ln.strip() for ln in listing.stdout.splitlines()
                     if Path(ln.strip()).name.lower() == 'comicinfo.xml'), None)
        if not name:
            return None
        out = subprocess.run(['bsdtar', '-xOf', str(arc), name], capture_output=True, timeout=15)
        return out.stdout.decode('utf-8', 'replace') if out.returncode == 0 else None
    except Exception:
        return None


def _detect_by_comicinfo(entries: list, xml_text: str):
    """Marcadores <Page Bookmark="…"> → límites de capítulo. El atributo Image es el índice
    (0-based) de la página en orden de lectura; asumimos que casa con las entradas ordenadas."""
    try:
        root = ET.fromstring(xml_text)
    except Exception:
        return None
    pages = root.find('Pages')
    if pages is None:
        return None
    marks = []
    for pg in pages.findall('Page'):
        bm = (pg.get('Bookmark') or '').strip()
        if not bm:
            continue
        try:
            marks.append((int(pg.get('Image')), bm))
        except (TypeError, ValueError):
            continue
    if not marks:
        return None
    marks.sort()
    n = len(entries)
    chapters = []
    for k, (img, bm) in enumerate(marks):
        start = max(0, img)
        end = marks[k + 1][0] if k + 1 < len(marks) else n
        if end - start <= 0:
            continue
        m = re.search(r'(\d+(?:\.\d+)?)', bm)
        chapters.append({'chapter': normalize_chapter(m.group(1) if m else str(k + 1)),
                         'start': start, 'count': end - start})
    if not chapters:
        return None
    cover = 0 if marks[0][0] > 0 else None   # páginas antes del 1er marcador = portada/extras
    return {'method': 'comicinfo', 'chapters': chapters, 'cover': cover}


# ── análisis (cascada N1 → N2 → flat) ─────────────────────────────────────────

def _analyze(arc: Path) -> dict:
    entries = _list_entries(arc)
    n = len(entries)
    if n == 0:
        return {'method': 'empty', 'count': 0, 'chapters': [], 'cover': None}

    det = _detect_by_names(entries)
    if not det:
        xml = _read_comicinfo(arc)
        if xml:
            det = _detect_by_comicinfo(entries, xml)
    if not det:
        # plano: un solo bloque; el usuario podrá anclar (N3) o cortar a mano (N4)
        cover = next((i for i, e in enumerate(entries) if _is_cover_name(e)), None)
        body_start = 0 if cover is None else (1 if cover == 0 else 0)
        det = {'method': 'flat',
               'chapters': [{'chapter': '1', 'start': body_start, 'count': n - (1 if cover == 0 else 0)}],
               'cover': cover}

    det['count'] = n
    return det


# ── endpoints ─────────────────────────────────────────────────────────────────

@import_bp.route('/analyze', methods=['POST'])
def analyze():
    """Sube UN archivo, lo deja en staging y DEVUELVE la división detectada (no escribe nada).
    El frontend muestra los capítulos detectados con miniaturas para confirmar/ajustar."""
    _purge_staging()
    upload = request.files.get('file')
    if not upload or not upload.filename:
        return jsonify({'error': 'no file'}), 400
    ext = Path(upload.filename).suffix.lower()
    if ext not in _ARCHIVE_EXTS:
        return jsonify({'error': f'extensión no soportada: {ext}'}), 400

    token = uuid.uuid4().hex
    d = _STAGING / token
    d.mkdir(parents=True, exist_ok=True)
    arc = d / f'archive{ext}'
    upload.save(str(arc))

    try:
        det = _analyze(arc)
    except Exception as e:
        return jsonify({'error': f'no se pudo leer el archivo: {e}'}), 500
    if det.get('method') == 'empty':
        return jsonify({'error': 'el archivo no contiene imágenes'}), 400

    # nombre por defecto del manga = nombre del archivo sin extensión ni "Tomo N"
    base = re.sub(r'(?i)[\s_\-]*(tomo|vol(?:umen|ume)?|v)[\s_\-.]*\d+.*$', '', Path(upload.filename).stem).strip()
    return jsonify({
        'token': token,
        'filename': upload.filename,
        'suggestedTitle': base or Path(upload.filename).stem,
        'method': det['method'],
        'count': det['count'],
        'cover': det['cover'],
        'chapters': det['chapters'],
    })


@import_bp.route('/thumb')
def thumb():
    """Miniatura de una página del archivo en staging (para la previsualización)."""
    arc = _staged_arc(request.args.get('token', ''))
    if arc is None:
        return 'not found', 404
    try:
        idx = int(request.args.get('idx', 0))
        entries = _list_entries(arc)
        if not (0 <= idx < len(entries)):
            return 'out of range', 404
        data = _extract(arc, entries[idx])
        try:
            from PIL import Image
            im = Image.open(io.BytesIO(data))
            im.thumbnail((360, 540))
            buf = io.BytesIO()
            im.convert('RGB').save(buf, 'JPEG', quality=80)
            data = buf.getvalue()
            mime = 'image/jpeg'
        except Exception:
            mime = _MIME.get(Path(entries[idx]).suffix.lower(), 'image/jpeg')
        return Response(data, mimetype=mime, headers={'Cache-Control': 'private, max-age=600'})
    except Exception as e:
        return str(e), 500


@import_bp.route('/anchor', methods=['POST'])
def anchor():
    """N3 — anclado por contenido para archivos PLANOS. Ubica la 1ª página de cada capítulo
    (desde una fuente Suwayomi/MangaDex) dentro del archivo por hash perceptual (dHash), así
    los cortes caen exactos aunque los conteos de página difieran. Body:
    {token, title, anilistId?, startChapter, maxChapters?}."""
    body = request.get_json(silent=True) or {}
    arc = _staged_arc(body.get('token', ''))
    if arc is None:
        return jsonify({'error': 'staging expirado; vuelve a subir el archivo'}), 404
    title = (body.get('title') or '').strip()
    start_ch = body.get('startChapter')
    if not title or start_ch is None:
        return jsonify({'error': 'title y startChapter requeridos'}), 400

    try:
        import cv2
        import numpy as np
        from concurrent.futures import ThreadPoolExecutor
        from api import transplant as T
        from api.anilist import title_variants

        entries = _list_entries(arc)
        # 1) dHash de TODAS las páginas del archivo (descarta color: créditos/portada)
        def _hash_entry(i):
            try:
                data = _extract(arc, entries[i])
                if T._is_color_bytes(data):
                    return (i, None)
                return (i, T._dhash_bytes(data))
            except Exception:
                return (i, None)
        with ThreadPoolExecutor(max_workers=8) as pool:
            arc_hashes = list(pool.map(_hash_entry, range(len(entries))))

        # 2) fuente con imágenes para sacar la 1ª página de cada capítulo
        al = body.get('anilistId')
        variants = title_variants(title, int(al) if al else None)
        cands = T._discover_candidates(variants)
        try:
            cands += T._md_candidates(variants, '')
        except Exception:
            pass
        if not cands:
            return jsonify({'error': 'no se encontró ninguna fuente con imágenes para anclar',
                            'method': 'flat'}), 200
        cand = max(cands, key=lambda c: c.get('match', 0))

        # 3) por cada capítulo desde start_ch, anclar su 1ª página B/N en el archivo (monótono)
        start_num = float(normalize_chapter(str(start_ch)))
        max_ch = int(body.get('maxChapters') or 40)
        boundaries, last_idx = [], -1
        for k in range(max_ch):
            chnum = start_num + k
            chstr = str(int(chnum)) if chnum == int(chnum) else str(chnum)
            try:
                _, urls = T._candidate_chapter_urls(
                    {'sourceId': cand['sourceId'], 'mangaId': cand['id'], 'sourceLang': cand.get('sourceLang')},
                    title, want_num=chstr)
            except Exception:
                urls = []
            if not urls:
                break
            # 1ª página B/N del capítulo en la fuente
            anchor_h = None
            for u in urls[:6]:
                data = T._fetch_ref(u)
                if data and not T._is_color_bytes(data):
                    anchor_h = T._dhash_bytes(data)
                    break
            if anchor_h is None:
                continue
            best_i, best_d = None, 999
            for i, h in arc_hashes:
                if i <= last_idx or h is None:
                    continue
                d = T._hamming(anchor_h, h)
                if d < best_d:
                    best_d, best_i = d, i
            if best_i is not None and (1 - best_d / 64) >= T._MATCH_MIN_SIM:
                boundaries.append((chstr, best_i))
                last_idx = best_i

        if not boundaries:
            return jsonify({'error': 'no se pudo anclar ningún capítulo (¿fuente sin esas páginas?)',
                            'method': 'flat'}), 200

        # 4) construir capítulos a partir de los cortes
        n = len(entries)
        cover = boundaries[0][1] if boundaries[0][1] > 0 else None
        chapters = []
        for j, (chstr, idx) in enumerate(boundaries):
            end = boundaries[j + 1][1] if j + 1 < len(boundaries) else n
            chapters.append({'chapter': normalize_chapter(chstr), 'start': idx, 'count': end - idx})
        return jsonify({'method': 'anchored', 'count': n, 'cover': cover, 'chapters': chapters})
    except Exception as e:
        return jsonify({'error': f'anclado falló: {e}', 'method': 'flat'}), 200


@import_bp.route('/commit', methods=['POST'])
def commit():
    """Escribe el split CONFIRMADO a MANGA_DIR/<title>/ch####_###.ext. Body:
    {token, title, cover:idx|null, chapters:[{chapter,start,count}]}."""
    body = request.get_json(silent=True) or {}
    arc = _staged_arc(body.get('token', ''))
    if arc is None:
        return jsonify({'error': 'staging expirado; vuelve a subir el archivo'}), 404
    title = (body.get('title') or '').strip()
    if not title or '..' in title or '/' in title or '\\' in title:
        return jsonify({'error': 'título inválido'}), 400
    chapters = body.get('chapters') or []
    if not chapters:
        return jsonify({'error': 'no hay capítulos que escribir'}), 400

    entries = _list_entries(arc)
    n = len(entries)
    folder = Path(manga_dir()) / title
    folder.mkdir(parents=True, exist_ok=True)

    cover_idx = body.get('cover')
    if isinstance(cover_idx, int) and 0 <= cover_idx < n:
        if not any((folder / f'cover{e}').exists() for e in ('.jpg', '.png', '.webp')):
            try:
                ce = entries[cover_idx]
                (folder / f'cover{Path(ce).suffix.lower() or ".jpg"}').write_bytes(_extract(arc, ce))
            except Exception:
                pass

    written = []
    for seg in chapters:
        try:
            start = int(seg['start']); count = int(seg['count'])
        except (KeyError, TypeError, ValueError):
            continue
        if count <= 0 or start < 0 or start >= n:
            continue
        ch_norm = normalize_chapter(str(seg.get('chapter', '')))
        prefix = _chapter_file_prefix(ch_norm)
        page = 0
        for idx in range(start, min(start + count, n)):
            try:
                ce = entries[idx]
                data = _extract(arc, ce)
            except Exception:
                continue
            page += 1
            (folder / f'{prefix}_{page:03d}{Path(ce).suffix.lower() or ".jpg"}').write_bytes(data)
        if page:
            written.append(ch_norm)

    if not written:
        return jsonify({'error': 'no se escribió ninguna página'}), 400

    meta = _read_meta(folder)
    meta['imported'] = True
    meta['title'] = title
    _write_meta(folder, meta)

    # limpiar staging
    try:
        for f in arc.parent.iterdir():
            f.unlink(missing_ok=True)
        arc.parent.rmdir()
    except Exception:
        pass

    return jsonify({'status': 'ok', 'title': title, 'chapters': written, 'chapter_count': len(written)})


@import_bp.route('/list')
def list_imported():
    items = []
    root = Path(manga_dir())
    if not root.is_dir():
        return jsonify(items)

    for f in root.iterdir():
        if not f.is_dir():
            continue
        meta = _read_meta(f)
        if not meta.get('imported'):
            continue

        images = list(f.glob('*.png')) + list(f.glob('*.jpg')) + list(f.glob('*.jpeg')) + list(f.glob('*.webp'))
        chapters = set()
        for img in images:
            m = re.match(r'(ch\d+(?:\.\d+)?)', img.stem)
            if m:
                chapters.add(m.group(1))

        upscaled = list((Path(upscaled_dir()) / f.name).glob('*.jpg')) if (Path(upscaled_dir()) / f.name).is_dir() else []

        translated_count = 0
        tp_path = f / '.transplant_meta.json'
        if tp_path.exists():
            try:
                translated_count = len(json.loads(tp_path.read_text(encoding='utf-8')).get('translated', []))
            except Exception:
                pass

        local_cover = next((p for p in (f / 'cover.jpg', f / 'cover.png', f / 'cover.webp') if p.exists()), None)
        cover = f"/api/library/cover/{quote(f.name, safe='')}" if local_cover else None

        items.append({
            'id': f.name, 'name': f.name,
            'chapter_count': len(chapters), 'image_count': len(images),
            'upscaled': len(upscaled), 'translated_count': translated_count, 'cover': cover,
        })

    items.sort(key=lambda x: x['name'].lower())
    return jsonify(items)
