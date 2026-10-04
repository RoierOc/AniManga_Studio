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
from api.resilient_http import http as _rhttp  # retry + backoff + per-host rate limiting (AniList/Jikan)
from flask import Blueprint, has_request_context, jsonify, request, send_file

from api.imgproxy import warm as _warm_img, invalidate as _invalidate_img
from api.platform import is_wsl as _is_wsl, is_macos as _is_macos
from api.config_store import get_secret, set_secrets  # runtime-editable API keys (Ajustes)
from api import sub_lang  # clasificación robusta de subtítulos ES/LAT (código + título)
from api.observability import record_error, swallow  # hace VISIBLE el fallo silencioso
from api.genre_tags import pick_tags  # géneros específicos (GL/BL, Isekai…) desde las tags de AniList
from api.anilist import _cover as _al_cover  # AniList: extraLarge (460x650) antes que large (230x325)
from api.imgproxy import hd_url as _hd       # sube el tamaño de las URLs de TMDB ya guardadas

# ── Estado de seguimiento: un solo convenio ───────────────────────────────────
# MEDIDO sobre la biblioteca real (87 series): 22 sin estado, más un 'FINISHED' y un '' sueltos.
# `FINISHED` no es un estado de SEGUIMIENTO, es el estado de EMISIÓN de AniList que se coló al
# importar. El resultado era que los filtros mentían: «Viendo» no listaba series que estabas
# viendo, y las 22 sin estado no salían en ningún pill aunque sí en «Todo».
_STATUS_VALIDOS = {'watching', 'completed', 'plan_to_watch', 'on_hold', 'dropped'}
# AniList tiene DOS vocabularios y aquí sólo vale uno. `MediaListStatus` (CURRENT, PLANNING,
# COMPLETED…) dice qué has hecho TÚ con la serie: eso sí se traduce. `MediaStatus` (FINISHED,
# RELEASING…) dice si la serie terminó de EMITIRSE, que no habla de ti para nada.
_STATUS_ALIAS = {
    'COMPLETED': 'completed', 'CURRENT': 'watching', 'WATCHING': 'watching',
    'PLANNING': 'plan_to_watch', 'PAUSED': 'on_hold', 'DROPPED': 'dropped',
}
# Traducir estos a un estado de seguimiento marcaba como VISTA una serie recién enlazada con 0
# episodios vistos — y «completed» está oculto en el filtro «Todo», así que la serie se guardaba
# y desaparecía de la vista. Se ignoran: sin estado propio, manda lo que de verdad has visto.
_STATUS_EMISION = {'FINISHED', 'RELEASING', 'NOT_YET_RELEASED', 'CANCELLED', 'HIATUS'}


def _norm_status(raw, vistos: int, total: int) -> str:
    """Devuelve siempre uno de los cinco estados. Nunca inventa por encima de lo que el usuario dijo.

    Sólo se DEDUCE cuando no hay estado guardado, y se deduce de lo único fiable que hay: cuántos
    episodios has visto. Sin esto, una serie terminada hace meses seguía apareciendo como si no la
    hubieras tocado.
    """
    s = (raw or '').strip()
    if s in _STATUS_VALIDOS:
        return s
    if s.upper() in _STATUS_ALIAS:
        return _STATUS_ALIAS[s.upper()]
    # Estado de EMISIÓN colado en el campo de seguimiento: vale tanto como no tener estado.
    # (No hace falta migrar el fichero: las entradas ya contaminadas se arreglan al leerlas.)
    if total and vistos >= total:
        return 'completed'
    if vistos > 0:
        return 'watching'
    return 'plan_to_watch'

anime_bp = Blueprint('anime', __name__)

_ANILIST = 'https://graphql.anilist.co'
_JIKAN   = 'https://api.jikan.moe/v4'
_NYAA    = 'https://nyaa.si'
_NS      = '{https://nyaa.si/xmlns/nyaa}'

_search_cache:   dict = {}
_nyaa_cache:     dict = {}
_seasonal_cache: dict = {}
_seasonal_refreshing: set = set()   # claves con refresh en background en vuelo
_SEASONAL_DISK_TTL = 6 * 3600       # tolerancia del stale servido desde disco
_qbt_files_cache: dict = {}   # info_hash → {ep_num: stem}; immutable per torrent, pruned to live hashes
_qbt_file_details_cache: dict = {}  # info_hash → {file_index: raw qBittorrent file}
_SEASONAL_TTL = 600  # 10 min

_THUMBS_DIR = _Path.home() / '.cache' / 'manga-upscaler' / 'thumbs'

def _clear_thumb_cache():
    if _THUMBS_DIR.exists():
        for f in _THUMBS_DIR.glob('*.jpg'):
            try: f.unlink()
            except Exception: pass


def _borrar_thumbs(anime_id, ep_num=None) -> int:
    """El fotograma es un DERIVADO del vídeo: si se borra el fichero, se borra el fotograma.

    Sin esto la miniatura sobrevivía al episodio para siempre (`has_thumb` sale de un glob de esta
    carpeta, no del disco de vídeo), así que un episodio borrado seguía enseñando su fotograma —
    y si ese fotograma había salido mal, no había forma humana de quitarlo. Se pasa por el ancho
    con comodín (`_w*`) para llevarse también los huérfanos de anchos anteriores.
    """
    if not _THUMBS_DIR.exists():
        return 0
    patrones = [f'{anime_id}_*.jpg'] if ep_num is None else \
               [f'{anime_id}_{ep_num}_w*.jpg', f'{anime_id}_sp{ep_num}_w*.jpg']
    n = 0
    for pat in patrones:
        for f in _THUMBS_DIR.glob(pat):
            try:
                f.unlink()
                n += 1
            except Exception:
                pass
    return n

# NOTE: los fotogramas NO se limpian al arrancar — sobreviven a los reinicios y a que se suelte
# el torrent, que es lo que hace que la ficha siga viéndose entera. Lo que SÍ se borra es el
# fotograma de un vídeo que se ha borrado de verdad (`_borrar_thumbs`).

# ── Anime library persistence ──────────────────────────────────────────────────

def _lib_path() -> _Path:
    # Raíz de biblioteca del modo ACTIVO (normal u oculta) — resuelta en tiempo de
    # llamada, nunca congelada al importar, para que en modo oculto la membresía de
    # anime viva por completo bajo la raíz oculta y no se cruce con la normal.
    from api.runtime import manga_dir
    return _Path(manga_dir()) / 'anime_library.json'

def _lib_read() -> dict:
    p = _lib_path()
    # "No hay biblioteca todavía" (fichero ausente) es un vacío LEGÍTIMO: no se loguea.
    # Un fichero presente pero ilegible/corrupto SÍ es un fallo — y hasta ahora devolvía {}
    # igual que el vacío, así que toda la app (storage, backup, progreso nativo) veía "0 animes"
    # sin rastro. Se sigue devolviendo {} (comportamiento intacto) pero ahora deja huella.
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding='utf-8'))
    except Exception as e:
        record_error('anime', e, op='lib_read', path=str(p))
        return {}

def _lib_write(data: dict):
    write_json_atomic(_lib_path(), data, indent=2, keep_backup=True)


# ── Identidad de una obra en la biblioteca ────────────────────────────────────
# ⚠️ La CLAVE del dict NO es la identidad: una entrada nacida de Jikan (que sólo trae
# `mal_id`) se guardaba con la clave = mal_id, y el backfill le rellenaba luego el `al_id`
# SIN reclavarla. El siguiente `/library/add` desde AniList calculaba la clave con el al_id,
# no la encontraba, y creaba una SEGUNDA entrada del mismo anime con sólo los episodios
# nuevos (medido: Umamusume Cinderella Gray Part 2 en `61930` y `195240`). Por eso la clave
# se RESUELVE contra lo que ya hay, en vez de acuñarse a ciegas.

def _mismo_id(a, b) -> bool:
    """¿Dos ids son el mismo? Compara como número: del cliente llegan como texto."""
    if a is None or b is None:
        return False
    try:
        return int(a) == int(b)
    except (TypeError, ValueError):
        return str(a) == str(b)


def _lib_key(lib: dict, al_id=None, mal_id=None, title: str = '') -> str:
    """La clave de esta obra en `lib`: la de la entrada que YA existe, o una nueva."""
    for k, v in lib.items():
        if (_mismo_id(al_id, v.get('al_id')) or _mismo_id(al_id, k)
                or _mismo_id(mal_id, v.get('mal_id')) or _mismo_id(mal_id, k)):
            return k
    return str(al_id or mal_id or re.sub(r'[^a-z0-9]+', '_', (title or '').lower())[:40])


# ── Una serie, VARIAS carpetas ────────────────────────────────────────────────
# ⚠️ `local_path` era UNA carpeta, y una serie se reparte entre discos con toda naturalidad:
# basta con cambiar la carpeta de descargas a mitad de temporada. Si la entrada apunta solo a
# la carpeta nueva, los episodios de la carpeta anterior desaparecen de la vista.
# `local_path` sigue siendo la carpeta principal (todo lo demás la usa); las otras van en
# `local_paths` y se recorren siempre juntas.

def _carpetas_de(anime: dict) -> list:
    """Todas las carpetas donde vive esta serie, la principal primero, sin repetir."""
    vistas, salida = set(), []
    for p in [anime.get('local_path')] + list(anime.get('local_paths') or []):
        p = (p or '').strip()
        if p and p not in vistas:
            vistas.add(p)
            salida.append(p)
    return salida


def _anadir_carpeta(anime: dict, carpeta: str) -> bool:
    """Registra otra carpeta de la serie. Devuelve si era nueva."""
    if not carpeta or carpeta in _carpetas_de(anime):
        return False
    if not anime.get('local_path'):
        anime['local_path'] = carpeta
    else:
        anime.setdefault('local_paths', []).append(carpeta)
    return True


def _quitar_carpeta(anime: dict, carpeta: str) -> bool:
    """Quita una carpeta sin perder las restantes ni cambiar su orden."""
    carpetas = _carpetas_de(anime)
    if carpeta not in carpetas:
        return False
    restantes = [p for p in carpetas if p != carpeta]
    if restantes:
        anime['local_path'] = restantes[0]
        if len(restantes) > 1:
            anime['local_paths'] = restantes[1:]
        else:
            anime.pop('local_paths', None)
    else:
        anime.pop('local_path', None)
        anime.pop('local_paths', None)
    return True


def _escanear_carpetas(anime: dict) -> list:
    """Episodios locales de TODAS sus carpetas. Gana la primera carpeta en los empates."""
    carpetas = _carpetas_de(anime)
    if len(carpetas) == 1:
        return _scan_local_episodes(carpetas[0], anime.get('episode_overrides', {}))
    fusion = {}
    for carpeta in carpetas:
        for ep in _scan_local_episodes(carpeta, anime.get('episode_overrides', {})):
            fusion.setdefault((ep.get('ep_type', 'episode'), ep['num']), ep)
    return [fusion[k] for k in sorted(fusion, key=lambda t: (t[0] != 'episode', t[1]))]


def _buscar_video(anime: dict, episode: int, subpath: str = '') -> str:
    """El fichero de un episodio, mire donde mire la serie.

    Primero se busca la coincidencia EXACTA por número en TODAS las carpetas, y sólo después se
    permite adivinar. El orden importa cuando una serie vive en dos discos: NIPPON SANGOKU tenía
    el ep 1 en `C:/…/Downloads` y los eps 2 y 3 en `D:/…`, y como la primera carpeta contenía un
    único fichero, la regla «un solo vídeo vale para cualquier episodio» —que existe para las
    películas— se disparaba ahí y devolvía el ep 1 para TODOS. Se veía como tres miniaturas
    clonadas, pero lo que estaba roto de verdad es que darle a reproducir el 2 ponía el 1.

    Una conjetura en la primera carpeta no puede ganarle a una coincidencia exacta en la segunda.
    """
    carpetas = _carpetas_de(anime)
    for carpeta in carpetas:
        video = _find_video(carpeta, episode, subpath, adivinar=False)
        if video:
            return video
    # Nadie lo tiene por número. Adivinar sólo vale con UNA carpeta: con varias, «aquí hay un solo
    # fichero» no dice nada de la serie entera — es justo la lectura que causó el clonado.
    if len(carpetas) == 1:
        return _find_video(carpetas[0], episode, subpath)
    return ''


# Los campos de ESTADO del usuario: al fundir dos entradas se unen, nunca se pisan.
_LIB_DICTS = ('episodes', 'watched', 'positions', 'durations', 'episode_overrides', 'batch_files')


def _fundir_entradas(base: dict, otra: dict) -> dict:
    """Une `otra` dentro de `base` (que manda en los conflictos)."""
    for campo in _LIB_DICTS:
        unido = {**(otra.get(campo) or {}), **(base.get(campo) or {})}
        if unido:
            base[campo] = unido
    for carpeta in _carpetas_de(otra):     # las carpetas se SUMAN, no se eligen
        _anadir_carpeta(base, carpeta)
    for campo, valor in otra.items():
        if campo not in _LIB_DICTS and campo != 'local_paths' and not base.get(campo):
            base[campo] = valor
    for campo in ('last_watched_at', 'last_ep'):
        base[campo] = max(int(base.get(campo) or 0), int(otra.get(campo) or 0))
    marcas = [int(e.get('added_at') or 0) for e in (base, otra) if e.get('added_at')]
    if marcas:
        base['added_at'] = min(marcas)   # cuándo entró de verdad, no cuándo se clonó
    return base


def _canonizar_lib(lib: dict) -> bool:
    """Una obra = UNA entrada, clavada por `al_id`. Devuelve si cambió algo.

    Repara las duplicadas que ya estén en disco y desactiva la bomba de las entradas
    clavadas por mal_id, que son las que la generan.
    """
    salida: dict = {}
    cambiado = False
    for k, v in lib.items():
        canon = str(v['al_id']) if v.get('al_id') else k
        if canon in salida:
            # Manda la entrada con carpeta local; en empate, la que más episodios tenga.
            a, b = salida[canon], v
            if not a.get('local_path') and (b.get('local_path')
                                            or len(b.get('episodes') or {}) > len(a.get('episodes') or {})):
                a, b = b, a
            salida[canon] = _fundir_entradas(a, b)
            cambiado = True
        else:
            salida[canon] = v
            cambiado = cambiado or canon != k
    if cambiado:
        lib.clear()
        lib.update(salida)
    return cambiado

# ── Watch history ──────────────────────────────────────────────────────────────

def _history_path() -> _Path:
    from api.runtime import manga_dir
    return _Path(manga_dir()) / 'watch_history.json'

def _history_read() -> list:
    """Las entradas recientes (el fichero vivo). Para la serie COMPLETA, `history_store.read_all`.

    El truncado a 500 que había aquí NO era un margen de sobra: medido el 2026-07-31 el fichero
    estaba exactamente en 500 y cubría 21 días, así que cada episodio nuevo borraba el más viejo.
    Ahora `history_store` archiva por meses en vez de tirar. Ver `api/history_store.py`.
    """
    from api import history_store
    return history_store.read_live('watch')

def _history_append(anime_id: str, title: str, episode: int, cover: str = ''):
    from api import history_store
    history_store.append('watch', {
        'anime_id': anime_id,
        'title': title,
        'episode': episode,
        'cover': cover,
    }, dedup_keys=('anime_id', 'episode'))

# ── Subtitle helpers ───────────────────────────────────────────────────────────

_SUBTITLE_EXTS = {'.srt', '.ass', '.ssa', '.sub', '.vtt'}

_ES_SIDE_EXTS = ('.srt', '.ass', '.ssa', '.vtt')
# Carpeta -> (mtime, {nombres de marcador/sidecar ES}). Sólo se guardan los nombres que CASAN
# con el patrón, no el listado entero: son un puñado de cadenas por carpeta, no un índice.
_es_marks: dict = {}
_es_marks_at: dict = {}      # carpeta -> última vez que se comprobó su mtime
_ES_MARKS_MAX = 256          # cota dura: es un caché de conveniencia, no una fuente de verdad


def _es_marks_in(folder: str) -> set:
    """Marcadores/sidecars ES de una carpeta, con UN listdir cacheado por mtime.

    Antes se hacían hasta 5 `os.path.exists` POR EPISODIO. El docstring decía "barato (solo
    stat)" — falso sobre DrvFS/9p, donde cada stat cuesta ~0,7 ms: con 601 episodios eran ~3000
    stats = **2,1 s en CADA carga** de /api/anime/library (y el front la repite cada 15 s
    mientras descargas). Un listdir por carpeta es O(carpetas) en vez de O(episodios×5).

    El TTL importa tanto como el listdir: sin él se hacía un `os.stat` por EPISODIO sólo para
    mirar el mtime (601 stats ≈ 440 ms), y el arreglo se quedaba a medias. Misma constante que
    `_scan_local_episodes`, que resuelve exactamente el mismo problema unas líneas más abajo.

    Invalida por mtime de la carpeta: crear/borrar un `.spa.ass` o un `.es_injected` lo cambia,
    que es justo lo que hay que detectar.
    """
    now = time.time()
    hit = _es_marks.get(folder)
    if hit and now - _es_marks_at.get(folder, 0) < _FOLDER_MTIME_TTL:
        return hit[1]
    try:
        mt = os.stat(folder).st_mtime
    except OSError:
        return set()
    _es_marks_at[folder] = now
    if hit and hit[0] == mt:
        return hit[1]
    try:
        names = {n for n in os.listdir(folder)
                 if n.endswith('.es_injected') or '.spa.' in n}
    except OSError:
        names = set()
    if len(_es_marks) >= _ES_MARKS_MAX:
        _es_marks.clear(); _es_marks_at.clear()
    _es_marks[folder] = (mt, names)
    return names


def _es_sub_injected(video_path: str) -> bool:
    """True si ESTE reproductor inyectó subs en español a este vídeo:
    marcador '.es_injected' (inyección a MKV, histórico) o sidecar '<base>.spa.<ext>' (lo que
    se escribe hoy). NO cuenta pistas ES que ya venían dentro del contenedor original."""
    try:
        names = _es_marks_in(os.path.dirname(video_path))
        if not names:
            return False
        base = os.path.basename(os.path.splitext(video_path)[0])
        if base + '.es_injected' in names:
            return True
        return any(base + '.spa' + e in names for e in _ES_SIDE_EXTS)
    except OSError:
        pass
    return False


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

def _tmdb_key():
    from api.config_store import get_secret
    return get_secret('TMDB_API_KEY')


def _norm_title(s):
    return re.sub(r'[^a-z0-9]', '', (s or '').lower())


def _anilist_post(query, variables, tries=4):
    """POST to AniList. The shared resilient client honors AniList's rate limit
    (HTTP 429 + Retry-After) and proactively spaces requests — AniList's degraded
    limit is ~30 req/min, so a single backfill can hit it; back off and retry
    instead of silently dropping the entry."""
    return _rhttp.post(_ANILIST, json={'query': query, 'variables': variables},
                       timeout=10, retries=tries)


def _clean_synopsis(text, limit=320):
    """AniList descriptions come with <br>/<i> markup and source notes; strip tags,
    drop the trailing '(Source: …)' credit and collapse whitespace into one paragraph."""
    if not text:
        return ''
    t = re.sub(r'<br\s*/?>', ' ', text)
    t = re.sub(r'<[^>]+>', '', t)
    t = re.sub(r'\(Source:.*?\)', '', t, flags=re.I | re.S)
    t = re.sub(r'\s+', ' ', t).strip()
    if len(t) > limit:
        t = t[:limit].rsplit(' ', 1)[0].rstrip(' .,;:') + '…'
    return t


def _anilist_enrich(al_id):
    """Full metadata for a library entry: episodes, format, titles, genres, season,
    the high-res cover (extraLarge), the wide hero bannerImage and a short synopsis."""
    q = '''query($id:Int){Media(id:$id,type:ANIME){
        episodes format status season seasonYear
        title{romaji english}
        coverImage{ extraLarge large medium }
        bannerImage genres tags{ name rank }
        description(asHtml:false)
    }}'''
    r = _anilist_post(q, {'id': int(al_id)})
    return (r.json().get('data') or {}).get('Media') or {} if r is not None else {}


_syn_cache: dict = {}   # al_id → sinopsis completa


@anime_bp.route('/synopsis/<int:al_id>')
def anime_synopsis(al_id):
    """Sinopsis COMPLETA para la pestaña Detalles — la que guarda library está
    recortada a 320 chars (_clean_synopsis) para las cards. Aquí se conservan
    los saltos de párrafo (el CSS del detalle usa white-space: pre-line)."""
    if al_id in _syn_cache:
        return jsonify({'synopsis': _syn_cache[al_id]})
    q = 'query($id:Int){Media(id:$id,type:ANIME){description(asHtml:false)}}'
    r = _anilist_post(q, {'id': int(al_id)})
    desc = ((((r.json().get('data') or {}).get('Media') or {}).get('description'))
            if r is not None else '') or ''
    t = re.sub(r'<br\s*/?>', '\n', desc)
    t = re.sub(r'<[^>]+>', '', t)
    t = re.sub(r'\(Source:.*?\)', '', t, flags=re.I | re.S)
    t = re.sub(r'[ \t]+', ' ', t)
    t = re.sub(r'\n{3,}', '\n\n', t).strip()
    if t:
        _syn_cache[al_id] = t
    return jsonify({'synopsis': t})


# ── Preview del hero (clip mudo estilo Netflix, 100% local) ──────────────────
from api.runtime import DATA_ROOT as _DATA_ROOT, write_json_atomic
_PREVIEW_DIR = _DATA_ROOT / '_previews'
_preview_lock = threading.Lock()


@anime_bp.route('/preview', methods=['POST'])
def anime_preview():
    """Genera (una vez) un clip corto sin audio de un episodio para reproducirlo
    en mute en el hero del detalle. Body: como /play ({anime_id, episode,
    local_path?/info_hash?}). Devuelve {url} servible por <video src>."""
    data = request.get_json(silent=True) or {}
    video, err = resolve_episode_video(data)
    if err:
        return jsonify({'error': err[0]}), err[1]
    key = re.sub(r'[^\w.-]', '_', f"{data.get('anime_id', 'x')}_{data.get('episode', 0)}")
    out = _PREVIEW_DIR / f'{key}_hq.mp4'   # _hq: invalida los previews viejos de 640px
    if not out.exists():
        _PREVIEW_DIR.mkdir(parents=True, exist_ok=True)
        try:
            pr = subprocess.run(['ffprobe', '-v', 'error', '-print_format', 'json',
                                 '-show_format', video], capture_output=True, text=True, timeout=20)
            dur = float((json.loads(pr.stdout or '{}').get('format') or {}).get('duration') or 0)
        except Exception:
            dur = 0
        # arrancar pasado el OP (~30%), acotado; episodios cortos → desde el inicio
        start = min(max(dur * 0.3, 60), 360) if dur > 120 else 5
        with _preview_lock:
            if not out.exists():
                tmp = out.with_suffix('.tmp.mp4')
                # Hero preview en buena calidad: hasta 1080p (sin sobre-escalar fuentes menores)
                # y CRF bajo → nítido al ocupar todo el hero. Sigue mudo, 14 s, cacheado una vez.
                r = subprocess.run(
                    ['ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'error', '-y',
                     '-ss', str(int(start)), '-t', '14', '-i', video,
                     '-an', '-sn', '-dn', '-map', '0:v:0',
                     '-vf', "scale='min(1920,iw)':-2,format=yuv420p",
                     '-c:v', 'libx264', '-preset', 'faster', '-crf', '20',
                     '-pix_fmt', 'yuv420p', '-movflags', '+faststart', str(tmp)],
                    capture_output=True, timeout=180)
                if r.returncode != 0 or not tmp.exists():
                    return jsonify({'error': 'no se pudo generar el preview'}), 500
                tmp.rename(out)
    return jsonify({'ok': True, 'url': f'/api/anime/preview_file/{out.name}'})


@anime_bp.route('/preview_file/<fn>')
def anime_preview_file(fn):
    f = (_PREVIEW_DIR / fn).resolve()
    if not str(f).startswith(str(_PREVIEW_DIR.resolve())) or not f.is_file():
        return 'not found', 404
    return send_file(str(f), max_age=86400)


