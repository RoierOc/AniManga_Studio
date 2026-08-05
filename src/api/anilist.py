#!/usr/bin/env python3
"""
AniList API — batch score lookup, top manga, genre list.
No API key required. Rate limit: ~90 req/min.
"""

from flask import Blueprint, jsonify, request
import re
import time
from html import unescape as _unescape

from api.resilient_http import http as _http  # retry + Retry-After + rate limiting

anilist_bp = Blueprint('anilist', __name__)

_URL = 'https://graphql.anilist.co'
_genres_cache: list = []
_genres_ts: float = 0.0
_top_cache: dict = {}
_TOP_TTL = 300   # 5 min
_GENRES_TTL = 3600


# Los tres tamaños de AniList son ficheros distintos, no recortes: `medium` 100x150, `large`
# 230x325 y `extraLarge` 460x650. Pedíamos `large` en todo Descubrir/Temporada, y MEDIDO en una
# rejilla a 1600px: 230 px de fuente pintados a 298 px CSS — ampliados ya en dpr 1, y el doble de
# mal en HiDPI. Es el mismo fallo que guardar el `.256.jpg` de MangaDex como portada definitiva.
def _cover(node: dict) -> str:
    """La portada MÁS GRANDE que sirva AniList para este nodo.

    El orden de campos ya era el correcto, pero no bastaba: cuando a una serie le falta
    `extraLarge` se cae a `large`, que son 230x325 — la mitad de lo que necesita una tarjeta.
    `hd_url` sube además la RUTA (`/cover/medium/` → `/cover/large/`), que es donde AniList
    codifica de verdad el tamaño. Ver [[feedback_max_resolution_images]].
    """
    from api.imgproxy import hd_url
    ci = (node or {}).get('coverImage') or {}
    return hd_url(ci.get('extraLarge') or ci.get('large') or ci.get('medium') or '')


def _ql(query: str, variables: dict | None = None):
    try:
        r = _http.post(_URL, json={'query': query, 'variables': variables or {}}, timeout=10)
        r.raise_for_status()
        return r.json().get('data') or {}
    except Exception as e:
        return {'_error': str(e)}


@anilist_bp.route('/scores', methods=['POST'])
def batch_scores():
    """Return AniList mean scores for a list of MAL IDs (keyed by mal_id string)."""
    data = request.get_json(silent=True) or {}
    mal_ids = [int(x) for x in (data.get('mal_ids') or []) if str(x).lstrip('-').isdigit()]
    if not mal_ids:
        return jsonify({})

    q = '''
    query ($ids: [Int]) {
      Page(perPage: 50) {
        media(idMal_in: $ids, type: MANGA) {
          idMal id meanScore genres popularity
        }
      }
    }
    '''
    d = _ql(q, {'ids': mal_ids})
    if '_error' in d:
        return jsonify({}), 200  # graceful — don't break the UI

    result = {}
    for item in (d.get('Page') or {}).get('media') or []:
        mid = item.get('idMal')
        if mid:
            result[str(mid)] = {
                'score':      item.get('meanScore'),
                'genres':     (item.get('genres') or [])[:5],
                'popularity': item.get('popularity'),
                'al_id':      item.get('id'),
            }
    return jsonify(result)


@anilist_bp.route('/top')
def top_manga():
    """Top manga. Params: genre=X OR tag=X, sort, page."""
    genre = request.args.get('genre', '').strip()
    tag   = request.args.get('tag', '').strip()
    sort  = request.args.get('sort', 'SCORE_DESC')
    page  = max(1, int(request.args.get('page', 1) or 1))

    if sort not in {'SCORE_DESC', 'TRENDING_DESC', 'POPULARITY_DESC'}:
        sort = 'SCORE_DESC'

    cache_key = f'g:{genre}|t:{tag}|{sort}|{page}'
    if cache_key in _top_cache:
        res, ts = _top_cache[cache_key]
        if time.time() - ts < _TOP_TTL:
            return jsonify(res)

    q = '''
    query ($genre: String, $tag: String, $sort: [MediaSort], $page: Int) {
      Page(page: $page, perPage: 30) {
        pageInfo { hasNextPage }
        media(type: MANGA, sort: $sort, genre: $genre, tag: $tag, isAdult: false, countryOfOrigin: "JP") {
          id idMal
          title { romaji english native }
          meanScore popularity genres
          coverImage { extraLarge large medium }
          status chapters
        }
      }
    }
    '''
    variables: dict = {'sort': [sort], 'page': page}
    if genre:
        variables['genre'] = genre
    if tag:
        variables['tag'] = tag

    d = _ql(q, variables)
    if '_error' in d:
        return jsonify({'results': [], 'hasNextPage': False, 'error': d['_error']}), 200

    page_data = d.get('Page') or {}
    results = []
    for item in page_data.get('media') or []:
        t = item.get('title') or {}
        results.append({
            'al_id':        item['id'],
            'mal_id':       item.get('idMal'),
            'title':        t.get('english') or t.get('romaji') or t.get('native') or '',
            'title_romaji': t.get('romaji') or '',
            'score':        item.get('meanScore'),
            'popularity':   item.get('popularity'),
            'genres':       (item.get('genres') or [])[:4],
            'cover':        _cover(item),
            'status':       item.get('status'),
            'chapters':     item.get('chapters'),
        })

    res = {'results': results, 'hasNextPage': (page_data.get('pageInfo') or {}).get('hasNextPage', False)}
    _top_cache[cache_key] = (res, time.time())
    return jsonify(res)


