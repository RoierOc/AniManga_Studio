#!/usr/bin/env python3
"""
Media API — series y películas occidentales (Sonarr + Radarr).

Fase 1: SOLO LECTURA. Estado de los servicios y biblioteca normalizada para la UI.

Por qué esto es una piel y no otro `anime.py`: el flujo de anime existe porque Nyaa es un caos
sin metadatos, así que hay que buscar torrents, parsear títulos, adivinar el episodio y hablar con
qBittorrent a mano. Aquí NADA de eso hace falta — Sonarr/Radarr ya hacen búsqueda, matcheo
(TVDB/TMDB), calidad, descarga, importación y renombrado. Replicarlo sería escribir un Sonarr peor
justo al lado de uno que funciona.

**Excepción medida a "aquí no se habla con qBittorrent"**: liberar espacio. Sonarr sólo borra SU
copia importada, y como la importación es por *hardlink*, mientras el torrent siga sembrando la
misma data no se libera ni un byte — y en el siguiente escaneo Sonarr la re-importa, así que el
episodio "borrado" reaparece. Anime ya lo resolvía quitando el torrent (`clear_episodes`); ésa era
toda la diferencia entre los dos módulos. Se REUTILIZA el cliente de anime (`anime._q`) en vez de
abrir uno propio: el cliente sigue teniendo un solo dueño (ver [[project_servarr_stack]]).

TMDB se usará solo para PRESENTACIÓN (portadas, descubrimiento). La identidad canónica de una serie
es la de Sonarr/Radarr; buscar por TMDB y luego reconciliar dos identidades es el problema que ya
salió caro en manga.

Endpoints (prefijo /api/media):
  GET  /status    → { sonarr: {online, version, error}, radarr: {…} }
  GET  /library   → { series: [...], movies: [...] }  (forma común, ver `_norm_*`)
  GET  /options   → { series: {roots, profiles}, movies: {…} }  para los desplegables del alta
  GET  /search    → ?q=&kind=series|movie — busca en el catálogo de Sonarr/Radarr, no en TMDB
  POST /add       → { kind, id, profile?, root?, search? } da de alta y (por defecto) busca ya
"""

from flask import Blueprint, jsonify, request
from pathlib import Path
import json
import os
import re
import threading
import time as _time

from api.resilient_http import http as http_requests
from api.observability import record_error
from api.runtime import write_json_atomic

media_bp = Blueprint("media", __name__)

_TIMEOUT = 10
# (connect, read): son servicios LOCALES — si el puerto no acepta la conexión en 2 s es que no
# está arriba, no que la red vaya lenta. Con un connect de 10 s, arrancar la app y entrar en
# Series y Películas eran 34 s de rueda antes de admitir que Radarr aún no había levantado.
_CONNECT = 2

_CFG = Path(__file__).resolve().parents[2] / "servarr" / "config"

APPS = {
    "sonarr": {"url": "http://localhost:8989", "app": "Sonarr"},
    "radarr": {"url": "http://localhost:7878", "app": "Radarr"},
    # Prowlarr habla API v1, no v3; solo se usa para el buscador manual de releases.
    "prowlarr": {"url": "http://localhost:9696", "app": "Prowlarr", "api": "v1"},
}


def _apikey(app: str) -> str:
    """Clave leída EN CALIENTE del config.xml que la propia app genera al arrancar.

    Nunca se escribe en el código ni se sincroniza: es un secreto (ver el panel de claves).
    Con regex y no con `xml.etree` a propósito — es un solo campo y así no se abre superficie XXE.
    """
    xml = (_CFG / app / "config.xml").read_text(encoding="utf-8")
    m = re.search(r"<ApiKey>([^<]+)</ApiKey>", xml)
    if not m:
        raise RuntimeError(f"{app}: sin ApiKey en config.xml (¿arrancó alguna vez? servarr/start.sh)")
    return m.group(1)


def _get(which: str, path: str, **params):
    """GET a la API de Sonarr/Radarr (v3) o Prowlarr (v1). Propaga el error: quien llama decide."""
    cfg = APPS[which]
    r = http_requests.get(f"{cfg['url']}/api/{cfg.get('api', 'v3')}/{path}",
                          headers={"X-Api-Key": _apikey(cfg["app"])},
                          params=params or None, timeout=(_CONNECT, _TIMEOUT))
    r.raise_for_status()
    return r.json()


def _get_slow(which: str, path: str, **params):
    """Como `_get` pero con timeout largo: buscar releases interroga a los indexers EN VIVO
    (Prowlarr → tracker) y tarda decenas de segundos. Con los 10 s de siempre, una búsqueda
    normal parecería un fallo del servicio."""
    cfg = APPS[which]
    r = http_requests.get(f"{cfg['url']}/api/{cfg.get('api', 'v3')}/{path}",
                          headers={"X-Api-Key": _apikey(cfg["app"])},
                          params=params or None, timeout=(_CONNECT, 120))
    r.raise_for_status()
    return r.json()


def _post(which: str, path: str, payload: dict):
    cfg = APPS[which]
    r = http_requests.post(f"{cfg['url']}/api/v3/{path}",
                           headers={"X-Api-Key": _apikey(cfg["app"])},
                           json=payload, timeout=(_CONNECT, 30))
    r.raise_for_status()
    return r.json()


def _put(which: str, path: str, payload):
    cfg = APPS[which]
    r = http_requests.put(f"{cfg['url']}/api/v3/{path}",
                          headers={"X-Api-Key": _apikey(cfg["app"])},
                          json=payload, timeout=(_CONNECT, 30))
    r.raise_for_status()
    return r.json() if r.content else {}


def _delete(which: str, path: str, **params):
    cfg = APPS[which]
    r = http_requests.delete(f"{cfg['url']}/api/v3/{path}",
                             headers={"X-Api-Key": _apikey(cfg["app"])},
                             params=params or None, timeout=(_CONNECT, 30))
    r.raise_for_status()
    return r


def _monitor_target(d: dict) -> None:
    """Monitoriza SÓLO lo que se va a descargar (un episodio, una temporada o una película).

    La biblioteca entra sin monitorizar para que nada se baje solo; esto es lo que reabre la
    puerta, del tamaño justo y en el instante justo.
    """
    kind, item_id = d.get("kind"), d.get("id")
    if not item_id:
        return  # el selector no dijo sobre qué — se descarga igual, sin tocar el monitorizado
    if kind == "movie":
        movie = _get("radarr", f"movie/{item_id}")
        if not movie.get("monitored"):
            movie["monitored"] = True
            _put("radarr", f"movie/{item_id}", movie)
        return
    # Series: `episode/monitor` acepta una lista de ids, así que temporada y episodio suelto
    # se resuelven con la MISMA llamada; sólo cambia cómo se arma la lista.
    season = d.get("season")
    if season is None:
        ids = [int(item_id)]
    else:
        eps = _get("sonarr", "episode", seriesId=int(item_id), seasonNumber=int(season))
        ids = [e["id"] for e in eps]
    if ids:
        _put("sonarr", "episode/monitor", {"episodeIds": ids, "monitored": True})


