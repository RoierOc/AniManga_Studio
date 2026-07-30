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
from pathlib import Path
from flask import Blueprint, request, jsonify

from api.config_store import get_secret  # runtime-editable API keys (Ajustes)
from api import sub_lang  # detección robusta ES/LAT (código + título), fuente única de verdad
from api.platform import first_windows_user_dir
from api.observability import record_error

subtitle_bp = Blueprint('subtitle', __name__)

_tasks: dict = {}
_cancel_flags: dict = {}
OLLAMA_BATCH_SIZE = 80          # 80 lines/batch → 25% fewer round trips; fits in 3200-token ctx
# Inter-batch pause — 0 by default (max speed). Set OLLAMA_BATCH_PAUSE_SECS=3 if MPV lags.
_OLLAMA_BATCH_PAUSE = float(os.environ.get('OLLAMA_BATCH_PAUSE_SECS', '0'))
_OLLAMA_TIMEOUT = 300           # seconds per batch (generous for 60 lines)

# Translation credentials/config are resolved at call time from the config store
# (Ajustes) → env → .env, so keys saved from the UI apply without a restart.
# El motor es SIEMPRE Ollama local (Qwen): el fallback a Gemini se retiró el 29-jul-2026 — la
# calidad medida del local es suficiente y la nube añadía clave, cuota y una segunda ruta de fallo.
def _ollama_url():   from api.config_store import get_secret; return get_secret('OLLAMA_URL', 'http://localhost:11434')
def _ollama_model(): from api.config_store import get_secret; return get_secret('OLLAMA_MODEL', 'qwen2.5:14b')

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
        # Los tags ASS ya NO llegan aquí: se sustituyen por marcadores antes de enviar
        # (mask_ass_tags) y se reponen después. Pedir "no toques {\an8}" no funcionaba.
        '• ⁢0⁢ ⁢1⁢ ⁢2⁢…   → marcadores de formato. Cópialos EXACTOS y en el mismo sitio de la\n'
        '                   frase donde estaban. No los numeres de nuevo, no los inventes, no los\n'
        '                   borres. Si uno va pegado a una palabra, déjalo pegado a su traducción.\n'
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