def _tmdb_images(tmdb_id, media_type='tv'):
    """For a known TMDB tv/movie id pick the best text-free wide backdrop, the
    best English logo (transparent PNG title treatment, Crunchyroll-style), and
    the best poster (TMDB posters are typically official key art scanned/exported
    at much higher resolution than AniList's coverImage, which tops out around
    460x650 even at 'extraLarge'). Returns {backdrop, logo, poster} (any may be
    None — not every show has poster art uploaded to TMDB)."""
    out = {'backdrop': None, 'logo': None, 'poster': None}
    try:
        imgs = _http.get(
            f"https://api.themoviedb.org/3/{media_type}/{tmdb_id}/images",
            params={'api_key': _tmdb_key(), 'include_image_language': 'en,null'}, timeout=8).json()
        backdrops = imgs.get('backdrops') or []
        textless = [b for b in backdrops if b.get('iso_639_1') is None]
        pool = textless or backdrops
        if pool:
            best = max(pool, key=lambda b: (b.get('vote_average') or 0, b.get('width') or 0))
            # `original`, no `w1280`: este backdrop se pinta a SANGRE (hero de Mi Anime y de la
            # Portada). En un monitor de 2560 un w1280 se estira al doble y se ve blando —
            # justo lo que delata una imagen mal servida. El proxy de imágenes lo cachea en
            # disco, así que el peso extra se paga una vez por serie, no por visita.
            out['backdrop'] = f"https://image.tmdb.org/t/p/original{best['file_path']}"
        logos = imgs.get('logos') or []
        # Prefer English, then PNG (transparent) over SVG, then most-voted.
        eng = [l for l in logos if l.get('iso_639_1') == 'en'] or logos
        if eng:
            blogo = max(eng, key=lambda l: ((l.get('file_path') or '').lower().endswith('.png'),
                                            l.get('vote_average') or 0))
            # `original`: el logo se pinta hasta 42 rem de ancho sobre el hero de la Portada y de
            # Mi Anime. Es un PNG con transparencia y trazos finos — es justo donde peor se ve un
            # reescalado. Pesa poco (son letras, no una foto) y el proxy lo cachea.
            out['logo'] = f"https://image.tmdb.org/t/p/original{blogo['file_path']}"
        posters = imgs.get('posters') or []
        eng_p = [p for p in posters if p.get('iso_639_1') in (None, 'en')] or posters
        if eng_p:
            bp = max(eng_p, key=lambda p: (p.get('vote_average') or 0, p.get('width') or 0))
            out['poster'] = f"https://image.tmdb.org/t/p/w780{bp['file_path']}"
    except Exception:
        pass
    return out


# AniList/MAL titles often carry a trailing "2nd Season" / "Cour 3" / "Part 2" /
# bare-digit marker that TMDB doesn't use — it groups every season of a show under
# one base entry. TMDB's own fuzzy search is inconsistent about ignoring these
# suffixes (some match fine, some return zero results), so we strip them ourselves
# and retry with the bare show name.
_SEASON_SUFFIX_RE = re.compile(
    r'\s*[:\-–]?\s*(?:'
    r'\d+(?:st|nd|rd|th)\s+season\b.*$'
    r'|season\s*\d+$'
    r'|\d+(?:st|nd|rd|th)\s+cour\b.*$'
    r'|cour\s*\d+$'
    r'|\d+(?:st|nd|rd|th)\s+stage\b.*$'
    r'|part\s*\d+$'
    r'|\d+$'
    r')',
    re.IGNORECASE,
)

# Matches an explicit "2nd Season" / "Season 3" marker (the only sequel-suffix
# forms that map onto a TMDB "season number" — "Cour"/"Part"/"Stage" splits and
# bare trailing digits don't reliably correspond to a TMDB season index, so we
# leave those on the show-level poster rather than guess wrong).
_SEASON_NUM_RE = re.compile(r'(\d+)(?:st|nd|rd|th)\s+season\b|season\s*(\d+)\b', re.IGNORECASE)


def _extract_season_number(title_en, title_romaji):
    """Used only as a secondary signal (see `_tmdb_art`) — when TMDB's own
    air-year season matching (`_tmdb_season_by_year`) comes up empty, an
    explicit "2nd Season"/"Season 3" marker in the title still tells us this
    is *definitely* a sequel, so we know not to trust the show's default
    poster rather than silently keeping a likely-wrong one."""
    for t in (title_en, title_romaji):
        if not t:
            continue
        m = _SEASON_NUM_RE.search(t)
        if m:
            return int(m.group(1) or m.group(2))
    return None


def _tmdb_season_by_year(tmdb_id, year):
    """Pick whichever TMDB season of a multi-season show aired closest to
    AniList's season_year, and return (season_number, its own poster). This
    is far more general than parsing a "2nd Season"/"Part 2" marker out of
    the title — it works for sequels with a completely different name too
    (e.g. "Attack on Titan: The Final Season" has no digit/season-number
    text at all), as long as we know roughly when it aired. Returns
    (None, None) when no season's air year is within 1 year of ours — better
    to fall back to AniList's own cover than guess the wrong season."""
    if not year:
        return None, None
    try:
        r = _http.get(f"https://api.themoviedb.org/3/tv/{tmdb_id}",
                      params={'api_key': _tmdb_key()}, timeout=8).json()
        seasons = [s for s in (r.get('seasons') or [])
                   if (s.get('season_number') or 0) >= 1 and s.get('air_date')]
        if not seasons:
            return None, None
        best = min(seasons, key=lambda s: abs(int(s['air_date'][:4]) - int(year)))
        if abs(int(best['air_date'][:4]) - int(year)) > 1:
            return None, None
        poster = f"https://image.tmdb.org/t/p/w780{best['poster_path']}" if best.get('poster_path') else None
        return best.get('season_number'), poster
    except Exception:
        return None, None


def _candidate_titles(title_en, title_romaji):
    """Ordered, de-duplicated list of (title, is_raw) variants to try against TMDB:
    the raw titles first, then each with a trailing season/cour/part/digit marker
    stripped, then each truncated at the first ': ' (subtitle separator) — covers
    sequels named either way ("X 2nd Season", "X: Subtitle"). `is_raw` marks the
    unmodified title — TMDB only stores one wide backdrop per *show*, usually art
    from whichever season it was catalogued with, so a match that required
    stripping a sequel marker means that backdrop is very likely the wrong
    season's; only a raw-title match is trusted for the backdrop."""
    out, seen = [], set()
    for raw in (title_en, title_romaji):
        if not raw:
            continue
        variants = [(raw, True)]
        stripped = _SEASON_SUFFIX_RE.sub('', raw).strip(' -:')
        if stripped and stripped.lower() != raw.lower():
            variants.append((stripped, False))
        if ': ' in raw:
            head = raw.split(': ', 1)[0].strip()
            if head:
                variants.append((head, False))
        for t, is_raw in variants:
            if t.lower() not in seen:
                seen.add(t.lower()); out.append((t, is_raw))
    return out


def _tmdb_search_one(media_type, name, year):
    """One TMDB search/<tv|movie> call. Returns (id, exact) for the first result
    whose normalized title overlaps the query and whose air/release year isn't
    *later* than ours (a sequel always airs on/after its original, so this rejects
    an unrelated show that happens to share a name but came out afterwards).
    `exact` means the query matched the candidate's title verbatim (not just an
    overlap) — used upstream to tell a season-1/standalone match from a sequel
    whose query merely *contains* the base show's name."""
    title_field = 'name' if media_type == 'tv' else 'title'
    date_field = 'first_air_date' if media_type == 'tv' else 'release_date'
    orig_field = 'original_name' if media_type == 'tv' else 'original_title'
    try:
        r = _http.get(f'https://api.themoviedb.org/3/search/{media_type}',
                      params={'api_key': _tmdb_key(), 'query': name, 'include_adult': 'false'}, timeout=8)
        target = _norm_title(name)
        for res in ((r.json() or {}).get('results') or [])[:5]:
            cand = _norm_title(res.get(title_field))
            cand_o = _norm_title(res.get(orig_field))
            ry = (res.get(date_field) or '')[:4]
            year_ok = (not year) or (not ry) or int(year) >= int(ry) - 1
            match = bool(target) and (target in cand or cand in target or target in cand_o or cand_o in target)
            if year_ok and match:
                return res['id'], (target == cand or target == cand_o)
    except Exception:
        pass
    return None, False


def _tmdb_art(title_en, title_romaji, year, known_id=None, known_type='tv', fmt=None):
    """TMDB hero art for an anime: {tmdb_id, tmdb_type, backdrop, logo,
    is_base_title}, or None when no confident match. When known_id is given we skip
    the search and go straight to that title's images (used to backfill logos for
    already-matched entries). Search order is TV-first by default (the vast
    majority of anime), but `fmt` (AniList's own format: MOVIE/OVA/ONA/SPECIAL/TV)
    flips it to movie-first for non-TV formats — without this, a movie whose title
    contains its parent show's name (e.g. "Dragon Maid: A lonely dragon wants to be
    loved") gets fuzzy-matched to the TV show during the 'tv' pass before 'movie'
    is ever tried, permanently locking the wrong tmdb_id/tmdb_type. Within each
    media type, an *exact* match on an unmodified title is preferred (and marked
    `is_base_title`) over a looser/stripped-suffix match — TMDB keeps only one
    backdrop per show, generally art from the season it was catalogued with, so
    only a season-1/standalone match is trusted for it."""
    if not _tmdb_key():
        return None
    # Title-text season markers ("2nd Season", "Part 2"...) only catch sequels
    # that are *named* that way — plenty aren't ("Attack on Titan: The Final
    # Season", or any sequel with a wholly different subtitle). `year` (from
    # AniList's season_year, always present) is a far more general signal:
    # match it against TMDB's own per-season air_date instead of the title.
    text_season_num = _extract_season_number(title_en, title_romaji)

    def _with_season_poster(result):
        # Multi-season anime share one TMDB show id with one show-level poster
        # (and many anime aren't split into per-season entries on TMDB at all —
        # every episode across every season gets lumped under one "Season 1").
        # Always try to pin down *this* season's own poster by air year; only
        # fall back to dropping the poster entirely (caller then uses AniList's
        # own season-correct cover) when the title text confirms this is
        # clearly a sequel and the year match still came up empty.
        if result.get('tmdb_type') != 'tv':
            return result
        _, poster = _tmdb_season_by_year(result['tmdb_id'], year)
        if poster:
            result['poster'] = poster
        elif text_season_num and text_season_num >= 2:
            result['poster'] = None
        return result

    if known_id:
        return _with_season_poster({'tmdb_id': known_id, 'tmdb_type': known_type, **_tmdb_images(known_id, known_type)})
    names = _candidate_titles(title_en, title_romaji)
    media_order = ('movie', 'tv') if (fmt or '').upper() in ('MOVIE', 'OVA', 'ONA', 'SPECIAL') else ('tv', 'movie')
    for media_type in media_order:
        fallback = None
        for name, is_raw in names:
            tid, exact = _tmdb_search_one(media_type, name, year)
            time.sleep(0.15)
            if not tid:
                continue
            if is_raw and exact:
                art = _tmdb_images(tid, media_type)
                if art['backdrop'] or art['logo']:
                    return _with_season_poster({'tmdb_id': tid, 'tmdb_type': media_type, 'is_base_title': True, **art})
            elif fallback is None:
                fallback = tid
        if fallback:
            art = _tmdb_images(fallback, media_type)
            if art['backdrop'] or art['logo']:
                return _with_season_poster({'tmdb_id': fallback, 'tmdb_type': media_type, 'is_base_title': False, **art})
    return None


_airing_cache = {'ts': 0, 'data': {}}
_AIRING_TTL = 1800  # 30 min — airing data only changes when an episode actually airs


def _fetch_airing(al_ids):
    """For the given AniList ids return {al_id: {status, next_episode, next_airing_at,
    last_episode, last_aired_at}} via two batched queries.

    Pass 1: nextAiringEpisode to know which episode (N) is next.
    Pass 2: airingSchedules for episode N-1 per show to get the EXACT time it aired.
    The estimate (next_at - 604800) was wrong for bi-weekly/monthly shows — it put
    last_aired_at in the future, causing the hero banner to show "hace un momento"."""
    now = time.time()
    if now - _airing_cache['ts'] < _AIRING_TTL and _airing_cache['data']:
        return _airing_cache['data']
    ids = [int(x) for x in al_ids if x]
    out = {}
    if not ids:
        return out

    # Pass 1 — next airing episode per show
    q1 = '''query($ids:[Int],$p:Int){Page(page:$p,perPage:50){
        pageInfo{hasNextPage}
        media(id_in:$ids,type:ANIME){
            id status
            nextAiringEpisode{episode airingAt}
        }
    }}'''
    page = 1
    # next_ep_map: media_id -> next_ep (for shows with a known upcoming episode)
    next_ep_map = {}
    while True:
        r = _anilist_post(q1, {'ids': ids, 'p': page})
        if r is None:
            break
        pg = (r.json().get('data') or {}).get('Page') or {}
        for m in (pg.get('media') or []):
            nae = m.get('nextAiringEpisode') or {}
            next_ep = nae.get('episode')
            next_at = nae.get('airingAt')
            last_ep = (next_ep - 1) if (next_ep and next_ep > 1) else None
            out[m['id']] = {
                'status':         m.get('status'),
                'next_episode':   next_ep,
                'next_airing_at': next_at,
                'last_episode':   last_ep,
                'last_aired_at':  None,  # filled in pass 2
            }
            if last_ep:
                next_ep_map[m['id']] = next_ep
        if not (pg.get('pageInfo') or {}).get('hasNextPage'):
            break
        page += 1
        time.sleep(1.0)

    # Pass 2 — get actual airingAt for each show's last episode (episode N-1).
    # Query the root-level airingSchedules connection filtered by mediaId+episode pairs.
    # The cross-product filter (mediaId_in × episode_in) may over-select, so we
    # validate each entry by checking it matches the show's expected last episode.
    if next_ep_map:
        want_ids = list(next_ep_map.keys())
        want_eps = list({ep - 1 for ep in next_ep_map.values() if ep > 1})
        q2 = '''query($ids:[Int],$eps:[Int]){Page(perPage:50){
            airingSchedules(mediaId_in:$ids,episode_in:$eps,notYetAired:false){
                mediaId episode airingAt
            }
        }}'''
        r2 = _anilist_post(q2, {'ids': want_ids, 'eps': want_eps})
        if r2 is not None:
            schedules = ((r2.json().get('data') or {}).get('Page') or {}).get('airingSchedules') or []
            for s in schedules:
                mid = s.get('mediaId')
                ep  = s.get('episode')
                at  = s.get('airingAt')
                if mid and ep and at and mid in next_ep_map and ep == next_ep_map[mid] - 1:
                    out[mid]['last_aired_at'] = at

        # Fallback for shows whose schedule entry wasn't returned (AniList data gaps):
        # estimate as next_at - 604800, but clamp to now so we never put a future
        # timestamp in last_aired_at (which caused "hace un momento" on bi-weekly shows).
        for mid, next_ep in next_ep_map.items():
            if out[mid]['last_aired_at'] is None:
                next_at = out[mid].get('next_airing_at')
                if next_at:
                    estimated = next_at - 604800
                    out[mid]['last_aired_at'] = min(estimated, now)

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
        # `gen_tags` entra en la guarda a propósito: las obras ya enriquecidas no las tienen, y
        # sin esto el filtro por género se quedaría sin «Yuri», «Isekai» y compañía en todo lo que
        # ya estaba en la biblioteca. Es UNA pasada de más, espaciada 1,5 s como el resto.
        if not (v.get('banner') and v.get('genres') and v.get('total_episodes') and v.get('cover_xl')
                and v.get('synopsis') and v.get('gen_tags') is not None):
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
                    # Géneros ESPECÍFICOS (GL/BL, Isekai, Escolar…) que los 18 `genres` de AniList
                    # no distinguen. Van en su propio campo: `genres` lo pintan las tarjetas y no
                    # queremos cambiar lo que enseñan, sólo por qué se puede filtrar.
                    if v.get('gen_tags') is None: v['gen_tags'] = pick_tags(m.get('tags')); changed = True
                    if m.get('season') and not v.get('season'): v['season'] = m['season']; changed = True
                    if m.get('seasonYear') and not v.get('season_year'): v['season_year'] = m['seasonYear']; changed = True
                    if m.get('description') and not v.get('synopsis'):
                        v['synopsis'] = _clean_synopsis(m['description']); changed = True
                    # AniList banner is the default; never overwrite a TMDB one,
                    # nor a banner the user manually pinned via the picker.
                    if m.get('bannerImage') and v.get('banner_source') != 'tmdb' and v.get('banner') != m['bannerImage'] \
                            and not v.get('banner_locked'):
                        v['banner'] = m['bannerImage']; v['banner_source'] = 'anilist'; changed = True
                time.sleep(1.5)  # AniList degraded rate limit is ~30 req/min
            except Exception:
                pass

        # TMDB art — wide backdrop + logo PNG (Crunchyroll-style). First pass searches
        # by title; a second pass backfills the logo for entries matched before logos
        # were fetched (tmdb_id known, logo still missing).
        if _tmdb_key():
            first_try = not v.get('_tmdb_tried')
            relogo = (not first_try) and v.get('tmdb_id') and not v.get('logo') and not v.get('_logo_tried')
            # Backfill a higher-res poster for entries matched before poster-fetching
            # existed, same pattern as relogo above — gated by its own _cover_tried
            # flag so a show with no TMDB poster art isn't re-checked every backfill run.
            recover = (not first_try) and v.get('tmdb_id') and v.get('cover_source') != 'tmdb' \
                and not v.get('_cover_tried') and not v.get('cover_locked')
            # Multi-season anime share one TMDB show id, so cover_source=='tmdb'
            # from an earlier pass may just be a different season's poster reused
            # for this entry. Re-check once per TV entry (not gated on the title
            # containing a literal "2nd Season" marker — _tmdb_art now matches by
            # AniList's season_year against TMDB's per-season air_date, which
            # also catches sequels named completely differently, e.g. "...: The
            # Final Season") — gated by its own flag so it only runs once.
            # cover_locked means the user manually picked a cover via the cover-options
            # picker — never let an automatic re-check overwrite that choice.
            reseason = (not first_try) and v.get('tmdb_id') and v.get('tmdb_type', 'tv') == 'tv' \
                and not v.get('_season_tried') and not v.get('cover_locked')
            if first_try or relogo or recover or reseason:
                art = _tmdb_art(v.get('title_english') or v.get('title'), v.get('title_romaji'),
                                v.get('season_year'), known_id=v.get('tmdb_id') if (relogo or recover or reseason) else None,
                                known_type=v.get('tmdb_type', 'tv'), fmt=v.get('format'))
                if first_try: v['_tmdb_tried'] = True
                if relogo:    v['_logo_tried'] = True
                if recover or reseason: v['_cover_tried'] = True
                if reseason:  v['_season_tried'] = True
                changed = True
                if art:
                    if art.get('tmdb_id'): v['tmdb_id'] = art['tmdb_id']
                    if art.get('tmdb_type'): v['tmdb_type'] = art['tmdb_type']
                    if art.get('logo'): v['logo'] = art['logo']
                    # AniList's coverImage tops out around 460x650 even at 'extraLarge';
                    # TMDB posters are usually official key art at much higher resolution
                    # (Crunchyroll-style), so prefer it for the card cover when found.
                    if art.get('poster'):
                        v['cover'] = art['poster']; v['cover_source'] = 'tmdb'
                    elif reseason and v.get('cover_xl'):
                        # The reseason recheck dropped an unconfirmed poster (TMDB
                        # often doesn't split anime into per-season entries at all,
                        # so there was nothing to confirm this entry's exact season
                        # against) — AniList's own cover is at least guaranteed to
                        # be the right season, even though it's lower-res.
                        v['cover'] = v['cover_xl']; v['cover_source'] = 'anilist'
                    # TMDB keeps one backdrop per *show*, usually art from whichever season
                    # it was catalogued with. Only adopt it when the match was on the raw
                    # title (season 1 / a non-serialized film) — a match that needed the
                    # sequel-suffix stripped means that backdrop is very likely the wrong
                    # season; keep AniList's own (season-specific) banner for those instead.
                    if first_try and art.get('backdrop') and art.get('is_base_title') and not v.get('banner_locked'):
                        v['banner'] = art['backdrop']; v['banner_source'] = 'tmdb'

        # Warm the disk image cache for whatever just got resolved, so the detail
        # page's hero/poster/logo are already on disk by the time the user opens
        # it instead of paying the TMDB/AniList CDN round trip on first paint.
        for _u in (v.get('banner'), v.get('banner_detail'), v.get('logo'), v.get('cover')):
            _warm_img(_u)

        # Persist periodically so partial progress survives interruptions/restarts.
        if changed and processed and processed % 8 == 0:
            _lib_write(lib)

    if changed:
        _lib_write(lib)
    # Al final: el backfill acaba de resolver `al_id` para las entradas que sólo tenían
    # mal_id, así que es aquí donde se pueden reclavar y fundir los clones ya en disco.
    # Sobre una RELECTURA: este hilo puede llevar minutos y algo se ha podido dar de alta.
    fresca = _lib_read()
    if _canonizar_lib(fresca):
        print('[anime] biblioteca canonizada: clones unificados', flush=True)
        _lib_write(fresca)
    _backfill_done = True


# ── Scan paths (local anime folders) ─────────────────────────────────────────

def _scanpaths_path() -> _Path:
    return _Path(os.environ.get('MANGA_DIR', str(_Path.home() / 'MangaLibrary'))) / 'anime_scan_paths.json'

def _scanpaths_read() -> dict:
    p = _scanpaths_path()
    if not p.exists():
        return {'paths': [], 'mappings': {}}     # sin rutas configuradas aún ≠ fallo
    try:
        return json.loads(p.read_text(encoding='utf-8'))
    except Exception as e:
        # Config corrupta: se degrada a "sin rutas" (comportamiento intacto), pero YA deja rastro
        # — antes esto perdía en silencio TODAS las carpetas de escaneo del usuario.
        record_error('anime', e, op='scanpaths_read', path=str(p))
        return {'paths': [], 'mappings': {}}

def _scanpaths_write(data: dict):
    _scan_cache.clear()          # cambió la lista de raíces: lo memoizado ya no vale
    write_json_atomic(_scanpaths_path(), data, indent=2, keep_backup=True)


def _anime_settings_path() -> _Path:
    return _Path(os.environ.get('MANGA_DIR', str(_Path.home() / 'MangaLibrary'))) / 'anime_settings.json'

def _anime_settings_read() -> dict:
    p = _anime_settings_path()
    if not p.exists():
        return {}                                # sin ajustes aún ≠ fallo
    try:
        return json.loads(p.read_text(encoding='utf-8'))
    except Exception as e:
        record_error('anime', e, op='anime_settings_read', path=str(p))
        return {}

def _anime_settings_write(data: dict):
    write_json_atomic(_anime_settings_path(), data, indent=2, keep_backup=True)


def _norm_scan_root(path: str) -> str:
    """Forma canónica de una raíz de escaneo: WSL, sin barra final.

    Se guarda YA normalizada porque si no, `E:\\Carpeta Anime` y `/mnt/e/Carpeta Anime` son dos
    entradas distintas para la misma carpeta y ninguna de las dos casa con los `mappings`, que
    siempre se escriben en forma WSL.
    """
    path = (path or '').strip().strip('"')
    if _is_wsl() and re.match(r'^[A-Za-z]:[/\\]', path):
        path = _win_to_wsl(path)
    return path.rstrip('/') or path


def _peek_dir(path: str):
    """(¿tiene vídeos sueltos?, [subcarpetas]) con UN solo `scandir`.

    Las dos preguntas se resuelven juntas a propósito: sobre DrvFS cada `scandir` cuesta de sobra
    como para pagarlo dos veces por carpeta (era la mitad de los 78 s que tardaba `D:\\`).
    """
    vids, subs = False, []
    try:
        with os.scandir(path) as it:
            for e in it:
                try:
                    if e.is_dir():
                        if not _skip_dir(e.name):
                            subs.append(e.path)
                    elif not vids and os.path.splitext(e.name)[1].lower() in _VIDEO_EXTS:
                        vids = True
                except OSError:
                    continue
    except PermissionError:
        pass                     # carpeta de sistema: "no puedo mirar" ≠ fallo, es lo esperado
    except OSError as e:
        record_error('anime', e, op='scan_list', path=path)
    return vids, sorted(subs)


# Un contenedor tiene MUCHAS obras dentro; una obra partida en temporadas tiene una o dos
# subcarpetas. Con 3 se separan los dos casos sin preguntar nada al usuario.
_CONTAINER_MIN = 3
_SCAN_MAX_DEPTH = 3
# Cada nivel que se baja son N `scandir` sobre DrvFS (~ms cada uno). Apuntar a la raíz de un disco
# entero es legítimo, así que el recorrido lleva presupuesto: sin él, `D:\` tardaba minutos.
_SCAN_MAX_DIRS = 600
_SKIP_DIRS = {'system volume information', 'recovery', 'msocache', 'config.msi', 'windows',
              'program files', 'program files (x86)', 'programdata', 'appdata', 'node_modules'}


def _skip_dir(name: str) -> bool:
    return name.startswith(('$', '.')) or name.lower() in _SKIP_DIRS


def _son_episodios(paths: list) -> bool:
    """¿Estas subcarpetas son EPISODIOS de una misma obra, en vez de obras distintas?

    Estructuralmente son idénticas (`X/<12 carpetas con vídeo>` puede ser un contenedor de 12
    obras o una obra con un episodio por carpeta), así que hay que mirar los NOMBRES: los
    episodios sólo se diferencian en un número. `[SphinxAnime] K0s2 - 01/02/03…` → una obra;
    `Chuunibyou / Dororo / Hyouka` → tres.
    """
    esqueletos = {re.sub(r'\d+', '#', _Path(p).name).strip() for p in paths}
    return len(esqueletos) == 1


