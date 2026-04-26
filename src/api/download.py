#!/usr/bin/env python3
"""
Download API - Download manga from MangaDex
"""

from flask import Blueprint, jsonify, request
from pathlib import Path
import time
import threading
import requests as http_requests
import subprocess
import re
from decimal import Decimal, InvalidOperation

from api.runtime import (
    MANGA_DIR,
    UPSCALED_DIR,
    PROJECT_ROOT,
    PYTHON_EXECUTABLE,
    build_task_id,
    normalize_chapter,
    sanitize_title_for_id,
)


download_bp = Blueprint('download', __name__)

download_status = {}


def _canonical_title(value):
    if value is None:
        return ""
    text = str(value).strip().lower()
    return ''.join(ch for ch in text if ch.isalnum())


def _chapter_matches(candidate, target_norm):
    candidate_norm = normalize_chapter(candidate)
    if candidate_norm == target_norm:
        return True

    raw = str(candidate or "").strip().lower()
    if raw.startswith('ch'):
        raw = raw[2:].strip()

    # MangaDex occasionally exposes chapter strings with suffixes; match numeric prefix.
    match = re.match(r'^(\d+(?:\.\d+)?)', raw)
    if not match:
        return False

    return normalize_chapter(match.group(1)) == target_norm


def _find_chapter_id_with_feed(manga_id, chapter_norm):
    offset = 0
    while True:
        feed_r = http_requests.get(
            f'https://api.mangadex.org/manga/{manga_id}/feed',
            params={
                'limit': 500,
                'offset': offset,
                'order[chapter]': 'asc',
            },
            timeout=30
        )
        feed_data = feed_r.json()
        chapters_found = feed_data.get('data', []) or []
        total = feed_data.get('total')

        if not chapters_found:
            return None

        for ch in chapters_found:
            ch_num = ch.get('attributes', {}).get('chapter', '')
            if _chapter_matches(ch_num, chapter_norm):
                return ch.get('id')

        offset += len(chapters_found)

        if total is not None:
            try:
                if offset >= int(total):
                    return None
            except (TypeError, ValueError):
                pass

        # Safety stop if API repeats pages unexpectedly.
        if len(chapters_found) == 0:
            return None


def _find_chapter_id_with_chapter_endpoint(manga_id, chapter_norm):
    # Try official chapter search endpoint as a fallback.
    for manga_param in ('manga', 'manga[]'):
        try:
            resp = http_requests.get(
                'https://api.mangadex.org/chapter',
                params={
                    manga_param: manga_id,
                    'chapter': chapter_norm,
                    'limit': 100,
                    'order[chapter]': 'asc',
                },
                timeout=30,
            )
            payload = resp.json()
            for ch in payload.get('data', []) or []:
                ch_num = ch.get('attributes', {}).get('chapter', '')
                if _chapter_matches(ch_num, chapter_norm):
                    return ch.get('id')
        except Exception:
            continue
    return None

def set_download_status(task_id, status):
    payload = dict(status)
    payload.setdefault('task_id', task_id)
    download_status[task_id] = payload

def get_download_status(task_id=None):
    if task_id is None:
        return download_status.copy()
    return download_status.get(task_id, {'status': 'not_found', 'task_id': task_id})

def _chapter_file_prefix(chapter):
    chapter_norm = normalize_chapter(chapter)
    try:
        value = Decimal(chapter_norm)
        int_part = int(value.to_integral_value(rounding='ROUND_FLOOR'))
        if '.' in chapter_norm:
            return f"ch{int_part:04d}.{chapter_norm.split('.')[-1]}"
        return f"ch{int_part:04d}"
    except (InvalidOperation, ValueError):
        return f"ch{chapter_norm}"

@download_bp.route('/status', methods=['GET'])
def get_all_download_status_route():
    return jsonify(get_download_status())

@download_bp.route('/status/<path:task_id>', methods=['GET'])
def get_download_status_route(task_id):
    return jsonify(get_download_status(task_id))

@download_bp.route('/download', methods=['POST'])
def download_manga():
    data = request.get_json()
    manga_id = data.get('mangaId')
    title = data.get('title')
    max_chapters = data.get('maxChapters', 10)
    
    if not title:
        return jsonify({'status': 'error', 'message': 'title required'}), 400

    if not manga_id:
        return jsonify({'status': 'error', 'message': 'mangaId required'}), 400
    
    folder = Path(MANGA_DIR) / title
    folder.mkdir(parents=True, exist_ok=True)

    download_id = f"{sanitize_title_for_id(title)}_download_all"
    
    set_download_status(download_id, {'status': 'started', 'title': title, 'chapters': 0, 'max': max_chapters})
    
    threading.Thread(target=run_download, args=(download_id, manga_id, title, max_chapters), daemon=True).start()
    
    return jsonify({'status': 'started', 'title': title, 'folder': str(folder), 'task_id': download_id})

