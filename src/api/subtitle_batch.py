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
    _is_signs_track,
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
        item.get("relative_path", "") or "",
        item.get("episode_key", "") or "",
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

# Un lote de 8 episodios son ~30 min (MEDIDO: 3,5-4 min/episodio con Ollama). Todo ese estado vivía
# SÓLO en memoria: reiniciar el servidor a mitad dejaba el modal en 404 y el trabajo ya hecho sin
# rastro, aunque los subtítulos estuvieran en disco. Se persiste tras cada episodio (una escritura
# cada varios minutos, no por línea) para poder contarlo al volver.
_STATE_NAME = 'subtitle_batches.json'


def _state_path():
    from api.runtime import DATA_ROOT
    return os.path.join(DATA_ROOT, _STATE_NAME)


def _save_batches() -> None:
    from api.runtime import write_json_atomic
    try:
        write_json_atomic(_state_path(),
                          {bid: _batch_public(b) for bid, b in _batches.items()},
                          durable=False)
    except Exception as e:                      # persistir es comodidad: nunca tumba el lote
        record_error("subbatch", e, op="save_state")


def load_batches() -> None:
    """Recupera los lotes al arrancar. Uno que estaba 'running' NO puede seguir vivo: el hilo murió
    con el proceso, así que se declara interrumpido en vez de mentir con una barra que no avanza."""
    from api.runtime import read_json_safe
    data = read_json_safe(_state_path(), default={}, component='subbatch')
    if not isinstance(data, dict):
        return
    for bid, b in data.items():
        if not isinstance(b, dict):
            continue
        if b.get('status') == 'running':
            b['status'] = 'interrupted'
            b['ended_at'] = b.get('ended_at') or time.time()
            b['error'] = 'el servidor se reinició durante el lote'
            for it in b.get('items') or []:
                if it.get('status') in ('processing', 'pending'):
                    it['status'] = 'cancelled'
                    it['message'] = 'interrumpido por el reinicio'
        b['_items'] = []                        # sin los items originales no se reanuda: sólo se lee
        _batches[bid] = b
        _batch_cancel[bid] = False


def _titles_of(item: dict) -> list:
    ts = [t for t in (item.get("titles") or []) if t and t.strip()]
    if ts:
        return ts
    from api.subtitle import _get_anime_titles
    return _get_anime_titles(item.get("anime_id", "") or "")


def _pick_source_track(tracks: list):
    """Mejor pista fuente para traducir.

    Orden: descartar las de CARTELES/karaoke (título o disposición `forced`) → preferir inglés →
    la primera que quede. MEDIDO: con dos pistas inglesas (`[Signs/Lyrics]` y `[Full]`) esto elegía
    la de carteles por ir primera, y el lote producía un subtítulo español sin una sola línea de
    diálogo. Si TODAS son de carteles no se descarta ninguna: traducir carteles es mejor que nada,
    y quedarse sin fuente sería una regresión.
    """
    src = [t for t in tracks if not is_es_track(t)]
    if not src:
        return None
    dialogo = [t for t in src if not (t.get("forced") or _is_signs_track(t.get("title") or ""))]
    pool = dialogo or src
    return next((t for t in pool if (t.get("language") or "").lower().startswith("en")), pool[0])


def _use_premade(path: str, tracks: list, info: dict, label: str) -> dict:
    """Descarga un subtítulo ES ya hecho, lo sincroniza y lo deja junto al vídeo."""
    with tempfile.TemporaryDirectory() as td:
        sub_file = _ext_download_sub(info, td)
        sub_file = _sync_sub_to_reference(sub_file, path, td)
        _inject_sub(path, sub_file, len([t for t in tracks if not is_es_track(t)]))
    return {"status": "done", "method": "premade", "message": label}


def _translate_with(batch: dict, item: dict, path: str, tracks: list, sub_index: int,
                    codec: str, src_lang: str, external: dict = None) -> dict:
    """Traduce con IA (Ollama) una pista concreta o un subtítulo externo, como child task."""
    ep = item.get("episode")
    child_id = uuid.uuid4().hex[:8]
    _mk_sub_task(child_id, item.get("anime_id", "") or "", int(ep or 1), fallback=os.path.basename(path))
    _tasks[child_id]["batch_id"] = batch["batch_id"]
    _do_translate(child_id, path, sub_index, codec, len(tracks), src_lang,
                  external_sub_info=external)
    st = _tasks.get(child_id, {}).get("status")
    if st == "done":
        return {"status": "done", "method": "ia", "message": "traducido con IA"}
    if st == "cancelled":
        return {"status": "cancelled", "method": "ia", "message": "cancelado"}
    return {"status": "error", "method": "ia",
            "message": _tasks.get(child_id, {}).get("message") or "error de traducción"}