# Sonarr habla de "series" y Radarr de "movie"; el resto del flujo es idéntico. La tabla evita
# un `if which == 'sonarr'` repetido en cada endpoint, que es donde se cuelan las divergencias.
KINDS = {
    "series": {"app": "sonarr", "res": "series", "ext_id": "tvdbId",
               "search_flag": "searchForMissingEpisodes"},
    "movie":  {"app": "radarr", "res": "movie",  "ext_id": "tmdbId",
               "search_flag": "searchForMovie"},
}


def _img(item: dict, kind: str) -> str:
    """URL remota de la portada/fondo. Se prefiere `remoteUrl` (TMDB/TVDB, cacheable por
    /api/img) sobre la local de Sonarr, que obligaría a proxyar cada miniatura."""
    for im in item.get("images") or []:
        if im.get("coverType") == kind:
            return im.get("remoteUrl") or im.get("url") or ""
    return ""


def _norm_series(s: dict) -> dict:
    st = s.get("statistics") or {}
    return {
        "id": s.get("id"),
        "kind": "series",
        "title": s.get("title") or "",
        "year": s.get("year") or None,
        "overview": s.get("overview") or "",
        "poster": _img(s, "poster"),
        "banner": _img(s, "fanart"),
        "status": s.get("status") or "",
        "monitored": bool(s.get("monitored")),
        "path": s.get("path") or "",
        # Id externo (TVDB): el `id` de arriba es el de Sonarr y NO sirve para volver
        # a dar de alta la serie. Lo necesita el "Deshacer" de quitar-de-biblioteca.
        "ext_id": s.get("tvdbId") or None,
        "seasons": st.get("seasonCount") or 0,
        # Para la pestaña "Detalles", el equivalente de lo que AnimeDetail saca de AniList.
        "genres": s.get("genres") or [],
        "network": s.get("network") or "",
        "runtime": s.get("runtime") or 0,
        "rating": round((s.get("ratings") or {}).get("value") or 0, 1) or None,
        "certification": s.get("certification") or "",
        # `episodeFileCount` son los que EXISTEN en disco; `episodeCount`, los emitidos.
        # La diferencia es justo lo que falta por descargar, que es lo que la UI querrá pintar.
        "have": st.get("episodeFileCount") or 0,
        "total": st.get("episodeCount") or 0,
        "size": st.get("sizeOnDisk") or 0,
    }


def _norm_movie(m: dict) -> dict:
    return {
        "id": m.get("id"),
        "kind": "movie",
        "title": m.get("title") or "",
        "year": m.get("year") or None,
        "overview": m.get("overview") or "",
        "poster": _img(m, "poster"),
        "banner": _img(m, "fanart"),
        "status": m.get("status") or "",
        "monitored": bool(m.get("monitored")),
        "path": m.get("path") or "",
        "ext_id": m.get("tmdbId") or None,   # ídem, para Radarr

        "genres": m.get("genres") or [],
        "studio": m.get("studio") or "",
        "runtime": m.get("runtime") or 0,
        "rating": round((m.get("ratings") or {}).get("tmdb", {}).get("value") or 0, 1) or None,
        "certification": m.get("certification") or "",
        # En Radarr `hasFile` es el equivalente a "lo tengo": no hay conteo de episodios.
        "have": 1 if m.get("hasFile") else 0,
        "total": 1,
        "size": (m.get("sizeOnDisk") or 0),
    }


@media_bp.route("/status")
def media_status():
    """¿Responden Sonarr y Radarr? Distingue "apagado" de "error": un `online:false` con
    `error` no es lo mismo que un servicio que nunca se instaló."""
    out = {}
    for which, cfg in APPS.items():
        try:
            info = _get(which, "system/status")
            out[which] = {"online": True, "version": info.get("version", ""), "error": ""}
        except Exception as e:
            # No es un fallo silencioso: el estado ES la respuesta. Pero se cuenta igual,
            # porque "Sonarr lleva días caído" no debería descubrirse mirando la UI.
            record_error("media", e, op="status", app=which)
            out[which] = {"online": False, "version": "", "error": str(e)[:200]}
    return jsonify(out)


# ── Progreso de visionado ─────────────────────────────────────────────────────────────────────
# En fichero PROPIO, no en la biblioteca de anime: son dominios distintos y mezclarlos haría que
# un backup/restore de anime arrastrase series occidentales (y al revés). La clave es
# "series:<id>:<episodeId>" o "movie:<id>" — el id de Sonarr/Radarr, que es la identidad canónica.
_prog_lock = threading.Lock()


def _prog_path() -> Path:
    from api.runtime import MANGA_DIR
    return Path(MANGA_DIR) / "media_progress.json"


def _prog_read() -> dict:
    p = _prog_path()
    if not p.exists():
        return {}          # "aún no has visto nada" es un vacío LEGÍTIMO: no se loguea.
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        # Ilegible/corrupto SÍ es un fallo: sin esto, perder el progreso se vería igual que
        # no tenerlo todavía.
        record_error("media", e, op="progress_read", path=str(p))
        return {}


def _prog_write(data: dict):
    # Escritura ATÓMICA (tmp + os.replace): había 3 lectores fuera del lock que pillaban el
    # fichero truncado a mitad de write_text → JSONDecodeError → _prog_read devolvía {} y
    # "Seguir viendo" salía vacío (10 casos en el log de un solo día). Con replace, un lector
    # ve siempre la versión vieja o la nueva, nunca media.
    # (ahora vía el helper compartido: mismo tmp+replace, y además fsync + respaldo)
    write_json_atomic(_prog_path(), data, indent=2, keep_backup=True)


def _watched(position: float, duration: float) -> bool:
    """Mismo criterio que anime (`_is_watched`): terminado si faltan <2 min o se pasó del 90%.
    Se replica en vez de importarlo de `anime.py` para no atar este módulo a aquel god-module;
    si el criterio cambia, es UNA constante en dos sitios y no un import cruzado."""
    if duration <= 0:
        return False
    return (duration - position) < 120 or (position / duration) >= 0.9


