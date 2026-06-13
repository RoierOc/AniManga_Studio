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
import glob
import shutil
import string
import uuid
import subprocess
from html import unescape as _html_unescape
import threading
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

def _clear_thumb_cache():
    if _THUMBS_DIR.exists():
        for f in _THUMBS_DIR.glob('*.jpg'):
            try: f.unlink()
            except Exception: pass

# NOTE: thumbnails are intentionally NOT cleared on startup — they persist so that an
# anime whose episodes were freed (clear_episodes) still shows its episode thumbnails
# when you revisit it, which looks organic and survives restarts. They're small JPGs.

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

# ── Watch history ──────────────────────────────────────────────────────────────

def _history_path() -> _Path:
    return _Path(os.environ.get('MANGA_DIR', str(_Path.home() / 'MangaLibrary'))) / 'watch_history.json'

def _history_read() -> list:
    try:
        if _history_path().exists():
            return json.loads(_history_path().read_text(encoding='utf-8'))
    except Exception:
        pass
    return []

def _history_append(anime_id: str, title: str, episode: int, cover: str = ''):
    history = _history_read()
    history.insert(0, {
        'anime_id': anime_id,
        'title': title,
        'episode': episode,
        'cover': cover,
        'watched_at': int(time.time()),
    })
    try:
        _history_path().write_text(
            json.dumps(history[:500], ensure_ascii=False, indent=2), encoding='utf-8'
        )
    except Exception:
        pass

# ── Subtitle helpers ───────────────────────────────────────────────────────────

_SUBTITLE_EXTS = {'.srt', '.ass', '.ssa', '.sub', '.vtt'}

def _find_subtitles(video_path: str) -> list:
    video = _Path(video_path)
    if not video.exists():
        return []
    subs = []
    for f in video.parent.iterdir():
        if f.suffix.lower() in _SUBTITLE_EXTS:
            subs.append({'name': f.name, 'path': str(f)})
    subs.sort(key=lambda s: (not s['name'].startswith(video.stem), s['name'].lower()))
    return subs


_backfill_done = False

_TMDB_KEY = os.environ.get('TMDB_API_KEY', '')


def _norm_title(s):
    return re.sub(r'[^a-z0-9]', '', (s or '').lower())


def _anilist_post(query, variables, tries=4):
    """POST to AniList honoring its rate limit (HTTP 429 + Retry-After). AniList's
    degraded limit is ~30 req/min, so a single backfill can hit it; back off and retry
    instead of silently dropping the entry."""
    r = None
    for _ in range(tries):
        r = _http.post(_ANILIST, json={'query': query, 'variables': variables}, timeout=10)
        if r.status_code == 429:
            wait = r.headers.get('Retry-After')
            time.sleep(min(int(wait) if (wait and wait.isdigit()) else 8, 60))
            continue
        return r
    return r


def _anilist_enrich(al_id):
    """Full metadata for a library entry: episodes, format, titles, genres, season,
    the high-res cover (extraLarge) and the wide hero bannerImage."""
    q = '''query($id:Int){Media(id:$id,type:ANIME){
        episodes format status season seasonYear
        title{romaji english}
        coverImage{extraLarge large}
        bannerImage genres
    }}'''
    r = _anilist_post(q, {'id': int(al_id)})
    return (r.json().get('data') or {}).get('Media') or {} if r is not None else {}


def _tmdb_backdrop(title_en, title_romaji, year):
    """Best wide, text-free backdrop from TMDB for a TV/anime title, or None.
    Conservative match (normalized-title overlap + year ±1) so we never show wrong art."""
    if not _TMDB_KEY:
        return None
    for name in [t for t in (title_en, title_romaji) if t]:
        try:
            params = {'api_key': _TMDB_KEY, 'query': name, 'include_adult': 'false'}
            if year:
                params['first_air_date_year'] = year
            r = _http.get('https://api.themoviedb.org/3/search/tv', params=params, timeout=8)
            target = _norm_title(name)
            for res in ((r.json() or {}).get('results') or [])[:5]:
                cand = _norm_title(res.get('name'))
                cand_o = _norm_title(res.get('original_name'))
                ry = (res.get('first_air_date') or '')[:4]
                year_ok = (not year) or (not ry) or abs(int(ry) - int(year)) <= 1
                match = bool(target) and (target in cand or cand in target or target in cand_o or cand_o in target)
                if not (year_ok and match):
                    continue
                imgs = _http.get(
                    f"https://api.themoviedb.org/3/tv/{res['id']}/images",
                    params={'api_key': _TMDB_KEY, 'include_image_language': 'null,en'}, timeout=8).json()
                backdrops = imgs.get('backdrops') or []
                textless = [b for b in backdrops if b.get('iso_639_1') is None]
                pool = textless or backdrops
                if pool:
                    best = max(pool, key=lambda b: (b.get('vote_average') or 0, b.get('width') or 0))
                    return {'tmdb_id': res['id'], 'url': f"https://image.tmdb.org/t/p/w1280{best['file_path']}"}
            time.sleep(0.2)
        except Exception:
            pass
    return None


_airing_cache = {'ts': 0, 'data': {}}
_AIRING_TTL = 1800  # 30 min — airing data only changes when an episode actually airs


def _fetch_airing(al_ids):
    """For the given AniList ids return {al_id: {status, next_episode, next_airing_at,
    last_episode, last_aired_at}} via one batched, paginated query. The last aired
    episode comes from the real airing schedule (exact time), not an estimate."""
    now = time.time()
    if now - _airing_cache['ts'] < _AIRING_TTL and _airing_cache['data']:
        return _airing_cache['data']
    ids = [int(x) for x in al_ids if x]
    out = {}
    if not ids:
        return out
    # Only currently-airing anime expose nextAiringEpisode. The latest aired episode
    # is (next - 1), which aired ~one interval (a week) before the next one — a good
    # estimate for "aired recently" without the schedule connection (no sort there).
    q = '''query($ids:[Int],$p:Int){Page(page:$p,perPage:50){
        pageInfo{hasNextPage}
        media(id_in:$ids,type:ANIME){
            id status
            nextAiringEpisode{episode airingAt}
        }
    }}'''
    page = 1
    while True:
        r = _anilist_post(q, {'ids': ids, 'p': page})
        if r is None:
            break
        pg = (r.json().get('data') or {}).get('Page') or {}
        for m in (pg.get('media') or []):
            nae = m.get('nextAiringEpisode') or {}
            next_ep = nae.get('episode')
            next_at = nae.get('airingAt')
            last_ep = (next_ep - 1) if (next_ep and next_ep > 1) else None
            last_at = (next_at - 604800) if (next_at and last_ep) else None
            out[m['id']] = {
                'status':         m.get('status'),
                'next_episode':   next_ep,
                'next_airing_at': next_at,
                'last_episode':   last_ep,
                'last_aired_at':  last_at,
            }
        if not (pg.get('pageInfo') or {}).get('hasNextPage'):
            break
        page += 1
        time.sleep(1.0)
    if out:
        _airing_cache['data'] = out
        _airing_cache['ts'] = now
    return out


def _backfill_anime_metadata():
    """Background enrichment of library entries: AniList metadata (episodes, format,
    genres, season, hi-res cover, wide bannerImage) plus a TMDB backdrop when one
    matches. Runs once per process and skips already-enriched entries. The resolved
    hero banner is cached per entry (banner / banner_source / tmdb_id)."""
    global _backfill_done
    lib = _lib_read()
    changed = False
    processed = 0

    for k, v in lib.items():
        al_id = v.get('al_id') or (int(k) if k.isdigit() else None)

        # Resolve al_id from mal_id when missing (Jikan-sourced entries)
        if not al_id and v.get('mal_id'):
            try:
                r = _anilist_post('query($mid:Int){Media(idMal:$mid,type:ANIME){id}}', {'mid': v['mal_id']})
                al_id = ((r.json().get('data') or {}).get('Media') or {}).get('id') if r is not None else None
                if al_id:
                    v['al_id'] = al_id; changed = True
                time.sleep(1.5)
            except Exception:
                pass
        if not al_id:
            continue

        # AniList enrichment when any key field is missing
        if not (v.get('banner') and v.get('genres') and v.get('total_episodes') and v.get('cover_xl')):
            try:
                m = _anilist_enrich(al_id)
                processed += 1
                if m:
                    t = m.get('title') or {}
                    ci = m.get('coverImage') or {}
                    if not v.get('al_id'): v['al_id'] = al_id; changed = True
                    if m.get('episodes') and not v.get('total_episodes'): v['total_episodes'] = m['episodes']; changed = True
                    if m.get('format') and not v.get('format'): v['format'] = m['format']; changed = True
                    if t.get('romaji') and not v.get('title_romaji'): v['title_romaji'] = t['romaji']; changed = True
                    if t.get('english') and not v.get('title_english'): v['title_english'] = t['english']; changed = True
                    if ci.get('extraLarge') and not v.get('cover_xl'): v['cover_xl'] = ci['extraLarge']; changed = True
                    if m.get('genres') and not v.get('genres'): v['genres'] = (m['genres'] or [])[:5]; changed = True
                    if m.get('season') and not v.get('season'): v['season'] = m['season']; changed = True
                    if m.get('seasonYear') and not v.get('season_year'): v['season_year'] = m['seasonYear']; changed = True
                    # AniList banner is the default; never overwrite a TMDB one
                    if m.get('bannerImage') and v.get('banner_source') != 'tmdb' and v.get('banner') != m['bannerImage']:
                        v['banner'] = m['bannerImage']; v['banner_source'] = 'anilist'; changed = True
                time.sleep(1.5)  # AniList degraded rate limit is ~30 req/min
            except Exception:
                pass

        # TMDB backdrop — attempt once per entry (higher quality than AniList)
        if _TMDB_KEY and not v.get('_tmdb_tried'):
            v['_tmdb_tried'] = True; changed = True
            hit = _tmdb_backdrop(v.get('title_english') or v.get('title'), v.get('title_romaji'), v.get('season_year'))
            if hit:
                v['banner'] = hit['url']; v['banner_source'] = 'tmdb'; v['tmdb_id'] = hit['tmdb_id']; changed = True

        # Persist periodically so partial progress survives interruptions/restarts.
        if changed and processed and processed % 8 == 0:
            _lib_write(lib)

    if changed:
        _lib_write(lib)
    _backfill_done = True


# ── Scan paths (local anime folders) ─────────────────────────────────────────

def _scanpaths_path() -> _Path:
    return _Path(os.environ.get('MANGA_DIR', str(_Path.home() / 'MangaLibrary'))) / 'anime_scan_paths.json'

def _scanpaths_read() -> dict:
    try:
        return json.loads(_scanpaths_path().read_text(encoding='utf-8'))
    except Exception:
        return {'paths': [], 'mappings': {}}

def _scanpaths_write(data: dict):
    p = _scanpaths_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding='utf-8')


