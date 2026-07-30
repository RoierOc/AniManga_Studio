#!/usr/bin/env python3
"""
MangaDex API Integration
Full authentication and library management
"""

from flask import Blueprint, jsonify, request, Response
import requests
import time
import json
from pathlib import Path
import os
from urllib.parse import urlparse, quote

from api.runtime import MANGA_DIR, normalize_chapter, write_json_atomic

auth_bp = Blueprint('mangadex', __name__)

# Shared resilient client (retry + backoff + per-host rate limiting). Drop-in for a Session:
# exposes .get/.post/.delete and returns requests.Response. It honors MangaDex's 429s and
# never injects a browser User-Agent — MangaDex's WAF 400s any Chrome/Firefox UA; the default
# `python-requests/x.x` UA is allowed through.
from api.resilient_http import http as _SESSION
from api.observability import record_error

MANGA_DIR = str(MANGA_DIR)

# MangaDex credentials — env vars take priority; fallback reads .env from project root
def _load_env_file():
    """Read key=value pairs from .env without requiring python-dotenv."""
    from pathlib import Path as _Path
    env_file = _Path(__file__).resolve().parents[2] / ".env"
    pairs = {}
    try:
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            pairs[k.strip()] = v.strip()
    except Exception:
        pass
    return pairs

_env = _load_env_file()

def _credential(key):
    # Reads from the runtime config store (Ajustes) → env → .env, so credentials
    # saved from the UI apply immediately without a restart. Resolved at call
    # time, never cached at import.
    from api.config_store import get_secret
    return get_secret(key) or os.environ.get(key) or _env.get(key, "")

# Token cache
_token_cache = {
    "access_token": None,
    "refresh_token": None,
    "expires_at": 0
}


def _first_title(attrs):
    title_map = attrs.get("title", {}) or {}
    if not title_map:
        return "Untitled"
    return title_map.get("en") or next(iter(title_map.values()), "Untitled")


def _canon(s: str) -> str:
    """Normaliza un título para comparar: minúsculas y sólo alfanumérico (ignora idioma de
    puntuación/espacios). 'The Moon on a Rainy Night' ~ 'themoononarainynight'."""
    return ''.join(ch for ch in (s or '').lower() if ch.isalnum())


def _all_titles(attrs: dict) -> list:
    """Todos los títulos de una obra MangaDex: principal (todos los idiomas) + altTitles."""
    out = []
    for v in (attrs.get("title") or {}).values():
        if v: out.append(v)
    for alt in (attrs.get("altTitles") or []):
        for v in (alt or {}).values():
            if v: out.append(v)
    return out


def resolve_manga_by_title(title: str, al_id=None, fuzzy=False):
    """Resuelve un título LOCAL a una obra de MangaDex probando TODAS sus variantes de nombre
    (romaji/inglés/nativo/sinónimos vía AniList), no sólo el nombre principal — así un manga
    cuya carpeta usa el romaji encuentra su entrada aunque MangaDex la indexe en inglés, y
    viceversa. Compara cada variante contra el título principal Y los altTitles de cada
    candidato (canónico: alfanumérico en minúsculas).

    Devuelve el dict serializado del mejor match EXACTO, o None. Con `fuzzy=True` (p.ej. para
    traer PORTADAS, donde acertar la obra exacta importa menos que mostrar algo), si no hay
    match exacto cae al MEJOR candidato (el primer resultado del 1er variante, que MangaDex
    rankea por relevancia) y lo marca `approx: True` para que la UI avise y permita corregir."""
    try:
        from api.anilist import title_variants
        variants = title_variants(title, int(al_id) if al_id else None) or [title]
    except Exception:
        variants = [title]
    variants = [v for v in variants if v]
    canon_variants = {_canon(v) for v in variants}
    if not canon_variants:
        return None

    candidates = {}
    first_id = None
    for q in variants[:5]:   # romaji + inglés + nativo + 2 sinónimos
        try:
            r = _SESSION.get("https://api.mangadex.org/manga", params={
                "title": q, "limit": 8, "includes[]": ["cover_art"],
                "contentRating[]": _ALL_RATINGS,
            }, timeout=15)
            if r.status_code != 200:
                continue
            for m in r.json().get("data", []):
                if first_id is None:
                    first_id = m["id"]
                candidates.setdefault(m["id"], m)
        except Exception:
            continue

    for m in candidates.values():
        attrs = m.get("attributes", {})
        if any(_canon(t) in canon_variants for t in _all_titles(attrs)):
            return _parse_manga_list([m])[0]   # exact alias match — take it
    if fuzzy and first_id and first_id in candidates:
        out = _parse_manga_list([candidates[first_id]])[0]
        out["approx"] = True   # mejor coincidencia aproximada (sin match exacto de alias)
        return out
    return None

