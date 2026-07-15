import os
import re
import json
import time
import tempfile
import subprocess
import threading
import uuid
import zipfile
import urllib.request as _ur
import urllib.parse as _up
from concurrent.futures import ThreadPoolExecutor, as_completed
from flask import Blueprint, request, jsonify

from api.config_store import get_secret  # runtime-editable API keys (Ajustes)

subtitle_bp = Blueprint('subtitle', __name__)

_tasks: dict = {}
_cancel_flags: dict = {}
BATCH_SIZE = 60
OLLAMA_BATCH_SIZE = 80          # 80 lines/batch → 25% fewer round trips; fits in 3200-token ctx
# Inter-batch pause — 0 by default (max speed). Set OLLAMA_BATCH_PAUSE_SECS=3 if MPV lags.
_OLLAMA_BATCH_PAUSE = float(os.environ.get('OLLAMA_BATCH_PAUSE_SECS', '0'))
_OLLAMA_TIMEOUT = 300           # seconds per batch (generous for 60 lines)

# Translation credentials/config are resolved at call time from the config store
# (Ajustes) → env → .env, so keys saved from the UI apply without a restart.
# 'ollama' → Qwen local first, Gemini fallback if Ollama fails
# 'gemini' → Gemini first, Qwen fallback on quota exhaustion
def _gemini_key():   from api.config_store import get_secret; return get_secret('_gemini_key()')
def _ollama_url():   from api.config_store import get_secret; return get_secret('OLLAMA_URL', 'http://localhost:11434')
def _ollama_model(): from api.config_store import get_secret; return get_secret('OLLAMA_MODEL', 'qwen2.5:14b')
def _engine():       from api.config_store import get_secret; return get_secret('TRANSLATION_ENGINE', 'ollama')

# Reference counter — model is only unloaded when the LAST concurrent task finishes
_OLLAMA_TASK_COUNT = 0
_OLLAMA_TASK_LOCK  = threading.Lock()

# OpenSubtitles JWT token cache (login required for download endpoint)
_OST_TOKEN: dict = {'token': '', 'expires': 0.0}
_OST_TOKEN_LOCK   = threading.Lock()

_LANG_NAMES: dict[str, str] = {
    'eng': 'inglés',   'en':  'inglés',
    'jpn': 'japonés',  'ja':  'japonés',
    'por': 'portugués','pt':  'portugués',
    'fre': 'francés',  'fra': 'francés',   'fr': 'francés',
    'ger': 'alemán',   'deu': 'alemán',    'de': 'alemán',
    'ita': 'italiano', 'it':  'italiano',
    'kor': 'coreano',  'ko':  'coreano',
    'chi': 'chino',    'zho': 'chino',     'zh': 'chino',
}


def _build_prompt(src_lang: str = 'eng') -> str:
    """Build translation prompt for the given ISO 639-2/1 language tag."""
    lang = _LANG_NAMES.get((src_lang or 'eng').lower(), src_lang or 'inglés')
    return (
        f'Eres un traductor especializado en subtítulos de anime del {lang} al español latinoamericano neutro. '
        f'Responde ÚNICAMENTE con las líneas traducidas en el formato indicado. Sin saludos, sin explicaciones.\n\n'

        '══ REGLA CRÍTICA — LÍNEAS ══\n'
        'Cada línea de entrada produce EXACTAMENTE una línea de salida con el MISMO número N.\n'
        'Entrada: N|texto original\n'
        'Salida:  N|traducción al español\n'
        'El conteo NUNCA puede variar: si entran 60 líneas, salen 60 líneas. '
        'Agregar, omitir o partir líneas desincroniza todos los subtítulos del episodio.\n\n'

        '══ TRADUCCIÓN ══\n'
        '1. Traduce el texto del idioma fuente aplicando criterio: no toda palabra extranjera '
        '   necesita traducirse (ver sección PRÉSTAMOS más abajo).\n'
        '2. NO traduzcas (cópialos tal cual): honoríficos (-san, -kun, -chan, -sama, -senpai, '
        '   -sensei, -dono, -nii, -nee, -tan), nombres propios de personajes y lugares, '
        '   nombres de ataques/técnicas/magia, términos culturales japoneses del fandom '
        '   (isekai, kami, onii-chan, nee-san, kawaii, sugoi, nakama, daijoubu, mendokusai…).\n'
        '3. Estilo: español neutro latinoamericano, tuteo casual. '
        '   Usted solo con personajes formales o de rango superior.\n'
        '4. Registro: GRITOS→MAYÚSCULAS, arcaísmos→arcaísmos, onomatopeyas→equivalente español.\n\n'

        '══ PRÉSTAMOS Y ANGLICISMOS — cuándo NO traducir ══\n'
        'Mantén en el idioma original cuando la versión española suena forzada o pierde el matiz:\n'
        '• Jerga gaming/internet consolidada en español coloquial:\n'
        '  tier, god-tier, S-tier, op (overpowered), buff, nerf, meta, grind, boss, skill,\n'
        '  level, combo, noob, pro, hype, spoiler, flashback, plot twist, badass, savage,\n'
        '  cringe, toxic, simp, troll, spam, lag, glitch, bug, patch, DLC, raid…\n'
        '• Expresiones compuestas donde traducir una parte suena ridículo:\n'
        '  "god-tier" → queda "god-tier" (NO "dios-tier")\n'
        '  "power level" → queda "power level" si el contexto es gaming/ki/energía\n'
        '  "death flag" → queda "death flag" (es un concepto de fandom)\n'
        '  "flag" (evento narrativo) → queda "flag"\n'
        '• Términos del fandom anime globalmente usados en español:\n'
        '  waifu, husbando, tsundere, yandere, kuudere, moe, OP (opening), ED (ending),\n'
        '  filler, canon, ship, shipping, spoiler, arc, power-up, backstory…\n'
        '• Marcas, títulos, acrónimos y nombres de organizaciones — cópialos tal cual.\n'
        '• Regla de oro: si al traducir literalmente el resultado suena más raro '
        '  que el original, deja el original.\n\n'

        '══ CARACTERES INTOCABLES (copia byte a byte) ══\n'
        r'• \N            → salto de línea de subtítulo, NO lo conviertas en salto real'
        '\n'
        r'• {cualquier_cosa}  → tags ASS (\an8, \i1, \pos, \t, \move, \alpha…) — NO los toques'
        '\n'
        '• ♪ ♫ … — « »  → símbolos especiales, cópialos idénticos\n'
        '• Líneas que solo contienen ♪, puntuación o están vacías → devuélvelas sin cambios\n\n'

        '══ FORMATO DE RESPUESTA ══\n'
        '1|primera línea traducida\n'
        '2|segunda línea traducida\n'
        '…\n'
        'N|última línea traducida\n\n'
        'Nada más. Ni una palabra fuera de ese bloque.'
    )


# ── Subtitle detection & extraction ──────────────────────────────────────────

_TEXT_SUB_CODECS = {'ass', 'ssa', 'subrip', 'srt', 'webvtt', 'mov_text', 'text', 'jacosub', 'microdvd', 'realtext', 'subviewer', 'vplayer'}

def _ffprobe_tracks(path: str) -> list:
    """Return list of text subtitle stream dicts from an MKV (image-based codecs excluded)."""
    cmd = ['ffprobe', '-v', 'quiet', '-print_format', 'json',
           '-show_streams', path]
    r = subprocess.run(cmd, capture_output=True, text=True)
    try:
        data = json.loads(r.stdout or '{}')
    except json.JSONDecodeError:
        return []
    streams = data.get('streams', [])
    sub_idx = 0
    subs = []
    for s in streams:
        if s.get('codec_type') == 'subtitle':
            codec = s.get('codec_name', 'srt')
            if codec not in _TEXT_SUB_CODECS:
                sub_idx += 1  # still count it so sub_index stays correct for ffmpeg
                continue
            tags = s.get('tags', {})
            subs.append({
                'index':     s.get('index'),
                'sub_index': sub_idx,
                'codec':     codec,
                'language':  tags.get('language', 'und'),
                'title':     tags.get('title', ''),
            })
            sub_idx += 1
    return subs


# ── External subtitle search (Jimaku / OpenSubtitles / Nyaa / Subdl / Subdivx) ──
# Requires env vars:
#   JIMAKU_API_KEY        — free account at jimaku.cc (best anime source)
#   OPENSUBTITLES_API_KEY — free account at opensubtitles.com
#   SUBDL_API_KEY         — free account at subdl.com (Spanish/LatAm anime subs)