def _list_anime_folders(root: str) -> list:
    if _is_wsl() and re.match(r'^[A-Za-z]:[/\\]', root):
        root = _win_to_wsl(root)
    p = _Path(root)
    if not p.exists():
        return []
    return sorted(str(sub) for sub in p.iterdir() if sub.is_dir())


_SHORT_VIDEO_SECS = 600  # < 10 min → auto-special

# Duration cache: path → (mtime, duration_secs)
# Keyed by absolute path; invalidated when file mtime changes.
# Persisted to disk so server restarts don't re-probe every file.
_dur_cache: dict = {}
_DUR_CACHE_PATH = _Path.home() / '.animanga_dur_cache.json'
_dur_cache_dirty = False


def _load_dur_cache():
    global _dur_cache
    try:
        with open(_DUR_CACHE_PATH) as f:
            _dur_cache = {k: tuple(v) for k, v in json.load(f).items()}
    except Exception:
        _dur_cache = {}


def _save_dur_cache():
    global _dur_cache_dirty
    if not _dur_cache_dirty:
        return
    try:
        with open(_DUR_CACHE_PATH, 'w') as f:
            json.dump({k: list(v) for k, v in _dur_cache.items()}, f)
        _dur_cache_dirty = False
    except Exception:
        pass


_load_dur_cache()


def _video_duration(path: str) -> float:
    """Return video duration in seconds via ffprobe, cached by file mtime (persisted to disk)."""
    global _dur_cache_dirty
    try:
        mtime = _Path(path).stat().st_mtime
    except Exception:
        return 0.0
    cached = _dur_cache.get(path)
    if cached and cached[0] == mtime:
        return cached[1]
    try:
        out = subprocess.check_output(
            ['ffprobe', '-v', 'quiet', '-show_entries', 'format=duration',
             '-of', 'default=noprint_wrappers=1:nokey=1', path],
            stderr=subprocess.DEVNULL, timeout=10,
        ).decode().strip()
        dur = float(out)
    except Exception:
        dur = 0.0
    _dur_cache[path] = (mtime, dur)
    _dur_cache_dirty = True
    # Flush cache in background to avoid blocking the request thread
    threading.Thread(target=_save_dur_cache, daemon=True).start()
    return dur


# Folder-level scan cache: wsl_path → (folder_mtime, overrides_key, episodes_list)
# Keyed by folder path; re-scans only when folder mtime or overrides change.
_scan_cache: dict = {}


def _folder_mtime(p: _Path) -> float:
    """Get max mtime of a folder and all immediate subdirs — detects new files."""
    try:
        mtimes = [p.stat().st_mtime]
        for sub in p.iterdir():
            if sub.is_dir():
                try:
                    mtimes.append(sub.stat().st_mtime)
                except Exception:
                    pass
        return max(mtimes)
    except Exception:
        return 0.0


def _scan_local_episodes(folder_path: str, overrides: dict = None) -> list:
    """Scan a local folder for video files.
    overrides: {filename: 'special'|'hidden'|'episode'} — manual type assignments.
    Files with no override and duration < 10 min are auto-classified as special.
    A manual override='episode' forces normal treatment even if short.
    Results are cached by folder mtime so repeated calls don't hit the filesystem.
    """
    wsl_path = folder_path
    if _is_wsl() and re.match(r'^[A-Za-z]:[/\\]', folder_path):
        wsl_path = _win_to_wsl(folder_path)
    p = _Path(wsl_path)
    if not p.exists():
        return []

    overrides = overrides or {}
    overrides_key = str(sorted(overrides.items()))
    fmtime = _folder_mtime(p)
    cached = _scan_cache.get(wsl_path)
    if cached and cached[0] == fmtime and cached[1] == overrides_key:
        return cached[2]

    files = sorted(f for f in p.rglob('*') if f.is_file() and f.suffix.lower() in _VIDEO_EXTS)
    seen: set = set()
    episodes = []
    sp_counter = 0
    for f in files:
        manual = overrides.get(f.name)  # None | 'episode' | 'special' | 'hidden'
        if manual == 'hidden':
            continue

        # Determine effective type: manual override > duration heuristic
        if manual == 'special':
            ep_type = 'special'
        elif manual == 'episode':
            ep_type = 'episode'  # force normal even if short
        else:
            dur = _video_duration(str(f))
            ep_type = 'special' if 0 < dur < _SHORT_VIDEO_SECS else 'episode'

        if ep_type == 'special':
            sp_counter += 1
            episodes.append({'num': sp_counter, 'path': str(f), 'ep_type': 'special', 'filename': f.name})
            continue

        ep_num = _parse_episode(f.stem)
        if ep_num <= 0:
            ep_num = len([e for e in episodes if e['ep_type'] == 'episode']) + 1
        while ep_num in seen:
            ep_num += 1
        seen.add(ep_num)
        episodes.append({'num': ep_num, 'path': str(f), 'ep_type': 'episode', 'filename': f.name})

    result = sorted(episodes, key=lambda e: (e['ep_type'] != 'episode', e['num']))
    _scan_cache[wsl_path] = (fmtime, overrides_key, result)
    return result


# ── MPV / video helpers ────────────────────────────────────────────────────────

_VIDEO_EXTS = {'.mkv', '.mp4', '.avi', '.mov', '.wmv', '.m4v', '.webm', '.flv', '.ts', '.m2ts'}


def _win_to_wsl(path: str) -> str:
    """Convert a Windows path (C:\\...) to a WSL path (/mnt/c/...)."""
    try:
        return subprocess.check_output(
            ['wslpath', '-u', path], stderr=subprocess.DEVNULL, timeout=3
        ).decode().strip()
    except Exception:
        return path


def _find_video(content_path: str, episode: int, subpath: str = '') -> str:
    # Windows path from qBittorrent → convert to WSL path first
    if _is_wsl() and re.match(r'^[A-Za-z]:[/\\\\]', content_path):
        content_path = _win_to_wsl(content_path)
    p = _Path(content_path)
    if p.is_file() and p.suffix.lower() in _VIDEO_EXTS:
        return str(p)
    if p.is_dir():
        # Subpath narrows the search to a specific subfolder (e.g. Season 2 within a batch)
        if subpath:
            sub = p / subpath
            if sub.is_dir():
                p = sub
        candidates = sorted(f for f in p.rglob('*') if f.is_file() and f.suffix.lower() in _VIDEO_EXTS)
        if not candidates:
            return ''
        if episode <= 0:
            return str(candidates[0])
        # Use _parse_episode for accurate matching (avoids false positives from hex hashes)
        for f in candidates:
            if _parse_episode(f.stem) == episode:
                return str(f)
        return str(candidates[0])
    return ''


def _local_ep_title(stem: str, ep_type: str) -> str:
    """Human-readable title for a local video file."""
    if ep_type == 'episode':
        # Plain episode pattern (E01, 01, 001) → empty so animeEpLabel shows "Episodio N"
        if re.match(r'^E?\d{1,3}(v\d+)?$', stem, re.I):
            return ''
        return _clean_ep_title(stem)
    # Special: strip SP- prefix then humanize
    s = re.sub(r'^SP[-_]', '', stem, flags=re.I)
    s = _clean_ep_title(s)
    s = s.replace('-', ' ').replace('_', ' ')
    s = re.sub(r'([A-Za-z])(\d)', r'\1 \2', s)  # NCOP1 → NCOP 1
    return re.sub(r'\s+', ' ', s).strip() or _clean_ep_title(stem)


def _clean_ep_title(stem: str) -> str:
    """Strip fansub group tags, quality/hash brackets from a filename stem."""
    s = re.sub(r'^\[[^\]]{1,30}\]\s*', '', stem)   # leading [Group]
    # Remove all [...] that are NOT just a bare episode number
    s = re.sub(r'\s*\[(?!\d{1,3}\])[^\]]*\]', '', s)
    # Remove (...) quality/resolution metadata
    s = re.sub(r'\s*\([^)]*(?:\d{3,4}p|BD|Hi10|Blu)[^)]*\)', '', s, flags=re.I)
    return s.strip(' -_.')


def _is_wsl() -> bool:
    try:
        ver = _Path('/proc/version').read_text().lower()
        return 'microsoft' in ver
    except Exception:
        return False


_CMD_EXE = '/mnt/c/Windows/System32/cmd.exe'

# Known Windows locations for mpv.exe (checked in order when not in PATH)
_MPV_CANDIDATE_PATHS = [
    '/mnt/c/Program Files (x86)/mpv/mpv.exe',
    '/mnt/c/Program Files/mpv/mpv.exe',
    '/mnt/c/tools/mpv/mpv.exe',
    '/mnt/c/scoop/apps/mpv/current/mpv.exe',
]


def _find_mpv_exe() -> str:
    """Return a usable mpv executable path, or empty string if not found."""
    # Prefer PATH entry first
    try:
        subprocess.check_output(['which', 'mpv.exe'], stderr=subprocess.DEVNULL, timeout=2)
        return 'mpv.exe'
    except Exception:
        pass
    for p in _MPV_CANDIDATE_PATHS:
        if _Path(p).exists():
            return p
    return ''


def _make_wl_dir_wsl() -> tuple:
    """Create a watch-later dir on the Windows side (%TEMP%) so MPV can write to it.
    Returns (wl_win, wl_linux): Windows path and Linux path for reading."""
    uid = uuid.uuid4().hex[:8]
    try:
        # Use PowerShell to get real Windows %TEMP% path
        win_temp = subprocess.check_output(
            ['powershell.exe', '-NoProfile', '-Command',
             '[System.IO.Path]::GetTempPath().TrimEnd("\\")'],
            stderr=subprocess.DEVNULL, timeout=5,
        ).decode().strip()
        if not win_temp:
            raise ValueError('empty temp path')
        wl_win = win_temp + f'\\mpv-wl-{uid}'
        # Create the directory on Windows side
        subprocess.run(
            ['powershell.exe', '-NoProfile', '-Command', f'New-Item -ItemType Directory -Path "{wl_win}" -Force | Out-Null'],
            stderr=subprocess.DEVNULL, timeout=5,
        )
        wl_linux = subprocess.check_output(
            ['wslpath', '-u', wl_win], stderr=subprocess.DEVNULL, timeout=3
        ).decode().strip()
        print(f'[mpv] wl_win={wl_win!r} wl_linux={wl_linux!r}', flush=True)
        return wl_win, wl_linux
    except Exception as e:
        print(f'[mpv] _make_wl_dir_wsl fallback: {e}', flush=True)
        # Fallback: Linux side (may not be writable by Windows MPV)
        wl_linux = f'/tmp/mpv-wl-{uid}'
        os.makedirs(wl_linux, exist_ok=True)
        try:
            wl_win = subprocess.check_output(
                ['wslpath', '-w', wl_linux], stderr=subprocess.DEVNULL, timeout=3
            ).decode().strip()
        except Exception:
            wl_win = wl_linux
        return wl_win, wl_linux


