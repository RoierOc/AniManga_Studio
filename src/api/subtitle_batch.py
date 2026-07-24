#!/usr/bin/env python3
"""
Traducción/búsqueda de subtítulos POR LOTES — orquestador sobre el pipeline de UN episodio.

`subtitle.py` ya sabe hacerlo todo para un episodio (detectar pistas, buscar ES premade, traducir
con IA, dejar el sidecar `.spa.ass` sin tocar el MKV). Este módulo NO reimplementa nada de eso:
enumera episodios y aplica esas primitivas en serie. Va aparte porque `subtitle.py` ya pasa de
2000 líneas (regla de documentación técnica: lo nuevo grande, en su módulo).

F1 (este commit): SOLO el escaneo de idiomas — el inventario por episodio que alimenta la tabla de
la UI. El worker serial (start/status/cancel) llega en F2.

Endpoints (prefijo /api/subtitle/batch):
  POST /scan  { items:[{episode, season?, path?, info_hash?, anime_id?, local_path?, titles?[]}] }
              → { episodes:[…], summary:{…} }
"""
import os
import tempfile
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

from flask import Blueprint, jsonify, request

from api.observability import record_error
from api.subtitle import (
    _ffprobe_tracks, is_es_track, _resolve_video_path,
    _ext_find_spanish_subs, _ext_download_sub, _sync_sub_to_reference, _inject_sub,
    _do_translate, _mk_sub_task, _tasks,
)

subbatch_bp = Blueprint("subbatch", __name__)

# Escanear 76 episodios hace 76 ffprobe sobre 9p (abrir el archivo es el coste, no decodificar):
# en paralelo acotado para que el escaneo sea responsivo sin saturar el disco. Cacheado por mtime
# del vídeo — re-escanear tras traducir uno debe ser instantáneo, no repetir los 76.
_SCAN_WORKERS = 6
_scan_cache: dict = {}          # video_path -> (mtime, folder_mtime, dict)
_scan_lock = threading.Lock()


def _resolve_item_path(item: dict):
    """Ruta del vídeo de un item del lote. `path` directo (series/pelis de Sonarr/Radarr) o
    resolución por info_hash/local_path/anime (anime de la biblioteca). None si no se encuentra."""
    p = (item.get("path") or "").strip()
    if p:
        return p if os.path.exists(p) else None
    return _resolve_video_path(
        item.get("info_hash", "") or "",
        int(item.get("episode", 1) or 1),
        item.get("anime_id", "") or "",
        item.get("local_path", "") or "",
    )


def _classify(path: str) -> dict:
    """Estado de subtítulos de UN vídeo, cacheado por mtime.

    - es_status: 'embedded' (pista ES dentro del MKV) | 'sidecar' (nuestro .spa.ass / .es_injected)
                 | 'none'
    - source_langs: idiomas de las pistas de texto NO españolas (candidatas a traducir)
    Distinguir 'embedded' de 'sidecar' importa: el embebido suele ser el oficial del grupo y no se
    debe tocar; el sidecar es nuestro y se puede rehacer.
    """
    try:
        mtime = os.path.getmtime(path)
        # El sidecar vive en la MISMA carpeta: su mtime también entra en la clave, para que
        # crear/borrar un .spa.ass invalide el estado cacheado de este vídeo.
        folder_mtime = os.path.getmtime(os.path.dirname(path))
    except OSError:
        mtime = folder_mtime = 0

    with _scan_lock:
        hit = _scan_cache.get(path)
        if hit and hit[0] == mtime and hit[1] == folder_mtime:
            return hit[2]

    try:
        tracks = _ffprobe_tracks(path)
    except Exception as e:
        record_error("subbatch", e, op="ffprobe", path=os.path.basename(path))
        tracks = []

    from api.anime import _es_sub_injected   # perezoso: evita el ciclo subtitle↔anime
    embedded_es = any(is_es_track(t) for t in tracks)
    if embedded_es:
        es_status = "embedded"
    elif _es_sub_injected(path):
        es_status = "sidecar"
    else:
        es_status = "none"

    source_langs = sorted({(t.get("language") or "und") for t in tracks if not is_es_track(t)})
    res = {
        "es_status": es_status,
        "has_es": es_status != "none",
        "source_langs": source_langs,
        "has_text_tracks": bool(tracks),
    }
    with _scan_lock:
        if len(_scan_cache) > 512:      # cota: es caché de conveniencia
            _scan_cache.clear()
        _scan_cache[path] = (mtime, folder_mtime, res)
    return res