def _do_choice(batch: dict, item: dict, path: str, tracks: list, choice: dict) -> dict:
    """Aplica la fuente que el usuario eligió A MANO para ESTE episodio.

    Una elección explícita NO cae a otra fuente si falla: el usuario pidió ésa, y cambiársela en
    silencio es exactamente lo que le hizo desconfiar del lote. Se declara el error y punto.
    """
    kind = choice.get("kind")
    info = choice.get("info") or {}
    if kind == "premade":
        return _use_premade(path, tracks, info, f"elegido: {info.get('source') or 'premade'}")
    if kind == "external":
        return _translate_with(batch, item, path, tracks, 0, "subrip",
                               info.get("language") or "eng", external=info)
    if kind == "track":
        want = int(choice.get("sub_index", -1))
        # Buscar la pista por su sub_index REAL: mandar el índice de otra lista traduciría una
        # pista distinta a la elegida, y el fallo sería mudo (ver el bug del índice absoluto).
        t = next((t for t in tracks if int(t.get("sub_index", -1)) == want), None)
        if not t:
            avail = ", ".join(f"{x.get('sub_index')}={x.get('language')}" for x in tracks)
            return {"status": "error", "method": None,
                    "message": f"la pista elegida ya no está (hay: {avail or 'ninguna'})"}
        return _translate_with(batch, item, path, tracks, t["sub_index"], t["codec"],
                               t.get("language") or "eng")
    return {"status": "error", "method": None, "message": f"elección desconocida: {kind!r}"}


def _process_episode(batch: dict, item: dict, policy: str, target_lang: str, force: bool) -> dict:
    """Procesa UN episodio y devuelve su resultado {status, method, message}. No lanza."""
    ep = item.get("episode")
    path = _resolve_item_path(item)
    if not path:
        return {"status": "error", "method": None, "message": "archivo no encontrado"}

    tracks = _ffprobe_tracks(path)
    choice = item.get("choice") or {}
    from api.anime import _es_sub_injected
    # Elegir una fuente a mano ES la intención de rehacerlo: no hace falta marcar además "forzar".
    if not force and not choice and (any(is_es_track(t) for t in tracks) or _es_sub_injected(path)):
        return {"status": "skipped", "method": "skip", "message": "ya tiene español"}

    if choice:
        try:
            return _do_choice(batch, item, path, tracks, choice)
        except Exception as e:
            record_error("subbatch", e, op="choice", episode=ep, kind=choice.get("kind"))
            return {"status": "error", "method": None, "message": str(e)[:120]}

    # 1) Premade primero (salvo política solo_ia): humano, gratis, sin GPU.
    if policy != "solo_ia":
        titles = _titles_of(item)
        report: list = []
        try:
            found = _ext_find_spanish_subs(titles, int(ep or 1), season=int(item.get("season", 1) or 1),
                                           report=report)
        except Exception as e:
            record_error("subbatch", e, op="find_premade", episode=ep)
            found = []
        if found:
            try:
                return _use_premade(path, tracks, found[0],
                                    f"premade de {found[0].get('source', 'fuente')}")
            except Exception as e:
                record_error("subbatch", e, op="inject_premade", episode=ep)
                # cae a IA (si la política lo permite) en vez de fallar
        if policy == "solo_buscar":
            # "Ninguna fuente lo tenía" y "las fuentes se cayeron" no pueden decirse igual: con lo
            # segundo el episodio SÍ podría tener subtítulo, y reintentar mañana tiene sentido.
            rotas = [r["source"] for r in report if r["status"] == "error"]
            sin_clave = [r["source"] for r in report if r["status"] == "unconfigured"]
            msg = "sin subtítulo premade"
            if rotas:
                msg += f" · fuentes caídas: {', '.join(rotas)}"
            if sin_clave:
                msg += f" · sin API key: {', '.join(sin_clave)}"
            return {"status": "error", "method": None, "message": msg, "sources": report}

    # 2) Traducir con IA (Ollama). Reusa _do_translate SÍNCRONO — el worker YA es serial.
    track = _pick_source_track(tracks)
    if not track:
        return {"status": "error", "method": None, "message": "sin pista de texto que traducir"}
    return _translate_with(batch, item, path, tracks, track["sub_index"], track["codec"],
                           track.get("language") or "eng")


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
            _save_batches()
        batch["status"] = "cancelled" if _batch_cancel.get(batch_id) else "done"
    except Exception as e:
        record_error("subbatch", e, op="run_batch", batch_id=batch_id)
        batch["status"] = "error"
        batch["error"] = str(e)[:200]
    finally:
        batch["current_episode"] = None
        batch["ended_at"] = time.time()
        _save_batches()


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
    _save_batches()
    threading.Thread(target=_run_batch, args=(batch_id,), daemon=True).start()
    return jsonify({"batch_id": batch_id})


def _batch_public(b: dict) -> dict:
    """Vista pública del lote + el avance del episodio EN CURSO.

    Sin `current_*` la barra del lote sólo se movía una vez por episodio: en un lote de 8 eso son
    8 saltos en 40 minutos, y entre salto y salto la interfaz parecía parada justo mientras el
    usuario la miraba. El dato existía en la tarea de traducción; sólo había que exponerlo aquí,
    que es lo que consultan tanto el modal como el Centro de Actividad.
    """
    out = {k: v for k, v in b.items() if not k.startswith("_")}
    bid = b.get("batch_id")
    for t in _tasks.values():
        if isinstance(t, dict) and t.get("batch_id") == bid and t.get("status") not in (
                "done", "error", "cancelled"):
            out["current_progress"] = t.get("progress") or 0
            out["current_message"] = t.get("message") or ""
            break
    return out


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
    if stale:
        _save_batches()
    return out