@media_bp.route("/progress", methods=["POST"])
def media_progress():
    """Guarda posición/visto desde el reproductor nativo. Mismo contrato que el de anime."""
    d = request.get_json(silent=True) or {}
    key = (d.get("key") or "").strip()
    if not key:
        return jsonify({"error": "key required"}), 400
    position = float(d.get("position") or 0)
    duration = float(d.get("duration") or 0)
    done = bool(d.get("ended")) or _watched(position, duration)
    with _prog_lock:
        prog = _prog_read()
        # Al terminar se guarda pos 0: reanudar en el segundo final sería inútil.
        # `at` (epoch) es lo que permite ordenar "Seguir viendo" por lo más reciente. Los
        # registros viejos no lo tienen: se leen como 0 y quedan al final, sin romper nada.
        prog[key] = {"pos": 0 if done else int(position), "watched": done,
                     "duration": int(duration), "at": int(_time.time())}
        _prog_write(prog)
    return jsonify({"ok": True, "watched": done})


@media_bp.route("/options")
def media_options():
    """Carpetas raíz y perfiles de calidad, para los desplegables del alta. Se leen en vivo:
    cachearlos significaría que crear un perfil en Sonarr no se ve aquí hasta reiniciar."""
    out = {}
    for kind, k in KINDS.items():
        try:
            out[kind] = {
                "roots": [{"path": r["path"], "free": r.get("freeSpace") or 0}
                          for r in _get(k["app"], "rootfolder")],
                "profiles": [{"id": p["id"], "name": p["name"]}
                             for p in _get(k["app"], "qualityprofile")],
            }
        except Exception as e:
            record_error("media", e, op="options", kind=kind)
            out[kind] = {"roots": [], "profiles": [], "error": str(e)[:200]}
    return jsonify(out)


@media_bp.route("/search")
def media_search():
    """Busca en el catálogo de Sonarr/Radarr (TVDB/TMDB por debajo), NO en TMDB directamente:
    así el id que devolvemos es ya el que hay que usar para dar de alta. Buscar por nuestra
    cuenta obligaría a reconciliar dos identidades, que es de donde salen los cruces de obras.

    Ojo: esto NO busca torrents. Busca la OBRA. Los releases los busca Sonarr al darla de alta.
    """
    q = (request.args.get("q") or "").strip()
    kind = request.args.get("kind") or "series"
    if not q:
        return jsonify({"results": []})
    if kind not in KINDS:
        return jsonify({"error": f"kind inválido: {kind}"}), 400
    k = KINDS[kind]
    try:
        raw = _get(k["app"], f"{k['res']}/lookup", term=q)
    except Exception as e:
        record_error("media", e, op="search", kind=kind, q=q[:80])
        # 502 y no `{"results": []}`: un fallo NO puede parecer "no hay resultados".
        return jsonify({"error": str(e)[:200]}), 502
    norm = _norm_series if kind == "series" else _norm_movie
    out = []
    for x in raw[:40]:
        it = norm(x)
        it["ext_id"] = x.get(k["ext_id"])
        # `id` viene 0/ausente si NO está en la biblioteca: así la UI sabe si ya lo sigues.
        it["already"] = bool(x.get("id"))
        out.append(it)
    return jsonify({"results": out})


@media_bp.route("/add", methods=["POST"])
def media_add():
    """Da de alta una serie/película y lanza la búsqueda de releases.

    Se reenvía el objeto TAL CUAL lo devolvió `lookup` (imágenes, temporadas, titleSlug…) y solo
    se le añaden los campos del alta. Reconstruirlo a mano es la forma clásica de romper Sonarr
    con un payload incompleto, y encima quedaría desactualizado en cada versión suya.
    """
    d = request.get_json(silent=True) or {}
    kind = d.get("kind") or "series"
    if kind not in KINDS:
        return jsonify({"error": f"kind inválido: {kind}"}), 400
    k = KINDS[kind]
    ext_id = d.get("id")
    if not ext_id:
        return jsonify({"error": "falta id (el ext_id que devuelve /search)"}), 400

    try:
        matches = [x for x in _get(k["app"], f"{k['res']}/lookup", term=f"{k['ext_id']}:{ext_id}")
                   if str(x.get(k["ext_id"])) == str(ext_id)]
        if not matches:
            return jsonify({"error": "no encontrado en el catálogo"}), 404
        item = matches[0]
        if item.get("id"):
            return jsonify({"ok": True, "already": True, "id": item["id"], "title": item.get("title", "")})

        roots = _get(k["app"], "rootfolder")
        profiles = _get(k["app"], "qualityprofile")
        if not roots or not profiles:
            return jsonify({"error": "Sonarr/Radarr sin carpeta raíz o perfil de calidad"}), 409

        item.update({
            "rootFolderPath": d.get("root") or roots[0]["path"],
            "qualityProfileId": int(d.get("profile") or profiles[0]["id"]),
            "monitored": True,
            # NADA se descarga solo. Añadir a la biblioteca es un acto de catálogo, no una orden
            # de descarga: el torrent lo elige el usuario en el selector, uno por uno.
            #
            # Son DOS interruptores y hacen falta los dos. `search_flag` evita la búsqueda del
            # momento del alta; `monitor: "none"` deja los episodios sin monitorizar, que es lo
            # que impide que el RSS sync (cada 15 min, por su cuenta) se ponga a bajar la serie
            # entera después, cuando ya nadie está mirando.
            "addOptions": {k["search_flag"]: bool(d.get("search", False)),
                           **({"monitor": "none"} if kind == "series" else {})},
        })
        if kind == "movie":
            # Sin esto Radarr no descarga hasta el estreno en cines, que no es lo que se espera.
            item.setdefault("minimumAvailability", "released")
            # En Radarr no hay "monitor: none": el interruptor del RSS es la peli entera. Entra
            # sin monitorizar y `/releases/grab` la monitoriza justo antes de bajar el torrent
            # elegido — si no, no se importaría al terminar.
            item["monitored"] = False

        created = _post(k["app"], k["res"], item)
        return jsonify({"ok": True, "already": False,
                        "id": created.get("id"), "title": created.get("title", "")})
    except Exception as e:
        record_error("media", e, op="add", kind=kind, ext_id=ext_id)
        return jsonify({"error": str(e)[:300]}), 502