# Qué cuenta como "ya está en español". Estaba copiado en 5 sitios; con un solo criterio no
# pueden desincronizarse el que OFRECE traducir, el que lo BLOQUEA y el que elige la pista fuente.
# La detección real vive en `sub_lang` (código + título; reconoce LAT/castellano/variantes de
# grupo), así que "ya está en español" y la auto-selección del player comparten el MISMO criterio.
def is_es_track(t: dict) -> bool:
    """True si la pista ya está en español (cualquier variante). Acepta ambas formas de dict:
    ffprobe ({language,title}) y mkvmerge ({properties:{language,track_name}})."""
    props = t.get('properties') or {}
    lang = t.get('language') or props.get('language') or ''
    title = t.get('title') or props.get('track_name') or ''
    return sub_lang.is_es(lang, title)


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
            disp = s.get('disposition') or {}
            subs.append({
                'index':     s.get('index'),
                'sub_index': sub_idx,
                'codec':     codec,
                'language':  tags.get('language', 'und'),
                'title':     tags.get('title', ''),
                # `forced` suele marcar la pista de carteles: útil para no elegirla como fuente.
                'forced':    bool(disp.get('forced')),
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



def _note_source(report, source: str, found: int, error=None, unconfigured: bool = False) -> None:
    """Apunta CÓMO le fue a una fuente de subtítulos.

    Sin esto, «Subdivx devolvió 403» y «Subdivx no tenía nada» eran el mismo valor de retorno (una
    lista vacía) y la UI decía «no se encontraron subtítulos» con dos de tres fuentes reventadas.
    MEDIDO el 29-jul-2026: Subdivx falló en 22 de 22 búsquedas (Cloudflare) sin que nada lo dijera.
    """
    if report is None:
        return
    report.append({
        'source': source,
        'found': found,
        'status': 'unconfigured' if unconfigured else ('error' if error and not found else 'ok'),
        'error': (str(error)[:140] if error else None),
    })


def _ext_search_opensubtitles(titles: list, episode: int, languages: str = '', season: int = 1,
                              report: list = None) -> list:
    """Search OpenSubtitles v3. Tries each title variant and filters by title similarity.
    Requires OPENSUBTITLES_API_KEY env var (free account at opensubtitles.com).
    Pass languages='es' to search for Spanish subtitles specifically."""
    key = get_secret('OPENSUBTITLES_API_KEY')
    if not key:
        print('[subtitle] OpenSubtitles: sin OPENSUBTITLES_API_KEY — saltando')
        _note_source(report, 'opensubtitles', 0, unconfigured=True)
        return []
    results = []
    fails: list = []
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
                # La temporada NO puede ir clavada a 1: el anime se numera de corrido (una sola
                # "temporada" con episodios 1..N) pero una serie occidental es S03E05, y pedir
                # S01E05 devuelve subtítulos de OTRO episodio — un fallo MUDO, porque el sub baja
                # y se inyecta igual, sólo que desincronizado y con diálogo que no es el que suena.
                q.update({'type': 'episode', 'season_number': season, 'episode_number': episode})
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
            fails.append(e)
    lang_label = f' [{languages}]' if languages else ''
    print(f'[subtitle] OpenSubtitles{lang_label}: {len(results)} encontrados')
    _note_source(report, 'opensubtitles', len(results), fails[0] if fails else None)
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


def _ext_search_subdl(titles: list, episode: int, season: int = 1, report: list = None) -> list:
    """Search Subdl.com for Spanish/LatAm subtitles. Requires SUBDL_API_KEY env var.
    Free account at subdl.com — returns ZIP archives containing .srt/.ass files."""
    key = get_secret('SUBDL_API_KEY')
    if not key:
        print('[subtitle] Subdl: sin SUBDL_API_KEY — saltando')
        _note_source(report, 'subdl', 0, unconfigured=True)
        return []
    results = []
    fails: list = []
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
            fails.append(e)
    print(f'[subtitle] Subdl: {len(results)} encontrados')
    _note_source(report, 'subdl', len(results), fails[0] if fails else None)
    return results


def _ext_search_subdivx(titles: list, episode: int, report: list = None) -> list:
    """Search Subdivx.com for Spanish LatAm subtitles. No API key required.
    Best source for Latin American fansub groups."""
    results = []
    fails: list = []
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
            fails.append(e)
    print(f'[subtitle] Subdivx: {len(results)} encontrados')
    _note_source(report, 'subdivx', len(results), fails[0] if fails else None)
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
                # El cuerpo dice POR QUÉ (OpenSubtitles manda el motivo en texto): sin él, un 406
                # de cuota y uno de otra causa se leen igual.
                if body:
                    print(f'[subtitle] OpenSubtitles {e.code}: {body}')
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


def _ext_find_spanish_subs(titles: list, episode: int, season: int = 1, report: list = None) -> list:
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
    results.extend(_ext_search_opensubtitles(clean_titles, episode, languages='es', season=season,
                                             report=report))
    results.extend(_ext_search_subdl(clean_titles, episode, season=season, report=report))
    results.extend(_ext_search_subdivx(clean_titles, episode, report=report))
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
        raise RuntimeError('No se pudo extraer el subtítulo. El archivo puede no tener pistas de texto.')
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


def _drop_es_sidecars(video_path: str, keep: str = '') -> int:
    """Borra los sidecars en español que ESTE proyecto dejó junto al vídeo (`<base>.spa.*`).

    Sin esto, rehacer un subtítulo que antes salió `.spa.srt` y ahora sale `.spa.ass` deja LOS DOS
    junto al vídeo: el reproductor lista dos pistas «Español» y elige la que no toca. Sólo toca
    nuestros archivos — el MKV y las pistas incrustadas no se rozan.
    """
    base = os.path.splitext(video_path)[0]
    pref = os.path.basename(base) + '.spa.'
    d = os.path.dirname(base) or '.'
    n = 0
    try:
        names = os.listdir(d)
    except OSError:
        return 0
    for name in names:
        if not name.startswith(pref):
            continue
        p = os.path.join(d, name)
        if os.path.abspath(p) == os.path.abspath(keep or ''):
            continue
        try:
            os.remove(p)
            n += 1
        except OSError as e:
            record_error('subtitle', e, op='drop_sidecar', file=name)
    return n


def _save_sub_external(video_path: str, sub_path: str) -> str:
    """Save subtitle as a sidecar file next to the video (same basename, .srt/.ass).
    MPV auto-loads subtitle files that match the video filename.
    Used for non-MKV formats where mkvmerge cannot embed."""
    base = os.path.splitext(video_path)[0]
    ext = os.path.splitext(sub_path)[1].lower() or '.srt'
    # Use language suffix so MPV picks it up: video.spa.srt
    out = f'{base}.spa{ext}'
    _drop_es_sidecars(video_path, keep=out)   # rehacer sustituye, no acumula
    import shutil
    shutil.copy2(sub_path, out)
    _mark_es_injected(video_path)
    return out


# Incrustar en el .mkv REESCRIBE el archivo, y esos archivos SON el contenido que qBittorrent
# siembra: al cambiar de tamaño el hash deja de casar y el torrent queda roto EN SILENCIO.
#
# MEDIDO (ep10 de Agents of the Four Seasons, traducido el 15/07): el torrent declara
# 1.456.610.283 B y el disco tenía 1.456.645.960 (+35.677) — y qBittorrent seguía en 'stalledUP'
# creyendo que sembraba. Un `recheck` lo daría por incompleto y re-descargaría 1,4 GB. El ep09,
# intacto, casa byte a byte con el suyo.
#
# Por defecto NO se toca el contenedor: la traducción va como sidecar `<vídeo>.spa.ass`, que NO es
# un ciudadano de segunda — `_sidecar_subs`/`_probe_tracks` (anime.py) lo listan y la shell lo carga
# con `sub-add`, así que se comporta como una pista incrustada (delay, estilo, selección).
# Efecto extra: el original del usuario queda intacto y traducir deja de ser destructivo.
#
# SUB_EMBED_IN_MKV=1 recupera el incrustado para quien quiera un archivo autosuficiente y no
# esté sembrando.
_EMBED_IN_MKV = os.environ.get('SUB_EMBED_IN_MKV', '') == '1'


def _inject_sub(mkv_path: str, sub_path: str, n_existing_subs: int) -> str:
    """Deja el subtítulo traducido junto al vídeo (sidecar) o, si SUB_EMBED_IN_MKV=1, dentro del
    .mkv con mkvmerge.

    Al incrustar se usa mkvmerge y no `ffmpeg -c copy`: ffmpeg omite el índice de cues del MKV y
    MPV acaba leyendo sólo el primer paquete de subtítulos.
    """
    if not _EMBED_IN_MKV or not mkv_path.lower().endswith('.mkv'):
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
    # Keep existing subtitle tracks except any prior Spanish track (we replace it). Usa el
    # criterio robusto (código + título) para no dejar una pista LAT/castellano previa duplicada.
    keep_sub_ids   = [str(t['id']) for t in tracks
                      if t.get('type') == 'subtitles' and not is_es_track(t)]

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


# ── Protección de tags ASS ───────────────────────────────────────────────────
# El diseño anterior mandaba los tags {\…} al LLM y le pedía POR PROMPT que no los tocara.
# No es fiable y se midió el destrozo (Agents of the Four Seasons ep10): un {\p1…} de DIBUJO
# perdido -> los comandos vectoriales se renderizaban como texto en pantalla ("m 0 0 l 230 0…"),
# un {\t(18,8735,\fs30)} con el parámetro interior borrado, y títulos con los tags reordenados.
# La cura no es más prompt: es NO enseñárselos.

_ASS_TAG_RE = re.compile(r'\{[^}]*\}')
# \p1..\p9 = modo DIBUJO. Ojo: \pos, \pbo también empiezan por \p -> exigir dígito y que no siga
# una letra. \p0 apaga el dibujo, así que sólo 1-9 marcan una línea como vectorial.
_ASS_DRAW_RE = re.compile(r'\\p[1-9](?![a-zA-Z0-9])')
_PH_RE = re.compile(r'⁢(\d+)⁢')


def is_ass_drawing(text: str) -> bool:
    """¿La línea es un DIBUJO vectorial (\\p1) en vez de texto? Entonces no se traduce jamás:
    su "texto" son coordenadas (`m 0 0 l 230 0 …`) y traducirlo no significa nada; perder el tag
    hace que se pinten como letras encima del vídeo."""
    return bool(_ASS_DRAW_RE.search(text or ''))


def mask_ass_tags(text: str):
    """Sustituye cada bloque {\\…} por un marcador invisible y estable. Devuelve (texto, tags).

    El marcador usa U+2062 (INVISIBLE TIMES): no existe en subtítulos reales, no se traduce y
    los tokenizadores lo respetan mejor que un `{…}` lleno de barras. Función con NOMBRE porque
    es una decisión que se mide (y se puede parchear en un A/B) — ver E-1."""
    tags = []

    def _grab(m):
        tags.append(m.group(0))
        return f'⁢{len(tags) - 1}⁢'

    return _ASS_TAG_RE.sub(_grab, text or ''), tags


def unmask_ass_tags(text: str, tags: list, original: str = '') -> str:
    """Reinserta los tags. Un marcador que el modelo se comió = ese tag se pierde, NO el texto.

    Excepción: si el original ABRÍA con un tag (estilo/posición: \\an8, \\pos, colores…) y el
    modelo lo perdió, se repone al principio — es el que decide dónde y cómo se ve la línea, y
    sin él la frase aparece descolocada en mitad de la pantalla."""
    seen = set()

    def _put(m):
        i = int(m.group(1))
        seen.add(i)
        return tags[i] if 0 <= i < len(tags) else ''

    out = _PH_RE.sub(_put, text or '')
    out = _PH_RE.sub('', out)          # marcadores rotos/inventados -> fuera
    if tags and 0 not in seen and (original or '').startswith('{'):
        out = tags[0] + out
    return out


# ── Estilo HORNEADO en nuestra pista en español — APAGADO por defecto ─────────────────────────
# El estilo lo pone el REPRODUCTOR en vivo (`sub-ass-style-overrides`, ver `ass_style_overrides`),
# que es lo que el usuario eligió: se cambia al momento desde Ajustes, sin re-inyectar, y aplica
# también a las pistas que NO tradujimos nosotros. Tener además el horneado daría dos dueños al
# mismo píxel, así que aquí sólo queda como opción para quien quiera un archivo autosuficiente
# (que se vea igual en la TV o en otro reproductor): SUB_STYLE_FONT="Adobe Arabic" lo reactiva.
_STYLE_FONT    = os.environ.get('SUB_STYLE_FONT', '')
_STYLE_SIZE    = os.environ.get('SUB_STYLE_SIZE', '26')
_STYLE_BOLD    = os.environ.get('SUB_STYLE_BOLD', '-1')     # -1 = sí, 0 = no
_STYLE_OUTLINE = os.environ.get('SUB_STYLE_OUTLINE', '1')
_STYLE_SHADOW  = os.environ.get('SUB_STYLE_SHADOW', '0')


# Estilos de TIPOGRAFIADO (carteles, canciones, créditos): NO son diálogo y no se re-estilan.
# El grupo los eligió para casar con el arte del vídeo (un rótulo en pantalla, un letrero), así
# que imponerles nuestra fuente los descuadra. CONSTANTE editable; el MECANISMO es `_is_sign_style`.
# Cubre las convenciones vistas en fuentes reales: Erai-raws (`sign_1234_…`, `Sign_Arial`, `song`),
# scans ES/PT (`Cart_A_Tre`, `Créditos`), y las típicas de karaoke/OP/ED.
# Dos patrones a propósito, no uno:
#  - PREFIJO: siglas cortas que sólo significan "cartel" al principio del nombre. `op`/`ed` NO
#    pueden buscarse dentro del nombre — el "op" de `Italics Top` casaría y dejaría un estilo de
#    DIÁLOGO sin re-estilar (falso positivo caro: se ve como fuente mezclada).
#  - EN CUALQUIER SITIO: palabras largas e inequívocas (`Opening Song`, `Ending Credits`).
_SIGN_PREFIX_PAT = re.compile(
    r'^\s*(sign|cart|letrero|rotulo|rótulo|titul|title|note|nota|op\b|ed\b)', re.I)
_SIGN_ANY_PAT = re.compile(
    r'(song|cancion|canción|karaoke|kfx|credit|credito|crédito)', re.I)


# El TÍTULO de una pista no sigue la convención de los nombres de Style, así que tiene su propio
# patrón. MEDIDO en Terror in Resonance [Judas]: hay dos pistas inglesas, `English [Signs/Lyrics]`
# (69 líneas, sólo carteles y karaoke) y `English [Full]` (339, el diálogo). Elegir la primera
# producía un subtítulo español SIN DIÁLOGO, y el fallo era mudo: sale un .spa.ass, se ve la barra
# de progreso, y sólo lo notas al reproducir.
_SIGNS_TRACK_PAT = re.compile(
    r'(sign|lyric|song|karaoke|forced|forzad|cartel|letrero|s\s*&\s*l)', re.I)


def untranslated_dialogue(originals: list, translated: list) -> int:
    """Cuántas líneas de DIÁLOGO volvieron idénticas al original (= sin traducir).

    Se descartan a propósito:
      - líneas de menos de 4 palabras («Hmm.», «Nueve», un nombre propio): coinciden por
        legítimas, no por fallo;
      - romaji de karaoke (letras de OP/ED), que no se traduce.
    Sin este filtro el recuento sube a ~12 % en un episodio perfectamente traducido.
    """
    n = 0
    for orig, tr in zip(originals, translated):
        o = (orig or '').strip()
        if not o or o != (tr or '').strip():
            continue
        if len(o.split()) < 4:
            continue
        n += 1
    return n


def _is_signs_track(title: str) -> bool:
    """True si el título de la PISTA la delata como carteles/karaoke en vez de diálogo."""
    return bool(_SIGNS_TRACK_PAT.search(title or ''))


def _is_sign_style(name: str) -> bool:
    """True si el Style: parece tipografiado/cartel en vez de diálogo."""
    n = name or ''
    return bool(_SIGN_PREFIX_PAT.match(n) or _SIGN_ANY_PAT.search(n))


def restyle_ass_header(header: list) -> list:
    """Reescribe fuente/cuerpo/negrita/borde/sombra de los Style: de DIÁLOGO del ASS.

    Se leen las columnas del `Format:` de [V4+ Styles] en vez de asumir posiciones fijas: el
    orden no es idéntico en todos los scripts (v4 vs v4+), y escribir a ciegas en el índice
    equivocado te cambia un color por un tamaño.

    OJO (medido, no supuesto): los carteles NO se protegen solos. Se creyó que llevaban `\\fn…`
    inline y que el inline mandaba sobre el estilo — FALSO: en [Erai-raws] los `sign_…` definen
    `Times New Roman,24` **en el propio Style:** y sus eventos no traen ningún `\\fn`. Sin este
    filtro, re-estilar les cambiaba la tipografía y los descuadraba del arte."""
    if not _STYLE_FONT:
        return header
    cols, out = None, []
    for line in header:
        s = line.strip()
        if s.lower().startswith('format:') and cols is None and any(
                'fontname' in c.strip().lower() for c in s.split(':', 1)[1].split(',')):
            cols = [c.strip().lower() for c in s.split(':', 1)[1].split(',')]
            out.append(line); continue
        if not (cols and s.lower().startswith('style:')):
            out.append(line); continue
        eol = line[len(line.rstrip('\r\n')):]
        vals = [v.strip() for v in s.split(':', 1)[1].split(',')]
        if len(vals) != len(cols):
            out.append(line); continue          # fila rara -> no tocar
        if 'name' in cols and _is_sign_style(vals[cols.index('name')]):
            out.append(line); continue          # cartel/canción -> intacto
        for name, val in (('fontname', _STYLE_FONT), ('fontsize', _STYLE_SIZE),
                          ('bold', _STYLE_BOLD), ('outline', _STYLE_OUTLINE),
                          ('shadow', _STYLE_SHADOW)):
            if name in cols:
                vals[cols.index(name)] = val
        out.append('Style: ' + ','.join(vals) + eol)
    return out


# ── Fuentes instaladas (para el selector de estilo de Ajustes) ────────────────────────────────
# libass resuelve por NOMBRE DE FAMILIA ('Adobe Arabic'), no por nombre de fichero
# ('AdobeArabic-Regular.otf'), así que hay que abrir cada fuente y leer su tabla de nombres.
# El escaneo cuesta ~1s con 300 fuentes → se cachea hasta que cambia el mtime de las carpetas.
_fonts_cache: dict = {"key": None, "list": []}
_fonts_lock = threading.Lock()


def _font_dirs() -> list:
    """Carpetas donde Windows guarda las fuentes que libass podrá resolver."""
    dirs = []
    sys_fonts = Path('/mnt/c/Windows/Fonts')
    if sys_fonts.exists():
        dirs.append(sys_fonts)
    user = first_windows_user_dir()
    if user:
        u = user / 'AppData/Local/Microsoft/Windows/Fonts'   # instaladas "sólo para mí"
        if u.exists():
            dirs.append(u)
    return dirs


def list_installed_fonts() -> list:
    """Familias tipográficas instaladas, ordenadas. [{family, styles:[…]}]

    Sólo informativo: el usuario elige una familia y libass la resuelve en el reproductor. Si
    la fuente no estuviera, libass cae a una de reserva en silencio — por eso listamos lo que
    HAY de verdad en vez de dejar escribir un nombre a mano.
    """
    dirs = _font_dirs()
    key = tuple((str(d), int(d.stat().st_mtime)) for d in dirs)
    with _fonts_lock:
        if _fonts_cache["key"] == key:
            return _fonts_cache["list"]
    try:
        from PIL import ImageFont
    except Exception:
        return []
    fams: dict = {}
    for d in dirs:
        for f in sorted(d.iterdir()):
            if f.suffix.lower() not in ('.ttf', '.otf', '.ttc'):
                continue
            try:
                fam, style = ImageFont.truetype(str(f), 12).getname()
            except Exception:
                continue                    # .ttc con varias caras, fuente rota: se ignora
            if not fam:
                continue
            fams.setdefault(fam, set()).add(style or 'Regular')
    out = [{"family": k, "styles": sorted(v)} for k, v in sorted(fams.items(), key=lambda x: x[0].lower())]
    with _fonts_lock:
        _fonts_cache.update(key=key, list=out)
    return out


@subtitle_bp.route('/fonts', methods=['GET'])
def fonts_route():
    return jsonify({"fonts": list_installed_fonts()})


# ── Estilo en vivo: qué estilos del archivo son diálogo y cuáles carteles ──────────────────────
def _parse_style_header(ass_path: str) -> tuple:
    """(nombres de Style:, PlayResY) de una cabecera ASS ya extraída.

    El PlayResY hace falta porque el `Fontsize` de ASS es RELATIVO a él: `Fontsize=26` se ve el
    DOBLE de grande en un script de PlayResY=360 que en uno de 720. Vive en [Script Info], antes
    de [Events], así que se lee en la misma pasada. None si el script no lo declara."""
    names, cols, res_y = [], None, None
    try:
        for line in open(ass_path, encoding='utf-8', errors='replace'):
            s = line.strip()
            low = s.lower()
            if low.startswith('[events]'):
                break
            if low.startswith('playresy:'):
                try:
                    res_y = int(s.split(':', 1)[1].strip())
                except ValueError:
                    pass
            elif low.startswith('format:') and 'name' in low:
                cols = [c.strip().lower() for c in s.split(':', 1)[1].split(',')]
            elif low.startswith('style:') and cols:
                vals = [v.strip() for v in s.split(':', 1)[1].split(',')]
                if len(vals) == len(cols) and 'name' in cols:
                    names.append(vals[cols.index('name')])
    except OSError:
        return [], None
    return names, res_y


def _ass_headers(path: str, sub_indexes: list) -> dict:
    """Cabeceras ASS de varias pistas: {sub_index: [nombres de estilo]}.

    Dos optimizaciones, ambas MEDIDAS sobre un episodio real de la biblioteca (21 pistas, en un
    disco de Windows vía WSL):

      1. `-t 0` — la cabecera vive en el CodecPrivate del MKV, así que el muxer la escribe al
         inicializar y no hace falta demuxar ni un diálogo: 3511 ms -> 68 ms por pista.
      2. UNA sola llamada a ffmpeg con varias salidas — el coste real es abrir el archivo por
         9p, no decodificar: 7,47 s -> 0,91 s. Contenido idéntico byte a byte.

    Junto: de inservible (llamada por episodio) a instantáneo. No se reutiliza `_extract_sub` a
    propósito: ese camino lo usa la traducción, que SÍ necesita los diálogos enteros.
    """
    if not sub_indexes:
        return {}
    with tempfile.TemporaryDirectory() as td:
        cmd = ['ffmpeg', '-y', '-v', 'error', '-i', path]
        outs = {}
        for i in sub_indexes:
            o = os.path.join(td, f'{i}.ass')
            outs[i] = o
            cmd += ['-map', f'0:s:{i}', '-t', '0', o]
        subprocess.run(cmd, capture_output=True, env={**os.environ, 'LC_ALL': 'C.UTF-8'})
        # Sin comprobar returncode: si UNA pista falla, ffmpeg devuelve != 0 pero el resto de
        # salidas sí se escribieron. Se toma lo que haya.
        return {i: _parse_style_header(o) for i, o in outs.items() if os.path.exists(o)}


_styles_cache: dict = {}
_styles_lock = threading.Lock()


def ass_style_split(path: str) -> dict:
    """Estilos de TODAS las pistas ASS del archivo, partidos en diálogo vs cartel.

    Se mira el archivo entero y no sólo la pista que sonará, porque el override de libass va por
    NOMBRE de estilo y no por pista: da igual cuál acabe eligiendo mpv con `slang`. Un nombre que
    sea cartel en CUALQUIER pista se trata como cartel en todas (fallo seguro: ante la duda no se
    toca, que es peor pasarse que quedarse corto).

    Cacheado por (ruta, mtime): el archivo no cambia mientras lo ves, y al mover un deslizador de
    Ajustes esto se pide en cada cambio.
    """
    try:
        key = (path, os.path.getmtime(path))
    except OSError:
        key = (path, 0)
    with _styles_lock:
        if key in _styles_cache:
            return _styles_cache[key]

    dialogue, signs = set(), set()
    res_ys = []
    idxs = [t['sub_index'] for t in _ffprobe_tracks(path) if t['codec'] in ('ass', 'ssa')]
    for names, res_y in _ass_headers(path, idxs).values():
        has_dialogue = False
        for n in names:
            if _is_sign_style(n):
                signs.add(n)
            else:
                dialogue.add(n); has_dialogue = True
        # Solo cuentan las pistas con DIÁLOGO: es su Fontsize el que escalamos, no el de una
        # pista de solo-carteles con otra resolución.
        if has_dialogue and res_y:
            res_ys.append(res_y)
    dialogue -= signs
    # PlayResY representativo = el más común entre las pistas de diálogo (un release suele ser
    # homogéneo: todas 720 o todas 360). None si ninguna lo declara → no se escala.
    play_res_y = max(set(res_ys), key=res_ys.count) if res_ys else None
    res = {"dialogue": sorted(dialogue), "signs": sorted(signs), "play_res_y": play_res_y}
    with _styles_lock:
        if len(_styles_cache) > 64:         # cota: es un cache de conveniencia, no un índice
            _styles_cache.clear()
        _styles_cache[key] = res
    return res


# Resolución de referencia para el tamaño de subtítulo: el valor que pides ("26") significa
# "26 a 720p". Los scripts de 720 se quedan igual; los de 360 se escalan a la mitad para que se
# VEAN igual. 720 es la res más común en los releases de la biblioteca (medido).
_SUB_REF_RES_Y = 720


def _scale_metric(value: str, play_res_y) -> str:
    """Escala un tamaño en unidades de script (Fontsize/Outline/Shadow) para que su tamaño VISUAL
    no dependa del PlayResY del script. `26` a 360 → `13` (13/360 == 26/720). Entero, mínimo 1."""
    if not play_res_y or play_res_y == _SUB_REF_RES_Y:
        return value
    try:
        scaled = round(float(value) * play_res_y / _SUB_REF_RES_Y)
    except ValueError:
        return value
    return str(max(1, scaled))


def ass_style_overrides(styles: list, font: str = '', size: str = '',
                        bold: str = '', outline: str = '', shadow: str = '',
                        play_res_y=None) -> str:
    """Construye el valor de `sub-ass-style-overrides` de mpv para los estilos dados.

    MEDIDO sobre archivos reales: `Default.Fontname=X` deja los carteles BYTE A BYTE idénticos,
    mientras que `Fontname=X` (sin prefijo de estilo) se los lleva por delante. De ahí que se
    emita una entrada POR ESTILO de diálogo en vez de una global: es la única forma de respetar
    el tipografiado que el grupo casó con el arte del vídeo.

    `size`/`outline`/`shadow` se ESCALAN por el PlayResY del script (ver `_scale_metric`): sin
    esto, el mismo tamaño se veía el doble de grande en un anime de PlayResY=360 que en uno de
    720 — que era justo el bug ("el tamaño cambia según el anime").

    OJO: no se pueden usar estilos cuyo nombre lleve ',' o '=' (romperían el parseo de la opción
    y libass parte por el ÚLTIMO '='). La ',' es imposible en ASS (el formato es CSV), pero el
    '=' no, así que se descartan.
    """
    fields = [(k, v) for k, v in (
        ('Fontname', font),
        ('Fontsize', _scale_metric(size, play_res_y)),
        ('Bold', bold),
        ('Outline', _scale_metric(outline, play_res_y)),
        ('Shadow', _scale_metric(shadow, play_res_y)),
    ) if v != '']
    if not fields:
        return ''
    out = []
    for st in styles:
        if ',' in st or '=' in st:
            continue
        for k, v in fields:
            out.append(f'{st}.{k}={v}')
    return ','.join(out)


@subtitle_bp.route('/styles', methods=['GET'])
def styles_route():
    """Estilos del vídeo clasificados + el override listo para mandarle al player.

    El front lo pide al empezar un episodio y cada vez que tocas el estilo en Ajustes; el player
    lo aplica con un `setprop sub-ass-style-overrides` (surte efecto EN CALIENTE, verificado).
    """
    path = _resolve_video_path(request.args.get('info_hash', ''),
                               int(request.args.get('episode', 1)),
                               request.args.get('anime_id', ''),
                               request.args.get('local_path', ''))
    if not path or not os.path.exists(path):
        return jsonify({'error': 'Archivo no encontrado'}), 404
    split = ass_style_split(path)
    return jsonify({**split, "overrides": ass_style_overrides(
        split['dialogue'],
        font=request.args.get('font', ''), size=request.args.get('size', ''),
        bold=request.args.get('bold', ''), outline=request.args.get('outline', ''),
        shadow=request.args.get('shadow', ''), play_res_y=split.get('play_res_y'))})


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


# ── Ollama / local-model translation ─────────────────────────────────────────

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
    # (Aquí había un "TIP: inicia Ollama con OLLAMA_FLASH_ATTENTION=1" que miraba el entorno de
    # ESTE proceso para adivinar cómo está arrancado OTRO — el servicio de Ollama. VERIFICADO el
    # 29-jul-2026: la variable SÍ estaba puesta en `ollama.service` y en el proceso vivo, y el aviso
    # salía igual 11 veces por lote. Un consejo que no puede comprobar lo que aconseja es ruido.)
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


def translate_with_tag_protection(texts: list, engine) -> list:
    """Envuelve a CUALQUIER motor: enmascara los tags ASS, traduce sólo el texto y los repone.

    Punto ÚNICO donde se protege el formato del subtítulo traducido.
    Las líneas de dibujo (\\p1) ni se mandan: se devuelven intactas."""
    idx, payload, masks = [], [], []
    out = list(texts)
    for i, t in enumerate(texts):
        if is_ass_drawing(t):
            continue                      # dibujo vectorial -> intacto, nunca al modelo
        masked, tags = mask_ass_tags(t)
        if not masked.strip():
            continue                      # sólo tags/vacío -> nada que traducir
        idx.append(i); payload.append(masked); masks.append(tags)
    if not payload:
        return out
    got = engine(payload)
    for i, tr, tags in zip(idx, got, masks):
        out[i] = unmask_ass_tags(tr, tags, texts[i])
    return out


def _translate_batch_ollama(texts: list, src_lang: str = 'eng') -> list:
    """Translate a batch via local Ollama. Retries once if model returns < 85% of lines.

    Uses system/user message split so Ollama reuses the KV cache for the instruction
    prompt across all batches of the same job (only the user portion changes).
    """
    if not texts:
        return []
    return translate_with_tag_protection(texts, lambda p: _translate_batch_ollama_raw(p, src_lang))


def _translate_batch_ollama_raw(texts: list, src_lang: str = 'eng') -> list:
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

    # Aviso de "esto no se ha traducido". Sólo cuenta DIÁLOGO: una línea corta o una de karaoke
    # romaji es idéntica al original porque debe serlo, no porque haya fallado.
    #
    # MEDIDO el 29-jul-2026 sobre 948 líneas ya traducidas (Terror in Resonance eps 2/5/9,
    # alineadas por marca de tiempo): 12 % de líneas idénticas, pero **diálogo real sin traducir =
    # 13 (1,4 %)**, y de ésas sólo 2 eran diálogo de verdad; el resto, carteles con nombres propios.
    # Con el umbral viejo (15 % de TODO) el aviso saltaba en todos los episodios y no significaba
    # nada — un aviso que siempre suena es un aviso apagado.
    unchanged = untranslated_dialogue(texts, result)
    if unchanged > max(3, n * 0.10):
        print(f'[subtitle] Ollama warning: {unchanged}/{n} líneas de DIÁLOGO sin traducir '
              f'— posible fallo de traducción')

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

            _cancelled = False

            if _cancelled or _cancel_flags.pop(task_id, False):
                _cancel_flags.pop(task_id, None)
                task.update(status='cancelled', progress=0, message='Traducción cancelada')
                return

            # ── Phase 2: Ollama for any position not yet translated ────────────
            missing_positions = [i for i in range(total) if i not in dedup_tm]

            if missing_positions:
                ollama_used[0] = True

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
                    f'Cargando {_ollama_model()} en VRAM…', engine='ollama')
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
                out_content = _rebuild_ass(restyle_ass_header(header), events, translated)
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

        task.update(status='done', progress=100,
                    message='¡Completado! (Qwen local)', output=out_mkv)

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
# La traducción de subtítulos de anime por el modelo local (Qwen) es una tarea
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
    # Series/películas occidentales: no viven en la biblioteca de anime, así que traen su
    # identidad puesta (títulos + temporada) en vez de buscarse por `anime_id`.
    season     = int(request.args.get('season', 1) or 1)
    ext_titles = [t for t in request.args.getlist('titles') if t.strip()]

    path = _resolve_video_path(info_hash, episode, anime_id, local_path)
    if not path or not os.path.exists(path):
        return jsonify({'error': 'Archivo no encontrado', 'path': path}), 404

    # Las pistas que YA están en español no son candidatas a traducir: se anuncian aparte para que
    # el modal no invite a traducir español a español. Antes salían en la lista de "traducir" y el
    # backend las rechazaba después — o peor, se traducían igual porque el front mandaba force=true.
    all_tracks = _ffprobe_tracks(path)
    embedded_spanish = [t for t in all_tracks if is_es_track(t)]
    tracks = [t for t in all_tracks if not is_es_track(t)]

    titles = ext_titles or _get_anime_titles(anime_id)
    external_tracks = []
    spanish_tracks  = []
    sources_missing_key = []
    # Parte por fuente: qué respondió cada una. Sin él, «no hay subtítulos» y «dos de tres fuentes
    # reventaron» se veían igual en la UI.
    sources_report: list = []

    # Specials/OVAs are not indexed by episode number — search by title only
    effective_episode = 0 if ep_type == 'special' else episode

    # Se buscan SIEMPRE los subs ES ya hechos (no gastan GPU). Antes esto colgaba de tener clave de
    # OpenSubtitles: sin ella no se probaba NINGUNA fuente, ni las que no necesitan clave (Subdivx).
    # Ahora cada fuente declara lo suyo en el parte y de ahí sale qué falta configurar.
    spanish_tracks = _ext_find_spanish_subs(titles, effective_episode, season=season,
                                            report=sources_report)
    sources_missing_key += [r['source'] for r in sources_report if r['status'] == 'unconfigured']

    # OJO: la condición mira `all_tracks`, no `tracks`. Un archivo que sólo trae subtítulos en
    # español TIENE pistas y no necesita nada; salir a buscar subs externos ahí sería absurdo.
    if not all_tracks:
        # No internal text tracks — also search English external sources for translation
        if not get_secret('JIMAKU_API_KEY'):
            sources_missing_key.append('jimaku')
        print(f'[subtitle] No text tracks in {os.path.basename(path)} — searching external sources')
        external_tracks = _ext_find_subs(anime_id, titles, effective_episode)
        print(f'[subtitle] External search found {len(external_tracks)} subs')

    return jsonify({
        'path': path,
        'tracks': tracks,
        'embedded_spanish': embedded_spanish,
        'external_tracks': external_tracks,
        'spanish_tracks': spanish_tracks,
        'sources_missing_key': sources_missing_key,
        'sources_report': sources_report,
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
        print('[subtitle] ffsubsync: sin cambios necesarios')
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
    if not force and any(is_es_track(t) for t in tracks):
        return jsonify({'error': 'El archivo ya tiene subtítulos en español. Usa force=true para reemplazar.'}), 409

    # Find the requested subtitle track (skip existing spa tracks).
    # `sub_index` es el índice ENTRE SUBTÍTULOS (0,1,2…), que es lo que consume `ffmpeg -map 0:s:N`.
    # NO confundir con `index`, el índice absoluto del stream en el contenedor (2,3,4…): el modal
    # mandaba ése y pedir "inglés" (index 2) traducía la pista ÁRABE (sub_index 2), con su
    # puntuación RTL al principio y todo. El fallo era MUDO porque el índice equivocado casaba con
    # OTRA pista real.
    src_tracks = [t for t in tracks if not is_es_track(t)]
    if not src_tracks:
        return jsonify({'error': 'No hay pista de subtítulo fuente disponible'}), 400
    track = next((t for t in src_tracks if t['sub_index'] == sub_index), None)
    if not track:
        # NO caer a src_tracks[0] en silencio: traducir una pista que el usuario no pidió es
        # peor que fallar, y es indistinguible de un acierto hasta que ves el resultado.
        avail = ', '.join(f"{t['sub_index']}={t['language']}" for t in src_tracks)
        return jsonify({'error': f'No existe la pista fuente sub_index={sub_index}. Disponibles: {avail}'}), 400
    print(f"[subtitle] pista fuente elegida: sub_index={track['sub_index']} "
          f"lang={track.get('language')} title={track.get('title')!r}", flush=True)

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
    """Re-extract the existing Spanish track and re-inject it (sin traducir). Fixes format only."""
    data       = request.get_json(silent=True) or {}
    info_hash  = data.get('info_hash', '')
    episode    = int(data.get('episode', 1))
    anime_id   = data.get('anime_id', '')
    local_path = data.get('local_path', '')

    path = _resolve_video_path(info_hash, episode, anime_id, local_path)
    if not path or not os.path.exists(path):
        return jsonify({'error': 'Archivo de video no encontrado'}), 404

    tracks = _ffprobe_tracks(path)
    spa = next((t for t in tracks if is_es_track(t)), None)
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

            n_non_spa = len([t for t in tracks if not is_es_track(t)])
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