def _walk_anime(subs: list, depth: int, budget: list) -> list:
    out = []
    for sub in subs:
        if budget[0] <= 0:
            break
        budget[0] -= 1
        vids, hijos = _peek_dir(sub)
        if vids:
            out.append(sub)                          # la carpeta ES la obra
            continue
        if depth <= 0 or not hijos:
            continue
        hijas = _walk_anime(hijos, depth - 1, budget)
        if not hijas:
            continue                                 # carpeta vacía o sin vídeos: no es nada
        # Muchas obras DISTINTAS dentro = contenedor, se devuelven ellas. Pocas, o todas con el
        # mismo nombre y un número (temporadas, episodios sueltos), = UNA obra: se devuelve la
        # carpeta madre, que es la que `_scan_local_episodes` sabe recorrer entera.
        if len(hijas) >= _CONTAINER_MIN and not _son_episodios(hijas):
            out.extend(hijas)
        else:
            out.append(sub)
    return out


# Recorrer `D:\` entero son cientos de `scandir` sobre DrvFS: el resultado se memoiza para que
# abrir el modal dos veces no lo pague dos veces. Se tira al añadir o quitar una raíz.
_scan_cache: dict = {}
_SCAN_TTL = 300


def _list_anime_folders(root: str, depth: int = _SCAN_MAX_DEPTH) -> list:
    """Carpetas de OBRA bajo `root`, bajando por los contenedores intermedios.

    La biblioteca del usuario no es plana: `E:/Carpeta Anime/Carpeta Animes/<28 obras>`. Mirando
    un solo nivel salían dos carpetas inútiles ("Carpeta Animes", "Peliculas") y ninguna obra, y
    en la raíz de un disco salían `$RECYCLE.BIN` y los discos de música. Se baja mientras la
    carpeta no tenga vídeos propios, y se para en cuanto los tiene: esa es la obra.
    """
    root = _norm_scan_root(root)
    hit = _scan_cache.get(root)
    if hit and time.time() - hit[0] < _SCAN_TTL:
        return hit[1]
    if not _Path(root).is_dir():
        return []
    out = _walk_anime(_peek_dir(root)[1], depth - 1, [_SCAN_MAX_DIRS])
    _scan_cache[root] = (time.time(), out)
    return out


_SHORT_VIDEO_SECS = 600  # < 10 min → auto-special

# Duration cache: path → (mtime, duration_secs)
# Keyed by absolute path; invalidated when file mtime changes.
# Persisted to disk so server restarts don't re-probe every file.
_dur_cache: dict = {}
_DUR_CACHE_PATH = _Path.home() / '.animanga_dur_cache.json'
_dur_cache_dirty = False
_dur_lock = threading.RLock()
_dur_save_active = False
_dur_generation = 0


def _load_dur_cache():
    global _dur_cache
    if not _DUR_CACHE_PATH.exists():
        _dur_cache = {}                          # primer arranque: aún no hay caché ≠ fallo
        return
    try:
        with open(_DUR_CACHE_PATH) as f:
            _dur_cache = {k: tuple(v) for k, v in json.load(f).items()}
    except Exception as e:
        # Es un caché (se re-mide al vuelo), así que degradar a {} es seguro; pero un caché
        # corrupto que se descarta en silencio esconde por qué se re-prueban todos los ficheros.
        record_error('anime', e, op='dur_cache_load', path=str(_DUR_CACHE_PATH))
        _dur_cache = {}


def _save_dur_cache(_claimed=False):
    """Persist duration measurements with one writer and a stable snapshot."""
    global _dur_cache_dirty, _dur_save_active
    if not _claimed:
        with _dur_lock:
            if _dur_save_active or not _dur_cache_dirty:
                return
            _dur_save_active = True
    while True:
        with _dur_lock:
            if not _dur_cache_dirty:
                _dur_save_active = False
                return
            generation = _dur_generation
            payload = {k: list(v) for k, v in _dur_cache.items()}
            path = _DUR_CACHE_PATH
        saved = False
        with swallow('anime', 'dur_cache_save', path=str(path)):
            write_json_atomic(path, payload, durable=False)
            saved = True
        with _dur_lock:
            if saved and generation == _dur_generation:
                _dur_cache_dirty = False
            if not saved or not _dur_cache_dirty:
                _dur_save_active = False
                return


_load_dur_cache()


def _video_duration(path: str) -> float:
    """Return video duration in seconds via ffprobe, cached by file mtime (persisted to disk)."""
    global _dur_cache_dirty, _dur_generation, _dur_save_active
    try:
        mtime = _Path(path).stat().st_mtime
    except Exception:
        return 0.0
    with _dur_lock:
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
    with _dur_lock:
        _dur_cache[path] = (mtime, dur)
        _dur_cache_dirty = True
        _dur_generation += 1
        schedule = not _dur_save_active
        if schedule:
            _dur_save_active = True
    # Flush cache in background to avoid blocking the request thread. One active writer drains
    # updates that arrive while it is writing; a read-only cache remains dirty for a later retry.
    if schedule:
        try:
            threading.Thread(target=_save_dur_cache, args=(True,), daemon=True).start()
        except Exception:
            with _dur_lock:
                _dur_save_active = False
    return dur


# Folder-level scan cache: wsl_path → (folder_mtime, overrides_key, episodes_list)
# Keyed by folder path; re-scans only when folder mtime or overrides change.
_scan_cache: dict = {}

# Most local anime folders live on /mnt/* (WSL2 DrvFS bridge to a Windows drive),
# where even a cheap stat()/iterdir() costs ~15-30ms — fine once, but
# /api/anime/library re-checks every local_path on every request (and the
# frontend polls it), so with ~45 folders that alone was ~0.7-1.3s per call.
# This TTL skips the freshness recheck entirely for recently-seen folders and
# just returns the last scan, trading a few seconds of staleness for making
# repeat page loads instant.
_FOLDER_MTIME_TTL = 10.0
_folder_checked_at: dict = {}


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


def _size_of(f) -> int:
    """Tamaño de un archivo ya localizado por el escaneo. Se toma AQUÍ, dentro del recorrido que
    de todos modos abre la carpeta, y viaja en el resultado cacheado: el endpoint sumaba
    `Path(ep['path']).stat().st_size` por episodio y eso eran 599 stats sobre DrvFS (~690 ms) en
    CADA carga de la biblioteca, incluida la que el front repite cada 15 s al descargar."""
    try:
        return f.stat().st_size
    except OSError:
        return 0


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

    overrides = overrides or {}
    overrides_key = str(sorted(overrides.items()))

    # Skip the filesystem entirely if we checked this folder recently — avoids
    # paying DrvFS stat latency again on every poll/page-load within the TTL.
    # Pasado el TTL, Stale-While-Revalidate: se sirve el caché YA y un hilo re-escanea.
    # Antes el recheck era síncrono → cada visita tras >10 s de reposo costaba ~1 s de
    # stats DrvFS (medido) justo en la vista de aterrizaje. El dato fresco llega en la
    # siguiente petición (el front re-pide cada 15 s mientras descarga, y en cada
    # navegación); un episodio nuevo tarda un poll en aparecer, no un segundo en cada open.
    now = time.time()
    cached = _scan_cache.get(wsl_path)
    if cached and cached[1] == overrides_key:
        if now - _folder_checked_at.get(wsl_path, 0) < _FOLDER_MTIME_TTL:
            return cached[2]
        if wsl_path not in _scan_refreshing:
            _scan_refreshing.add(wsl_path)
            def _refresh(path=wsl_path, ov=dict(overrides)):
                try:
                    _scan_local_episodes_sync(path, ov)
                finally:
                    _scan_refreshing.discard(path)
            threading.Thread(target=_refresh, daemon=True).start()
        return cached[2]
    return _scan_local_episodes_sync(wsl_path, overrides)


_scan_refreshing: set = set()


def invalidar_escaneo(carpeta: str) -> None:
    """Olvida el escaneo cacheado de una carpeta: el siguiente `/api/anime/library` la relee.

    🔴 **Es lo que faltaba para que un episodio recién horneado pase a ser EL episodio.** La
    resolución ya era correcta (`_prefiere_a4k` en `_find_video`, y `path`→horneada en el escaneo),
    pero el escaneo se sirve con Stale-While-Revalidate y un TTL de 10 s: justo después de hornear,
    la biblioteca seguía contestando con la lista de ANTES —sin `a4k`, con el original— y nadie
    avisaba a la interfaz. Desde fuera eso es exactamente «he escalado y no se ha guardado»: hay que
    salir de la ficha, esperar y volver a entrar para que aparezca.

    El que escribe el fichero es quien sabe que la carpeta cambió, así que lo dice en vez de dejar
    que se descubra por reloj.
    """
    _scan_cache.pop(carpeta, None)
    _folder_checked_at.pop(carpeta, None)


def _scan_local_episodes_sync(wsl_path: str, overrides: dict) -> list:
    """Escaneo real (bloqueante) de una carpeta; actualiza el caché. Lo llama el camino
    frío (sin caché) y el hilo de refresco del SWR de arriba."""
    overrides_key = str(sorted(overrides.items()))
    now = time.time()
    cached = _scan_cache.get(wsl_path)

    p = _Path(wsl_path)
    if not p.exists():
        return []

    fmtime = _folder_mtime(p)
    _folder_checked_at[wsl_path] = now
    if cached and cached[0] == fmtime and cached[1] == overrides_key:
        return cached[2]

    # Las horneadas con Anime4K no son episodios aparte: son OTRA VERSIÓN del mismo. Se sacan del
    # listado aquí y cada episodio recoge la suya más abajo (si no, el capítulo 1 salía dos veces y
    # el segundo se numeraba como el 2).
    files = sorted(f for f in p.rglob('*')
                   if f.is_file() and f.suffix.lower() in _VIDEO_EXTS and not _es_a4k(f))
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
            episodes.append({'num': sp_counter, 'path': str(f), 'ep_type': 'special',
                             'filename': f.name, 'size': _size_of(f)})
            continue

        ep_num = _parse_episode(f.stem)
        if ep_num <= 0:
            ep_num = len([e for e in episodes if e['ep_type'] == 'episode']) + 1
        while ep_num in seen:
            ep_num += 1
        seen.add(ep_num)
        episodes.append({'num': ep_num, 'path': str(f), 'ep_type': 'episode',
                         'filename': f.name, 'size': _size_of(f)})

    # `path` apunta a la horneada si existe (es la principal); `original_path` deja el de siempre a
    # mano para poder verlo, y `a4k` es lo que pinta el distintivo en la lista.
    for e in episodes:
        h = a4k_de(e['path'])
        if h:
            e['original_path'], e['path'], e['a4k'] = e['path'], h, True
            e['size'] = _size_of(_Path(h))

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


# ── Episodios horneados con Anime4K ───────────────────────────────────────────
# El horneado (api/anime_upscale.py) deja un fichero HERMANO `<nombre>.a4k.mkv` y NUNCA toca el
# original: el original es lo que siembra qBittorrent y un remux le cambia el tamaño y el hash.
#
# Aquí sólo vive la parte de RESOLUCIÓN: si existe la versión horneada, ésa es el episodio. Va en
# `_find_video` —el embudo por el que pasan los 12 sitios que abren un vídeo— y no en cada llamante,
# que es como se queda la mitad de la app viendo el original sin que nadie se entere.
_A4K_SUF = '.a4k'


def a4k_de(video: str) -> str:
    """Ruta de la versión horneada de `video`, o '' si no está. `video` puede ser ya la horneada."""
    if not video:
        return ''
    p = _Path(video)
    if p.stem.endswith(_A4K_SUF):
        return str(p) if p.exists() else ''
    h = p.with_name(p.stem + _A4K_SUF + '.mkv')
    return str(h) if h.exists() else ''


def original_de(video: str) -> str:
    """El original del que salió una horneada (para poder seguir viéndolo)."""
    p = _Path(video)
    if not p.stem.endswith(_A4K_SUF):
        return str(p)
    base = p.with_suffix('').with_suffix('')       # quita .mkv y .a4k
    for ext in _VIDEO_EXTS:
        c = base.with_suffix(ext)
        if c.exists():
            return str(c)
    return ''


def _prefiere_a4k(video: str) -> str:
    return a4k_de(video) or video


def _es_a4k(f) -> bool:
    return _Path(f).stem.endswith(_A4K_SUF)


def _qbt_file_path(torrent: dict, relative_path: str) -> str:
    """Resolve a qBittorrent file name relative to its save_path safely.

    qBittorrent's `/torrents/files` names include the torrent root directory,
    while `content_path` already points inside that directory. Prefer save_path
    so a multi-file torrent does not duplicate the root folder.
    """
    rel = str(relative_path or '').replace('\\', '/')
    if not rel or rel.startswith('/') or re.match(r'^[A-Za-z]:/', rel):
        return ''
    base_raw = str(torrent.get('save_path') or torrent.get('content_path') or '').strip()
    if not base_raw:
        return ''
    if _is_wsl() and re.match(r'^[A-Za-z]:[/\\]', base_raw):
        base_raw = _win_to_wsl(base_raw)
    base = _Path(base_raw)
    if base.is_file():
        return _prefiere_a4k(str(base)) if base.name == _Path(rel).name else ''
    if not base.is_dir():
        return ''
    candidate = (base / rel).resolve()
    try:
        if base.resolve() not in candidate.parents or not candidate.is_file():
            return ''
    except OSError:
        return ''
    return _prefiere_a4k(str(candidate))


def _local_relative_file(anime: dict, relative_path: str) -> str:
    """Find a persisted torrent-relative file in registered local folders."""
    rel = str(relative_path or '').replace('\\', '/')
    if not rel or rel.startswith('/') or re.match(r'^[A-Za-z]:/', rel):
        return ''
    for folder in _carpetas_de(anime):
        base = _Path(folder)
        if not base.is_dir():
            continue
        candidate = (base / rel).resolve()
        try:
            if base.resolve() in candidate.parents and candidate.is_file():
                video = _prefiere_a4k(str(candidate))
                return video if _path_is_within(video, str(base)) else ''
        except OSError:
            continue
    return ''


def _local_episode_identity(ep: dict) -> tuple[str, int | None]:
    """Infer a stable season-aware key for an unregistered local scan item."""
    raw = str(ep.get('original_path') or ep.get('path') or '').replace('\\', '/')
    match = re.search(r'(?<![A-Za-z0-9])S(\d{1,2})E(\d{1,4})(?!\d)', raw, re.I)
    if match:
        season, number = int(match.group(1)), int(match.group(2))
        return f's{season:02d}e{number:03d}', season
    folder = re.search(r'(?:season|temporada)[ _.-]*(\d{1,2})', raw, re.I)
    if folder:
        season = int(folder.group(1))
        return f's{season:02d}e{int(ep["num"]):03d}', season
    return str(ep['num']), None


def _find_video(content_path: str, episode: int, subpath: str = '', adivinar: bool = True) -> str:
    """El vídeo de un episodio dentro de `content_path`.

    `adivinar=False` deja SÓLO la coincidencia por número: sin el respaldo de «un único fichero»
    ni el posicional. Lo usa `_buscar_video` para barrer todas las carpetas de una serie antes de
    permitir ninguna conjetura (ver allí por qué).
    """
    # Windows path from qBittorrent → convert to WSL path first
    if _is_wsl() and re.match(r'^[A-Za-z]:[/\\\\]', content_path):
        content_path = _win_to_wsl(content_path)
    p = _Path(content_path)
    if p.is_file() and p.suffix.lower() in _VIDEO_EXTS:
        return _prefiere_a4k(str(p))
    if p.is_dir():
        # Subpath narrows the search to a specific subfolder (e.g. Season 2 within a batch)
        if subpath:
            sub = p / subpath
            if sub.is_dir():
                p = sub
        # Las horneadas se excluyen del emparejamiento y se recuperan al final con `_prefiere_a4k`:
        # si entraran aquí, `ep01.a4k.mkv` y `ep01.mkv` competirían por el mismo número.
        candidates = sorted(f for f in p.rglob('*')
                            if f.is_file() and f.suffix.lower() in _VIDEO_EXTS and not _es_a4k(f))
        if not candidates:
            return ''
        if episode <= 0 or (adivinar and len(candidates) == 1):
            return _prefiere_a4k(str(candidates[0]))
        # Use _parse_episode for accurate matching (avoids false positives from hex hashes)
        for f in candidates:
            if _parse_episode(f.stem) == episode:
                return _prefiere_a4k(str(f))
        # ⚠️ Aquí ANTES se devolvía `candidates[0]`: pedir un episodio que no está te daba OTRO
        # episodio, en silencio. Se vio pidiendo escalar el 99 de una serie de 10 — encoló el 1.
        # «No está» no puede devolver lo mismo que «aquí lo tienes».
        #
        # El único caso legítimo del respaldo es una carpeta cuyos ficheros NO llevan número
        # (medido sobre la biblioteca real: 2 carpetas de 85, y son partes en numeral romano —
        # Kizumonogatari I/II/III). Ahí el orden alfabético SÍ es el orden de los episodios.
        if adivinar and all(_parse_episode(f.stem) <= 0 for f in candidates):
            return _prefiere_a4k(str(candidates[episode - 1])) if episode <= len(candidates) else ''
        return ''
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


_CMD_EXE = '/mnt/c/Windows/System32/cmd.exe'

# Windows dirs needed on PATH so bare `powershell.exe` / `cmd.exe` resolve.
_WIN_PATH_DIRS = [
    '/mnt/c/Windows/System32',
    '/mnt/c/Windows/System32/WindowsPowerShell/v1.0',
    '/mnt/c/Windows',
]


def _repair_win_env() -> None:
    """A server auto-started in the background (detached from the login shell) loses
    WSL_INTEROP and the appended Windows PATH, which makes launching mpv.exe/powershell.exe
    fail ('No such file or directory' / interop unavailable). Re-point WSL_INTEROP at any
    live interop socket and make sure the Windows dirs are on PATH. Cheap + idempotent, so
    it's safe to call right before each launch."""
    if not _is_wsl():
        return
    # WSL removes /run/WSL/<pid>_interop when its session ends, so existence ≈ alive.
    cur = os.environ.get('WSL_INTEROP', '')
    if not cur or not os.path.exists(cur):
        try:
            socks = sorted(glob.glob('/run/WSL/*_interop'),
                           key=lambda p: os.stat(p).st_mtime, reverse=True)
            if socks:
                os.environ['WSL_INTEROP'] = socks[0]
        except OSError:
            pass
    parts = os.environ.get('PATH', '').split(':')
    missing = [p for p in _WIN_PATH_DIRS if p not in parts]
    if missing:
        os.environ['PATH'] = ':'.join(parts + missing)


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
_SKIP_SECS = int(os.environ.get('ANIME_SKIP_SECS', '88'))       # 1:28 default
_SKIP_WINDOW = int(os.environ.get('ANIME_SKIP_WINDOW', '240'))  # show button for first N s
# Auto-hide delay for the OSC bar (play/seek/etc.) in ms. The "Saltar OP" button uses the
# same value so both fade out together — no lingering button after the controls vanish.
_OSC_HIDE_MS = int(os.environ.get('ANIME_OSC_HIDE_MS', '1000'))
# Preferred subtitle languages, Latin-American Spanish first, then generic Spanish. mpv
# matches these against each track's language tag and picks the first that exists.
# Lista de PRIORIDAD para --slang de mpv: se queda con la PRIMERA que case, no con la mejor.
# Mismo orden que el player nativo (player.rs), que es el que usa el usuario: latino → genérico
# (nuestra pista inyectada es `spa` y es latino neutro) → España → inglés. Mantener ambos a la par.
# NOTA: mpv --slang sólo casa contra el CÓDIGO de idioma de la pista, no el título. La
# detección real (que también mira el título: 'LAT', 'Spanish (LAT)'…) vive en `sub_lang` y la
# usa el reproductor NATIVO vía `preferred_sub`. Esta lista es sólo para el mpv EXTERNO (modo
# 'mpv'), como red de seguridad por código; latino → genérico → castellano → inglés.
_SUB_LANGS = ('es-419,es-la,es-lat,es-mx,es-ar,es-co,es-cl,es-pe,es-ve,lat,lat-am,latam,'
              'latino,spa-419,spa-mx,spa-la,'
              'spa,es,esp,spanish,español,'
              'es-es,spa-es,cas,castellano,castilian,'
              'eng,en,english')


def _skip_args(dest_dir_linux: str, script_path_for_mpv: str) -> list:
    """Copy skip-intro.lua next to the watch-later dir and return the mpv args that load
    it (floating 'Saltar OP' button + key). script_path_for_mpv is the path string mpv
    will read (a Windows path under WSL, a linux path otherwise). The button's idle-hide
    is tied to the OSC hide delay so they disappear in sync."""
    if not _SKIP_LUA.exists():
        return []
    try:
        os.makedirs(dest_dir_linux, exist_ok=True)
        shutil.copy2(str(_SKIP_LUA), str(_Path(dest_dir_linux) / 'skip-intro.lua'))
    except Exception:
        return []
    # Use -append (not --script-opts=) so these merge with osc-hidetimeout from _wl_args
    # instead of replacing the whole dict regardless of argument order.
    return [f'--script={script_path_for_mpv}',
            f'--script-opts-append=skip-intro-skip={_SKIP_SECS}',
            f'--script-opts-append=skip-intro-window={_SKIP_WINDOW}',
            f'--script-opts-append=skip-intro-idle={_OSC_HIDE_MS / 1000:.3f}']


_MPV_PID_FILE = '/tmp/manga_mpv_pids'


def _record_mpv_pid(win_pid: int) -> None:
    """Anota el PID de Windows de un mpv.exe que lanzamos, para que el teardown
    (stop.sh / cierre de la app) lo cierre con taskkill.exe y no queden reproductores
    huérfanos. Solo se guardan los PIDs que ESTA app abrió — nunca se toca un mpv ajeno."""
    try:
        with open(_MPV_PID_FILE, 'a') as f:
            f.write(f'{win_pid}\n')
    except Exception:
        pass