_ANIME_SORTS = {
    'SCORE_DESC', 'POPULARITY_DESC', 'TRENDING_DESC', 'FAVOURITES_DESC', 'START_DATE_DESC',
}
_ANIME_FORMATS = {'TV', 'TV_SHORT', 'MOVIE', 'SPECIAL', 'OVA', 'ONA', 'MUSIC'}
_ANIME_STATUS = {'FINISHED', 'RELEASING', 'NOT_YET_RELEASED', 'CANCELLED', 'HIATUS'}
_ANIME_SEASONS = {'WINTER', 'SPRING', 'SUMMER', 'FALL'}


@anilist_bp.route('/anime_top')
def top_anime():
    """Browse anime by AniList filters (mirror of /top for ANIME).
    Params: genre, tag, year(=seasonYear), season, format, status, sort, page."""
    genre  = request.args.get('genre', '').strip()
    tag    = request.args.get('tag', '').strip()
    season = request.args.get('season', '').strip().upper()
    fmt    = request.args.get('format', '').strip().upper()
    status = request.args.get('status', '').strip().upper()
    sort   = request.args.get('sort', 'SCORE_DESC').strip().upper()
    page   = max(1, int(request.args.get('page', 1) or 1))
    try:
        year = int(request.args.get('year', 0) or 0)
    except ValueError:
        year = 0

    if sort not in _ANIME_SORTS:
        sort = 'SCORE_DESC'
    if season not in _ANIME_SEASONS:
        season = ''
    if fmt not in _ANIME_FORMATS:
        fmt = ''
    if status not in _ANIME_STATUS:
        status = ''

    cache_key = f'A|g:{genre}|t:{tag}|y:{year}|s:{season}|f:{fmt}|st:{status}|{sort}|{page}'
    if cache_key in _top_cache:
        res, ts = _top_cache[cache_key]
        if time.time() - ts < _TOP_TTL:
            return jsonify(res)

    q = '''
    query ($genre: String, $tag: String, $year: Int, $season: MediaSeason,
           $format: MediaFormat, $status: MediaStatus, $sort: [MediaSort], $page: Int) {
      Page(page: $page, perPage: 30) {
        pageInfo { hasNextPage }
        media(type: ANIME, sort: $sort, genre: $genre, tag: $tag, seasonYear: $year,
              season: $season, format: $format, status: $status, isAdult: false) {
          id idMal
          title { romaji english native }
          meanScore popularity genres episodes format status seasonYear season
          coverImage { extraLarge large medium }
          bannerImage
        }
      }
    }
    '''
    variables: dict = {'sort': [sort], 'page': page}
    if genre:  variables['genre'] = genre
    if tag:    variables['tag'] = tag
    if year:   variables['year'] = year
    if season: variables['season'] = season
    if fmt:    variables['format'] = fmt
    if status: variables['status'] = status

    d = _ql(q, variables)
    if '_error' in d:
        return jsonify({'results': [], 'hasNextPage': False, 'error': d['_error']}), 200

    page_data = d.get('Page') or {}
    results = []
    for item in page_data.get('media') or []:
        t = item.get('title') or {}
        results.append({
            'al_id':        item['id'],
            'mal_id':       item.get('idMal'),
            'title':        t.get('english') or t.get('romaji') or t.get('native') or '',
            'title_romaji': t.get('romaji') or '',
            'score':        item.get('meanScore'),
            'popularity':   item.get('popularity'),
            'genres':       (item.get('genres') or [])[:4],
            'cover':        _cover(item),
            'banner':       item.get('bannerImage'),
            'status':       item.get('status'),
            'episodes':     item.get('episodes'),
            'format':       item.get('format'),
            'seasonYear':   item.get('seasonYear'),
            'season':       item.get('season'),
        })

    res = {'results': results, 'hasNextPage': (page_data.get('pageInfo') or {}).get('hasNextPage', False)}
    _top_cache[cache_key] = (res, time.time())
    return jsonify(res)