def _ext_search_jimaku(al_id: str, titles: list, episode: int) -> list:
    """Search Jimaku CC by AniList ID (only reliable search method — title search returns full catalog).
    Requires JIMAKU_API_KEY env var (free account at jimaku.cc)."""
    key = get_secret('JIMAKU_API_KEY')
    if not key:
        print('[subtitle] Jimaku: sin JIMAKU_API_KEY — saltando')
        return []
    if not al_id:
        print('[subtitle] Jimaku: sin al_id — saltando (búsqueda por título no filtra)')
        return []
    headers = {'User-Agent': 'MangaUpscaler/1.0', 'Authorization': key}
    results = []
    try:
        req = _ur.Request(f'https://jimaku.cc/api/entries/search?anilist_id={al_id}', headers=headers)
        with _ur.urlopen(req, timeout=10) as r:
            entries = json.loads(r.read())
        if not isinstance(entries, list) or not entries:
            print(f'[subtitle] Jimaku: anime al_id={al_id} no encontrado en catálogo')
            return []
        for entry in entries[:3]:
            eid = entry.get('id')
            if not eid:
                continue
            req2 = _ur.Request(
                f'https://jimaku.cc/api/entries/{eid}/files?episode={episode}',
                headers=headers,
            )
            with _ur.urlopen(req2, timeout=10) as r2:
                files = json.loads(r2.read())
            if not isinstance(files, list):
                continue
            for f in files:
                name = f.get('name', '')
                if not any(name.lower().endswith(x) for x in ('.srt', '.ass', '.ssa', '.vtt')):
                    continue
                results.append({'url': f.get('url', ''), 'language': 'eng', 'title': name, 'source': 'jimaku'})
        print(f'[subtitle] Jimaku: {len(results)} archivos encontrados')
    except Exception as e:
        print(f'[subtitle] Jimaku search error: {e}')
    return results


def _ext_search_opensubtitles(titles: list, episode: int, languages: str = '') -> list:
    """Search OpenSubtitles v3. Tries each title variant and filters by title similarity.
    Requires OPENSUBTITLES_API_KEY env var (free account at opensubtitles.com).
    Pass languages='es' to search for Spanish subtitles specifically."""
    key = get_secret('OPENSUBTITLES_API_KEY')
    if not key:
        print('[subtitle] OpenSubtitles: sin OPENSUBTITLES_API_KEY — saltando')
        return []
    results = []
    seen_ids: set = set()
    # Build set of significant words (5+ chars) from all title variants for relevance filtering
    _STOP = {'nanatsu', 'taizai', 'anime', 'episode', 'season', 'part', 'the', 'and', 'for'}
    title_words: set = set()
    for t in titles:
        if t:
            title_words.update(
                w.lower() for w in re.split(r'\W+', t)
                if len(w) >= 5 and w.lower() not in _STOP
            )

    def _is_relevant(release_name: str, feature_name: str) -> bool:
        """Return True if at least 2 significant title words appear in the result metadata."""
        if not title_words:
            return True
        combined = (release_name + ' ' + feature_name).lower()
        matched = sum(1 for w in title_words if w in combined)
        return matched >= min(2, len(title_words))

    # Bearer token required for search results — login once and reuse cached token
    token = _opensubtitles_login()
    search_headers = {
        'Api-Key': key,
        'User-Agent': 'MangaUpscaler v1.0',
        'Content-Type': 'application/json',
    }
    if token:
        search_headers['Authorization'] = f'Bearer {token}'

    for title in titles:
        if not title:
            continue
        try:
            q: dict = {'query': title}
            if episode > 0:
                q.update({'type': 'episode', 'season_number': 1, 'episode_number': episode})
            if languages:
                q['languages'] = languages
            params = _up.urlencode(q)
            req = _ur.Request(
                f'https://api.opensubtitles.com/api/v1/subtitles?{params}',
                headers=search_headers,
            )
            with _ur.urlopen(req, timeout=10) as r:
                data = json.loads(r.read())
            for item in data.get('data', [])[:10]:
                attrs = item.get('attributes', {})
                files = attrs.get('files', [])
                if not files:
                    continue
                file_id = files[0].get('file_id')
                if not file_id or file_id in seen_ids:
                    continue
                release = attrs.get('release', '')
                feature = (attrs.get('feature_details') or {}).get('movie_name', '')
                if not _is_relevant(release, feature):
                    continue
                seen_ids.add(file_id)
                results.append({
                    'file_id': file_id,
                    'language': attrs.get('language', 'eng'),
                    'title': release or feature,
                    'source': 'opensubtitles',
                })
        except Exception as e:
            print(f'[subtitle] OpenSubtitles search error (title="{title}"): {e}')
    lang_label = f' [{languages}]' if languages else ''
    print(f'[subtitle] OpenSubtitles{lang_label}: {len(results)} encontrados')
    return results


def _ext_search_nyaa(titles: list, episode: int) -> list:
    """Search Nyaa.si RSS for subtitle-only releases (.srt/.ass standalone files).
    No API key needed — only finds results when a group uploaded subs separately."""
    results = []
    seen: set = set()
    for title in titles:
        if not title:
            continue
        try:
            # Category 3_0 = Anime English-translated; search for subtitle-only releases
            query = f'{title} {episode:02d} subtitle'
            params = _up.urlencode({'page': 'rss', 'q': query, 'c': '3_0', 'f': '0'})
            req = _ur.Request(f'https://nyaa.si/?{params}', headers={'User-Agent': 'Mozilla/5.0'})
            with _ur.urlopen(req, timeout=10) as r:
                xml = r.read().decode('utf-8', errors='replace')
            # Parse RSS items
            for m in re.finditer(r'<item>(.*?)</item>', xml, re.DOTALL):
                item_xml = m.group(1)
                item_title = (re.findall(r'<title>([^<]+)</title>', item_xml) or [''])[0]
                item_link  = (re.findall(r'<link>([^<]+)</link>', item_xml) or [''])[0]
                item_lower = item_title.lower()
                # Only include if the release title contains subtitle-related keywords
                if not any(kw in item_lower for kw in ('sub', 'srt', 'ass', 'subtitle')):
                    continue
                if item_link in seen:
                    continue
                seen.add(item_link)
                results.append({
                    'url': item_link,  # torrent URL — user must download and extract
                    'language': 'eng',
                    'title': item_title,
                    'source': 'nyaa',
                })
        except Exception as e:
            print(f'[subtitle] Nyaa search error (title="{title}"): {e}')
    print(f'[subtitle] Nyaa: {len(results)} encontrados')
    return results


def _ext_search_subdl(titles: list, episode: int, season: int = 1) -> list:
    """Search Subdl.com for Spanish/LatAm subtitles. Requires SUBDL_API_KEY env var.
    Free account at subdl.com — returns ZIP archives containing .srt/.ass files."""
    key = get_secret('SUBDL_API_KEY')
    if not key:
        print('[subtitle] Subdl: sin SUBDL_API_KEY — saltando')
        return []
    results = []
    seen_ids: set = set()
    for title in titles[:2]:
        if not title:
            continue
        try:
            # Try Spanish (Latin America) first, then general Spanish
            for lang in ('SL', 'ES'):
                q: dict = {
                    'api_key':   key,
                    'film_name': title,
                    'languages': lang,
                }
                if episode > 0:
                    q.update({'type': 'tv', 'season_number': season, 'episode_number': episode})
                params = _up.urlencode(q)
                req = _ur.Request(
                    f'https://api.subdl.com/api/v1/subtitles?{params}',
                    headers={'User-Agent': 'MangaUpscaler/1.0'},
                )
                with _ur.urlopen(req, timeout=10) as r:
                    data = json.loads(r.read())
                if not data.get('status'):
                    continue
                for s in (data.get('subtitles') or [])[:6]:
                    dl_url = s.get('url') or s.get('download_link', '')
                    if not dl_url:
                        continue
                    uid = dl_url  # use URL as dedup key
                    if uid in seen_ids:
                        continue
                    seen_ids.add(uid)
                    results.append({
                        'url':      dl_url,
                        'language': 'es',
                        'title':    s.get('release_name') or s.get('name', title),
                        'source':   'subdl',
                        'direct':   True,
                    })
        except Exception as e:
            print(f'[subtitle] Subdl search error (title="{title}"): {e}')
    print(f'[subtitle] Subdl: {len(results)} encontrados')
    return results


