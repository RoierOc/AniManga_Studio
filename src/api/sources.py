#!/usr/bin/env python3
"""
Sources API — proxy Flask → Suwayomi GraphQL.
Suwayomi exposes Tachiyomi/Mihon extensions via GraphQL at localhost:4567.
"""

from flask import Blueprint, jsonify, request
import requests as http_requests

sources_bp = Blueprint("sources", __name__)

SUWAYOMI_URL = "http://localhost:4567/api/graphql"
SUWAYOMI_BASE = "http://localhost:4567"
_GQL_TIMEOUT = 20


def _gql(query: str, variables: dict = None):
    resp = http_requests.post(
        SUWAYOMI_URL,
        json={"query": query, "variables": variables or {}},
        timeout=_GQL_TIMEOUT,
    )
    resp.raise_for_status()
    data = resp.json()
    if "errors" in data:
        raise RuntimeError(data["errors"])
    return data["data"]


def _suwayomi_online() -> bool:
    try:
        http_requests.get(SUWAYOMI_BASE, timeout=3)
        return True
    except Exception:
        return False


# ── Health ────────────────────────────────────────────────────────────────────

@sources_bp.route("/health", methods=["GET"])
def health():
    online = _suwayomi_online()
    return jsonify({"online": online, "url": SUWAYOMI_BASE})


# ── Sources list ──────────────────────────────────────────────────────────────

@sources_bp.route("/list", methods=["GET"])
def list_sources():
    try:
        data = _gql("""
            query {
              sources {
                nodes {
                  id
                  name
                  lang
                  iconUrl
                  isNsfw
                  supportsLatest
                }
              }
            }
        """)
        nodes = data["sources"]["nodes"]
        # Filter out the local source (id "0") — not useful for search
        sources = [
            {
                "id": n["id"],
                "name": n["name"],
                "lang": n["lang"],
                "iconUrl": SUWAYOMI_BASE + n["iconUrl"] if n.get("iconUrl") else None,
                "isNsfw": n.get("isNsfw", False),
                "supportsLatest": n.get("supportsLatest", False),
            }
            for n in nodes
            if n["id"] != "0"
        ]
        return jsonify(sources)
    except Exception as e:
        return jsonify({"error": str(e), "offline": not _suwayomi_online()}), 503


# ── Search ────────────────────────────────────────────────────────────────────

@sources_bp.route("/search_all", methods=["GET"])
def search_all():
    """Search a query across every installed source in parallel."""
    query = (request.args.get("q") or "").strip()
    if not query:
        return jsonify({"error": "q is required"}), 400

    try:
        src_data = _gql("{ sources { nodes { id name lang } } }")
        source_nodes = [n for n in src_data["sources"]["nodes"] if n["id"] != "0"]
    except Exception as e:
        return jsonify({"error": str(e), "offline": not _suwayomi_online()}), 503

    def _search_one(source):
        try:
            data = _gql(
                """
                mutation SearchManga($source: LongString!, $query: String, $page: Int!) {
                  fetchSourceManga(input: { source: $source, type: SEARCH, query: $query, page: $page }) {
                    mangas { id title thumbnailUrl inLibrary }
                  }
                }
                """,
                {"source": source["id"], "query": query, "page": 1},
            )
            mangas = [
                {
                    "id": m["id"],
                    "title": m["title"],
                    "thumbnailUrl": SUWAYOMI_BASE + m["thumbnailUrl"] if m.get("thumbnailUrl") else None,
                    "inLibrary": m.get("inLibrary", False),
                    "sourceId": source["id"],
                    "sourceName": source["name"],
                    "sourceLang": source["lang"],
                }
                for m in data["fetchSourceManga"]["mangas"]
            ]
            return {"source": source, "results": mangas}
        except Exception as e:
            return {"source": source, "results": [], "error": str(e)}

    from concurrent.futures import ThreadPoolExecutor, as_completed
    groups = []
    with ThreadPoolExecutor(max_workers=6) as pool:
        futs = {pool.submit(_search_one, s): s for s in source_nodes}
        for fut in as_completed(futs):
            groups.append(fut.result())

    groups.sort(key=lambda g: -len(g["results"]))
    return jsonify(groups)