def _scan_one(item: dict) -> dict:
    ep = item.get("episode")
    base = {"episode": ep, "season": item.get("season"), "title": item.get("title") or ""}
    path = _resolve_item_path(item)
    if not path:
        # Sin archivo NO es lo mismo que "sin subtítulos": se dice aparte (regla falló≠no-había).
        return {**base, "file_present": False, "es_status": "none", "has_es": False,
                "source_langs": [], "recommended": "missing_file"}
    info = _classify(path)
    # `recommended` bajo la política por defecto (buscar→traducir): saltar si ya hay ES, procesar
    # el resto. El escaneo NO sale a la red a comprobar premade (sería lentísimo por episodio); esa
    # decisión la toma el worker en F2. Aquí sólo se informa de lo que se ve en local.
    recommended = "skip" if info["has_es"] else "process"
    return {**base, "file_present": True, **info, "recommended": recommended}


@subbatch_bp.route("/scan", methods=["POST"])
def scan():
    """Inventario de subtítulos por episodio para el modal de lote.

    Entrada uniforme para los tres dominios: cada item trae cómo resolver su vídeo (`path` directo
    para series/pelis, o info_hash/local_path/anime_id para anime). El front ya conoce estos datos
    de la lista de episodios que muestra.
    """
    body = request.get_json(silent=True) or {}
    items = body.get("items") or []
    if not isinstance(items, list) or not items:
        return jsonify({"error": "items es obligatorio (lista no vacía)"}), 400
    items = items[:500]     # cota defensiva

    with ThreadPoolExecutor(max_workers=_SCAN_WORKERS) as ex:
        episodes = list(ex.map(_scan_one, items))

    episodes.sort(key=lambda e: (e.get("season") or 0, e.get("episode") or 0))
    summary = {
        "total": len(episodes),
        "with_es": sum(1 for e in episodes if e["has_es"]),
        "to_process": sum(1 for e in episodes if e["recommended"] == "process"),
        "missing_file": sum(1 for e in episodes if not e["file_present"]),
    }
    return jsonify({"episodes": episodes, "summary": summary})


# ── Worker serial ──────────────────────────────────────────────────────────────
# Un episodio a la vez (lo pidió el usuario y lo exige el hardware: un solo GPU para Ollama, y el
# disco 9p no quiere N remux a la vez). El motor IA es SIEMPRE Ollama (Gemini retirado); premade
# primero. Los estados por episodio + el agregado viven aquí; cada traducción IA crea además su
# child task en `_tasks` (tag `batch_id`) → el Centro de Actividad la ve sin código extra.
_batches: dict = {}
_batch_cancel: dict = {}
_batch_lock = threading.Lock()


def _titles_of(item: dict) -> list:
    ts = [t for t in (item.get("titles") or []) if t and t.strip()]
    if ts:
        return ts
    from api.subtitle import _get_anime_titles
    return _get_anime_titles(item.get("anime_id", "") or "")


def _pick_source_track(tracks: list):
    """Mejor pista fuente para traducir: preferir inglés (mejor calidad de traducción), luego la
    primera no-española. None si no hay ninguna de texto."""
    src = [t for t in tracks if not is_es_track(t)]
    if not src:
        return None
    return next((t for t in src if (t.get("language") or "").lower().startswith("en")), src[0])


def _process_episode(batch: dict, item: dict, policy: str, target_lang: str, force: bool) -> dict:
    """Procesa UN episodio y devuelve su resultado {status, method, message}. No lanza."""
    ep = item.get("episode")
    path = _resolve_item_path(item)
    if not path:
        return {"status": "error", "method": None, "message": "archivo no encontrado"}

    tracks = _ffprobe_tracks(path)
    from api.anime import _es_sub_injected
    if not force and (any(is_es_track(t) for t in tracks) or _es_sub_injected(path)):
        return {"status": "skipped", "method": "skip", "message": "ya tiene español"}

    # 1) Premade primero (salvo política solo_ia): humano, gratis, sin GPU.
    if policy != "solo_ia":
        titles = _titles_of(item)
        try:
            found = _ext_find_spanish_subs(titles, int(ep or 1), season=int(item.get("season", 1) or 1))
        except Exception as e:
            record_error("subbatch", e, op="find_premade", episode=ep)
            found = []
        if found:
            try:
                with tempfile.TemporaryDirectory() as td:
                    sub_file = _ext_download_sub(found[0], td)
                    sub_file = _sync_sub_to_reference(sub_file, path, td)
                    _inject_sub(path, sub_file, len([t for t in tracks if not is_es_track(t)]))
                return {"status": "done", "method": "premade",
                        "message": f"premade de {found[0].get('source', 'fuente')}"}
            except Exception as e:
                record_error("subbatch", e, op="inject_premade", episode=ep)
                # cae a IA (si la política lo permite) en vez de fallar
        if policy == "solo_buscar":
            return {"status": "error", "method": None, "message": "sin subtítulo premade"}

    # 2) Traducir con IA (Ollama). Reusa _do_translate SÍNCRONO — el worker YA es serial.
    track = _pick_source_track(tracks)
    if not track:
        return {"status": "error", "method": None, "message": "sin pista de texto que traducir"}
    child_id = uuid.uuid4().hex[:8]
    _mk_sub_task(child_id, item.get("anime_id", "") or "", int(ep or 1), fallback=os.path.basename(path))
    _tasks[child_id]["batch_id"] = batch["batch_id"]
    _do_translate(child_id, path, track["sub_index"], track["codec"],
                  len(tracks), track.get("language") or "eng", force_engine="ollama")
    st = _tasks.get(child_id, {}).get("status")
    if st == "done":
        return {"status": "done", "method": "ia", "message": "traducido con IA"}
    if st == "cancelled":
        return {"status": "cancelled", "method": "ia", "message": "cancelado"}
    return {"status": "error", "method": "ia",
            "message": _tasks.get(child_id, {}).get("message") or "error de traducción"}