def _ext_search_subdivx(titles: list, episode: int) -> list:
    """Search Subdivx.com for Spanish LatAm subtitles. No API key required.
    Best source for Latin American fansub groups."""
    results = []
    seen_ids: set = set()
    ep_variants = {str(episode), f'{episode:02d}', f'{episode:03d}'}
    for title in titles[:2]:
        if not title:
            continue
        try:
            params = _up.urlencode({'q': title, 't': '1', 'pg': '1'})
            req = _ur.Request(
                f'https://www.subdivx.com/api/search?{params}',
                headers={
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)',
                    'X-Requested-With': 'XMLHttpRequest',
                    'Accept': 'application/json',
                },
            )
            with _ur.urlopen(req, timeout=12) as r:
                data = json.loads(r.read())
            for item in (data.get('datos') or [])[:8]:
                item_id = item.get('id')
                if not item_id or item_id in seen_ids:
                    continue
                # Filter to entries that mention the episode number
                text = f"{item.get('titulo','')} {item.get('descripcion','')}".lower()
                if not any(ev in text for ev in ep_variants):
                    # Broad match: still keep if title matches well enough
                    title_words = [w for w in re.split(r'\W+', title.lower()) if len(w) >= 4]
                    if not title_words or sum(1 for w in title_words if w in text) < max(1, len(title_words) // 2):
                        continue
                seen_ids.add(item_id)
                results.append({
                    'subdivx_id': item_id,
                    'language':   'es',
                    'title':      item.get('titulo', title),
                    'source':     'subdivx',
                    'direct':     True,
                })
        except Exception as e:
            print(f'[subtitle] Subdivx search error (title="{title}"): {e}')
    print(f'[subtitle] Subdivx: {len(results)} encontrados')
    return results


def _extract_sub_from_zip(zip_path: str, tmpdir: str) -> str:
    """Extract first .srt/.ass/.ssa/.vtt from a zip archive. Returns path to extracted file."""
    try:
        with zipfile.ZipFile(zip_path, 'r') as zf:
            # Prefer .ass over .srt for better styling
            names = zf.namelist()
            for ext_pref in ('.ass', '.ssa', '.srt', '.vtt'):
                for name in names:
                    if name.lower().endswith(ext_pref) and not name.startswith('__MACOSX'):
                        out = os.path.join(tmpdir, f'extracted{ext_pref}')
                        with zf.open(name) as src, open(out, 'wb') as dst:
                            dst.write(src.read())
                        return out
    except zipfile.BadZipFile:
        pass
    raise RuntimeError('No se encontró archivo de subtítulo (.srt/.ass) en el paquete descargado')


def _opensubtitles_login() -> str:
    """Login to OpenSubtitles and return JWT token. Caches for 23h.
    Requires OPENSUBTITLES_USERNAME + OPENSUBTITLES_PASSWORD + OPENSUBTITLES_API_KEY."""
    import time
    username = get_secret('OPENSUBTITLES_USERNAME')
    password = get_secret('OPENSUBTITLES_PASSWORD')
    key      = get_secret('OPENSUBTITLES_API_KEY')
    if not (username and password and key):
        return ''
    with _OST_TOKEN_LOCK:
        if _OST_TOKEN['token'] and time.time() < _OST_TOKEN['expires']:
            return _OST_TOKEN['token']
    payload = json.dumps({'username': username, 'password': password}).encode()
    req = _ur.Request(
        'https://api.opensubtitles.com/api/v1/login',
        data=payload,
        headers={'Api-Key': key, 'User-Agent': 'MangaUpscaler v1.0',
                 'Content-Type': 'application/json'},
        method='POST',
    )
    try:
        with _ur.urlopen(req, timeout=15) as r:
            data = json.loads(r.read())
        token = data.get('token', '')
        if token:
            with _OST_TOKEN_LOCK:
                _OST_TOKEN['token']   = token
                _OST_TOKEN['expires'] = time.time() + 23 * 3600
            print('[subtitle] OpenSubtitles: login OK')
        return token
    except Exception as e:
        print(f'[subtitle] OpenSubtitles login error: {e}')
        return ''


def _ext_download_sub(sub_info: dict, tmpdir: str) -> str:
    """Download an external subtitle to tmpdir. Returns local file path."""
    source = sub_info.get('source', '')

    if source == 'opensubtitles':
        key   = get_secret('OPENSUBTITLES_API_KEY')
        token = _opensubtitles_login()
        headers = {
            'Api-Key':      key,
            'Authorization': f'Bearer {token}' if token else '',
            'User-Agent':   'MangaUpscaler v1.0',
            'Content-Type': 'application/json',
            'Accept':       'application/json',   # prevents CDN returning HTML error pages
        }
        if not token:
            del headers['Authorization']
        payload = json.dumps({'file_id': int(sub_info['file_id'])}).encode()

        def _ost_download_request():
            req = _ur.Request(
                'https://api.opensubtitles.com/api/v1/download',
                data=payload, headers=headers, method='POST',
            )
            try:
                with _ur.urlopen(req, timeout=15) as r:
                    return json.loads(r.read())
            except _ur.HTTPError as e:
                body = ''
                try: body = e.read().decode('utf-8', errors='replace')[:200]
                except Exception: pass
                if e.code == 406:
                    raise RuntimeError('Cuota diaria agotada (20/día cuenta gratuita)') from e
                if e.code == 401:
                    raise RuntimeError('Token expirado o credenciales inválidas') from e
                # 503 suele ser transitorio — el caller reintentará
                raise _ur.HTTPError(e.url, e.code, e.reason, e.headers, None) from e

        try:
            dl_data = _ost_download_request()
        except _ur.HTTPError as e:
            if e.code == 503:
                print('[subtitle] OpenSubtitles 503 — reintentando en 3s…')
                time.sleep(3)
                dl_data = _ost_download_request()
            else:
                raise RuntimeError(f'OpenSubtitles error {e.code}') from e

        url = dl_data.get('link', '')
        if not url:
            remaining = dl_data.get('remaining', '?')
            raise RuntimeError(f'OpenSubtitles no devolvió URL (descargas restantes: {remaining})')
    elif source == 'subdl':
        # Subdl distributes subtitles as ZIP archives
        dl_url = sub_info.get('url', '')
        if not dl_url:
            raise RuntimeError('Subdl: sin URL de descarga')
        if not dl_url.startswith('http'):
            dl_url = f'https://dl.subdl.com{dl_url}'
        req = _ur.Request(dl_url, headers={'User-Agent': 'MangaUpscaler/1.0'})
        zip_path = os.path.join(tmpdir, 'subdl.zip')
        with _ur.urlopen(req, timeout=30) as r:
            with open(zip_path, 'wb') as f:
                f.write(r.read())
        return _extract_sub_from_zip(zip_path, tmpdir)

    elif source == 'subdivx':
        # Subdivx: first fetch the file list for this entry, then download the archive
        subdivx_id = sub_info.get('subdivx_id')
        if not subdivx_id:
            raise RuntimeError('Subdivx: sin ID de entrada')
        # Get downloadable files for this entry
        files_req = _ur.Request(
            f'https://www.subdivx.com/api/files/{subdivx_id}',
            headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)',
                'X-Requested-With': 'XMLHttpRequest',
                'Accept': 'application/json',
            },
        )
        with _ur.urlopen(files_req, timeout=12) as r:
            files_data = json.loads(r.read())
        file_url = None
        for f in (files_data if isinstance(files_data, list) else []):
            u = f.get('url', '')
            if u:
                file_url = u
                break
        if not file_url:
            raise RuntimeError('Subdivx: no se encontraron archivos descargables para esta entrada')
        dl_req = _ur.Request(
            file_url,
            headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)', 'Referer': 'https://www.subdivx.com/'},
        )
        archive_path = os.path.join(tmpdir, 'subdivx_dl.zip')
        with _ur.urlopen(dl_req, timeout=30) as r:
            with open(archive_path, 'wb') as f:
                f.write(r.read())
        return _extract_sub_from_zip(archive_path, tmpdir)

    elif source == 'nyaa':
        raise RuntimeError('Nyaa devuelve torrents, no archivos directos. Descarga el torrent manualmente y extrae el .srt/.ass.')
    else:
        url = sub_info.get('url', '')
        if not url:
            raise RuntimeError(f'No hay URL para descargar de {source}')

    ext = os.path.splitext(url.split('?')[0])[1].lower()
    if ext not in ('.srt', '.ass', '.ssa', '.vtt'):
        ext = '.srt'
    out_path = os.path.join(tmpdir, f'external{ext}')
    req = _ur.Request(url, headers={'User-Agent': 'MangaUpscaler/1.0'})
    with _ur.urlopen(req, timeout=30) as r:
        with open(out_path, 'wb') as f:
            f.write(r.read())
    return out_path


