#!/usr/bin/env python3
"""
AniList API — batch score lookup, top manga, genre list.
No API key required. Rate limit: ~90 req/min.
"""

from flask import Blueprint, jsonify, request
import time

from api.resilient_http import http as _http  # retry + Retry-After + rate limiting

anilist_bp = Blueprint('anilist', __name__)

_URL = 'https://graphql.anilist.co'
_genres_cache: list = []
_genres_ts: float = 0.0
_top_cache: dict = {}
_TOP_TTL = 300   # 5 min
_GENRES_TTL = 3600


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
          coverImage { large }
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
            'cover':        (item.get('coverImage') or {}).get('large'),
            'status':       item.get('status'),
            'chapters':     item.get('chapters'),
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
          coverImage { medium large }
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
            'cover': (m.get('coverImage') or {}).get('large') or (m.get('coverImage') or {}).get('medium'),
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
          coverImage { large medium }
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
        'cover':        (m.get('coverImage') or {}).get('large') or (m.get('coverImage') or {}).get('medium', ''),
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
        return [f.name for f in _Path(_manga_dir()).iterdir()
                if f.is_dir() and not f.name.startswith('.')]
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
    owned = {_norm_key(t) for t in titles}
    # Huella estable de la biblioteca → clave de caché del agregado.
    fp = str(hash(frozenset(owned)))
    cached = _cache_get('manga_for_you', fp, _REC_TTL)
    if cached is not None:
        return jsonify(cached)

    # Muestrea hasta 8 títulos (los alfabéticamente primeros, estable) para acotar el
    # gasto de AniList; cada resolución + recs va cacheada, así que en llamadas
    # sucesivas es barato aunque la muestra rote.
    sample = sorted(titles, key=str.lower)[:8]
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