def _launch_mpv(file_path: str, sub_file: str = '', start_pos: float = 0.0) -> tuple:
    """Open the video file with mpv.
    Returns (ok, proc, wl_dir): proc is Popen|None; wl_dir is the Linux watch-later dir for reading.
    --save-position-on-quit writes position to wl_dir when MPV exits — used for watched detection
    and resume. This works around the WSL2 interop shim exiting early before Windows MPV closes."""

    def _wl_args(wl_win_path):
        args = [f'--watch-later-dir={wl_win_path}', '--save-position-on-quit',
                '--ontop',  # always-on-top so MPV appears above browser/other windows
                f'--slang={_SUB_LANGS}',          # auto-pick the Spanish subtitle track
                # OSC bar fades in sync with the Saltar OP button. osc-hidetimeout is an OSC
                # *script* option (no top-level --osc-hidetimeout flag exists), so append it.
                f'--script-opts-append=osc-hidetimeout={_OSC_HIDE_MS}']
        if start_pos > 30:
            args.append(f'--start={start_pos:.1f}')
        return args

    if _is_wsl():
        _repair_win_env()  # self-heal WSL_INTEROP + Windows PATH for detached servers
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
                _record_mpv_pid(win_pid)  # para que el teardown (stop.sh) lo cierre al salir
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
                print('[mpv] fallback via cmd.exe', flush=True)
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
            opener = 'open' if _is_macos() else 'xdg-open'
            subprocess.Popen([opener, file_path],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return (True, None, wl_dir)
        except Exception:
            return (False, None, wl_dir)


_WATCHED_THRESHOLD = 0.85  # (legado) fracción vista antes de marcar visto — ya no se usa como umbral principal
# Umbral real: un episodio solo se da por TERMINADO (→ visto + avanzar al siguiente) cuando el
# usuario lo abandona faltando ≤ este número de segundos para el final. En cualquier otro caso se
# conserva el episodio actual y su posición exacta para "Continuar viendo". Fijo (2 min), no fracción,
# para que sea coherente en episodios cortos y largos.
_WATCHED_TAIL_SECS = 120

def _is_watched(position, duration):
    """True SOLO si el episodio realmente llegó al final: faltaban ≤ _WATCHED_TAIL_SECS para
    terminar. Exigimos que la duración sea MAYOR que la cola (evita que un episodio más corto
    que 2 min, o una duración espuria reportada al arrancar/cambiar de archivo, se marque como
    visto tras reproducir un instante). Mientras esto no se cumpla se conserva el minuto exacto."""
    return (duration > _WATCHED_TAIL_SECS
            and position > 0
            and (duration - position) <= _WATCHED_TAIL_SECS)


def _guardar_posicion(entrada: dict, ep_str: str, save_pos: int) -> None:
    """La posición de un episodio — y la regla que faltaba: **retomarlo lo DESMARCA**.

    Los tres caminos que escriben progreso (MPV externo, reproductor nativo y player web)
    guardaban la posición pero dejaban intacto un `watched` anterior. Reabrir un episodio ya
    visto y salir a la mitad dejaba las dos cosas escritas a la vez, y como el parche optimista
    del store SÍ desmarca, la contradicción no se veía hasta recargar: barra al 60 % antes de
    recargar, «visto» después. Medido en la biblioteca real: **14 series** con esa contradicción
    en el disco (BOCCHI ep 1, 853 s de 1420, y «visto»).

    ⚠️ **Sólo desmarca a partir de dos minutos**, el mismo umbral con el que [_is_watched] decide
    que un final cuenta como final. Asomarse un minuto a un episodio que ya viste no es volver a
    verlo, y desmarcarlo por eso reabriría en tu biblioteca algo que ya habías terminado — que es
    el error contrario y se nota más.
    """
    if save_pos > 30:
        entrada.setdefault('positions', {})[ep_str] = save_pos
        if save_pos > _WATCHED_TAIL_SECS:
            entrada.get('watched', {}).pop(ep_str, None)
    else:
        entrada.get('positions', {}).pop(ep_str, None)


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
                    print('[mpv] mpv.exe gone from tasklist → EOS', flush=True)
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
        print('[mpv] proc=None, waiting 6s for MPV to start…', flush=True)
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
        watched  = _is_watched(position, duration)
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

        _guardar_posicion(lib[anime_id], ep_str, save_pos)

        # QUÉ episodio tocaste el último. Sin este dato la UI tenía que ADIVINARLO, y adivinaba
        # mal: «Seguir viendo» buscaba el primer episodio con posición guardada, así que asomarse
        # un minuto al 7 dejaba la serie anclada ahí aunque después vieras el 4 entero. Los mapas
        # `positions`/`watched` no llevan hora, y `last_watched_at` es de la SERIE, no del
        # episodio, así que el dato no existía en ningún sitio. Se escribe en los dos caminos
        # (visto y progreso parcial) porque los dos son «estuve aquí».
        lib[anime_id]['last_ep'] = ep_str

        if watched:
            was_watched = bool(lib[anime_id].get('watched', {}).get(ep_str))
            lib[anime_id].setdefault('watched', {})[ep_str] = True
            now = int(time.time())
            lib[anime_id]['last_watched_at'] = now
            _lib_write(lib)
            if not was_watched:   # solo la PRIMERA vez que pasa a visto (evita duplicados)
                _history_append(anime_id, lib[anime_id].get('title', anime_id),
                                _episode_number(ep_str), lib[anime_id].get('cover', ''))
            print(f'[mpv] marked watched: anime={anime_id} ep={ep_str}', flush=True)
            push_sse_event('watched', anime_id=anime_id, ep_str=ep_str,
                           last_watched_at=now, watched=True,
                           duration=int(duration), from_mpv=True)
        else:
            now = int(time.time())
            if save_pos > 30:
                lib[anime_id]['last_watched_at'] = now
            _lib_write(lib)
            push_sse_event('position', anime_id=anime_id, ep_str=ep_str,
                           position=save_pos, duration=int(duration),
                           last_watched_at=(now if save_pos > 30 else 0))
    except Exception as e:
        print(f'[mpv] track error: {e}', flush=True)


_SEARCH_TTL = 120
_NYAA_TTL   = 60

_qbt     = _http.Session()
_qbt_url = ''
_qbt_ok  = False

# Shared session for Nyaa so the parallel title-variant searches reuse pooled
# keep-alive connections instead of paying a fresh TLS handshake each time.
_nyaa_http = _http.Session()
_nyaa_http.headers.update({'User-Agent': 'Mozilla/5.0'})
_nyaa_http.mount('https://', _http.adapters.HTTPAdapter(pool_connections=4, pool_maxsize=8))


# ── Helpers ────────────────────────────────────────────────────────────────────

def _ql(query, variables=None):
    try:
        r = _rhttp.post(_ANILIST, json={'query': query, 'variables': variables or {}}, timeout=10)
        return r.json().get('data') or {}
    except Exception:
        return {}


def _jikan_search(q: str) -> list:
    """Jikan (MAL) search — fallback when AniList is down."""
    try:
        r = _rhttp.get(f'{_JIKAN}/anime', params={'q': q, 'limit': 20},
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
    # Una carpeta local puede tener sus capítulos como `1.mkv`, `2.mkv`... El nombre completo
    # ya es el episodio; no lo confundimos con números sueltos dentro de títulos de releases.
    bare = title.strip()
    if re.fullmatch(r'\d{1,3}', bare):
        number = int(bare)
        if number > 0:
            return number
    # "Season N Complete/Pack/Full", "Complete Series/Collection" → batch
    if re.search(
        r'\bSeason\s*\d+\s*(?:Complete|Full|Pack)\b'
        r'|\bComplete\s*(?:Series|Season|Pack|Collection)\b'
        r'|\bFull\s*Season\b',
        title, re.I,
    ):
        return 0
    # Numeric range (01-13, 001-026) → batch.  Both sides must be 2+ digits to avoid "Part 2 - 01"
    if re.search(r'(?<!\d)\d{2,3}\s*[-~–]\s*\d{2,3}(?!\d)', title):
        return 0
    # Episode range with E prefix: E01-E13, S01E01-E12 → batch
    if re.search(r'\b[Ee]\d{1,3}\s*[-~–]\s*[Ee]?\d{1,3}\b', title):
        return 0
    # Strip codec identifiers so x264/x265/h264/h265 numbers are never extracted as episodes
    clean = re.sub(r'[xXhH]\.?26[45]', ' ', title)
    clean = re.sub(r'\b(HEVC|AVC1?|xvid|divx)\b', ' ', clean, flags=re.I)
    # Remove resolution tags (1080p, 720p…) — prevents extracting them as episode numbers
    clean = re.sub(r'\b(2160|1080|720|480|360)[pPiI]?\b', ' ', clean)
    # Remove 6-8 char hex CRCs in brackets [A1B2C3D4] — never episode numbers
    clean = re.sub(r'\[[0-9A-Fa-f]{6,8}\]', ' ', clean)
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
        r = _nyaa_http.get(url, timeout=12)
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

        def _parse_size(s):
            # Nyaa da el peso como texto ("1.4 GiB", "780.2 MiB"). Lo pasamos a bytes para ordenar.
            try:
                num, unit = s.split()
                mult = {'B': 1, 'KiB': 1024, 'MiB': 1048576, 'GiB': 1073741824, 'TiB': 1099511627776}
                return int(float(num) * mult.get(unit, 1))
            except (ValueError, AttributeError):
                return 0

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
            'size':        _t('size'),           # string legible de Nyaa ("1.4 GiB")
            'size_bytes':  _parse_size(_t('size')),  # numérico para ordenar
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
    _qbt_url = get_secret('QBT_URL', 'http://localhost:8080').rstrip('/')
    try:
        r = _qbt.post(f'{_qbt_url}/api/v2/auth/login',
                      data={'username': get_secret('QBT_USERNAME', 'admin'),
                            'password': get_secret('QBT_PASSWORD', 'adminadmin')},
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

@anime_bp.route('/title_variants')
def anime_title_variants():
    """Variantes de nombre (romaji/inglés/nativo/sinónimos vía AniList) de un anime, para que la
    búsqueda de torrents no dependa sólo del título principal. Params: al_id (opt), title (opt).
    Comparte la misma maquinaria de alias que la búsqueda de mejor fuente de manga."""
    from api.anilist import title_variants
    al_id = (request.args.get('al_id') or '').strip()
    title = (request.args.get('title') or '').strip()
    if not al_id and len(title) < 2:
        return jsonify([])
    try:
        out = title_variants(title or None, int(al_id) if al_id.isdigit() else None, 'ANIME')
    except Exception:
        out = [title] if title else []
    return jsonify(out)


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
          coverImage{ extraLarge large medium }
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
            'cover':        _al_cover(it),
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
    # A single fetch (f=0 = all) already carries the per-item `trusted` flag, and the
    # trusted-only filter (f=2) is a strict subset of it — so the old second request
    # was pure redundant latency. Fetch once and keep trusted entries first (stable).
    res = _nyaa_search(q, category, '0')
    seen, merged = set(), []
    for t in sorted(res, key=lambda x: not x['trusted']):
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
    return jsonify({'connected': ok, 'url': get_secret('QBT_URL', 'http://localhost:8080'), 'version': ver})


@anime_bp.route('/qbt/configure', methods=['POST'])
def qbt_configure():
    data = request.get_json(silent=True) or {}
    creds = {}
    if data.get('url'):      creds['QBT_URL']      = data['url'].rstrip('/')
    if data.get('username'): creds['QBT_USERNAME'] = data['username']
    if data.get('password'): creds['QBT_PASSWORD'] = data['password']
    if creds: set_secrets(creds)
    global _qbt_ok
    _qbt_ok = False
    return jsonify({'connected': _qbt_login()})


def _sanitize_folder(title: str) -> str:
    return re.sub(r'[\\/:*?"<>|]', '', title).strip()


def _build_save_path(base: str, folder: str) -> str:
    sep = '\\' if ('\\' in base or (len(base) >= 2 and base[1] == ':')) else '/'
    return base.rstrip('/\\') + sep + folder


# Subcarpeta (con punto) donde caen los vídeos de anime cuando el modo oculto está
# activo. El panel de Almacenamiento normal ignora las carpetas con punto, así que
# los animes ocultos nunca se listan ni suman en la biblioteca normal, aunque
# compartan el mismo download_path físico configurado por el usuario.
_HIDDEN_ANIME_SUBDIR = '.hidden_anime'


def _apply_hidden_anime_subdir(base: str) -> str:
    """Devuelve la base de descargas ajustada al modo de biblioteca ACTIVO: en modo
    oculto cuelga de `_HIDDEN_ANIME_SUBDIR`; en normal, la base tal cual."""
    from api.runtime import get_library_mode
    if base and get_library_mode() == 'hidden':
        return _build_save_path(base, _HIDDEN_ANIME_SUBDIR)
    return base


def _anime_save_dir(anime_title: str) -> str:
    """Carpeta (ruta de Windows/qBittorrent) donde se guardan los torrents de este anime:
    `<download_path>/<título saneado>`. Cae al save_path propio de qBittorrent si no hay
    download_path configurado. Devuelve '' si no se puede determinar.

    Vive aparte porque lo necesitan DOS sitios: `qbt/add`, que le dice a qBittorrent dónde
    guardar, y `library/add`, que apunta esa misma carpeta como `local_path` del anime. Si cada
    uno la calculase por su cuenta, en cuanto divergieran la biblioteca dejaría de encontrar los
    ficheros al quitar el torrent — que es exactamente el fallo que esto arregla.
    """
    base = (_anime_settings_read().get('download_path') or '').strip()
    if not base:
        try:
            base = (_q('get', '/app/preferences').json() or {}).get('save_path', '')
        except Exception:
            base = ''
    base = _apply_hidden_anime_subdir(base)
    if not base:
        return ''
    folder = _sanitize_folder(anime_title or '')
    return _build_save_path(base, folder) if folder else base


def _qbt_add_link(link: str, save_dir: str) -> tuple[bool, str]:
    """Manda un enlace a qBittorrent. Devuelve `(ok, mensaje)`.

    Vive aparte porque lo usan dos entradas —el escritorio por `qbt/add` y el móvil por
    `episode_download`— y lo que hay que interpretar no es trivial: qBittorrent contesta `Ok.` en
    texto plano unas veces y un JSON con contadores otras, así que una segunda copia de esta
    lectura acabaría dando por buena una descarga que no arrancó.
    """
    form = {'urls': link}
    if save_dir:
        form['savepath'] = save_dir
    r = _q('post', '/torrents/add', data=form)
    body = r.text.strip()
    ok = r.status_code in (200, 204) and body in ('Ok.', '')
    if not ok and r.status_code == 200:
        try:
            j = r.json()
            ok = j.get('success_count', 0) > 0 or j.get('failure_count', 0) == 0
        except Exception:
            pass
    return ok, body or 'Ok.'


@anime_bp.route('/qbt/add', methods=['POST'])
def qbt_add():
    data = request.get_json(silent=True) or {}
    link = (data.get('magnet') or data.get('torrent_url') or '').strip()
    if not link:
        return jsonify({'error': 'no link'}), 400
    try:
        # Prefer the user-configured anime download folder (another disk), falling
        # back to qBittorrent's own default save path. Each anime gets a subfolder.
        save_dir = (data.get('save_path') or '').strip() or _anime_save_dir(data.get('anime_title') or '')
        ok, msg = _qbt_add_link(link, save_dir)
        return jsonify({'ok': ok, 'msg': msg})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@anime_bp.route('/settings', methods=['GET'])
def anime_settings_get():
    """Anime download settings. download_path is the base folder new torrents save to
    (each anime in a subfolder); qbt_default is qBittorrent's own default, for reference."""
    s = _anime_settings_read()
    qbt_default = ''
    try:
        qbt_default = (_q('get', '/app/preferences').json() or {}).get('save_path', '')
    except Exception:
        pass
    return jsonify({'download_path': s.get('download_path', ''), 'qbt_default': qbt_default})


@anime_bp.route('/settings', methods=['POST'])
def anime_settings_set():
    data = request.get_json(silent=True) or {}
    s = _anime_settings_read()
    if 'download_path' in data:
        s['download_path'] = (data.get('download_path') or '').strip()
    _anime_settings_write(s)
    return jsonify({'ok': True, 'download_path': s.get('download_path', '')})


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
        # «Falló» y «no había» NUNCA pueden ser el mismo valor: devolver [] con un 200 hacía que
        # qBittorrent apagado (o la VPN caída) se pintase como «no hay descargas» y parecieran
        # perdidos los 87 torrents. El front ya sabe distinguirlo (`qbtError`); sólo faltaba
        # que el backend se lo dijera.
        record_error('anime', e, op='qbt_list')
        return jsonify({'error': str(e) or 'qBittorrent no responde'}), 502


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
            # qBittorrent v5 renamed pause/resume → stop/start. Try the classic
            # endpoint first and fall back to the v5 alias on 404.
            v5 = {'pause': 'stop', 'resume': 'start'}[action]
            r = _q('post', f'/torrents/{action}', data={'hashes': hash_})
            if getattr(r, 'status_code', 200) == 404:
                _q('post', f'/torrents/{v5}', data={'hashes': hash_})
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
    except Exception as e:
        # 🔴 Aquí había un `except Exception: return jsonify({})` mudo, que es EXACTAMENTE el error
        # de confundir «falló» y «no había» devolviendo el mismo
        # valor. El carril «Emitido hoy» del móvil se quedaba vacío y decía «hoy no emite nada» con
        # la misma cara con la que diría «no pude preguntar a AniList» — y no aparecía ni en el log
        # ni en el contador de errores, así que desde fuera era indistinguible.
        #
        # ⚠️ Se mantiene el 200 y el diccionario vacío a propósito: el cliente ya sabe tratar `{}`
        # y devolver un 500 rompería el Inicio entero por un carril. Lo que cambia es que ahora el
        # fallo es VISIBLE (log + contador + SSE) y viaja marcado en `_error`.
        record_error('anime', e, op='airing', ids=len(al_ids))
        return jsonify({'_error': 'no se pudo consultar el calendario de AniList'})


def _compact_library_result(result: list) -> list:
    """Reduce el payload de los sondeos sin cambiar el contrato de la carga completa.

    Las tarjetas sólo necesitan el estado del episodio. Las rutas son datos de detalle y pueden
    ocupar decenas de KB repetidas en cada sondeo mientras qBittorrent está activo; el cliente
    conserva la última respuesta completa para reproducir y administrar esos episodios.
    """
    compact = []
    for anime in result:
        item = dict(anime)
        item['episodes'] = []
        for episode in anime.get('episodes') or []:
            value = dict(episode)
            for field in ('local_path', 'filename', 'original_path'):
                value.pop(field, None)
            item['episodes'].append(value)
        compact.append(item)
    return compact


def _qbt_local_episodes(torrent: dict) -> dict:
    """Indexa los ficheros locales de un torrent completado, una sola vez por hash.

    La rama de qBittorrent conserva el enlace al torrent para poder administrarlo, pero también
    debe exponer el mismo contrato local cuando el fichero ya está en disco y el `save_path` no
    coincide con ninguna carpeta registrada de la serie. El escaneo es por carpeta y aprovecha su
    caché: resolver la ruta con `_find_video` para cada episodio volvería a pagar un recorrido de
    DrvFS por cada elemento de un batch.
    """
    raw_path = str(torrent.get('content_path') or torrent.get('save_path') or '').strip()
    if not raw_path:
        return {}

    content_path = raw_path
    if _is_wsl() and re.match(r'^[A-Za-z]:[/\\\\]', content_path):
        content_path = _win_to_wsl(content_path)

    try:
        path = _Path(content_path)
        if path.is_dir():
            return {
                (ep.get('ep_type', 'episode'), ep.get('num')): ep
                for ep in _scan_local_episodes(str(path), {})
            }
        if path.is_file() and path.suffix.lower() in _VIDEO_EXTS:
            video = _prefiere_a4k(str(path))
            original = original_de(video) if video != str(path) else ''
            return {
                ('episode', -1): {
                    'num': -1,
                    'path': video,
                    'original_path': original,
                    'filename': path.name,
                    'ep_type': 'episode',
                    'a4k': video != str(path),
                }
            }
    except OSError:
        pass
    return {}


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

    # Legacy map keeps the old numeric lookup. The detail map is the new lossless
    # index keyed by qBittorrent's file index, so S01E01 and S02E01 never collide.
    files_map: dict = {}
    file_details_map: dict = {}
    for ih in unique_hashes:
        cached = _qbt_files_cache.get(ih)
        details = _qbt_file_details_cache.get(ih)
        if cached is not None and details is not None:
            files_map[ih] = cached
            file_details_map[ih] = details
            continue
        try:
            raw_files = _q('get', '/torrents/files', params={'hash': ih}).json()
            details = {}
            ep_files: dict = {}
            for f in raw_files:
                try:
                    details[int(f.get('index'))] = f
                except (TypeError, ValueError):
                    continue
                stem = _Path(f.get('name', '')).stem  # basename without extension
                if not stem:
                    continue
                ep_num = _parse_episode(stem)
                if ep_num > 0 and ep_num not in ep_files:
                    # Legacy fallback only; new selected records use file_index.
                    ep_files[ep_num] = stem
            if not ep_files and len(raw_files) == 1:
                ep_files[-1] = _Path(raw_files[0].get('name', '')).stem
            files_map[ih] = ep_files
            file_details_map[ih] = details
            if ep_files:
                _qbt_files_cache[ih] = ep_files
            if details:
                _qbt_file_details_cache[ih] = details
        except Exception:
            files_map[ih] = {}
            file_details_map[ih] = {}
    # Bound memory: forget cached hashes that are no longer in qBittorrent.
    if _qbt_files_cache:
        live = set(hash_map.keys())
        for h in [h for h in _qbt_files_cache if h not in live]:
            _qbt_files_cache.pop(h, None)
            _qbt_file_details_cache.pop(h, None)
    result = []
    carpetas_nuevas: dict = {}
    qbt_local_cache: dict = {}
    for anime_id, anime in lib.items():
        watched_map   = anime.get('watched', {})
        positions_map = anime.get('positions', {})
        durations_map = anime.get('durations', {})
        # Auto-reparación de una serie repartida entre discos: los `save_path` de sus PROPIOS
        # torrents dicen dónde están los episodios que la carpeta principal no ve. El dato ya
        # viene en `hash_map` (una sola llamada, hecha arriba): no cuesta ni una petición más.
        # El `isdir` sólo se paga por carpeta desconocida, y una vez registrada ya no vuelve.
        for ep_data in (anime.get('episodes') or {}).values():
            t = hash_map.get((ep_data.get('info_hash') or '').lower())
            if not t or t.get('progress', 0) < 1:
                continue
            carpeta = _win_to_wsl(t.get('save_path') or '')
            if carpeta and carpeta not in _carpetas_de(anime) and os.path.isdir(carpeta):
                _anadir_carpeta(anime, carpeta)
                carpetas_nuevas.setdefault(anime_id, []).append(carpeta)
        # Sólo usar la rama LOCAL si el folder existe y tiene episodios. Un local_path
        # OBSOLETO/inaccesible (p.ej. un /mnt/d de otra máquina o un disco no montado) ya
        # NO debe ensombrecer el enlace por TORRENT: si no hay archivos locales reales,
        # caemos a la rama qBittorrent y el episodio queda enlazado/reproducible igual.
        local_eps = _escanear_carpetas(anime)
        has_granular = any(
            e.get('from_batch_selection') and e.get('relative_path')
            for e in (anime.get('episodes') or {}).values()
        )
        if local_eps and not has_granular:
            regular_count = sum(1 for ep in local_eps if ep.get('ep_type', 'episode') == 'episode')
            episodes_out = [
                {
                    'num':        ep['num'],
                    'episode_key': (f"s{int(ep.get('season') or 0):02d}e{ep['num']:03d}"
                                    if ep.get('season') else str(ep['num'])),
                    # ⚠️ El título sale del ORIGINAL: el stem de una horneada acaba en `.a4k` y
                    # se colaba en el nombre visible del episodio.
                    'title':      _local_ep_title(_Path(ep.get('original_path') or ep['path']).stem,
                                                  ep.get('ep_type', 'episode')),
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
                    # También contra el ORIGINAL: el marcador de subtítulos se guardó con SU
                    # nombre, y preguntando por el de la horneada salía siempre "no traducido".
                    'es_injected': _es_sub_injected(ep.get('original_path') or ep['path']),
                    # Este dict es una lista FIJA de claves: lo que no se nombre aquí, no llega a la
                    # UI. `a4k` es lo que pinta el distintivo, apaga los shaders del reproductor
                    # (aplicarlos sobre un horneado es pasar la red dos veces) y ofrece «volver al
                    # original».
                    'a4k':         bool(ep.get('a4k')),
                    'original_path': ep.get('original_path', ''),
                }
                for ep in local_eps
            ]
            series_total = anime.get('total_episodes')
            series_total = series_total if isinstance(series_total, int) else 0

            # Espacio en disco: el tamaño ya viene del escaneo (que lo toma en el mismo recorrido
            # y lo cachea). Hacer aquí un stat por episodio costaba ~690 ms por carga sobre DrvFS.
            disk_size = sum(ep.get('size') or 0 for ep in local_eps)

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
                e['has_thumb'] = _thumb_key(
                    anime_id, e['num'], e.get('ep_type') == 'special', e.get('episode_key', '')
                ) in all_thumbs

            result.append({
                'id': anime_id,
                'al_id': anime.get('al_id'),
                'mal_id': anime.get('mal_id'),
                'title': anime.get('title', ''),
                'title_romaji': anime.get('title_romaji', ''),
                'cover': anime.get('cover', ''),
                'cover_xl': _hd(anime.get('cover_xl', '')),
                'cover_rev': anime.get('cover_rev', 0),
                'banner': _hd(anime.get('banner', ''), hero=True),
                'banner_source': anime.get('banner_source', ''),
                'banner_detail': _hd(anime.get('banner_detail', ''), hero=True),
                'logo': _hd(anime.get('logo', ''), hero=True),
                'synopsis': anime.get('synopsis', ''),
                'genres': anime.get('genres', []),
                # Géneros específicos (Yuri, Isekai, Escolar…) para el filtro de la biblioteca.
                # Van aparte de `genres` porque eso es lo que pinta la tarjeta y no cambia.
                'gen_tags': anime.get('gen_tags') or [],
                'season': anime.get('season', ''),
                'season_year': anime.get('season_year'),
                'total_episodes': series_total or regular_count,
                'format': anime.get('format', ''),
                'status': _norm_status(anime.get('status'),
                                       sum(1 for v in (anime.get('watched') or {}).values() if v),
                                       series_total or regular_count),
                'episodes': episodes_out,
                'downloaded_count': regular_count,
                'disk_size': disk_size,
                'is_local': True,
                'added_at': anime.get('added_at', 0),
                'last_watched_at': anime.get('last_watched_at', 0),
                # El último episodio que tocaste. Va como número (0 = no hay dato, entradas
                # anteriores a este campo) para que el front no tenga que parsear.
                'last_ep': _episode_number(anime.get('last_ep') or 0),
            })
            continue

        total = anime.get('total_episodes')
        total = total if isinstance(total, int) else 0   # guard against bad data (e.g. an episodes list)
        ep_map = anime.get('episodes', {})
        episodes_out = []
        for ep_str, ep_data in ep_map.items():
            season = ep_data.get('season')
            episode_key = str(ep_str)
            key_match = re.fullmatch(r'[sS](\d{1,2})[eE](\d{1,4})', episode_key)
            if key_match:
                season = int(key_match.group(1)) if season is None else season
                ep_num = int(key_match.group(2))
            else:
                try:
                    ep_num = int(ep_str)
                except (TypeError, ValueError):
                    ep_num = int(ep_data.get('episode') or -1)
            ih = (ep_data.get('info_hash') or '').lower()
            qbt = hash_map.get(ih, {})
            file_detail = None
            if ep_data.get('file_index') is not None:
                try:
                    file_detail = file_details_map.get(ih, {}).get(int(ep_data['file_index']))
                except (TypeError, ValueError):
                    file_detail = None
            if qbt:
                file_progress = file_detail.get('progress') if file_detail else None
                progress = round((file_progress if file_progress is not None else qbt.get('progress', 0)) * 100, 1)
                state = qbt.get('state', 'unknown')
                in_qbt = True
            else:
                progress = 0
                state = 'missing'
                in_qbt = False

            # Selected batch records carry their exact relative path. Legacy records
            # retain the numeric filename fallback for compatibility.
            ep_files = files_map.get(ih, {})
            exact_title = _Path(ep_data.get('relative_path', '')).stem if ep_data.get('relative_path') else ''
            actual_title = (
                exact_title
                or (file_detail and _Path(file_detail.get('name', '')).stem)
                or ep_files.get(ep_num)
                or ep_files.get(-1)
                or (ep_data.get('title', '') if not ep_data.get('from_batch') else '')
            )

            # Un torrent completado puede seguir fuera de las carpetas registradas (por ejemplo,
            # si qBittorrent conservó otro `save_path`). En ese caso no perder el contrato local:
            # indexar el contenido una vez por hash y casar el episodio desde ese índice.
            local_ep = None
            # New selections resolve by the exact torrent path, not by episode number.
            if qbt and ep_data.get('relative_path'):
                video = _qbt_file_path(qbt, ep_data['relative_path'])
                if video:
                    original = original_de(video) if _es_a4k(video) else video
                    local_ep = {'path': video, 'original_path': original,
                                'filename': _Path(original).name, 'a4k': video != original}
            if local_ep is None and ep_data.get('relative_path'):
                video = _local_relative_file(anime, ep_data['relative_path'])
                if video:
                    original = original_de(video) if _es_a4k(video) else video
                    local_ep = {'path': video, 'original_path': original,
                                'filename': _Path(original).name, 'a4k': video != original}
            if (local_ep is None and qbt and qbt.get('progress', 0) >= 1
                    and not ep_data.get('from_batch_selection')):
                if ih not in qbt_local_cache:
                    qbt_local_cache[ih] = _qbt_local_episodes(qbt)
                local_map = qbt_local_cache[ih]
                ep_type = ep_data.get('ep_type', 'episode')
                local_ep = (local_map.get((ep_type, ep_num))
                            or local_map.get(('episode', ep_num)))
                if local_ep is None and ep_num in (-1, 0, 1):
                    local_ep = local_map.get(('episode', -1))
            local_path = local_ep.get('path', '') if local_ep else ''
            original_path = local_ep.get('original_path', '') if local_ep else ''

            identity_key = episode_key
            episodes_out.append({
                'num': ep_num,
                'season': season,
                'episode_key': identity_key,
                'title': actual_title,
                'info_hash': ih,
                'file_index': ep_data.get('file_index'),
                'relative_path': ep_data.get('relative_path', ''),
                'from_batch_selection': bool(ep_data.get('from_batch_selection')),
                'progress': progress,
                'state': state,
                'size': (file_detail.get('size', 0) if file_detail else (qbt.get('size', 0) if qbt else 0)),
                'in_qbt':     in_qbt,
                'in_local':   bool(local_path),
                'local_path': local_path,
                'original_path': original_path,
                'a4k':         bool(local_ep and local_ep.get('a4k')),
                'filename':    local_ep.get('filename', '') if local_ep else '',
                'added_on':   ep_data.get('added_on', 0),
                'watched':    bool(watched_map.get(identity_key)),
                'resume_pos': positions_map.get(identity_key, 0),
                'duration':   durations_map.get(identity_key, 0),
            })

        # A granular batch must not hide unrelated local episodes. Merge only
        # scan results whose exact path was not already claimed by a selected slot.
        claimed_local = {str(e.get('local_path') or '') for e in episodes_out if e.get('local_path')}
        claimed_keys = {str(e.get('episode_key') or '') for e in episodes_out}
        for local in local_eps:
            path = str(local.get('path') or '')
            if not path or path in claimed_local:
                continue
            local_key, local_season = _local_episode_identity(local)
            if local_key in claimed_keys:
                continue
            num = int(local.get('num') or 0)
            episodes_out.append({
                'num': num, 'season': local_season, 'episode_key': local_key,
                'title': _local_ep_title(_Path(local.get('original_path') or path).stem,
                                         local.get('ep_type', 'episode')),
                'info_hash': '', 'file_index': None, 'relative_path': '',
                'from_batch_selection': False, 'progress': 100, 'state': 'local',
                'size': local.get('size', 0), 'in_qbt': False, 'in_local': True,
                'local_path': path, 'original_path': local.get('original_path', ''),
                'a4k': bool(local.get('a4k')), 'filename': local.get('filename', ''),
                'added_on': 0, 'watched': bool(watched_map.get(local_key)),
                'resume_pos': positions_map.get(local_key, 0),
                'duration': durations_map.get(local_key, 0),
                'ep_type': local.get('ep_type', 'episode'),
            })
            claimed_local.add(path)
            claimed_keys.add(local_key)

        episodes_out.sort(key=lambda e: (e['num'] < 0, e.get('season') or 0, e['num']))

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
            known_ep_nums = {e['num'] for e in episodes_out
                             if e.get('ep_type', 'episode') == 'episode'}
            for n in range(1, total + 1):
                if n not in known_ep_nums:
                    episodes_out.append({'num': n, 'title': '', 'info_hash': '', 'progress': 0,
                                         'state': 'missing', 'size': 0, 'in_qbt': False, 'added_on': 0,
                                         'watched': False, 'episode_key': str(n)})
            episodes_out.sort(key=lambda e: (e['num'] < 0, e.get('season') or 0, e['num']))

        # Propagate batch (ep 0) state to individual placeholder episodes so that
        # card dots and the eplist show the correct download/done state even when
        # the individual episode slots were never pre-filled.
        batch_ep = next(
            (e for e in episodes_out if e['num'] == 0 and e.get('in_qbt')),
            None
        )
        if batch_ep and not batch_ep.get('from_batch_selection'):
            batch_ih   = batch_ep['info_hash']
            ep_files_b = files_map.get(batch_ih, {})
            local_map_b = qbt_local_cache.get(batch_ih, {})
            for e in episodes_out:
                if e['num'] > 0 and not e.get('in_qbt'):
                    e['in_qbt']    = True
                    e['progress']  = batch_ep['progress']
                    e['info_hash'] = batch_ih
                    e['state']     = batch_ep.get('state', 'downloading')
                    if not e.get('title'):
                        e['title'] = ep_files_b.get(e['num'], '')
                    local_ep = (local_map_b.get((e.get('ep_type', 'episode'), e['num']))
                                or local_map_b.get(('episode', e['num'])))
                    if local_ep:
                        e['in_local'] = True
                        e['local_path'] = local_ep.get('path', '')
                        e['original_path'] = local_ep.get('original_path', '')
                        e['a4k'] = bool(local_ep.get('a4k'))
                        e['filename'] = local_ep.get('filename', '')

        for e in episodes_out:
            e['has_thumb'] = _thumb_key(
                anime_id, e['num'], e.get('ep_type') == 'special', e.get('episode_key', '')
            ) in all_thumbs

        done_count = sum(1 for e in episodes_out if e.get('num', 0) > 0
                         and (e.get('in_local') or (e.get('in_qbt') and e.get('progress', 0) >= 100)))
        # Espacio en disco de la serie: sumar el tamaño de cada torrent UNA sola vez
        # (dedup por info_hash) — un batch comparte hash entre todos sus episodios, así
        # que sumarlo por episodio inflaría el total.
        _sizes = {}
        disk_size = 0
        for e in episodes_out:
            if e.get('in_local') and e.get('file_index') is not None:
                disk_size += e.get('size') or 0
                continue
            _ih = e.get('info_hash')
            if _ih and e.get('in_qbt') and e.get('size'):
                _sizes[_ih] = e['size']
            elif e.get('in_local') and not _ih:
                disk_size += e.get('size') or 0
        disk_size += sum(_sizes.values())
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
            'cover_xl': _hd(anime.get('cover_xl', '')),
            'cover_rev': anime.get('cover_rev', 0),
            'banner': _hd(anime.get('banner', ''), hero=True),
            'banner_source': anime.get('banner_source', ''),
            'banner_detail': _hd(anime.get('banner_detail', ''), hero=True),
            'logo': _hd(anime.get('logo', ''), hero=True),
            'synopsis': anime.get('synopsis', ''),
            'genres': anime.get('genres', []),
            'season': anime.get('season', ''),
            'season_year': anime.get('season_year'),
            'total_episodes': total,
            'format': anime.get('format', ''),
            'status': _norm_status(anime.get('status'),
                                   sum(1 for v in (anime.get('watched') or {}).values() if v),
                                   total),
            'episodes': episodes_out,
            'downloaded_count': done_count,
            'disk_size': disk_size,
            'added_at': anime.get('added_at', added_at_fallback),
            'last_watched_at': anime.get('last_watched_at', 0),
            'last_ep': _episode_number(anime.get('last_ep') or 0),
        })
    if carpetas_nuevas:
        # ⚠️ Se persiste el DELTA sobre una relectura, nunca el `lib` de arriba: esta carga
        # puede llevar 30 s escaneando discos, y guardarla entera pisa todo lo que se haya
        # escrito mientras. Ya pasó: resucitó el clon que el backfill acababa de fundir.
        fresca = _lib_read()
        for aid, carpetas in carpetas_nuevas.items():
            for carpeta in carpetas:
                if aid in fresca:
                    _anadir_carpeta(fresca[aid], carpeta)
        _lib_write(fresca)
        print(f'[anime] carpetas nuevas por save_path en {len(carpetas_nuevas)} serie(s)', flush=True)
    if request.args.get('summary') == '1':
        return jsonify(_compact_library_result(result))
    return jsonify(result)


@anime_bp.route('/library/add', methods=['POST'])
def anime_library_add():
    global _backfill_done
    _backfill_done = False   # re-enrich (banner/genres/TMDB) for the newly added entry
    data = request.get_json(silent=True) or {}
    al_id  = data.get('al_id')
    mal_id = data.get('mal_id')
    title  = (data.get('title') or '').strip()
    if not (al_id or mal_id or title):
        return jsonify({'error': 'need al_id, mal_id, or title'}), 400

    total_eps = data.get('total_episodes')
    if not isinstance(total_eps, int):
        total_eps = None   # never store a non-number (e.g. an episodes array) — it breaks the library load
    fmt       = data.get('format', '')

    # If episode count is missing but we have an AniList id, fetch it now
    if not total_eps and al_id:
        try:
            _q = '''query($id:Int){Media(id:$id,type:ANIME){episodes format
                title{english romaji} coverImage{ extraLarge large medium }}}'''
            r = _rhttp.post(_ANILIST, json={'query': _q, 'variables': {'id': int(al_id)}}, timeout=8)
            m = (r.json().get('data') or {}).get('Media') or {}
            if m.get('episodes'):
                total_eps = m['episodes']
            if m.get('format') and not fmt:
                fmt = m['format']
        except Exception:
            pass

    lib = _lib_read()
    # La clave sale de la biblioteca, no de los ids sueltos: si esta obra ya está (aunque
    # entrase por el otro id), se AÑADE a la que hay en vez de clonarla.
    anime_id = _lib_key(lib, al_id, mal_id, title)
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
        # Completar el id que faltase: es lo que permite reconocerla la próxima vez
        # por CUALQUIERA de los dos y lo que deja que `_canonizar_lib` la reclave.
        if al_id and not lib[anime_id].get('al_id'):
            lib[anime_id]['al_id'] = al_id
        if mal_id and not lib[anime_id].get('mal_id'):
            lib[anime_id]['mal_id'] = mal_id
        if not lib[anime_id].get('cover') and data.get('cover'):
            lib[anime_id]['cover'] = data['cover']
        if not lib[anime_id].get('total_episodes') and total_eps:
            lib[anime_id]['total_episodes'] = total_eps
        if not lib[anime_id].get('format') and fmt:
            lib[anime_id]['format'] = fmt

    # Apuntar la carpeta de descargas como local_path. Sin esto, un anime bajado por torrent
    # SOLO existía para la app mientras el torrent siguiera en qBittorrent: al quitarlo (aunque
    # se conserven los ficheros) el episodio desaparecía de la biblioteca, porque toda la UI
    # decide con `in_local || (in_qbt && progress>=100)` y `local_path` únicamente se rellenaba
    # al vincular una carpeta A MANO. Los vídeos seguían en disco; era la app la que no miraba.
    # Solo se pone si falta y si la carpeta EXISTE de verdad: un local_path fantasma haría que
    # la rama local gane y no muestre nada.
    # Se AÑADE, no se elige: si cambiaste la carpeta de descargas a mitad de temporada, los
    # episodios viejos siguen en la otra y deben seguir viéndose (ver `_carpetas_de`).
    save_dir = _win_to_wsl(_anime_save_dir(title or data.get('title_romaji', '')))
    if save_dir and save_dir not in _carpetas_de(lib[anime_id]) and os.path.isdir(save_dir):
        _anadir_carpeta(lib[anime_id], save_dir)

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
            _borrar_thumbs(anime_id)
        del lib[anime_id]
        _lib_write(lib)
    return jsonify({'ok': True})


@anime_bp.route('/library/<anime_id>/episode/<int:ep_num>', methods=['DELETE'])
@anime_bp.route('/library/<anime_id>/episode/<path:episode_key>', methods=['DELETE'])
def anime_episode_remove(anime_id, ep_num=None, episode_key=None):
    lib = _lib_read()
    data = request.get_json(silent=True) or {}
    if anime_id not in lib:
        return jsonify({'error': 'not found'}), 404
    ep_str = str(episode_key if episode_key is not None else ep_num)
    ep_data = lib[anime_id].get('episodes', {}).get(ep_str, {})
    episode_num = _episode_number(ep_str)
    if data.get('delete_files'):
        granular = bool(ep_data.get('from_batch_selection') and ep_data.get('file_index') is not None)
        refs = [value for key, value in lib[anime_id].get('episodes', {}).items()
                if key != ep_str and value.get('info_hash') == ep_data.get('info_hash')]
        if granular:
            # Never delete a multi-file torrent for one selected episode. Lower
            # only this file's priority, remove its exact local file, and leave
            # every sibling episode and the torrent itself intact.
            try:
                ih = (ep_data.get('info_hash') or '').lower()
                if ih:
                    _q('post', '/torrents/filePrio', data={
                        'hash': ih, 'id': str(int(ep_data['file_index'])), 'priority': '0'})
                    rows = _q('get', '/torrents/info', params={'hashes': ih}).json()
                    row = rows[0] if rows else {}
                    video = _qbt_file_path(row, ep_data.get('relative_path', ''))
                else:
                    video = ''
                if not video:
                    video = _local_relative_file(lib[anime_id], ep_data.get('relative_path', ''))
                if video:
                    _Path(video).unlink(missing_ok=True)
            except Exception as e:
                record_error('anime', e, op='episode_remove_granular', anime=anime_id, ep=ep_str)
            batch_hash = (ep_data.get('info_hash') or '').lower()
            batch_files = lib[anime_id].get('batch_files', {}).get(batch_hash, {})
            batch_files.get('files', {}).pop(str(ep_data['file_index']), None)
        else:
            # Legacy behavior is retained only when this is the last library
            # reference to the torrent. Shared hashes must never be deleted as
            # a side effect of removing one episode.
            ih = (ep_data.get('info_hash') or '').lower()
            if ih and not refs:
                try:
                    _q('post', '/torrents/delete', data={'hashes': ih, 'deleteFiles': 'true'})
                except Exception as e:
                    record_error('anime', e, op='episode_remove_torrent', anime=anime_id, ep=ep_str)
            try:
                video = _local_relative_file(lib[anime_id], ep_data.get('relative_path', ''))
                if not video:
                    video = _buscar_video(lib[anime_id], episode_num)
                if video:
                    _Path(video).unlink(missing_ok=True)
            except Exception as e:
                record_error('anime', e, op='episode_remove_local', anime=anime_id, ep=ep_str)
        _borrar_thumbs(anime_id, ep_str)
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


def _cover_candidates(v: dict) -> list:
    """Every cover image we can find for this entry across sources, for the
    manual 'change cover' picker. AniList's own cover is always offered as a
    safe baseline; TMDB contributes every poster uploaded for the matched
    id (not just the auto-picked one) plus each season's own poster when the
    show is split into seasons on TMDB; MyAnimeList via Jikan adds one more
    independent option. Deduped by URL, each tagged with its source/label."""
    out, seen = [], set()

    def add(url, source, label):
        if url and url not in seen:
            seen.add(url)
            out.append({'url': url, 'source': source, 'label': label})

    add(v.get('cover_xl'), 'anilist', 'AniList')

    tmdb_id, tmdb_type = v.get('tmdb_id'), v.get('tmdb_type', 'tv')
    if tmdb_id and _tmdb_key():
        try:
            imgs = _http.get(f"https://api.themoviedb.org/3/{tmdb_type}/{tmdb_id}/images",
                              params={'api_key': _tmdb_key(), 'include_image_language': 'en,null'}, timeout=8).json()
            posters = sorted(imgs.get('posters') or [], key=lambda p: -(p.get('vote_average') or 0))
            for p in posters[:12]:
                add(f"https://image.tmdb.org/t/p/w780{p['file_path']}", 'tmdb', 'TMDB')
        except Exception:
            pass
        if tmdb_type == 'tv':
            try:
                r = _http.get(f"https://api.themoviedb.org/3/tv/{tmdb_id}",
                              params={'api_key': _tmdb_key()}, timeout=8).json()
                for s in r.get('seasons') or []:
                    if s.get('poster_path'):
                        label = f"TMDB · {s.get('name') or ('Temporada ' + str(s.get('season_number')))}"
                        add(f"https://image.tmdb.org/t/p/w780{s['poster_path']}", 'tmdb', label)
            except Exception:
                pass

    if v.get('mal_id'):
        try:
            r = _rhttp.get(f"{_JIKAN}/anime/{v['mal_id']}", timeout=8).json()
            img = ((r.get('data') or {}).get('images') or {}).get('jpg') or {}
            add(img.get('large_image_url'), 'mal', 'MyAnimeList')
        except Exception:
            pass

    return out


@anime_bp.route('/library/<anime_id>/cover_options', methods=['GET'])
def anime_cover_options(anime_id):
    """Candidate covers from every source we know for this entry, for the
    manual cover picker (replaces the old per-episode rename button)."""
    lib = _lib_read()
    v = lib.get(anime_id)
    if not v:
        return jsonify({'error': 'not found'}), 404
    return jsonify({'options': _cover_candidates(v), 'current': v.get('cover')})


@anime_bp.route('/library/<anime_id>/cover', methods=['POST'])
def anime_set_cover(anime_id):
    """Manually pin a cover. Body: {cover: url, source?: str}. Marks
    cover_locked so the periodic TMDB/AniList backfill never overwrites a
    choice the user made on purpose."""
    data = request.get_json(silent=True) or {}
    url = (data.get('cover') or '').strip()
    if not url:
        return jsonify({'error': 'cover required'}), 400

    lib = _lib_read()
    if anime_id not in lib:
        return jsonify({'error': 'not found'}), 404

    lib[anime_id]['cover'] = url
    lib[anime_id]['cover_source'] = (data.get('source') or 'manual').strip()
    lib[anime_id]['cover_locked'] = True
    _lib_write(lib)
    threading.Thread(target=_warm_img, args=(url,), daemon=True).start()
    return jsonify({'ok': True, 'cover': url})


@anime_bp.route('/library/<anime_id>/reset_cover', methods=['POST'])
def anime_reset_cover(anime_id):
    """Re-resolve and invalidate poster art for exactly one library entry."""
    from api.runtime import cache_get, cache_invalidate

    lib = _lib_read()
    anime = lib.get(anime_id)
    if not anime:
        return jsonify({'error': 'not found'}), 404

    old_urls = {anime.get('cover'), anime.get('cover_xl'), _hd(anime.get('cover_xl', ''))}
    old_tmdb_id = anime.get('tmdb_id')
    old_tmdb_type = anime.get('tmdb_type', 'tv')
    al_id = anime.get('al_id')
    fresh = None
    if al_id:
        try:
            fresh = _anilist_enrich(int(al_id))
        except Exception as e:
            record_error('anime', e, op='cover_reset_anilist', anime_id=anime_id)

    media = fresh or {}
    cover_xl = (media.get('coverImage') or {}).get('extraLarge') or anime.get('cover_xl') or ''
    titles = media.get('title') or {}
    title_en = anime.get('title_english') or titles.get('english') or anime.get('title', '')
    title_romaji = anime.get('title_romaji') or titles.get('romaji') or ''
    year = anime.get('season_year') or media.get('seasonYear')
    fmt = anime.get('format') or media.get('format')

    art = None
    if _tmdb_key() and (title_en or title_romaji):
        try:
            art = _tmdb_art(title_en, title_romaji, year, fmt=fmt)
        except Exception as e:
            record_error('anime', e, op='cover_reset_tmdb', anime_id=anime_id)

    poster = (art or {}).get('poster') or cover_xl or anime.get('cover', '')
    if not poster:
        return jsonify({'error': 'no se encontró una portada para restablecer'}), 502

    if art:
        if art.get('tmdb_id'):
            anime['tmdb_id'] = art['tmdb_id']
        if art.get('tmdb_type'):
            anime['tmdb_type'] = art['tmdb_type']
    anime['cover_xl'] = cover_xl
    anime['cover'] = poster
    anime['cover_source'] = 'tmdb' if (art or {}).get('poster') else ('anilist' if cover_xl else anime.get('cover_source', ''))
    anime['cover_locked'] = False
    anime['_tmdb_tried'] = True
    anime['_cover_tried'] = True
    anime['_season_tried'] = True
    try:
        anime['cover_rev'] = int(anime.get('cover_rev') or 0) + 1
    except (TypeError, ValueError):
        anime['cover_rev'] = 1

    # Episode cards have an independent frame cache plus TMDB still metadata/cache.
    # Invalidate only the selected show's old/new season entries.
    episode_meta_sources = {
        str(tmdb_id): tmdb_type
        for tmdb_id, tmdb_type in ((old_tmdb_id, old_tmdb_type),
                                   (anime.get('tmdb_id'), anime.get('tmdb_type', 'tv')))
        if tmdb_id and tmdb_type == 'tv'
    }
    episode_meta_keys = set()
    for tmdb_id in episode_meta_sources:
        season, grouped = _episode_meta_season(
            tmdb_id, year, title_en, title_romaji or anime.get('title', ''))
        if season is not None:
            episode_meta_keys.add(_episode_meta_cache_key(tmdb_id, season, grouped))
    for key in episode_meta_keys:
        cached_meta = cache_get('ep_meta', key, _EP_META_TTL) or {}
        cache_invalidate('ep_meta', key)
        if isinstance(cached_meta, dict):
            old_urls.update(
                item.get('still') for item in cached_meta.values()
                if isinstance(item, dict) and item.get('still')
            )

    for url in old_urls | {anime.get('cover'), anime.get('cover_xl'), _hd(anime.get('cover_xl', ''))}:
        if not url:
            continue
        try:
            _invalidate_img(url)
        except OSError as e:
            record_error('anime', e, op='cover_reset_cache', anime_id=anime_id)
            return jsonify({'error': 'no se pudo invalidar la caché de portada'}), 500

    _borrar_thumbs(anime_id)
    _lib_write(lib)
    return jsonify({
        'ok': True,
        'cover': anime['cover'],
        'cover_xl': anime['cover_xl'],
        'cover_rev': anime['cover_rev'],
    })


_BACKDROP_TARGETS = {'banner', 'banner_detail'}


def _backdrop_candidates(v: dict) -> list:
    """Wide (horizontal) hero-art candidates for the manual background picker —
    a separate pool from _cover_candidates because posters are vertical and
    don't fit a wide hero frame well. AniList's bannerImage is re-fetched fresh
    here (not read from v['banner'], which may already have been overwritten by
    a TMDB backdrop) as the safe baseline; TMDB contributes every backdrop
    uploaded for the matched id, not just the one _tmdb_art auto-picked.
    Deduped by URL, each tagged with its source/label."""
    out, seen = [], set()

    def add(url, source, label):
        if url and url not in seen:
            seen.add(url)
            out.append({'url': url, 'source': source, 'label': label})

    al_id = v.get('al_id')
    if al_id:
        try:
            r = _anilist_post('query($id:Int){Media(id:$id,type:ANIME){bannerImage}}', {'id': int(al_id)})
            banner = ((r.json().get('data') or {}).get('Media') or {}).get('bannerImage') if r is not None else None
            add(banner, 'anilist', 'AniList')
        except Exception:
            pass

    tmdb_id, tmdb_type = v.get('tmdb_id'), v.get('tmdb_type', 'tv')
    if tmdb_id and _tmdb_key():
        try:
            imgs = _http.get(f"https://api.themoviedb.org/3/{tmdb_type}/{tmdb_id}/images",
                              params={'api_key': _tmdb_key(), 'include_image_language': 'en,null'}, timeout=8).json()
            backdrops = sorted(imgs.get('backdrops') or [], key=lambda b: -(b.get('vote_average') or 0))
            for b in backdrops[:12]:
                add(f"https://image.tmdb.org/t/p/w1280{b['file_path']}", 'tmdb', 'TMDB')
        except Exception:
            pass

    return out


@anime_bp.route('/library/<anime_id>/backdrop_options', methods=['GET'])
def anime_backdrop_options(anime_id):
    """Candidate wide background images (TMDB backdrops + AniList banner) for
    the manual background picker. ?target=banner (Home hero, default) or
    banner_detail (this series' own detail-page header) — they can be set
    independently so the two contexts don't have to share one image."""
    target = request.args.get('target', 'banner')
    if target not in _BACKDROP_TARGETS:
        return jsonify({'error': 'invalid target'}), 400
    lib = _lib_read()
    v = lib.get(anime_id)
    if not v:
        return jsonify({'error': 'not found'}), 404
    current = v.get(target) or (v.get('banner') if target == 'banner_detail' else '')
    return jsonify({'options': _backdrop_candidates(v), 'current': current})


@anime_bp.route('/library/<anime_id>/backdrop', methods=['POST'])
def anime_set_backdrop(anime_id):
    """Manually pin a wide background. Body: {backdrop: url, source?: str,
    target?: 'banner'|'banner_detail'}. Marks <target>_locked so the periodic
    TMDB/AniList backfill never overwrites a choice the user made on purpose
    (only applies to 'banner' — banner_detail is never touched by backfill)."""
    data = request.get_json(silent=True) or {}
    url = (data.get('backdrop') or '').strip()
    target = data.get('target') or 'banner'
    if not url:
        return jsonify({'error': 'backdrop required'}), 400
    if target not in _BACKDROP_TARGETS:
        return jsonify({'error': 'invalid target'}), 400

    lib = _lib_read()
    if anime_id not in lib:
        return jsonify({'error': 'not found'}), 404

    lib[anime_id][target] = url
    lib[anime_id][f'{target}_source'] = (data.get('source') or 'manual').strip()
    lib[anime_id][f'{target}_locked'] = True
    _lib_write(lib)
    threading.Thread(target=_warm_img, args=(url,), daemon=True).start()
    return jsonify({'ok': True, 'backdrop': url, 'target': target})


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
    carpetas = _carpetas_de(entry)
    if carpetas and delete_files:
        # TODAS sus carpetas: una serie repartida entre discos dejaría los ficheros de la
        # otra ocupando espacio sin que nada volviese a mencionarlos.
        for ep in _escanear_carpetas(entry):
            try:
                _Path(ep['path']).unlink()
            except Exception:
                pass
        entry.pop('local_path', None)
        entry.pop('local_paths', None)
        entry.pop('episode_overrides', None)
        sp = _scanpaths_read()
        quitados = [sp.get('mappings', {}).pop(c, None) for c in carpetas]   # lista: sin cortocircuito
        if any(q is not None for q in quitados):
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

    if delete_files:
        _borrar_thumbs(anime_id)

    entry['episodes'] = {}
    entry.pop('batch_files', None)
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
    # `exists` va aparte del nombre: un disco desenchufado tiene que VERSE, no comportarse igual
    # que una carpeta vacía. Cuesta un is_dir por raíz, no por carpeta.
    return jsonify([{'path': p, 'exists': _Path(_norm_scan_root(p)).is_dir()}
                    for p in _scanpaths_read().get('paths', [])])


@anime_bp.route('/scanpaths', methods=['POST'])
def anime_add_scanpath():
    body = request.get_json(silent=True) or {}
    path = _norm_scan_root(body.get('path') or '')
    if not path:
        return jsonify({'error': 'Escribe una ruta'}), 400
    # Guardar una ruta que no existe la deja ahí sin detectar nada y sin decir por qué: la queja
    # "la ruta no se queda" era esto. Se rechaza en el momento, con el motivo.
    if not _Path(path).is_dir():
        return jsonify({'error': f'No existe la carpeta: {path}'}), 400
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
    norm = _norm_scan_root(path)
    data = _scanpaths_read()
    # Compara en crudo Y normalizado: las guardadas antes de normalizar siguen siendo borrables.
    data['paths'] = [p for p in data.get('paths', []) if p != path and _norm_scan_root(p) != norm]
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
            if mapped_id is None:
                mapped_id = next((value for saved_folder, value in mappings.items()
                                  if _norm_scan_root(saved_folder) == _norm_scan_root(folder)), None)
            matched = lib.get(_lib_key(lib, al_id=mapped_id)) if mapped_id else None
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


_SUGGEST_TTL = 30 * 86400        # el nombre de una carpeta no cambia; la respuesta tampoco
_SUGGEST_MISS = '__sin_sugerencia__'


@anime_bp.route('/scan/suggest', methods=['GET'])
def anime_scan_suggest():
    """Return the best AniList match for a folder name (called lazily per row)."""
    folder_name = request.args.get('name', '').strip()
    if not folder_name:
        return jsonify(None)
    # El nombre de una carpeta no cambia, y la respuesta de AniList para ese nombre tampoco: sin
    # caché, abrir el modal costaba una llamada por carpeta sin enlazar (~56) CADA VEZ. Se cachea
    # también el "no hay nada" (`{}`), que es un resultado tan válido como los demás y era el que
    # más se repetía. `_SUGGEST_MISS` lo distingue de "no estaba cacheado".
    from api.runtime import cache_get, cache_set
    hit = cache_get('anime_suggest', folder_name, _SUGGEST_TTL)
    if hit is not None:
        return jsonify(None if hit == _SUGGEST_MISS else hit)

    def _remember(value):
        cache_set('anime_suggest', folder_name, value if value else _SUGGEST_MISS,
                  ttl=_SUGGEST_TTL, max_entries=2000)
        return jsonify(value)

    try:
        search_name = _clean_folder_name(folder_name)
        q = '''query($s:String){Page(perPage:5){media(search:$s,type:ANIME,sort:SEARCH_MATCH){
            id title{romaji english}coverImage{ extraLarge large medium }}}}'''

        def _search(name):
            r = _rhttp.post(_ANILIST, json={'query': q, 'variables': {'s': name}}, timeout=8)
            return ((r.json().get('data') or {}).get('Page') or {}).get('media') or []

        # Search with the RAW folder name first — AniList titles routinely spell
        # out "2nd Season"/"Part 2" themselves, so keeping that marker lets
        # AniList's own SEARCH_MATCH find the season-specific entry directly
        # (e.g. "Medalist 2nd Season" -> only the season-2 id). Stripping it
        # first (as _clean_folder_name does, for fansub-noisy names AniList
        # can't parse raw) turns the query generic and can rank the *base*
        # show's season-1 entry above the season the folder is actually for —
        # which silently linked a season-2 download to the season-1 entry.
        items = _search(folder_name)
        if not items and search_name != folder_name:
            items = _search(search_name)
        if not items:
            return _remember(None)

        norm_folder = _normalize(folder_name)
        norm_clean  = _normalize(search_name)

        def _score(item):
            t = item.get('title') or {}
            candidates = [t.get('romaji', ''), t.get('english', '') or '']
            for c in candidates:
                nc = _normalize(c)
                if nc == norm_folder:
                    return 4  # exact match against the full, season-preserving name
            for c in candidates:
                nc = _normalize(c)
                if nc == norm_clean:
                    return 3  # exact match only once the season marker is stripped
                if nc and (nc in norm_clean or norm_clean in nc):
                    return 2  # substring match
            return 1  # best AniList relevance, no title match

        best = max(items, key=_score)
        t = best.get('title') or {}
        return _remember({
            'id': best['id'],
            'title': t.get('english') or t.get('romaji', ''),
            'cover': _al_cover(best),
        })
    except Exception as e:
        # Un fallo de red NO se cachea: cachearlo dejaría la carpeta sin sugerencia un mes.
        record_error('anime', e, op='scan_suggest', name=folder_name)
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
    folder     = _norm_scan_root(body.get('folder') or '')
    anilist_id = str(body.get('anilist_id', '')).strip()
    title      = (body.get('title') or '').strip()
    cover      = (body.get('cover') or '').strip()
    if not folder or not anilist_id:
        return jsonify({'error': 'folder and anilist_id required'}), 400
    try:
        anilist_num = int(anilist_id)
    except (TypeError, ValueError):
        return jsonify({'error': 'anilist_id must be numeric'}), 400
    if anilist_num <= 0:
        return jsonify({'error': 'anilist_id must be positive'}), 400
    if not _Path(folder).is_dir():
        return jsonify({'error': f'No existe la carpeta: {folder}'}), 400

    data = _scanpaths_read()
    mappings = data.setdefault('mappings', {})
    saved_folder = next((saved for saved in mappings
                         if saved == folder or _norm_scan_root(saved) == folder), None)
    if saved_folder is not None and str(mappings[saved_folder]) != anilist_id:
        return jsonify({'error': 'La carpeta ya esta enlazada a otra serie'}), 409
    if saved_folder is not None and saved_folder != folder:
        del mappings[saved_folder]
    mappings[folder] = anilist_id
    _scanpaths_write(data)

    # Fetch full AniList metadata so we store episodes/format/romaji
    al_meta = {}
    try:
        q = '''query($id:Int){Media(id:$id,type:ANIME){
            episodes format status
            title{romaji english native}
            coverImage{ extraLarge large medium }
        }}'''
        r = _rhttp.post(_ANILIST, json={'query': q, 'variables': {'id': anilist_num}}, timeout=8)
        al_media = (r.json().get('data') or {}).get('Media') or {}
        if al_media:
            t = al_media.get('title') or {}
            al_meta = {
                'title':         t.get('english') or t.get('romaji') or title,
                'title_romaji':  t.get('romaji') or '',
                'title_native':  t.get('native') or '',
                'cover':         _al_cover(al_media) or cover,
                'total_episodes': al_media.get('episodes'),
                'format':        al_media.get('format') or '',
                # OJO: `al_media['status']` es el estado de EMISIÓN (FINISHED/RELEASING). NO va a
                # `status`, que significa "qué he hecho YO con esta serie" — escribirlo ahí
                # marcaba como vista toda serie terminada nada más enlazar la carpeta.
                'airing_status': al_media.get('status') or '',
            }
    except Exception as e:
        record_error('anime', e, op='scan_match_metadata', anilist_id=anilist_id)

    lib = _lib_read()
    library_id = _lib_key(lib, al_id=anilist_num, title=title)
    if library_id not in lib:
        lib[library_id] = {'title': title, 'cover': cover, 'episodes': {}}
    _anadir_carpeta(lib[library_id], folder)
    lib[library_id]['al_id'] = anilist_num
    for k, v in al_meta.items():
        if v and (not lib[library_id].get(k)):
            lib[library_id][k] = v
    # Always update total_episodes and format from AniList (they may improve)
    if al_meta.get('total_episodes'):
        lib[library_id]['total_episodes'] = al_meta['total_episodes']
    if al_meta.get('format'):
        lib[library_id]['format'] = al_meta['format']
    _lib_write(lib)
    return jsonify({'ok': True, 'id': library_id})


@anime_bp.route('/scan/unmatch', methods=['POST'])
def anime_scan_unmatch():
    body = request.get_json(silent=True) or {}
    folder = _norm_scan_root(body.get('folder') or '')
    if not folder:
        return jsonify({'error': 'folder required'}), 400
    data = _scanpaths_read()
    mappings = data.get('mappings', {})
    saved_folder = next((saved for saved in mappings
                         if saved == folder or _norm_scan_root(saved) == folder), None)
    anilist_id = mappings.pop(saved_folder, None) if saved_folder is not None else None
    _scanpaths_write(data)
    if anilist_id:
        lib = _lib_read()
        library_id = _lib_key(lib, al_id=anilist_id)
        if library_id in lib:
            _quitar_carpeta(lib[library_id], folder)
            if not _carpetas_de(lib[library_id]) and not lib[library_id].get('episodes'):
                lib.pop(library_id, None)
        _lib_write(lib)
    return jsonify({'ok': True})


def _runtime_fs_path(raw: str) -> str:
    raw = str(raw or '').strip()
    if _is_wsl() and re.match(r'^[A-Za-z]:[/\\]', raw):
        return _win_to_wsl(raw)
    return raw


def _path_is_within(path: str, root: str) -> bool:
    try:
        target = _Path(_runtime_fs_path(path)).expanduser().resolve()
        base = _Path(_runtime_fs_path(root)).expanduser().resolve()
        if base.is_file():
            return target == base
        target.relative_to(base)
        return True
    except (OSError, ValueError):
        return False


def _path_registered_for_anime(anime_id: str, local_path: str) -> bool:
    """Comprueba que ``local_path`` pertenece a una carpeta ya vinculada a la serie."""
    anime = (_lib_read() or {}).get(str(anime_id))
    if not anime:
        return False
    return any(_path_is_within(local_path, folder) for folder in _carpetas_de(anime))


def _path_matches_torrent(local_path: str, info_hash: str) -> bool | None:
    """Permite un fichero local de un torrent completado aunque aún no esté vinculado como carpeta."""
    if not info_hash:
        return False
    try:
        torrents = _q('get', '/torrents/info', params={'hashes': info_hash}).json()
    except Exception:
        return None
    if not isinstance(torrents, list):
        return False
    for torrent in torrents or []:
        content = torrent.get('content_path') or ''
        if content and _path_is_within(local_path, content):
            return True
        # A single-file torrent reports the file as content_path; its .a4k sibling
        # legitimately lives beside it. Do not widen this to all of save_path:
        # qBittorrent's save_path can contain unrelated downloads.
        if content and _single_file_derivative(content, local_path):
            return True
        if not content and torrent.get('save_path') and _path_is_within(local_path, torrent['save_path']):
            return True
    return False


def _single_file_derivative(original: str, candidate: str) -> bool:
    try:
        source = _Path(_runtime_fs_path(original)).expanduser().resolve()
        target = _Path(_runtime_fs_path(candidate)).expanduser()
        return (source.is_file() and not target.is_symlink()
                and target.name == f'{source.stem}{_A4K_SUF}{source.suffix}'
                and target.parent.resolve() == source.parent)
    except (OSError, ValueError):
        return False


def _torrent_result_is_safe(video: str, torrent: dict) -> bool:
    """Comprueba también la ruta que `_find_video` eligió dentro de qBittorrent."""
    content = torrent.get('content_path') or ''
    if content and _path_is_within(video, content):
        return True
    if content and _single_file_derivative(content, video):
        return True
    return bool(not content and torrent.get('save_path')
                and _path_is_within(video, torrent['save_path']))


def _local_result_is_safe(local_path: str, video: str) -> bool:
    """Evita que `_find_video` devuelva un symlink/a4k que salga del árbol pedido."""
    raw = _runtime_fs_path(local_path)
    try:
        base = _Path(raw).expanduser().resolve()
        root = base if base.is_dir() else str(base.parent)
    except (OSError, ValueError):
        return False
    return _path_is_within(video, root)


def _local_result_matches_torrent(anime_id: str, local_path: str,
                                  video: str, info_hash: str) -> bool | None:
    """Mantiene la misma autorización al pasar del original al resultado `.a4k`."""
    if not anime_id or _path_registered_for_anime(anime_id, local_path):
        return True
    if not info_hash or _Path(_runtime_fs_path(video)).is_symlink():
        return False
    return _path_matches_torrent(video, info_hash)


def _local_path_allowed(anime_id: str, local_path: str, data: dict | None = None) -> bool | None:
    if anime_id:
        return (_path_registered_for_anime(anime_id, local_path)
                or _path_matches_torrent(local_path, str((data or {}).get('info_hash') or '').lower()))
    if has_request_context():
        from api.auth import peticion_local
        return peticion_local()
    return True


def resolve_episode_video(data: dict):
    """Resuelve la ruta del archivo de vídeo de un episodio (carpeta local o
    qBittorrent) — lógica compartida entre el lanzador de MPV (anime_play) y el
    streaming del player web (api.stream). Devuelve (ruta|None, (msg, status)|None)."""
    episode    = data.get('episode', -1)
    local_path = (data.get('local_path') or '').strip()
    anime_id   = data.get('anime_id', '')
    ep_str     = str(data.get('episode_key') or episode)

    if local_path:
        allowed = _local_path_allowed(anime_id, local_path, data)
        if allowed is None:
            return None, ('qBittorrent unavailable while validating local path', 502)
        if not allowed:
            return None, ('local path is not registered', 403)
        video = _find_video(local_path, int(episode))
        if not video:
            return None, (f'no video found in: {local_path}', 404)
        if not _local_result_is_safe(local_path, video):
            return None, ('local path is not registered', 403)
        result_allowed = _local_result_matches_torrent(
            anime_id, local_path, video, str(data.get('info_hash') or '').lower())
        if result_allowed is None:
            return None, ('qBittorrent unavailable while validating local path', 502)
        if not result_allowed:
            return None, ('local path is not registered', 403)
        return video, None

    info_hash = (data.get('info_hash') or '').lower()
    if not info_hash:
        return None, ('info_hash required', 400)
    try:
        torrents = _q('get', '/torrents/info', params={'hashes': info_hash}).json()
    except Exception as e:
        return None, (f'qBT error: {e}', 500)
    if not torrents and anime_id:
        lib = _lib_read()
        batch_hash = (lib.get(anime_id) or {}).get('episodes', {}).get('0', {}).get('info_hash', '')
        if batch_hash and batch_hash != info_hash:
            try:
                torrents = _q('get', '/torrents/info', params={'hashes': batch_hash}).json()
            except Exception:
                pass
    if not torrents:
        return None, ('torrent not found in qBittorrent', 404)
    content_path = torrents[0].get('content_path') or torrents[0].get('save_path', '')
    if not content_path:
        return None, ('no content path from qBittorrent', 404)
    ep_subpath = ''
    lib_ep = {}
    if anime_id:
        entry = (_lib_read().get(anime_id) or {})
        episode_map = entry.get('episodes') or {}
        lib_ep = episode_map.get(ep_str) or episode_map.get(str(episode), {})
        # A multi-season selected slot is stored as s02e001 while the UI keeps
        # num=1 for episode controls. Match the exact relative path first, then
        # the season+episode identity, never merely the first episode 1.
        wanted_rel = (data.get('relative_path') or '').replace('\\', '/')
        if wanted_rel:
            lib_ep = next((value for value in episode_map.values()
                           if value.get('relative_path', '').replace('\\', '/') == wanted_rel), lib_ep)
        if not lib_ep and data.get('episode') is not None:
            try:
                wanted_num = int(data['episode'])
                lib_ep = next((value for key, value in episode_map.items()
                               if int(value.get('episode', -1)) == wanted_num
                               and value.get('from_batch_selection')), {})
            except (TypeError, ValueError):
                pass
        if not lib_ep:
            lib_ep = episode_map.get('0', {})
        ep_subpath = lib_ep.get('subpath', '')
    relative_path = (data.get('relative_path') or lib_ep.get('relative_path') or '').strip()
    if relative_path:
        video = _qbt_file_path(torrents[0], relative_path)
        # A legacy qBittorrent record may not expose save_path. Keep the old
        # content_path/episode fallback, but never let a missing exact path
        # silently resolve to another episode when the exact file is present.
        if not video:
            video = _find_video(content_path, int(episode), ep_subpath)
    else:
        video = _find_video(content_path, int(episode), ep_subpath)
    if not video:
        return None, (f'no video file found in: {content_path}', 404)
    if not _torrent_result_is_safe(video, torrents[0]):
        return None, ('video path is outside torrent content', 403)
    return video, None


@anime_bp.route('/play', methods=['POST'])
def anime_play():
    try:
        data = request.get_json(silent=True) or {}
        anime_id   = data.get('anime_id', '')
        episode    = data.get('episode', -1)
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

        video, err = resolve_episode_video(data)
        if err:
            return jsonify({'error': err[0]}), err[1]
        if not _launch_and_track(video):
            return jsonify({'error': 'failed to launch mpv — is mpv.exe in PATH?'}), 500
        return jsonify({'ok': True, 'path': video, 'resume_pos': start_pos})
    except Exception as e:
        return jsonify({'error': f'internal error: {e}'}), 500


def _video_to_win(path: str) -> str:
    """Convierte una ruta WSL (/mnt/d/..) a Windows (D:\\..) para libmpv en la shell
    nativa. Fuera de WSL devuelve la ruta tal cual."""
    try:
        return subprocess.check_output(
            ['wslpath', '-w', path], stderr=subprocess.DEVNULL, timeout=3
        ).decode().strip()
    except Exception:
        return path


def _sidecar_subs(path: str) -> list:
    """Subtítulos externos (sidecar) junto al vídeo — el mismo criterio que
    `_find_subtitles`, pero SOLO los de ESTE episodio (basename que empieza por el
    stem del vídeo, p.ej. `Ep01.spa.srt`). Devuelve la ruta Windows para que la shell
    los cargue con `sub-add`, más una etiqueta de idioma inferida del sufijo del nombre.

    Esto UNIFICA el tratamiento entre contenedores: en MKV los subs traducidos se
    incrustan (mkvmerge) y salen por ffprobe; en Blu-ray/.m2ts (donde mkvmerge no puede
    escribir el contenedor) se guardan como sidecar y ANTES no aparecían como pista. Al
    listarlos aquí y cargarlos por `sub-add`, se comportan como una pista normal (con
    delay/tamaño/estilo)."""
    try:
        video = _Path(path)
        if not video.exists():
            return []
    except Exception:
        return []
    # Sufijo de nombre → etiqueta de idioma (video.spa.srt → 'spa').
    _SUF_LANG = {'spa': 'spa', 'es': 'spa', 'esp': 'spa', 'lat': 'spa',
                 'eng': 'eng', 'en': 'eng', 'jpn': 'jpn', 'ja': 'jpn'}
    out = []
    stem = video.stem.lower()
    try:
        entries = sorted(video.parent.iterdir(), key=lambda f: f.name.lower())
    except Exception:
        return []
    for f in entries:
        if f.suffix.lower() not in _SUBTITLE_EXTS:
            continue
        name_l = f.name.lower()
        # Solo sidecars de este episodio (evita mezclar subs de otros vídeos de la carpeta).
        if not name_l.startswith(stem):
            continue
        # Sufijos entre el stem y la extensión → idioma (p.ej. '.spa' en 'Ep01.spa.srt').
        mid = f.name[len(video.stem):-len(f.suffix)].strip('. ').lower()
        lang = ''
        for part in re.split(r'[.\-_ ]+', mid):
            if part in _SUF_LANG:
                lang = _SUF_LANG[part]
                break
        out.append({
            'codec': f.suffix.lower().lstrip('.'), 'lang': lang,
            'title': f.name, 'external': True,
            'win_path': _video_to_win(str(f)),
        })
    return out


def _probe_tracks(path: str) -> dict:
    """Lista pistas de audio/subs (mismo formato que stream.py) para el player nativo.
    Los ids de mpv (aid/sid) son 1-based en orden de aparición → index+1 en el front.
    Las pistas de subtítulos incrustadas van PRIMERO (orden del contenedor) y los
    sidecar externos DESPUÉS, en el mismo orden en que la shell hará `sub-add` → así el
    sid (índice+1) que envía el front coincide con el que asigna mpv."""
    try:
        out = subprocess.check_output(
            ['ffprobe', '-v', 'quiet', '-print_format', 'json', '-show_streams', path],
            stderr=subprocess.DEVNULL, timeout=10,
        ).decode()
        streams = json.loads(out).get('streams') or []
    except Exception:
        streams = []

    def _t(s, rel):
        tags = s.get('tags') or {}
        return {'index': rel, 'codec': s.get('codec_name', ''),
                'lang': tags.get('language', ''), 'title': tags.get('title', ''),
                'external': False}

    astreams = [s for s in streams if s.get('codec_type') == 'audio']
    sstreams = [s for s in streams if s.get('codec_type') == 'subtitle']
    subs = [_t(s, i) for i, s in enumerate(sstreams)]
    # Añadir los sidecar externos DESPUÉS de los incrustados (orden = orden de sub-add).
    for i, ext in enumerate(_sidecar_subs(path)):
        ext['index'] = len(subs)
        subs.append(ext)
    # Pista española preferida (sid 1-based) calculada aquí, no en mpv: --slang sólo mira el
    # código de idioma, pero el español latino vive muchas veces sólo en el TÍTULO ('LAT',
    # 'SPA-LAT', 'Spanish (LAT)'…). sub_lang mira código Y título y prioriza latino. 0 = ninguna.
    best = sub_lang.best_es_index(subs)
    return {
        'audio_tracks':  [_t(s, i) for i, s in enumerate(astreams)],
        'sub_tracks':    subs,
        'preferred_sub': (best + 1) if best >= 0 else 0,
    }


_SCRUB_IV = 10  # segundos entre miniaturas de la barra de progreso (player nativo)
_SCRUB_DIR = _DATA_ROOT / '_native_thumbs'
_scrub_lock = threading.Lock()
_scrub_active = set()  # keys en generación (evita relanzar ffmpeg en paralelo)


def _scrub_key(video: str) -> str:
    import hashlib
    return hashlib.md5(video.encode('utf-8', 'ignore')).hexdigest()[:16]


def _gen_scrub_thumbs(video: str, key: str):
    """Genera t_00001.jpg… (una cada _SCRUB_IV s, ancho 240) para el scrubbing del
    reproductor nativo, en un ffmpeg de fondo. Idempotente: si ya está hecho no repite;
    los archivos de vídeo no cambian, así que la caché no expira."""
    tdir = _SCRUB_DIR / key
    done = tdir / '.done'
    if done.exists():
        return
    with _scrub_lock:
        if key in _scrub_active:
            return
        _scrub_active.add(key)
    try:
        tdir.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ['ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'error', '-y',
             '-i', video, '-vf', f'fps=1/{_SCRUB_IV},scale=240:-2', '-q:v', '5',
             str(tdir / 't_%05d.jpg')],
            stderr=subprocess.DEVNULL, timeout=600,
        )
        done.touch()
    except Exception:
        pass
    finally:
        with _scrub_lock:
            _scrub_active.discard(key)