@media_bp.route("/continue")
def media_continue():
    """"Seguir viendo": por serie, el episodio a medias o el siguiente sin ver.

    Mismo criterio que el `continueWatching` del store de anime (`stores/anime.js`): primero lo
    empezado y no terminado; si no hay, el siguiente sin ver **que esté en disco** — ofrecer algo
    que no se puede reproducir es peor que no ofrecer nada. Ordenado por visto más reciente, que
    es para lo que existe el campo `at`.
    """
    prog = _prog_read()
    if not prog:
        return jsonify({"items": []})   # nadie ha visto nada aún: vacío LEGÍTIMO.

    # Solo se consultan las series que aparecen en el progreso, no la biblioteca entera:
    # una llamada a Sonarr por serie EMPEZADA, no por serie existente.
    seen = {}
    for key, p in prog.items():
        parts = key.split(":")
        if len(parts) == 3 and parts[0] == "series":
            seen.setdefault(int(parts[1]), {})[int(parts[2])] = p

    try:
        series = {s["id"]: _norm_series(s) for s in _get("sonarr", "series")}
    except Exception as e:
        record_error("media", e, op="continue")
        return jsonify({"items": [], "error": str(e)[:200]}), 502

    items = []
    for sid, eps_prog in seen.items():
        meta = series.get(sid)
        if not meta:
            continue          # la serie se quitó de Sonarr pero quedó su progreso
        try:
            eps = _get("sonarr", "episode", seriesId=sid)
        except Exception as e:
            record_error("media", e, op="continue_eps", series=sid)
            continue
        eps.sort(key=lambda e: (e.get("seasonNumber") or 0, e.get("episodeNumber") or 0))

        started = [e for e in eps if (eps_prog.get(e["id"]) or {}).get("pos")]
        pick = started[-1] if started else next(
            (e for e in eps if e.get("hasFile") and not (eps_prog.get(e["id"]) or {}).get("watched")
             and (e.get("seasonNumber") or 0) > 0), None)
        if not pick:
            continue
        p = eps_prog.get(pick["id"]) or {}
        items.append({
            "series_id": sid, "episode_id": pick["id"],
            "title": meta["title"], "poster": meta["poster"], "banner": meta["banner"],
            "season": pick.get("seasonNumber"), "num": pick.get("episodeNumber"),
            "episode_title": pick.get("title") or "",
            "still": _img(pick, "screenshot") or meta["banner"],
            "pos": p.get("pos", 0), "duration": p.get("duration", 0),
            "has_file": bool(pick.get("hasFile")),
            "at": max((x.get("at", 0) for x in eps_prog.values()), default=0),
        })

    items.sort(key=lambda x: x["at"], reverse=True)
    return jsonify({"items": items[:12]})


@media_bp.route("/series/<int:series_id>/episodes")
def media_episodes(series_id):
    """Episodios de una serie, con la RUTA del fichero cuando existe en disco.

    Esa ruta es lo único que necesita el reproductor: `/api/anime/native/resolve` acepta
    `local_path`, así que el player nativo se reusa tal cual, sin duplicar nada de la shell.
    """
    try:
        # `includeImages` no viene por defecto: sin él no hay miniatura de episodio (Sonarr las
        # tiene de TVDB, no hace falta pedirlas a TMDB aparte).
        eps = _get("sonarr", "episode", seriesId=series_id, includeImages="true")
        files = {f["id"]: f for f in _get("sonarr", "episodefile", seriesId=series_id)}
    except Exception as e:
        record_error("media", e, op="episodes", series=series_id)
        return jsonify({"error": str(e)[:200]}), 502

    prog = _prog_read()
    out = []
    for e in eps:
        f = files.get(e.get("episodeFileId")) or {}
        p = prog.get(f"series:{series_id}:{e['id']}") or {}
        out.append({
            "id": e["id"],
            "season": e.get("seasonNumber"),
            "num": e.get("episodeNumber"),
            "title": e.get("title") or "",
            "overview": e.get("overview") or "",
            "aired": e.get("airDateUtc") or "",
            "still": _img(e, "screenshot"),
            "monitored": bool(e.get("monitored")),
            "has_file": bool(e.get("hasFile")),
            "path": f.get("path") or "",
            "size": f.get("size") or 0,
            "quality": ((f.get("quality") or {}).get("quality") or {}).get("name", ""),
            "pos": p.get("pos", 0),
            # La duración viene del progreso guardado (la sabe el reproductor, no Sonarr): sin
            # ella la barra de reanudación no se puede dibujar.
            "duration": p.get("duration", 0),
            "watched": bool(p.get("watched")),
        })
    out.sort(key=lambda x: (x["season"] or 0, x["num"] or 0))
    return jsonify({"episodes": out})


def _torrent_hashes(which: str, path: str, season=None, **params) -> set:
    """Hashes de los torrents que trajeron este contenido, según el historial de Sonarr/Radarr.

    `downloadId` es el info_hash con el que qBittorrent conoce el torrent. Se mira el historial
    y no la cola porque la cola sólo tiene lo que está descargando AHORA — lo que ocupa disco es
    justo lo que ya terminó. Un fallo aquí NO es "no había torrents": lo propaga quien llama.
    """
    if season not in (None, ""):
        params["includeEpisode"] = True   # sin esto no se sabe de qué temporada es cada registro
    recs = _get(which, path, **params)
    # Sonarr/Radarr devuelven a veces {records: [...]} y a veces la lista pelada.
    if isinstance(recs, dict):
        recs = recs.get("records") or []
    out = set()
    for r in recs:
        dl = (r.get("downloadId") or "").strip().lower()
        if not dl:
            continue
        if season not in (None, ""):
            ep = r.get("episode") or {}
            if str(ep.get("seasonNumber")) != str(season):
                continue
        out.add(dl)
    return out


def _drop_torrents(hashes: set) -> tuple:
    """Quita los torrents (con sus datos) de qBittorrent. Devuelve (quitados, fallidos).

    Se reutiliza el cliente de anime a propósito — es el MISMO qBittorrent y la misma sesión;
    un segundo login aquí sería una segunda verdad que mantener. Import perezoso para no atar
    la carga de este módulo al god-module.

    Borrar los datos es seguro para lo que se conserva: Sonarr importa por hardlink, así que
    mientras quede un enlace (los episodios que NO se han borrado) los bytes siguen ahí.
    """
    if not hashes:
        return 0, 0
    from api.anime import _q
    ok, failed = 0, 0
    for h in hashes:
        try:
            _q("post", "/torrents/delete", data={"hashes": h, "deleteFiles": "true"})
            ok += 1
        except Exception as e:
            failed += 1
            record_error("media", e, op="qbt_delete", hash=h)
    return ok, failed