_SKIP_LUA = _Path(__file__).resolve().parents[2] / 'mpv' / 'skip-intro.lua'
_SKIP_SECS = int(os.environ.get('ANIME_SKIP_SECS', '90'))      # 1:30 default
_SKIP_WINDOW = int(os.environ.get('ANIME_SKIP_WINDOW', '240'))  # show button for first N s


def _skip_args(dest_dir_linux: str, script_path_for_mpv: str) -> list:
    """Copy skip-intro.lua next to the watch-later dir and return the mpv args that load
    it (floating 'Saltar OP' button + key). script_path_for_mpv is the path string mpv
    will read (a Windows path under WSL, a linux path otherwise)."""
    if not _SKIP_LUA.exists():
        return []
    try:
        os.makedirs(dest_dir_linux, exist_ok=True)
        shutil.copy2(str(_SKIP_LUA), str(_Path(dest_dir_linux) / 'skip-intro.lua'))
    except Exception:
        return []
    return [f'--script={script_path_for_mpv}',
            f'--script-opts=skip-intro-skip={_SKIP_SECS},skip-intro-window={_SKIP_WINDOW}']


def _launch_mpv(file_path: str, sub_file: str = '', start_pos: float = 0.0) -> tuple:
    """Open the video file with mpv.
    Returns (ok, proc, wl_dir): proc is Popen|None; wl_dir is the Linux watch-later dir for reading.
    --save-position-on-quit writes position to wl_dir when MPV exits — used for watched detection
    and resume. This works around the WSL2 interop shim exiting early before Windows MPV closes."""

    def _wl_args(wl_win_path):
        args = [f'--watch-later-dir={wl_win_path}', '--save-position-on-quit',
                '--ontop']  # always-on-top so MPV appears above browser/other windows
        if start_pos > 30:
            args.append(f'--start={start_pos:.1f}')
        return args

    if _is_wsl():
        # Create watch-later dir on Windows side so MPV can write to it
        wl_win, wl_dir = _make_wl_dir_wsl()
        try:
            win_path = subprocess.check_output(
                ['wslpath', '-w', file_path], stderr=subprocess.DEVNULL, timeout=3
            ).decode().strip()
        except Exception:
            win_path = file_path
        win_sub = ''
        if sub_file and _Path(sub_file).exists():
            try:
                win_sub = subprocess.check_output(
                    ['wslpath', '-w', sub_file], stderr=subprocess.DEVNULL, timeout=3
                ).decode().strip()
            except Exception:
                win_sub = sub_file
        mpv_bin = _find_mpv_exe()
        print(f'[mpv] bin={mpv_bin!r} win_path={win_path!r} start={start_pos:.0f}s wl_win={wl_win!r}', flush=True)
        if mpv_bin:
            try:
                # Use PowerShell Start-Process so MPV opens in the foreground
                args = [win_path] + _wl_args(wl_win)
                args += _skip_args(wl_dir, wl_win + '\\skip-intro.lua')
                if win_sub:
                    args.append(f'--sub-file={win_sub}')

                def _ps_quote(s):
                    return "'" + s.replace("'", "''") + "'"

                ps_args = ','.join(_ps_quote(a) for a in args)
                ps_cmd = (
                    f'$p = Start-Process -FilePath {_ps_quote(mpv_bin)} '
                    f'-ArgumentList @({ps_args}) -PassThru; '
                    f'Write-Output $p.Id'
                )
                pid_str = subprocess.check_output(
                    ['powershell.exe', '-NoProfile', '-Command', ps_cmd],
                    stderr=subprocess.DEVNULL, timeout=15,
                ).decode().strip()
                win_pid = int(pid_str)
                print(f'[mpv] launched via Start-Process win_pid={win_pid}', flush=True)
                return (True, None, wl_dir)  # proc=None; tracked via tasklist
            except Exception as e:
                print(f'[mpv] Start-Process failed: {e}, trying direct Popen', flush=True)
                try:
                    cmd = [mpv_bin, win_path] + _wl_args(wl_win)
                    cmd += _skip_args(wl_dir, wl_win + '\\skip-intro.lua')
                    if win_sub:
                        cmd += [f'--sub-file={win_sub}']
                    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    print(f'[mpv] direct launch pid={proc.pid}', flush=True)
                    return (True, proc, wl_dir)
                except Exception as e2:
                    print(f'[mpv] direct launch failed: {e2}', flush=True)
        for cmd_exe in [_CMD_EXE, 'cmd.exe']:
            try:
                subprocess.Popen(
                    [cmd_exe, '/c', 'start', '', win_path],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
                print(f'[mpv] fallback via cmd.exe', flush=True)
                return (True, None, wl_dir)
            except Exception:
                continue
        return (False, None, wl_dir)
    else:
        wl_dir = f'/tmp/mpv-wl-{uuid.uuid4().hex[:8]}'
        os.makedirs(wl_dir, exist_ok=True)
        for mpv_bin in ['mpv']:
            try:
                cmd = [mpv_bin, file_path] + _wl_args(wl_dir)
                cmd += _skip_args(wl_dir, str(_Path(wl_dir) / 'skip-intro.lua'))
                if sub_file and _Path(sub_file).exists():
                    cmd += [f'--sub-file={sub_file}']
                proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return (True, proc, wl_dir)
            except Exception:
                pass
        try:
            subprocess.Popen(['xdg-open', file_path],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return (True, None, wl_dir)
        except Exception:
            return (False, None, wl_dir)


_WATCHED_THRESHOLD = 0.85  # must watch ≥85% before episode is auto-marked watched


def _read_wl_position(wl_dir: str) -> float:
    """Read saved playback position from MPV watch-later dir. Returns 0 if not found."""
    files = glob.glob(os.path.join(wl_dir, '*'))
    for f in files:
        try:
            with open(f) as fh:
                for line in fh:
                    if line.startswith('start='):
                        return float(line.split('=', 1)[1].strip())
        except Exception:
            pass
    return 0.0


def _wait_for_mpv_close(wl_dir: str, timeout: float = 14400) -> float:
    """Wait for Windows MPV to close (WSL2 interop exits early).
    Polls for watch-later file (mid-episode quit) or absence of mpv.exe in tasklist (EOS/crash).
    Returns playback position in seconds (0 = EOS or no position saved)."""
    deadline = time.time() + timeout
    # Give MPV 4 seconds to start before first tasklist check
    startup_grace = time.time() + 4
    while time.time() < deadline:
        pos = _read_wl_position(wl_dir)
        if pos > 0:
            time.sleep(0.5)  # ensure file write is complete
            final = _read_wl_position(wl_dir)
            print(f'[mpv] position file found: {final:.0f}s', flush=True)
            return final
        if time.time() > startup_grace:
            try:
                out = subprocess.check_output(
                    ['tasklist.exe', '/FI', 'IMAGENAME eq mpv.exe', '/NH'],
                    stderr=subprocess.DEVNULL, timeout=5,
                ).decode()
                if 'mpv.exe' not in out.lower():
                    # MPV process gone — do one final check with delay in case the
                    # watch-later file is being flushed to disk at this exact moment
                    time.sleep(1.0)
                    final_pos = _read_wl_position(wl_dir)
                    if final_pos > 0:
                        print(f'[mpv] mpv.exe gone but position file appeared: {final_pos:.0f}s', flush=True)
                        return final_pos
                    print(f'[mpv] mpv.exe gone from tasklist → EOS', flush=True)
                    return 0.0  # MPV gone, no position file → EOS or clean finish
            except Exception:
                pass
        time.sleep(0.5)
    return 0.0


def _track_mpv_session(proc, wl_dir: str, anime_id: str, ep_str: str, duration: float):
    """Background thread: track MPV session, detect watched status, save resume position.

    WSL2: the interop shim exits in ~5s even though Windows MPV keeps running.
    We use --save-position-on-quit + watch-later dir to get the real close signal.
    - Position file appears  → user quit mid-episode; save for resume if < threshold
    - No position file + mpv.exe gone → episode finished (EOS) or quick close → mark watched
    """
    from api.runtime import push_sse_event
    print(f'[mpv] tracking anime={anime_id} ep={ep_str} dur={duration:.0f}s wl_dir={wl_dir!r}', flush=True)
    if proc is not None:
        try:
            proc.wait()
        except Exception as e:
            print(f'[mpv] proc.wait() error: {e}', flush=True)
    else:
        # cmd.exe / no-proc fallback: give MPV a moment to start before polling
        print(f'[mpv] proc=None, waiting 6s for MPV to start…', flush=True)
        time.sleep(6)

    if _is_wsl():
        # proc.wait() returned fast (interop shim) — wait for actual Windows MPV
        position = _wait_for_mpv_close(wl_dir, timeout=14400)
    else:
        time.sleep(0.3)
        position = _read_wl_position(wl_dir)

    try:
        shutil.rmtree(wl_dir, ignore_errors=True)
    except Exception:
        pass

    # position > 0 → user quit mid-episode; position == 0 → EOS or quick close
    if position > 0 and duration > 0:
        watched  = (position / duration) >= _WATCHED_THRESHOLD
        save_pos = 0 if watched else int(position)
    elif position > 0:
        # duration unknown (ffprobe failed) — save position unconditionally for resume
        watched  = False
        save_pos = int(position)
    else:
        watched  = True   # EOS = finished
        save_pos = 0

    print(f'[mpv] session end: pos={position:.0f}s dur={duration:.0f}s watched={watched} save_pos={save_pos}', flush=True)

    try:
        lib = _lib_read()
        if anime_id not in lib:
            return
        # Always persist duration so frontend can compute progress %
        if duration > 0:
            lib[anime_id].setdefault('durations', {})[ep_str] = int(duration)

        if save_pos > 30:
            lib[anime_id].setdefault('positions', {})[ep_str] = save_pos
        else:
            lib[anime_id].get('positions', {}).pop(ep_str, None)

        if watched:
            lib[anime_id].setdefault('watched', {})[ep_str] = True
            now = int(time.time())
            lib[anime_id]['last_watched_at'] = now
            _lib_write(lib)
            _history_append(anime_id, lib[anime_id].get('title', anime_id),
                            int(ep_str), lib[anime_id].get('cover', ''))
            print(f'[mpv] marked watched: anime={anime_id} ep={ep_str}', flush=True)
            push_sse_event('watched', anime_id=anime_id, ep_str=ep_str,
                           last_watched_at=now, watched=True,
                           duration=int(duration), from_mpv=True)
        else:
            _lib_write(lib)
            push_sse_event('position', anime_id=anime_id, ep_str=ep_str,
                           position=save_pos, duration=int(duration))
    except Exception as e:
        print(f'[mpv] track error: {e}', flush=True)


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
    # SxxExx / SxxOVAxx patterns — extract episode part only
    m = re.search(r'\bS\d{1,2}[Ee](\d{1,3})\b', clean, re.I)
    if m:
        n = int(m.group(1))
        if 1 <= n <= 999:
            return n
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
          bannerImage
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
            'banner':       it.get('bannerImage') or '',
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


# ── Animetosho search (HTML scrape — JSON API discontinued) ───────────────────

_TOSHO = 'https://animetosho.org'

@anime_bp.route('/torrents_tosho')
def get_torrents_tosho():
    q = request.args.get('q', '').strip()
    if not q:
        return jsonify([])
    try:
        resp = _http.get(
            f'{_TOSHO}/search',
            params={'q': q},
            headers={'User-Agent': _MAL_UA},
            timeout=10,
        )
        html = resp.text
    except Exception as e:
        return jsonify({'error': str(e)}), 500

    # Split into entry blocks
    raw_blocks = re.split(r'(?=<div class="home_list_entry)', html)

    results = []
    seen: set = set()

    for block in raw_blocks[1:]:
        # Title + view URL
        link_m = re.search(r'<div class="link"><a href="([^"]+)">([^<]+)</a></div>', block)
        if not link_m:
            continue
        view_url = link_m.group(1)
        title    = _html_unescape(link_m.group(2).strip())

        # Torrent URL — path contains hex info_hash
        torrent_m = re.search(
            r'href="(https?://animetosho\.org/storage/torrent/([a-f0-9]{40})/[^"]+\.torrent)"',
            block,
        )
        info_hash   = torrent_m.group(2).lower() if torrent_m else ''
        torrent_url = torrent_m.group(1)         if torrent_m else ''

        if info_hash and info_hash in seen:
            continue
        if info_hash:
            seen.add(info_hash)

        # Magnet
        mag_m  = re.search(r'href="(magnet:[^"]+)"', block)
        magnet = _html_unescape(mag_m.group(1)) if mag_m else (_magnet(info_hash, title) if info_hash else '')

        # Size (display string already formatted)
        size_m = re.search(r'<div class="size"[^>]*>([^<]+)</div>', block)
        size   = size_m.group(1).strip() if size_m else ''

        # Date
        date_m = re.search(r'<div class="date" title="Date/time submitted:\s*([^"]+)"', block)
        date   = date_m.group(1).strip() if date_m else ''

        results.append({
            'title':       title,
            'torrent_url': torrent_url,
            'view_url':    view_url if view_url.startswith('http') else f'{_TOSHO}{view_url}',
            'magnet':      magnet,
            'info_hash':   info_hash,
            'size':        size,
            'seeders':     -1,   # not shown in search HTML
            'leechers':    0,
            'trusted':     False,
            'date':        date,
            'episode':     _parse_episode(title),
            'quality':     _parse_quality(title),
            'group':       _parse_group(title),
            'source':      'tosho',
        })

    return jsonify(results)


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
        elif action == 'recheck':
            _q('post', '/torrents/recheck', data={'hashes': hash_})
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ── Anime library ──────────────────────────────────────────────────────────────

@anime_bp.route('/airing')
def anime_airing_get():
    """Fresh airing info (last/next aired episode) for the library's AniList ids.
    Powers the 'new episode just aired' hero. Cached ~30 min."""
    lib = _lib_read()
    al_ids = [v.get('al_id') for v in lib.values() if v.get('al_id')]
    try:
        return jsonify(_fetch_airing(al_ids))
    except Exception:
        return jsonify({})


@anime_bp.route('/library')
def anime_library_get():
    global _backfill_done
    if not _backfill_done:
        threading.Thread(target=_backfill_anime_metadata, daemon=True).start()
        _backfill_done = True  # prevent re-spawning; thread sets it True when done

    lib = _lib_read()
    # Cached episode thumbnails (one glob) → so the UI can show thumbs for episodes
    # whose files were freed, keeping a revisited series looking organic.
    try:
        all_thumbs = {f.name for f in _THUMBS_DIR.glob('*.jpg')}
    except Exception:
        all_thumbs = set()
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
                if ep_num > 0 and ep_num not in ep_files:
                    # First-match wins: for multi-season batches sorted alphabetically,
                    # S01 files come before S02, so Season 1 title names are preferred.
                    ep_files[ep_num] = stem
            # If only one video file exists, store it under key -1 (single-file torrent)
            if not ep_files and len(raw_files) == 1:
                ep_files[-1] = _Path(raw_files[0].get('name', '')).stem
            files_map[ih] = ep_files
        except Exception:
            files_map[ih] = {}

    result = []
    for anime_id, anime in lib.items():
        watched_map   = anime.get('watched', {})
        positions_map = anime.get('positions', {})
        durations_map = anime.get('durations', {})
        local_path = anime.get('local_path', '')
        if local_path:
            overrides = anime.get('episode_overrides', {})
            local_eps = _scan_local_episodes(local_path, overrides)
            regular_count = sum(1 for ep in local_eps if ep.get('ep_type', 'episode') == 'episode')
            episodes_out = [
                {
                    'num':        ep['num'],
                    'title':      _local_ep_title(_Path(ep['path']).stem, ep.get('ep_type', 'episode')),
                    'info_hash':  '',
                    'progress':   100,
                    'state':      'local',
                    'size':       0,
                    'in_qbt':     False,
                    'in_local':   True,
                    'local_path': ep['path'],   # specific file path for direct playback
                    'added_on':   0,
                    'watched':    bool(watched_map.get(str(ep['num']))),
                    'resume_pos': positions_map.get(str(ep['num']), 0),
                    'duration':   durations_map.get(str(ep['num']), 0),
                    'ep_type':    ep.get('ep_type', 'episode'),
                    'filename':   ep.get('filename', _Path(ep['path']).name),
                }
                for ep in local_eps
            ]
            series_total = anime.get('total_episodes') or 0

            # Add placeholder entries for episodes not yet downloaded
            if series_total > 0:
                known_ep_nums = {e['num'] for e in episodes_out if e.get('ep_type', 'episode') == 'episode'}
                for n in range(1, series_total + 1):
                    if n not in known_ep_nums:
                        episodes_out.append({
                            'num': n, 'title': '', 'info_hash': '', 'progress': 0,
                            'state': 'missing', 'size': 0,
                            'in_qbt': False, 'in_local': False,
                            'added_on': 0, 'watched': False,
                            'ep_type': 'episode',
                        })
                episodes_out.sort(key=lambda e: (e.get('ep_type', 'episode') != 'episode', e['num']))

            for e in episodes_out:
                _tk = f'{anime_id}_sp{e["num"]}.jpg' if e.get('ep_type') == 'special' else f'{anime_id}_{e["num"]}.jpg'
                e['has_thumb'] = _tk in all_thumbs

            result.append({
                'id': anime_id,
                'al_id': anime.get('al_id'),
                'mal_id': anime.get('mal_id'),
                'title': anime.get('title', ''),
                'title_romaji': anime.get('title_romaji', ''),
                'cover': anime.get('cover', ''),
                'cover_xl': anime.get('cover_xl', ''),
                'banner': anime.get('banner', ''),
                'banner_source': anime.get('banner_source', ''),
                'genres': anime.get('genres', []),
                'season': anime.get('season', ''),
                'season_year': anime.get('season_year'),
                'total_episodes': series_total or regular_count,
                'format': anime.get('format', ''),
                'status': anime.get('status', ''),
                'episodes': episodes_out,
                'downloaded_count': regular_count,
                'is_local': True,
                'added_at': anime.get('added_at', 0),
                'last_watched_at': anime.get('last_watched_at', 0),
            })
            continue

        total = anime.get('total_episodes') or 0
        ep_map = anime.get('episodes', {})
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
                'in_qbt':     in_qbt,
                'added_on':   ep_data.get('added_on', 0),
                'watched':    bool(watched_map.get(ep_str)),
                'resume_pos': positions_map.get(ep_str, 0),
                'duration':   durations_map.get(ep_str, 0),
            })

        episodes_out.sort(key=lambda e: (e['num'] < 0, e['num']))

        # Movies / single-episode entries: a single-file torrent is registered under
        # episode -1 (unknown). Treat the downloaded file as episode 1 so the detail
        # doesn't show a phantom "-1" next to a placeholder ep 1.
        fmt = anime.get('format', '')
        is_single = (total == 1 or fmt in ('MOVIE', 'MUSIC')
                     or (total == 0 and len(episodes_out) == 1 and episodes_out[0]['num'] < 1))
        if is_single:
            dl = next((e for e in episodes_out if e.get('info_hash')), None)
            if dl:
                dl['num'] = 1
                episodes_out = [dl]
            else:
                episodes_out = [e for e in episodes_out if e['num'] > 0]

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

        for e in episodes_out:
            e['has_thumb'] = f'{anime_id}_{e["num"]}.jpg' in all_thumbs

        done_count = sum(1 for e in episodes_out if e.get('in_qbt') and e.get('num', 0) > 0)
        # added_at fallback: earliest episode added_on (for entries created before this field existed)
        ep_times = [ep.get('added_on', 0) for ep in ep_map.values() if ep.get('added_on', 0) > 0]
        added_at_fallback = min(ep_times) if ep_times else 0
        result.append({
            'id': anime_id,
            'al_id': anime.get('al_id'),
            'mal_id': anime.get('mal_id'),
            'title': anime.get('title', ''),
            'title_romaji': anime.get('title_romaji', ''),
            'cover': anime.get('cover', ''),
            'cover_xl': anime.get('cover_xl', ''),
            'banner': anime.get('banner', ''),
            'banner_source': anime.get('banner_source', ''),
            'genres': anime.get('genres', []),
            'season': anime.get('season', ''),
            'season_year': anime.get('season_year'),
            'total_episodes': total,
            'format': anime.get('format', ''),
            'status': anime.get('status', ''),
            'episodes': episodes_out,
            'downloaded_count': done_count,
            'added_at': anime.get('added_at', added_at_fallback),
            'last_watched_at': anime.get('last_watched_at', 0),
        })
    return jsonify(result)


