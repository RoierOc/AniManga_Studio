#!/usr/bin/env python3
"""
Library API - Manage local manga library
"""

from flask import Blueprint, jsonify, request
from pathlib import Path
from decimal import Decimal, InvalidOperation
from urllib.parse import quote
import json

from api.runtime import MANGA_DIR, UPSCALED_DIR, normalize_chapter


def _chapter_sort_key(value):
    try:
        return float(Decimal(normalize_chapter(value)))
    except (InvalidOperation, ValueError):
        return -1.0

library_bp = Blueprint('library', __name__)

@library_bp.route('')
def get_library():
    folders = []
    for f in Path(MANGA_DIR).iterdir():
        if f.is_dir():
            images = list(f.glob('*.png')) + list(f.glob('*.jpg')) + list(f.glob('*.webp'))
            upscaled = list(Path(UPSCALED_DIR).joinpath(f.name).glob('*.jpg'))
            
            # Count chapters, not pages
            chapters_count = 0
            if images:
                chapters = set()
                for img in images:
                    parts = img.stem.split('_')
                    if len(parts) > 0 and parts[0].startswith('ch'):
                        chapters.add(parts[0])
                chapters_count = len(chapters)
            
            source_meta = None
            meta_path = f / '.source_meta.json'
            if meta_path.exists():
                try:
                    source_meta = json.loads(meta_path.read_text())
                except Exception:
                    pass

            cover = source_meta.get('thumbnailUrl') if source_meta else None

            folders.append({
                'id': f.name,
                'name': f.name,
                'chapter_count': chapters_count,
                'image_count': len(images),
                'page_count': sum(1 for img in images if img.suffix in ('.jpg', '.png', '.webp')),
                'upscaled': len(upscaled),
                'cover': cover,
                'source_meta': source_meta,
            })
    
    folders.sort(key=lambda x: x['name'].lower())
    return jsonify(folders)

@library_bp.route('/search-covers')
def search_covers():
    """Search MangaDex for covers of library manga"""
    import requests as http_requests
    
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

    return jsonify({
        'id': title,
        'name': title,
        'chapters': chapters,
        'upscaled': upscaled,
        'source_meta': source_meta,
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
    return chapter_data[:50]


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