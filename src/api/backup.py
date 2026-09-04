#!/usr/bin/env python3
"""
Backup API - Export/import the manga + anime + novel tracking lists (titles, status,
read/watched progress) as a single portable JSON file, independent of any
downloaded chapter/episode files. Meant for moving to a new PC without
having to re-download anything just to remember what you were following.
"""

import time
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from flask import Blueprint, jsonify, request

from api.mangadex import load_local_library, save_local_library
from api.anime import _lib_read, _lib_write

backup_bp = Blueprint('backup', __name__)

_FORMAT = 'animanga-studio-backup-v1'

_ANIME_EXPORT_FIELDS = (
    'al_id', 'mal_id', 'title', 'title_romaji', 'cover', 'total_episodes',
    'format', 'status', 'watched', 'positions', 'durations',
    'last_watched_at', 'added_at',
)


def _progress_value(ch):
    try:
        return float(Decimal(str(ch)))
    except (InvalidOperation, ValueError, TypeError):
        return -1.0


def build_payload():
    """Assemble the portable profile (manga + anime + novel tracking/progress). Shared by
    the file-download route and the Git sync backend (sync.py)."""
    from api.novels import _progress_read as _novel_progress_read

    manga = load_local_library()

    anime_lib = _lib_read()
    anime = []
    for anime_id, entry in anime_lib.items():
        trimmed = {k: entry[k] for k in _ANIME_EXPORT_FIELDS if k in entry}
        trimmed['id'] = anime_id
        anime.append(trimmed)

    return {
        'format': _FORMAT,
        'exported_at': datetime.now(timezone.utc).isoformat(),
        'manga': manga,
        'anime': anime,
        'novel_progress': _novel_progress_read(),
    }


def apply_payload(data):
    """Merge an incoming profile into the local state (additive, never destroys).
    Returns per-domain counts. Shared by the file-import route and sync.restore."""
    manga_in = data.get('manga')
    anime_in = data.get('anime')
    counts = {'manga': {'added': 0, 'merged': 0}, 'anime': {'added': 0, 'merged': 0},
              'novels': {'added': 0, 'merged': 0}}

    if isinstance(manga_in, list):
        local_lib = load_local_library()
        a, m = _merge_manga(local_lib, manga_in)
        save_local_library(local_lib)
        counts['manga'] = {'added': a, 'merged': m}

    if isinstance(anime_in, list):
        lib = _lib_read()
        a, m = _merge_anime(lib, anime_in)
        _lib_write(lib)
        counts['anime'] = {'added': a, 'merged': m}

    novel_in = data.get('novel_progress')
    if isinstance(novel_in, dict):
        from api.novels import _progress_read as _novel_progress_read, merge_progress
        from api.runtime import write_json_atomic
        current = _novel_progress_read()
        merged = merge_progress(current, novel_in)
        if merged != current:
            from api.novels import _PROGRESS_PATH
            write_json_atomic(_PROGRESS_PATH, merged, indent=2, keep_backup=True)
        counts['novels'] = {
            'added': len(set(merged) - set(current)),
            'merged': len(set(merged) & set(current)),
        }

    return counts


@backup_bp.route('/export')
def export_backup():
    filename = f"animanga-studio-backup-{time.strftime('%Y-%m-%d')}.json"
    resp = jsonify(build_payload())
    resp.headers['Content-Disposition'] = f'attachment; filename="{filename}"'
    return resp


def _merge_manga(local_lib, incoming):
    by_id = {m.get('id'): m for m in local_lib if m.get('id')}
    added = merged = 0

    for entry in incoming:
        manga_id = entry.get('id')
        if not manga_id:
            continue

        existing = by_id.get(manga_id)
        if existing is None:
            entry.setdefault('added_at', time.strftime('%Y-%m-%d'))
            local_lib.append(entry)
            by_id[manga_id] = entry
            added += 1
            continue

        if _progress_value(entry.get('last_chapter')) > _progress_value(existing.get('last_chapter')):
            existing['last_chapter'] = entry.get('last_chapter')

        if (entry.get('updated_at') or '') > (existing.get('updated_at') or ''):
            if entry.get('status') is not None:
                existing['status'] = entry.get('status')
                existing['updated_at'] = entry.get('updated_at')

        if entry.get('favorite'):
            existing['favorite'] = True

        if not existing.get('cover') and entry.get('cover'):
            existing['cover'] = entry.get('cover')
        if not existing.get('title') and entry.get('title'):
            existing['title'] = entry.get('title')

        merged += 1

    return added, merged


def _merge_anime(lib, incoming):
    added = merged = 0

    for entry in incoming:
        anime_id = entry.get('id')
        if not anime_id:
            continue

        if anime_id not in lib:
            new_entry = {k: entry[k] for k in _ANIME_EXPORT_FIELDS if k in entry}
            new_entry['episodes'] = {}
            lib[anime_id] = new_entry
            added += 1
            continue

        existing = lib[anime_id]

        # Import only ever adds — it never removes or overwrites existing local
        # state, so a destination PC's own progress can't be erased by a merge.
        pre_watched = set(existing.get('watched', {}).keys())

        incoming_watched = entry.get('watched') or {}
        if incoming_watched:
            existing.setdefault('watched', {}).update(incoming_watched)

        incoming_positions = entry.get('positions') or {}
        if incoming_positions:
            existing_positions = existing.setdefault('positions', {})
            for ep, secs in incoming_positions.items():
                if ep in pre_watched or ep in incoming_watched:
                    continue  # already complete — don't add a stale resume point
                if secs > existing_positions.get(ep, 0):
                    existing_positions[ep] = secs

        if entry.get('last_watched_at', 0) > existing.get('last_watched_at', 0):
            existing['last_watched_at'] = entry.get('last_watched_at')
            if entry.get('status') is not None:
                existing['status'] = entry.get('status')

        for field in ('total_episodes', 'format', 'cover', 'title', 'title_romaji'):
            if not existing.get(field) and entry.get(field):
                existing[field] = entry.get(field)

        merged += 1

    return added, merged


@backup_bp.route('/import', methods=['POST'])
def import_backup():
    data = request.get_json(silent=True) or {}
    if all(data.get(key) is None for key in ('manga', 'anime', 'novel_progress')):
        return jsonify({'error': 'Archivo inválido: falta "manga", "anime" o "novel_progress"'}), 400

    counts = apply_payload(data)
    return jsonify({'ok': True, **counts})
