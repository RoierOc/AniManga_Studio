#!/usr/bin/env python3
"""
Download API - Download manga from MangaDex
"""

from flask import Blueprint, jsonify, request
from pathlib import Path
import json
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

_STATUS_FILE = Path(MANGA_DIR) / '.download_status.json'
_IN_FLIGHT = {'downloading', 'started', 'starting'}


def _load_download_status() -> dict:
    try:
        if _STATUS_FILE.exists():
            data = json.loads(_STATUS_FILE.read_text(encoding='utf-8'))
            for v in data.values():
                if isinstance(v, dict) and v.get('status') in _IN_FLIGHT:
                    v['status'] = 'interrupted'
            return data
    except Exception:
        pass
    return {}


def _persist_download_status():
    try:
        _STATUS_FILE.write_text(
            json.dumps(download_status, ensure_ascii=False), encoding='utf-8'
        )
    except Exception:
        pass


download_status: dict = _load_download_status()
_download_cancel_flags: dict = {}
_dl_semaphore = threading.Semaphore(4)  # max 4 concurrent chapter downloads


def _fetch_with_retry(url, retries=3, timeout=30):
    """GET with exponential backoff on 429 / network errors."""
    delay = 1.5
    for attempt in range(retries):
        try:
            r = http_requests.get(url, timeout=timeout)
            if r.status_code == 200:
                return r
            if r.status_code == 429:
                time.sleep(delay * (2 ** attempt))
                continue
            # Other non-200: don't retry
            return r
        except Exception:
            if attempt < retries - 1:
                time.sleep(delay * (2 ** attempt))
    return None


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
            if chapter_norm == "one_shot":
                if ch_num is None or ch_num == '':
                    return ch.get('id')
            elif _chapter_matches(ch_num, chapter_norm):
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
    old_status = download_status.get(task_id, {}).get('status')
    download_status[task_id] = payload
    if payload.get('status') != old_status:
        _persist_download_status()

def get_download_status(task_id=None):
    if task_id is None:
        return download_status.copy()
    return download_status.get(task_id, {'status': 'not_found', 'task_id': task_id})

def _chapter_file_prefix(chapter):
    chapter_norm = normalize_chapter(chapter)
    if chapter_norm == "one_shot":
        return "ch0000"
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


@download_bp.route('/cancel/<path:download_id>', methods=['POST'])
def cancel_download(download_id):
    _download_cancel_flags[download_id] = True
    current = download_status.get(download_id, {})
    set_download_status(download_id, {**current, 'status': 'cancelled'})
    return jsonify({'status': 'cancel_requested', 'task_id': download_id})

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

def _run_download_chapter(download_id, title, chapter_norm, chapter_id, manga_id):
    """Background thread: resolve chapter ID if needed, then download all pages."""
    with _dl_semaphore:
        folder = Path(MANGA_DIR) / title
        folder.mkdir(parents=True, exist_ok=True)

        try:
            # Resolve manga_id if missing
            if not chapter_id and not manga_id:
                search_r = http_requests.get(
                    'https://api.mangadex.org/manga',
                    params={'title': title, 'limit': 10},
                    timeout=30,
                )
                manga_data = search_r.json().get('data', [])
                if not manga_data:
                    set_download_status(download_id, {'status': 'error', 'message': 'Manga no encontrado en MangaDex'})
                    return
                target = _canonical_title(title)

                def _score(item):
                    variants = list((item.get('attributes', {}).get('title') or {}).values())
                    for v in variants:
                        c = _canonical_title(v)
                        if c == target: return 3
                        if target and target in c: return 2
                        if c and c in target: return 1
                    return 0

                manga_data.sort(key=_score, reverse=True)
                manga_id = manga_data[0]['id']

            # Resolve chapter_id if missing
            if not chapter_id:
                chapter_id = _find_chapter_id_with_feed(manga_id, chapter_norm)
            if not chapter_id:
                chapter_id = _find_chapter_id_with_chapter_endpoint(manga_id, chapter_norm)
            if not chapter_id:
                set_download_status(download_id, {'status': 'error', 'message': f'Capítulo {chapter_norm} no encontrado'})
                return

            # Fetch at-home server info
            r = http_requests.get(f'https://api.mangadex.org/at-home/server/{chapter_id}', timeout=30)
            resp_data = r.json()
            if resp_data.get('result') != 'ok':
                set_download_status(download_id, {'status': 'error', 'message': 'Capítulo no disponible'})
                return

            pages = resp_data.get('chapter', {}).get('data', [])
            if not pages:
                set_download_status(download_id, {'status': 'error', 'message': 'No hay páginas'})
                return

            base = resp_data['baseUrl']
            hash_val = resp_data['chapter']['hash']
            _download_cancel_flags[download_id] = False

            set_download_status(download_id, {
                'status': 'downloading',
                'title': title,
                'chapter': chapter_norm,
                'progress': 0,
                'total': len(pages),
            })

            downloaded_count = 0
            for i, page in enumerate(pages, 1):
                if _download_cancel_flags.get(download_id):
                    _download_cancel_flags.pop(download_id, None)
                    set_download_status(download_id, {
                        'status': 'cancelled', 'title': title,
                        'chapter': chapter_norm, 'progress': i - 1, 'total': len(pages),
                    })
                    return

                set_download_status(download_id, {
                    'status': 'downloading', 'title': title,
                    'chapter': chapter_norm, 'progress': i, 'total': len(pages),
                })

                img_url = f"{base}/data/{hash_val}/{page}"
                time.sleep(0.25)
                resp = _fetch_with_retry(img_url)
                if resp and resp.status_code == 200:
                    ext = page.split('.')[-1]
                    fname = f"{_chapter_file_prefix(chapter_norm)}_{i:03d}.{ext}"
                    with open(folder / fname, 'wb') as f:
                        f.write(resp.content)
                    downloaded_count += 1

            _download_cancel_flags.pop(download_id, None)

            if downloaded_count > 0:
                set_download_status(download_id, {
                    'status': 'complete', 'title': title, 'chapter': chapter_norm,
                    'pages': downloaded_count, 'progress': len(pages), 'total': len(pages),
                })
                from api.runtime import push_sse_event
                push_sse_event('download_complete', title=title, chapter=chapter_norm)
            else:
                set_download_status(download_id, {'status': 'error', 'message': 'No se pudo descargar ninguna página'})

        except Exception as e:
            set_download_status(download_id, {'status': 'error', 'message': str(e)})