@anime_bp.route('/library/add', methods=['POST'])
def anime_library_add():
    global _backfill_done
    _backfill_done = False   # re-enrich (banner/genres/TMDB) for the newly added entry
    data = request.get_json(silent=True) or {}
    al_id  = data.get('al_id')
    mal_id = data.get('mal_id')
    title  = (data.get('title') or '').strip()
    anime_id = str(al_id or mal_id or re.sub(r'[^a-z0-9]+', '_', title.lower())[:40])
    if not anime_id:
        return jsonify({'error': 'need al_id, mal_id, or title'}), 400

    total_eps = data.get('total_episodes')
    fmt       = data.get('format', '')

    # If episode count is missing but we have an AniList id, fetch it now
    if not total_eps and al_id:
        try:
            _q = '''query($id:Int){Media(id:$id,type:ANIME){episodes format
                title{english romaji} coverImage{large}}}'''
            r = _http.post(_ANILIST, json={'query': _q, 'variables': {'id': int(al_id)}}, timeout=8)
            m = (r.json().get('data') or {}).get('Media') or {}
            if m.get('episodes'):
                total_eps = m['episodes']
            if m.get('format') and not fmt:
                fmt = m['format']
        except Exception:
            pass

    lib = _lib_read()
    if anime_id not in lib:
        lib[anime_id] = {
            'al_id': al_id, 'mal_id': mal_id,
            'title': title,
            'title_romaji': data.get('title_romaji', ''),
            'cover': data.get('cover', ''),
            'total_episodes': total_eps,
            'format': fmt,
            'episodes': {},
            'added_at': int(time.time()),
        }
    else:
        if not lib[anime_id].get('cover') and data.get('cover'):
            lib[anime_id]['cover'] = data['cover']
        if not lib[anime_id].get('total_episodes') and total_eps:
            lib[anime_id]['total_episodes'] = total_eps
        if not lib[anime_id].get('format') and fmt:
            lib[anime_id]['format'] = fmt

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