@media_bp.route("/series/<int:series_id>/files", methods=["DELETE"])
def media_delete_files(series_id):
    """Borra los ficheros de vídeo para liberar espacio, sin quitar la serie de la biblioteca.

    Equivale al "Borrar eps" de anime: la serie sigue ahí con su ficha y su progreso, sólo
    desaparece lo que ocupa disco. Con `season` se acota a una temporada; sin él, va toda.
    Se quita también el torrent — si no, el hardlink que siembra qBittorrent deja los bytes
    en el disco y Sonarr re-importa el episodio en el siguiente escaneo (ver cabecera).
    """
    season = request.args.get("season")
    try:
        files = _get("sonarr", "episodefile", seriesId=series_id)
    except Exception as e:
        record_error("media", e, op="delete_files", series=series_id)
        return jsonify({"error": str(e)[:200]}), 502

    if season is not None and season != "":
        files = [f for f in files if str(f.get("seasonNumber")) == str(season)]

    # Los hashes se leen ANTES de borrar: el historial sobrevive al borrado, pero así el
    # resultado no depende de en qué orden decida limpiar Sonarr.
    hashes, hashes_err = set(), ""
    try:
        hashes = _torrent_hashes("sonarr", "history/series", season, seriesId=series_id)
    except Exception as e:
        # No poder consultar el historial NO es "no había torrents": se borran los ficheros
        # igual, pero se dice que el torrent quedó sin tocar en vez de callar.
        hashes_err = str(e)[:200]
        record_error("media", e, op="delete_files_history", series=series_id)

    freed = sum(f.get("size") or 0 for f in files)
    deleted, failed = 0, 0
    for f in files:
        try:
            _delete("sonarr", f"episodefile/{f['id']}")
            deleted += 1
        except Exception as e:
            failed += 1
            record_error("media", e, op="delete_file", file=f.get("id"))

    torrents, torrents_failed = _drop_torrents(hashes) if deleted else (0, 0)
    # `failed` viaja aparte: "no había nada que borrar" y "no se pudo borrar" no pueden
    # leerse igual desde la UI. Lo mismo para el torrent: que no se pudiera quitar significa
    # que el espacio NO se ha liberado del todo, y eso hay que decirlo.
    return jsonify({"ok": failed == 0 and torrents_failed == 0 and not hashes_err,
                    "deleted": deleted, "failed": failed, "freed": freed,
                    "torrents": torrents, "torrents_failed": torrents_failed,
                    "torrents_error": hashes_err})


@media_bp.route("/movie/<int:movie_id>/file", methods=["DELETE"])
def media_delete_movie_file(movie_id):
    """Liberar espacio de una PELÍCULA: el mismo gesto que en series, que hasta ahora no existía
    (el botón sólo se ofrecía en series, así que en películas no había forma de soltar disco)."""
    try:
        m = _get("radarr", f"movie/{movie_id}")
    except Exception as e:
        record_error("media", e, op="delete_movie_file", movie=movie_id)
        return jsonify({"error": str(e)[:200]}), 502

    f = m.get("movieFile") or {}
    if not f.get("id"):
        # 0 borrados con ok=True: "no había fichero" es un vacío legítimo, no un fallo.
        return jsonify({"ok": True, "deleted": 0, "failed": 0, "freed": 0,
                        "torrents": 0, "torrents_failed": 0, "torrents_error": ""})

    hashes, hashes_err = set(), ""
    try:
        hashes = _torrent_hashes("radarr", "history/movie", None, movieId=movie_id)
    except Exception as e:
        hashes_err = str(e)[:200]
        record_error("media", e, op="delete_movie_history", movie=movie_id)

    deleted, failed = 0, 0
    try:
        _delete("radarr", f"moviefile/{f['id']}")
        deleted = 1
    except Exception as e:
        failed = 1
        record_error("media", e, op="delete_movie_file", movie=movie_id, file=f.get("id"))

    torrents, torrents_failed = _drop_torrents(hashes) if deleted else (0, 0)
    return jsonify({"ok": failed == 0 and torrents_failed == 0 and not hashes_err,
                    "deleted": deleted, "failed": failed, "freed": f.get("size") or 0,
                    "torrents": torrents, "torrents_failed": torrents_failed,
                    "torrents_error": hashes_err})


@media_bp.route("/movie/<int:movie_id>/file")
def media_movie_file(movie_id):
    """Ruta del fichero de una película (+ su progreso), para el mismo reproductor."""
    try:
        m = _get("radarr", f"movie/{movie_id}")
    except Exception as e:
        record_error("media", e, op="movie_file", movie=movie_id)
        return jsonify({"error": str(e)[:200]}), 502
    f = m.get("movieFile") or {}
    if not f.get("path"):
        # 404 con motivo: "aún no la has descargado" no es lo mismo que "Radarr falló".
        return jsonify({"error": "sin fichero en disco todavía", "has_file": False}), 404
    p = _prog_read().get(f"movie:{movie_id}") or {}
    return jsonify({"has_file": True, "path": f["path"], "size": f.get("size") or 0,
                    "quality": ((f.get("quality") or {}).get("quality") or {}).get("name", ""),
                    "pos": p.get("pos", 0), "watched": bool(p.get("watched"))})


# ── Descubrimiento (TMDB) ─────────────────────────────────────────────────────────────────────
# TMDB SOLO para descubrir y presentar: qué se está viendo, portadas, sinopsis. La identidad
# canónica sigue siendo la de Sonarr/Radarr — por eso "Añadir" pasa por `/resolve`, que traduce
# título+año al id de ellos en vez de arrastrar el id de TMDB por el resto del sistema.
_TMDB = "https://api.themoviedb.org/3"
# w500: en una rejilla de ~180px con pantallas 2x, w342 se veía blando. El proxy lo cachea
# en disco, así que el tamaño extra se paga una sola vez.
_TMDB_IMG = "https://image.tmdb.org/t/p/w500"

# TMDB llama `tv` a lo que Sonarr llama `series`; y sus listas no se llaman igual en cada tipo.
_LISTS = {"trending": "trending/{t}/week", "popular": "{t}/popular",
          "top": "{t}/top_rated", "upcoming": "tv/on_the_air|movie/upcoming"}

# Con un género elegido no valen las listas fijas (trending/popular/top_rated no aceptan filtro):
# se usa /discover/{t}?with_genres=…, y el pill de lista pasa a ser el ORDEN. `top` exige un
# mínimo de votos para no llenar la rejilla de obras oscuras con un 10 de dos votos.
_GENRE_SORT = {"trending": "popularity.desc", "popular": "popularity.desc",
               "top": "vote_average.desc", "upcoming": "primary_release_date.desc"}


def _tmdb(path, **params):
    from api.config_store import get_secret
    key = get_secret("TMDB_API_KEY")
    if not key:
        raise RuntimeError("falta TMDB_API_KEY (Ajustes → claves API)")
    r = http_requests.get(f"{_TMDB}/{path}",
                          params={"api_key": key, "language": "es-ES", **params}, timeout=_TIMEOUT)
    r.raise_for_status()
    return r.json()


_genres_cache: dict = {}   # kind -> [{id, name}]


