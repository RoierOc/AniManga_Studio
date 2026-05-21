#!/usr/bin/env python3
"""
Anime tab — AniList metadata + Nyaa torrent search + qBittorrent integration.
No API keys required for AniList or Nyaa.
qBittorrent WebUI credentials: QBT_URL / QBT_USERNAME / QBT_PASSWORD env vars.
"""
import os
import re
import time
import json
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path as _Path
from urllib.parse import urlencode, quote

import requests as _http
from flask import Blueprint, jsonify, request, send_file

anime_bp = Blueprint('anime', __name__)

_ANILIST = 'https://graphql.anilist.co'
_JIKAN   = 'https://api.jikan.moe/v4'
_NYAA    = 'https://nyaa.si'
_NS      = '{https://nyaa.si/xmlns/nyaa}'

_search_cache:   dict = {}
_nyaa_cache:     dict = {}
_seasonal_cache: dict = {}
_SEASONAL_TTL = 600  # 10 min

_THUMBS_DIR = _Path.home() / '.cache' / 'manga-upscaler' / 'thumbs'

# ── Anime library persistence ──────────────────────────────────────────────────

def _lib_path() -> _Path:
    return _Path(os.environ.get('MANGA_DIR', str(_Path.home() / 'MangaLibrary'))) / 'anime_library.json'

def _lib_read() -> dict:
    try:
        return json.loads(_lib_path().read_text(encoding='utf-8'))
    except Exception:
        return {}

def _lib_write(data: dict):
    p = _lib_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding='utf-8')


# ── MPV / video helpers ────────────────────────────────────────────────────────

_VIDEO_EXTS = {'.mkv', '.mp4', '.avi', '.mov', '.wmv', '.m4v', '.webm', '.flv', '.ts'}


def _win_to_wsl(path: str) -> str:
    """Convert a Windows path (C:\\...) to a WSL path (/mnt/c/...)."""
    try:
        return subprocess.check_output(
            ['wslpath', '-u', path], stderr=subprocess.DEVNULL, timeout=3
        ).decode().strip()
    except Exception:
        return path


def _find_video(content_path: str, episode: int) -> str:
    # Windows path from qBittorrent → convert to WSL path first
    if _is_wsl() and re.match(r'^[A-Za-z]:[/\\\\]', content_path):
        content_path = _win_to_wsl(content_path)
    p = _Path(content_path)
    if p.is_file() and p.suffix.lower() in _VIDEO_EXTS:
        return str(p)
    if p.is_dir():
        candidates = sorted(f for f in p.rglob('*') if f.is_file() and f.suffix.lower() in _VIDEO_EXTS)
        if not candidates:
            return ''
        if episode <= 0:
            return str(candidates[0])
        for f in candidates:
            if re.search(rf'(?<!\d)0*{episode}(?!\d)', f.stem):
                return str(f)
        return str(candidates[0])
    return ''


def _is_wsl() -> bool:
    try:
        ver = _Path('/proc/version').read_text().lower()
        return 'microsoft' in ver
    except Exception:
        return False


_MPV_EXE_WIN  = r'C:\Program Files (x86)\mpv\mpv.exe'
_MPV_EXE_UNIX = '/mnt/c/Program Files (x86)/mpv/mpv.exe'

