#!/usr/bin/env python3
"""
MangaDex API Integration
Full authentication and library management
"""

from flask import Blueprint, jsonify, request
import requests
import time
import json
from pathlib import Path
import os

from api.runtime import MANGA_DIR

auth_bp = Blueprint('mangadex', __name__)

_SESSION = requests.Session()
_SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
})

MANGA_DIR = str(MANGA_DIR)

# MangaDex credentials
CLIENT_ID = "REDACTED_ROTATE_REQUIRED"
CLIENT_SECRET = "REDACTED_ROTATE_REQUIRED"
USERNAME = "REDACTED_ROTATE_REQUIRED"
PASSWORD = "REDACTED_ROTATE_REQUIRED"

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
                    "client_id": CLIENT_ID,
                    "client_secret": CLIENT_SECRET
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
                "username": USERNAME,
                "password": PASSWORD,
                "client_id": CLIENT_ID,
                "client_secret": CLIENT_SECRET
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

@auth_bp.route('/search')
def search():
    """Search MangaDex — public endpoint, no auth required"""
    query = request.args.get("q", "")
    if len(query) < 2:
        return jsonify([])

    try:
        r = _SESSION.get(
            "https://api.mangadex.org/manga",
            params={"title": query, "limit": 20, "includes[]": "cover_art"},
            timeout=15
        )
        if r.status_code != 200:
            return jsonify([])
        data = r.json()
        results = []
        
        for m in data.get("data", []):
            attrs = m["attributes"]
            title = _first_title(attrs)
            
            cover_url = None
            for rel in m.get("relationships", []):
                if rel.get("type") == "cover_art":
                    filename = rel.get("attributes", {}).get("fileName")
                    if filename:
                        cover_url = f"https://uploads.mangadex.org/covers/{m['id']}/{filename}.256.jpg"
                    break
            
            results.append({
                "id": m["id"],
                "title": title,
                "cover": cover_url,
                "status": attrs.get("status"),
                "year": attrs.get("year"),
                "last_chapter": attrs.get("lastChapter")
            })
        
        return jsonify(results)

    except Exception as e:
        print(f"MangaDex search error: {e}")
        return jsonify([])

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
            chapter = attrs.get("chapter") or "0"
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

# Local library management
LIBRARY_FILE = str(Path(MANGA_DIR) / "local_library.json")


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
    if os.path.exists(LIBRARY_FILE):
        try:
            with open(LIBRARY_FILE, "r") as f:
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
    os.makedirs(os.path.dirname(LIBRARY_FILE), exist_ok=True)
    with open(LIBRARY_FILE, "w") as f:
        json.dump(lib, f, indent=2)

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

@auth_bp.route('/local_library/status', methods=['POST'])
def update_reading_status():
    """Update manga reading status"""
    data = request.get_json()
    manga_id = data.get("manga_id")
    status = data.get("status")
    last_chapter = data.get("last_chapter")
    
    lib = load_local_library()
    for m in lib:
        if m.get("id") == manga_id:
            if status:
                m["status"] = status
            if last_chapter:
                m["last_chapter"] = last_chapter
            m["updated_at"] = time.strftime("%Y-%m-%d")
            break
    save_local_library(lib)
    return jsonify({"success": True, "library": lib})

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


@auth_bp.route('/volumes/<manga_id>')
def get_volumes(manga_id):
    """Return volume → chapter list mapping.
    Uses /feed (all languages, paginated) so external/official chapters are included —
    the /aggregate endpoint silently omits chapters hosted outside MangaDex.
    Response: [{ volume, chapters: ["1","2",...] }, ...] sorted by volume number."""
    try:
        volume_chapters = {}  # vol_key → set of chapter number strings
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

        # Fallback to aggregate if feed returned nothing
        if not volume_chapters:
            r = _SESSION.get(f"https://api.mangadex.org/manga/{manga_id}/aggregate", timeout=10)
            data = r.json() if r.ok else {}
            for vol_key, vol_data in (data.get("volumes", {}) or {}).items():
                volume_chapters[vol_key] = set(vol_data.get("chapters", {}).keys())

        def _ch_sort_key(x):
            try: return float(x)
            except ValueError: return -1

        result = []
        for vol_key, chap_set in volume_chapters.items():
            chapters = sorted(chap_set, key=_ch_sort_key)
            result.append({
                "volume": vol_key,
                "label": f"Tomo {vol_key}" if vol_key != "none" else "Sin tomo",
                "chapters": chapters,
                "count": len(chapters),
            })

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
                ch_text = lines[1]

                if vol_label.startswith("Volume "):
                    vol_key = vol_label[len("Volume "):].strip()
                elif vol_label in ("No Volume", "Sin Volumen", "No volume"):
                    vol_key = "none"
                else:
                    continue

                # Parse chapter numbers from "Ch. 37 - 44", "Ch. 22", "Ch. 0, 36"
                ch_nums = re.findall(r"\d+(?:\.\d+)?", ch_text)
                if not ch_nums:
                    continue

                # Store only min and max — sufficient for range-based selection
                min_ch, max_ch = ch_nums[0], ch_nums[-1]
                chapters = [min_ch] if min_ch == max_ch else [min_ch, max_ch]

                results.append({
                    "volume": vol_key,
                    "label": f"Tomo {vol_key}" if vol_key != "none" else "Sin tomo",
                    "chapters": chapters,
                    "count": int(lines[2]) if len(lines) > 2 and lines[2].isdigit() else len(ch_nums),
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