@anime_bp.route('/scrub/<anime_id>/<int:episode>')
def anime_scrub_info(anime_id, episode):
    """Las miniaturas de la barra de progreso, para un cliente de la RED (el móvil).

    Las genera y las sirve la misma maquinaria que usa el player nativo del PC; lo único que cambia
    es la puerta. El nativo entra por `/native/prepare`, que resuelve la ruta del vídeo porque corre
    en la propia máquina; un móvil **no puede decir ninguna ruta**, así que aquí sólo dice qué
    episodio de qué serie y el fichero sale de la biblioteca del servidor (`video_de_biblioteca`).

    Devuelve la clave y el intervalo; las imágenes se piden una a una a `/native/scrub/<key>/<idx>`.
    Generar es idempotente y va en un hilo: la primera vez el móvil pedirá miniaturas que aún no
    existen y recibirá 404, que es exactamente lo que el cliente ya sabe tratar.
    """
    episode_key = request.args.get('episode_key', '')
    season = request.args.get('season', '').strip()
    if not episode_key and season.isdigit():
        episode_key = f's{int(season):02d}e{episode:03d}'
    video, err = video_de_biblioteca(anime_id, episode, episode_key)
    if err:
        return jsonify({'error': err[0]}), err[1]
    key = _scrub_key(video)
    threading.Thread(target=_gen_scrub_thumbs, args=(video, key), daemon=True).start()
    return jsonify({'key': key, 'interval': _SCRUB_IV})