def _launch_mpv(file_path: str) -> bool:
    """Open the video file with MPV, preferring Spanish subtitles."""
    if _is_wsl():
        try:
            win_path = subprocess.check_output(
                ['wslpath', '-w', file_path], stderr=subprocess.DEVNULL, timeout=3
            ).decode().strip()
        except Exception:
            win_path = file_path
        try:
            # Launch MPV directly so we can pass --slang for subtitle language
            subprocess.Popen(
                ['cmd.exe', '/c', 'start', '', _MPV_EXE_WIN,
                 '--slang=es,spa', '--alang=ja,jpn,en,eng', win_path],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            return True
        except Exception:
            # Fallback: open with default Windows app
            try:
                subprocess.Popen(['cmd.exe', '/c', 'start', '', win_path],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return True
            except Exception:
                return False
    else:
        try:
            subprocess.Popen(['xdg-open', file_path],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
        except Exception:
            return False


_SEARCH_TTL = 120
_NYAA_TTL   = 60

_qbt     = _http.Session()
_qbt_url = ''
_qbt_ok  = False


# ── Helpers ────────────────────────────────────────────────────────────────────

def _ql(query, variables=None):
    try:
        r = _http.post(_ANILIST, json={'query': query, 'variables': variables or {}}, timeout=10)
        return r.json().get('data') or {}
    except Exception:
        return {}


def _jikan_search(q: str) -> list:
    """Jikan (MAL) search — fallback when AniList is down."""
    try:
        r = _http.get(f'{_JIKAN}/anime', params={'q': q, 'limit': 20},
                      timeout=10, headers={'User-Agent': 'Mozilla/5.0'})
        r.raise_for_status()
        items = r.json().get('data') or []
    except Exception:
        return []

    results = []
    for it in items:
        images = it.get('images') or {}
        cover  = (images.get('jpg') or {}).get('large_image_url') or \
                 (images.get('jpg') or {}).get('image_url') or ''
        genres = [g['name'] for g in (it.get('genres') or [])[:4]]
        score_raw = it.get('score')
        score = int(score_raw * 10) if score_raw else None  # convert 8.3 → 83 (same scale as AniList)
        titles = it.get('titles') or []
        romaji = next((t['title'] for t in titles if t.get('type') == 'Japanese'), '') or it.get('title', '')
        english = next((t['title'] for t in titles if t.get('type') == 'English'), '') or it.get('title_english') or ''
        season = (it.get('season') or '').capitalize()
        year   = it.get('year') or ''
        season_label = f'{season} {year}'.strip()
        results.append({
            'al_id':        None,
            'mal_id':       it.get('mal_id'),
            'title':        english or it.get('title') or romaji,
            'title_romaji': it.get('title') or romaji,
            'title_english': english,
            'title_native': romaji,
            'season_label': season_label,
            'score':        score,
            'episodes':     it.get('episodes'),
            'status':       it.get('status'),
            'cover':        cover,
            'genres':       genres,
            'next_episode': None,
        })
    return results


def _parse_episode(title: str) -> int:
    """Episode number >0, 0=batch, -1=unknown."""
    if re.search(r'\b(batch|complete|BD ?[Pp]ack|全話|COMPLETE|Pack)\b', title, re.I):
        return 0
    # Range pattern requires BOTH sides to be 2+ digits to avoid "Part 2 - 01" false positives
    if re.search(r'(?<!\d)\d{2,3}\s*[-~–]\s*\d{2,3}(?!\d)', title):
        return 0
    # Strip codec identifiers so x264/x265/h264/h265 numbers are never extracted as episodes
    clean = re.sub(r'[xXhH]\.?26[45]', ' ', title)
    clean = re.sub(r'\b(HEVC|AVC1?|xvid|divx)\b', ' ', clean, flags=re.I)
    # Dash-number: take the LAST match so "Part 2 - 01" returns 1 not 2
    matches = list(re.finditer(r'[-–]\s*(\d{1,3})(?:v\d+)?(?=\s|\[|\(|_|\.mkv|$)', clean))
    for m in reversed(matches):
        n = int(m.group(1))
        if 1 <= n <= 999:
            return n
    for pat in [
        r'(?<!\d)(\d{2,3})(?:v\d+)?(?=\s|\[|\(|_|\.mkv|$)',
        r'\[(\d{1,3})\]',
        r'\bE(\d{1,3})\b',
    ]:
        m = re.search(pat, clean)
        if m:
            n = int(m.group(1))
            if 1 <= n <= 999:
                return n
    return -1


def _parse_quality(title: str) -> str:
    if '2160' in title or '4K' in title: return '4K'
    if '1080' in title: return '1080p'
    if '720'  in title: return '720p'
    if '480'  in title: return '480p'
    return ''


def _parse_group(title: str) -> str:
    m = re.match(r'^\[([^\]]{1,30})\]', title)
    return m.group(1) if m else ''


def _magnet(info_hash: str, title: str) -> str:
    trs = [
        'http://nyaa.tracker.wf:7777/announce',
        'udp://open.stealth.si:80/announce',
        'udp://tracker.opentrackr.org:1337/announce',
        'udp://exodus.desync.com:6969/announce',
    ]
    tr = ''.join(f'&tr={quote(t, safe="")}' for t in trs)
    return f'magnet:?xt=urn:btih:{info_hash}&dn={quote(title)}{tr}'


def _nyaa_search(query: str, category: str = '1_2', filter_code: str = '0') -> list:
    key = f'{query}|{category}|{filter_code}'
    if key in _nyaa_cache:
        res, ts = _nyaa_cache[key]
        if time.time() - ts < _NYAA_TTL:
            return res
    try:
        url = f'{_NYAA}/?{urlencode({"page":"rss","q":query,"c":category,"f":filter_code})}'
        r = _http.get(url, timeout=12, headers={'User-Agent': 'Mozilla/5.0'})
        r.raise_for_status()
        root = ET.fromstring(r.content)
    except Exception:
        return []

    results = []
    for item in root.findall('.//item'):
        ttl  = item.findtext('title', '').strip()
        link = item.findtext('link',  '').strip()

        ih = item.find(f'{_NS}infoHash')
        info_hash = (ih.text or '').strip().lower() if ih is not None else ''
        if not info_hash and '/download/' in link:
            info_hash = link.split('/download/')[-1].replace('.torrent', '').lower()

        def _t(tag):
            el = item.find(f'{_NS}{tag}')
            return el.text.strip() if el is not None and el.text else ''

        trusted_el = item.find(f'{_NS}trusted')
        trusted = trusted_el is not None and (trusted_el.text or '').strip().lower() == 'yes'

        try: seeders  = int(_t('seeders')  or 0)
        except ValueError: seeders = 0
        try: leechers = int(_t('leechers') or 0)
        except ValueError: leechers = 0

        # Derive Nyaa view URL from GUID or from the download link
        guid = item.findtext('guid', '').strip()
        if '/view/' in guid:
            view_url = guid.split('?')[0]
        elif '/download/' in link:
            vid = link.split('/download/')[-1].replace('.torrent', '').strip()
            view_url = f'{_NYAA}/view/{vid}' if vid.isdigit() else ''
        else:
            view_url = ''

        results.append({
            'title':       ttl,
            'torrent_url': link,
            'view_url':    view_url,
            'magnet':      _magnet(info_hash, ttl) if info_hash else '',
            'info_hash':   info_hash,
            'size':        _t('size'),
            'seeders':     seeders,
            'leechers':    leechers,
            'trusted':     trusted,
            'date':        item.findtext('pubDate', '').strip(),
            'episode':     _parse_episode(ttl),
            'quality':     _parse_quality(ttl),
            'group':       _parse_group(ttl),
        })

    _nyaa_cache[key] = (results, time.time())
    return results


def _qbt_login():
    global _qbt_ok, _qbt_url
    _qbt_url = os.environ.get('QBT_URL', 'http://localhost:8080').rstrip('/')
    try:
        r = _qbt.post(f'{_qbt_url}/api/v2/auth/login',
                      data={'username': os.environ.get('QBT_USERNAME', 'admin'),
                            'password': os.environ.get('QBT_PASSWORD', 'adminadmin')},
                      timeout=4)
        _qbt_ok = r.text.strip() in ('Ok.', '') and r.status_code in (200, 204)
    except Exception:
        _qbt_ok = False
    return _qbt_ok


def _q(method, path, **kw):
    global _qbt_ok
    if not _qbt_ok:
        _qbt_login()
    try:
        r = getattr(_qbt, method)(f'{_qbt_url}/api/v2{path}', timeout=6, **kw)
        if r.status_code == 403:
            _qbt_login()
            r = getattr(_qbt, method)(f'{_qbt_url}/api/v2{path}', timeout=6, **kw)
        return r
    except Exception as e:
        raise RuntimeError(str(e)) from e


# ── AniList anime search ───────────────────────────────────────────────────────

@anime_bp.route('/search')
def search_anime():
    q = request.args.get('q', '').strip()
    if len(q) < 2:
        return jsonify([])

    if q.lower() in _search_cache:
        res, ts = _search_cache[q.lower()]
        if time.time() - ts < _SEARCH_TTL:
            return jsonify(res)

    gql = '''
    query ($search: String) {
      Page(perPage: 20) {
        media(type: ANIME, search: $search, sort: SEARCH_MATCH) {
          id idMal
          title { romaji english native }
          meanScore episodes status season seasonYear format
          coverImage { large medium }
          genres
          nextAiringEpisode { episode }
        }
      }
    }
    '''
    data  = _ql(gql, {'search': q})
    items = (data.get('Page') or {}).get('media') or []
    anilist_ok = 'Page' in data

    results = []
    for it in items:
        t   = it.get('title') or {}
        nae = it.get('nextAiringEpisode') or {}
        season_year = it.get('seasonYear') or ''
        season_name = (it.get('season') or '').capitalize()
        season_label = f'{season_name} {season_year}'.strip()
        eng    = t.get('english') or ''
        romaji = t.get('romaji') or ''
        display = eng or romaji or t.get('native') or ''
        results.append({
            'al_id':        it['id'],
            'mal_id':       it.get('idMal'),
            'title':        display,
            'title_romaji': romaji,
            'title_native': t.get('native') or '',
            'title_english': eng,
            'season_label': season_label,
            'score':        it.get('meanScore'),
            'episodes':     it.get('episodes'),
            'status':       it.get('status'),
            'cover':        (it.get('coverImage') or {}).get('large') or (it.get('coverImage') or {}).get('medium'),
            'genres':       (it.get('genres') or [])[:4],
            'next_episode': nae.get('episode'),
            'format':       it.get('format') or '',
        })

    # AniList down → fallback to Jikan (MAL)
    if not anilist_ok:
        results = _jikan_search(q)

    if results:
        _search_cache[q.lower()] = (results, time.time())

    return jsonify(results)


# ── Nyaa search ────────────────────────────────────────────────────────────────

@anime_bp.route('/torrents')
def get_torrents():
    q        = request.args.get('q', '').strip()
    category = request.args.get('category', '1_2')
    if not q:
        return jsonify([])
    # Fetch both all (f=0) and trusted-only (f=2); merge to maximise episode coverage
    all_res     = _nyaa_search(q, category, '0')
    trusted_res = _nyaa_search(q, category, '2')
    seen, merged = set(), []
    for t in trusted_res + all_res:          # trusted first keeps their metadata
        key = t['info_hash'] or t['title']
        if key not in seen:
            seen.add(key)
            merged.append(t)
    return jsonify(merged)


# ── qBittorrent ────────────────────────────────────────────────────────────────

@anime_bp.route('/qbt/status')
def qbt_status():
    ok  = _qbt_login()
    ver = ''
    if ok:
        try: ver = _q('get', '/app/version').text.strip()
        except Exception: pass
    return jsonify({'connected': ok, 'url': os.environ.get('QBT_URL','http://localhost:8080'), 'version': ver})


@anime_bp.route('/qbt/configure', methods=['POST'])
def qbt_configure():
    data = request.get_json(silent=True) or {}
    if data.get('url'):      os.environ['QBT_URL']      = data['url'].rstrip('/')
    if data.get('username'): os.environ['QBT_USERNAME'] = data['username']
    if data.get('password'): os.environ['QBT_PASSWORD'] = data['password']
    global _qbt_ok
    _qbt_ok = False
    return jsonify({'connected': _qbt_login()})


def _sanitize_folder(title: str) -> str:
    return re.sub(r'[\\/:*?"<>|]', '', title).strip()


def _build_save_path(base: str, folder: str) -> str:
    sep = '\\' if ('\\' in base or (len(base) >= 2 and base[1] == ':')) else '/'
    return base.rstrip('/\\') + sep + folder


@anime_bp.route('/qbt/add', methods=['POST'])
def qbt_add():
    data = request.get_json(silent=True) or {}
    link = (data.get('magnet') or data.get('torrent_url') or '').strip()
    if not link:
        return jsonify({'error': 'no link'}), 400
    try:
        form = {'urls': link}
        if data.get('save_path'):
            form['savepath'] = data['save_path']
        elif data.get('anime_title'):
            try:
                prefs = _q('get', '/app/preferences').json()
                base = prefs.get('save_path', '')
                if base:
                    folder = _sanitize_folder(data['anime_title'])
                    if folder:
                        form['savepath'] = _build_save_path(base, folder)
            except Exception:
                pass
        r = _q('post', '/torrents/add', data=form)
        body = r.text.strip()
        ok = r.status_code in (200, 204) and body in ('Ok.', '')
        if not ok and r.status_code == 200:
            try:
                j = r.json()
                ok = j.get('success_count', 0) > 0 or j.get('failure_count', 0) == 0
            except Exception:
                pass
        return jsonify({'ok': ok, 'msg': body or 'Ok.'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@anime_bp.route('/qbt/list')
def qbt_list():
    try:
        torrents = _q('get', '/torrents/info').json()
        result = [{
            'hash':       t.get('hash',''),
            'name':       t.get('name',''),
            'state':      t.get('state',''),
            'progress':   round(t.get('progress',0)*100, 1),
            'size':       t.get('size',0),
            'dlspeed':    t.get('dlspeed',0),
            'upspeed':    t.get('upspeed',0),
            'eta':        t.get('eta',0),
            'num_seeds':  t.get('num_seeds',0),
            'num_leechs': t.get('num_leechs',0),
            'added_on':   t.get('added_on',0),
            'save_path':  t.get('save_path',''),
        } for t in torrents]
        result.sort(key=lambda x: x['added_on'], reverse=True)
        return jsonify(result)
    except Exception as e:
        return jsonify([])


@anime_bp.route('/qbt/action', methods=['POST'])
def qbt_action():
    data   = request.get_json(silent=True) or {}
    action = data.get('action')
    hash_  = data.get('hash', '')
    if not action or not hash_:
        return jsonify({'error': 'action+hash required'}), 400
    try:
        if action == 'delete':
            _q('post', '/torrents/delete',
               data={'hashes': hash_, 'deleteFiles': str(data.get('delete_files', False)).lower()})
        elif action in ('pause', 'resume'):
            _q('post', f'/torrents/{action}', data={'hashes': hash_})
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ── Anime library ──────────────────────────────────────────────────────────────

@anime_bp.route('/library')
def anime_library_get():
    lib = _lib_read()
    try:
        torrents_raw = _q('get', '/torrents/info').json()
        hash_map = {t['hash'].lower(): t for t in torrents_raw}
    except Exception:
        hash_map = {}

    # Collect unique hashes present in qBittorrent so we can fetch their file lists
    unique_hashes = {
        (ep_data.get('info_hash') or '').lower()
        for anime in lib.values()
        for ep_data in anime.get('episodes', {}).values()
        if (ep_data.get('info_hash') or '').lower() in hash_map
    }

    # files_map: info_hash → {episode_num: stem_filename}
    # Fetching /torrents/files gives us the real per-episode filenames
    files_map: dict = {}
    for ih in unique_hashes:
        try:
            raw_files = _q('get', '/torrents/files', params={'hash': ih}).json()
            ep_files: dict = {}
            for f in raw_files:
                stem = _Path(f.get('name', '')).stem  # basename without extension
                if not stem:
                    continue
                ep_num = _parse_episode(stem)
                if ep_num > 0:
                    ep_files[ep_num] = stem
            # If only one video file exists, store it under key -1 (single-file torrent)
            if not ep_files and len(raw_files) == 1:
                ep_files[-1] = _Path(raw_files[0].get('name', '')).stem
            files_map[ih] = ep_files
        except Exception:
            files_map[ih] = {}

    result = []
    for anime_id, anime in lib.items():
        total = anime.get('total_episodes') or 0
        ep_map = anime.get('episodes', {})
        watched_map = anime.get('watched', {})
        episodes_out = []
        for ep_str, ep_data in ep_map.items():
            try:
                ep_num = int(ep_str)
            except ValueError:
                ep_num = -1
            ih = (ep_data.get('info_hash') or '').lower()
            qbt = hash_map.get(ih, {})
            if qbt:
                progress = round(qbt.get('progress', 0) * 100, 1)
                state = qbt.get('state', 'unknown')
                in_qbt = True
            else:
                progress = 0
                state = 'missing'
                in_qbt = False

            # Prefer the actual filename from qBittorrent over the stored torrent title.
            # For batch-pre-filled episodes don't fall back to the batch torrent name —
            # it would show "[SubsPlease] Anime (01-12) [Batch]" for every episode.
            ep_files = files_map.get(ih, {})
            actual_title = (
                ep_files.get(ep_num)          # exact episode match from qBt file list
                or ep_files.get(-1)           # single-file torrent fallback
                or (ep_data.get('title', '') if not ep_data.get('from_batch') else '')
            )

            episodes_out.append({
                'num': ep_num,
                'title': actual_title,
                'info_hash': ih,
                'progress': progress,
                'state': state,
                'size': qbt.get('size', 0) if qbt else 0,
                'in_qbt': in_qbt,
                'added_on': ep_data.get('added_on', 0),
                'watched': bool(watched_map.get(ep_str)),
            })

        episodes_out.sort(key=lambda e: (e['num'] < 0, e['num']))

        if total > 0:
            known = {e['num'] for e in episodes_out}
            for n in range(1, total + 1):
                if n not in known:
                    episodes_out.append({'num': n, 'title': '', 'info_hash': '', 'progress': 0,
                                         'state': 'missing', 'size': 0, 'in_qbt': False, 'added_on': 0,
                                         'watched': False})
            episodes_out.sort(key=lambda e: (e['num'] < 0, e['num']))

        # Propagate batch (ep 0) state to individual placeholder episodes so that
        # card dots and the eplist show the correct download/done state even when
        # the individual episode slots were never pre-filled.
        batch_ep = next(
            (e for e in episodes_out if e['num'] == 0 and e.get('in_qbt')),
            None
        )
        if batch_ep:
            batch_ih   = batch_ep['info_hash']
            ep_files_b = files_map.get(batch_ih, {})
            for e in episodes_out:
                if e['num'] > 0 and not e.get('in_qbt'):
                    e['in_qbt']    = True
                    e['progress']  = batch_ep['progress']
                    e['info_hash'] = batch_ih
                    e['state']     = batch_ep.get('state', 'downloading')
                    if not e.get('title'):
                        e['title'] = ep_files_b.get(e['num'], '')

        done_count = sum(1 for e in episodes_out if e.get('in_qbt') and e.get('num', 0) > 0)
        result.append({
            'id': anime_id,
            'al_id': anime.get('al_id'),
            'mal_id': anime.get('mal_id'),
            'title': anime.get('title', ''),
            'title_romaji': anime.get('title_romaji', ''),
            'cover': anime.get('cover', ''),
            'total_episodes': total,
            'format': anime.get('format', ''),
            'episodes': episodes_out,
            'downloaded_count': done_count,
        })
    return jsonify(result)


@anime_bp.route('/library/add', methods=['POST'])
def anime_library_add():
    data = request.get_json(silent=True) or {}
    al_id  = data.get('al_id')
    mal_id = data.get('mal_id')
    title  = (data.get('title') or '').strip()
    anime_id = str(al_id or mal_id or re.sub(r'[^a-z0-9]+', '_', title.lower())[:40])
    if not anime_id:
        return jsonify({'error': 'need al_id, mal_id, or title'}), 400

    lib = _lib_read()
    if anime_id not in lib:
        lib[anime_id] = {
            'al_id': al_id, 'mal_id': mal_id,
            'title': title,
            'title_romaji': data.get('title_romaji', ''),
            'cover': data.get('cover', ''),
            'total_episodes': data.get('total_episodes'),
            'format': data.get('format', ''),
            'episodes': {},
        }
    else:
        if not lib[anime_id].get('cover') and data.get('cover'):
            lib[anime_id]['cover'] = data['cover']
        if not lib[anime_id].get('total_episodes') and data.get('total_episodes'):
            lib[anime_id]['total_episodes'] = data['total_episodes']
        if not lib[anime_id].get('format') and data.get('format'):
            lib[anime_id]['format'] = data['format']

    # track_only = add anime to library without registering any episode
    if not data.get('track_only'):
        ep_num = str(data.get('episode', -1))
        ih     = (data.get('info_hash') or '').lower()
        title_t = data.get('torrent_title', '')
        now    = int(time.time())
        lib[anime_id]['episodes'][ep_num] = {
            'title':     title_t,
            'info_hash': ih,
            'added_on':  now,
        }
        # Batch (episode 0) with known total → pre-fill every episode slot with this hash
        if ep_num == '0':
            total = lib[anime_id].get('total_episodes') or 0
            if total and total > 0:
                for n in range(1, int(total) + 1):
                    if str(n) not in lib[anime_id]['episodes']:
                        lib[anime_id]['episodes'][str(n)] = {
                            'title':      title_t,
                            'info_hash':  ih,
                            'added_on':   now,
                            'from_batch': True,
                        }

    _lib_write(lib)
    return jsonify({'ok': True, 'id': anime_id})


@anime_bp.route('/library/<anime_id>', methods=['DELETE'])
def anime_library_remove(anime_id):
    lib = _lib_read()
    data = request.get_json(silent=True) or {}
    if anime_id in lib:
        if data.get('delete_files'):
            for ep_data in lib[anime_id].get('episodes', {}).values():
                ih = ep_data.get('info_hash', '')
                if ih:
                    try:
                        _q('post', '/torrents/delete', data={'hashes': ih, 'deleteFiles': 'true'})
                    except Exception:
                        pass
        del lib[anime_id]
        _lib_write(lib)
    return jsonify({'ok': True})


@anime_bp.route('/library/<anime_id>/episode/<int:ep_num>', methods=['DELETE'])
def anime_episode_remove(anime_id, ep_num):
    lib = _lib_read()
    data = request.get_json(silent=True) or {}
    if anime_id not in lib:
        return jsonify({'error': 'not found'}), 404
    ep_str = str(ep_num)
    ep_data = lib[anime_id].get('episodes', {}).get(ep_str, {})
    if ep_data and data.get('delete_files'):
        ih = ep_data.get('info_hash', '')
        if ih:
            try:
                _q('post', '/torrents/delete', data={'hashes': ih, 'deleteFiles': 'true'})
            except Exception:
                pass
    lib[anime_id].get('episodes', {}).pop(ep_str, None)
    _lib_write(lib)
    return jsonify({'ok': True})


@anime_bp.route('/play', methods=['POST'])
def anime_play():
    try:
        data = request.get_json(silent=True) or {}
        anime_id  = data.get('anime_id', '')
        episode   = data.get('episode', -1)
        info_hash = (data.get('info_hash') or '').lower()
        if not info_hash:
            return jsonify({'error': 'info_hash required'}), 400
        try:
            torrents = _q('get', '/torrents/info', params={'hashes': info_hash}).json()
        except Exception as e:
            return jsonify({'error': f'qBT error: {e}'}), 500
        # Fallback: if hash not found but anime has a batch (episode 0), use that torrent
        if not torrents and anime_id:
            lib = _lib_read()
            batch_hash = (lib.get(anime_id) or {}).get('episodes', {}).get('0', {}).get('info_hash', '')
            if batch_hash and batch_hash != info_hash:
                try:
                    torrents = _q('get', '/torrents/info', params={'hashes': batch_hash}).json()
                except Exception:
                    pass
        if not torrents:
            return jsonify({'error': 'torrent not found in qBittorrent'}), 404
        content_path = torrents[0].get('content_path') or torrents[0].get('save_path', '')
        if not content_path:
            return jsonify({'error': 'no content path from qBittorrent'}), 404
        video = _find_video(content_path, int(episode))
        if not video:
            return jsonify({'error': f'no video file found in: {content_path}'}), 404
        if not _launch_mpv(video):
            return jsonify({'error': 'failed to launch mpv — is mpv.exe in PATH?'}), 500
        if anime_id:
            lib = _lib_read()
            if anime_id in lib:
                lib[anime_id].setdefault('watched', {})[str(episode)] = True
                _lib_write(lib)
        return jsonify({'ok': True, 'path': video})
    except Exception as e:
        return jsonify({'error': f'internal error: {e}'}), 500


@anime_bp.route('/seasonal')
def anime_seasonal():
    import datetime
    season   = request.args.get('season', '').upper()
    sort_by  = request.args.get('sort', 'score')
    try:    year = int(request.args.get('year', 0))
    except  ValueError: year = 0

    now = datetime.datetime.now()
    if not year:
        year = now.year
    if not season:
        m = now.month
        season = 'WINTER' if m <= 3 else 'SPRING' if m <= 6 else 'SUMMER' if m <= 9 else 'FALL'

    sort_map = {'score': 'SCORE_DESC', 'popularity': 'POPULARITY_DESC', 'trending': 'TRENDING_DESC'}
    gql_sort = sort_map.get(sort_by, 'SCORE_DESC')

    cache_key = f'{season}_{year}_{sort_by}'
    if cache_key in _seasonal_cache:
        res, ts = _seasonal_cache[cache_key]
        if time.time() - ts < _SEASONAL_TTL:
            return jsonify(res)

    gql = '''
    query ($season: MediaSeason, $seasonYear: Int, $sort: [MediaSort]) {
      Page(perPage: 50) {
        media(type: ANIME, season: $season, seasonYear: $seasonYear, sort: $sort, isAdult: false) {
          id idMal
          title { romaji english native }
          meanScore popularity episodes status season seasonYear format
          coverImage { large medium }
          genres
          nextAiringEpisode { episode airingAt }
        }
      }
    }
    '''
    data  = _ql(gql, {'season': season, 'seasonYear': year, 'sort': [gql_sort, 'ID_DESC']})
    items = (data.get('Page') or {}).get('media') or []
    anilist_ok = 'Page' in data
    print(f'[seasonal] {season} {year} anilist_ok={anilist_ok} items={len(items)}')

    def _build(it):
        t   = it.get('title') or {}
        nae = it.get('nextAiringEpisode') or {}
        eng    = t.get('english') or ''
        romaji = t.get('romaji') or ''
        return {
            'al_id':        it.get('id'),
            'mal_id':       it.get('idMal'),
            'title':        eng or romaji or t.get('native') or '',
            'title_romaji': romaji,
            'title_english': eng,
            'title_native': t.get('native') or '',
            'season_label': f"{(it.get('season') or '').capitalize()} {it.get('seasonYear') or ''}".strip(),
            'score':        it.get('meanScore'),
            'popularity':   it.get('popularity'),
            'episodes':     it.get('episodes'),
            'status':       it.get('status'),
            'cover':        (it.get('coverImage') or {}).get('large') or (it.get('coverImage') or {}).get('medium'),
            'genres':       (it.get('genres') or [])[:4],
            'next_episode': nae.get('episode'),
            'airing_at':    nae.get('airingAt'),
            'format':       it.get('format') or '',
        }

    results = [_build(it) for it in items]

    # Jikan fallback when AniList is down
    if not anilist_ok or not results:
        try:
            now = datetime.datetime.now()
            is_current = (year == now.year) and (season == (
                'WINTER' if now.month <= 3 else 'SPRING' if now.month <= 6 else
                'SUMMER' if now.month <= 9 else 'FALL'))
            if is_current:
                jikan_url = f'{_JIKAN}/seasons/now'
                jparams = {'limit': 25}
            else:
                jikan_url = f'{_JIKAN}/seasons/{year}/{season.lower()}'
                jparams = {'limit': 25}
            r = _http.get(jikan_url, params=jparams, timeout=15,
                          headers={'User-Agent': 'Mozilla/5.0'})
            r.raise_for_status()
            jitems = r.json().get('data') or []
            results = []
            for it in jitems:
                images = it.get('images') or {}
                cover  = (images.get('jpg') or {}).get('large_image_url') or \
                         (images.get('jpg') or {}).get('image_url') or ''
                score_raw = it.get('score')
                results.append({
                    'al_id':        None,
                    'mal_id':       it.get('mal_id'),
                    'title':        it.get('title_english') or it.get('title') or '',
                    'title_romaji': it.get('title') or '',
                    'title_english': it.get('title_english') or '',
                    'title_native': '',
                    'season_label': f"{(it.get('season') or '').capitalize()} {it.get('year') or ''}".strip(),
                    'score':        int(score_raw * 10) if score_raw else None,
                    'popularity':   it.get('members'),
                    'episodes':     it.get('episodes'),
                    'status':       it.get('status'),
                    'cover':        cover,
                    'genres':       [g['name'] for g in (it.get('genres') or [])[:4]],
                    'next_episode': None,
                    'airing_at':    None,
                })
            if sort_by == 'popularity':
                results.sort(key=lambda x: x.get('popularity') or 0, reverse=True)
            else:
                results.sort(key=lambda x: x.get('score') or 0, reverse=True)
        except Exception:
            pass

    payload = {'season': season, 'year': year, 'sort': sort_by, 'results': results}
    if results:
        _seasonal_cache[cache_key] = (payload, time.time())
    return jsonify(payload)


@anime_bp.route('/library/<anime_id>/watched', methods=['POST'])
def anime_watched_toggle(anime_id):
    data = request.get_json(silent=True) or {}
    episode = str(data.get('episode', -1))
    lib = _lib_read()
    if anime_id not in lib:
        return jsonify({'error': 'anime not found'}), 404
    watched = lib[anime_id].setdefault('watched', {})
    if episode in watched:
        del watched[episode]
        state = False
    else:
        watched[episode] = True
        state = True
    _lib_write(lib)
    return jsonify({'ok': True, 'watched': state})


# ── Episode thumbnail extraction ───────────────────────────────────────────────

def _ffprobe_duration(path: str) -> float:
    """Return video duration in seconds, or 0 on failure."""
    try:
        out = subprocess.check_output(
            ['ffprobe', '-v', 'quiet', '-show_entries', 'format=duration',
             '-of', 'default=noprint_wrappers=1:nokey=1', path],
            stderr=subprocess.DEVNULL, timeout=10,
        ).decode().strip()
        return float(out)
    except Exception:
        return 0.0


def _secs_to_hms(secs: float) -> str:
    secs = int(secs)
    h, rem = divmod(secs, 3600)
    m, s = divmod(rem, 60)
    return f'{h:02d}:{m:02d}:{s:02d}'


@anime_bp.route('/thumb/<anime_id>/<int:episode>')
def anime_thumb(anime_id, episode):
    _THUMBS_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = _THUMBS_DIR / f'{anime_id}_{episode}.jpg'

    # Serve cached thumb if it exists (no expiry — video files don't change)
    if cache_path.exists() and cache_path.stat().st_size > 0:
        return send_file(str(cache_path), mimetype='image/jpeg',
                         max_age=604800)  # 7 days

    # Resolve info_hash from library (episode directly, or batch ep 0)
    lib = _lib_read()
    anime = lib.get(anime_id)
    if not anime:
        return ('', 404)

    ep_map = anime.get('episodes', {})
    ep_data = ep_map.get(str(episode)) or ep_map.get('0') or {}
    info_hash = (ep_data.get('info_hash') or '').lower()
    if not info_hash:
        return ('', 404)

    # Get content path from qBittorrent; fall back to ep-0 (batch) hash if needed
    try:
        torrents = _q('get', '/torrents/info', params={'hashes': info_hash}).json()
    except Exception:
        return ('', 502)
    if not torrents:
        batch_hash = (ep_map.get('0') or {}).get('info_hash', '').lower()
        if batch_hash and batch_hash != info_hash:
            try:
                torrents = _q('get', '/torrents/info', params={'hashes': batch_hash}).json()
            except Exception:
                pass
    if not torrents:
        return ('', 404)
    content_path = torrents[0].get('content_path') or torrents[0].get('save_path', '')
    if not content_path:
        return ('', 404)

    video = _find_video(content_path, episode)
    if not video:
        return ('', 404)

    # Get duration and seek to 25%
    duration = _ffprobe_duration(video)
    seek_secs = duration * 0.25 if duration > 0 else 30.0
    seek_ts = _secs_to_hms(seek_secs)

    try:
        subprocess.run(
            ['ffmpeg', '-y', '-ss', seek_ts, '-i', video,
             '-vframes', '1', '-vf', 'scale=360:-1',
             '-q:v', '5', str(cache_path)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=30,
        )
    except Exception:
        return ('', 500)

    if not cache_path.exists() or cache_path.stat().st_size == 0:
        return ('', 500)

    return send_file(str(cache_path), mimetype='image/jpeg', max_age=604800)