def _ext_find_subs(al_id: str, titles: list, episode: int) -> list:
    """Try all external subtitle sources. `titles` = [english_title, romaji_title, ...]."""
    seen_t: set = set()
    clean_titles = []
    for t in titles:
        if t and t.lower() not in seen_t:
            seen_t.add(t.lower())
            clean_titles.append(t)
    print(f'[subtitle] Buscando subs externos: al_id={al_id}, titles={clean_titles}, ep={episode}')
    results = []
    results.extend(_ext_search_jimaku(al_id, clean_titles, episode))
    results.extend(_ext_search_opensubtitles(clean_titles, episode))
    results.extend(_ext_search_nyaa(clean_titles, episode))
    print(f'[subtitle] Total subs externos encontrados: {len(results)}')
    return results


def _ext_find_spanish_subs(titles: list, episode: int) -> list:
    """Search all sources for pre-made Spanish subtitles (direct inject, no LLM).
    Order: OpenSubtitles ES → Subdl LatAm → Subdivx (best LatAm fansub coverage)."""
    seen_t: set = set()
    clean_titles = []
    for t in titles:
        if t and t.lower() not in seen_t:
            seen_t.add(t.lower())
            clean_titles.append(t)
    print(f'[subtitle] Buscando subs en español: titles={clean_titles}, ep={episode}')
    results = []
    results.extend(_ext_search_opensubtitles(clean_titles, episode, languages='es'))
    results.extend(_ext_search_subdl(clean_titles, episode))
    results.extend(_ext_search_subdivx(clean_titles, episode))
    # Mark all as direct Spanish (no translation needed)
    for r in results:
        r['direct'] = True
    print(f'[subtitle] Subs en español encontrados: {len(results)} '
          f'(OST={sum(1 for r in results if r["source"]=="opensubtitles")}, '
          f'Subdl={sum(1 for r in results if r["source"]=="subdl")}, '
          f'Subdivx={sum(1 for r in results if r["source"]=="subdivx")})')
    return results


def _extract_sub(mkv_path: str, sub_index: int, codec: str, tmpdir: str) -> str:
    """Extract subtitle track to a temp file. Returns path to the file."""
    ext = '.ass' if codec in ('ass', 'ssa') else '.srt'
    out = os.path.join(tmpdir, f'sub{ext}')
    cmd = ['ffmpeg', '-y', '-i', mkv_path, '-map', f'0:s:{sub_index}', out]
    result = subprocess.run(cmd, capture_output=True)
    if result.returncode != 0:
        stderr = result.stderr.decode('utf-8', errors='replace')
        if 'image' in stderr.lower() or 'pgs' in stderr.lower() or 'dvdsub' in stderr.lower():
            raise RuntimeError('El archivo tiene subtítulos de imagen (PGS/VOBSUB) que no se pueden traducir')
        raise RuntimeError(f'No se pudo extraer el subtítulo. El archivo puede no tener pistas de texto.')
    return out


def _es_marker_path(video_path: str) -> str:
    """Ruta del marcador persistente '.es_injected' junto al vídeo. Su sola existencia
    indica que ESTE reproductor inyectó subtítulos en español (no aplica a los que ya
    venían en español dentro del contenedor)."""
    return os.path.splitext(video_path)[0] + '.es_injected'


def _mark_es_injected(video_path: str) -> None:
    try:
        with open(_es_marker_path(video_path), 'w') as f:
            f.write('1')
    except OSError:
        pass


def _save_sub_external(video_path: str, sub_path: str) -> str:
    """Save subtitle as a sidecar file next to the video (same basename, .srt/.ass).
    MPV auto-loads subtitle files that match the video filename.
    Used for non-MKV formats where mkvmerge cannot embed."""
    base = os.path.splitext(video_path)[0]
    ext = os.path.splitext(sub_path)[1].lower() or '.srt'
    # Use language suffix so MPV picks it up: video.spa.srt
    out = f'{base}.spa{ext}'
    import shutil
    shutil.copy2(sub_path, out)
    _mark_es_injected(video_path)
    return out


def _inject_sub(mkv_path: str, sub_path: str, n_existing_subs: int) -> str:
    """Insert translated subtitle into the original MKV in-place using mkvmerge.
    For non-MKV files, saves a sidecar subtitle file instead (MPV auto-loads it).
    mkvmerge writes proper cue entries (index) so MPV can seek all subtitle packets.
    ffmpeg -c copy omits the cue index, causing MPV to only read the first packet.
    """
    if not mkv_path.lower().endswith('.mkv'):
        return _save_sub_external(mkv_path, sub_path)
    tmp_out = mkv_path + '._tmp_spa.mkv'

    # Identify tracks via mkvmerge; collect subtitle track IDs to exclude existing spa ones
    id_result = subprocess.run(
        ['mkvmerge', '--identify', '--identification-format', 'json', mkv_path],
        capture_output=True, text=True, env={**os.environ, 'LC_ALL': 'C.UTF-8'},
    )
    id_data = json.loads(id_result.stdout or '{}')
    tracks = id_data.get('tracks', [])

    keep_audio_ids = [str(t['id']) for t in tracks if t.get('type') == 'audio']
    keep_video_ids = [str(t['id']) for t in tracks if t.get('type') == 'video']
    # Keep existing subtitle tracks except any prior Spanish track (we replace it)
    keep_sub_ids   = [str(t['id']) for t in tracks
                      if t.get('type') == 'subtitles'
                      and t.get('properties', {}).get('language', '') not in ('spa', 'es')]

    cmd = ['mkvmerge', '-o', tmp_out]
    if keep_video_ids:
        cmd += ['--video-tracks', ','.join(keep_video_ids)]
    if keep_audio_ids:
        cmd += ['--audio-tracks', ','.join(keep_audio_ids)]
    if keep_sub_ids:
        cmd += ['--subtitle-tracks', ','.join(keep_sub_ids)]
    else:
        cmd += ['--no-subtitles']
    cmd += [mkv_path]
    # New Spanish subtitle (appended after existing tracks)
    cmd += [
        '--language', '0:spa',
        '--track-name', '0:Español',
        '--default-track', '0:yes',
        sub_path,
    ]

    result = subprocess.run(cmd, capture_output=True, text=True,
                            env={**os.environ, 'LC_ALL': 'C.UTF-8'})
    if result.returncode not in (0, 1):   # mkvmerge returns 1 for warnings (ok)
        if os.path.exists(tmp_out):
            os.remove(tmp_out)
        raise RuntimeError(f'mkvmerge inject failed: {result.stderr[-500:]}')

    os.replace(tmp_out, mkv_path)
    _mark_es_injected(mkv_path)
    return mkv_path


# ── SRT parsing ───────────────────────────────────────────────────────────────

def _parse_srt(content: str) -> list:
    """Parse SRT → list of {idx, timestamp, text} blocks. Handles LF and CRLF."""
    blocks = []
    # Normalize CRLF to LF before splitting
    content = content.replace('\r\n', '\n').replace('\r', '\n')
    for block in re.split(r'\n{2,}', content.strip()):
        lines = block.strip().splitlines()
        if len(lines) < 3:
            continue
        # Strip HTML tags; join multi-line text with \N so each block fits on one "NUMBER|text" line for Gemini
        text_lines = lines[2:]
        text = r'\N'.join(l.strip() for l in text_lines)
        text = re.sub(r'<[^>]+>', '', text).strip()
        blocks.append({
            'idx':       lines[0].strip(),
            'timestamp': lines[1].strip(),
            'text':      text,
        })
    return blocks


def _rebuild_srt(blocks: list, translated: list) -> str:
    parts = []
    for b, t in zip(blocks, translated):
        # \N was used as a single-line separator for Gemini; restore real newlines for SRT output
        text = (t or b['text']).replace(r'\N', '\n')
        parts.append(f"{b['idx']}\n{b['timestamp']}\n{text}\n")
    return '\n'.join(parts)


# ── ASS parsing ───────────────────────────────────────────────────────────────

def _parse_ass(content: str):
    """
    Split ASS into header lines and a list of event records.
    Each event: {prefix, text, passthrough}
      passthrough=True  → non-Dialogue line, copy as-is
      passthrough=False → Dialogue line; 'text' is the full text WITH ASS tags
                          (Gemini is instructed to preserve {…} blocks)
    """
    lines = content.splitlines(keepends=True)
    header = []
    events = []
    in_events = False
    text_col = 9  # default for standard ASS (Layer…Effect,Text)

    for line in lines:
        stripped = line.rstrip('\n\r')
        low = stripped.lower().strip()

        if low == '[events]':
            in_events = True
            header.append(line)
            continue

        if not in_events:
            header.append(line)
            continue

        if low.startswith('format:'):
            cols = [c.strip() for c in stripped.split(':', 1)[1].split(',')]
            if 'Text' in cols:
                text_col = cols.index('Text')
            header.append(line)
            continue

        if low.startswith('dialogue:'):
            parts = stripped.split(',', text_col)
            if len(parts) > text_col:
                prefix = ','.join(parts[:text_col]) + ','
                text   = ','.join(parts[text_col:])
                events.append({
                    'passthrough': False,
                    'prefix':      prefix,
                    'text':        text,   # full text WITH tags — Gemini preserves {…}
                    'eol':         '\n' if line.endswith('\n') else '',
                })
            else:
                events.append({'passthrough': True, 'raw': line})
        else:
            events.append({'passthrough': True, 'raw': line})

    return header, events