@download_bp.route('/download_chapter', methods=['POST'])
def download_chapter():
    data = request.get_json()
    chapter_id = data.get('chapterId')
    manga_id = data.get('mangaId')
    title = data.get('title')
    chapter = data.get('chapter')

    if not title or chapter is None:
        return jsonify({'error': 'title and chapter required'}), 400

    chapter_norm = normalize_chapter(chapter)
    download_id = build_task_id(title, chapter_norm, 'download')
    set_download_status(download_id, {
        'status': 'starting', 'title': title, 'chapter': chapter_norm,
    })

    threading.Thread(
        target=_run_download_chapter,
        args=(download_id, title, chapter_norm, chapter_id, manga_id),
        daemon=True,
    ).start()

    return jsonify({'status': 'started', 'chapter': chapter_norm, 'task_id': download_id})

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

@download_bp.route('/download_source_chapter', methods=['POST'])
def download_source_chapter():
    """Download a chapter from an external source (Suwayomi page URLs)."""
    data = request.get_json(silent=True) or {}
    title = (data.get('title') or '').strip()
    chapter = data.get('chapter')
    page_urls = data.get('pageUrls') or []

    if not title or chapter is None:
        return jsonify({'error': 'title and chapter required'}), 400
    if not page_urls:
        return jsonify({'error': 'pageUrls required'}), 400

    source_id = data.get('sourceId')
    manga_id = data.get('mangaId')

    chapter_norm = normalize_chapter(chapter)
    download_id = build_task_id(title, chapter_norm, 'download')

    set_download_status(download_id, {
        'status': 'downloading',
        'title': title,
        'chapter': chapter_norm,
        'progress': 0,
        'total': len(page_urls),
        'source': 'external',
    })

    threading.Thread(
        target=_run_source_download,
        args=(download_id, title, chapter_norm, page_urls, source_id, manga_id),
        daemon=True,
    ).start()

    return jsonify({
        'status': 'started',
        'chapter': chapter_norm,
        'total': len(page_urls),
        'task_id': download_id,
    })