def title_variants(title: str | None = None, al_id: int | None = None,
                   media_type: str = 'MANGA') -> list[str]:
    """Return name variants for a series so multi-source search doesn't miss a
    release indexed under a different language/alias (e.g. "Amayo no Tsuki" ↔
    "The Moon on a Rainy Night"). Source: AniList title{romaji,english,native} +
    synonyms — the richest alias list available without auth. Falls back to just
    `title` if AniList is unreachable. Dedups case/space-insensitively while
    preserving the original casing of the first occurrence.

    `media_type` is 'MANGA' (default) or 'ANIME' — the same alias machinery powers
    manga source discovery, MangaDex auto-resolution and anime torrent search, so
    every online lookup in the app can share one variant list."""
    mt = 'ANIME' if str(media_type).upper().startswith('ANI') else 'MANGA'
    out: list[str] = []
    seen: set[str] = set()

    def _add(s):
        s = (s or "").strip()
        key = ''.join(ch for ch in s.lower() if ch.isalnum())
        if s and key and key not in seen:
            seen.add(key); out.append(s)

    _add(title)
    if al_id:
        q = 'query ($id: Int) { Media(id: $id, type: %s) { title { romaji english native } synonyms } }' % mt
        d = _ql(q, {'id': int(al_id)})
        media = (d or {}).get('Media')
    else:
        q = 'query ($s: String) { Media(search: $s, type: %s) { title { romaji english native } synonyms } }' % mt
        d = _ql(q, {'s': title})
        media = (d or {}).get('Media')
    if media:
        t = media.get('title') or {}
        for k in ('romaji', 'english', 'native'):
            _add(t.get(k))
        for syn in (media.get('synonyms') or []):
            _add(syn)
    return out


@anilist_bp.route('/search')
def search_manga():
    """Buscador de manga por título (para elegir la serie a traducir). Devuelve
    al_id + títulos + portada. Sin auth."""
    q = request.args.get('q', '').strip()
    if len(q) < 2:
        return jsonify([])
    query = '''
    query ($s: String) {
      Page(perPage: 15) {
        media(search: $s, type: MANGA, sort: SEARCH_MATCH) {
          id idMal
          title { romaji english native }
          coverImage { extraLarge large medium }
          format status startDate { year }
        }
      }
    }
    '''
    d = _ql(query, {'s': q})
    if '_error' in d:
        return jsonify([])
    out = []
    for m in (d.get('Page') or {}).get('media') or []:
        t = m.get('title') or {}
        out.append({
            'al_id': m['id'],
            'mal_id': m.get('idMal'),
            'title': t.get('english') or t.get('romaji') or t.get('native') or '',
            'title_romaji': t.get('romaji') or '',
            'title_native': t.get('native') or '',
            'cover': _cover(m),
            'format': m.get('format'),
            'status': m.get('status'),
            'year': (m.get('startDate') or {}).get('year'),
        })
    return jsonify(out)


# ── Recomendaciones de manga (paridad con las de anime) ────────────────────────
# Dos entradas: (1) por la serie que estás viendo (recommendations de AniList de ese
# manga), (2) "Para ti" agregando las recomendaciones de una muestra de tu biblioteca,
# ponderadas por frecuencia×rating y excluyendo lo que ya tienes. AniList limita ~90/min
# → todo se cachea en disco 24h (cache_get/set) y el agregado muestrea pocos títulos.
import threading as _threading
from api.runtime import cache_get as _cache_get, cache_set as _cache_set, manga_dir as _manga_dir
from pathlib import Path as _Path

_REC_TTL = 24 * 3600

_MANGA_REC_Q = '''
query ($id: Int) {
  Media(id: $id, type: MANGA) {
    recommendations(sort: RATING_DESC, page: 1, perPage: 20) {
      nodes {
        rating
        mediaRecommendation {
          id idMal
          title { romaji english native }
          coverImage { extraLarge large medium }
          averageScore genres format chapters status countryOfOrigin
        }
      }
    }
  }
}
'''