@anime_bp.route('/library/<anime_id>/ep_override', methods=['POST'])
def anime_ep_override(anime_id):
    """Set or clear a manual type override for a local episode file.
    Body: {filename: str, type: 'episode'|'special'|'hidden'}
    """
    data = request.get_json(silent=True) or {}
    filename = (data.get('filename') or '').strip()
    ep_type  = data.get('type', 'episode')
    if not filename:
        return jsonify({'error': 'filename required'}), 400
    if ep_type not in ('episode', 'special', 'hidden'):
        return jsonify({'error': 'type must be episode, special or hidden'}), 400

    lib = _lib_read()
    if anime_id not in lib:
        return jsonify({'error': 'not found'}), 404

    overrides = lib[anime_id].setdefault('episode_overrides', {})
    if ep_type == 'episode':
        overrides.pop(filename, None)   # reset → remove override
    else:
        overrides[filename] = ep_type

    if not overrides:
        lib[anime_id].pop('episode_overrides', None)

    _lib_write(lib)
    return jsonify({'ok': True})


@anime_bp.route('/library/<anime_id>/status', methods=['POST'])
def anime_set_status(anime_id):
    """Set watch status for a library entry.
    Body: { "status": "watching" | "completed" | "plan_to_watch" | "on_hold" | "dropped" | "" }
    """
    ALLOWED = {'watching', 'completed', 'plan_to_watch', 'on_hold', 'dropped', ''}
    data = request.get_json(silent=True) or {}
    status = data.get('status', '')
    if status not in ALLOWED:
        return jsonify({'error': 'invalid status'}), 400

    lib = _lib_read()
    if anime_id not in lib:
        return jsonify({'error': 'not found'}), 404

    lib[anime_id]['status'] = status
    _lib_write(lib)
    return jsonify({'ok': True})


@anime_bp.route('/library/<anime_id>/clear_episodes', methods=['POST'])
def anime_clear_episodes(anime_id):
    """Remove all episode records from a library entry, optionally deleting torrents/files.
    Body: { "remove_from_qbt": bool, "delete_files": bool }
    """
    data = request.get_json(silent=True) or {}
    remove_from_qbt = bool(data.get('remove_from_qbt', False))
    delete_files = bool(data.get('delete_files', False))

    lib = _lib_read()
    if anime_id not in lib:
        return jsonify({'error': 'not found'}), 404

    entry = lib[anime_id]

    # Local-folder-linked anime have no torrent. Free space by deleting the scanned
    # video files and unlinking the folder (+ its scan mapping) so it isn't re-linked.
    # The entry is kept (cover/status/watch progress) → re-downloadable via torrents.
    local_path = entry.get('local_path', '')
    if local_path and delete_files:
        for ep in _scan_local_episodes(local_path, entry.get('episode_overrides', {})):
            try:
                _Path(ep['path']).unlink()
            except Exception:
                pass
        entry.pop('local_path', None)
        entry.pop('episode_overrides', None)
        sp = _scanpaths_read()
        if sp.get('mappings', {}).pop(local_path, None) is not None:
            _scanpaths_write(sp)

    if remove_from_qbt:
        seen_hashes = set()
        for ep in entry.get('episodes', {}).values():
            ih = ep.get('info_hash', '')
            if ih and ih not in seen_hashes:
                seen_hashes.add(ih)
                try:
                    _q('post', '/torrents/delete',
                       data={'hashes': ih, 'deleteFiles': str(delete_files).lower()})
                except Exception:
                    pass

    entry['episodes'] = {}
    _lib_write(lib)
    return jsonify({'ok': True})


@anime_bp.route('/library/<anime_id>/link_torrent', methods=['POST'])
def anime_link_torrent(anime_id):
    """Link an existing torrent to a library entry episode slot.
    Body: { "info_hash": str, "episode": int (default 0), "torrent_title": str }
    If episode == 0 and total_episodes > 0, pre-fills episodes 1..total with from_batch=True.
    """
    data = request.get_json(silent=True) or {}
    info_hash = (data.get('info_hash') or '').strip().lower()
    episode = int(data.get('episode', 0))
    torrent_title = (data.get('torrent_title') or '').strip()

    if not info_hash:
        return jsonify({'error': 'info_hash required'}), 400

    lib = _lib_read()
    if anime_id not in lib:
        return jsonify({'error': 'not found'}), 404

    entry = lib[anime_id]
    episodes = entry.setdefault('episodes', {})
    now = int(time.time())

    subpath = (data.get('subpath') or '').strip()
    ep_record = {
        'title': torrent_title,
        'info_hash': info_hash,
        'added_on': now,
    }
    if subpath:
        ep_record['subpath'] = subpath

    if episode == 0:
        episodes['0'] = ep_record  # always write batch slot
        total = int(entry.get('total_episodes', 0))
        if total > 0:
            for n in range(1, total + 1):
                slot = str(n)
                existing = episodes.get(slot)
                if existing is None:
                    episodes[slot] = dict(ep_record, from_batch=True)
                elif existing.get('info_hash', '') == info_hash or existing.get('from_batch'):
                    # Update subpath on existing batch-linked slots so re-linking with a
                    # subpath always propagates to all pre-filled episode records.
                    if subpath:
                        existing['subpath'] = subpath
                    elif 'subpath' in existing:
                        del existing['subpath']
                    existing['from_batch'] = True
    else:
        episodes[str(episode)] = ep_record

    _lib_write(lib)
    return jsonify({'ok': True})


@anime_bp.route('/scanpaths', methods=['GET'])
def anime_get_scanpaths():
    return jsonify(_scanpaths_read().get('paths', []))


@anime_bp.route('/scanpaths', methods=['POST'])
def anime_add_scanpath():
    body = request.get_json(silent=True) or {}
    path = (body.get('path') or '').strip()
    if not path:
        return jsonify({'error': 'path required'}), 400
    data = _scanpaths_read()
    paths = data.get('paths', [])
    if path not in paths:
        paths.append(path)
        data['paths'] = paths
        _scanpaths_write(data)
    return jsonify({'ok': True})


@anime_bp.route('/scanpaths', methods=['DELETE'])
def anime_remove_scanpath():
    body = request.get_json(silent=True) or {}
    path = (body.get('path') or '').strip()
    data = _scanpaths_read()
    data['paths'] = [p for p in data.get('paths', []) if p != path]
    _scanpaths_write(data)
    return jsonify({'ok': True})


@anime_bp.route('/scan/folders', methods=['GET'])
def anime_scan_folders():
    data = _scanpaths_read()
    mappings = data.get('mappings', {})
    lib = _lib_read()
    result = []
    for root in data.get('paths', []):
        for folder in _list_anime_folders(root):
            folder_name = _Path(folder).name
            mapped_id = mappings.get(folder)
            matched = lib.get(mapped_id) if mapped_id else None
            result.append({
                'folder': folder,
                'name': folder_name,
                'mapped_id': mapped_id,
                'matched_title': matched.get('title', '') if matched else '',
                'matched_cover': matched.get('cover', '') if matched else '',
                'suggestion': None,  # fetched lazily via /scan/suggest
            })
    return jsonify(result)


def _normalize(s: str) -> str:
    return re.sub(r'[^a-z0-9]', '', (s or '').lower())


def _clean_folder_name(name: str) -> str:
    """Strip season/quality/episode suffixes from folder names for AniList search."""
    s = name
    # Remove anything in square brackets: [1080p], [BD], [Multiple Subtitle], etc.
    s = re.sub(r'\[.*?\]', ' ', s)
    # Remove anything in parentheses that looks like metadata: (2014), (BD), (1080p)
    s = re.sub(r'\(\s*(?:\d{4}|\d{3,4}p|BD|BluRay|Web-DL)\s*\)', ' ', s, flags=re.I)
    # Remove episode ranges: "- 01 ~ 13", "01-13", "+ Special" at end
    s = re.sub(r'[-–+]\s*\d{1,3}\s*[~\-–]\s*\d{1,3}.*$', '', s)
    s = re.sub(r'\+\s*special.*$', '', s, flags=re.I)
    # Remove season/part suffixes
    s = re.sub(r'\s+(?:(?:season|s)\s*\d+|\d+(?:st|nd|rd|th)\s+season|part\s*\d+)\b.*$', '', s, flags=re.I)
    return s.strip(' -_.')


@anime_bp.route('/scan/suggest', methods=['GET'])
def anime_scan_suggest():
    """Return the best AniList match for a folder name (called lazily per row)."""
    folder_name = request.args.get('name', '').strip()
    if not folder_name:
        return jsonify(None)
    try:
        search_name = _clean_folder_name(folder_name)
        q = '''query($s:String){Page(perPage:5){media(search:$s,type:ANIME,sort:SEARCH_MATCH){
            id title{romaji english}coverImage{large}}}}'''
        r = _http.post(_ANILIST, json={'query': q, 'variables': {'s': search_name}}, timeout=8)
        items = ((r.json().get('data') or {}).get('Page') or {}).get('media') or []
        if not items:
            return jsonify(None)

        norm_folder = _normalize(folder_name)
        norm_clean  = _normalize(search_name)

        def _score(item):
            t = item.get('title') or {}
            candidates = [t.get('romaji', ''), t.get('english', '') or '']
            for c in candidates:
                nc = _normalize(c)
                if nc in (norm_folder, norm_clean):
                    return 3  # exact match
            for c in candidates:
                nc = _normalize(c)
                if nc and (nc in norm_clean or norm_clean in nc):
                    return 2  # substring match
            return 1  # best AniList relevance, no title match

        best = max(items, key=_score)
        t = best.get('title') or {}
        return jsonify({
            'id': best['id'],
            'title': t.get('english') or t.get('romaji', ''),
            'cover': (best.get('coverImage') or {}).get('large', ''),
        })
    except Exception:
        pass
    return jsonify(None)