def _run_source_download(download_id, title, chapter_norm, page_urls, source_id=None, manga_id=None):
    import json as _json
    with _dl_semaphore:
        folder = Path(MANGA_DIR) / title
        folder.mkdir(parents=True, exist_ok=True)
        # Persist source context so the library can reload chapters from Suwayomi
        if source_id and manga_id:
            meta_path = folder / '.source_meta.json'
            if not meta_path.exists():
                try:
                    meta_path.write_text(_json.dumps({'sourceId': str(source_id), 'mangaId': int(manga_id)}))
                except Exception:
                    pass
        prefix = _chapter_file_prefix(chapter_norm)
        downloaded = 0
        total = len(page_urls)
        _download_cancel_flags[download_id] = False

        try:
            for i, url in enumerate(page_urls, 1):
                if _download_cancel_flags.get(download_id):
                    _download_cancel_flags.pop(download_id, None)
                    set_download_status(download_id, {
                        'status': 'cancelled',
                        'title': title,
                        'chapter': chapter_norm,
                        'progress': i - 1,
                        'total': total,
                    })
                    return

                set_download_status(download_id, {
                    'status': 'downloading',
                    'title': title,
                    'chapter': chapter_norm,
                    'progress': i,
                    'total': total,
                    'source': 'external',
                })
                try:
                    r = _fetch_with_retry(url, timeout=30)
                    if r and r.status_code == 200:
                        ct = r.headers.get('Content-Type', '')
                        if 'png' in ct:
                            ext = 'png'
                        elif 'webp' in ct:
                            ext = 'webp'
                        else:
                            ext = url.split('?')[0].rsplit('.', 1)[-1].lower()
                            if ext not in ('jpg', 'jpeg', 'png', 'webp'):
                                ext = 'jpg'
                        filename = f"{prefix}_{i:03d}.{ext}"
                        with open(folder / filename, 'wb') as f:
                            f.write(r.content)
                        downloaded += 1
                except Exception as e:
                    print(f"Error downloading page {i}: {e}", flush=True)
                time.sleep(0.1)

            _download_cancel_flags.pop(download_id, None)

            if downloaded > 0:
                set_download_status(download_id, {
                    'status': 'complete',
                    'title': title,
                    'chapter': chapter_norm,
                    'pages': downloaded,
                    'progress': total,
                    'total': total,
                    'source': 'external',
                })
            else:
                set_download_status(download_id, {
                    'status': 'error',
                    'message': 'No se pudo descargar ninguna página',
                    'title': title,
                    'chapter': chapter_norm,
                })
        except Exception as e:
            _download_cancel_flags.pop(download_id, None)
            set_download_status(download_id, {'status': 'error', 'message': str(e)})


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

# ── Scanlation comparison download ───────────────────────────────────────────

def _run_download_compare(download_id, title, chapter_norm, chapter_id, compare_subdir, group, lang):
    """Download a chapter variant to a _compare subfolder for side-by-side comparison."""
    with _dl_semaphore:
        folder = Path(MANGA_DIR) / title / compare_subdir
        folder.mkdir(parents=True, exist_ok=True)
        try:
            r = http_requests.get(
                f'https://api.mangadex.org/at-home/server/{chapter_id}',
                timeout=30,
            )
            resp_data = r.json()
            if resp_data.get('result') != 'ok':
                set_download_status(download_id, {'status': 'error', 'message': 'Capítulo no disponible'})
                return

            pages = resp_data.get('chapter', {}).get('data', [])
            base = resp_data['baseUrl']
            hash_val = resp_data['chapter']['hash']

            set_download_status(download_id, {
                'status': 'downloading', 'title': title, 'chapter': chapter_norm,
                'group': group, 'lang': lang, 'progress': 0, 'total': len(pages),
            })

            for i, page in enumerate(pages, 1):
                set_download_status(download_id, {
                    'status': 'downloading', 'title': title, 'chapter': chapter_norm,
                    'group': group, 'lang': lang, 'progress': i, 'total': len(pages),
                })
                img_url = f"{base}/data/{hash_val}/{page}"
                time.sleep(0.25)
                resp = _fetch_with_retry(img_url)
                if resp and resp.status_code == 200:
                    ext = page.split('.')[-1]
                    fname = f"{_chapter_file_prefix(chapter_norm)}_{i:03d}.{ext}"
                    with open(folder / fname, 'wb') as fh:
                        fh.write(resp.content)

            (folder / '.meta.json').write_text(
                json.dumps({'group': group, 'lang': lang, 'chapter': chapter_norm,
                            'chapter_id': chapter_id, 'pages': len(pages)},
                           ensure_ascii=False),
                encoding='utf-8',
            )
            set_download_status(download_id, {
                'status': 'complete', 'title': title, 'chapter': chapter_norm,
                'group': group, 'lang': lang, 'pages': len(pages),
                'progress': len(pages), 'total': len(pages),
            })
        except Exception as e:
            set_download_status(download_id, {'status': 'error', 'message': str(e)})


@download_bp.route('/download_compare', methods=['POST'])
def download_compare():
    """Download a MangaDex chapter variant to _compare subfolder."""
    data = request.get_json(silent=True) or {}
    chapter_id = (data.get('chapterId') or '').strip()
    title      = (data.get('title')     or '').strip()
    chapter    = data.get('chapter')
    group      = (data.get('group')     or 'unknown').strip()
    lang       = (data.get('lang')      or 'en').strip()

    if not all([chapter_id, title, chapter is not None]):
        return jsonify({'error': 'chapterId, title, chapter required'}), 400

    chapter_norm = normalize_chapter(str(chapter))
    group_slug   = re.sub(r'[^\w]', '_', group)[:28]
    prefix       = _chapter_file_prefix(chapter_norm).rstrip('_')
    compare_subdir = f"_compare/{prefix}_{group_slug}_{lang}"
    download_id  = f"cmp_{sanitize_title_for_id(title)}_ch{chapter_norm}_{group_slug}"

    threading.Thread(
        target=_run_download_compare,
        args=(download_id, title, chapter_norm, chapter_id, compare_subdir, group, lang),
        daemon=True,
    ).start()

    return jsonify({'download_id': download_id, 'compare_subdir': compare_subdir})