@download_bp.route('/download_chapter', methods=['POST'])
def download_chapter():
    data = request.get_json()
    chapter_id = data.get('chapterId')
    manga_id = data.get('mangaId')
    title = data.get('title')
    chapter = data.get('chapter')

    if not title or chapter is None:
        return jsonify({'error': 'title and chapter required'}), 400

    download_id = build_task_id(title, chapter, 'download')
    chapter_norm = normalize_chapter(chapter)
    set_download_status(download_id, {
        'status': 'starting',
        'title': title,
        'chapter': chapter_norm,
    })
    
    # If no chapterId, look it up from MangaDex
    if not chapter_id:
        try:
            # Resolve manga id if missing by searching and picking the closest title
            if not manga_id:
                search_r = http_requests.get(
                    'https://api.mangadex.org/manga',
                    params={'title': title, 'limit': 10},
                    timeout=30
                )
                search_data = search_r.json()
                manga_data = search_data.get('data', [])
                if not manga_data:
                    message = 'Manga no encontrado en MangaDex'
                    set_download_status(download_id, {'status': 'error', 'message': message})
                    return jsonify({'error': message, 'task_id': download_id}), 404

                target_title = _canonical_title(title)

                def score_candidate(item):
                    attrs = item.get('attributes', {})
                    title_map = attrs.get('title', {}) or {}
                    variants = [v for v in title_map.values() if isinstance(v, str)]
                    if not variants:
                        return 0
                    best = 0
                    for variant in variants:
                        canonical = _canonical_title(variant)
                        if canonical == target_title:
                            return 3
                        if target_title and target_title in canonical:
                            best = max(best, 2)
                        elif canonical and canonical in target_title:
                            best = max(best, 1)
                    return best

                manga_data.sort(key=score_candidate, reverse=True)
                manga_id = manga_data[0]['id']

            chapter_id = _find_chapter_id_with_feed(manga_id, chapter_norm)
            if not chapter_id:
                chapter_id = _find_chapter_id_with_chapter_endpoint(manga_id, chapter_norm)

            if not chapter_id:
                message = f'Capítulo {chapter} no encontrado'
                set_download_status(download_id, {'status': 'error', 'message': message})
                return jsonify({'error': message, 'task_id': download_id, 'mangaId': manga_id}), 404

        except Exception as e:
            message = 'Error buscando capítulo: ' + str(e)
            set_download_status(download_id, {'status': 'error', 'message': message})
            return jsonify({'error': message, 'task_id': download_id}), 500

    folder = Path(MANGA_DIR) / title
    folder.mkdir(parents=True, exist_ok=True)

    try:
        r = http_requests.get(f'https://api.mangadex.org/at-home/server/{chapter_id}', timeout=30)
        resp_data = r.json()

        if resp_data.get('result') != 'ok':
            set_download_status(download_id, {'status': 'error', 'message': 'Capítulo no disponible'})
            return jsonify({'error': 'Capítulo no disponible en MangaDex', 'pages': 0, 'task_id': download_id}), 404

        pages = resp_data.get('chapter', {}).get('data', [])
        if not pages:
            set_download_status(download_id, {'status': 'error', 'message': 'No hay páginas'})
            return jsonify({'error': 'No hay páginas disponibles', 'pages': 0, 'task_id': download_id}), 404

        set_download_status(download_id, {
            'status': 'downloading',
            'title': title,
            'chapter': chapter_norm,
            'progress': 0,
            'total': len(pages),
        })

        base = resp_data['baseUrl']
        hash_val = resp_data['chapter']['hash']

        downloaded_count = 0
        for i, page in enumerate(pages, 1):
            set_download_status(download_id, {
                'status': 'downloading',
                'title': title,
                'chapter': chapter_norm,
                'progress': i,
                'total': len(pages),
            })
            img_url = f"{base}/data/{hash_val}/{page}"
            time.sleep(0.2)

            r = http_requests.get(img_url)
            if r.status_code == 200:
                ext = page.split('.')[-1]
                filename = f"{_chapter_file_prefix(chapter_norm)}_{i:03d}.{ext}"
                with open(folder / filename, 'wb') as f:
                    f.write(r.content)
                downloaded_count += 1

        if downloaded_count > 0:
            set_download_status(download_id, {
                'status': 'complete',
                'title': title,
                'chapter': chapter_norm,
                'pages': downloaded_count,
                'progress': len(pages),
                'total': len(pages),
            })
            return jsonify({'status': 'started', 'chapter': chapter_norm, 'pages': downloaded_count, 'task_id': download_id})

        set_download_status(download_id, {'status': 'error', 'message': 'No se pudo descargar ninguna página'})
        return jsonify({'error': 'No se pudo descargar ninguna página', 'pages': 0, 'task_id': download_id}), 500

    except Exception as e:
        set_download_status(download_id, {'status': 'error', 'message': str(e)})
        return jsonify({'error': str(e), 'pages': 0, 'task_id': download_id}), 500