@sources_bp.route("/search", methods=["GET"])
def search():
    source_id = (request.args.get("source") or "").strip()
    query = (request.args.get("q") or "").strip()
    page = max(1, int(request.args.get("page", 1)))

    if not source_id or not query:
        return jsonify({"error": "source and q are required"}), 400

    try:
        data = _gql(
            """
            mutation SearchManga($source: LongString!, $query: String, $page: Int!) {
              fetchSourceManga(input: { source: $source, type: SEARCH, query: $query, page: $page }) {
                mangas {
                  id
                  title
                  thumbnailUrl
                  inLibrary
                }
                hasNextPage
              }
            }
            """,
            {"source": source_id, "query": query, "page": page},
        )
        results = data["fetchSourceManga"]
        mangas = [
            {
                "id": m["id"],
                "title": m["title"],
                "thumbnailUrl": SUWAYOMI_BASE + m["thumbnailUrl"] if m.get("thumbnailUrl") else None,
                "inLibrary": m.get("inLibrary", False),
            }
            for m in results["mangas"]
        ]
        return jsonify({"results": mangas, "hasNextPage": results["hasNextPage"], "page": page})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Manga details + chapters ──────────────────────────────────────────────────

@sources_bp.route("/manga/<int:manga_id>", methods=["GET"])
def manga_details(manga_id):
    """Fetch/refresh manga metadata from its source, then return details."""
    try:
        # fetchManga loads metadata from the remote source
        data = _gql(
            """
            mutation FetchManga($id: Int!) {
              fetchManga(input: { id: $id }) {
                manga {
                  id
                  title
                  description
                  genre
                  status
                  author
                  artist
                  thumbnailUrl
                  url
                  source { id name lang }
                }
              }
            }
            """,
            {"id": manga_id},
        )
        manga = data["fetchManga"]["manga"]
        manga["thumbnailUrl"] = SUWAYOMI_BASE + manga["thumbnailUrl"] if manga.get("thumbnailUrl") else None
        return jsonify(manga)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@sources_bp.route("/manga/<int:manga_id>/chapters", methods=["GET"])
def manga_chapters(manga_id):
    """Return chapter list for a manga. Fetches from source if needed."""
    try:
        # First ensure chapters are loaded from the source
        _gql(
            """
            mutation FetchChapters($id: Int!) {
              fetchChapters(input: { mangaId: $id }) {
                chapters { id }
              }
            }
            """,
            {"id": manga_id},
        )

        data = _gql(
            """
            query GetChapters($mangaId: Int!) {
              chapters(condition: { mangaId: $mangaId }, orderBy: CHAPTER_NUMBER, orderByType: DESC) {
                nodes {
                  id
                  name
                  chapterNumber
                  uploadDate
                  scanlator
                  pageCount
                  isRead
                }
              }
            }
            """,
            {"mangaId": manga_id},
        )
        chapters = data["chapters"]["nodes"]
        return jsonify(chapters)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Pages ─────────────────────────────────────────────────────────────────────

@sources_bp.route("/chapter/<int:chapter_id>/pages", methods=["GET"])
def chapter_pages(chapter_id):
    """Return full image URLs for a chapter's pages."""
    try:
        data = _gql(
            """
            mutation FetchPages($chapterId: Int!) {
              fetchChapterPages(input: { chapterId: $chapterId }) {
                pages
              }
            }
            """,
            {"chapterId": chapter_id},
        )
        pages = data["fetchChapterPages"]["pages"]
        # Pages are relative paths — prefix with Suwayomi base URL
        full_urls = [SUWAYOMI_BASE + p if p.startswith("/") else p for p in pages]
        return jsonify({"pages": full_urls, "count": len(full_urls)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Extensions ────────────────────────────────────────────────────────────────

@sources_bp.route("/extensions", methods=["GET"])
def list_extensions():
    """List all available extensions (installed and not installed)."""
    try:
        data = _gql("""
            query {
              extensions {
                nodes {
                  pkgName
                  name
                  lang
                  versionName
                  isInstalled
                  isObsolete
                  iconUrl
                }
              }
            }
        """)
        exts = [
            {
                "pkgName": e["pkgName"],
                "name": e["name"],
                "lang": e["lang"],
                "versionName": e["versionName"],
                "isInstalled": e["isInstalled"],
                "isObsolete": e.get("isObsolete", False),
                "iconUrl": SUWAYOMI_BASE + e["iconUrl"] if e.get("iconUrl") else None,
            }
            for e in data["extensions"]["nodes"]
        ]
        return jsonify(exts)
    except Exception as e:
        return jsonify({"error": str(e)}), 503


@sources_bp.route("/extensions/install", methods=["POST"])
def install_extension():
    """Install an extension by package name."""
    body = request.get_json(silent=True) or {}
    pkg = (body.get("pkgName") or "").strip()
    if not pkg:
        return jsonify({"error": "pkgName required"}), 400
    try:
        data = _gql(
            """
            mutation InstallExtension($pkg: String!) {
              installExternalExtension(input: { extensionUrl: $pkg }) {
                extension { pkgName isInstalled }
              }
            }
            """,
            {"pkg": pkg},
        )
        return jsonify(data)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
