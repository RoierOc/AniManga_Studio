#!/usr/bin/env python3
"""
Sources API — proxy Flask → Suwayomi GraphQL.
Suwayomi exposes Tachiyomi/Mihon extensions via GraphQL at localhost:4567.
"""

from flask import Blueprint, jsonify, request, Response, stream_with_context
from pathlib import Path
import json as _json
from api.resilient_http import http as http_requests  # retry + backoff + per-host rate limiting

from api.runtime import manga_dir, cache_get, cache_set, cache_invalidate, write_json_atomic
from api.roots import glob_series, series_dir, series_up_dir  # resuelven el DISCO de la obra
from api.observability import record_error

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


def _fetch_sources():
    """Lista viva de fuentes desde Suwayomi (sin caché). Lanza si la JVM no contesta."""
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
    # Filter out the local source (id "0") — not useful for search
    return [
        {
            "id": n["id"],
            "name": n["name"],
            "lang": n["lang"],
            "iconUrl": SUWAYOMI_BASE + n["iconUrl"] if n.get("iconUrl") else None,
            "isNsfw": n.get("isNsfw", False),
            "supportsLatest": n.get("supportsLatest", False),
        }
        for n in data["sources"]["nodes"]
        if n["id"] != "0"
    ]


@sources_bp.route("/list", methods=["GET"])
def list_sources():
    cached = cache_get("sources_list", "all", _TTL_LIST)
    if cached is not None:
        return jsonify(cached)
    try:
        sources = _fetch_sources()
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


@sources_bp.route("/reload", methods=["POST"])
def reload_sources():
    """Recarga la lista de fuentes. Con `{"restart": true}` reinicia antes la JVM.

    Motivo: una extensión que se atasca (reto de Cloudflare, timeout, extensión rota) deja el
    listado y las búsquedas tocados, y las cachés de disco (5-10 min) fijan ese mal estado. Hasta
    ahora la única salida era apagar y encender el servidor ENTERO. Dos niveles:
      · suave  — tira las cachés de fuentes y vuelve a preguntar (arregla el estado pegado);
      · duro   — además reinicia Suwayomi (arregla la JVM en sí), sin tocar la app.

    Devuelve SIEMPRE si se reinició y cuántas fuentes hay: 0 fuentes con `restarted` es un
    diagnóstico distinto de un fallo de red, y quien llama necesita distinguirlos.
    """
    body = request.get_json(silent=True) or {}
    restart = bool(body.get("restart"))

    for ns in ("sources_list", "sources_search_all", "sources_search", "sources_popular"):
        cache_invalidate(ns)

    if restart:
        stop_suwayomi()
        if not ensure_suwayomi(timeout=90):
            return jsonify({"error": "Suwayomi no volvió a arrancar", "restarted": True,
                            "online": False, "sources": 0}), 503

    try:
        sources = _fetch_sources()
    except Exception as e:
        return jsonify({"error": str(e), "restarted": restart,
                        "online": _suwayomi_online(), "sources": 0}), 503

    # A propósito NO se cachea el resultado: MEDIDO en vivo, una recarga suave justo después de
    # arrancar devolvió 301 fuentes y el reinicio duro 558 — la JVM responde antes de terminar de
    # cargar sus extensiones. Cachear ese listado parcial lo dejaría pegado 5 min, que es
    # exactamente el problema del que esto es la salida. La caché la vuelve a llenar `/list`.
    return jsonify({"sources": len(sources), "restarted": restart, "online": True,
                    "list": sources})


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


def _first_gql_error(data) -> str:
    """Primer mensaje de un payload GraphQL con errores (para no perder la causa)."""
    try:
        return str(data["errors"][0].get("message") or "GraphQL error")
    except Exception:
        return "GraphQL error"


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
                # "falló" y "no había" NO pueden ser el mismo valor: una fuente caída devolvía
                # [] igual que una que simplemente no tiene ese título, y la UI se callaba.
                return {"source": source, "results": [], "error": _first_gql_error(data)}
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
        except Exception as e:
            return {"source": source, "results": [], "error": str(e) or e.__class__.__name__}

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
                    # Sin resultados: se distingue el fallo (la fuente reventó o dio timeout) del
                    # vacío legítimo, para que la vista pueda decir «3 de 15 no respondieron».
                    payload = {"type": "progress", "done": done, "total": total}
                    if result.get("error"):
                        payload["failed"] = {"source": result["source"], "error": result["error"]}
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
        mangas { id title url }
      }
    }
"""
_MANGA_DB_Q = """
    query($id: Int!) { manga(id: $id) { title url source { id name } } }
