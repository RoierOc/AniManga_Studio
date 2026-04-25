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
            r = requests.post(
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
        r = requests.post(
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
        r = requests.get(
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
    """Search MangaDex"""
    query = request.args.get("q", "")
    if len(query) < 2:
        return jsonify([])
    
    token = get_access_token()
    
    try:
        r = requests.get(
            "https://api.mangadex.org/manga",
            params={"title": query, "limit": 20, "includes[]": "cover_art"}
        )
        
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
        return jsonify({"error": str(e)}), 500

@auth_bp.route('/chapters/<manga_id>')
def get_chapters(manga_id):
    """Get chapters for a manga with all languages and scan groups"""
    token = get_access_token()
    if not token:
        return jsonify({"error": "Not authenticated"}), 401
    
    try:
        # Get all chapters with pagination
        all_chapters = []
        offset = 0
        
        while True:
            r = requests.get(
                f"https://api.mangadex.org/manga/{manga_id}/feed",
                headers={"Authorization": f"Bearer {token}"},
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
        return jsonify({"error": str(e)}), 500

@auth_bp.route('/read/<chapter_id>', methods=['POST'])
def read_chapter(chapter_id):
    """Mark chapter as read"""
    token = get_access_token()
    if not token:
        return jsonify({"error": "Not authenticated"}), 401
    
    try:
        # Mark as read
        r = requests.post(
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
        r = requests.post(
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
        r = requests.delete(
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