@media_bp.route("/genres")
def media_genres():
    """Géneros de TMDB para filtrar Descubrir. Cacheado en proceso: apenas cambian."""
    kind = request.args.get("kind") or "series"
    if kind not in KINDS:
        return jsonify({"error": "kind inválido"}), 400
    if kind in _genres_cache:
        return jsonify(_genres_cache[kind])
    t = "tv" if kind == "series" else "movie"
    try:
        raw = _tmdb(f"genre/{t}/list")
    except Exception as e:
        record_error("media", e, op="genres", kind=kind)
        return jsonify({"error": str(e)[:200]}), 502
    # El género 16 (Animación) japonés tiene su sección en Anime, pero aquí NO lo quitamos: la
    # animación occidental (Rick y Morty, Arcane) sí es de esta sección. El filtro por idioma+16
    # de /discover ya aparta el anime real.
    out = [{"id": g["id"], "name": g["name"]} for g in raw.get("genres", [])]
    _genres_cache[kind] = out
    return jsonify(out)


def _library_tmdb_ids(kind: str):
    """Ids de TMDB que YA están en la biblioteca, o `None` si no se pudo preguntar.

    `None` y `set()` no significan lo mismo: con Sonarr caído, "no la tengo" sería mentira, así
    que la UI prefiere no decir nada a decir algo falso (regla «falló ≠ no había»).
    Sonarr trae `tmdbId` en cada serie además del `tvdbId`, así que el cruce es por id exacto.
    """
    k = KINDS[kind]
    try:
        return {x["tmdbId"] for x in _get(k["app"], k["res"]) if x.get("tmdbId")}
    except Exception as e:
        record_error("media", e, op="library_ids", kind=kind)
        return None


@media_bp.route("/discover")
def media_discover():
    """Qué ver: tendencias, populares o mejor valoradas, según TMDB."""
    kind = request.args.get("kind") or "series"
    which = request.args.get("list") or "trending"
    if kind not in KINDS or which not in _LISTS:
        return jsonify({"error": "kind o list inválidos"}), 400
    t = "tv" if kind == "series" else "movie"
    genre = (request.args.get("genre") or "").strip()
    page = int(request.args.get("page") or 1)
    try:
        if genre.isdigit():
            # Filtrado por género: /discover/{t} con el orden derivado del pill de lista.
            params = {"with_genres": genre, "sort_by": _GENRE_SORT.get(which, "popularity.desc"),
                      "page": page}
            if which == "top":
                params["vote_count.gte"] = 200   # evita el "10 de dos votos"
            raw = _tmdb(f"discover/{t}", **params)
        else:
            path = (_LISTS[which].split("|")[0 if t == "tv" else 1] if which == "upcoming"
                    else _LISTS[which].format(t=t))
            raw = _tmdb(path, page=page)
    except Exception as e:
        record_error("media", e, op="discover", kind=kind, list=which)
        return jsonify({"error": str(e)[:200]}), 502

    have = _library_tmdb_ids(kind)
    out = []
    skipped_anime = 0
    for x in raw.get("results", []):
        # El anime tiene su PROPIA sección (con AniList, emisión y Nyaa detrás), así que aquí
        # solo estorba. Se detecta por la intersección japonés + animación (género 16), no por
        # una sola de las dos: filtrar solo por género 16 tiraría también la animación
        # occidental (Rick y Morty, Arcane), y filtrar solo por idioma tiraría el live-action
        # japonés, que sí es contenido de esta sección.
        if x.get("original_language") == "ja" and 16 in (x.get("genre_ids") or []):
            skipped_anime += 1
            continue
        date = x.get("first_air_date") or x.get("release_date") or ""
        out.append({
            "tmdb_id": x.get("id"),
            "kind": kind,
            "title": x.get("name") or x.get("title") or "",
            # TMDB responde en español y Sonarr/Radarr indexan por el título ORIGINAL. Se manda
            # el original para resolver: sin esto, "La casa del dragón" nunca casaría con
            # "House of the Dragon" y todo pediría confirmación manual.
            "title_original": x.get("original_name") or x.get("original_title") or "",
            "year": int(date[:4]) if date[:4].isdigit() else None,
            "overview": x.get("overview") or "",
            "poster": f"{_TMDB_IMG}{x['poster_path']}" if x.get("poster_path") else "",
            "score": round(x.get("vote_average") or 0, 1),
            # Ausente (no `false`) si no se pudo consultar la biblioteca: ver `_library_tmdb_ids`.
            **({"already": x.get("id") in have} if have is not None else {}),
        })
    # `skipped_anime` viaja para que una lista corta no parezca un fallo de TMDB: si de 20
    # resultados quedan 12, la UI puede decir por qué faltan los otros 8.
    # `page`/`total_pages` los usa el scroll infinito para saber si quedan más (TMDB tope 500).
    return jsonify({
        "results": out,
        "skipped_anime": skipped_anime,
        "page": raw.get("page") or 1,
        "total_pages": min(raw.get("total_pages") or 1, 500),
    })


@media_bp.route("/resolve")
def media_resolve():
    """Traduce un título+año de TMDB al id de Sonarr/Radarr, que es con el que se da de alta.

    Se hace explícito y no "por dentro" del alta a propósito: el emparejamiento por título puede
    fallar (remakes, mismo nombre en distinto año), y confundirse de obra al descargar es mucho
    peor que pedir una confirmación. Si no hay una coincidencia clara, devuelve los candidatos
    para que elija el usuario en vez de adivinar.
    """
    kind = request.args.get("kind") or "series"
    title = (request.args.get("title") or "").strip()
    year = request.args.get("year")
    tmdb_id = (request.args.get("tmdb_id") or "").strip()
    if kind not in KINDS or not (title or tmdb_id):
        return jsonify({"error": "kind y title/tmdb_id son obligatorios"}), 400
    k = KINDS[kind]

    # Camino exacto: Sonarr y Radarr aceptan `term=tmdb:<id>` en su lookup y devuelven UNA obra.
    # Como Descubrir viene de TMDB, ese id lo tenemos siempre — y así no hay nada que emparejar
    # por título (que es de donde salía el "varias coincidencias, elige tú").
    if tmdb_id.isdigit():
        try:
            raw = _get(k["app"], f"{k['res']}/lookup", term=f"tmdb:{tmdb_id}")
        except Exception as e:
            record_error("media", e, op="resolve", kind=kind, tmdb_id=tmdb_id)
            return jsonify({"error": str(e)[:200]}), 502
        if len(raw) == 1:
            x = raw[0]
            it = (_norm_series if kind == "series" else _norm_movie)(x)
            it["ext_id"] = x.get(k["ext_id"])
            it["already"] = bool(x.get("id"))
            return jsonify({"match": it, "candidates": [it]})
        # Sin mapeo TMDB→TVDB en el catálogo: se sigue por título, abajo.
        if not title:
            return jsonify({"match": None, "candidates": []})

    try:
        raw = _get(k["app"], f"{k['res']}/lookup", term=title)
    except Exception as e:
        record_error("media", e, op="resolve", kind=kind, title=title[:80])
        return jsonify({"error": str(e)[:200]}), 502

    norm = _norm_series if kind == "series" else _norm_movie
    cands = []
    for x in raw[:20]:
        it = norm(x)
        it["ext_id"] = x.get(k["ext_id"])
        it["already"] = bool(x.get("id"))
        cands.append(it)

    def same(c):
        return (c["title"] or "").strip().lower() == title.lower() and (
            not year or str(c["year"]) == str(year))

    exact = [c for c in cands if same(c)]
    if len(exact) == 1:
        return jsonify({"match": exact[0], "candidates": cands})
    # Ambiguo: NO se elige por él. La UI enseña los candidatos.
    return jsonify({"match": None, "candidates": cands})