@anime_bp.route('/native/scrub/<key>/<int:idx>')
def anime_native_scrub(key, idx):
    """Sirve la miniatura idx (1-based) del scrubbing. 404 si aún no está generada
    (el front oculta la imagen y reintenta al mover el cursor)."""
    if not re.fullmatch(r'[0-9a-f]{1,32}', key or ''):
        return ('', 404)
    p = _SCRUB_DIR / key / f't_{idx:05d}.jpg'
    if p.exists() and p.stat().st_size > 0:
        return send_file(str(p), mimetype='image/jpeg', max_age=604800)
    return ('', 404)


def video_de_biblioteca(anime_id: str, episode: int, episode_key: str = ''):
    """Resolve a library episode without accepting arbitrary client paths.

    `episode_key` is optional for legacy/mobile callers. Granular callers send
    `sXXeYYY`, which prevents two seasons' episode 1 from collapsing together.
    """
    anime = (_lib_read() or {}).get(str(anime_id))
    if not anime:
        return None, ('anime not in library', 404)
    ep_map = anime.get('episodes') or {}
    key = str(episode_key or '').strip() or str(episode)
    ep = ep_map.get(key) or ep_map.get(str(episode)) or {}
    datos = {'anime_id': str(anime_id), 'episode': episode, 'episode_key': key}

    if ep.get('info_hash'):
        datos.update({
            'info_hash': ep['info_hash'],
            'file_index': ep.get('file_index'),
            'relative_path': ep.get('relative_path', ''),
        })
        video, error = resolve_episode_video(datos)
        if video:
            return video, None
        if error and error[1] == 403:
            return None, error
        local = _local_relative_file(anime, ep.get('relative_path', ''))
        if local:
            return local, None
        # qBittorrent is optional after a file is safely in the library. For
        # legacy records, retain the established folder scan as a final fallback.
        if not ep.get('from_batch_selection'):
            local = _buscar_video(anime, episode)
            if local:
                if any(_path_is_within(local, folder) for folder in _carpetas_de(anime)):
                    return local, None
                return None, ('video path is outside library', 403)
        return video, error

    # Local-only legacy records may not have a torrent identity. Prefer the
    # exact file path stored by the scanner; otherwise retain numeric fallback.
    local_file = ep.get('local_path', '')
    if local_file and _Path(local_file).is_file():
        datos['local_path'] = local_file
        return resolve_episode_video(datos)
    carpeta = next((c for c in _carpetas_de(anime) if _find_video(c, episode)), '')
    if carpeta:
        datos['local_path'] = carpeta
        return resolve_episode_video(datos)
    return None, ('episode has no source', 404)


