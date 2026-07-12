#!/usr/bin/env python3
"""
Sources API — proxy Flask → Suwayomi GraphQL.
Suwayomi exposes Tachiyomi/Mihon extensions via GraphQL at localhost:4567.
"""

from flask import Blueprint, jsonify, request, Response, stream_with_context
from pathlib import Path
import json as _json
from api.resilient_http import http as http_requests  # retry + backoff + per-host rate limiting

from api.runtime import manga_dir, cache_get, cache_set

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
    # Socket puro a propósito: pasar por http_requests actualizaría last_use y el
    # propio healthcheck impediría que el reaper de inactividad apague la JVM.
    import socket
    try:
        with socket.create_connection(("127.0.0.1", 4567), timeout=2):
            return True
    except OSError:
        return False


# ── Ciclo de vida on-demand ───────────────────────────────────────────────────
# La JVM de Suwayomi (~300-500 MB) solo corre cuando hace falta: cualquier uso
# real la arranca (ensure_suwayomi) y un reaper la apaga tras N min sin tráfico.
# El "uso" se mide en resilient_http.last_use — TODO el tráfico a :4567
# (GraphQL, páginas de capítulo, thumbnails, descargas) pasa por ese cliente.

import os as _os
import time as _time
import threading as _threading
import subprocess as _subprocess

_SUWAYOMI_SCRIPTS = Path(__file__).resolve().parents[2] / "suwayomi"
_IDLE_SECS = max(1, int(_os.environ.get("SUWAYOMI_IDLE_MIN", "15"))) * 60
_EAGER = _os.environ.get("SUWAYOMI_EAGER") == "1"
_start_lock = _threading.Lock()
_reaper_baseline = _time.monotonic()  # "último uso" implícito al arrancar Flask


def _last_use() -> float:
    lu = http_requests.last_use
    return max(lu.get("localhost:4567", 0.0), lu.get("127.0.0.1:4567", 0.0),
               _reaper_baseline)


def ensure_suwayomi(timeout: float = 45.0) -> bool:
    """Garantiza que Suwayomi responda: si no está arriba, lanza start.sh y espera
    readiness (la JVM + KCEF tardan ~10-20 s). Devuelve False si no arrancó."""
    if _suwayomi_online():
        return True
    with _start_lock:
        if _suwayomi_online():
            return True
        script = _SUWAYOMI_SCRIPTS / "start.sh"
        if not script.exists():
            return False
        print("[suwayomi] arranque bajo demanda…", flush=True)
        try:
            _subprocess.Popen(["bash", str(script)],
                              stdout=_subprocess.DEVNULL, stderr=_subprocess.DEVNULL)
        except Exception as e:
            print(f"[suwayomi] start falló: {e}", flush=True)
            return False
        deadline = _time.monotonic() + timeout
        while _time.monotonic() < deadline:
            _time.sleep(1.5)
            if _suwayomi_online():
                print("[suwayomi] listo", flush=True)
                # marca de uso: que el reaper no la mate recién nacida
                http_requests.last_use["localhost:4567"] = _time.monotonic()
                return True
        print("[suwayomi] timeout esperando readiness", flush=True)
        return False


def stop_suwayomi():
    script = _SUWAYOMI_SCRIPTS / "stop.sh"
    if script.exists():
        try:
            _subprocess.run(["bash", str(script)], timeout=15,
                            stdout=_subprocess.DEVNULL, stderr=_subprocess.DEVNULL)
        except Exception as e:
            print(f"[suwayomi] stop falló: {e}", flush=True)


def _idle_reaper():
    while True:
        _time.sleep(60)
        try:
            if _EAGER or not _suwayomi_online():
                continue
            idle = _time.monotonic() - _last_use()
            if idle >= _IDLE_SECS:
                print(f"[suwayomi] {idle/60:.0f} min sin uso — apagando JVM", flush=True)
                stop_suwayomi()
        except Exception:
            pass


_threading.Thread(target=_idle_reaper, daemon=True, name="suwayomi-reaper").start()


@sources_bp.before_request
def _wake_suwayomi():
    # /health solo informa (el frontend sondea estado); todo lo demás despierta la JVM.
    if request.endpoint and request.endpoint.endswith(".health"):
        return None
    if not ensure_suwayomi():
        return jsonify({"error": "Suwayomi no disponible (no arrancó)"}), 503
    return None


# ── Health ────────────────────────────────────────────────────────────────────

@sources_bp.route("/health", methods=["GET"])
def health():
    online = _suwayomi_online()
    # ?wake=1 (vista Fuentes): dispara el arranque en background y responde ya;
    # el polling del frontend (6×5 s) recoge el online cuando la JVM esté lista.
    if not online and request.args.get("wake") == "1":
        _threading.Thread(target=ensure_suwayomi, daemon=True).start()
        return jsonify({"online": False, "starting": True, "url": SUWAYOMI_BASE})
    return jsonify({"online": online, "url": SUWAYOMI_BASE})


