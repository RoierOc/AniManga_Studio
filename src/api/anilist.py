#!/usr/bin/env python3
"""
AniList API — batch score lookup, top manga, genre list.
No API key required. Rate limit: ~90 req/min.
"""

from flask import Blueprint, jsonify, request
import requests as _http
import time

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