@media_bp.route("/grab", methods=["POST"])
def media_grab():
    """Pide a Sonarr/Radarr que BUSQUE y descargue: un episodio, una temporada o una película.

    No elegimos el release nosotros: eso es exactamente lo que Sonarr hace bien (perfiles de
    calidad, tamaño, idioma, listas de rechazo). La app solo dispara el comando.
    """
    d = request.get_json(silent=True) or {}
    kind = d.get("kind")
    try:
        if kind == "episode":
            cmd = {"name": "EpisodeSearch", "episodeIds": [int(d["id"])]}
            app = "sonarr"
        elif kind == "season":
            cmd = {"name": "SeasonSearch", "seriesId": int(d["id"]), "seasonNumber": int(d["season"])}
            app = "sonarr"
        elif kind == "movie":
            cmd = {"name": "MoviesSearch", "movieIds": [int(d["id"])]}
            app = "radarr"
        else:
            return jsonify({"error": f"kind inválido: {kind}"}), 400
        r = _post(app, "command", cmd)
    except Exception as e:
        record_error("media", e, op="grab", kind=kind, id=d.get("id"))
        return jsonify({"error": str(e)[:300]}), 502
    # El comando es ASÍNCRONO: `ok` significa "encargado", no "descargado". La vista Descargas
    # es la que dice si de verdad encontró algo — decir aquí que está descargado sería mentir.
    return jsonify({"ok": True, "command_id": r.get("id"), "status": r.get("status", "queued")})


def _indexers_down() -> list:
    """Indexers que Prowlarr ha apartado por fallar, con el motivo.

    Esto NO es decorado. Cuando un indexer se cae (medido: apibay.org con *"Http request timed
    out"*), Prowlarr lo deshabilita un rato y las búsquedas siguientes se hacen contra NINGÚN
    indexer, devolviendo 0 resultados sin un solo error. Es el `[]` que significa "falló" en vez
    de "no había" — el error más caro de este proyecto. Con esto, la UI puede decir "The Pirate
    Bay no responde" en lugar de "no hay releases".
    """
    try:
        status = {s["indexerId"]: s for s in _get("prowlarr", "indexerstatus")}
        if not status:
            return []
        names = {i["id"]: i.get("name", f"#{i['id']}") for i in _get("prowlarr", "indexer")}
    except Exception as e:
        record_error("media", e, op="indexer_status")
        return []
    return [{"name": names.get(i, f"#{i}"), "until": s.get("disabledTill") or "",
             "reason": (s.get("mostRecentFailure") and "fallos recientes") or ""}
            for i, s in status.items()]


@media_bp.route("/releases")
def media_releases():
    """Los torrents disponibles para un episodio / temporada / película, SIN descargar nada.

    Existe porque elegir a mano es lo que se pide aquí (igual que en anime): `/grab` deja que
    Sonarr decida por su perfil de calidad, y eso está bien para lo automático, pero no cuando
    quieres mirar seeders, tamaño y grupo antes de bajar.
    """
    kind = request.args.get("kind") or "episode"
    ident = request.args.get("id")
    if not ident:
        return jsonify({"error": "id requerido"}), 400
    params = ({"episodeId": int(ident)} if kind == "episode" else
              {"seriesId": int(ident), "seasonNumber": int(request.args.get("season") or 1)}
              if kind == "season" else {"movieId": int(ident)})
    app = "radarr" if kind == "movie" else "sonarr"
    try:
        # 120 s y no los 10 de siempre: esto interroga a los indexers EN VIVO, no una caché.
        raw = _get_slow(app, "release", **params)
    except Exception as e:
        record_error("media", e, op="releases", kind=kind, id=ident)
        return jsonify({"error": str(e)[:200]}), 502

    out = []
    for r in raw:
        q = ((r.get("quality") or {}).get("quality") or {})
        out.append({
            "guid": r.get("guid"),
            "indexer_id": r.get("indexerId"),
            "download_url": r.get("downloadUrl") or r.get("magnetUrl") or "",
            "indexer": r.get("indexer") or "",
            "title": r.get("title") or "",
            "size": r.get("size") or 0,
            "seeders": r.get("seeders"),
            "leechers": r.get("leechers"),
            "quality": q.get("name") or "",
            "resolution": q.get("resolution") or 0,
            "age": r.get("ageHours") or 0,
            "languages": [x.get("name") for x in (r.get("languages") or []) if x.get("name")],
            # Sonarr ya ha juzgado el release: si lo rechazaría, dice POR QUÉ. Enseñarlo evita
            # que el usuario elija a ciegas uno que luego se descarta solo.
            "rejected": bool(r.get("rejected")),
            "rejections": r.get("rejections") or [],
            # Un pack de temporada trae varios episodios en un torrent: Sonarr los importa todos
            # por separado. Marcarlo evita bajar 1 GB creyendo que es un episodio suelto.
            "full_season": bool(r.get("fullSeason")),
            "episodes": len(r.get("episodeNumbers") or []),
        })
    # Sonarr filtra por su propio parseo y a veces devuelve 0 aunque el indexer SÍ tenga cosas
    # (medido: 1x02 de Breaking Bad → Sonarr 0, Prowlarr 13). Para un selector MANUAL eso no
    # vale: se pregunta a Prowlarr en crudo y se marcan como `via: prowlarr`, porque al bajarlos
    # hay que empujárselos a Sonarr en vez de referenciar un guid que no conoce.
    via_prowlarr = False
    if not out and (q := (request.args.get("q") or "").strip()):
        try:
            for r in _get_slow("prowlarr", "search", query=q, type="search")[:60]:
                out.append({
                    "guid": r.get("guid"), "indexer_id": r.get("indexerId"),
                    "indexer": r.get("indexer") or "", "title": r.get("title") or "",
                    "size": r.get("size") or 0, "seeders": r.get("seeders"),
                    "leechers": r.get("leechers"), "quality": "", "resolution": 0,
                    "age": r.get("ageHours") or 0, "languages": [],
                    "rejected": False, "rejections": [],
                    "via": "prowlarr", "download_url": r.get("downloadUrl") or r.get("magnetUrl") or "",
                })
            via_prowlarr = True
        except Exception as e:
            record_error("media", e, op="releases_prowlarr", q=q[:80])
            return jsonify({"error": f"Prowlarr: {e}"[:200]}), 502

    out.sort(key=lambda x: (x["rejected"], -(x["seeders"] or 0)))
    # Una lista vacía SIEMPRE viaja con el estado de los indexers: sin esto, "el indexer está
    # caído" y "no existe ese episodio" se ven exactamente igual en la pantalla.
    return jsonify({"releases": out, "via_prowlarr": via_prowlarr,
                    "indexers_down": _indexers_down() if not out else []})