# ── Sources list ──────────────────────────────────────────────────────────────

# Caché de disco para búsquedas/listados de fuentes: Suwayomi consulta extensiones
# remotas (lento) y repetimos las mismas llamadas al abrir Fuentes / paginar. TTLs
# cortos → coste de RAM/CPU nulo y frescura razonable (inLibrary puede ir unos min
# desfasado). Se invalida `sources_list` al instalar una extensión.
_TTL_LIST = 300
_TTL_SEARCH = 300
_TTL_POPULAR = 600


@sources_bp.route("/list", methods=["GET"])
def list_sources():
    cached = cache_get("sources_list", "all", _TTL_LIST)
    if cached is not None:
        return jsonify(cached)
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
        # NO cachear una lista vacía: Suwayomi responde por HTTP (health "online")
        # antes de terminar de cargar sus extensiones, así que un `list` disparado
        # justo tras arrancar (p.ej. el location.reload() al alternar biblioteca
        # oculta) puede devolver 0 fuentes. Si lo cacheáramos, quedaría pegado en 0
        # durante _TTL_LIST (5 min) para TODOS los modos —cache no namespaceada por
        # modo—, incluido al volver a la biblioteca normal. Cachear solo si hay datos.
        if sources:
            cache_set("sources_list", "all", sources, _TTL_LIST)
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

    cached = cache_get("sources_search_all", query.lower(), _TTL_SEARCH)
    if cached is not None:
        return jsonify(cached)

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
    # Cachear solo si al menos una fuente respondió (no fijar un fallo transitorio).
    if any(g["results"] for g in groups):
        cache_set("sources_search_all", query.lower(), groups, _TTL_SEARCH)
    return jsonify(groups)


_GQL_STREAM_TIMEOUT = 5  # per-source timeout for streaming search


