"""Granular qBittorrent file selection for anime batch torrents.

The legacy anime routes keep their episode=0 behaviour for old library records.  New
batch selections are stored by qBittorrent file index so multi-season torrents cannot
collapse S01E01 and S02E01 into the same episode slot.
"""
from __future__ import annotations

import posixpath
import re
import time
from pathlib import Path

from flask import Blueprint, jsonify, request

from api import anime

anime_batch_bp = Blueprint('anime_batch', __name__)

_HASH_RE = re.compile(r'^[0-9a-f]{40}$', re.I)
_VIDEO_EXTS = {'.mkv', '.mp4', '.avi', '.mov', '.wmv', '.m4v', '.webm', '.flv', '.ts', '.m2ts'}
_METADATA_PROBES: set[str] = set()
_METADATA_PROBE_LOCK = __import__('threading').Lock()


def _probe_metadata(info_hash: str, info: dict) -> None:
    """Briefly start a stopped magnet so qBittorrent can fetch metadata.

    The batch invariant remains paused-before-selection: once `/torrents/files`
    exposes the file list, `_stop_metadata_probe` stops the torrent before the
    response is returned. The in-memory set prevents repeated starts per hash.
    """
    state = str(info.get('state') or '')
    if state not in {'stoppedDL', 'metaDL', 'pausedDL'}:
        return
    with _METADATA_PROBE_LOCK:
        if info_hash in _METADATA_PROBES:
            return
        _METADATA_PROBES.add(info_hash)
    response = anime._q('post', '/torrents/start', data={'hashes': info_hash})
    if response.status_code not in (200, 204):
        with _METADATA_PROBE_LOCK:
            _METADATA_PROBES.discard(info_hash)
        raise RuntimeError(f'qBittorrent metadata probe failed ({response.status_code})')


def _stop_metadata_probe(info_hash: str) -> None:
    with _METADATA_PROBE_LOCK:
        probed = info_hash in _METADATA_PROBES
    if not probed:
        return
    response = anime._q('post', '/torrents/stop', data={'hashes': info_hash})
    if response.status_code in (400, 404):
        anime._q('post', '/torrents/pause', data={'hashes': info_hash})
    with _METADATA_PROBE_LOCK:
        _METADATA_PROBES.discard(info_hash)


def _hash(value: str) -> str:
    value = (value or '').strip().lower()
    return value if _HASH_RE.fullmatch(value) else ''


def _safe_relative(name: str) -> str:
    """Normalize qBittorrent's relative name without accepting an absolute path."""
    raw = str(name or '').replace('\\', '/')
    if not raw or raw.startswith('/') or re.match(r'^[A-Za-z]:/', raw):
        return ''
    normalized = posixpath.normpath(raw)
    if normalized in ('', '.') or normalized == '..' or normalized.startswith('../'):
        return ''
    return normalized


def _season_episode(name: str) -> tuple[int | None, int | None, str]:
    stem = Path(name).stem
    match = re.search(r'(?<![A-Za-z0-9])S(\d{1,2})E(\d{1,4})(?!\d)', stem, re.I)
    if match:
        return int(match.group(1)), int(match.group(2)), 'episode'
    season_match = re.search(r'\b(?:season|temporada)[ _.-]*(\d{1,2})\b', name, re.I)
    season = int(season_match.group(1)) if season_match else None
    episode = anime._parse_episode(stem)
    return season, episode if episode > 0 else None, 'episode'


def _content_file(info: dict, relative_path: str) -> Path | None:
    # qBittorrent's `name` is relative to save_path and includes the torrent root
    # folder. `content_path` already points inside that root, so joining against it
    # duplicates the folder on multi-file torrents.
    raw = str(info.get('save_path') or info.get('content_path') or '').strip()
    if not raw or not relative_path:
        return None
    if anime._is_wsl() and re.match(r'^[A-Za-z]:[/\\]', raw):
        raw = anime._win_to_wsl(raw)
    base = Path(raw)
    if base.is_file():
        return base if base.name == Path(relative_path).name else None
    candidate = (base / relative_path).resolve()
    try:
        if base.resolve() not in candidate.parents and candidate != base.resolve():
            return None
    except OSError:
        return None
    return candidate


def _torrent_info(info_hash: str) -> dict | None:
    rows = anime._q('get', '/torrents/info', params={'hashes': info_hash}).json()
    return next((row for row in rows if str(row.get('hash', '')).lower() == info_hash), None)


def _raw_files(info_hash: str) -> tuple[dict | None, list]:
    info = _torrent_info(info_hash)
    if info is None:
        return None, []
    response = anime._q('get', '/torrents/files', params={'hash': info_hash})
    return info, response.json() or []


