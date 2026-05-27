#!/usr/bin/env python3
"""
Sources API — proxy Flask → Suwayomi GraphQL.
Suwayomi exposes Tachiyomi/Mihon extensions via GraphQL at localhost:4567.
"""

from flask import Blueprint, jsonify, request, Response, stream_with_context
from pathlib import Path
import json as _json
import requests as http_requests

from api.runtime import MANGA_DIR

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


_GQL_STREAM_TIMEOUT = 5  # per-source timeout for streaming search


@sources_bp.route("/search_all_stream", methods=["GET"])
def search_all_stream():
    """Stream global search results as SSE — results arrive as each source responds."""
    query = (request.args.get("q") or "").strip()
    lang  = (request.args.get("lang") or "").strip().lower()
    if not query:
        return jsonify({"error": "q is required"}), 400

    try:
        src_data = _gql("{ sources { nodes { id name lang } } }")
        source_nodes = [n for n in src_data["sources"]["nodes"] if n["id"] != "0"]
    except Exception as e:
        return jsonify({"error": str(e), "offline": not _suwayomi_online()}), 503

    if lang:
        source_nodes = [s for s in source_nodes if s["lang"].lower() == lang]

    def _search_one(source):
        try:
            resp = http_requests.post(
                SUWAYOMI_URL,
                json={
                    "query": """mutation SearchManga($source: LongString!, $query: String, $page: Int!) {
                      fetchSourceManga(input: { source: $source, type: SEARCH, query: $query, page: $page }) {
                        mangas { id title thumbnailUrl inLibrary }
                      }
                    }""",
                    "variables": {"source": source["id"], "query": query, "page": 1},
                },
                timeout=_GQL_STREAM_TIMEOUT,
            )
            resp.raise_for_status()
            data = resp.json()
            if "errors" in data:
                return {"source": source, "results": []}
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
                for m in data["data"]["fetchSourceManga"]["mangas"]
            ]
            return {"source": source, "results": mangas}
        except Exception:
            return {"source": source, "results": []}

    def generate():
        from concurrent.futures import ThreadPoolExecutor, as_completed
        total = len(source_nodes)
        yield f"data: {_json.dumps({'type': 'start', 'total': total})}\n\n"
        done = 0
        with ThreadPoolExecutor(max_workers=20) as pool:
            futs = {pool.submit(_search_one, s): s for s in source_nodes}
            for fut in as_completed(futs):
                result = fut.result()
                done += 1
                if result["results"]:
                    payload = {
                        "type": "result",
                        "done": done,
                        "total": total,
                        "source": result["source"],
                        "results": result["results"],
                    }
                else:
                    payload = {"type": "progress", "done": done, "total": total}
                yield f"data: {_json.dumps(payload)}\n\n"
        yield f"data: {_json.dumps({'type': 'done', 'total': total})}\n\n"

    return Response(
        stream_with_context(generate()),
        content_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


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


# ── Popular / Latest ─────────────────────────────────────────────────────────

@sources_bp.route("/popular", methods=["GET"])
def popular():
    """Fetch popular or latest manga from a Suwayomi source."""
    source_id = (request.args.get("source") or "").strip()
    page      = max(1, int(request.args.get("page", 1)))
    kind      = request.args.get("type", "POPULAR").upper()   # POPULAR | LATEST
    if kind not in ("POPULAR", "LATEST"):
        kind = "POPULAR"
    if not source_id:
        return jsonify({"error": "source required"}), 400
    try:
        data = _gql(
            """
            mutation FetchPopular($source: LongString!, $type: FetchSourceMangaType!, $page: Int!) {
              fetchSourceManga(input: { source: $source, type: $type, page: $page }) {
                mangas { id title thumbnailUrl inLibrary }
                hasNextPage
              }
            }
            """,
            {"source": source_id, "type": kind, "page": page},
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


@sources_bp.route("/enrich_library", methods=["POST"])
def enrich_library():
    """Scan all manga folders, resolve sourceId → sourceName/sourceLang from Suwayomi, update source_meta.json."""
    try:
        src_data = _gql("{ sources { nodes { id name lang } } }")
        sources_map = {n["id"]: n for n in src_data["sources"]["nodes"]}
    except Exception as e:
        return jsonify({"error": str(e), "offline": not _suwayomi_online()}), 503

    enriched = []
    for meta_path in Path(MANGA_DIR).glob("*/.source_meta.json"):
        try:
            meta = _json.loads(meta_path.read_text())
            sid = str(meta.get("sourceId", ""))
            if sid and sid in sources_map:
                src = sources_map[sid]
                if meta.get("sourceName") != src["name"] or meta.get("sourceLang") != src["lang"]:
                    meta["sourceName"] = src["name"]
                    meta["sourceLang"] = src["lang"]
                    meta_path.write_text(_json.dumps(meta))
                    enriched.append(meta_path.parent.name)
        except Exception:
            pass

    return jsonify({"enriched": enriched, "count": len(enriched)})


@sources_bp.route("/save_to_library", methods=["POST"])
def save_to_library():
    """Create a manga folder + write .source_meta.json so it shows as a Mihon entry in the library.

    If onlyIfExists=true is sent, only writes the meta file when the folder already
    exists (used to silently tag old Mihon downloads when the user opens them from
    the sources view).
    """
    data = request.get_json(silent=True) or {}
    title = (data.get("title") or "").strip()
    source_id = data.get("sourceId")
    manga_id = data.get("mangaId")
    thumbnail_url = (data.get("thumbnailUrl") or "").strip() or None
    source_name = (data.get("sourceName") or "").strip() or None
    source_lang = (data.get("sourceLang") or "").strip() or None
    only_if_exists = data.get("onlyIfExists", False)

    if not title or not source_id or not manga_id:
        return jsonify({"error": "title, sourceId, mangaId required"}), 400

    folder = Path(MANGA_DIR) / title
    if only_if_exists and not folder.exists():
        return jsonify({"status": "skipped", "reason": "folder does not exist"})

    folder.mkdir(parents=True, exist_ok=True)

    meta_path = folder / ".source_meta.json"
    # Read existing meta to preserve fields we're not overwriting
    existing = {}
    if meta_path.exists():
        try:
            existing = _json.loads(meta_path.read_text())
        except Exception:
            pass

    meta = {"sourceId": str(source_id), "mangaId": int(manga_id), "title": title}
    if thumbnail_url:
        meta["thumbnailUrl"] = thumbnail_url
    elif existing.get("thumbnailUrl"):
        meta["thumbnailUrl"] = existing["thumbnailUrl"]
    if source_name:
        meta["sourceName"] = source_name
    elif existing.get("sourceName"):
        meta["sourceName"] = existing["sourceName"]
    if source_lang:
        meta["sourceLang"] = source_lang
    elif existing.get("sourceLang"):
        meta["sourceLang"] = existing["sourceLang"]

    meta_path.write_text(_json.dumps(meta))

    return jsonify({"status": "ok", "title": title})