@media_bp.route("/releases/grab", methods=["POST"])
def media_release_grab():
    """Descarga UN release concreto, el que el usuario ha elegido."""
    d = request.get_json(silent=True) or {}
    guid, indexer_id = d.get("guid"), d.get("indexer_id")
    if not guid or indexer_id is None:
        return jsonify({"error": "guid e indexer_id requeridos"}), 400
    app = "radarr" if d.get("kind") == "movie" else "sonarr"
    try:
        # Entrar sin monitorizar es lo que impide las descargas automáticas, pero Sonarr/Radarr
        # NO importan lo que no monitorizan: el torrent bajaría y se quedaría fuera de la
        # biblioteca, en silencio. Se monitoriza EXACTAMENTE lo que el usuario acaba de elegir,
        # ni un episodio más, y sólo en el momento de elegirlo.
        _monitor_target(d)
    except Exception as e:
        # Falla monitorizar → no se descarga. Bajar algo que luego no se va a importar deja un
        # torrent huérfano y al usuario esperando un episodio que nunca aparece.
        record_error("media", e, op="grab_monitor", target=str(d.get("id"))[:40])
        return jsonify({"error": f"no se pudo preparar la descarga: {e}"[:300]}), 502
    try:
        if d.get("via") == "prowlarr":
            # Viene del buscador crudo: Sonarr no conoce ese guid, así que se le EMPUJA el
            # release (`release/push`). Sigue siendo Sonarr quien descarga, sigue e importa —
            # que es justo lo que no queremos perder por elegir a mano.
            _post(app, "release/push", {
                "title": d.get("title") or "",
                "downloadUrl": d.get("download_url") or "",
                "protocol": "torrent",
                "publishDate": d.get("publish_date") or "1970-01-01T00:00:00Z",
            })
        else:
            # `release` (grab por guid) es un grab MANUAL: descarga el release que el usuario eligió
            # aunque su calidad esté "not wanted in profile" (así se baja 4K en una serie con perfil
            # 1080p). Pero depende de una caché EN MEMORIA de Sonarr que caduca: si pasó un rato
            # desde la búsqueda, da 500 "Couldn't find requested release in cache". Re-lanzamos la
            # búsqueda para repoblar la caché con el MISMO guid y reintentamos una vez.
            try:
                _post(app, "release", {"guid": guid, "indexerId": int(indexer_id)})
            except Exception as first:
                if "cache" not in str(first).lower():
                    raise
                _repopulate_release_cache(app, d)
                _post(app, "release", {"guid": guid, "indexerId": int(indexer_id)})
    except Exception as e:
        record_error("media", e, op="release_grab", guid=str(guid)[:60])
        return jsonify({"error": str(e)[:300]}), 502
    return jsonify({"ok": True})


def _repopulate_release_cache(app, d):
    """Re-lanza la búsqueda de releases en Sonarr/Radarr para que el guid vuelva a la caché.
    Reconstruye los params desde lo que el picker envía: kind='movie'→movieId; con `season`→
    seriesId+seasonNumber; si no, el `id` es el episodeId."""
    ident = d.get("id")
    if d.get("kind") == "movie":
        params = {"movieId": int(ident)}
    elif d.get("season") is not None:
        params = {"seriesId": int(ident), "seasonNumber": int(d["season"])}
    else:
        params = {"episodeId": int(ident)}
    _get_slow(app, "release", **params)


@media_bp.route("/library/<kind>/<int:item_id>", methods=["DELETE"])
def media_library_remove(kind, item_id):
    """Quita una serie/película de la biblioteca. Body: {delete_files, exclude}.

    `delete_files` va SEPARADO y por defecto en falso: dejar de seguir algo y borrar 40 GB de
    disco son decisiones distintas, y confundirlas solo puede salir mal en una dirección.
    `exclude` añade la obra a la lista de exclusión para que una lista de importación no la
    vuelva a meter sola; también opcional, porque es una decisión con memoria.
    """
    if kind not in KINDS:
        return jsonify({"error": f"kind inválido: {kind}"}), 400
    d = request.get_json(silent=True) or {}
    k = KINDS[kind]
    cfg = APPS[k["app"]]
    params = {
        "deleteFiles": str(bool(d.get("delete_files", False))).lower(),
        "addImportListExclusion": str(bool(d.get("exclude", False))).lower(),
    }
    try:
        r = http_requests.delete(f"{cfg['url']}/api/v3/{k['res']}/{item_id}",
                                 headers={"X-Api-Key": _apikey(cfg["app"])},
                                 params=params, timeout=60)
        # 404 = ya no estaba. Es el resultado que se pedía, no un fallo (idempotente).
        if r.status_code != 404:
            r.raise_for_status()
    except Exception as e:
        record_error("media", e, op="library_remove", kind=kind, id=item_id)
        return jsonify({"error": str(e)[:300]}), 502

    # El progreso de visionado se limpia SIEMPRE: si no, al volver a añadir la serie
    # reaparecerían marcas de episodios que ya no existen (pasó en anime, ver
    # [[project_continue_reading_fixes]]).
    prefix = f"{'series' if kind == 'series' else 'movie'}:{item_id}"
    with _prog_lock:
        prog = _prog_read()
        gone = [key for key in prog if key == prefix or key.startswith(prefix + ":")]
        if gone:
            for key in gone:
                prog.pop(key, None)
            _prog_write(prog)
    return jsonify({"ok": True, "progress_cleared": len(gone)})


@media_bp.route("/library")
def media_library():
    """Series + películas ya seguidas. Cada lista falla por separado a propósito: que Radarr
    esté caído no puede vaciar las series de Sonarr — un [] indistinguible de "no hay nada"
    es el error más caro del proyecto (ver la regla "falló ≠ no había")."""
    out = {"series": [], "movies": [], "errors": {}}
    for which, key, norm in (("sonarr", "series", _norm_series),
                             ("radarr", "movies", _norm_movie)):
        try:
            out[key] = sorted((norm(x) for x in _get(which, key if which == "sonarr" else "movie")),
                              key=lambda x: x["title"].lower())
        except Exception as e:
            record_error("media", e, op="library", app=which)
            out["errors"][which] = str(e)[:200]
    return jsonify(out)