def _rebuild_ass(header: list, events: list, translated: list) -> str:
    out = ''.join(header)
    t_iter = iter(translated)
    for ev in events:
        if ev['passthrough']:
            out += ev['raw']
        else:
            t = next(t_iter, ev['text'])
            out += ev['prefix'] + (t or ev['text']) + ev['eol']
    return out


# ── Gemini translation ────────────────────────────────────────────────────────

def _translate_batch(texts: list, client, model_name: str, src_lang: str = 'eng') -> list:
    """Translate a list of strings via Gemini. Returns same-length list."""
    if not texts:
        return []

    numbered = '\n'.join(f'{i+1}|{t}' for i, t in enumerate(texts))
    prompt = f'{_build_prompt(src_lang)}\n\nTraduce estas {len(texts)} líneas:\n\n{numbered}'

    for attempt in range(3):
        try:
            response = client.models.generate_content(model=model_name, contents=prompt)
            raw = (response.text or '').strip()
            break
        except Exception as e:
            err = str(e)
            if '429' in err or 'RESOURCE_EXHAUSTED' in err:
                if 'limit: 0' in err:
                    raise RuntimeError(
                        'Cuota Gemini es 0 — activa la API en tu proyecto: '
                        'console.cloud.google.com → APIs & Services → Library → '
                        '"Generative Language API" → Enable. '
                        'O crea una nueva key en aistudio.google.com con "Create API key in new project".'
                    ) from e
                delay = re.search(r'(\d+)s', err)
                wait = int(delay.group(1)) + 2 if delay else (15 * (attempt + 1))
                print(f'[subtitle] 429 on {model_name}, waiting {wait}s (attempt {attempt+1})')
                time.sleep(wait)
                if attempt == 2:
                    raise
            elif '400' in err or 'BAD_REQUEST' in err:
                raise RuntimeError(f'Bad request al modelo {model_name}: {err[:200]}') from e
            elif '404' in err or 'NOT_FOUND' in err:
                raise RuntimeError(f'Modelo {model_name} no disponible') from e
            else:
                raise
    else:
        return list(texts)

    result = list(texts)
    for line in raw.splitlines():
        m = re.match(r'^(\d+)\|(.*)$', line)
        if m:
            idx = int(m.group(1)) - 1
            if 0 <= idx < len(texts):
                result[idx] = m.group(2)
    return result


# ── Main worker ───────────────────────────────────────────────────────────────

def _resolve_video_path(info_hash: str, episode: int, anime_id: str, local_path: str = '') -> str | None:
    """Resolve MKV path via local_path or qBittorrent."""
    try:
        from api.anime import _q, _find_video, _lib_read
        if local_path:
            return _find_video(local_path, episode)
        # Fallback: check library for local_path
        if anime_id:
            lib = _lib_read()
            lp = (lib.get(anime_id) or {}).get('local_path', '')
            if lp:
                return _find_video(lp, episode)
        torrents = _q('get', '/torrents/info', params={'hashes': info_hash}).json()
        if not torrents and anime_id:
            lib = _lib_read()
            batch_hash = (lib.get(anime_id) or {}).get('episodes', {}).get('0', {}).get('info_hash', '')
            if batch_hash and batch_hash != info_hash:
                torrents = _q('get', '/torrents/info', params={'hashes': batch_hash}).json()
        if not torrents:
            return None
        return _find_video(torrents[0].get('content_path', ''), episode)
    except Exception as e:
        print(f'[subtitle] resolve path error: {e}')
        return None


# Best free-tier models ordered by RPD (requests/day): flash-lite ~1500, flash ~250
# Models ordered by free-tier RPD (highest first):
# gemma-4-26b-a4b-it  ~1500 RPD  (MoE: 26B total / 4B active, instruction-tuned)
# gemma-4-31b-it      ~1500 RPD
# gemini-2.5-flash-lite ~1000 RPD
# gemini-2.5-flash      ~500 RPD
_MODELS = [
    'models/gemma-4-26b-a4b-it',
    'models/gemma-4-31b-it',
    'models/gemini-2.5-flash-lite',
    'models/gemini-2.5-flash',
    'models/gemini-flash-lite-latest',
]
_PARALLEL_WORKERS = 6  # concurrent batch requests


def _translate_batch_with_fallback(texts: list, client, src_lang: str = 'eng') -> list:
    """Try each model in order; return translated list."""
    last_err = None
    for mn in _MODELS:
        try:
            return _translate_batch(texts, client, mn, src_lang)
        except RuntimeError as e:
            if 'Cuota Gemini es 0' in str(e):
                raise
            last_err = e
            print(f'[subtitle] {mn} failed: {e} — trying next model')
    raise last_err or RuntimeError('Todos los modelos fallaron')


# ── Ollama / local-model translation ─────────────────────────────────────────

def _is_quota_error(exc: Exception) -> bool:
    err = str(exc)
    return ('429' in err or 'RESOURCE_EXHAUSTED' in err
            or 'quota' in err.lower() or 'exhausted' in err.lower())


def _ollama_available() -> bool:
    import urllib.request as _ur
    try:
        _ur.urlopen(f'{_ollama_url()}/', timeout=3)
        return True
    except Exception:
        return False


def _ollama_preload():
    """Load model into VRAM once; keep alive 30 min. May take 30-60 s on first call."""
    import urllib.request as _ur
    if not os.environ.get('OLLAMA_FLASH_ATTENTION'):
        print('[subtitle] TIP: inicia Ollama con OLLAMA_FLASH_ATTENTION=1 para ~20-30% más velocidad')
    payload = json.dumps({
        'model': _ollama_model(),
        'messages': [{'role': 'user', 'content': '1|ok'}],
        'stream': False,
        'keep_alive': '30m',
        'options': {'num_predict': 3},
    }).encode()
    req = _ur.Request(f'{_ollama_url()}/api/chat', data=payload,
                      headers={'Content-Type': 'application/json'}, method='POST')
    try:
        _ur.urlopen(req, timeout=90)
    except Exception as e:
        raise RuntimeError(f'No se pudo cargar {_ollama_model()} en VRAM: {e}') from e


def _ollama_unload():
    """Release model from VRAM immediately.
    Must use /api/chat (same endpoint as preload) — /api/generate uses a separate
    context slot in Ollama and won't evict a model loaded via chat."""
    import urllib.request as _ur
    try:
        payload = json.dumps({
            'model': _ollama_model(),
            'messages': [],
            'keep_alive': 0,
        }).encode()
        req = _ur.Request(f'{_ollama_url()}/api/chat', data=payload,
                          headers={'Content-Type': 'application/json'}, method='POST')
        _ur.urlopen(req, timeout=15)
        print(f'[subtitle] {_ollama_model()} descargado de VRAM')
    except Exception as e:
        print(f'[subtitle] _ollama_unload error: {e}')