@anime_bp.route('/browse', methods=['GET'])
def anime_browse():
    path = request.args.get('path', '').strip()

    def _to_win(p: str) -> str:
        if not _is_wsl():
            return p
        try:
            return subprocess.check_output(
                ['wslpath', '-w', p], stderr=subprocess.DEVNULL, timeout=3
            ).decode().strip()
        except Exception:
            return p

    def _dir_entry(p: _Path) -> dict:
        return {'name': p.name, 'path': str(p), 'win_path': _to_win(str(p))}

    if not path:
        # Root: show available Windows drives under /mnt/
        if _is_wsl():
            drives = []
            for letter in string.ascii_lowercase:
                mp = _Path(f'/mnt/{letter}')
                if mp.exists() and mp.is_dir():
                    drives.append({
                        'name': f'{letter.upper()}:',
                        'path': str(mp),
                        'win_path': f'{letter.upper()}:\\',
                        'is_drive': True,
                    })
            return jsonify({'path': '', 'win_path': '', 'parent': None, 'items': drives})
        else:
            items = [_dir_entry(p) for p in sorted(_Path('/').iterdir()) if p.is_dir()]
            return jsonify({'path': '/', 'win_path': '/', 'parent': None, 'items': items})

    # Convert Windows path if needed
    if _is_wsl() and re.match(r'^[A-Za-z]:[/\\]', path):
        path = _win_to_wsl(path)

    p = _Path(path)
    if not p.exists() or not p.is_dir():
        return jsonify({'error': 'not found'}), 404

    items = []
    try:
        for sub in sorted(p.iterdir()):
            if sub.is_dir() and not sub.name.startswith('.'):
                items.append(_dir_entry(sub))
    except PermissionError:
        pass

    parent = str(p.parent) if str(p.parent) != str(p) else None
    return jsonify({
        'path': str(p),
        'win_path': _to_win(str(p)),
        'parent': parent,
        'items': items,
    })


@anime_bp.route('/scan/match', methods=['POST'])
def anime_scan_match():
    body = request.get_json(silent=True) or {}
    folder     = (body.get('folder') or '').strip()
    anilist_id = str(body.get('anilist_id', '')).strip()
    title      = (body.get('title') or '').strip()
    cover      = (body.get('cover') or '').strip()
    if not folder or not anilist_id:
        return jsonify({'error': 'folder and anilist_id required'}), 400
    data = _scanpaths_read()
    data.setdefault('mappings', {})[folder] = anilist_id
    _scanpaths_write(data)

    # Fetch full AniList metadata so we store episodes/format/romaji
    al_meta = {}
    try:
        q = '''query($id:Int){Media(id:$id,type:ANIME){
            episodes format status
            title{romaji english native}
            coverImage{large}
        }}'''
        r = _http.post(_ANILIST, json={'query': q, 'variables': {'id': int(anilist_id)}}, timeout=8)
        al_media = (r.json().get('data') or {}).get('Media') or {}
        if al_media:
            t = al_media.get('title') or {}
            al_meta = {
                'title':         t.get('english') or t.get('romaji') or title,
                'title_romaji':  t.get('romaji') or '',
                'title_native':  t.get('native') or '',
                'cover':         (al_media.get('coverImage') or {}).get('large') or cover,
                'total_episodes': al_media.get('episodes'),
                'format':        al_media.get('format') or '',
                'status':        al_media.get('status') or '',
            }
    except Exception:
        pass

    lib = _lib_read()
    if anilist_id not in lib:
        lib[anilist_id] = {'title': title, 'cover': cover, 'episodes': {}}
    lib[anilist_id]['local_path'] = folder
    lib[anilist_id]['al_id'] = int(anilist_id)
    for k, v in al_meta.items():
        if v and (not lib[anilist_id].get(k)):
            lib[anilist_id][k] = v
    # Always update total_episodes and format from AniList (they may improve)
    if al_meta.get('total_episodes'):
        lib[anilist_id]['total_episodes'] = al_meta['total_episodes']
    if al_meta.get('format'):
        lib[anilist_id]['format'] = al_meta['format']
    _lib_write(lib)
    return jsonify({'ok': True})


@anime_bp.route('/scan/unmatch', methods=['POST'])
def anime_scan_unmatch():
    body = request.get_json(silent=True) or {}
    folder = (body.get('folder') or '').strip()
    if not folder:
        return jsonify({'error': 'folder required'}), 400
    data = _scanpaths_read()
    anilist_id = data.get('mappings', {}).pop(folder, None)
    _scanpaths_write(data)
    if anilist_id:
        lib = _lib_read()
        if anilist_id in lib and lib[anilist_id].get('local_path') == folder:
            lib[anilist_id].pop('local_path', None)
            if not lib[anilist_id].get('episodes'):
                lib.pop(anilist_id, None)
        _lib_write(lib)
    return jsonify({'ok': True})


@anime_bp.route('/play', methods=['POST'])
def anime_play():
    try:
        data = request.get_json(silent=True) or {}
        anime_id   = data.get('anime_id', '')
        episode    = data.get('episode', -1)
        local_path = (data.get('local_path') or '').strip()
        sub_file   = (data.get('sub_file') or '').strip()
        ep_str     = str(episode)

        # Explicit start_pos override (e.g. skip intro) takes priority over saved resume position
        if data.get('start_pos') is not None:
            start_pos = float(data['start_pos'])
        elif anime_id:
            lib_peek = _lib_read()
            start_pos = float((lib_peek.get(anime_id) or {}).get('positions', {}).get(ep_str, 0))
        else:
            start_pos = 0.0

        def _launch_and_track(video):
            ok, proc, wl_dir = _launch_mpv(video, sub_file, start_pos)
            if not ok:
                return False
            if anime_id:
                dur = _video_duration(video)
                threading.Thread(target=_track_mpv_session,
                                 args=(proc, wl_dir, anime_id, ep_str, dur),
                                 daemon=True).start()
            return True

        if local_path:
            video = _find_video(local_path, int(episode))
            if not video:
                return jsonify({'error': f'no video found in: {local_path}'}), 404
            if not _launch_and_track(video):
                return jsonify({'error': 'failed to launch mpv'}), 500
            return jsonify({'ok': True, 'path': video, 'resume_pos': start_pos})

        info_hash = (data.get('info_hash') or '').lower()
        if not info_hash:
            return jsonify({'error': 'info_hash required'}), 400
        try:
            torrents = _q('get', '/torrents/info', params={'hashes': info_hash}).json()
        except Exception as e:
            return jsonify({'error': f'qBT error: {e}'}), 500
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
        # Read subpath from library episode record (set when linking multi-season batches)
        ep_subpath = ''
        if anime_id:
            lib_ep = (_lib_read().get(anime_id) or {}).get('episodes', {}).get(ep_str, {})
            if not lib_ep:  # fallback to batch ep record
                lib_ep = (_lib_read().get(anime_id) or {}).get('episodes', {}).get('0', {})
            ep_subpath = lib_ep.get('subpath', '')
        video = _find_video(content_path, int(episode), ep_subpath)
        if not video:
            return jsonify({'error': f'no video file found in: {content_path}'}), 404
        if not _launch_and_track(video):
            return jsonify({'error': 'failed to launch mpv — is mpv.exe in PATH?'}), 500
        return jsonify({'ok': True, 'path': video, 'resume_pos': start_pos})
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
          bannerImage
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
            'banner':       it.get('bannerImage') or '',
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
        lib[anime_id]['last_watched_at'] = int(time.time())
        _history_append(anime_id, lib[anime_id].get('title', anime_id),
                        int(episode), lib[anime_id].get('cover', ''))
        state = True
    _lib_write(lib)
    from api.runtime import push_sse_event
    push_sse_event('watched', anime_id=anime_id, ep_str=episode,
                   watched=state, last_watched_at=lib[anime_id].get('last_watched_at', 0))
    return jsonify({'ok': True, 'watched': state})


# ── Episode thumbnail extraction ───────────────────────────────────────────────

_ffprobe_duration = _video_duration  # alias used by thumb endpoint


def _secs_to_hms(secs: float) -> str:
    secs = int(secs)
    h, rem = divmod(secs, 3600)
    m, s = divmod(rem, 60)
    return f'{h:02d}:{m:02d}:{s:02d}'