def _norm_key(s: str) -> str:
    import re
    return re.sub(r'[^a-z0-9]', '', (s or '').lower())


def _resolve_manga_al_id(title: str):
    """AniList id de un título de manga (cache 24h; 0 = no encontrado, se cachea igual
    para no re-preguntar)."""
    key = (title or '').strip().lower()
    if not key:
        return None
    hit = _cache_get('manga_al_id', key, _REC_TTL)
    if hit is not None:
        return hit or None
    d = _ql('query($s:String){Media(search:$s,type:MANGA){id}}', {'s': title})
    al = ((d or {}).get('Media') or {}).get('id') or 0
    _cache_set('manga_al_id', key, al, ttl=_REC_TTL, max_entries=800)
    return al or None


def cover_by_al_id(al_id):
    """Portada AniList (large) de un manga por su id. Cache 24h ('' = sin portada, se cachea
    igual para no re-preguntar). Sirve de red de seguridad al añadir una Obra a la biblioteca
    sin portada: las Obras de Descubrir traen `al_id`, así que casi siempre hay portada fiable."""
    if not al_id:
        return None
    key = str(al_id)
    hit = _cache_get('al_cover', key, _REC_TTL)
    if hit is not None:
        return hit or None
    try:
        d = _ql('query($id:Int){Media(id:$id,type:MANGA){coverImage { extraLarge large medium }}}', {'id': int(al_id)})
        url = _cover((d or {}).get('Media') or {})
    except Exception:
        url = ''
    _cache_set('al_cover', key, url, ttl=_REC_TTL, max_entries=800)
    return url or None


def chapters_by_al_id(al_id):
    """(totalChapters|None, status) de un manga por su id AniList. `chapters` es el total
    OFICIAL cuando la obra está terminada; es None mientras sigue en emisión (ahí la referencia
    de 'cuántos capítulos hay' la da el máximo visto entre fuentes). Cache 24h."""
    if not al_id:
        return None, None
    key = str(al_id)
    hit = _cache_get('al_chapters', key, _REC_TTL)
    if hit is not None:
        return (hit.get('chapters'), hit.get('status'))
    ch = st = None
    try:
        d = _ql('query($id:Int){Media(id:$id,type:MANGA){chapters status}}', {'id': int(al_id)})
        m = (d or {}).get('Media') or {}
        ch, st = m.get('chapters'), m.get('status')
    except Exception:
        pass
    _cache_set('al_chapters', key, {'chapters': ch, 'status': st}, ttl=_REC_TTL, max_entries=800)
    return ch, st


_STATUS_ES = {
    'RELEASING': 'En curso', 'FINISHED': 'Terminado', 'NOT_YET_RELEASED': 'Sin publicar',
    'CANCELLED': 'Cancelado', 'HIATUS': 'En pausa',
}
_FORMAT_ES = {'MANGA': 'Manga', 'NOVEL': 'Novela ligera', 'ONE_SHOT': 'One-shot'}
_COUNTRY_ES = {'JP': 'Manga', 'KR': 'Manhwa', 'CN': 'Manhua', 'TW': 'Manhua'}


def _strip_html(txt: str) -> str:
    """AniList devuelve la sinopsis con <br>, <i> y entidades. Sin cv2 ni bs4: es un campo de
    texto, no un documento."""
    t = re.sub(r'<br\s*/?>', '\n', txt or '', flags=re.I)
    t = re.sub(r'<[^>]+>', '', t)
    t = _unescape(t)
    return re.sub(r'\n{3,}', '\n\n', t).strip()