def _translate_batch_ollama(texts: list, src_lang: str = 'eng') -> list:
    """Translate a batch via local Ollama. Retries once if model returns < 85% of lines.

    Uses system/user message split so Ollama reuses the KV cache for the instruction
    prompt across all batches of the same job (only the user portion changes).
    """
    if not texts:
        return []
    import urllib.request as _ur

    n = len(texts)
    # system ~600t (extended prompt) + user header ~20t + n lines ~14t input + ~14t output each
    num_ctx = max(3600, 600 + (n * 30))
    # ~18 tokens/line output; +25% headroom so trailing lines never get cut off
    n_predict = min(int(n * 23), 2800)

    system_prompt = _build_prompt(src_lang)

    def _call() -> tuple[list, int]:
        numbered = '\n'.join(f'{i+1}|{t}' for i, t in enumerate(texts))
        payload = json.dumps({
            'model': _ollama_model(),
            'messages': [
                {'role': 'system', 'content': system_prompt},
                {'role': 'user',   'content': f'Traduce estas {n} líneas:\n\n{numbered}'},
            ],
            'stream': False,
            'keep_alive': '30m',
            'options': {
                'temperature': 0.01,    # casi greedy; temperatura 0 causa output vacío en Qwen
                'num_predict': n_predict,
                'num_ctx':     num_ctx,
                'num_gpu':     -1,      # all layers on GPU
                'f16_kv':      True,    # FP16 KV cache: ~2x less VRAM, faster attention
            },
        }).encode()
        req = _ur.Request(f'{_ollama_url()}/api/chat', data=payload,
                          headers={'Content-Type': 'application/json'}, method='POST')
        try:
            with _ur.urlopen(req, timeout=_OLLAMA_TIMEOUT) as resp:
                data = json.loads(resp.read())
        except Exception as e:
            raise RuntimeError(f'Ollama: {e}') from e
        raw = ((data.get('message') or {}).get('content') or '').strip()
        result = list(texts)
        found = 0
        for line in raw.splitlines():
            m = re.match(r'^(\d+)\|(.*)$', line)
            if m:
                idx = int(m.group(1)) - 1
                if 0 <= idx < n:
                    result[idx] = m.group(2)
                    found += 1
        return result, found

    result, found = _call()
    if found < n * 0.85:
        print(f'[subtitle] Ollama returned {found}/{n} lines — retrying batch…')
        result2, found2 = _call()
        if found2 > found:
            result, found = result2, found2
        if found == 0:
            # Log raw response to help diagnose model format failures
            numbered = '\n'.join(f'{i+1}|{t}' for i, t in enumerate(texts))
            payload_debug = json.dumps({
                'model': _ollama_model(),
                'messages': [
                    {'role': 'system', 'content': system_prompt},
                    {'role': 'user',   'content': f'Traduce estas {n} líneas:\n\n{numbered}'},
                ],
                'stream': False,
                'options': {'temperature': 0.01, 'num_predict': 200, 'num_ctx': num_ctx, 'num_gpu': -1},
            }).encode()
            try:
                req2 = _ur.Request(f'{_ollama_url()}/api/chat', data=payload_debug,
                                   headers={'Content-Type': 'application/json'}, method='POST')
                with _ur.urlopen(req2, timeout=30) as resp2:
                    debug_raw = ((json.loads(resp2.read()).get('message') or {}).get('content') or '').strip()
                print(f'[subtitle] Ollama debug raw (primeras 300 chars): {debug_raw[:300]!r}')
            except Exception:
                pass

    # Warn if result lines are identical to input (translation may not have happened)
    unchanged = sum(1 for orig, tr in zip(texts, result) if orig.strip() and orig == tr)
    if unchanged > n * 0.15:
        print(f'[subtitle] Ollama warning: {unchanged}/{n} lines sin cambio — posible fallo de traducción')

    return result


def _do_translate(task_id: str, mkv_path: str, sub_index: int, codec: str, n_subs: int,
                  src_lang: str = 'eng', external_sub_info: dict = None):
    global _OLLAMA_TASK_COUNT
    task = _tasks[task_id]
    _cancel_flags.setdefault(task_id, False)
    done_count  = threading.Lock()
    ollama_used = [False]

    def upd(status, progress, message, engine=None):
        patch = dict(status=status, progress=progress, message=message)
        if engine:
            patch['engine'] = engine
            task['engine']  = engine
        task.update(patch)

    def _cancelled_now():
        return bool(_cancel_flags.get(task_id))

    try:
        if external_sub_info:
            source_label = external_sub_info.get('source', 'fuente externa')
            upd('extracting', 5, f'Descargando subtítulos de {source_label}…')
        else:
            upd('extracting', 5, 'Extrayendo subtítulos del MKV…')

        with tempfile.TemporaryDirectory() as tmpdir:
            if external_sub_info:
                sub_file = _ext_download_sub(external_sub_info, tmpdir)
                ext = os.path.splitext(sub_file)[1].lower()
                codec = 'ass' if ext in ('.ass', '.ssa') else 'subrip'
            else:
                sub_file = _extract_sub(mkv_path, sub_index, codec, tmpdir)
            with open(sub_file, encoding='utf-8', errors='replace') as f:
                content = f.read()

            is_ass = codec in ('ass', 'ssa')
            if is_ass:
                header, events = _parse_ass(content)
                dialogue = [e for e in events if not e['passthrough']]
                texts = [e['text'] for e in dialogue]
            else:
                blocks = _parse_srt(content)
                texts = [b['text'] for b in blocks]

            texts_orig = texts
            total_orig = len(texts_orig)
            if total_orig == 0:
                raise RuntimeError('El archivo de subtítulos no tiene líneas de diálogo')

            # ── Dedup + passthrough pre-filter ────────────────────────────────
            # Fansub ASS files (e.g. EMBER) embed karaoke animations: the same lyric line
            # repeats hundreds of times with different \clip() / \c&H...& tags per frame.
            # Dedup key = VISIBLE TEXT (all {…} tags stripped) so those frames collapse to
            # one translation. Duplicates are rebuilt with their original leading tags +
            # the translated visible text, preserving each frame's animation tags.
            _TAG_RE_D = re.compile(r'\{[^}]*\}')
            _SKIP_RE_D = re.compile(r'^[\s♪♫…\-_.,!?¡¿:;\'\"()【】「」『』\[\]°·•—–]+$')
            _LEAD_TAG_RE = re.compile(r'^(\{[^}]*\})+')  # leading tag block(s)
            _seen_vis: dict[str, int] = {}   # visible_text_key → dedup index
            _dedup_texts: list[str] = []     # representative full text (first occurrence)
            _dedup_to_orig: list[list[int]] = []

            for _p, _t in enumerate(texts_orig):
                _vis = _TAG_RE_D.sub('', _t).strip()
                _vis_key = _vis.replace('\\N', ' ').strip()
                if not _vis_key or _SKIP_RE_D.match(_vis_key):
                    continue  # passthrough — rebuild fallback handles it
                if _vis_key in _seen_vis:
                    _dedup_to_orig[_seen_vis[_vis_key]].append(_p)
                else:
                    _idx = len(_dedup_texts)
                    _seen_vis[_vis_key] = _idx
                    _dedup_texts.append(_t)   # full text of first occurrence
                    _dedup_to_orig.append([_p])

            texts = _dedup_texts
            total = len(texts)
            _n_saved = total_orig - total
            if _n_saved > 0:
                print(f'[subtitle] Dedup: {total_orig} → {total} líneas únicas ({_n_saved} omitidas/duplicadas)')

            # dedup_tm: keyed by dedup index (0..total-1)
            # Expanded to original positions at the end before rebuild.
            dedup_tm: dict[int, str] = {}

            # ── Phase 1: Gemini (parallel) — only when engine != 'ollama' ────────
            key = _gemini_key() or os.environ.get('_gemini_key()', '')
            quota_hit  = False
            _cancelled = False

            if key and _engine() != 'ollama':
                g_batches = [texts[i:i + BATCH_SIZE] for i in range(0, total, BATCH_SIZE)]
                n_g = len(g_batches)
                try:
                    from google import genai
                    client = genai.Client(api_key=key)
                except Exception as ie:
                    print(f'[subtitle] Gemini import error: {ie}')
                    client = None

                if client:
                    completed_g = [0]
                    _prog_label = f'{total} únicas' if _n_saved > 0 else str(total)
                    upd('translating', 10,
                        f'Traduciendo {_prog_label} líneas con Gemini…', engine='gemini')
                    pool = ThreadPoolExecutor(max_workers=min(_PARALLEL_WORKERS, n_g))
                    try:
                        futures = {
                            pool.submit(_translate_batch_with_fallback, batch, client, src_lang): (idx, i_start)
                            for idx, (i_start, batch) in enumerate(
                                (i * BATCH_SIZE, g_batches[i]) for i in range(n_g)
                            )
                        }
                        for fut in as_completed(futures):
                            if _cancelled_now():
                                _cancelled = True
                                pool.shutdown(wait=False, cancel_futures=True)
                                break
                            idx, i_start = futures[fut]
                            try:
                                for j, t in enumerate(fut.result()):
                                    dedup_tm[i_start + j] = t
                            except Exception as e:
                                if _is_quota_error(e):
                                    quota_hit = True
                                    pool.shutdown(wait=False, cancel_futures=True)
                                    pct = 10 + int(78 * len(dedup_tm) / total)
                                    upd('translating', pct,
                                        '⚠ Gemini agotado — cambiando a Qwen local…',
                                        engine='gemini')
                                    break
                                raise
                            with done_count:
                                completed_g[0] += 1
                            pct = 10 + int(78 * len(dedup_tm) / total)
                            upd('translating', pct,
                                f'Gemini: {len(dedup_tm)}/{total} líneas…',
                                engine='gemini')
                        if not quota_hit and not _cancelled:
                            pool.shutdown(wait=True)
                    except Exception:
                        pool.shutdown(wait=False, cancel_futures=True)
                        raise

            if _cancelled or _cancel_flags.pop(task_id, False):
                _cancel_flags.pop(task_id, None)
                task.update(status='cancelled', progress=0, message='Traducción cancelada')
                return

            # ── Phase 2: Ollama for any position not yet translated ────────────
            missing_positions = [i for i in range(total) if i not in dedup_tm]

            if missing_positions:
                ollama_used[0] = True
                if _engine() == 'ollama':
                    reason = 'motor local'
                elif not key:
                    reason = 'sin clave Gemini'
                else:
                    reason = 'cuota Gemini agotada'

                if not _ollama_available():
                    raise RuntimeError(
                        f'Ollama no disponible en {_ollama_url()}. '
                        'Instala Ollama (ollama.com) y ejecuta: '
                        f'ollama pull {_ollama_model()}'
                    )

                # Increment reference counter before loading model into VRAM
                with _OLLAMA_TASK_LOCK:
                    _OLLAMA_TASK_COUNT += 1

                upd('translating', 10 + int(78 * len(dedup_tm) / total),
                    f'Cargando {_ollama_model()} en VRAM ({reason})…', engine='ollama')
                _ollama_preload()

                lang_label = _LANG_NAMES.get((src_lang or 'eng').lower(), src_lang or 'inglés')
                upd('translating', 10 + int(78 * len(dedup_tm) / total),
                    f'Traduciendo {len(missing_positions)} líneas ({lang_label} → español)…',
                    engine='ollama')

                # Re-batch only the missing positions (contiguous groups → fewer requests)
                o_batches = []
                buf = [missing_positions[0]]
                for pos in missing_positions[1:]:
                    if pos == buf[-1] + 1 and len(buf) < OLLAMA_BATCH_SIZE:
                        buf.append(pos)
                    else:
                        o_batches.append(buf)
                        buf = [pos]
                o_batches.append(buf)

                done_o = [0]
                for o_batch_idx, o_batch_positions in enumerate(o_batches):
                    if _cancelled_now():
                        _cancelled = True
                        break
                    # GPU breathing room between batches so MPV can play without lag
                    if o_batch_idx > 0 and _OLLAMA_BATCH_PAUSE > 0:
                        pct = 10 + int(78 * len(dedup_tm) / total)
                        upd('translating', min(pct, 88),
                            f'GPU en pausa ({_OLLAMA_BATCH_PAUSE:.0f}s)…', engine='ollama')
                        time.sleep(_OLLAMA_BATCH_PAUSE)
                        if _cancelled_now():
                            _cancelled = True
                            break
                    o_texts = [texts[i] for i in o_batch_positions]
                    o_translated = _translate_batch_ollama(o_texts, src_lang)
                    for pos, t in zip(o_batch_positions, o_translated):
                        dedup_tm[pos] = t
                    with done_count:
                        done_o[0] += 1
                    pct = 10 + int(78 * len(dedup_tm) / total)
                    upd('translating', min(pct, 88),
                        f'Qwen: {len(dedup_tm)}/{total} líneas…', engine='ollama')

            if _cancelled or _cancel_flags.pop(task_id, False):
                _cancel_flags.pop(task_id, None)
                task.update(status='cancelled', progress=0, message='Traducción cancelada')
                return

            # ── Expand dedup results back to original positions ───────────────
            # Representative (first occurrence): use translation as-is.
            # Duplicates (same visible text, different animation tags): keep this
            # position's leading {…} tags and append the translated visible text.
            translated_map: dict[int, str] = {}
            for _d_idx, _trans in dedup_tm.items():
                _trans_vis = _TAG_RE_D.sub('', _trans).strip()
                for _i, _orig_pos in enumerate(_dedup_to_orig[_d_idx]):
                    if _i == 0:
                        translated_map[_orig_pos] = _trans
                    else:
                        _orig_t   = texts_orig[_orig_pos]
                        _lead_m   = _LEAD_TAG_RE.match(_orig_t)
                        _leading  = _lead_m.group(0) if _lead_m else ''
                        translated_map[_orig_pos] = _leading + _trans_vis

            # ── Rebuild subtitle with translations ─────────────────────────────
            # Passthrough positions (empty/symbol-only) fall back to original text
            translated = [translated_map.get(i, texts_orig[i]) for i in range(total_orig)]

            upd('injecting', 90, 'Añadiendo track español al MKV…')

            if is_ass:
                out_content = _rebuild_ass(header, events, translated)
                out_sub = os.path.join(tmpdir, 'translated.ass')
            else:
                out_content = _rebuild_srt(blocks, translated)
                out_sub = os.path.join(tmpdir, 'translated.srt')

            with open(out_sub, 'w', encoding='utf-8') as f:
                f.write(out_content)

            out_mkv = _inject_sub(mkv_path, out_sub, n_subs)

            if _cancel_flags.pop(task_id, False):
                task.update(status='cancelled', progress=0, message='Traducción cancelada')
                return

        engine_label = 'Qwen local' if ollama_used[0] else 'Gemini'
        task.update(status='done', progress=100,
                    message=f'¡Completado! ({engine_label})', output=out_mkv)

    except Exception as e:
        task.update(status='error', progress=0,
                    message=f'Error: {e}', error=str(e))
        print(f'[subtitle] translation error: {e}')
    finally:
        _cancel_flags.pop(task_id, None)
        if ollama_used[0]:
            with _OLLAMA_TASK_LOCK:
                _OLLAMA_TASK_COUNT -= 1
                should_unload = (_OLLAMA_TASK_COUNT == 0)
            if should_unload:
                threading.Thread(target=_ollama_unload, daemon=True).start()