def _normalize_files(info_hash: str, info: dict, raw_files: list) -> list[dict]:
    result = []
    for raw in raw_files:
        try:
            index = int(raw.get('index'))
        except (TypeError, ValueError):
            continue
        relative_path = _safe_relative(raw.get('name', ''))
        if not relative_path:
            continue
        suffix = Path(relative_path).suffix.lower()
        is_video = suffix in _VIDEO_EXTS
        season, episode, ep_type = _season_episode(relative_path) if is_video else (None, None, 'file')
        local_path = _content_file(info, relative_path)
        declared_size = int(raw.get('size') or 0)
        progress = round(float(raw.get('progress') or 0) * 100, 1)
        already_local = bool(
            local_path and local_path.is_file()
            and progress >= 100
        )
        priority = int(raw.get('priority') or 0)
        # A local file is deliberately not selected by default: it is preserved and
        # qBittorrent's priority is left unchanged until the user confirms the batch.
        selected = bool(is_video and not already_local and priority > 0)
        result.append({
            'id': index,
            'name': relative_path,
            'relative_path': relative_path,
            'size': declared_size,
            'progress': progress,
            'priority': priority,
            'selected': selected,
            'already_local': already_local,
            'is_video': is_video,
            'season': season,
            'episode': episode,
            'ep_type': ep_type,
            'group': f'Season {season:02d}' if season is not None else 'Other files',
        })
    return result


def _files_payload(info_hash: str, info: dict, raw_files: list) -> dict:
    files = _normalize_files(info_hash, info, raw_files)
    groups = []
    seen = set()
    for item in files:
        group = item['group']
        if group not in seen:
            seen.add(group)
            groups.append(group)
    return {
        'hash': info_hash,
        'torrent_name': info.get('name', ''),
        'state': info.get('state', ''),
        'metadata_pending': not bool(files),
        'files': files,
        'groups': groups,
    }


@anime_batch_bp.route('/add', methods=['POST'])
def batch_add():
    data = request.get_json(silent=True) or {}
    link = (data.get('magnet') or data.get('torrent_url') or '').strip()
    anime_id = str(data.get('anime_id') or '').strip()
    if not link:
        return jsonify({'error': 'no link'}), 400
    if not _hash(data.get('info_hash', '')):
        return jsonify({'error': 'info_hash required for batch selection'}), 400
    if not anime_id:
        return jsonify({'error': 'anime_id required'}), 400
    if anime_id not in anime._lib_read():
        return jsonify({'error': 'anime not found'}), 404

    info_hash = _hash(data.get('info_hash', ''))
    try:
        existing = _torrent_info(info_hash) if info_hash else None
        if existing is None:
            title = anime._lib_read()[anime_id].get('title') or ''
            save_dir = anime._anime_save_dir(title)
            # `stopped` is the current WebAPI spelling; keep `paused` as the
            # compatibility alias used by older qBittorrent releases.
            form = {'urls': link, 'stopped': 'true', 'paused': 'true'}
            if save_dir:
                form['savepath'] = save_dir
            response = anime._q('post', '/torrents/add', data=form)
            body = response.text.strip()
            accepted = response.status_code in (200, 204) and body in ('', 'Ok.')
            if not accepted and response.status_code == 200:
                try:
                    payload = response.json()
                    accepted = payload.get('success_count', 0) > 0 and payload.get('failure_count', 0) == 0
                except Exception:
                    accepted = False
            if not accepted:
                return jsonify({'error': body or 'qBittorrent rechazó el torrent'}), 502
        else:
            # Reusing a torrent must still honor the selector's invariant:
            # no file may continue downloading before confirmation.
            stopped = anime._q('post', '/torrents/stop', data={'hashes': info_hash})
            if stopped.status_code in (400, 404):
                anime._q('post', '/torrents/pause', data={'hashes': info_hash})

        # Nyaa gives us the hash, but a magnet may still need a short moment before
        # qBittorrent exposes its torrent record. The UI will poll /files afterwards.
        if info_hash:
            try:
                info = _torrent_info(info_hash)
            except Exception:
                info = None
        else:
            info = None
        return jsonify({
            'ok': True,
            'hash': info_hash,
            'metadata_pending': info is None,
            'state': (info or {}).get('state', 'metadata_pending'),
        }), 202 if info is None else 200
    except Exception as exc:
        anime.record_error('anime', exc, op='batch_add')
        return jsonify({'error': str(exc) or 'qBittorrent no responde'}), 502


@anime_batch_bp.route('/files')
def batch_files():
    info_hash = _hash(request.args.get('hash', ''))
    if not info_hash:
        return jsonify({'error': 'valid info_hash required'}), 400
    try:
        info, raw_files = _raw_files(info_hash)
        if info is None:
            # qBittorrent may accept a magnet before registering its torrent
            # record. Treat that window as pending metadata, not a hard failure.
            return jsonify({'hash': info_hash, 'metadata_pending': True,
                            'files': [], 'groups': []}), 202
        if not raw_files:
            _probe_metadata(info_hash, info)
            payload = _files_payload(info_hash, info, raw_files)
            payload['metadata_pending'] = True
            return jsonify(payload), 202
        _stop_metadata_probe(info_hash)
        payload = _files_payload(info_hash, info, raw_files)
        if not payload['files']:
            payload['metadata_pending'] = True
            return jsonify(payload), 202
        return jsonify(payload)
    except Exception as exc:
        anime.record_error('anime', exc, op='batch_files', hash=info_hash)
        return jsonify({'error': str(exc) or 'no se pudieron leer los archivos'}), 502