@sources_bp.route("/search_all_stream", methods=["GET"])
def search_all_stream():
    """Stream global search results as SSE — results arrive as each source responds."""
    query   = (request.args.get("q") or "").strip()
    lang    = (request.args.get("lang") or "").strip().lower()
    src_ids = request.args.get("sources", "").strip()  # comma-separated source IDs
    kind    = request.args.get("kind", "SEARCH").upper()  # SEARCH | POPULAR
    if not query and kind == "SEARCH":
        return jsonify({"error": "q is required"}), 400

    try:
        src_data = _gql("{ sources { nodes { id name lang } } }")
        source_nodes = [n for n in src_data["sources"]["nodes"] if n["id"] != "0"]
    except Exception as e:
        return jsonify({"error": str(e), "offline": not _suwayomi_online()}), 503

    # Filter by explicit source IDs if provided
    if src_ids:
        ids_set = {s.strip() for s in src_ids.split(",") if s.strip()}
        source_nodes = [s for s in source_nodes if s["id"] in ids_set]

    if lang:
        source_nodes = [s for s in source_nodes if s["lang"].lower() == lang]

    def _search_one(source):
        try:
            if kind == "POPULAR":
                gql_type = "POPULAR"
                variables = {"source": source["id"], "type": gql_type, "page": 1}
                query_str = """mutation FetchPopular($source: LongString!, $type: FetchSourceMangaType!, $page: Int!) {
                  fetchSourceManga(input: { source: $source, type: $type, page: $page }) {
                    mangas { id title thumbnailUrl inLibrary }
                  }
                }"""
            else:
                gql_type = "SEARCH"
                variables = {"source": source["id"], "query": query, "page": 1}
                query_str = """mutation SearchManga($source: LongString!, $query: String, $page: Int!) {
                  fetchSourceManga(input: { source: $source, type: SEARCH, query: $query, page: $page }) {
                    mangas { id title thumbnailUrl inLibrary }
                  }
                }"""
            resp = http_requests.post(
                SUWAYOMI_URL,
                json={"query": query_str, "variables": variables},
                timeout=_GQL_STREAM_TIMEOUT,
                retries=1,  # parallel multi-source search: a slow source must fail fast
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

    ckey = f"{source_id}|{query.lower()}|{page}"
    cached = cache_get("sources_search", ckey, _TTL_SEARCH)
    if cached is not None:
        return jsonify(cached)
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
        payload = {"results": mangas, "hasNextPage": results["hasNextPage"], "page": page}
        cache_set("sources_search", ckey, payload, _TTL_SEARCH)
        return jsonify(payload)
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
    ckey = f"{source_id}|{kind}|{page}"
    cached = cache_get("sources_popular", ckey, _TTL_POPULAR)
    if cached is not None:
        return jsonify(cached)
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
        payload = {"results": mangas, "hasNextPage": results["hasNextPage"], "page": page}
        cache_set("sources_popular", ckey, payload, _TTL_POPULAR)
        return jsonify(payload)
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


# ── Capítulos de fuente: Stale-While-Revalidate ───────────────────────────────
# El cuello de botella con Tachiyomi/Mihon era que `fetchChapters` (mutation) hace un
# SCRAPE EN VIVO de la web de la fuente en CADA apertura, síncrono y bloqueante — mientras
# que Suwayomi ya guarda los capítulos en su DB local (la query es instantánea, como el
# feed de MangaDex). Estrategia SWR: servir la DB al instante y refrescar la fuente en 2º
# plano, throttleado por TTL. Solo la PRIMERA vez (DB vacía) o un `?refresh=1` explícito
# hacen el scrape síncrono.
_TTL_SRC_CHAPTERS = 300          # caché de disco: absorbe reaperturas rápidas (5 min)
_SRC_REFRESH_SECS = 6 * 3600     # refrescar la fuente como mucho 1×/6h por manga
_src_fetched_at: dict = {}       # manga_id → epoch del último scrape completado (en memoria)
_src_refreshing: set = set()     # manga_ids con un refresco en 2º plano en curso (dedup)
_src_lock = _threading.Lock()

_FETCH_CHAPTERS_MUT = """
    mutation FetchChapters($id: Int!) {
      fetchChapters(input: { mangaId: $id }) { chapters { id } }
    }
"""
_GET_CHAPTERS_QUERY = """
    query GetChapters($mangaId: Int!) {
      chapters(condition: { mangaId: $mangaId }, orderBy: CHAPTER_NUMBER, orderByType: DESC) {
        nodes { id name chapterNumber uploadDate scanlator pageCount isRead }
      }
    }
"""


def _query_src_chapters(manga_id):
    return _gql(_GET_CHAPTERS_QUERY, {"mangaId": manga_id})["chapters"]["nodes"]


_SEARCH_SRC_MUT = """
    mutation SearchManga($source: LongString!, $query: String, $page: Int!) {
      fetchSourceManga(input: { source: $source, type: SEARCH, query: $query, page: $page }) {
        mangas { id title }
      }
    }
"""


def _resolve_source_manga_id(source_id: str, title: str):
    """Re-resuelve el mangaId de Suwayomi buscando el título en su fuente. El id guardado en
    `.source_meta.json` puede quedar OBSOLETO si la DB de Suwayomi se reconstruye/purga (los
    ids son secuenciales y se reasignan) → los capítulos dejan de cargar. La búsqueda mete el
    manga de vuelta en la DB de Suwayomi y devuelve su id ACTUAL (match exacto de título si lo
    hay, si no el primer resultado)."""
    try:
        data = _gql(_SEARCH_SRC_MUT, {"source": str(source_id), "query": title, "page": 1})
        mangas = data["fetchSourceManga"]["mangas"]
    except Exception as e:
        print(f"[sources] re-resolución falló ({title!r} en {source_id}): {e}", flush=True)
        return None
    tl = (title or "").strip().lower()
    for m in mangas:
        if (m.get("title") or "").strip().lower() == tl:
            return m["id"]
    return mangas[0]["id"] if mangas else None


def _persist_source_meta_id(old_id: int, new_id: int, title: str = ""):
    """Reescribe el mangaId (y el thumbnail, que lleva el id embebido) en el `.source_meta.json`
    del manga, para que lecturas/descargas/reaperturas usen ya el id nuevo. Busca por el id
    viejo y, si no, por título."""
    try:
        for meta_path in Path(manga_dir()).glob("*/.source_meta.json"):
            try:
                meta = _json.loads(meta_path.read_text())
            except Exception:
                continue
            match = (meta.get("mangaId") == old_id) or (title and meta.get("title") == title)
            if not match:
                continue
            meta["mangaId"] = int(new_id)
            thumb = meta.get("thumbnailUrl") or ""
            if f"/manga/{old_id}/" in thumb:
                meta["thumbnailUrl"] = thumb.replace(f"/manga/{old_id}/", f"/manga/{new_id}/")
            meta_path.write_text(_json.dumps(meta))
            print(f"[sources] mangaId obsoleto {old_id} → {new_id} en {meta_path.parent.name}", flush=True)
            return True
    except Exception as e:
        print(f"[sources] no se pudo persistir el id nuevo: {e}", flush=True)
    return False


def _scrape_src_chapters(manga_id):
    """Refresca desde la web de la fuente (bloqueante). Actualiza la DB de Suwayomi."""
    _gql(_FETCH_CHAPTERS_MUT, {"id": manga_id})
    _src_fetched_at[manga_id] = _time.time()


def _is_src_stale(manga_id) -> bool:
    last = _src_fetched_at.get(manga_id, 0)
    return (_time.time() - last) >= _SRC_REFRESH_SECS


def _bg_refresh_src_chapters(manga_id):
    """Hilo daemon: scrape + re-query + actualiza la caché de disco con lo fresco, para que
    la siguiente lectura (o el re-poll SWR del front) muestre los capítulos nuevos."""
    key = str(manga_id)
    try:
        if not ensure_suwayomi():
            return
        _scrape_src_chapters(manga_id)
        fresh = _query_src_chapters(manga_id)
        cache_set("sources_chapters", key, fresh, _TTL_SRC_CHAPTERS)
    except Exception as e:
        print(f"[sources] refresco 2º plano falló (manga {manga_id}): {e}", flush=True)
    finally:
        with _src_lock:
            _src_refreshing.discard(manga_id)


def _maybe_bg_refresh(manga_id):
    """Lanza el refresco en 2º plano solo si está obsoleto y no hay ya uno en curso."""
    if not _is_src_stale(manga_id):
        return False
    with _src_lock:
        if manga_id in _src_refreshing:
            return True
        _src_refreshing.add(manga_id)
    _threading.Thread(target=_bg_refresh_src_chapters, args=(manga_id,),
                      daemon=True, name=f"src-refresh-{manga_id}").start()
    return True


@sources_bp.route("/manga/<int:manga_id>/chapters", methods=["GET"])
def manga_chapters(manga_id):
    """Lista de capítulos de una fuente. SWR: sirve la DB local al instante y refresca la
    web de la fuente en 2º plano. `?refresh=1` fuerza un scrape síncrono; `?meta=1` devuelve
    `{chapters, stale}` (stale=hay un refresco pendiente → el front puede re-consultar)."""
    force     = request.args.get("refresh") == "1"
    with_meta = request.args.get("meta") == "1"
    source_id = (request.args.get("sourceId") or "").strip()
    title     = (request.args.get("title") or "").strip()
    key = str(manga_id)
    resolved_id = manga_id

    def _respond(chapters, stale):
        if not with_meta:
            return jsonify(chapters)
        body = {"chapters": chapters, "stale": stale}
        if resolved_id != manga_id:
            body["resolvedId"] = resolved_id   # el front actualiza su source_meta
        return jsonify(body)

    def _reresolve():
        """Si el id está obsoleto (DB de Suwayomi reconstruida) y tenemos fuente+título,
        busca el id ACTUAL, persístelo y actualiza la clave de caché. Devuelve True si cambió."""
        nonlocal resolved_id, key
        if not (source_id and title):
            return False
        new_id = _resolve_source_manga_id(source_id, title)
        if not new_id or new_id == manga_id:
            return False
        _persist_source_meta_id(manga_id, new_id, title)
        resolved_id, key = new_id, str(new_id)
        return True

    try:
        # 1) Caché de disco (salvo refresco forzado) → respuesta inmediata.
        if not force:
            cached = cache_get("sources_chapters", key, _TTL_SRC_CHAPTERS)
            if cached is not None:
                _maybe_bg_refresh(manga_id)  # mantén frescura sin bloquear
                return _respond(cached, _is_src_stale(manga_id))

        # 2) Refresco forzado: scrape síncrono (el usuario pidió "buscar nuevos").
        if force:
            try:
                _scrape_src_chapters(resolved_id)
                chapters = _query_src_chapters(resolved_id)
            except Exception:
                chapters = []
            if not chapters and _reresolve():
                _scrape_src_chapters(resolved_id)
                chapters = _query_src_chapters(resolved_id)
            cache_set("sources_chapters", key, chapters, _TTL_SRC_CHAPTERS)
            return _respond(chapters, False)

        # 3) Camino normal: DB primero (instantáneo).
        chapters = _query_src_chapters(resolved_id)
        if not chapters:
            # DB sin capítulos → primer scrape síncrono. Si falla o sigue vacío y el id está
            # obsoleto, re-resuelve por búsqueda del título y reintenta con el id nuevo.
            try:
                _scrape_src_chapters(resolved_id)
                chapters = _query_src_chapters(resolved_id)
            except Exception:
                chapters = []
            if not chapters and _reresolve():
                _scrape_src_chapters(resolved_id)
                chapters = _query_src_chapters(resolved_id)
            stale = False
        else:
            # Hay datos: sírvelos ya y refresca en 2º plano si toca (SWR).
            stale = _maybe_bg_refresh(resolved_id)
        cache_set("sources_chapters", key, chapters, _TTL_SRC_CHAPTERS)
        return _respond(chapters, stale)
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
        # La lista de fuentes cambió → invalida su caché para que aparezca ya.
        cache_set("sources_list", "all", None, 0)
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
    for meta_path in Path(manga_dir()).glob("*/.source_meta.json"):
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

    folder = Path(manga_dir()) / title
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