def _run_batch(batch_id: str):
    batch = _batches[batch_id]
    policy = batch["policy"]; target_lang = batch["target_lang"]; force = batch["force"]
    try:
        for it in batch["_items"]:
            if _batch_cancel.get(batch_id):
                break
            ep = it.get("episode")
            item_state = next(x for x in batch["items"] if x["episode"] == ep)
            item_state["status"] = "processing"
            batch["current_episode"] = ep
            res = _process_episode(batch, it, policy, target_lang, force)
            item_state.update(res)
            batch["done"] += 1
        batch["status"] = "cancelled" if _batch_cancel.get(batch_id) else "done"
    except Exception as e:
        record_error("subbatch", e, op="run_batch", batch_id=batch_id)
        batch["status"] = "error"
        batch["error"] = str(e)[:200]
    finally:
        batch["current_episode"] = None
        batch["ended_at"] = time.time()


@subbatch_bp.route("/start", methods=["POST"])
def start():
    """Arranca un lote serial. Motor IA = Ollama (fijo). `policy`: buscar_o_traducir (def) /
    solo_buscar / solo_ia. `items` = los episodios elegidos (misma forma que /scan)."""
    body = request.get_json(silent=True) or {}
    items = body.get("items") or []
    if not items:
        return jsonify({"error": "items es obligatorio"}), 400
    policy = body.get("policy") or "buscar_o_traducir"
    if policy not in ("buscar_o_traducir", "solo_buscar", "solo_ia"):
        return jsonify({"error": "policy inválida"}), 400

    with _batch_lock:
        # Un lote a la vez: el worker es serial y comparte GPU/red. Si hay uno vivo, no arrancar otro.
        for b in _batches.values():
            if b["status"] == "running":
                return jsonify({"error": "Ya hay un lote en curso", "batch_id": b["batch_id"]}), 409

    batch_id = uuid.uuid4().hex[:8]
    batch = {
        "batch_id": batch_id, "kind": "subtitle_batch", "status": "running",
        "title": body.get("title") or "Subtítulos",   # para el Centro de Actividad
        "policy": policy, "engine": "ollama", "target_lang": body.get("target_lang") or "spa",
        "force": bool(body.get("force")), "done": 0, "total": len(items), "current_episode": None,
        "started_at": time.time(),
        "items": [{"episode": it.get("episode"), "season": it.get("season"),
                   "title": it.get("title") or "", "status": "pending", "method": None, "message": ""}
                  for it in items],
        "_items": items,
    }
    _batches[batch_id] = batch
    _batch_cancel[batch_id] = False
    threading.Thread(target=_run_batch, args=(batch_id,), daemon=True).start()
    return jsonify({"batch_id": batch_id})


def _batch_public(b: dict) -> dict:
    return {k: v for k, v in b.items() if not k.startswith("_")}


@subbatch_bp.route("/<batch_id>")
def batch_status(batch_id):
    b = _batches.get(batch_id)
    if not b:
        return jsonify({"error": "lote no encontrado"}), 404
    return jsonify(_batch_public(b))


@subbatch_bp.route("/<batch_id>/cancel", methods=["POST"])
def batch_cancel(batch_id):
    b = _batches.get(batch_id)
    if not b:
        return jsonify({"error": "lote no encontrado"}), 404
    _batch_cancel[batch_id] = True
    # `hard`: además cancela la traducción IA del episodio en curso (si la hay).
    if (request.get_json(silent=True) or {}).get("hard"):
        for tid, t in _tasks.items():
            if isinstance(t, dict) and t.get("batch_id") == batch_id and t.get("status") not in (
                    "done", "error", "cancelled"):
                from api.subtitle import _cancel_flags
                _cancel_flags[tid] = True
    return jsonify({"ok": True})


def get_batch_tasks() -> dict:
    """Snapshot de lotes para el Centro de Actividad. Poda los terminados de más de 1 h."""
    now = time.time()
    out, stale = {}, []
    for bid, b in list(_batches.items()):
        ended = b.get("ended_at")
        if ended and (now - ended) > 3600:
            stale.append(bid)
            continue
        out[bid] = _batch_public(b)
    for bid in stale:
        _batches.pop(bid, None)
        _batch_cancel.pop(bid, None)
    return out
