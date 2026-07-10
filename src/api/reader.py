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

from api.runtime import manga_dir, upscaled_dir, normalize_chapter


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
        _history_path().write_text(
            json.dumps(history[:500], ensure_ascii=False, indent=2), encoding='utf-8'
        )
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
        'read_at': int(time.time()),
    })
    _history_write(history)
    return jsonify({'success': True})

@reader_bp.route('/history/clear', methods=['POST'])
def clear_history():
    _history_write([])
    return jsonify({'success': True})

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