@anilist_bp.route('/manga/<int:al_id>')
def manga_info(al_id):
    """Ficha de la obra para un manga de TU biblioteca.

    `/api/library` sólo devuelve contadores (capítulos, páginas, 4K): la ficha de un manga
    DESCARGADO no tenía sinopsis, ni autor, ni géneros, ni año — mientras que uno que NO tienes
    sí los tenía (`WorkInfoModal` de Descubrir). El `al_id` ya viaja en `/api/library/overview`
    (22 de 28 obras), así que sólo faltaba a quién preguntarle. Cache 24 h.
    """
    # `md` (UUID de MangaDex) es opcional y sólo sirve para una cosa: la sinopsis en ESPAÑOL.
    # AniList sólo la tiene en inglés; MangaDex la trae traducida en la mayoría de obras, y esta
    # app la lee un hispanohablante. Entra en la clave de caché para no mezclar ambas versiones.
    md_id = (request.args.get('md') or '').strip()
    key = f'{al_id}|{md_id}' if md_id else str(al_id)
    hit = _cache_get('al_manga_info', key, _REC_TTL)
    if hit is not None:
        return jsonify(hit)

    q = """
    query ($id: Int) {
      Media(id: $id, type: MANGA) {
        description(asHtml: false)
        genres meanScore averageScore chapters volumes status format countryOfOrigin
        startDate { year } endDate { year }
        staff(perPage: 4, sort: RELEVANCE) { edges { role node { name { full } } } }
        siteUrl
      }
    }
    """
    d = _ql(q, {'id': int(al_id)})
    m = (d or {}).get('Media') or {}
    if '_error' in d or not m:
        # «Falló» y «no hay ficha» no son lo mismo: el 502 deja que la UI lo diga y reintente.
        return jsonify({'error': d.get('_error') or 'AniList no devolvió la obra'}), 502

    # Autores: AniList mete dibujante, asistentes y hasta el editor. Nos quedamos con quien
    # firma la obra, y sin repetir a quien hace las dos cosas (lo habitual en manga).
    autores, vistos = [], set()
    for e in ((m.get('staff') or {}).get('edges') or []):
        rol = (e.get('role') or '')
        if not re.search(r'story|art', rol, re.I):
            continue
        nombre = (((e.get('node') or {}).get('name') or {}).get('full') or '').strip()
        if nombre and nombre.lower() not in vistos:
            vistos.add(nombre.lower())
            autores.append(nombre)

    sinopsis = ''
    if md_id:
        from api.mangadex import description_es
        sinopsis = _strip_html(description_es(md_id))
    if not sinopsis:
        sinopsis = _strip_html(m.get('description') or '')

    y0 = (m.get('startDate') or {}).get('year')
    y1 = (m.get('endDate') or {}).get('year')
    info = {
        'al_id':     al_id,
        'synopsis':  sinopsis,
        'genres':    m.get('genres') or [],
        'score':     m.get('meanScore') or m.get('averageScore') or None,
        'chapters':  m.get('chapters'),
        'volumes':   m.get('volumes'),
        'status':    _STATUS_ES.get(m.get('status') or '', ''),
        'kind':      _COUNTRY_ES.get(m.get('countryOfOrigin') or '')
                     or _FORMAT_ES.get(m.get('format') or '', ''),
        'years':     f'{y0}–{y1}' if y0 and y1 and y1 != y0 else (str(y0) if y0 else ''),
        'authors':   autores[:2],
        'url':       m.get('siteUrl') or '',
    }
    _cache_set('al_manga_info', key, info, ttl=_REC_TTL, max_entries=600)
    return jsonify(info)


def _rec_row(node: dict) -> dict | None:
    m = node.get('mediaRecommendation')
    if not m:
        return None
    t = m.get('title') or {}
    return {
        'al_id':        m['id'],
        'mal_id':       m.get('idMal'),
        'title':        t.get('english') or t.get('romaji') or t.get('native') or '',
        'title_romaji': t.get('romaji') or '',
        'cover':        _cover(m),
        'score':        m.get('averageScore') or 0,
        'genres':       (m.get('genres') or [])[:3],
        'format':       m.get('format', ''),
        'chapters':     m.get('chapters') or 0,
        'status':       m.get('status', ''),
        'rating':       node.get('rating') or 0,
    }


def _manga_recs_for(al_id: int) -> list:
    """Recomendaciones de AniList para un manga (cache 24h)."""
    hit = _cache_get('manga_recs', str(al_id), _REC_TTL)
    if hit is not None:
        return hit
    d = _ql(_MANGA_REC_Q, {'id': int(al_id)})
    nodes = (((d or {}).get('Media') or {}).get('recommendations') or {}).get('nodes') or []
    recs = [r for r in (_rec_row(n) for n in nodes) if r and r['title']]
    _cache_set('manga_recs', str(al_id), recs, ttl=_REC_TTL, max_entries=500)
    return recs


def _library_titles() -> list:
    """Títulos (nombres de carpeta) de la biblioteca de manga del modo activo."""
    try:
        from api.roots import series_titles
        return list(series_titles())
    except Exception:
        return []