# ── Flask endpoints ───────────────────────────────────────────────────────────

def _get_anime_titles(anime_id: str) -> list:
    """Return [title, title_romaji] for a library entry, filtering empties."""
    if not anime_id:
        return []
    try:
        from api.anime import _lib_read
        lib = _lib_read()
        anime = lib.get(anime_id) or {}
        return [t for t in [anime.get('title', ''), anime.get('title_romaji', '')] if t]
    except Exception:
        return []


# ── Centro de Actividad: exponer las traducciones de subtítulos ────────────────
# La traducción de subtítulos de anime por el modelo (Gemini/Qwen) es una tarea
# "importante" que faltaba en Actividad. Se cuela en el MISMO snapshot SSE
# (/api/status/stream → clave `subtitles`) que descargas/upscale/tomo/transplant,
# para que Actividad sea el punto central de seguimiento. Las descargas de anime
# NO van aquí (tienen su vista propia de Descargas).
_SUBTITLE_TERMINAL = {'done', 'error', 'cancelled'}


def _mk_sub_task(task_id: str, anime_id: str, episode: int, fallback: str = ''):
    """Crea la entrada de tarea con metadatos para Actividad (título del anime +
    episodio) además del estado de progreso."""
    titles = _get_anime_titles(anime_id)
    title = titles[0] if titles else (fallback or 'Anime')
    _tasks[task_id] = dict(
        status='starting', progress=0, message='Iniciando…', error=None, output=None,
        kind='subtitle', title=title, anime_id=anime_id, episode=int(episode),
        _ts=time.time(),
    )


def get_subtitle_tasks() -> dict:
    """Snapshot de las traducciones de subtítulos para el Centro de Actividad.
    Estampa `ended_at` al detectar estado terminal (para el historial) y poda las
    terminales de más de 1 h para que el dict no crezca sin fin. Omite `output`
    (ruta interna del MKV, no relevante para la UI)."""
    now = time.time()
    out, stale = {}, []
    for tid, t in list(_tasks.items()):
        if not isinstance(t, dict):
            continue
        if t.get('status') in _SUBTITLE_TERMINAL and not t.get('ended_at'):
            t['ended_at'] = now
        ended = t.get('ended_at')
        if ended and (now - ended) > 3600:
            stale.append(tid)
            continue
        out[tid] = {k: v for k, v in t.items() if k != 'output'}
    for tid in stale:
        _tasks.pop(tid, None)
        _cancel_flags.pop(tid, None)
    return out


