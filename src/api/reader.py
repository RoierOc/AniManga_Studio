#!/usr/bin/env python3
"""
Reader API - Read manga chapters
"""

from flask import Blueprint, jsonify, request, send_from_directory
from pathlib import Path
import random
from decimal import Decimal, InvalidOperation

from api.runtime import MANGA_DIR, UPSCALED_DIR, normalize_chapter


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

    upscaled_folder = Path(UPSCALED_DIR) / actual_folder
    original_folder = Path(MANGA_DIR) / actual_folder

    if source == 'original':
        folder = original_folder
    elif source == 'upscaled':
        folder = upscaled_folder
    else:
        # Auto: use upscaled only if this specific chapter has pages there
        if upscaled_folder.exists() and chapter_pages(upscaled_folder):
            folder = upscaled_folder
        else:
            folder = original_folder

    if not folder.exists():
        return jsonify({'pages': [], 'error': 'Folder not found'})

    pages = chapter_pages(folder)
    return jsonify({'pages': [f"{actual_folder}/{p}" for p in pages], 'folder': actual_folder, 'source': source})

@reader_bp.route('/random')
def random_chapter():
    all_chapters = []
    
    for folder in Path(MANGA_DIR).iterdir():
        if not folder.is_dir():
            continue
        
        images = list(folder.glob('*.jpg')) + list(folder.glob('*.png'))
        if images:
            all_chapters.append(folder.name)
    
    if not all_chapters:
        return jsonify({'error': 'No manga in library'})
    
    import random
    title = random.choice(all_chapters)
    
    images = list((Path(MANGA_DIR) / title).glob('*.jpg'))
    if images:
        ch = images[0].stem.split('_')[0][2:]
    else:
        ch = '001'
    
    return jsonify({'title': title, 'chapter': ch})