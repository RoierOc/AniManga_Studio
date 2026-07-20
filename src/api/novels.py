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
  POST /novel   { pluginId, path }    → { name, cover, summary, author, chapters:[…] }
  POST /chapter { pluginId, path }    → { html, text, words }
"""

from flask import Blueprint, jsonify, request
from pathlib import Path
import os as _os
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
    "en": ["readnovelfull", "allnovel", "novelbin", "novelfull", "boxnovel"],
    "es": ["skynovels", "novelasligera", "tunovelaligera", "yuukitls", "novelyra"],
}


def _curated_ids(langs):
    ids = []
    for lg in langs:
        ids += _CURATED.get(lg, [])
    return list(dict.fromkeys(ids))


# Preferencias de fuentes: qué plugins consultar y en qué idiomas. No son secretos,
# así que van en su propio json (no en config.json, que es el almacén de claves).
from api.runtime import DATA_ROOT  # noqa: E402
import json as _json  # noqa: E402

_PREFS_PATH = Path(DATA_ROOT) / "novels_prefs.json"
_DEFAULT_PREFS = {"langs": ["en", "es"], "pluginIds": []}  # [] = usar la lista curada


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
        _PREFS_PATH.parent.mkdir(parents=True, exist_ok=True)
        _PREFS_PATH.write_text(_json.dumps(p, ensure_ascii=False, indent=2), "utf-8")
    except Exception as e:
        record_error("novels", e, op="write_prefs")
        return jsonify({"error": "no se pudieron guardar las preferencias"}), 500
    return jsonify({**p, "active": _active_ids()})


@novels_bp.route("/find", methods=["POST"])
def find():
    """Busca un título en varios plugins a la vez → versiones para elegir."""
    body = request.get_json(silent=True) or {}
    q = (body.get("title") or body.get("q") or "").strip()
    if not q:
        return jsonify({"error": "title es obligatorio"}), 400
    # Prioridad: lo que pida la llamada → lo que el usuario haya configurado → curado.
    ids = body.get("pluginIds") or _active_ids()
    data, code = _call("/find", payload={"q": q, "pluginIds": ids})
    if code == 200 and isinstance(data, dict):
        # Un plugin que ya no existe en el índice no es un error del usuario: se
        # reporta como fuente no consultable, la búsqueda sigue con las demás.
        data["query"] = q
    return jsonify(data), code


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