@anime_bp.route('/thumb/<anime_id>/<int:episode>')
def anime_thumb(anime_id, episode):
    _THUMBS_DIR.mkdir(parents=True, exist_ok=True)
    is_special = request.args.get('special') == '1'
    cache_key   = f'{anime_id}_sp{episode}.jpg' if is_special else f'{anime_id}_{episode}.jpg'
    cache_path  = _THUMBS_DIR / cache_key

    # Serve cached thumb if it exists (no expiry — video files don't change)
    if cache_path.exists() and cache_path.stat().st_size > 0:
        return send_file(str(cache_path), mimetype='image/jpeg',
                         max_age=604800)  # 7 days

    lib = _lib_read()
    anime = lib.get(anime_id)
    if not anime:
        return ('', 404)

    # Local-path anime: find video directly from folder
    local_path = anime.get('local_path', '')
    if local_path:
        if is_special:
            scanned  = _scan_local_episodes(local_path, anime.get('episode_overrides', {}))
            specials = [e for e in scanned if e.get('ep_type') == 'special']
            matched  = next((e for e in specials if e['num'] == episode), None)
            video    = matched['path'] if matched else ''
        else:
            video = _find_video(local_path, episode)
        if not video:
            return ('', 404)
    else:
        ep_map = anime.get('episodes', {})
        ep_data = ep_map.get(str(episode)) or ep_map.get('0') or {}
        info_hash = (ep_data.get('info_hash') or '').lower()
        if not info_hash:
            return ('', 404)
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

    # Get duration and seek to 50% (midpoint)
    duration = _ffprobe_duration(video)
    seek_secs = duration * 0.50 if duration > 0 else 30.0

    # Two-pass seek: fast pre-seek (keyframe) + short accurate post-seek.
    # Using only -ss before -i lands on a B/P-frame without its references → blurry.
    fine_margin = min(6.0, seek_secs)
    pre_ts  = _secs_to_hms(max(0.0, seek_secs - fine_margin))
    fine_ts = _secs_to_hms(fine_margin)

    try:
        subprocess.run(
            ['ffmpeg', '-y',
             '-ss', pre_ts, '-i', video,   # fast seek to near-target keyframe
             '-ss', fine_ts,               # accurate fine-seek post-input (short, cheap)
             '-vframes', '1', '-vf', 'scale=480:-2',
             '-q:v', '2', str(cache_path)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=30,
        )
    except Exception:
        return ('', 500)

    if not cache_path.exists() or cache_path.stat().st_size == 0:
        return ('', 500)

    return send_file(str(cache_path), mimetype='image/jpeg', max_age=604800)


# ── Background thumbnail pre-generation ───────────────────────────────────────

def _pregen_thumbs():
    """Pre-generate missing episode thumbnails for all local anime at startup.
    Runs in background with a 30s startup delay and 2s throttle between thumbs."""
    time.sleep(30)  # let the server finish starting up
    _THUMBS_DIR.mkdir(parents=True, exist_ok=True)
    lib = _lib_read()
    for anime_id, anime in lib.items():
        local_path = anime.get('local_path', '')
        if not local_path:
            continue
        try:
            episodes = _scan_local_episodes(local_path, anime.get('episode_overrides', {}))
        except Exception:
            continue
        for ep in episodes:
            ep_type = ep.get('ep_type', 'episode')
            ep_num  = ep['num']
            cache_key  = f'{anime_id}_sp{ep_num}.jpg' if ep_type == 'special' else f'{anime_id}_{ep_num}.jpg'
            cache_path = _THUMBS_DIR / cache_key
            if cache_path.exists() and cache_path.stat().st_size > 0:
                continue
            video = ep['path']
            try:
                duration   = _video_duration(video)
                seek_secs  = duration * 0.50 if duration > 0 else 30.0
                fine_margin = min(6.0, seek_secs)
                pre_ts  = _secs_to_hms(max(0.0, seek_secs - fine_margin))
                fine_ts = _secs_to_hms(fine_margin)
                subprocess.run(
                    ['ffmpeg', '-y', '-ss', pre_ts, '-i', video, '-ss', fine_ts,
                     '-vframes', '1', '-vf', 'scale=480:-2', '-q:v', '2', str(cache_path)],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30,
                )
            except Exception:
                pass
            time.sleep(2.0)  # throttle to avoid CPU saturation while server is running


threading.Thread(target=_pregen_thumbs, daemon=True).start()


# ── AniSkip — OP/ED timestamp lookup ──────────────────────────────────────────

_ANISKIP = 'https://api.aniskip.com/v2'
_aniskip_cache: dict = {}   # (mal_id, episode) → {op_start, op_end, ed_start, ed_end}


@anime_bp.route('/skip_times/<int:mal_id>/<int:episode>')
def anime_skip_times(mal_id, episode):
    key = (mal_id, episode)
    if key in _aniskip_cache:
        return jsonify(_aniskip_cache[key])
    try:
        r = _http.get(
            f'{_ANISKIP}/skip-times/{mal_id}/{episode}',
            params=[('types[]', 'op'), ('types[]', 'ed'), ('episodeLength', '0')],
            timeout=6,
        )
        r.raise_for_status()
        data = r.json()
        result: dict = {}
        for item in (data.get('results') or []):
            iv  = item.get('interval') or {}
            st  = item.get('skipType', '')
            if st == 'op':
                result['op_start'] = round(iv.get('startTime', 0), 2)
                result['op_end']   = round(iv.get('endTime',   0), 2)
            elif st in ('ed', 'recap'):
                result['ed_start'] = round(iv.get('startTime', 0), 2)
                result['ed_end']   = round(iv.get('endTime',   0), 2)
        _aniskip_cache[key] = result
        return jsonify(result)
    except Exception:
        return jsonify({})


# ── Episode info (Jikan) ───────────────────────────────────────────────────────

_ep_info_cache: dict = {}   # (mal_id, episode) → {title, synopsis, aired, filler, recap}


@anime_bp.route('/episode_info/<int:mal_id>/<int:episode>')
def anime_episode_info(mal_id, episode):
    key = (mal_id, episode)
    if key in _ep_info_cache:
        return jsonify(_ep_info_cache[key])
    try:
        r = _http.get(
            f'{_JIKAN}/anime/{mal_id}/episodes/{episode}',
            timeout=8, headers={'User-Agent': 'Mozilla/5.0'},
        )
        r.raise_for_status()
        d = r.json().get('data') or {}
        result = {
            'title':    (d.get('title') or '').strip(),
            'title_jp': (d.get('title_japanese') or '').strip(),
            'synopsis': (d.get('synopsis') or '').strip(),
            'aired':    (d.get('aired') or '').strip(),
            'filler':   bool(d.get('filler')),
            'recap':    bool(d.get('recap')),
        }
        _ep_info_cache[key] = result
        return jsonify(result)
    except Exception:
        return jsonify({})


# ── Subtitle list endpoint ─────────────────────────────────────────────────────

@anime_bp.route('/subtitles/<anime_id>/<int:episode>')
def anime_subtitles(anime_id, episode):
    """Return subtitle files found alongside the episode video."""
    lib = _lib_read()
    anime = lib.get(anime_id)
    if not anime:
        return jsonify([])

    local_path = anime.get('local_path', '')
    if local_path:
        video = _find_video(local_path, episode)
    else:
        ep_map = anime.get('episodes', {})
        ep_data = ep_map.get(str(episode)) or {}
        ih = (ep_data.get('info_hash') or '').lower()
        if not ih:
            return jsonify([])
        try:
            torrents = _q('get', '/torrents/info', params={'hashes': ih}).json()
        except Exception:
            return jsonify([])
        if not torrents:
            return jsonify([])
        content_path = torrents[0].get('content_path') or torrents[0].get('save_path', '')
        video = _find_video(content_path, episode)

    if not video:
        return jsonify([])
    return jsonify(_find_subtitles(video))


# ── Episode auto-renamer ──────────────────────────────────────────────────────

@anime_bp.route('/rename_preview/<anime_id>')
def anime_rename_preview(anime_id):
    lib = _lib_read()
    if anime_id not in lib:
        return jsonify({'error': 'not found'}), 404
    entry = lib[anime_id]
    local_path = (entry.get('local_path') or '').strip()
    if not local_path or not _Path(local_path).is_dir():
        return jsonify({'error': 'no_local'}), 400

    title = (entry.get('title') or anime_id)
    safe_title = re.sub(r'[\\/:*?"<>|]', '', title).strip()
    overrides = entry.get('episode_overrides', {})
    episodes = _scan_local_episodes(local_path, overrides)

    renames = []
    for ep in episodes:
        old_path = ep['path']
        old_name = ep['filename']
        ext = _Path(old_name).suffix
        if ep['ep_type'] == 'special':
            new_name = f"{safe_title} - SP{ep['num']:02d}{ext}"
        else:
            new_name = f"{safe_title} - E{ep['num']:02d}{ext}"
        renames.append({
            'old_path': old_path,
            'old_name': old_name,
            'new_name': new_name,
            'num': ep['num'],
            'ep_type': ep['ep_type'],
            'changed': old_name != new_name,
        })

    return jsonify({
        'title': title,
        'renames': renames,
        'changes': sum(1 for r in renames if r['changed']),
    })


@anime_bp.route('/rename_apply/<anime_id>', methods=['POST'])
def anime_rename_apply(anime_id):
    lib = _lib_read()
    if anime_id not in lib:
        return jsonify({'error': 'not found'}), 404
    data = request.json or {}
    to_rename = [r for r in data.get('renames', []) if r.get('changed')]
    renamed = 0
    errors = []
    for r in to_rename:
        old_p = _Path(r['old_path'])
        new_p = old_p.parent / r['new_name']
        try:
            if old_p.exists() and not new_p.exists():
                old_p.rename(new_p)
                renamed += 1
            elif new_p.exists() and new_p != old_p:
                errors.append({'file': old_p.name, 'error': 'ya existe'})
        except Exception as e:
            errors.append({'file': old_p.name, 'error': str(e)})
    return jsonify({'renamed': renamed, 'errors': errors})


# ── Watch history endpoints ────────────────────────────────────────────────────

@anime_bp.route('/history')
def anime_history_get():
    return jsonify(_history_read())


@anime_bp.route('/history/clear', methods=['POST'])
def anime_history_clear():
    try:
        _history_path().write_text('[]', encoding='utf-8')
    except Exception:
        pass
    return jsonify({'ok': True})


# ── AniList recommendations ────────────────────────────────────────────────────

_REC_QUERY = """
query ($id: Int) {
  Media(id: $id) {
    recommendations(sort: RATING_DESC, page: 1, perPage: 15) {
      nodes {
        rating
        mediaRecommendation {
          id
          title { romaji english }
          coverImage { large medium }
          averageScore
          genres
          format
          episodes
          status
        }
      }
    }
  }
}
"""


@anime_bp.route('/recommendations/<int:al_id>')
def anime_recommendations(al_id):
    cached = _al_cache_get('recs', al_id)
    if cached is not None:
        return jsonify(cached)
    try:
        resp = _http.post(
            _ANILIST,
            json={'query': _REC_QUERY, 'variables': {'id': al_id}},
            timeout=10,
        )
        nodes = (resp.json()
                 .get('data', {})
                 .get('Media', {})
                 .get('recommendations', {})
                 .get('nodes', []))
        recs = []
        for node in nodes:
            if not node.get('rating'):
                continue
            m = node.get('mediaRecommendation')
            if not m:
                continue
            recs.append({
                'al_id':        m['id'],
                'title':        m['title'].get('english') or m['title'].get('romaji', ''),
                'title_romaji': m['title'].get('romaji', ''),
                'cover':        (m.get('coverImage') or {}).get('large') or (m.get('coverImage') or {}).get('medium', ''),
                'score':        m.get('averageScore') or 0,
                'genres':       (m.get('genres') or [])[:3],
                'format':       m.get('format', ''),
                'episodes':     m.get('episodes') or 0,
                'status':       m.get('status', ''),
                'rating':       node['rating'],
            })
        threading.Thread(target=_al_cache_set, args=('recs', al_id, recs), daemon=True).start()
        return jsonify(recs)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ── AniList tags ───────────────────────────────────────────────────────────────

_TAGS_QUERY = """
query ($id: Int) {
  Media(id: $id) {
    tags { name rank category isAdult }
    idMal
    nextAiringEpisode { episode airingAt }
  }
}
"""

_TAG_BROWSE_QUERY = """
query ($tag: String) {
  Page(page: 1, perPage: 30) {
    media(type: ANIME, tag: $tag, sort: SCORE_DESC, isAdult: false) {
      id idMal
      title { romaji english }
      coverImage { large medium }
      averageScore genres format episodes status
      tags { name rank }
    }
  }
}
"""


@anime_bp.route('/tags/<int:al_id>')
def anime_tags(al_id):
    cached = _al_cache_get('tags', al_id)
    if cached is not None:
        return jsonify(cached)
    try:
        resp = _http.post(
            _ANILIST,
            json={'query': _TAGS_QUERY, 'variables': {'id': al_id}},
            timeout=10,
        )
        media = (resp.json().get('data', {}).get('Media', {}) or {})
        tags  = media.get('tags') or []
        mal_id = media.get('idMal')

        result = [
            {'name': t['name'], 'rank': t['rank'], 'category': t.get('category', '')}
            for t in tags
            if not t.get('isAdult') and t.get('rank', 0) >= 60
        ]
        result.sort(key=lambda x: -x['rank'])

        # Fetch mal_url from Jikan so we can link directly to the community page
        mal_url = ''
        if mal_id:
            try:
                mal_url = f'https://myanimelist.net/anime/{mal_id}'
            except Exception:
                pass

        nae = media.get('nextAiringEpisode') or {}
        next_airing = {'episode': nae['episode'], 'airing_at': nae['airingAt']} if nae.get('airingAt') else None
        payload = {'tags': result, 'mal_url': mal_url, 'mal_id': mal_id, 'next_airing': next_airing}
        threading.Thread(target=_al_cache_set, args=('tags', al_id, payload), daemon=True).start()
        return jsonify(payload)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@anime_bp.route('/browse_tag')
def browse_by_tag():
    tag  = request.args.get('tag', '').strip()
    al_id = request.args.get('al_id', type=int)
    if not tag:
        return jsonify([])
    try:
        # Get source anime's full tag set for similarity scoring
        source_tags: set = set()
        if al_id:
            try:
                r2 = _http.post(
                    _ANILIST,
                    json={'query': _TAGS_QUERY, 'variables': {'id': al_id}},
                    timeout=8,
                )
                raw = (r2.json().get('data', {}).get('Media', {}) or {}).get('tags') or []
                source_tags = {t['name'] for t in raw if not t.get('isAdult')}
            except Exception:
                pass

        resp = _http.post(
            _ANILIST,
            json={'query': _TAG_BROWSE_QUERY, 'variables': {'tag': tag}},
            timeout=10,
        )
        items = (resp.json().get('data', {}).get('Page', {}).get('media') or [])
        result = []
        for m in items:
            if al_id and m['id'] == al_id:
                continue  # skip the source anime itself
            item_tags = {t['name'] for t in (m.get('tags') or [])}
            shared = len(source_tags & item_tags) if source_tags else 0
            result.append({
                'al_id':        m['id'],
                'mal_id':       m.get('idMal'),
                'title':        m['title'].get('english') or m['title'].get('romaji', ''),
                'title_romaji': m['title'].get('romaji', ''),
                'cover':        (m.get('coverImage') or {}).get('large') or (m.get('coverImage') or {}).get('medium', ''),
                'score':        m.get('averageScore') or 0,
                'genres':       (m.get('genres') or [])[:3],
                'format':       m.get('format', ''),
                'episodes':     m.get('episodes') or 0,
                'status':       m.get('status', ''),
                'shared':       shared,
            })
        # Most tag-overlap first, then by score
        result.sort(key=lambda x: (-x['shared'], -x['score']))
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ── MAL Interest Stacks ────────────────────────────────────────────────────────

_stacks_cache: dict = {}  # mal_id → list of stacks

_STACKS_CACHE_PATH = _Path.home() / '.animanga_stacks_cache.json'


def _load_stacks_cache():
    global _stacks_cache
    try:
        with open(_STACKS_CACHE_PATH) as f:
            _stacks_cache = {int(k): v for k, v in json.load(f).items()}
    except Exception:
        pass


def _save_stacks_cache():
    try:
        with open(_STACKS_CACHE_PATH, 'w') as f:
            json.dump({str(k): v for k, v in _stacks_cache.items()}, f)
    except Exception:
        pass


_load_stacks_cache()

# ── AniList disk cache (tags + recs) ──────────────────────────────────────────

_AL_CACHE_PATH = _Path.home() / '.animanga_cache.json'
_AL_CACHE_TTL  = 7 * 86400  # 7 days — tags/recs rarely change


def _al_cache_get(section: str, key) -> object:
    """Return cached value for (section, key) if it exists and is within TTL, else None."""
    try:
        with open(_AL_CACHE_PATH) as f:
            data = json.load(f)
        entry = (data.get(section) or {}).get(str(key))
        if entry and time.time() - entry.get('ts', 0) < _AL_CACHE_TTL:
            return entry['data']
    except Exception:
        pass
    return None


def _al_cache_set(section: str, key, value):
    """Persist a value in the AniList disk cache under (section, key)."""
    try:
        try:
            with open(_AL_CACHE_PATH) as f:
                data = json.load(f)
        except Exception:
            data = {}
        data.setdefault(section, {})[str(key)] = {'data': value, 'ts': int(time.time())}
        with open(_AL_CACHE_PATH, 'w') as f:
            json.dump(data, f)
    except Exception:
        pass


_MAL_UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'


def _scrape_stacks(mal_url: str) -> list:
    url = mal_url.rstrip('/') + '/stacks'
    resp = _http.get(url, headers={'User-Agent': _MAL_UA}, timeout=10)
    html = resp.text
    # Extract stack id + name from anchor pairs
    raw = re.findall(
        r'href="https://myanimelist\.net/stacks/(\d+)"[^>]*>\s*(.*?)\s*</a>',
        html, re.DOTALL
    )
    seen: set = set()
    stacks = []
    for sid, name in raw:
        name = re.sub(r'<[^>]+>', '', name).strip()
        if sid not in seen and name:
            seen.add(sid)
            stacks.append({
                'id':   int(sid),
                'name': name,
                'url':  f'https://myanimelist.net/stacks/{sid}',
            })
    return stacks


@anime_bp.route('/resolve_al_id')
def anime_resolve_al_id():
    """Resolve AniList ID (al_id) from a MAL ID and optionally patch the library entry."""
    mal_id = request.args.get('mal_id', type=int)
    if not mal_id:
        return jsonify({'error': 'mal_id required'}), 400
    _q = 'query($mid:Int){Media(idMal:$mid,type:ANIME){id idMal title{english romaji} coverImage{large} episodes format}}'
    try:
        resp = _http.post(_ANILIST, json={'query': _q, 'variables': {'mid': mal_id}}, timeout=8)
        m = (resp.json().get('data') or {}).get('Media') or {}
        if not m:
            return jsonify({'error': 'not found on AniList'}), 404
        al_id = m['id']
        # Patch the library entry if it exists with this mal_id but lacks al_id
        lib = _lib_read()
        for _key, entry in lib.items():
            if entry.get('mal_id') == mal_id and not entry.get('al_id'):
                entry['al_id'] = al_id
                if m.get('episodes') and not entry.get('total_episodes'):
                    entry['total_episodes'] = m['episodes']
                if m.get('format') and not entry.get('format'):
                    entry['format'] = m['format']
                t = m.get('title') or {}
                if not entry.get('title_romaji') and t.get('romaji'):
                    entry['title_romaji'] = t['romaji']
                _lib_write(lib)
                break
        return jsonify({'al_id': al_id, 'mal_id': mal_id})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@anime_bp.route('/stacks')
def anime_stacks():
    mal_id = request.args.get('mal_id', type=int)
    if not mal_id:
        return jsonify([])
    if mal_id in _stacks_cache:
        return jsonify(_stacks_cache[mal_id])
    try:
        # MAL accepts the numeric-only URL without the slug — no Jikan needed
        mal_url = f'https://myanimelist.net/anime/{mal_id}'
        stacks = _scrape_stacks(mal_url)
        _stacks_cache[mal_id] = stacks
        threading.Thread(target=_save_stacks_cache, daemon=True).start()
        return jsonify(stacks)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ── Stack browse (pure MAL scraping) ──────────────────────────────────────────

_stack_browse_cache: dict = {}  # stack_id → {stack, items}


def _slug_to_title(slug: str) -> str:
    return slug.replace('__', ': ').replace('_', ' ').strip()


@anime_bp.route('/stacks/browse/<int:stack_id>')
def browse_stack(stack_id):
    if stack_id in _stack_browse_cache:
        return jsonify(_stack_browse_cache[stack_id])
    try:
        # Use list view: exposes author personal score + per-item intro note
        resp = _http.get(
            f'https://myanimelist.net/stacks/{stack_id}?view_style=list',
            headers={'User-Agent': _MAL_UA}, timeout=12,
        )
        html = resp.text

        # ── Stack metadata ────────────────────────────────────────────────────
        og_m = re.search(r'<meta property="og:title"\s+content="([^"]+)"', html)
        stack_name = ''
        if og_m:
            stack_name = re.sub(r'\s*\|\s*MyAnimeList\.net\s*$', '', og_m.group(1)).strip()

        desc_m = re.search(r'<meta name="description"\s+content="([^"]+)"', html)
        stack_desc = _html_unescape(desc_m.group(1)) if desc_m else ''

        entries_m  = re.search(r'([\d,]+)\s+Entr(?:y|ies)', html, re.IGNORECASE)
        restacks_m = re.search(r'([\d,]+)\s+Restacks?', html, re.IGNORECASE)
        entries  = int(entries_m.group(1).replace(',', ''))  if entries_m  else 0
        restacks = int(restacks_m.group(1).replace(',', '')) if restacks_m else 0

        # ── Item blocks ───────────────────────────────────────────────────────
        raw_blocks = re.split(
            r'(?=<div[^>]+class="[^"]*seasonal-anime\s+js-seasonal-anime[^"]*")',
            html,
        )

        items: list = []
        seen: set   = set()

        for block in raw_blocks[1:]:
            # Title link and MAL id/slug (list view: class="link-title")
            link_m = re.search(
                r'href="https://myanimelist\.net/anime/(\d+)/([^"]+)"\s+class="link-title">([^<]+)</a>',
                block,
            )
            if not link_m:
                continue
            mal_id = int(link_m.group(1))
            if mal_id in seen:
                continue
            seen.add(mal_id)
            slug  = link_m.group(2)
            title = _html_unescape(link_m.group(3).strip())

            # Cover image
            cover_m = re.search(
                r'<div class="image">.*?<img[^>]+src="(https://cdn\.myanimelist\.net/images/anime/[^"]+)"',
                block, re.DOTALL,
            )
            cover = cover_m.group(1) if cover_m else ''

            # Info line: "TV, 2021,\n11 eps  <span>Me:...</span> <span>Author:...</span>"
            info_m = re.search(r'<div class="info">(.*?)</div>', block, re.DOTALL)
            type_ = year = ''
            episodes = 0
            author_score = 0
            if info_m:
                info_raw = info_m.group(1)
                # Text before first <span>: "TV, 2021,\n11 eps"
                text_part = re.split(r'<span', info_raw, maxsplit=1)[0]
                text_part = re.sub(r'\s+', ' ', text_part).strip().rstrip(',')
                tm = re.match(r'(\w+),\s*(\d{4})', text_part)
                if tm:
                    type_ = tm.group(1)
                    year  = tm.group(2)
                em = re.search(r'(\d+)\s+ep', text_part)
                if em:
                    episodes = int(em.group(1))
                # Author's personal score
                am = re.search(r'Author:<i[^>]*></i>(\d+)', info_raw)
                if am:
                    author_score = int(am.group(1))

            # Per-item note from the creator
            note_m = re.search(r'<div class="intro">\s*(.*?)\s*</div>', block, re.DOTALL)
            note = ''
            if note_m:
                raw_note = re.sub(r'<[^>]+>', '', note_m.group(1)).strip()
                note = _html_unescape(raw_note) if raw_note else ''

            items.append({
                'mal_id':       mal_id,
                'cover':        cover,
                'title':        title,
                'type':         type_,
                'year':         year,
                'episodes':     episodes,
                'author_score': author_score,
                'note':         note,
                'url':          f'https://myanimelist.net/anime/{mal_id}/{slug}',
            })

        result = {
            'stack': {
                'name':        stack_name,
                'description': stack_desc,
                'entries':     entries,
                'restacks':    restacks,
                'url':         f'https://myanimelist.net/stacks/{stack_id}',
            },
            'items': items,
        }
        _stack_browse_cache[stack_id] = result
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500