@subtitle_bp.route('/tracks')
def subtitle_tracks():
    """List subtitle tracks in an MKV. Always searches for Spanish subs (direct inject).
    Falls back to English external sources when no text tracks found."""
    info_hash  = request.args.get('info_hash', '')
    episode    = int(request.args.get('episode', 1))
    anime_id   = request.args.get('anime_id', '')
    local_path = request.args.get('local_path', '')
    ep_type    = request.args.get('ep_type', 'episode')

    path = _resolve_video_path(info_hash, episode, anime_id, local_path)
    if not path or not os.path.exists(path):
        return jsonify({'error': 'Archivo no encontrado', 'path': path}), 404

    tracks = _ffprobe_tracks(path)
    titles = _get_anime_titles(anime_id)
    external_tracks = []
    spanish_tracks  = []
    sources_missing_key = []

    # Specials/OVAs are not indexed by episode number — search by title only
    effective_episode = 0 if ep_type == 'special' else episode

    if not get_secret('OPENSUBTITLES_API_KEY'):
        sources_missing_key.append('opensubtitles')
    else:
        # Always search for pre-made Spanish subs (no GPU needed)
        spanish_tracks = _ext_find_spanish_subs(titles, effective_episode)

    if not tracks:
        # No internal text tracks — also search English external sources for translation
        if not get_secret('JIMAKU_API_KEY'):
            sources_missing_key.append('jimaku')
        print(f'[subtitle] No text tracks in {os.path.basename(path)} — searching external sources')
        external_tracks = _ext_find_subs(anime_id, titles, effective_episode)
        print(f'[subtitle] External search found {len(external_tracks)} subs')

    return jsonify({
        'path': path,
        'tracks': tracks,
        'external_tracks': external_tracks,
        'spanish_tracks': spanish_tracks,
        'sources_missing_key': sources_missing_key,
    })


def _sync_sub_to_reference(sub_path: str, mkv_path: str, tmpdir: str) -> str:
    """Sync a subtitle file's timestamps against the video's audio using ffsubsync.
    Returns path to the synced subtitle (may be the same file if sync fails/skipped)."""
    try:
        from ffsubsync.ffsubsync import make_parser, run as ffs_run
        ext  = os.path.splitext(sub_path)[1].lower() or '.srt'
        out  = os.path.join(tmpdir, f'synced{ext}')
        args = make_parser().parse_args([mkv_path, '-i', sub_path, '-o', out])
        result = ffs_run(args)
        if result.get('exc') is None and os.path.exists(out):
            offset = result.get('offset_seconds', 0)
            print(f'[subtitle] ffsubsync: sincronizado (offset={offset:.2f}s) → {os.path.basename(out)}')
            return out
        print(f'[subtitle] ffsubsync: sin cambios necesarios')
        return sub_path
    except Exception as e:
        print(f'[subtitle] ffsubsync error (usando sub original): {e}')
        return sub_path


@subtitle_bp.route('/inject_direct', methods=['POST'])
def subtitle_inject_direct():
    """Download a pre-made Spanish subtitle, sync its timing to the video, and inject it — no LLM."""
    data         = request.get_json(silent=True) or {}
    info_hash    = data.get('info_hash', '')
    episode      = int(data.get('episode', 1))
    anime_id     = data.get('anime_id', '')
    local_path   = data.get('local_path', '')
    external_sub = data.get('external_sub')
    sync         = bool(data.get('sync', True))   # sync timing by default

    if not external_sub:
        return jsonify({'error': 'Se requiere external_sub'}), 400

    path = _resolve_video_path(info_hash, episode, anime_id, local_path)
    if not path or not os.path.exists(path):
        return jsonify({'error': 'Archivo de video no encontrado'}), 404

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            sub_file = _ext_download_sub(external_sub, tmpdir)
            if sync:
                sub_file = _sync_sub_to_reference(sub_file, path, tmpdir)
            tracks = _ffprobe_tracks(path)
            _inject_sub(path, sub_file, len(tracks))
        return jsonify({'status': 'ok', 'file': os.path.basename(path)})
    except Exception as e:
        print(f'[subtitle] inject_direct error: {e}')
        return jsonify({'error': str(e)}), 500


@subtitle_bp.route('/translate', methods=['POST'])
def subtitle_translate():
    """Start async subtitle translation. Returns task_id."""
    data         = request.get_json(silent=True) or {}
    info_hash    = data.get('info_hash', '')
    episode      = int(data.get('episode', 1))
    anime_id     = data.get('anime_id', '')
    sub_index    = int(data.get('sub_index', 0))  # 0-based subtitle stream index
    force        = bool(data.get('force', False))  # replace existing spa track
    local_path   = data.get('local_path', '')
    external_sub = data.get('external_sub')        # {url|file_id, language, source, title}

    path = _resolve_video_path(info_hash, episode, anime_id, local_path)
    if not path or not os.path.exists(path):
        return jsonify({'error': 'Archivo de video no encontrado'}), 404

    tracks = _ffprobe_tracks(path)

    # ── External subtitle path ─────────────────────────────────────────────────
    if external_sub:
        src_lang = external_sub.get('language', 'eng')
        task_id = uuid.uuid4().hex[:8]
        _mk_sub_task(task_id, anime_id, episode, fallback=os.path.basename(path))
        t = threading.Thread(
            target=_do_translate,
            args=(task_id, path, 0, 'subrip', len(tracks), src_lang),
            kwargs={'external_sub_info': external_sub},
            daemon=True,
        )
        t.start()
        return jsonify({'task_id': task_id, 'file': os.path.basename(path)})

    # ── Internal subtitle path ─────────────────────────────────────────────────
    if not tracks:
        return jsonify({'error': 'El archivo no tiene pistas de subtítulos de texto'}), 400

    # Block re-translation unless force=true
    if not force and any(t['language'] in ('spa', 'es') for t in tracks):
        return jsonify({'error': 'El archivo ya tiene subtítulos en español. Usa force=true para reemplazar.'}), 409

    # Find the requested subtitle track (skip existing spa tracks)
    src_tracks = [t for t in tracks if t['language'] not in ('spa', 'es')]
    track = next((t for t in src_tracks if t['sub_index'] == sub_index), src_tracks[0] if src_tracks else None)
    if not track:
        return jsonify({'error': 'No hay pista de subtítulo fuente disponible'}), 400

    codec    = track['codec']
    n_subs   = len(tracks)
    src_lang = track.get('language') or 'eng'

    task_id = uuid.uuid4().hex[:8]
    _mk_sub_task(task_id, anime_id, episode, fallback=os.path.basename(path))

    t = threading.Thread(
        target=_do_translate,
        args=(task_id, path, track['sub_index'], codec, n_subs, src_lang),
        daemon=True,
    )
    t.start()
    return jsonify({'task_id': task_id, 'file': os.path.basename(path)})


@subtitle_bp.route('/reinject', methods=['POST'])
def subtitle_reinject():
    """Re-extract the existing Spanish track and re-inject it (no Gemini). Fixes format without retranslating."""
    data       = request.get_json(silent=True) or {}
    info_hash  = data.get('info_hash', '')
    episode    = int(data.get('episode', 1))
    anime_id   = data.get('anime_id', '')
    local_path = data.get('local_path', '')

    path = _resolve_video_path(info_hash, episode, anime_id, local_path)
    if not path or not os.path.exists(path):
        return jsonify({'error': 'Archivo de video no encontrado'}), 404

    tracks = _ffprobe_tracks(path)
    spa = next((t for t in tracks if t['language'] in ('spa', 'es')), None)
    if not spa:
        return jsonify({'error': 'No hay track español que re-inyectar'}), 400

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            # Extract the existing Spanish track
            ext = '.ass' if spa['codec'] in ('ass', 'ssa') else '.srt'
            extracted = os.path.join(tmpdir, f'spa{ext}')
            subprocess.run(
                ['ffmpeg', '-y', '-i', path, '-map', f'0:s:{spa["sub_index"]}', extracted],
                capture_output=True, check=True,
            )
            with open(extracted, encoding='utf-8', errors='replace') as f:
                content = f.read()

            # Parse + rebuild to ensure clean format (real newlines, no \N in SRT)
            if ext == '.srt':
                blocks = _parse_srt(content)
                clean = _rebuild_srt(blocks, [b['text'] for b in blocks])
                out_path = os.path.join(tmpdir, 'clean.srt')
            else:
                out_path = extracted
                clean = content

            with open(out_path, 'w', encoding='utf-8') as f:
                f.write(clean)

            n_non_spa = len([t for t in tracks if t['language'] not in ('spa', 'es')])
            _inject_sub(path, out_path, n_non_spa)

        return jsonify({'status': 'ok', 'file': os.path.basename(path)})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@subtitle_bp.route('/cancel/<task_id>', methods=['POST'])
def subtitle_cancel(task_id):
    _cancel_flags[task_id] = True
    task = _tasks.get(task_id)
    if task:
        task.update(status='cancelled', progress=0, message='Cancelando…')
    return jsonify({'ok': True})


@subtitle_bp.route('/status/<task_id>')
def subtitle_status(task_id):
    task = _tasks.get(task_id)
    if not task:
        return jsonify({'error': 'task not found'}), 404
    return jsonify(task)