def get_access_token():
    """Get valid access token, refresh if needed"""
    current_time = time.time()
    
    # Check if we have a valid token
    if _token_cache["access_token"] and current_time < _token_cache["expires_at"]:
        return _token_cache["access_token"]
    
    # Need to get new token
    try:
        # Try refresh token first
        if _token_cache["refresh_token"]:
            r = _SESSION.post(
                "https://auth.mangadex.org/realms/mangadex/protocol/openid-connect/token",
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": _token_cache["refresh_token"],
                    "client_id": _credential("MANGADEX_CLIENT_ID"),
                    "client_secret": _credential("MANGADEX_CLIENT_SECRET")
                }
            )
            if r.status_code == 200:
                data = r.json()
                _token_cache["access_token"] = data.get("access_token")
                _token_cache["refresh_token"] = data.get("refresh_token", _token_cache["refresh_token"])
                _token_cache["expires_at"] = current_time + data.get("expires_in", 900) - 30
                return _token_cache["access_token"]
        
        # No refresh token or refresh failed, do full login
        r = _SESSION.post(
            "https://auth.mangadex.org/realms/mangadex/protocol/openid-connect/token",
            data={
                "grant_type": "password",
                "username": _credential("MANGADEX_USERNAME"),
                "password": _credential("MANGADEX_PASSWORD"),
                "client_id": _credential("MANGADEX_CLIENT_ID"),
                "client_secret": _credential("MANGADEX_CLIENT_SECRET")
            }
        )
        
        if r.status_code != 200:
            return None
        
        data = r.json()
        _token_cache["access_token"] = data.get("access_token")
        _token_cache["refresh_token"] = data.get("refresh_token")
        _token_cache["expires_at"] = current_time + data.get("expires_in", 900) - 30
        
        return _token_cache["access_token"]
    
    except Exception as e:
        print(f"Error getting token: {e}")
        return None

@auth_bp.route('/login', methods=['POST'])
def login():
    """Login to MangaDex"""
    token = get_access_token()
    if not token:
        return jsonify({"error": "Login failed"}), 401
    
    return jsonify({
        "logged_in": True,
        "token": token[:20] + "...",
        "expires_in": 900
    })

@auth_bp.route('/check')
def check():
    """Check if authenticated"""
    token = get_access_token()
    return jsonify({"authenticated": token is not None})

@auth_bp.route('/library')
def get_library():
    """Get user's followed manga library"""
    token = get_access_token()
    print(f"DEBUG: Token = {token[:20] if token else 'None'}...")
    
    if not token:
        return jsonify({"error": "Not authenticated"}), 401
    
    try:
        r = _SESSION.get(
            "https://api.mangadex.org/user/follows/manga",
            headers={"Authorization": f"Bearer {token}"},
            params={"limit": 100, "includes[]": "cover_art"}
        )
        
        print(f"DEBUG: Library request status = {r.status_code}")
        
        if r.status_code != 200:
            return jsonify({"error": "Failed to get library", "details": r.text[:200]}), 400
        
        data = r.json()
        library = []
        
        for m in data.get("data", []):
            attrs = m["attributes"]
            title = _first_title(attrs)
            
            # Get cover
            cover_url = None
            for rel in m.get("relationships", []):
                if rel.get("type") == "cover_art":
                    filename = rel.get("attributes", {}).get("fileName")
                    if filename:
                        cover_url = f"https://uploads.mangadex.org/covers/{m['id']}/{filename}.256.jpg"
                    break
            
            library.append({
                "id": m["id"],
                "title": title,
                "cover": cover_url,
                "status": attrs.get("status", "unknown"),
                "last_chapter": attrs.get("lastChapter"),
                "year": attrs.get("year"),
                "tags": [t.get("attributes", {}).get("name", {}).get("en") for t in attrs.get("tags", [])[:5]]
            })
        
        return jsonify(library)
    
    except Exception as e:
        return jsonify({"error": str(e)}), 500

_search_cache: dict = {}  # cache_key → (results, timestamp)
_SEARCH_TTL = 300          # 5 minutes
_tags_cache: list = []
_tags_cache_ts: float = 0
_TAGS_TTL = 3600           # 1 hour

_updates_cache: dict = {}  # manga_id → {'chapter_nums': set, 'ts': float}
_UPDATES_TTL = 600         # 10 minutes

_ALL_RATINGS = ["safe", "suggestive", "erotica", "pornographic"]


def _parse_manga_list(data_items: list) -> list:
    """Shared serializer for manga list API responses."""
    results = []
    for m in data_items:
        attrs = m["attributes"]
        title = _first_title(attrs)
        cover_url = None
        for rel in m.get("relationships", []):
            if rel.get("type") == "cover_art":
                fn = rel.get("attributes", {}).get("fileName")
                if fn:
                    cover_url = f"https://uploads.mangadex.org/covers/{m['id']}/{fn}.256.jpg"
                break
        links = attrs.get("links") or {}
        results.append({
            "id": m["id"],
            "title": title,
            "cover": cover_url,
            "status": attrs.get("status"),
            "year": attrs.get("year"),
            "contentRating": attrs.get("contentRating"),
            "mal_id": links.get("mal"),
            "al_id":  links.get("al"),
        })
    return results