@anime_bp.route('/stream/<anime_id>/<int:episode>')
def anime_stream(anime_id, episode):
    """Sirve el vídeo del episodio para el reproductor del MÓVIL.

    El móvil no torrentea ni monta discos: ve el fichero por HTTP. `conditional=True` es lo que
    activa las peticiones por rango, y sin rangos no hay saltar al minuto 12 — mpv tendría que
    tragarse el MKV entero desde el principio.
    """
    episode_key = request.args.get('episode_key', '')
    season = request.args.get('season', '').strip()
    if not episode_key and season.isdigit():
        episode_key = f's{int(season):02d}e{episode:03d}'
    video, err = video_de_biblioteca(anime_id, episode, episode_key)
    if err:
        return jsonify({'error': err[0]}), err[1]
    return send_file(video, conditional=True)


@anime_bp.route('/episode_torrents/<anime_id>/<int:episode>')
def anime_episode_torrents(anime_id, episode):
    """Torrents de UN episodio concreto de una serie que ya está en la biblioteca.

    Existe para el MÓVIL. En el escritorio, la búsqueda por episodio la arma `stores/anime.js`:
    resuelve los alias del anime, monta una consulta por alias (`«Título - 04»`) y funde las
    listas. Reproducir ese cálculo en Kotlin sería mantener el mismo criterio en dos idiomas, y
    el día que Nyaa cambie de forma sólo se arreglaría uno de los dos.

    Las consultas se arman a partir de la ficha del SERVIDOR (`anime_id`), no de un título que
    mande el cliente: el móvil pide «el episodio 4 de esto que ya tengo», no una búsqueda libre.
    """
    anime = _lib_read().get(str(anime_id))
    if not anime:
        return jsonify({'error': 'anime not in library'}), 404

    base = [t for t in (anime.get('title_romaji'), anime.get('title')) if t]
    # Los sinónimos sólo se piden POR ID. Buscar en AniList por nombre trae la ficha de otra obra
    # cuando el título es genérico, y entonces las consultas de Nyaa se llenan de alias ajenos.
    if anime.get('al_id'):
        try:
            from api.anilist import title_variants
            base = title_variants(base[0] if base else None, anime['al_id'], 'ANIME') or base
        except Exception:
            pass

    pad = f'{episode:02d}'
    # Cat 1_0 (todo el anime) y no 1_2: si no, las releases en español no aparecen nunca.
    vistos, salida = set(), []
    for t in base[:4]:
        for torrent in _nyaa_search(f'{t} - {pad}', '1_0'):
            # El episodio suelto o un lote que lo contenga; lo demás es ruido de otra consulta.
            if torrent['episode'] not in (episode, 0):
                continue
            clave = torrent['info_hash'] or torrent['title']
            if clave in vistos:
                continue
            vistos.add(clave)
            salida.append(torrent)

    salida.sort(key=lambda t: (not t['trusted'], -t['seeders']))
    return jsonify(salida[:30])


@anime_bp.route('/episode_download/<anime_id>/<int:episode>', methods=['POST'])
def anime_episode_download(anime_id, episode):
    """Encarga al PC la descarga de un episodio. Es lo que pulsa el MÓVIL.

    **La carpeta de destino no viaja nunca.** El cliente elige un torrent de la lista que le dio
    `episode_torrents` y nada más; el dónde lo pone el servidor con `_anime_save_dir`, igual que
    cuando se pulsa en el escritorio. Un endpoint que aceptase `save_path` de la red sería escribir
    donde diga quien llame, y «es una app local» no es una defensa.
    """
    lib = _lib_read()
    anime = lib.get(str(anime_id))
    if not anime:
        return jsonify({'error': 'anime not in library'}), 404
    datos = request.get_json(silent=True) or {}
    link = (datos.get('magnet') or datos.get('torrent_url') or '').strip()
    if not link:
        return jsonify({'error': 'no link'}), 400

    try:
        ok, msg = _qbt_add_link(link, _anime_save_dir(anime.get('title') or anime.get('title_romaji') or ''))
    except Exception as e:
        return jsonify({'ok': False, 'msg': str(e)}), 502
    if not ok:
        return jsonify({'ok': False, 'msg': msg}), 502

    # Se apunta bajo el episodio PEDIDO aunque el torrent sea un lote: lo que el usuario quiere ver
    # es ese episodio, y el resto del lote lo recoge igual el escaneo de la carpeta local.
    anime.setdefault('episodes', {})[str(episode)] = {
        'title':     datos.get('torrent_title', ''),
        'info_hash': (datos.get('info_hash') or '').lower(),
        'added_on':  int(time.time()),
    }
    _lib_write(lib)
    return jsonify({'ok': True})


@anime_bp.route('/native/resolve', methods=['POST'])
def anime_native_resolve():
    """Resuelve la ruta del vídeo para el reproductor NATIVO embebido (libmpv en la
    shell Windows), SIN lanzar mpv externo — el motor vive en la shell y recibe la
    ruta por IPC. Devuelve la ruta Windows + la posición de reanudación."""
    try:
        data = request.get_json(silent=True) or {}
        video, err = resolve_episode_video(data)
        if err:
            return jsonify({'error': err[0]}), err[1]
        anime_id = data.get('anime_id', '')
        ep_str   = str(data.get('episode_key') or data.get('episode', -1))
        if data.get('start_pos') is not None:
            resume = float(data['start_pos'])
        elif anime_id:
            resume = float((_lib_read().get(anime_id) or {}).get('positions', {}).get(ep_str, 0))
        else:
            resume = 0.0
        # Miniaturas de la barra de progreso: generarlas en background y devolver
        # su base + intervalo + duración (para mapear hover→índice en el front).
        key = _scrub_key(video)
        threading.Thread(target=_gen_scrub_thumbs, args=(video, key), daemon=True).start()
        return jsonify({
            'ok': True,
            'path': video,
            'win_path': _video_to_win(video),
            'resume_pos': resume,
            'duration': _video_duration(video),
            'thumbs': {'url': f'/api/anime/native/scrub/{key}', 'interval': _SCRUB_IV},
            **_probe_tracks(video),
        })
    except Exception as e:
        return jsonify({'error': f'internal error: {e}'}), 500


@anime_bp.route('/native/progress', methods=['POST'])
def anime_native_progress():
    """Persiste posición/visto desde el reproductor nativo (el motor manda el tiempo
    por IPC). Misma lógica de umbral y misma estructura que _track_mpv_session, para
    que resume/visto/historial se comporten igual que con MPV externo."""
    from api.runtime import push_sse_event
    from api.contracts import NativeProgressBody, validate_and_log
    try:
        data = request.get_json(silent=True) or {}
        validate_and_log(NativeProgressBody, data, "anime/native/progress")
        anime_id = data.get('anime_id', '')
        ep_str   = str(data.get('episode_key') or data.get('episode', -1))
        position = float(data.get('position', 0))
        duration = float(data.get('duration', 0))
        ended    = bool(data.get('ended', False))
        if not anime_id:
            return jsonify({'ok': True})

        if ended:
            watched, save_pos = True, 0
        elif position > 0 and duration > 0:
            watched  = _is_watched(position, duration)
            save_pos = 0 if watched else int(position)
        elif position > 0:
            watched, save_pos = False, int(position)
        else:
            watched, save_pos = False, 0

        lib = _lib_read()
        if anime_id not in lib:
            return jsonify({'ok': True})
        if duration > 0:
            lib[anime_id].setdefault('durations', {})[ep_str] = int(duration)
        _guardar_posicion(lib[anime_id], ep_str, save_pos)

        lib[anime_id]['last_ep'] = ep_str   # ver el comentario en _track: la UI no puede adivinarlo

        if watched:
            was_watched = bool(lib[anime_id].get('watched', {}).get(ep_str))
            lib[anime_id].setdefault('watched', {})[ep_str] = True
            now = int(time.time())
            lib[anime_id]['last_watched_at'] = now
            _lib_write(lib)
            if not was_watched:   # solo la PRIMERA vez que pasa a visto (evita duplicados)
                _history_append(anime_id, lib[anime_id].get('title', anime_id),
                                _episode_number(ep_str), lib[anime_id].get('cover', ''))
            push_sse_event('watched', anime_id=anime_id, ep_str=ep_str,
                           last_watched_at=now, watched=True,
                           duration=int(duration), from_mpv=True)
        else:
            # Progreso parcial: bump de last_watched_at para que "Continuar viendo"
            # muestre/reordene la serie al instante (no solo al completar).
            now = int(time.time())
            if save_pos > 30:
                lib[anime_id]['last_watched_at'] = now
            _lib_write(lib)
            push_sse_event('position', anime_id=anime_id, ep_str=ep_str,
                           position=save_pos, duration=int(duration),
                           last_watched_at=(now if save_pos > 30 else 0))
        return jsonify({'ok': True, 'watched': watched, 'saved_pos': save_pos})
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

    # Caché disco (stale-while-revalidate): tras un reinicio, la temporada pinta
    # al instante desde disco (hasta 6 h de antigüedad) y se refresca en
    # background — abrir la app no espera a AniList ni gasta rate-limit.
    from api.runtime import cache_get, cache_set
    disk = cache_get('seasonal', cache_key, _SEASONAL_DISK_TTL)
    if disk and disk.get('results'):
        if cache_key not in _seasonal_refreshing:
            _seasonal_refreshing.add(cache_key)
            def _bg_refresh(s=season, y=year, sb=sort_by, gs=sort_map.get(sort_by, 'SCORE_DESC'), ck=cache_key):
                try:
                    payload = _fetch_seasonal(s, y, sb, gs)
                    if payload and payload.get('results'):
                        _seasonal_cache[ck] = (payload, time.time())
                        cache_set('seasonal', ck, payload, ttl=_SEASONAL_DISK_TTL)
                finally:
                    _seasonal_refreshing.discard(ck)
            threading.Thread(target=_bg_refresh, daemon=True).start()
        _seasonal_cache[cache_key] = (disk, time.time() - _SEASONAL_TTL)  # RAM: stale a propósito
        return jsonify(disk)

    payload = _fetch_seasonal(season, year, sort_by, gql_sort)
    if payload.get('results'):
        _seasonal_cache[cache_key] = (payload, time.time())
        cache_set('seasonal', cache_key, payload, ttl=_SEASONAL_DISK_TTL)
    return jsonify(payload)