@anilist_bp.route('/manga/recommendations')
def manga_recommendations():
    """Recomendaciones para UN manga. Acepta ?al_id= o ?title= (se resuelve el id)."""
    al_id = request.args.get('al_id', type=int)
    title = request.args.get('title', '').strip()
    if not al_id and title:
        al_id = _resolve_manga_al_id(title)
    if not al_id:
        return jsonify([])
    return jsonify(_manga_recs_for(al_id))


@anilist_bp.route('/manga/for_you')
def manga_for_you():
    """"Para ti": agrega las recomendaciones de una MUESTRA de tu biblioteca, pondera
    por frecuencia×rating y excluye lo que ya tienes. Cache 24h del resultado agregado
    (clave = huella de la biblioteca) para no repetir el barrido de AniList."""
    titles = _library_titles()
    if not titles:
        return jsonify([])
    from api.for_you import hoy, rotar

    owned = {_norm_key(t) for t in titles}
    # El DÍA entra en la clave junto con la huella de la biblioteca: las recomendaciones rotan a
    # diario (ver abajo), así que la caché tiene que caducar con el día, no sólo con la biblioteca.
    dia = hoy()
    fp = str(hash((dia, frozenset(owned))))
    cached = _cache_get('manga_for_you', fp, _REC_TTL)
    if cached is not None:
        return jsonify(cached)

    # Muestrea 8 títulos para acotar el gasto de AniList (cada resolución + recs va cacheada, así
    # que en llamadas sucesivas es barato). La ventana se toma sobre el orden alfabético —estable,
    # para que la caché por título sirva— pero GIRA cada día: antes eran siempre los 8 primeros,
    # así que «Para ti» de manga enseñaba lo mismo indefinidamente.
    sample = rotar(sorted(titles, key=str.lower), 8, dia)
    agg: dict = {}
    for tt in sample:
        al = _resolve_manga_al_id(tt)
        if not al:
            continue
        for r in _manga_recs_for(al):
            if not r['al_id'] or _norm_key(r['title']) in owned:
                continue
            e = agg.get(r['al_id'])
            # Peso: sumatorio de ratings (una serie recomendada por varios de tus mangas
            # y con rating alto sube). +1 por aparición para premiar la coincidencia.
            w = (r['rating'] or 0) + 1
            if e:
                e['_w'] += w
                e['_seeds'] += 1
            else:
                r = dict(r); r['_w'] = w; r['_seeds'] = 1
                agg[r['al_id']] = r
    ranked = sorted(agg.values(), key=lambda x: (x['_seeds'], x['_w'], x['score']), reverse=True)
    for r in ranked:
        r.pop('_w', None); r.pop('_seeds', None)
    ranked = ranked[:24]
    _cache_set('manga_for_you', fp, ranked, ttl=_REC_TTL, max_entries=20)
    return jsonify(ranked)


@anilist_bp.route('/variants')
def variants_route():
    """Debug/UI endpoint: name variants for a title (or al_id)."""
    title = request.args.get('title', '').strip()
    al_id = request.args.get('al_id', '').strip()
    media_type = request.args.get('type', 'MANGA')
    return jsonify(title_variants(title or None, int(al_id) if al_id.isdigit() else None, media_type))


@anilist_bp.route('/genres')
def get_genres():
    """Genres + all non-adult non-spoiler tags from AniList, combined and sorted. Cached 1 h."""
    global _genres_cache, _genres_ts
    if _genres_cache and time.time() - _genres_ts < _GENRES_TTL:
        return jsonify(_genres_cache)

    q = '''
    {
      GenreCollection
      MediaTagCollection { name category isAdult isGeneralSpoiler }
    }
    '''
    d = _ql(q)
    genres = [{'name': g, 'type': 'genre', 'category': 'Genre'} for g in (d.get('GenreCollection') or [])]
    tags   = [
        {'name': t['name'], 'type': 'tag', 'category': t.get('category', 'Other')}
        for t in (d.get('MediaTagCollection') or [])
        if not t.get('isAdult') and not t.get('isGeneralSpoiler')
    ]
    # Sort tags: Demographic first, then Theme-Romance, then rest alphabetically
    _order = {'Demographic': 0, 'Theme-Romance': 1}
    tags.sort(key=lambda t: (_order.get(t['category'], 99), t['name']))

    combined = genres + tags
    if combined:
        _genres_cache = combined
        _genres_ts = time.time()
    return jsonify(combined)
