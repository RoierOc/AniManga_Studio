#!/usr/bin/env python3
"""
Novels API — proxy Flask → sidecar de novelas (Node, plugins LNReader).

Mismo patrón que `sources.py` → Suwayomi: el sidecar es un servicio local
(127.0.0.1:4568) que se arranca BAJO DEMANDA y se apaga solo tras un rato sin
uso. Aquí solo se traduce HTTP→HTTP y se normaliza la forma de los datos para
el frontend; la lógica de scraping vive en los plugins (ver novels/sidecar).

Endpoints (prefijo /api/novels):
  GET  /status                        → { online, plugins }
  GET  /plugins?lang=English          → [{ id, name, lang, site, iconUrl }]
  GET  /search?pluginId=&q=&page=     → [{ name, path, cover }]
  GET  /popular?pluginId=&page=       → idem
  GET  /progress                     → progreso durable por novela
  POST /progress                     → fusiona progreso enviado por un cliente
  POST /novel   { pluginId, path }    → { name, cover, summary, author, chapters:[…] }
  POST /chapter { pluginId, path }    → { html, text, words }
"""

from flask import Blueprint, jsonify, request
from pathlib import Path
import os as _os
import re
import time as _time
import threading as _threading
import subprocess as _subprocess
import socket as _socket

from api.resilient_http import http as http_requests
from api.observability import record_error

novels_bp = Blueprint("novels", __name__)

NOVELS_PORT = int(_os.environ.get("NOVELS_PORT", "4568"))
NOVELS_BASE = f"http://127.0.0.1:{NOVELS_PORT}"
_TIMEOUT = 40  # scraping real: algunos sitios tardan

_SCRIPTS = Path(__file__).resolve().parents[2] / "novels"
_IDLE_SECS = max(1, int(_os.environ.get("NOVELS_IDLE_MIN", "20"))) * 60
_start_lock = _threading.Lock()
_last_use = _time.monotonic()


# ── Ciclo de vida on-demand ───────────────────────────────────────────────────

def _online() -> bool:
    # Socket puro: un healthcheck no debe contar como "uso" (si no, el reaper
    # nunca apagaría el sidecar). Mismo razonamiento que en sources.py.
    try:
        with _socket.create_connection(("127.0.0.1", NOVELS_PORT), timeout=2):
            return True
    except OSError:
        return False


def ensure_novels(timeout: float = 25.0) -> bool:
    """Arranca el sidecar si hace falta y espera readiness. Node arranca rápido
    (~1 s), pero la primera vez puede tener que hacer `pnpm install`."""
    if _online():
        return True
    with _start_lock:
        if _online():
            return True
        script = _SCRIPTS / "start.sh"
        if not script.exists():
            return False
        print("[novels] arranque bajo demanda…", flush=True)
        try:
            _subprocess.Popen(["bash", str(script)],
                              stdout=_subprocess.DEVNULL, stderr=_subprocess.DEVNULL)
        except Exception as e:
            record_error("novels", e, phase="start")
            return False
        deadline = _time.monotonic() + timeout
        while _time.monotonic() < deadline:
            _time.sleep(1.0)
            if _online():
                print("[novels] listo", flush=True)
                return True
        print("[novels] timeout esperando readiness", flush=True)
        return False


def stop_novels():
    script = _SCRIPTS / "stop.sh"
    if script.exists():
        try:
            _subprocess.run(["bash", str(script)], timeout=10,
                            stdout=_subprocess.DEVNULL, stderr=_subprocess.DEVNULL)
        except Exception as e:
            record_error("novels", e, phase="stop")


def _idle_reaper():
    while True:
        _time.sleep(60)
        try:
            if _online() and (_time.monotonic() - _last_use) > _IDLE_SECS:
                print("[novels] inactivo → apagando sidecar", flush=True)
                stop_novels()
        except Exception as e:
            record_error("novels", e, phase="reaper")


_threading.Thread(target=_idle_reaper, daemon=True).start()


# ── Proxy ─────────────────────────────────────────────────────────────────────