"""


def _canon_title(s: str) -> str:
    """Normaliza un título a solo alfanuméricos en minúscula (para casar sin puntuación/idioma)."""
    return "".join(ch for ch in (s or "").lower() if ch.isalnum())


def _titles_match(a: str, b: str) -> bool:
    """¿Son el MISMO título? Canon con contención en ambos sentidos (tolera sufijos tipo
    '(Pre-serialization)' o el nombre de la fuente añadido) pero exige solape sustancial."""
    ca, cb = _canon_title(a), _canon_title(b)
    if not ca or not cb:
        return False
    return ca == cb or (len(ca) >= 6 and len(cb) >= 6 and (ca in cb or cb in ca))


def _source_manga_db(manga_id):
    """Título/url/fuente ACTUALES de un mangaId, leídos de la DB de Suwayomi (query, SIN scrape
    remoto → instantáneo). Sirve para detectar que un id guardado DERIVÓ a otra obra (los ids
    de Suwayomi se reasignan al reconstruir su DB). Devuelve {} si no se puede consultar (≠ 'no
    existe': un fallo de red NO debe leerse como cruce)."""
    try:
        m = _gql(_MANGA_DB_Q, {"id": int(manga_id)}).get("manga") or {}
        return {"title": m.get("title") or "", "url": m.get("url") or "",
                "sourceId": (m.get("source") or {}).get("id"),
                "sourceName": (m.get("source") or {}).get("name")}
    except Exception as e:
        record_error("sources", e, op="manga_db", manga_id=manga_id)
        return {}


def _resolve_source_manga_id(source_id: str, title: str, url: str = "", strict: bool = True,
                             variants: list = None):
    """Re-resuelve el mangaId ACTUAL buscando el título en su fuente. El id guardado puede quedar
    OBSOLETO o DERIVAR a otra obra si la DB de Suwayomi se reconstruye (ids reasignados). La
    búsqueda re-inserta el manga en la DB y devuelve su id actual. Anclaje por prioridad:
      1) `url` de la fuente (ESTABLE — el slug no cambia entre reconstrucciones), 2) título canónico
      (contra `title` y, si se dan, sus `variants` romaji/inglés/nativo/sinónimos).
    `strict=True` (corrigiendo un CRUCE): si no hay match fiable devuelve id None — un primer-resultado
    a ciegas re-cruzaría (peor que fallar). `strict=False` (id vacío/purgado, re-alta): permite caer
    al primer resultado. Devuelve (id|None, matched_url, status) con status ∈ {'ok','none','failed'}:
    'failed' = la BÚSQUEDA reventó (fuente caída ≠ 'no está', regla falló≠no-había)."""
    try:
        data = _gql(_SEARCH_SRC_MUT, {"source": str(source_id), "query": title, "page": 1})
        mangas = data["fetchSourceManga"]["mangas"]
    except Exception as e:
        record_error("sources", e, op="reresolve", title=title, source_id=source_id)
        return (None, "", "failed")
    want_url = (url or "").strip()
    if want_url:
        for m in mangas:
            if (m.get("url") or "").strip() == want_url:
                return (m["id"], want_url, "ok")
    names = [title] + list(variants or [])
    for m in mangas:                                   # match de título canónico (título o variantes)
        if any(_titles_match(m.get("title"), n) for n in names):
            return (m["id"], m.get("url") or "", "ok")
    if strict:
        return (None, "", "none")                      # sin match fiable: no re-cruzar a ciegas
    return (mangas[0]["id"], mangas[0].get("url") or "", "ok") if mangas else (None, "", "none")


def reresolve_manga_id(source_id: str, title: str, url: str = "", current_id=None):
    """Devuelve el mangaId ACTUAL de una obra cuyo id numérico pudo DERIVAR, y lo persiste.

    El id numérico de Suwayomi es efímero: si su DB se reconstruye, el id guardado (en historial,
    progreso o pin) apunta a nada — `_chapters_map` sale vacío, indistinguible de "sin páginas".
    Re-resuelve por url (ancla estable) o título, persiste en `.source_meta.json` y lo devuelve.
    `int | None` (None si no hay match fiable o la búsqueda falló). Ver [[project_source_id_drift]].
    Reúne lo que el endpoint de capítulos hacía inline, para que TODO re-resolvedor (trasplante,
    continuar-leyendo) pase por la misma costura en vez de reimplementarla."""
    if not (source_id and title):
        return None
    new_id, new_url, _st = _resolve_source_manga_id(str(source_id), title, url, strict=True)
    if not new_id:
        return None
    try:
        if current_id is not None and int(new_id) != int(current_id):
            _persist_source_meta_id(int(current_id), int(new_id), title, new_url)
    except (TypeError, ValueError):
        pass
    return int(new_id)


def _persist_source_meta_id(old_id: int, new_id: int, title: str = "", new_url: str = ""):
    """Reescribe el mangaId derivado en el `.source_meta.json`: TANTO el `mangaId` top-level
    (origen) COMO el `recommended_source.mangaId` (la versión FIJADA) — el cruce de Ao no Hako
    vivía en el pin, y persistir solo el top-level lo dejaba roto. Guarda además la `url` estable
    como ancla para futuras re-resoluciones. Busca la obra por cualquier id que case (top-level o
    pin) y, si no, por título."""
    try:
        for meta_path in glob_series("*/.source_meta.json"):
            try:
                meta = _json.loads(meta_path.read_text())
            except Exception as e:
                record_error("sources", e, op="persist_id_read", path=str(meta_path))
                continue
            rec = meta.get("recommended_source") or {}
            hit_top = meta.get("mangaId") == old_id
            hit_pin = rec.get("mangaId") == old_id
            if not (hit_top or hit_pin or (title and meta.get("title") == title)):
                continue
            if hit_top or (title and meta.get("title") == title and not hit_pin):
                meta["mangaId"] = int(new_id)
                thumb = meta.get("thumbnailUrl") or ""
                if f"/manga/{old_id}/" in thumb:
                    meta["thumbnailUrl"] = thumb.replace(f"/manga/{old_id}/", f"/manga/{new_id}/")
                if new_url:
                    meta["mangaUrl"] = new_url
            if hit_pin:
                rec["mangaId"] = int(new_id)
                if new_url:
                    rec["url"] = new_url
                meta["recommended_source"] = rec
            write_json_atomic(meta_path, meta)
            print(f"[sources] mangaId derivado {old_id} → {new_id} en {meta_path.parent.name}"
                  f" ({'pin' if hit_pin else 'origen'})", flush=True)
            return True
    except Exception as e:
        record_error("sources", e, op="persist_id", old_id=old_id, new_id=new_id)
    return False
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
    url       = (request.args.get("url") or "").strip()   # ancla estable (slug de la fuente)
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
        """Busca el id ACTUAL en la fuente (ancla url→título), lo persiste (top-level Y pin) y
        actualiza la clave de caché. `strict`: no re-cruza a ciegas. Devuelve True si cambió."""
        nonlocal resolved_id, key
        if not (source_id and title):
            return False
        new_id, new_url, _st = _resolve_source_manga_id(source_id, title, url, strict=True)
        if not new_id or new_id == manga_id:
            return False
        _persist_source_meta_id(manga_id, new_id, title, new_url)
        resolved_id, key = new_id, str(new_id)
        return True

    # GUARD DE CRUCE: un id DERIVADO (Suwayomi reasignó su fila a otra obra) devuelve capítulos
    # —pero de la obra equivocada—, así que el viejo re-resolver "solo si 0 capítulos" no lo veía.
    # Antes de servir NADA, si tenemos fuente+título comprobamos (lectura de DB, ~ms) que el id
    # sigue siendo ESTA obra; si el título no casa → re-resolvemos al id correcto. Best-effort: un
    # fallo de consulta NO se trata como cruce ("falló ≠ no había").
    if source_id and title:
        db = _source_manga_db(manga_id)
        if db.get("title") and not _titles_match(db["title"], title):
            if _reresolve():
                cache_set("sources_chapters", str(manga_id), None, 1)  # invalida la caché del id viejo

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


@sources_bp.route("/verify_source_refs", methods=["POST"])
def verify_source_refs():
    """Barrido: verifica/repara los mangaId de fuente (origen y pin) de toda la biblioteca cuando
    han DERIVADO a otra obra (Suwayomi reasigna ids al reconstruir su DB). `?fix=0` solo informa."""
    from api.source_identity import verify_source_refs as _sweep
    fix = (request.args.get("fix") or "1") != "0"
    report = _sweep(fix=fix)
    crossed = [r for r in report if r.get("status", "").startswith("cruzado")]
    fixed = [r for r in report if r.get("fixed")]
    return jsonify({"ok": True, "total": len(report), "crossed": len(crossed),
                    "fixed": len(fixed), "report": report})


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
    for meta_path in glob_series("*/.source_meta.json"):
        try:
            meta = _json.loads(meta_path.read_text())
            sid = str(meta.get("sourceId", ""))
            if sid and sid in sources_map:
                src = sources_map[sid]
                if meta.get("sourceName") != src["name"] or meta.get("sourceLang") != src["lang"]:
                    meta["sourceName"] = src["name"]
                    meta["sourceLang"] = src["lang"]
                    write_json_atomic(meta_path, meta)
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

    folder = series_dir(title)
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

    write_json_atomic(meta_path, meta)

    return jsonify({"status": "ok", "title": title})
