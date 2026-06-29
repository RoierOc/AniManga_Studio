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