def _call(path: str, params=None, payload=None):
    """Llama al sidecar garantizando que esté arriba. Devuelve (json, status)."""
    global _last_use
    if not ensure_novels():
        return {"error": "sidecar de novelas no disponible"}, 503
    _last_use = _time.monotonic()
    url = f"{NOVELS_BASE}{path}"
    try:
        if payload is None:
            r = http_requests.get(url, params=params or {}, timeout=_TIMEOUT)
        else:
            r = http_requests.post(url, json=payload, timeout=_TIMEOUT)
        return r.json(), r.status_code
    except Exception as e:
        record_error("novels", e, path=path, params=params, payload=payload)
        return {"error": str(e)}, 502


@novels_bp.route("/status")
def status():
    online = _online()
    n = 0
    if online:
        data, code = _call("/plugins")
        n = len(data) if code == 200 and isinstance(data, list) else 0
    return jsonify({"online": online, "plugins": n, "port": NOVELS_PORT})


@novels_bp.route("/plugins")
def plugins():
    data, code = _call("/plugins", {"lang": request.args.get("lang", "")})
    return jsonify(data), code


@novels_bp.route("/search")
def search():
    pid = request.args.get("pluginId", "")
    q = request.args.get("q", "").strip()
    if not pid or not q:
        return jsonify({"error": "pluginId y q son obligatorios"}), 400
    data, code = _call("/search", {"pluginId": pid, "q": q,
                                   "page": request.args.get("page", 1)})
    return jsonify(data), code


@novels_bp.route("/popular")
def popular():
    pid = request.args.get("pluginId", "")
    if not pid:
        return jsonify({"error": "pluginId es obligatorio"}), 400
    data, code = _call("/popular", {"pluginId": pid,
                                    "page": request.args.get("page", 1)})
    return jsonify(data), code


@novels_bp.route("/browse")
def browse():
    """Navegar el catálogo completo de una fuente nativa (hoy SkyNovels): descubrimiento ES
    fiable con rating/estado, ordenable, paginado. Ver novels/sidecar/skynovels.cjs."""
    pid = request.args.get("pluginId", "")
    if not pid:
        return jsonify({"error": "pluginId es obligatorio"}), 400
    data, code = _call("/browse", {"pluginId": pid,
                                   "page": request.args.get("page", 1),
                                   "sort": request.args.get("sort", "views")})
    return jsonify(data), code


# ── "Buscar para leer" (fan-out por título) ───────────────────────────────────
# 258 plugins es demasiado para consultar en cada búsqueda. Se consulta un puñado
# CURADO por idioma (catálogos grandes y estables); el resto sigue disponible
# eligiendo el plugin a mano desde /plugins. Mismo espíritu que source_health en
# manga: acotar el fan-out a lo que suele acertar.
# ids VERIFICADOS contra el índice real (ojo: `lightnovelpub`/`freewebnovel` no existen
# como plugin). Los que hoy dan 403/captcha (novelbin, novelfull, boxnovel) se dejan a
# propósito: son catálogos grandes y con FlareSolverr configurado sí responden; si fallan,
# `find` los reporta como fuente caída y sigue con las demás.
_CURATED = {
    # Medido con "Shadow Slave" sobre los 137 plugins EN del índice (86 respondieron, 17 la
    # tenían): estas son las de catálogo más COMPLETO. libread/novelarrow/novelbuddy daban
    # 3110 capítulos, readnovelfull/allnovel ~3098, y otras se quedaban en 1736 o en 25.
    "en": ["libread", "novelarrow", "novelbuddy", "anf.net",
           "readnovelfull", "allnovel", "novelcool", "novelbin", "novelfull", "boxnovel"],
    # LNReader solo tiene 16 plugins en español (frente a 137 en inglés): ese es el techo del
    # ecosistema, no una elección nuestra. De esos 16, responden 9 — se consultan TODOS los que
    # responden, porque el fan-out es paralelo y con timeout, así que sobran pocos motivos para
    # dejar fuera ninguno. De los 7 que fallan: 3 tienen el dominio MUERTO (lightnoveldaily,
    # TCSega, panchotranslations: el DNS ni resuelve) y el resto pide captcha/FlareSolverr.
    "es": ["skynovels", "tunovelaligera", "yuukitls", "novelyra", "novelasligera",
           "HasuTL", "oasistranslations", "reinowuxia", "traducciones", "allnovelread"],
}


