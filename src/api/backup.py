#!/usr/bin/env python3
"""
Backup API - Export/import the manga + anime tracking lists (titles, status,
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


@backup_bp.route('/export')
def export_backup():
    manga = load_local_library()

    anime_lib = _lib_read()
    anime = []
    for anime_id, entry in anime_lib.items():
        trimmed = {k: entry[k] for k in _ANIME_EXPORT_FIELDS if k in entry}
        trimmed['id'] = anime_id
        anime.append(trimmed)

    payload = {
        'format': _FORMAT,
        'exported_at': datetime.now(timezone.utc).isoformat(),
        'manga': manga,
        'anime': anime,
    }

    filename = f"animanga-studio-backup-{time.strftime('%Y-%m-%d')}.json"
    resp = jsonify(payload)
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
    manga_in = data.get('manga')
    anime_in = data.get('anime')

    if manga_in is None and anime_in is None:
        return jsonify({'error': 'Archivo inválido: falta "manga" o "anime"'}), 400

    manga_added = manga_merged = anime_added = anime_merged = 0

    if isinstance(manga_in, list):
        local_lib = load_local_library()
        manga_added, manga_merged = _merge_manga(local_lib, manga_in)
        save_local_library(local_lib)

    if isinstance(anime_in, list):
        lib = _lib_read()
        anime_added, anime_merged = _merge_anime(lib, anime_in)
        _lib_write(lib)

    return jsonify({
        'ok': True,
        'manga': {'added': manga_added, 'merged': manga_merged},
        'anime': {'added': anime_added, 'merged': anime_merged},
    })