@auth_bp.route('/tags')
def get_tags():
    """Return all MangaDex tags, cached 1 hour."""
    global _tags_cache, _tags_cache_ts
    if _tags_cache and time.time() - _tags_cache_ts < _TAGS_TTL:
        return jsonify(_tags_cache)
    try:
        r = _SESSION.get("https://api.mangadex.org/manga/tag", timeout=10)
        if r.status_code != 200:
            return jsonify([])
        tags = []
        for t in r.json().get("data", []):
            attrs = t["attributes"]
            name = attrs.get("name", {}).get("en") or next(iter(attrs.get("name", {}).values()), "")
            if name:
                tags.append({"id": t["id"], "name": name, "group": attrs.get("group", "genre")})
        tags.sort(key=lambda x: (x["group"], x["name"]))
        _tags_cache = tags
        _tags_cache_ts = time.time()
        return jsonify(tags)
    except Exception:
        return jsonify([])


@auth_bp.route('/manga/<manga_id>')
def manga_detail(manga_id):
    """Full metadata for a single manga (synopsis, author, tags)."""
    try:
        r = _SESSION.get(
            f"https://api.mangadex.org/manga/{manga_id}",
            params={"includes[]": ["cover_art", "author", "artist"]},
            timeout=15,
        )
        if r.status_code != 200:
            return jsonify({"error": "Not found"}), 404
        data = r.json().get("data", {})
        attrs = data.get("attributes", {})

        desc_map = attrs.get("description") or {}
        description = desc_map.get("en") or next(iter(desc_map.values()), "") if desc_map else ""

        cover_url = author = artist = None
        for rel in data.get("relationships", []):
            t = rel.get("type")
            if t == "cover_art":
                fn = rel.get("attributes", {}).get("fileName")
                if fn:
                    cover_url = f"https://uploads.mangadex.org/covers/{data['id']}/{fn}.512.jpg"
            elif t == "author" and not author:
                author = rel.get("attributes", {}).get("name")
            elif t == "artist" and not artist:
                artist = rel.get("attributes", {}).get("name")

        tags = []
        for t in attrs.get("tags", []):
            name = t.get("attributes", {}).get("name", {}).get("en")
            if name:
                tags.append({"id": t["id"], "name": name, "group": t.get("attributes", {}).get("group", "genre")})

        return jsonify({
            "id": data["id"],
            "title": _first_title(attrs),
            "description": description,
            "status": attrs.get("status"),
            "year": attrs.get("year"),
            "contentRating": attrs.get("contentRating"),
            "cover": cover_url,
            "author": author,
            "artist": artist,
            "tags": tags,
            "lastChapter": attrs.get("lastChapter"),
            "lastVolume": attrs.get("lastVolume"),
            "links": attrs.get("links") or {},
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@auth_bp.route('/search')
def search():
    """Search MangaDex — public, no auth. Supports tag and content-rating filters."""
    query   = request.args.get("q", "").strip()
    tags    = request.args.getlist("tags[]")
    ratings = request.args.getlist("rating[]") or _ALL_RATINGS

    if len(query) < 2 and not tags:
        return jsonify([])

    cache_key = f"{query.lower()}|{'|'.join(sorted(tags))}|{'|'.join(sorted(ratings))}"
    if cache_key in _search_cache:
        results, ts = _search_cache[cache_key]
        if time.time() - ts < _SEARCH_TTL:
            return jsonify(results)

    try:
        params = {
            "limit": 30,
            "includes[]": ["cover_art"],
            "contentRating[]": ratings,
        }
        if query:
            params["title"] = query
        if tags:
            params["includedTags[]"] = tags

        r = _SESSION.get("https://api.mangadex.org/manga", params=params, timeout=15)
        if r.status_code != 200:
            return jsonify([])
        results = _parse_manga_list(r.json().get("data", []))
        _search_cache[cache_key] = (results, time.time())
        return jsonify(results)
    except Exception as e:
        print(f"MangaDex search error: {e}")
        return jsonify([])


@auth_bp.route('/resolve')
def resolve():
    """Auto-resuelve un título local a su obra de MangaDex por VARIANTES de nombre (sinónimos
    AniList), no sólo por el nombre principal. Params: title (req), al_id (opt). Devuelve el
    dict del manga o {} si no hay match exacto. Lo usa el Tomo Builder (Exportar Tomo)."""
    title = (request.args.get("title") or "").strip()
    al_id = (request.args.get("al_id") or "").strip()
    fuzzy = request.args.get("fuzzy") in ("1", "true")
    if len(title) < 2:
        return jsonify({})
    m = resolve_manga_by_title(title, int(al_id) if al_id.isdigit() else None, fuzzy=fuzzy)
    return jsonify(m or {})


@auth_bp.route('/popular')
def popular():
    """Popular/trending/top-rated manga. Supports tag and content-rating filters."""
    kind    = request.args.get("type", "followed")
    page    = max(1, int(request.args.get("page", 1)))
    tags    = request.args.getlist("tags[]")
    ratings = request.args.getlist("rating[]") or _ALL_RATINGS
    limit   = 50
    offset  = (page - 1) * limit

    order = {
        "followed": {"followedCount": "desc"},
        "latest":   {"latestUploadedChapter": "desc"},
        "rating":   {"rating": "desc"},
    }.get(kind, {"followedCount": "desc"})

    params = {
        "limit": limit,
        "offset": offset,
        "includes[]": ["cover_art"],
        "contentRating[]": ratings,
        **{f"order[{k}]": v for k, v in order.items()},
    }
    if tags:
        params["includedTags[]"] = tags

    try:
        r = _SESSION.get("https://api.mangadex.org/manga", params=params, timeout=15)
        if r.status_code != 200:
            return jsonify({"error": "MangaDex API error", "code": r.status_code}), 502
        data = r.json()
        results = _parse_manga_list(data.get("data", []))
        return jsonify({"results": results, "total": data.get("total", 0), "page": page})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@auth_bp.route('/chapters/<manga_id>')
def get_chapters(manga_id):
    """Get chapters for a manga with all languages and scan groups"""
    token = get_access_token()  # optional — feed is public, token improves rate limits

    try:
        # Get all chapters with pagination
        all_chapters = []
        offset = 0

        while True:
            headers = {"Authorization": f"Bearer {token}"} if token else {}
            r = _SESSION.get(
                f"https://api.mangadex.org/manga/{manga_id}/feed",
                headers=headers,
                params={
                    "limit": 500,
                    "offset": offset,
                    "includes[]": "scanlation_group",
                    "contentRating[]": _ALL_RATINGS,
                    "translatedLanguage[]": ["en", "es", "es-xl", "es-la", "ja", "ko", "pt", "pt-br", "zh", "zh-hk", "ru", "fr", "de", "it", "vi", "id", "th", "pl", "tr", "hu", "ar", "cs", "uk", "bg", "el", "he", "ms", "ro", "sv", "fa", "hi", "bn", "tl"]
                }
            )
            
            if r.status_code != 200:
                return jsonify({"error": "Failed to get chapters"}), 400
            
            data = r.json()
            all_chapters.extend(data.get("data", []))
            
            if len(data.get("data", [])) < 500:
                break
            offset += 500
        
        # Now process all collected chapters
        chapters_dict = {}
        
        for ch in all_chapters:
            attrs = ch["attributes"]
            chapter_raw = attrs.get("chapter")
            chapter = chapter_raw if chapter_raw is not None else "one_shot"
            lang = attrs.get("translatedLanguage", "unknown")
            volume = attrs.get("volume")
            pages = attrs.get("pages", 0)
            title = attrs.get("title")
            publish_at = attrs.get("publishAt")
            
            group_name = None
            for rel in ch.get("relationships", []):
                if rel.get("type") == "scanlation_group":
                    group_name = rel.get("attributes", {}).get("name")
                    break
            
            key = f"{chapter}_{lang}"
            if key not in chapters_dict:
                chapters_dict[key] = {
                    "id": ch["id"],
                    "chapter": chapter,
                    "language": lang,
                    "volume": volume,
                    "pages": pages,
                    "title": title,
                    "publishAt": publish_at,
                    "groups": []
                }
            
            if group_name:
                chapters_dict[key]["groups"].append(group_name)
        
        chapters = list(chapters_dict.values())
        chapters.sort(key=lambda x: float(x["chapter"]) if x["chapter"] and x["chapter"].replace(".", "", 1).isdigit() else 0, reverse=True)
        
        return jsonify(chapters)

    except Exception as e:
        print(f"MangaDex chapters error: {e}")
        return jsonify([])

@auth_bp.route('/read/<chapter_id>', methods=['POST'])
def read_chapter(chapter_id):
    """Mark chapter as read"""
    token = get_access_token()
    if not token:
        return jsonify({"error": "Not authenticated"}), 401
    
    try:
        # Mark as read
        r = _SESSION.post(
            f"https://api.mangadex.org/chapter/{chapter_id}/read",
            headers={"Authorization": f"Bearer {token}"},
            json={}
        )
        
        return jsonify({"success": r.status_code == 200})
    
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@auth_bp.route('/follow/<manga_id>', methods=['POST'])
def follow_manga(manga_id):
    """Follow a manga"""
    token = get_access_token()
    if not token:
        return jsonify({"error": "Not authenticated"}), 401
    
    try:
        r = _SESSION.post(
            f"https://api.mangadex.org/manga/{manga_id}/follow",
            headers={"Authorization": f"Bearer {token}"},
            json={}
        )
        
        return jsonify({"success": r.status_code == 200})
    
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@auth_bp.route('/unfollow/<manga_id>', methods=['POST'])
def unfollow_manga(manga_id):
    """Unfollow a manga"""
    token = get_access_token()
    if not token:
        return jsonify({"error": "Not authenticated"}), 401
    
    try:
        r = _SESSION.delete(
            f"https://api.mangadex.org/manga/{manga_id}/follow",
            headers={"Authorization": f"Bearer {token}"}
        )
        
        return jsonify({"success": r.status_code == 200})
    
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# Local library management. Resuelto en tiempo de llamada (no congelado al
# importar) para que en modo oculto la lista de seguidos viva bajo la raíz oculta.
def _library_file() -> str:
    from api.runtime import manga_dir
    return str(Path(manga_dir()) / "local_library.json")


def _normalize_library_entry(entry):
    if not isinstance(entry, dict):
        return None

    manga_id = entry.get("id") or entry.get("mangaId") or entry.get("manga_id")
    if not manga_id:
        return None

    normalized = dict(entry)
    normalized["id"] = str(manga_id)

    if not normalized.get("title") and normalized.get("name"):
        normalized["title"] = normalized.get("name")

    return normalized

def load_local_library():
    """Load local library from JSON"""
    library_file = _library_file()
    if os.path.exists(library_file):
        try:
            with open(library_file, "r") as f:
                raw = json.load(f)

            if not isinstance(raw, list):
                return []

            dedup = {}
            for item in raw:
                normalized = _normalize_library_entry(item)
                if normalized:
                    dedup[normalized["id"]] = normalized

            cleaned = list(dedup.values())

            # Self-heal malformed entries persisted by older clients.
            if cleaned != raw:
                save_local_library(cleaned)

            return cleaned
        except:
            return []
    return []

def save_local_library(lib):
    """Save local library to JSON"""
    write_json_atomic(_library_file(), lib, indent=2, keep_backup=True)

@auth_bp.route('/local_library')
def get_local_library():
    """Get local library"""
    return jsonify(load_local_library())

@auth_bp.route('/local_library/add', methods=['POST'])
def add_to_local_library():
    """Add manga to local library"""
    data = request.get_json() or {}
    manga = data.get("manga")

    if not isinstance(manga, dict):
        return jsonify({"error": "Manga data required"}), 400

    manga_id = manga.get("id") or manga.get("mangaId") or data.get("mangaId")
    if not manga_id:
        return jsonify({"error": "Manga id required"}), 400

    manga["id"] = str(manga_id)
    if not manga.get("title") and manga.get("name"):
        manga["title"] = manga.get("name")

    # Red de seguridad de PORTADA: si la Obra llega sin portada (algunas del meta-source no la
    # traen) pero sí con al_id, la resolvemos por AniList antes de guardar — así una entrada
    # nunca queda coverless para siempre. Cascada barata: al_id (AniList) → título (AniList).
    if not manga.get("cover"):
        try:
            from api import anilist
            manga["cover"] = (anilist.cover_by_al_id(manga.get("al_id"))
                              or (anilist.cover_by_al_id(anilist._resolve_manga_al_id(manga.get("title")))
                                  if manga.get("title") else None))
        except Exception as e:
            record_error("mangadex", e, op="resolve_cover_on_add",
                         note="no se pudo resolver portada al añadir; entrada sin portada")

    lib = load_local_library()

    for m in lib:
        if str(m.get("id")) == manga["id"]:
            # Idempotent add: if it already exists, return success.
            return jsonify({"success": True, "already_exists": True, "library": lib})

    manga["added_at"] = time.strftime("%Y-%m-%d")
    lib.append(manga)
    save_local_library(lib)
    
    return jsonify({"success": True, "library": lib})

@auth_bp.route('/local_library/remove/<manga_id>', methods=['DELETE'])
def remove_from_local_library(manga_id):
    """Remove manga from local library"""
    lib = load_local_library()
    lib = [m for m in lib if m.get("id") != manga_id]
    save_local_library(lib)
    
    return jsonify({"success": True, "library": lib})

@auth_bp.route('/local_library/check/<manga_id>')
def check_local_library(manga_id):
    """Check if manga is in local library"""
    lib = load_local_library()
    return jsonify({"in_library": any(m.get("id") == manga_id for m in lib)})

@auth_bp.route('/local_library/favorite/<manga_id>', methods=['POST'])
def toggle_favorite(manga_id):
    """Toggle favorite status"""
    lib = load_local_library()
    for m in lib:
        if m.get("id") == manga_id:
            m["favorite"] = not m.get("favorite", False)
            break
    save_local_library(lib)
    return jsonify({"success": True, "library": lib})

MANGA_STATUS_ALLOWED = {'reading', 'completed', 'plan_to_read', 'on_hold', 'dropped', ''}


@auth_bp.route('/local_library/status', methods=['POST'])
def update_reading_status():
    """Update manga reading status"""
    data = request.get_json()
    manga_id = data.get("manga_id")
    status = data.get("status")
    last_chapter = data.get("last_chapter")

    if status is not None and status not in MANGA_STATUS_ALLOWED:
        return jsonify({"error": "Invalid status"}), 400

    lib = load_local_library()
    for m in lib:
        if m.get("id") == manga_id:
            if status is not None:
                m["status"] = status
            if last_chapter:
                m["last_chapter"] = last_chapter
            m["updated_at"] = time.strftime("%Y-%m-%d")
            break
    save_local_library(lib)
    return jsonify({"success": True, "library": lib})


@auth_bp.route('/chapter/<chapter_id>/pages')
def chapter_pages(chapter_id):
    """Resolve a MangaDex chapter's page image URLs for direct reading (no download).
    Always fetches at-home/server fresh — the baseUrl is single-use, never cached."""
    try:
        r = _SESSION.get(f"https://api.mangadex.org/at-home/server/{chapter_id}", timeout=15)
        data = r.json()
        if data.get("result") != "ok":
            return jsonify({"error": "Capítulo no disponible"}), 404
        base = data["baseUrl"]
        hash_val = data["chapter"]["hash"]
        pages = data["chapter"].get("data", [])
        # Serve pages THROUGH the backend (page_proxy): MangaDex@Home nodes return 404 to
        # browser <img> loads (cold-cache + hotlink) even though the same URL works
        # server-side. Proxying makes the fetch server-side (warms the node, retries 404,
        # no cross-origin Referer) and the browser only ever loads a same-origin URL.
        urls = [f"/api/mangadex/page_proxy?u={quote(f'{base}/data/{hash_val}/{p}', safe='')}" for p in pages]
        return jsonify({"pages": urls, "count": len(urls)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@auth_bp.route('/page_proxy')
def page_proxy():
    """Stream a MangaDex@Home page image server-side. Fixes browser-direct 404s
    (cold @home node + hotlink protection). Retries 404 because a cold node returns
    404 while it fetches the image from MangaDex's backend, then serves it."""
    url = request.args.get('u', '')
    p = urlparse(url)
    if p.scheme != 'https' or not p.netloc.lower().endswith('.mangadex.network'):
        return ('', 400)
    last = 502
    for attempt in range(4):
        try:
            r = requests.get(url, timeout=20, stream=True)
            if r.status_code == 200:
                return Response(
                    r.iter_content(65536),
                    content_type=r.headers.get('Content-Type', 'image/jpeg'),
                    headers={'Cache-Control': 'public, max-age=86400'},
                )
            last = r.status_code
            r.close()
            if r.status_code in (404, 429, 502, 503) and attempt < 3:
                time.sleep(0.6)
                continue
            break
        except Exception:
            last = 502
            if attempt < 3:
                time.sleep(0.4)
    return ('', last)

@auth_bp.route('/local_library/update', methods=['POST'])
def update_manga_data():
    """Update manga data in library"""
    data = request.get_json()
    manga_id = data.get("manga_id")
    updates = data.get("updates", {})

    lib = load_local_library()
    for m in lib:
        if m.get("id") == manga_id:
            m.update(updates)
            m["updated_at"] = time.strftime("%Y-%m-%d")
            break
    save_local_library(lib)
    return jsonify({"success": True})


@auth_bp.route('/updates')
def manga_updates():
    """Return library manga that have chapters on MangaDex not yet downloaded locally.
    Only checks manga with at least one local chapter (downloaded, not just saved).
    Results cached per-manga for 10 minutes."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    lib = load_local_library()
    from api.runtime import manga_dir
    manga_dir_path = Path(manga_dir())

    # Collect entries that have local chapters on disk
    candidates = []  # [(manga_id, title, cover, local_chs)]
    for entry in lib:
        manga_id = entry.get('id')
        title = (entry.get('title') or entry.get('name') or '').strip()
        cover = entry.get('cover') or ''
        if not manga_id or not title:
            continue

        local_chs: set = set()
        folder = manga_dir_path / title
        if folder.exists():
            for img in folder.iterdir():
                if img.is_file() and img.suffix.lower() in ('.jpg', '.png', '.webp'):
                    parts = img.stem.split('_')
                    if parts and parts[0].lower().startswith('ch'):
                        local_chs.add(normalize_chapter(parts[0]))

        if local_chs:
            candidates.append((manga_id, title, cover, local_chs))

    if not candidates:
        return jsonify([])

    def _fetch_remote(manga_id: str) -> set:
        now = time.time()
        cached = _updates_cache.get(manga_id)
        if cached and now - cached['ts'] < _UPDATES_TTL:
            return cached['chapter_nums']
        try:
            r = _SESSION.get(
                f"https://api.mangadex.org/manga/{manga_id}/aggregate",
                timeout=10,
            )
            remote_chs: set = set()
            if r.ok:
                for vol_data in (r.json().get('volumes') or {}).values():
                    for ch_num in (vol_data.get('chapters') or {}).keys():
                        norm = normalize_chapter(ch_num)
                        if norm and norm != 'one_shot':
                            remote_chs.add(norm)
            _updates_cache[manga_id] = {'chapter_nums': remote_chs, 'ts': now}
            return remote_chs
        except Exception:
            return set()

    # Fetch all remote chapter sets in parallel (max 6 concurrent)
    remote_map: dict = {}
    ids_to_fetch = [mid for mid, *_ in candidates]
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = {pool.submit(_fetch_remote, mid): mid for mid in ids_to_fetch}
        for future in as_completed(futures):
            mid = futures[future]
            try:
                remote_map[mid] = future.result()
            except Exception:
                remote_map[mid] = set()

    def _num_sort(c):
        try:
            return float(c)
        except (ValueError, TypeError):
            return 0.0

    result = []
    for manga_id, title, cover, local_chs in candidates:
        remote_chs = remote_map.get(manga_id, set())

        # Convert local chapter numbers to floats for comparison
        local_nums = set()
        for c in local_chs:
            try:
                local_nums.add(float(c))
            except (ValueError, TypeError):
                pass

        max_local = max(local_nums) if local_nums else 0.0

        # "New" = chapters on MangaDex with a number GREATER than the highest local chapter.
        # This avoids flagging intentionally-skipped back-catalogue chapters as "new".
        new_chs = []
        for c in remote_chs:
            try:
                if float(c) > max_local:
                    new_chs.append(c)
            except (ValueError, TypeError):
                pass

        if not new_chs:
            continue
        sorted_new = sorted(new_chs, key=_num_sort)
        result.append({
            'manga_id': manga_id,
            'title': title,
            'cover': cover,
            'new_count': len(sorted_new),
            'new_chapters': sorted_new[:50],
            'latest_local': str(int(max_local)) if max_local == int(max_local) else str(max_local),
        })

    result.sort(key=lambda x: x['title'].lower())
    return jsonify(result)


@auth_bp.route('/updates/refresh', methods=['POST'])
def manga_updates_refresh():
    """Clear the updates cache so next /updates call re-fetches from MangaDex."""
    _updates_cache.clear()
    return jsonify({'ok': True})


@auth_bp.route('/volumes/<manga_id>')
def get_volumes(manga_id):
    """Return volume → chapter list mapping.
    Priority: aggregate (matches website display) → feed fallback for external chapters.
    Response: [{ volume, chapters: ["1","2",...], feedFallback? }, ...] sorted by volume number.
    feedFallback=true is set when aggregate had no data and we fell back to the feed,
    so the frontend knows to also attempt the scraper for a more accurate result."""
    try:
        volume_chapters = {}  # vol_key → set of chapter number strings

        # Prefer aggregate: it mirrors what MangaDex website shows per volume,
        # avoiding cross-language volume assignment conflicts from the feed.
        agg_r = _SESSION.get(
            f"https://api.mangadex.org/manga/{manga_id}/aggregate",
            timeout=10,
        )
        if agg_r.ok:
            agg_data = agg_r.json()
            for vol_key, vol_data in (agg_data.get("volumes", {}) or {}).items():
                chaps = set(vol_data.get("chapters", {}).keys())
                if chaps:
                    volume_chapters[vol_key] = chaps

        agg_had_data = bool(volume_chapters)

        # Fall back to full feed if aggregate returned no chapter data
        # (e.g. all chapters hosted externally / not indexed by MangaDex).
        if not agg_had_data:
            offset = 0
            limit = 500
            while True:
                r = _SESSION.get(
                    f"https://api.mangadex.org/manga/{manga_id}/feed",
                    params={"limit": limit, "offset": offset,
                            "order[chapter]": "asc", "order[volume]": "asc"},
                    timeout=20,
                )
                if not r.ok:
                    break
                data = r.json()
                batch = data.get("data", [])
                for ch in batch:
                    attrs = ch.get("attributes", {})
                    vol  = str(attrs.get("volume") or "none")
                    chap = attrs.get("chapter")
                    if chap is not None:
                        volume_chapters.setdefault(vol, set()).add(str(chap))
                total = data.get("total", 0)
                offset += limit
                if offset >= total or not batch:
                    break

        def _ch_sort_key(x):
            try: return float(x)
            except ValueError: return -1

        result = []
        for vol_key, chap_set in volume_chapters.items():
            chapters = sorted(chap_set, key=_ch_sort_key)
            entry = {
                "volume": vol_key,
                "label": f"Tomo {vol_key}" if vol_key != "none" else "Sin tomo",
                "chapters": chapters,
                "count": len(chapters),
            }
            if not agg_had_data:
                entry["feedFallback"] = True
            result.append(entry)

        def _vol_sort(v):
            try: return (0, float(v["volume"]))
            except ValueError: return (1, 0)

        result.sort(key=_vol_sort)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@auth_bp.route('/scrape_volumes/<manga_id>')
def scrape_volumes(manga_id):
    """Use headless Chromium (Playwright) to render the MangaDex title page and
    extract volume→chapter-range data from the summary grid rows.
    Each row shows 'Volume N / Ch. X - Y / count' — this gives the exact range
    even when individual chapters are 'unavailable' in the public API.
    Returns: [{volume, label, chapters: [min, max], count, fromScrape}]"""
    import re
    try:
        from playwright.sync_api import sync_playwright

        results = []
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(
                f"https://mangadex.org/title/{manga_id}",
                wait_until="networkidle",
                timeout=30000,
            )
            # Scroll to trigger any lazy-loaded volume rows
            for _ in range(12):
                page.evaluate("window.scrollBy(0, 600)")
                page.wait_for_timeout(300)
            page.wait_for_timeout(1500)

            rows = page.query_selector_all(".grid.grid-cols-12")
            for row in rows:
                text = row.inner_text().strip()
                lines = [l.strip() for l in text.split("\n") if l.strip()]
                if len(lines) < 2:
                    continue

                vol_label = lines[0]
                ch_text   = lines[1]

                # Extract volume key language-agnostically: just find the number in the
                # first cell (works for "Volume 2", "Volumen 2", "第2巻", etc.).
                # If the cell has no number it's a "No Volume" row in whatever language.
                vol_num_m = re.search(r'(\d+(?:\.\d+)?)', vol_label)
                if vol_num_m:
                    vol_key = vol_num_m.group(1)
                else:
                    # Accept any "no volume" label (no digits in the first cell)
                    vol_key = "none"

                # The second cell must contain a chapter number — skip unrelated grid rows.
                ch_range_m = re.search(r'(\d+(?:\.\d+)?)\s*[-–]\s*(\d+(?:\.\d+)?)', ch_text)
                if ch_range_m:
                    min_ch, max_ch = ch_range_m.group(1), ch_range_m.group(2)
                else:
                    single_m = re.search(r'(\d+(?:\.\d+)?)', ch_text)
                    if not single_m:
                        continue
                    min_ch = max_ch = single_m.group(1)
                chapters = [min_ch] if min_ch == max_ch else [min_ch, max_ch]

                results.append({
                    "volume": vol_key,
                    "label": f"Tomo {vol_key}" if vol_key != "none" else "Sin tomo",
                    "chapters": chapters,
                    "count": int(lines[2]) if len(lines) > 2 and lines[2].isdigit() else len(chapters),
                    "fromScrape": True,
                })
            browser.close()

        if not results:
            return jsonify({"error": "no_volumes_found"}), 404

        def _vol_sort(v):
            try: return (0, float(v["volume"]))
            except ValueError: return (1, 0)

        results.sort(key=_vol_sort)
        return jsonify(results)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@auth_bp.route('/covers/<manga_id>')
def get_covers(manga_id):
    """Return all cover art for a manga, sorted by volume (asc).
    Response: [{ id, volume, fileName, url, url512, url256, locale }, ...]"""
    try:
        covers = []
        offset = 0
        limit = 100
        while True:
            r = _SESSION.get(
                "https://api.mangadex.org/cover",
                params={
                    "manga[]": manga_id,
                    "limit": limit,
                    "offset": offset,
                    "order[volume]": "asc",
                },
                timeout=10,
            )
            if not r.ok:
                break
            data = r.json()
            items = data.get("data", [])
            for item in items:
                attrs = item.get("attributes", {})
                fname = attrs.get("fileName", "")
                covers.append({
                    "id": item["id"],
                    "volume": attrs.get("volume") or "?",
                    "fileName": fname,
                    "locale": attrs.get("locale", ""),
                    "description": attrs.get("description", ""),
                    "url": f"https://uploads.mangadex.org/covers/{manga_id}/{fname}",
                    "url512": f"https://uploads.mangadex.org/covers/{manga_id}/{fname}.512.jpg",
                    "url256": f"https://uploads.mangadex.org/covers/{manga_id}/{fname}.256.jpg",
                })
            total = data.get("total", 0)
            offset += limit
            if offset >= total:
                break

        return jsonify(covers)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@auth_bp.route('/cover_b64', methods=['POST'])
def fetch_cover_b64():
    """Proxy-fetch a cover image URL and return it as base64 for CBZ embedding.
    Needed because the frontend can't CORS-fetch uploads.mangadex.org as binary."""
    data = request.get_json(silent=True) or {}
    url = (data.get("url") or "").strip()
    if not url or "mangadex.org" not in url:
        return jsonify({"error": "invalid url"}), 400
    try:
        r = _SESSION.get(url, timeout=15)
        if not r.ok:
            return jsonify({"error": f"HTTP {r.status_code}"}), 502
        import base64
        b64 = base64.b64encode(r.content).decode()
        mime = r.headers.get("content-type", "image/jpeg").split(";")[0]
        return jsonify({"b64": b64, "mime": mime, "data_url": f"data:{mime};base64,{b64}"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500