@download_bp.route('/download_cli', methods=['POST'])
def download_cli():
    data = request.get_json()
    title = data.get('title')
    chapters = data.get('chapters', 5)
    
    if not title:
        return jsonify({'status': 'error', 'message': 'title required'}), 400

    download_id = f"{sanitize_title_for_id(title)}_download_cli"
    
    def run():
        set_download_status(download_id, {'status': 'starting', 'title': title, 'chapters': chapters})

        try:
            result = subprocess.run(
                [PYTHON_EXECUTABLE, str(PROJECT_ROOT / 'manga_cli.py'), 'download', title, '--chapters', str(chapters)],
                cwd=str(PROJECT_ROOT),
                capture_output=True,
                text=True,
                timeout=300
            )

            output = result.stdout + result.stderr
            if "0 English chapters" in output:
                set_download_status(download_id, {'status': 'no_chapters', 'message': 'No chapters available'})
            elif result.returncode == 0:
                set_download_status(download_id, {'status': 'complete', 'message': 'Download complete'})
            else:
                set_download_status(download_id, {'status': 'error', 'message': result.stderr[:200]})
        except Exception as e:
            set_download_status(download_id, {'status': 'error', 'message': str(e)})

    threading.Thread(target=run, daemon=True).start()

    return jsonify({'status': 'started', 'title': title, 'download_id': download_id})

@download_bp.route('/delete_chapter', methods=['POST'])
def delete_chapter():
    """Delete a downloaded chapter"""
    data = request.get_json()
    title = data.get('title')
    chapter = data.get('chapter')

    if not title or not chapter:
        return jsonify({'error': 'title and chapter required'}), 400

    try:
        prefix = _chapter_file_prefix(chapter)
        pattern = f"{prefix}_*"
        deleted = 0

        # Delete from downloaded folder
        folder = Path(MANGA_DIR) / title
        if folder.exists():
            for f in folder.glob(pattern):
                f.unlink()
                deleted += 1

        # Also delete from upscaled folder
        upscaled_folder = Path(UPSCALED_DIR) / title
        if upscaled_folder.exists():
            for f in upscaled_folder.glob(pattern):
                f.unlink()
                deleted += 1

        if deleted > 0:
            return jsonify({'status': 'ok', 'deleted': deleted})
        return jsonify({'error': 'Capítulo no encontrado'}), 404

    except Exception as e:
        return jsonify({'error': str(e)}), 500

@download_bp.route('/delete_manga', methods=['DELETE'])
def delete_manga():
    """Delete a manga and all its chapters"""
    data = request.get_json()
    title = data.get('title')
    
    if not title:
        return jsonify({'error': 'title required'}), 400
    
    try:
        import shutil
        folder = Path(MANGA_DIR) / title
        upscaled_folder = Path(UPSCALED_DIR) / title

        deleted = 0
        if folder.exists():
            shutil.rmtree(folder)
            deleted += 1
        if upscaled_folder.exists():
            shutil.rmtree(upscaled_folder)
            deleted += 1

        if deleted > 0:
            return jsonify({'status': 'ok', 'deleted': deleted})
        return jsonify({'error': 'Manga no encontrado'}), 404

    except Exception as e:
        return jsonify({'error': str(e)}), 500

def run_download(download_id, manga_id, title, max_chapters):
    try:
        chapters_by_num = {}
        offset = 0

        while len(chapters_by_num) < max_chapters:
            r = http_requests.get(
                f'https://api.mangadex.org/manga/{manga_id}/feed',
                params={'limit': 100, 'offset': offset, 'translatedLanguage[]': ['en']},
            )
            data = r.json()
            items = data.get('data', [])

            if not items:
                break

            for item in items:
                ch = item['attributes']
                num = ch.get('chapter', '0')
                if not num:
                    continue

                if num not in chapters_by_num:
                    chapters_by_num[num] = item['id']

            offset += 100
            if len(items) < 100:
                break

        chapters = sorted(chapters_by_num.keys(), key=lambda x: float(x) if x.replace('.', '').isdigit() else 0, reverse=True)[:max_chapters]

        folder = Path(MANGA_DIR) / title
        folder.mkdir(parents=True, exist_ok=True)

        for i, ch in enumerate(chapters):
            set_download_status(download_id, {
                'status': 'downloading',
                'title': title,
                'chapter': normalize_chapter(ch),
                'progress': i + 1,
                'total': len(chapters),
            })

            try:
                chapter_id = chapters_by_num[ch]
                r = http_requests.get(f'https://api.mangadex.org/at-home/server/{chapter_id}', timeout=30)
                resp_data = r.json()

                if resp_data.get('result') != 'ok':
                    continue

                pages = resp_data.get('chapter', {}).get('data', [])
                if not pages:
                    continue

                base = resp_data['baseUrl']
                hash_val = resp_data['chapter']['hash']

                for j, page in enumerate(pages, 1):
                    img_url = f"{base}/data/{hash_val}/{page}"
                    time.sleep(0.3)

                    r = http_requests.get(img_url)
                    if r.status_code == 200:
                        ext = page.split('.')[-1]
                        filename = f"{_chapter_file_prefix(ch)}_{j:03d}.{ext}"
                        with open(folder / filename, 'wb') as f:
                            f.write(r.content)
            except Exception as e:
                print(f"Error downloading chapter {ch}: {e}")

        set_download_status(download_id, {'status': 'complete', 'title': title})

    except Exception as e:
        set_download_status(download_id, {'status': 'error', 'message': str(e)})