def _fetch_seasonal(season, year, sort_by, gql_sort):
    """Consulta AniList (fallback Jikan) y construye el payload de temporada."""
    import datetime
    gql = '''
    query ($season: MediaSeason, $seasonYear: Int, $sort: [MediaSort]) {
      Page(perPage: 50) {
        media(type: ANIME, season: $season, seasonYear: $seasonYear, sort: $sort, isAdult: false) {
          id idMal
          title { romaji english native }
          meanScore popularity episodes status season seasonYear format
          coverImage{ extraLarge large medium }
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
            'cover':        _al_cover(it),
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
            r = _rhttp.get(jikan_url, params=jparams, timeout=15,
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

    return {'season': season, 'year': year, 'sort': sort_by, 'results': results}


def _episode_number(value) -> int:
    match = re.fullmatch(r'[sS]\d{1,2}[eE](\d{1,4})', str(value or ''))
    if match:
        return int(match.group(1))
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


@anime_bp.route('/library/<anime_id>/watched', methods=['POST'])
def anime_watched_toggle(anime_id):
    data = request.get_json(silent=True) or {}
    episode = str(data.get('episode_key') or data.get('episode', -1))
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
                        _episode_number(episode), lib[anime_id].get('cover', ''))
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


# Ancho del fotograma de episodio. La tarjeta lo pinta a ~742 px FÍSICOS (2560x1440 a dpr 1,5):
# a los 480 de antes el estirón era ×1,55 y los planos con detalle fino (una carrera, multitud)
# salían borrosos mientras los planos quietos aguantaban. El ancho va en el NOMBRE del caché para
# que subirlo re-extraiga solo; los ficheros viejos quedan huérfanos y se pueden borrar a mano.
_THUMB_W = 960


def _thumb_key(anime_id, episode, is_special: bool, episode_key: str = '') -> str:
    identity = str(episode_key or episode).replace('/', '_')
    sp = 'sp' if is_special else ''
    return f'{anime_id}_{sp}{identity}_w{_THUMB_W}.jpg'


def _extraer_fotograma(video: str, dest, seek_secs: float) -> None:
    """Un fotograma a `_THUMB_W`. Doble seek: rápido hasta el keyframe anterior + fino exacto
    (sólo `-ss` antes de `-i` cae en un B/P-frame sin sus referencias → borroso).

    Un fallo DURO (fichero truncado, ruta que no existe) ya sale por el código de retorno — medido:
    un .mkv cortado a la mitad da `rc=234` y no escribe nada. Lo que no se ve aquí es el fotograma
    EMBADURNADO de un vídeo a medio descargar: ffmpeg oculta el error, devuelve 0 y escribe el
    churro. Eso lo juzga `_extraer_fotograma_util` MIRANDO el resultado.
    """
    fine_margin = min(6.0, seek_secs)
    r = subprocess.run(
        ['ffmpeg', '-y',
         '-ss', _secs_to_hms(max(0.0, seek_secs - fine_margin)), '-i', video,
         '-ss', _secs_to_hms(fine_margin),
         '-vframes', '1', '-vf', f'scale={_THUMB_W}:-2', '-q:v', '2', str(dest)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30,
    )
    if r.returncode != 0:
        _Path(dest).unlink(missing_ok=True)
        raise RuntimeError(f'ffmpeg no pudo sacar un fotograma limpio de {video} (rc={r.returncode})')


# Momentos del episodio, en orden de preferencia. El primero es el de siempre (la mitad).
_MOMENTOS_THUMB = (0.50, 0.35, 0.65, 0.20)


# Cuánto puede saltar el brillo en los filos de macrobloque frente al resto antes de considerar
# que el fotograma está embadurnado. CALIBRADO con dos muestras reales del mismo anime, extraídas
# con un minuto de diferencia: la buena da 1,17 y la podrida 3,07. Sobre el caché entero (1099
# fotogramas) la mediana es 1,38 y el percentil 95, 2,47.
_MAX_BLOQUEO = 2.5

# Desviación típica del gris por debajo de la cual el fotograma no enseña NADA (negro, fundido,
# cartel en blanco). BAJO a propósito: una escena nocturna real ronda 6-9 y es utilizable.
_MIN_CONTRASTE = 5.0

# Relleno del decodificador: cuando h264 se queda sin referencias, el hueco queda en el color del
# YUV a cero, que en RGB es VERDE PURO. Se mide qué parte del fotograma es ese verde.
#
# Se probó primero con «el color dominante ocupa más del 60 %» y NO vale: la basura suele traer dos
# verdes distintos y ninguno domina por separado. Y se probó con «saturación alta» a secas: un
# cartel amarillo legítimo de Monogatari da 0,80 y se colaba entre la basura. El tono sí separa —
# medido sobre los 1091 del caché: la basura va de 0,60 a 1,00 y el siguiente fotograma legítimo
# (un bosque) se queda en 0,15. El umbral tiene margen ×2 por los dos lados.
_MAX_VERDE_RELLENO = 0.30


def _juzgar_fotograma(dest) -> str:
    """`'ok'`, `'plano'` o `'sucio'`. Se juzga la IMAGEN, no el proceso.

    Se probó primero con `-xerror` (que ffmpeg aborte al primer error de decodificación) y hay que
    no repetirlo: **hay series enteras con errores esporádicos que se ocultan solos y cuyos
    fotogramas salen perfectos** — Welcome to the Ballroom da 2-3 «error while decoding MB» en
    TODOS sus episodios y la mayoría de sus miniaturas se ven bien. Con `-xerror` esa serie se
    quedaba sin una sola miniatura. Lo que hay que rechazar es el resultado feo, no el camino feo.
    """
    try:
        import numpy as np
        from PIL import Image
        with Image.open(dest) as im:
            rgb = np.asarray(im.convert('RGB'), dtype=np.uint8)
        g = np.asarray(Image.fromarray(rgb).convert('L'), dtype=np.float32)
        if g.std() < _MIN_CONTRASTE:
            return 'plano'

        # Medio fotograma del verde imposible: h264 rellenando lo que no pudo decodificar.
        f = rgb.astype(np.float32)
        verde = f[:, :, 1]
        otros = np.maximum(f[:, :, 0], f[:, :, 2])
        if float(((verde > 60) & (verde - otros > 0.55 * verde)).mean()) > _MAX_VERDE_RELLENO:
            return 'sucio'
        # Filos de macrobloque: el salto medio en las filas/columnas múltiplo de k contra el resto.
        # k varía con el reescalado (1080p→960 deja los de 16 en 8), así que se prueban tres.
        peor = 0.0
        for eje in (1, 0):
            d = np.abs(np.diff(g, axis=eje))
            idx = np.arange(d.shape[eje])
            for k in (8, 12, 16):
                rejilla = (idx + 1) % k == 0
                a = d[:, rejilla].mean() if eje else d[rejilla, :].mean()
                b = d[:, ~rejilla].mean() if eje else d[~rejilla, :].mean()
                # Sin textura fuera de la rejilla el cociente se dispara por el divisor, no por los
                # filos: un cartel de color plano daría «embadurnado». No se juzga.
                if b < 1.0:
                    continue
                peor = max(peor, a / b)
        return 'sucio' if peor >= _MAX_BLOQUEO else 'ok'
    except Exception:
        return 'ok'   # si no se puede mirar, se da por bueno: peor es quedarse sin fotograma


def _es_plano(dest) -> bool:
    return _juzgar_fotograma(dest) == 'plano'


def _extraer_fotograma_util(video: str, dest, duration: float) -> None:
    """El fotograma del episodio, mirando que sirva para algo.

    Sacarlo siempre del minuto central es una lotería: ahí está a menudo el corte del eyecatch, un
    fundido o el cartel del título, y la tarjeta se quedaba en un rectángulo negro **para siempre**
    (este caché no caduca). Y si el vídeo estaba a medio descargar, en un churro de macrobloques.
    Medido sobre los 1099 del caché real: 27 negros o blancos enteros y 34 embadurnados.

    Si lo que sale no sirve se prueba otro momento; si no sirve ninguno, se levanta y no se cachea
    nada — la tarjeta cae al still de TMDB, que es mejor que una mancha permanente.

    El coste normal sigue siendo UNA llamada a ffmpeg: sólo los malos pagan las demás.
    """
    if duration <= 0:
        _extraer_fotograma(video, dest, 30.0)
        return
    for frac in _MOMENTOS_THUMB:
        _extraer_fotograma(video, dest, duration * frac)
        if _juzgar_fotograma(dest) == 'ok':
            return
    _Path(dest).unlink(missing_ok=True)
    raise RuntimeError(f'ningún momento de {video} da un fotograma utilizable')


def _cache_tmdb_thumb(meta, episode, episode_key, dest) -> bool:
    """Guarda el still de respaldo en el mismo caché persistente que los fotogramas locales."""
    from PIL import Image

    entry = meta.get(episode_key) or meta.get(str(episode)) or {}
    src = _warm_img(entry.get('still'))
    if not src:
        return False
    tmp = dest.with_suffix(f'.{os.getpid()}-{threading.get_ident()}.part')
    try:
        with Image.open(src) as im:
            im = im.convert('RGB')
            im.thumbnail((_THUMB_W, _THUMB_W), Image.Resampling.LANCZOS)
            im.save(tmp, 'JPEG', quality=95, subsampling=0)
        tmp.replace(dest)
    finally:
        tmp.unlink(missing_ok=True)
    return True


@anime_bp.route('/thumb/<anime_id>/<int:episode>')
def anime_thumb(anime_id, episode):
    _THUMBS_DIR.mkdir(parents=True, exist_ok=True)
    is_special = request.args.get('special') == '1'
    episode_key = request.args.get('episode_key', '').strip()
    season = request.args.get('season', '').strip()
    if not episode_key and season.isdigit():
        episode_key = f's{int(season):02d}e{episode:03d}'
    cache_path = _THUMBS_DIR / _thumb_key(anime_id, episode, is_special, episode_key)

    if cache_path.exists() and cache_path.stat().st_size > 0:
        return send_file(str(cache_path), mimetype='image/jpeg', max_age=604800)

    lib = _lib_read()
    anime = lib.get(anime_id)
    if not anime:
        return ('', 404)

    ep_data = (anime.get('episodes') or {}).get(episode_key)
    if not ep_data:
        ep_data = (anime.get('episodes') or {}).get(str(episode)) or {}

    if is_special and _carpetas_de(anime):
        scanned = _escanear_carpetas(anime)
        matched = next((e for e in scanned
                        if e.get('ep_type') == 'special' and e['num'] == episode), None)
        video = matched['path'] if matched else ''
    elif ep_data.get('info_hash') or ep_data.get('relative_path'):
        video, err = resolve_episode_video({
            'anime_id': anime_id, 'episode': episode, 'episode_key': episode_key,
            'info_hash': ep_data.get('info_hash', ''),
            'file_index': ep_data.get('file_index'),
            'relative_path': ep_data.get('relative_path', ''),
        })
        if err:
            video = _local_relative_file(anime, ep_data.get('relative_path', ''))
            if not video and not ep_data.get('from_batch_selection'):
                video = _buscar_video(anime, episode)
    elif _carpetas_de(anime):
        video = _buscar_video(anime, episode)
    else:
        video = ''

    if not video:
        return ('', 404)

    duration = _ffprobe_duration(video)
    try:
        _extraer_fotograma_util(video, cache_path, duration)
    except Exception as e:
        cache_path.unlink(missing_ok=True)
        record_error('anime', e, op='thumb_extract', anime_id=anime_id, episode=episode)
        try:
            # Un especial no puede heredar el still del episodio regular con el mismo número.
            meta = {} if is_special else _episode_metadata(anime_id).get('meta') or {}
            if is_special or not _cache_tmdb_thumb(meta, episode, episode_key, cache_path):
                return ('', 500)
        except Exception as fallback_error:
            record_error('anime', fallback_error, op='thumb_fallback', anime_id=anime_id, episode=episode)
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
        if not _carpetas_de(anime):
            continue
        try:
            episodes = _escanear_carpetas(anime)
        except Exception:
            continue
        fallback_meta = None
        for ep in episodes:
            ep_type = ep.get('ep_type', 'episode')
            ep_num  = ep['num']
            episode_key = (f"s{int(ep['season']):02d}e{ep_num:03d}"
                           if ep.get('season') else str(ep_num))
            cache_path = _THUMBS_DIR / _thumb_key(anime_id, ep_num, ep_type == 'special', episode_key)
            if cache_path.exists() and cache_path.stat().st_size > 0:
                continue
            video = ep['path']
            try:
                _extraer_fotograma_util(video, cache_path, _video_duration(video))
            except Exception:
                cache_path.unlink(missing_ok=True)
                try:
                    if ep_type != 'special':
                        if fallback_meta is None:
                            fallback_meta = _episode_metadata(anime_id).get('meta') or {}
                        _cache_tmdb_thumb(fallback_meta, ep_num, episode_key, cache_path)
                except Exception as fallback_error:
                    record_error('anime', fallback_error, op='thumb_pregen_fallback', anime_id=anime_id, episode=ep_num)
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
    except Exception as e:
        record_error('anime', e, op='aniskip', mal_id=mal_id, episode=episode)
        return jsonify({'error': str(e) or 'AniSkip no responde'}), 502


# ── Episode info (Jikan) ───────────────────────────────────────────────────────

_ep_info_cache: dict = {}   # (mal_id, episode) → {title, synopsis, aired, filler, recap}


@anime_bp.route('/episode_info/<int:mal_id>/<int:episode>')
def anime_episode_info(mal_id, episode):
    key = (mal_id, episode)
    if key in _ep_info_cache:
        return jsonify(_ep_info_cache[key])
    try:
        r = _rhttp.get(
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


@anime_bp.route('/episode_titles/<int:mal_id>')
def anime_episode_titles(mal_id):
    """Todos los títulos de episodio de una serie en UNA llamada (Jikan en bloque, 100/pág).
    Devuelve { "titles": { "<num>": {title, filler, recap} } }. Cacheado en disco 7 días —
    los títulos de MAL no cambian. Para pintar el título real bajo cada portada del detalle."""
    from api.runtime import cache_get, cache_set
    ck = str(mal_id)
    disk = cache_get('ep_titles', ck, 7 * 24 * 3600)
    if disk is not None:
        return jsonify({'titles': disk})
    titles = {}
    try:
        page = 1
        while page <= 5:   # 5×100 = 500 episodios, tope de seguridad
            r = _rhttp.get(f'{_JIKAN}/anime/{mal_id}/episodes',
                           params={'page': page}, timeout=10,
                           headers={'User-Agent': 'Mozilla/5.0'})
            r.raise_for_status()
            j = r.json()
            for d in (j.get('data') or []):
                num = d.get('mal_id')
                t = (d.get('title') or '').strip()
                if num and t:
                    titles[str(num)] = {'title': t, 'filler': bool(d.get('filler')), 'recap': bool(d.get('recap'))}
            if not (j.get('pagination') or {}).get('has_next_page'):
                break
            page += 1
    except Exception:
        pass
    if titles:
        cache_set('ep_titles', ck, titles, ttl=7 * 24 * 3600)
    return jsonify({'titles': titles})


import re as _re
_PLACEHOLDER_TITLE = _re.compile(r'^(?:episodi?o|episode|épisode|folge|capítulo)\s*\d+$', _re.IGNORECASE)

def _is_placeholder_title(t):
    """TMDB rellena los nombres sin traducir con 'Episodio 7'/'Episode 7' (no vacío) →
    hay que tratarlos como ausentes para caer al otro idioma / al título real."""
    return not t or bool(_PLACEHOLDER_TITLE.match(t.strip()))


def _tmdb_season_episodes(tmdb_id, season, lang):
    """Un GET a /tv/{id}/season/{season} → {num: {title, overview, still, aired}} en `lang`.
    TMDB deja vacío name/overview cuando no hay traducción para ese idioma."""
    out = {}
    r = _http.get(f'https://api.themoviedb.org/3/tv/{tmdb_id}/season/{season}',
                  params={'api_key': _tmdb_key(), 'language': lang}, timeout=10).json()
    for e in (r.get('episodes') or []):
        num = e.get('episode_number')
        if num is None:
            continue
        out[str(num)] = _tmdb_episode_entry(e)
    return out


def _tmdb_episode_entry(e):
    still = e.get('still_path')
    return {
        'title': (e.get('name') or '').strip(),
        'overview': (e.get('overview') or '').strip(),
        'still': f'https://image.tmdb.org/t/p/original{still}' if still else '',
        'aired': (e.get('air_date') or '').strip(),
    }


def _tmdb_season_group(tmdb_id, season):
    """Devuelve (grupo, subgrupo, detalle EN) si TMDB identifica Tn sin ambigüedad."""
    try:
        groups = _http.get(
            f'https://api.themoviedb.org/3/tv/{tmdb_id}/episode_groups',
            params={'api_key': _tmdb_key(), 'language': 'en-US'}, timeout=10).json().get('results') or []
        for group_type in (6, 7):  # Production primero; TV como alternativa.
            matches = []
            for group in groups:
                if str(group.get('type')) != str(group_type) or not group.get('id'):
                    continue
                detail = _http.get(
                    f"https://api.themoviedb.org/3/tv/episode_group/{group['id']}",
                    params={'api_key': _tmdb_key(), 'language': 'en-US'}, timeout=10).json()
                seasons = [g for g in (detail.get('groups') or [])
                           if _extract_season_number(g.get('name'), '') == season and g.get('id')]
                if len(seasons) > 1:
                    return None
                if seasons:
                    matches.append((group['id'], seasons[0]['id'], detail))
            if len(matches) == 1:
                return matches[0]
            if matches:  # Varios grupos del mismo tipo describen Tn: no elegir a ciegas.
                return None
    except Exception:
        pass
    return None


def _tmdb_group_episodes(tmdb_id, group_ref, lang, english_detail=None):
    """Mapea 1..N del subgrupo; no usa el número global del episodio emitido."""
    group_id, season_group_id, _ = group_ref
    detail = english_detail
    if detail is None:
        detail = _http.get(
            f'https://api.themoviedb.org/3/tv/episode_group/{group_id}',
            params={'api_key': _tmdb_key(), 'language': lang}, timeout=10).json()
    season_group = next((g for g in (detail.get('groups') or [])
                         if str(g.get('id')) == str(season_group_id)), None)
    episodes = (season_group or {}).get('episodes') or []
    def _order(pair):
        try:
            return int(pair[1]['order']), pair[0]
        except (KeyError, TypeError, ValueError):
            return pair[0], pair[0]
    ordered = sorted(enumerate(episodes), key=_order)
    return {str(index): _tmdb_episode_entry(e) for index, (_, e) in enumerate(ordered, 1)}


_EP_META_TTL = 180 * 24 * 3600
_EP_META_VERSION = 5


def _episode_meta_season(tmdb_id, year, title_en, title_romaji):
    year_season = _tmdb_season_by_year(tmdb_id, year)[0] if year else None
    title_season = _extract_season_number(title_en, title_romaji)
    if title_season and year_season and title_season != year_season:
        return title_season, True
    if year_season:
        return year_season, False
    return (title_season, True) if title_season else (None, False)


def _episode_meta_cache_key(tmdb_id, season, grouped=False):
    source = '_g' if grouped else ''
    return f'{tmdb_id}_s{season}{source}_v{_EP_META_VERSION}'


@anime_bp.route('/episode_meta/<anime_id>')
def anime_episode_meta(anime_id):
    return jsonify(_episode_metadata(anime_id))


def _episode_metadata(anime_id):
    """Título + descripción (+ still) de cada episodio desde TMDB, para el detalle del anime.
    Usa el tmdb_id/tmdb_type/season_year ya resueltos por el arte del hero. Si el año no identifica
    la temporada, recurre al grupo TMDB de producción/TV cuando el título indica Tn; mapea su orden
    local para no confundirlo con la numeración global. Nunca fuerza T1. Español primero y caché larga.
    Devuelve { source:'tmdb', season, meta:{ '<num>': {title, overview, still, aired} } } o
    { source:null, meta:{} } cuando no hay match TMDB (el front cae a los títulos de MAL)."""
    from api.runtime import cache_get, cache_set
    lib = _lib_read()
    v = lib.get(anime_id) or {}
    tmdb_id = v.get('tmdb_id')
    ttype = v.get('tmdb_type', 'tv')
    year = v.get('season_year')
    if not tmdb_id or not _tmdb_key() or ttype != 'tv':
        return {'source': None, 'meta': {}}

    title_en = v.get('title_english') or v.get('title') or ''
    title_romaji = v.get('title_romaji') or ''
    season, grouped = _episode_meta_season(tmdb_id, year, title_en, title_romaji)
    if season is None:
        return {'source': None, 'meta': {}}
    ck = _episode_meta_cache_key(tmdb_id, season, grouped)
    # Los títulos de episodio son prácticamente inmutables → caché larga, sin desalojar.
    disk = cache_get('ep_meta', ck, _EP_META_TTL)
    if disk is not None:
        return {'source': 'tmdb', 'season': season, 'meta': disk}

    group_ref = _tmdb_season_group(tmdb_id, season) if grouped else None
    if grouped and not group_ref:
        return {'source': None, 'meta': {}}

    meta = {}
    try:
        if group_ref:
            es = _tmdb_group_episodes(tmdb_id, group_ref, 'es-ES')
            en = _tmdb_group_episodes(tmdb_id, group_ref, 'en-US', group_ref[2])
        else:
            es = _tmdb_season_episodes(tmdb_id, season, 'es-ES')
            en = {}
        # Rellenar con inglés cuando el español falte O sea un placeholder ('Episodio 7').
        need_en = any(_is_placeholder_title(e['title']) or not e['overview'] for e in es.values()) or not es
        if need_en and not group_ref:
            en = _tmdb_season_episodes(tmdb_id, season, 'en-US')

        nums = set(es) | set(en)
        # Consistencia de idioma en los TÍTULOS: si a algún episodio le falta el título real en
        # español, TODOS van en inglés (evita mezclar ES/EN, que se ve raro en la cuadrícula).
        all_spanish = bool(nums) and all(
            not _is_placeholder_title(es.get(n, {}).get('title', '')) for n in nums)

        def _pick_title(a, b):
            if not _is_placeholder_title(a): return a
            if not _is_placeholder_title(b): return b
            return a or b

        for num in nums:
            e_es, e_en = es.get(num, {}), en.get(num, {})
            if all_spanish:
                title = e_es.get('title', '')
            else:
                title = _pick_title(e_en.get('title', ''), e_es.get('title', ''))  # inglés primero
            meta[num] = {
                'title':    title,
                'overview': e_es.get('overview') or e_en.get('overview', ''),
                'still':    e_es.get('still') or e_en.get('still', ''),
                'aired':    e_es.get('aired') or e_en.get('aired', ''),
            }
    except Exception:
        pass
    if meta:
        cache_set('ep_meta', ck, meta, ttl=_EP_META_TTL, max_entries=5000)
    return {'source': 'tmdb' if meta else None, 'season': season, 'meta': meta}


# ── Subtitle list endpoint ─────────────────────────────────────────────────────

@anime_bp.route('/subtitles/<anime_id>/<int:episode>')
def anime_subtitles(anime_id, episode):
    """Return subtitle files found alongside the episode video."""
    lib = _lib_read()
    anime = lib.get(anime_id)
    if not anime:
        return jsonify([])

    episode_key = request.args.get('episode_key', '')
    season = request.args.get('season', '').strip()
    if not episode_key and season.isdigit():
        episode_key = f's{int(season):02d}e{episode:03d}'
    video, err = video_de_biblioteca(anime_id, episode, episode_key)
    if err or not video:
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
    # `write_text` TRUNCA antes de escribir: si el proceso muere ahí, el fichero se pierde
    # entero. Y hay que borrar también el archivo por meses, o «limpiar» dejaba doce ficheros
    # de historial vivos que reaparecían en la retrospectiva.
    from api import history_store
    history_store.clear('watch')
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
          coverImage{ extraLarge large medium }
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


def recs_for_al(al_id: int) -> list:
    """Recomendaciones de AniList para UNA serie, cacheadas. Lanza si la red falla.

    Extraído de la ruta para que `for_you.py` pueda agregarlas sobre toda la biblioteca sin
    pasar por HTTP contra nosotros mismos.
    """
    cached = _al_cache_get('recs', al_id)
    if cached is not None:
        return cached
    resp = _http.post(_ANILIST, json={'query': _REC_QUERY, 'variables': {'id': al_id}}, timeout=10)
    nodes = (resp.json().get('data', {}).get('Media', {})
             .get('recommendations', {}).get('nodes', []))
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
            'cover':        _al_cover(m),
            'score':        m.get('averageScore') or 0,
            'genres':       (m.get('genres') or [])[:3],
            'format':       m.get('format', ''),
            'episodes':     m.get('episodes') or 0,
            'status':       m.get('status', ''),
            'rating':       node['rating'],
        })
    threading.Thread(target=_al_cache_set, args=('recs', al_id, recs), daemon=True).start()
    return recs


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
                'cover':        _al_cover(m),
                'score':        m.get('averageScore') or 0,
                'genres':       (m.get('genres') or [])[:3],
                'format':       m.get('format', ''),
                'episodes':     m.get('episodes') or 0,
                'status':       m.get('status', ''),
                'rating':       node['rating'],
            })
        threading.Thread(target=_al_cache_set, args=('recs', al_id, recs), daemon=True).start()
        for rec in recs:
            if rec['cover']:
                threading.Thread(target=_warm_img, args=(rec['cover'],), daemon=True).start()
        return jsonify(recs)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ── Orden de franquicia (temporadas/OVAs/películas en orden de estreno) ─────────
# Relaciones "fuertes" que forman una franquicia (se ignoran ADAPTATION/CHARACTER/
# OTHER/SOURCE… que llevan a manga o crossovers sueltos).
_FRANCHISE_REL = {'PREQUEL', 'SEQUEL', 'PARENT', 'SIDE_STORY', 'ALTERNATIVE', 'SPIN_OFF'}
_FRANCHISE_Q = '''query($id:Int){Media(id:$id,type:ANIME){
  id format status episodes seasonYear
  startDate{year month day}
  title{romaji english}
  coverImage{ extraLarge large medium }
  relations{edges{relationType node{id type}}}
}}'''


@anime_bp.route('/franchise/<int:al_id>')
def anime_franchise(al_id):
    """Todas las entregas de la franquicia (recorriendo relaciones de AniList)
    en ORDEN DE ESTRENO, marcando las que tienes en la biblioteca. Cache 24h."""
    cached = _al_cache_get('franchise', al_id)
    if cached is not None:
        return jsonify(cached)

    nodes, visited, queue = {}, set(), [al_id]
    queries, MAX_NODES, MAX_QUERIES = 0, 40, 25
    while queue and queries < MAX_QUERIES and len(nodes) < MAX_NODES:
        cur = queue.pop(0)
        if cur in visited:
            continue
        visited.add(cur)
        try:
            r = _anilist_post(_FRANCHISE_Q, {'id': int(cur)})
            queries += 1
            m = (r.json().get('data') or {}).get('Media') if r is not None else None
        except Exception:
            m = None
        if not m:
            continue
        nodes[m['id']] = m
        for e in ((m.get('relations') or {}).get('edges') or []):
            n = e.get('node') or {}
            if (e.get('relationType') in _FRANCHISE_REL
                    and n.get('type') == 'ANIME' and n.get('id') not in visited):
                queue.append(n['id'])

    owned = {int(v['al_id']) for v in _lib_read().values() if v.get('al_id')}

    def _sortkey(m):
        d = m.get('startDate') or {}
        return (d.get('year') or m.get('seasonYear') or 9999,
                d.get('month') or 13, d.get('day') or 32)

    items = []
    for m in sorted(nodes.values(), key=_sortkey):
        d = m.get('startDate') or {}
        items.append({
            'al_id': m['id'],
            'title': (m['title'].get('english') or m['title'].get('romaji') or ''),
            'title_romaji': m['title'].get('romaji', ''),
            'cover': _al_cover(m),
            'format': m.get('format', ''),
            'episodes': m.get('episodes') or 0,
            'status': m.get('status', ''),
            'year': d.get('year') or m.get('seasonYear'),
            'in_library': m['id'] in owned,
            'is_current': m['id'] == al_id,
        })
    result = {'items': items, 'truncated': bool(queue)}
    if al_id in nodes:   # solo cachear si al menos se resolvió la raíz
        threading.Thread(target=_al_cache_set, args=('franchise', al_id, result), daemon=True).start()
    return jsonify(result)


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

# `minimumTagRank` es un filtro REAL de AniList y es lo que convierte esta pantalla en algo útil:
# un tag como "Magic" lo llevan cientos de series con un 60 % de relevancia, que es como no
# filtrar. Con 90 % pides las que van DE eso — medido: a rank 0 salen Re:ZERO (88 %) y Jujutsu
# (85 %); a rank 90 desaparecen y entra Witch Hat Atelier (98 %).
_TAG_BROWSE_QUERY = """
query ($tag: String, $rank: Int) {
  Page(page: 1, perPage: 30) {
    media(type: ANIME, tag: $tag, minimumTagRank: $rank, sort: SCORE_DESC, isAdult: false) {
      id idMal
      title { romaji english }
      coverImage{ extraLarge large medium }
      averageScore genres format episodes status
      tags { name rank }
    }
  }
}
"""


@anime_bp.route('/enrich/<int:al_id>')
def anime_enrich_preview(al_id):
    """AniList extraLarge cover + TMDB backdrop/logo for a non-library anime
    (hero recommendation). Returns the same quality fields that the backfill
    produces for library entries so the detail panel looks identical."""
    data = _anilist_enrich(al_id)
    if not data:
        return jsonify({})
    t          = data.get('title') or {}
    title_en   = t.get('english') or ''
    title_rom  = t.get('romaji') or ''
    year       = data.get('seasonYear')
    fmt        = data.get('format') or ''
    cover_xl   = (data.get('coverImage') or {}).get('extraLarge') or ''
    al_banner  = data.get('bannerImage') or ''
    synopsis   = _clean_synopsis(data.get('description') or '')
    total_eps  = data.get('episodes')
    banner = al_banner
    logo   = ''
    if title_en or title_rom:
        art = _tmdb_art(title_en, title_rom, year, fmt=fmt)
        if art:
            banner   = art.get('backdrop') or al_banner
            logo     = art.get('logo') or ''
            cover_xl = art.get('poster') or cover_xl
    return jsonify({
        'cover_xl':       cover_xl,
        'banner':         banner,
        'logo':           logo,
        'synopsis':       synopsis,
        'total_episodes': total_eps,
    })


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
    # 0 = sin filtro. Se acota a [0, 100] porque AniList devuelve un error de esquema fuera de
    # rango y eso llegaría a la UI como "no hay resultados", que es la mentira de siempre.
    min_rank = max(0, min(100, request.args.get('min_rank', type=int) or 0))
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
            json={'query': _TAG_BROWSE_QUERY, 'variables': {'tag': tag, 'rank': min_rank}},
            timeout=10,
        )
        items = (resp.json().get('data', {}).get('Page', {}).get('media') or [])
        result = []
        for m in items:
            if al_id and m['id'] == al_id:
                continue  # skip the source anime itself
            item_tags = {t['name'] for t in (m.get('tags') or [])}
            shared = len(source_tags & item_tags) if source_tags else 0
            # El % del tag PEDIDO viaja con cada resultado: sin él, filtrar por 90 % es un salto
            # de fe — se ve la lista pero no por qué está cada obra en ella.
            tag_rank = next((t['rank'] for t in (m.get('tags') or []) if t['name'] == tag), None)
            result.append({
                'al_id':        m['id'],
                'mal_id':       m.get('idMal'),
                'title':        m['title'].get('english') or m['title'].get('romaji', ''),
                'title_romaji': m['title'].get('romaji', ''),
                'cover':        _al_cover(m),
                'score':        m.get('averageScore') or 0,
                'genres':       (m.get('genres') or [])[:3],
                'format':       m.get('format', ''),
                'episodes':     m.get('episodes') or 0,
                'status':       m.get('status', ''),
                'shared':       shared,
                'tag_rank':     tag_rank,
            })
        # Filtrando por relevancia manda el % del tag: pediste «90 % Magic», así que lo primero
        # que quieres ver es lo más Magic. Sin filtro sigue mandando el parecido con la obra de
        # origen, que es de donde vienes.
        if min_rank:
            result.sort(key=lambda x: (-(x['tag_rank'] or 0), -x['score']))
        else:
            result.sort(key=lambda x: (-x['shared'], -x['score']))
        for item in result:
            if item['cover']:
                threading.Thread(target=_warm_img, args=(item['cover'],), daemon=True).start()
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ── MAL Interest Stacks ────────────────────────────────────────────────────────

_stacks_cache: dict = {}  # mal_id → list of stacks

_STACKS_CACHE_PATH = _Path.home() / '.animanga_stacks_cache.json'


def _load_stacks_cache():
    global _stacks_cache
    if not _STACKS_CACHE_PATH.exists():
        return                                   # aún sin caché ≠ fallo
    try:
        with open(_STACKS_CACHE_PATH) as f:
            _stacks_cache = {int(k): v for k, v in json.load(f).items()}
    except Exception as e:
        record_error('anime', e, op='stacks_cache_load', path=str(_STACKS_CACHE_PATH))


def _save_stacks_cache():
    with swallow('anime', 'stacks_cache_save', path=str(_STACKS_CACHE_PATH)):
        write_json_atomic(_STACKS_CACHE_PATH, {str(k): v for k, v in _stacks_cache.items()}, durable=False)


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
    with swallow('anime', 'al_cache_set', path=str(_AL_CACHE_PATH), section=section):
        try:
            with open(_AL_CACHE_PATH) as f:
                data = json.load(f)
        except Exception:
            data = {}         # caché corrupta al escribir: se arranca de cero (auto-sana), esperado
        data.setdefault(section, {})[str(key)] = {'data': value, 'ts': int(time.time())}
        write_json_atomic(_AL_CACHE_PATH, data, durable=False)


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
    _q = 'query($mid:Int){Media(idMal:$mid,type:ANIME){id idMal title{english romaji} coverImage{ extraLarge large medium } episodes format}}'
    try:
        resp = _rhttp.post(_ANILIST, json={'query': _q, 'variables': {'mid': mal_id}}, timeout=8)
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