def _priority(info_hash: str, ids: list[int], value: int):
    if not ids:
        return
    response = anime._q('post', '/torrents/filePrio', data={
        'hash': info_hash,
        'id': '|'.join(str(i) for i in ids),
        'priority': str(value),
    })
    if response.status_code not in (200, 204):
        raise RuntimeError(f'qBittorrent filePrio failed ({response.status_code})')


def _episode_slot(file: dict) -> str | None:
    episode = file.get('episode')
    if not isinstance(episode, int) or episode <= 0:
        return None
    season = file.get('season')
    return f's{int(season):02d}e{episode:03d}' if season is not None else str(episode)


def _persist_selection(anime_id: str, info_hash: str, info: dict, files: list[dict], selected: set[int]):
    lib = anime._lib_read()
    entry = lib.get(anime_id)
    if entry is None:
        raise LookupError('anime not found')
    now = int(time.time())
    batch = entry.setdefault('batch_files', {}).setdefault(info_hash, {
        'torrent_title': info.get('name', ''), 'files': {}, 'added_on': now,
    })
    batch['torrent_title'] = info.get('name', '') or batch.get('torrent_title', '')
    episodes = entry.setdefault('episodes', {})
    # Reconfiguring a partial batch must not leave stale download references for
    # files the user explicitly omitted. Completed/local files remain visible.
    by_id = {int(file['id']): file for file in files}
    protected_ids = {
        int(file['id']) for file in files
        if file.get('already_local') or file.get('progress', 0) >= 100
    }
    for old_id in list(batch.get('files', {})):
        if int(old_id) not in selected and int(old_id) not in protected_ids:
            batch['files'].pop(old_id, None)
    for slot, old_record in list(episodes.items()):
        if old_record.get('info_hash') != info_hash or not old_record.get('from_batch_selection'):
            continue
        old_id = old_record.get('file_index')
        if old_id is None or int(old_id) in selected:
            continue
        current = by_id.get(int(old_id))
        if current and (current.get('already_local') or current.get('progress', 0) >= 100):
            continue
        episodes.pop(slot, None)
    for file in files:
        file_id = int(file['id'])
        if file_id not in selected:
            continue
        record = {
            'title': Path(file['relative_path']).stem,
            'info_hash': info_hash,
            'file_index': file_id,
            'relative_path': file['relative_path'],
            'added_on': now,
            'from_batch_selection': True,
        }
        if file.get('season') is not None:
            record['season'] = int(file['season'])
        if file.get('episode') is not None:
            record['episode'] = int(file['episode'])
        batch['files'][str(file_id)] = record
        slot = _episode_slot(file)
        if slot:
            episodes[slot] = dict(record)
    # Keep a batch marker for administration, but never pre-fill every AniList slot.
    episodes['0'] = {
        'title': info.get('name', ''), 'info_hash': info_hash, 'added_on': now,
        'from_batch_selection': True,
    }
    anime._lib_write(lib)


@anime_batch_bp.route('/apply', methods=['POST'])
def batch_apply():
    data = request.get_json(silent=True) or {}
    info_hash = _hash(data.get('hash', ''))
    anime_id = str(data.get('anime_id') or '').strip()
    selected_raw = data.get('selected_file_ids')
    if not info_hash or not anime_id or not isinstance(selected_raw, list):
        return jsonify({'error': 'hash, anime_id and selected_file_ids required'}), 400
    try:
        selected = {int(value) for value in selected_raw}
    except (TypeError, ValueError):
        return jsonify({'error': 'selected_file_ids must contain integers'}), 400
    try:
        info, raw_files = _raw_files(info_hash)
        if info is None:
            return jsonify({'error': 'torrent not found'}), 404
        files = _normalize_files(info_hash, info, raw_files)
        valid_ids = {int(file['id']) for file in files}
        if not selected <= valid_ids:
            return jsonify({'error': 'selected file id does not belong to this torrent'}), 409
        if not selected:
            return jsonify({'error': 'select at least one file'}), 400
        selected_ids = sorted(selected)
        # Complete/local files are preserved for seeding even when the user
        # leaves them unchecked. Priority 0 is only for files that still need
        # a download and were explicitly omitted.
        protected_ids = {
            int(file['id']) for file in files
            if file.get('already_local') or file.get('progress', 0) >= 100
        }
        omitted_ids = sorted(valid_ids - selected - protected_ids)
        _priority(info_hash, selected_ids, 1)
        _priority(info_hash, omitted_ids, 0)
        if data.get('resume', True):
            response = anime._q('post', '/torrents/start', data={'hashes': info_hash})
            if response.status_code == 404:
                anime._q('post', '/torrents/resume', data={'hashes': info_hash})
        _persist_selection(anime_id, info_hash, info, files, selected)
        return jsonify({'ok': True, 'hash': info_hash, 'selected_file_ids': selected_ids,
                        'omitted_file_ids': omitted_ids})
    except LookupError as exc:
        return jsonify({'error': str(exc)}), 404
    except Exception as exc:
        anime.record_error('anime', exc, op='batch_apply', hash=info_hash)
        return jsonify({'error': str(exc) or 'no se pudieron aplicar las prioridades'}), 502