def _curated_ids(langs):
    ids = []
    for lg in langs:
        ids += _CURATED.get(lg, [])
    return list(dict.fromkeys(ids))


# Preferencias de fuentes: qué plugins consultar y en qué idiomas. No son secretos,
# así que van en su propio json (no en config.json, que es el almacén de claves).
from api.runtime import DATA_ROOT, read_json_safe, write_json_atomic  # noqa: E402
import json as _json  # noqa: E402

_PREFS_PATH = Path(DATA_ROOT) / "novels_prefs.json"
_PROGRESS_PATH = Path(DATA_ROOT) / "novel_progress.json"
_DEFAULT_PREFS = {"langs": ["en", "es"], "pluginIds": []}  # [] = usar la lista curada


def _progress_read() -> dict:
    data = read_json_safe(_PROGRESS_PATH, default={}, component='novels')
    return data if isinstance(data, dict) else {}


def _progress_at(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _clean_progress(value):
    if not isinstance(value, dict):
        return None
    fields = ('title', 'pluginId', 'path', 'chapterIndex', 'chapterName', 'scroll', 'total', 'at')
    return {field: value[field] for field in fields if field in value}


def merge_progress(base: dict, incoming: dict) -> dict:
    """Funde el último punto de lectura de cada novela sin pisar avances más recientes."""
    out = {}
    for novel_id, value in (base or {}).items():
        clean = _clean_progress(value)
        if clean is not None:
            out[str(novel_id)] = clean
    for novel_id, value in (incoming or {}).items():
        clean = _clean_progress(value)
        if clean is None:
            continue
        key = str(novel_id)
        if key not in out or _progress_at(clean.get('at')) >= _progress_at(out[key].get('at')):
            out[key] = clean
    return out


def _read_prefs() -> dict:
    try:
        return {**_DEFAULT_PREFS, **_json.loads(_PREFS_PATH.read_text("utf-8"))}
    except FileNotFoundError:
        return dict(_DEFAULT_PREFS)          # aún no configurado: no es un error
    except Exception as e:
        record_error("novels", e, op="read_prefs", note="prefs ilegibles; se usan las de fábrica")
        return dict(_DEFAULT_PREFS)


def _active_ids() -> list:
    p = _read_prefs()
    return p.get("pluginIds") or _curated_ids(p.get("langs") or ["en", "es"])


@novels_bp.route("/prefs", methods=["GET", "POST"])
def prefs():
    if request.method == "GET":
        p = _read_prefs()
        # `curated` deja ver en la UI cuál es el default sin tener que duplicarlo allí.
        return jsonify({**p, "curated": _CURATED, "active": _active_ids()})
    body = request.get_json(silent=True) or {}
    p = {"langs": body.get("langs") or _DEFAULT_PREFS["langs"],
         "pluginIds": body.get("pluginIds") or []}
    try:
        write_json_atomic(_PREFS_PATH, p, indent=2, keep_backup=True)
    except Exception as e:
        record_error("novels", e, op="write_prefs")
        return jsonify({"error": "no se pudieron guardar las preferencias"}), 500
    return jsonify({**p, "active": _active_ids()})


def _queries_for(title: str, al_id=None) -> list:
    """Títulos a probar. Una novela casi nunca está indexada con el título que
    muestra el meta-source: los sitios usan el romaji ("Kono Subarashii Sekai ni
    Shukufuku wo!") o el inglés oficial, y los ES el suyo propio. Se reusa la
    misma maquinaria de alias que ya alimenta la búsqueda multi-fuente de manga."""
    out = [title]
    try:
        from api import anilist
        for v in anilist.title_variants(title, al_id=al_id) or []:
            # Solo variantes en alfabeto latino: las fuentes que consultamos son EN/ES y
            # no indexan en japonés/tailandés/ruso, así que esas variantes solo gastarían
            # rondas de fan-out (medido: Konosuba probaba 4 títulos, 2 de ellos inútiles).
            if v not in out and _is_latin(v):
                out.append(v)
    except Exception as e:
        record_error("novels", e, op="title_variants", title=title)

    # Título BASE (sin subtítulo). Los buscadores de estos sitios son literales y fallan con
    # títulos largos: "Mushoku Tensei: Jobless Reincarnation" no encuentra nada en allnovel,
    # pero "Mushoku Tensei" (que es como lo indexan) devuelve la versión de 285 capítulos.
    for v in list(out):
        base = re.split(r"[:\-–(]", v, 1)[0].strip()
        if len(base) >= 4 and base not in out:
            out.append(base)
    return out[:6]  # acotado: cada consulta es otra tanda de fan-out (en paralelo)


def _is_latin(s: str) -> bool:
    letters = [c for c in (s or "") if c.isalpha()]
    if not letters:
        return False
    return sum(c.isascii() for c in letters) / len(letters) > 0.6


def _relevant(name: str, queries: list) -> bool:
    """¿Este resultado tiene algo que ver con lo que se buscó?

    Hace falta porque varias fuentes NO devuelven vacío cuando no encuentran nada:
    devuelven novelas al azar (medido: buscar el título japonés de Konosuba en
    readnovelfull devolvía "I Have Medicine", "Radiant Blade of the Wilderness"…).
    Sin este filtro, "no la tienen" se mostraba como diez versiones falsas.
    No vale el comparador de manga (`_titles_match`, contención simple): con títulos
    cortos y genéricos mete ruido — buscando "Overlord" aceptaba "I Am Overlord",
    "Silver Overlord" y "Overlord, Love Me Tender", y enterraba la buena. Aquí se
    exige que uno sea PREFIJO del otro, que es como se comportan de verdad los
    títulos ("Overlord (LN)" ✓, "I Am Overlord" ✗; "Mushoku Tensei" ✓ de
    "Mushoku Tensei: Jobless Reincarnation")."""
    def canon(s):
        return "".join(c for c in (s or "").lower() if c.isalnum())
    a = canon(name)
    if len(a) < 3:
        return False
    for q in queries:
        b = canon(q)
        if len(b) < 3:
            continue
        if a.startswith(b) or b.startswith(a):
            return True
    return False


def _best_per_source(results: list) -> list:
    """Una versión por fuente: la de título más CORTO, que es la entrada canónica
    (con 10 fuentes, dejar 5 coincidencias de cada una daba 23 opciones para elegir
    entre 4 fuentes reales). "Overlord (LN)" gana a "Overlord, Love Me Tender"."""
    best = {}
    for r in results:
        pid = r.get("pluginId")
        if pid not in best or len(r.get("name") or "") < len(best[pid].get("name") or ""):
            best[pid] = r
    return list(best.values())


def _rank(results: list, es_ids: set) -> list:
    """Español primero (es el idioma en el que el usuario quiere leer), y dentro
    de cada grupo se respeta el orden en que respondió cada fuente."""
    return sorted(results, key=lambda r: 0 if r.get("pluginId") in es_ids else 1)


@novels_bp.route("/find", methods=["POST"])
def find():
    """Busca un título en varios plugins a la vez → versiones para elegir.

    Reintenta con variantes del título hasta encontrar algo: buscar solo por el
    título que muestra la ficha fallaba en cuanto el sitio lo indexaba en romaji
    o en español."""
    body = request.get_json(silent=True) or {}
    title = (body.get("title") or body.get("q") or "").strip()
    if not title:
        return jsonify({"error": "title es obligatorio"}), 400
    # Prioridad: lo que pida la llamada → lo que el usuario haya configurado → curado.
    ids = body.get("pluginIds") or _active_ids()
    es_ids = set(_CURATED.get("es", []))

    queries = _queries_for(title, body.get("al_id"))
    data, code = _call("/find", payload={"queries": queries, "pluginIds": ids})
    if code != 200 or not isinstance(data, dict):
        return jsonify(data), code
    # Un plugin cuyo sitio cambió puede devolver objetos VACÍOS ("[{}]", medido en
    # novelasligera): responde OK pero no parsea nada. Sin título o ruta no hay versión.
    results = [r for r in (data.get("results") or [])
               if r.get("name") and r.get("path") and _relevant(r.get("name"), queries)]
    return jsonify({"results": _rank(_best_per_source(results), es_ids), "sources": data.get("sources") or [],
                    "query": title, "tried": queries})


# ── Biblioteca de novelas ─────────────────────────────────────────────────────
# Una novela es una entrada más de local_library.json (kind='novel'), así reusa
# listado, borrado y portadas. Lo específico va en `novel`: de qué plugin y qué
# ruta, que es lo que el lector necesita para traer capítulos.

def _library():
    from api.mangadex import load_local_library
    return load_local_library()


@novels_bp.route("/library")
def library():
    return jsonify([m for m in _library() if m.get("kind") == "novel"])


@novels_bp.route("/progress")
def get_progress():
    return jsonify(_progress_read())


@novels_bp.route("/progress", methods=["POST"])
def put_progress():
    incoming = request.get_json(silent=True)
    if not isinstance(incoming, dict):
        return jsonify({"error": "se esperaba un objeto {novela: progreso}"}), 400
    merged = merge_progress(_progress_read(), incoming)
    write_json_atomic(_PROGRESS_PATH, merged, indent=2, keep_backup=True)
    return jsonify(merged)


@novels_bp.route("/library/add", methods=["POST"])
def library_add():
    body = request.get_json(silent=True) or {}
    title = (body.get("title") or "").strip()
    plugin_id, path = body.get("pluginId"), body.get("path")
    if not (title and plugin_id and path):
        return jsonify({"error": "title, pluginId y path son obligatorios"}), 400

    from api.mangadex import load_local_library, save_local_library
    lib = load_local_library()
    entry_id = f"novel:{plugin_id}:{path}"
    for m in lib:
        if str(m.get("id")) == entry_id:
            return jsonify({"success": True, "already_exists": True})

    lib.append({
        "id": entry_id, "title": title, "kind": "novel",
        "cover": body.get("cover") or None,
        "novel": {"pluginId": plugin_id, "path": path,
                  "site": body.get("site") or "", "lang": body.get("lang") or ""},
        "added_at": _time.strftime("%Y-%m-%d"),
    })
    save_local_library(lib)
    return jsonify({"success": True, "id": entry_id})


@novels_bp.route("/counts", methods=["POST"])
def counts():
    """Nº de capítulos de cada versión, para poder elegir con criterio.

    Sin esto se elige a ciegas: la versión ES de Mushoku Tensei tiene 23 capítulos
    y la EN 285, y ambas se veían igual en la lista."""
    body = request.get_json(silent=True) or {}
    items = [{"pluginId": i.get("pluginId"), "path": i.get("path")}
             for i in (body.get("items") or []) if i.get("pluginId") and i.get("path")]
    if not items:
        return jsonify([])
    data, code = _call("/counts", payload={"items": items})
    return jsonify(data), code


@novels_bp.route("/novel", methods=["POST"])
def novel():
    body = request.get_json(silent=True) or {}
    if not body.get("pluginId") or not body.get("path"):
        return jsonify({"error": "pluginId y path son obligatorios"}), 400
    data, code = _call("/novel", payload={"pluginId": body["pluginId"],
                                          "path": body["path"]})
    return jsonify(data), code


@novels_bp.route("/chapter", methods=["POST"])
def chapter():
    body = request.get_json(silent=True) or {}
    if not body.get("pluginId") or not body.get("path"):
        return jsonify({"error": "pluginId y path son obligatorios"}), 400
    data, code = _call("/chapter", payload={"pluginId": body["pluginId"],
                                            "path": body["path"]})
    # El lector muestra "N palabras / ~M min"; se cuenta aquí una sola vez.
    if code == 200 and isinstance(data, dict):
        words = len((data.get("text") or "").split())
        data["words"] = words
        data["minutes"] = max(1, round(words / 250))
    return jsonify(data), code
