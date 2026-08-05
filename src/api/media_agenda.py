"""Agenda, cola e historial de Series y Películas — lo que Cine tenía en blanco frente a anime.

Módulo APARTE de `media.py` (que ya son 1100 líneas) siguiendo la regla del repo: bloque temático
nuevo = módulo propio. Lo que comparte con él son las tuberías (`_get`, `_norm_*`), no la lógica.

Las tres preguntas que Cine no sabía responder:

  - `/agenda`   ¿qué llega? — episodios que se emiten y películas que se estrenan/salen en digital.
  - `/queue`    ¿qué se está bajando AHORA? — hasta ahora una descarga desaparecía al lanzarla.
  - `/history`  ¿qué he visto y qué se ha bajado? — dos lentes de la misma pregunta.

⚠️ **`unmonitored=true` no es opcional aquí.** Esta app da de alta la biblioteca SIN monitorizar a
propósito (`_monitor_target` en media.py sólo abre la puerta a lo que pides descargar), y el
calendario de Sonarr/Radarr filtra por monitorizado por defecto. MEDIDO con la biblioteca real: el
mismo rango devuelve **0 entradas** con el valor por defecto y **4 + 2** con `unmonitored=true`. Sin
esa bandera la pestaña estaría vacía para siempre y se leería como «no hay estrenos» cuando lo que
pasa es que no preguntamos bien.
"""
from __future__ import annotations

import time as _time
from datetime import date, datetime, timedelta, timezone

from flask import Blueprint, jsonify, request

from api.media import _get, _delete, _img, _norm_movie, _norm_series, _prog_read
from api.observability import record_error

media_agenda_bp = Blueprint("media_agenda", __name__)

# Fechas de estreno de una película, de la más útil a la más lejana de tu disco duro. Se emite una
# entrada por fecha que caiga en la ventana en vez de elegir «la buena»: que un estreno en cines y
# su salida digital son eventos distintos lo sabe el usuario, no una heurística nuestra.
_MOVIE_DATES = [
    ("digitalRelease", "Digital"),
    ("physicalRelease", "Físico"),
    ("inCinemas", "En cines"),
]


def _ts(iso: str) -> int:
    """ISO-8601 de Sonarr/Radarr → epoch. Devuelve 0 si no hay fecha (no es un fallo: hay
    episodios anunciados sin fecha de emisión)."""
    if not iso:
        return 0
    try:
        return int(datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp())
    except ValueError:
        return 0


@media_agenda_bp.route("/agenda")
def media_agenda():
    """Qué se emite y qué se estrena, en una sola lista ordenada por fecha.

    `back` mira hacia atrás (lo de esta semana que ya salió sigue siendo noticia si no lo tienes)
    y `days` hacia delante. Los errores viajan APARTE de los datos: con Sonarr caído la lista de
    películas es válida, y decir «no hay nada» sería mentira.
    """
    back = max(0, min(60, request.args.get("back", 7, type=int)))
    days = max(1, min(120, request.args.get("days", 21, type=int)))
    hoy = date.today()
    desde, hasta = hoy - timedelta(days=back), hoy + timedelta(days=days)
    params = {"start": desde.isoformat(), "end": hasta.isoformat(), "unmonitored": "true"}

    items, errors = [], {}

    try:
        for e in _get("sonarr", "calendar", includeSeries="true", **params):
            serie = e.get("series") or {}
            meta = _norm_series(serie) if serie else {}
            items.append({
                "id": f"ep:{e.get('id')}",
                "kind": "series",
                "series_id": e.get("seriesId"),
                "title": meta.get("title") or serie.get("title") or "",
                "poster": meta.get("poster") or "",
                "banner": meta.get("banner") or "",
                "season": e.get("seasonNumber"),
                "num": e.get("episodeNumber"),
                "ep_title": e.get("title") or "",
                "event": "Episodio",
                "ts": _ts(e.get("airDateUtc") or ""),
                "have": bool(e.get("hasFile")),
            })
    except Exception as exc:
        record_error("media", exc, op="agenda", app="sonarr")
        errors["sonarr"] = str(exc)[:200]

    try:
        for m in _get("radarr", "calendar", **params):
            meta = _norm_movie(m)
            for campo, etiqueta in _MOVIE_DATES:
                ts = _ts(m.get(campo) or "")
                if not ts or not (desde <= datetime.fromtimestamp(ts, timezone.utc).date() <= hasta):
                    continue
                items.append({
                    "id": f"mv:{m.get('id')}:{campo}",
                    "kind": "movie",
                    "movie_id": m.get("id"),
                    # La FECHA tal cual, sin instante. Un estreno en físico no ocurre a una hora:
                    # Radarr manda `2026-08-11T00:00:00Z` y pasarlo por el reloj local (UTC-5) lo
                    # movía al día ANTERIOR a las 19:00 — la agenda decía «lunes 10» donde Radarr
                    # dice «martes 11». Quien pinte esto agrupa por `day`, no por `ts`.
                    "day": (m.get(campo) or "")[:10],
                    "title": meta["title"],
                    "poster": meta["poster"],
                    "banner": meta["banner"],
                    "event": etiqueta,
                    "ts": ts,
                    "have": bool(m.get("hasFile")),
                })
    except Exception as exc:
        record_error("media", exc, op="agenda", app="radarr")
        errors["radarr"] = str(exc)[:200]

    items.sort(key=lambda x: x["ts"])
    return jsonify({"items": items, "errors": errors, "from": desde.isoformat(), "to": hasta.isoformat()})


def _queue_records(which: str) -> list:
    """`queue` pagina (por defecto 20). Se pide una página grande de una vez: son descargas en
    curso, nunca van a ser miles, y paginar en la UI sería complejidad por nada."""
    d = _get(which, "queue", pageSize=200, includeSeries="true", includeEpisode="true",
             includeMovie="true")
    return d.get("records", d if isinstance(d, list) else [])


@media_agenda_bp.route("/queue")
def media_queue():
    """Lo que Sonarr/Radarr están bajando ahora mismo, con porcentaje real.

    El porcentaje sale de `size`/`sizeleft`, no de un campo `progress`: Sonarr no manda uno y
    calcularlo aquí evita que la UI invente. Un elemento sin `size` conocido se marca con
    `pct: null`, que la UI dibuja como indeterminado — un 0 % sería una afirmación falsa.
    """
    items, errors = [], {}
    for which in ("sonarr", "radarr"):
        try:
            for r in _queue_records(which):
                size, left = r.get("size") or 0, r.get("sizeleft")
                pct = None
                if size and left is not None:
                    pct = max(0.0, min(100.0, (1 - left / size) * 100))
                serie = r.get("series") or {}
                ep = r.get("episode") or {}
                peli = r.get("movie") or {}
                obra = serie.get("title") or peli.get("title") or r.get("title") or "?"
                detalle = ""
                if ep:
                    detalle = f"{ep.get('seasonNumber')}x{str(ep.get('episodeNumber') or 0).zfill(2)}"
                    if ep.get("title"):
                        detalle += f" · {ep['title']}"
                items.append({
                    "id": r.get("id"),
                    "app": which,
                    "kind": "series" if which == "sonarr" else "movie",
                    "title": obra,
                    "detail": detalle,
                    "poster": _img(serie or peli, "poster"),
                    "release": r.get("title") or "",
                    "size": size,
                    "sizeleft": left,
                    "pct": pct,
                    "status": r.get("status") or "",
                    # `trackedDownloadState` es lo que distingue «bajando» de «importando»: sin él
                    # una descarga al 100 % parece colgada durante el import.
                    "state": r.get("trackedDownloadState") or "",
                    "timeleft": r.get("timeleft") or "",
                    "indexer": r.get("indexer") or "",
                    # Sonarr mete los avisos en una lista de mensajes; el primero es el que importa.
                    "warning": (r.get("statusMessages") or [{}])[0].get("title", "")
                    if r.get("statusMessages") else (r.get("errorMessage") or ""),
                })
        except Exception as exc:
            record_error("media", exc, op="queue", app=which)
            errors[which] = str(exc)[:200]

    items.sort(key=lambda x: (x["pct"] is None, -(x["pct"] or 0)))
    return jsonify({"items": items, "errors": errors})


@media_agenda_bp.route("/queue/remove", methods=["POST"])
def media_queue_remove():
    """Cancelar una descarga. Sin esto, una descarga atascada no se puede tocar desde la app.

    `removeFromClient` va a `true` porque dejarla en qBittorrent sería cancelar sólo de boquilla;
    `blocklist` es opcional y es cosa distinta: no volver a coger ESA release.
    """
    d = request.get_json(silent=True) or {}
    which = d.get("app")
    if which not in ("sonarr", "radarr") or not d.get("id"):
        return jsonify({"error": "app e id requeridos"}), 400
    try:
        _delete(which, f"queue/{int(d['id'])}", removeFromClient="true",
                blocklist="true" if d.get("blocklist") else "false")
    except Exception as exc:
        record_error("media", exc, op="queue_remove", app=which)
        return jsonify({"error": str(exc)[:200]}), 502
    return jsonify({"ok": True})


@media_agenda_bp.route("/history")
def media_history():
    """Dos historiales, porque son dos preguntas: qué he VISTO y qué se ha BAJADO.

    Lo visto sale del fichero de progreso local (la verdad de este dominio la tenemos nosotros,
    no Sonarr); lo bajado, del historial de Sonarr/Radarr. Se devuelven por separado a propósito:
    fundirlos en una sola lista mezcla «lo que hiciste» con «lo que hizo la máquina».
    """
    limit = max(1, min(200, request.args.get("limit", 50, type=int)))
    out = {"watched": [], "grabs": [], "errors": {}}

    # ── Visto ────────────────────────────────────────────────────────────────
    prog = _prog_read()
    if prog:
        por_serie: dict[int, dict] = {}
        pelis: dict[int, dict] = {}
        for key, p in prog.items():
            partes = key.split(":")
            if len(partes) == 3 and partes[0] == "series":
                por_serie.setdefault(int(partes[1]), {})[int(partes[2])] = p
            elif len(partes) == 2 and partes[0] == "movie":
                pelis[int(partes[1])] = p
        try:
            series = {s["id"]: _norm_series(s) for s in _get("sonarr", "series")} if por_serie else {}
        except Exception as exc:
            record_error("media", exc, op="history_series")
            series, out["errors"]["sonarr"] = {}, str(exc)[:200]
        for sid, eps_prog in por_serie.items():
            meta = series.get(sid)
            if not meta:
                continue    # la serie ya no está en Sonarr, pero su progreso sí: no se inventa título
            try:
                eps = {e["id"]: e for e in _get("sonarr", "episode", seriesId=sid)}
            except Exception as exc:
                record_error("media", exc, op="history_eps", series=sid)
                continue
            for eid, p in eps_prog.items():
                e = eps.get(eid)
                if not e or not p.get("at"):
                    continue
                out["watched"].append({
                    "id": f"series:{sid}:{eid}", "kind": "series", "series_id": sid,
                    "title": meta["title"], "poster": meta["poster"],
                    "season": e.get("seasonNumber"), "num": e.get("episodeNumber"),
                    "ep_title": e.get("title") or "",
                    "still": _img(e, "screenshot") or meta["banner"],
                    "at": p.get("at", 0), "pos": p.get("pos", 0),
                    "duration": p.get("duration", 0), "watched": bool(p.get("watched")),
                })
        if pelis:
            try:
                movies = {m["id"]: _norm_movie(m) for m in _get("radarr", "movie")}
            except Exception as exc:
                record_error("media", exc, op="history_movies")
                movies, out["errors"]["radarr"] = {}, str(exc)[:200]
            for mid, p in pelis.items():
                meta = movies.get(mid)
                if not meta or not p.get("at"):
                    continue
                out["watched"].append({
                    "id": f"movie:{mid}", "kind": "movie", "movie_id": mid,
                    "title": meta["title"], "poster": meta["poster"],
                    "still": meta["banner"], "at": p.get("at", 0), "pos": p.get("pos", 0),
                    "duration": p.get("duration", 0), "watched": bool(p.get("watched")),
                })
        out["watched"].sort(key=lambda x: x["at"], reverse=True)
        out["watched"] = out["watched"][:limit]

    # ── Bajado ───────────────────────────────────────────────────────────────
    # Sólo los eventos que significan algo para ti: cogido (1) e importado (3). El historial crudo
    # trae además borrados, reintentos y renombrados, que son ruido de fontanería.
    #
    # ⚠️ El filtro va en la PETICIÓN, no en un `if` al recibir. MEDIDO en tu Sonarr: de los 50
    # registros más recientes, 28 son `episodeFileDeleted`; filtrando en cliente una página de 10
    # se quedaba en CERO y la pestaña decía «no hay descargas» teniendo 291 registros detrás.
    _EVENTOS = {"grabbed": "Cogido", "downloadFolderImported": "Importado"}
    for which in ("sonarr", "radarr"):
        try:
            d = _get(which, "history", pageSize=limit, page=1, eventType=[1, 3],
                     sortKey="date", sortDirection="descending")
            for r in d.get("records", []):
                ev = _EVENTOS.get(r.get("eventType"))
                if not ev:
                    continue
                data = r.get("data") or {}
                out["grabs"].append({
                    "id": f"{which}:{r.get('id')}",
                    "kind": "series" if which == "sonarr" else "movie",
                    "title": (r.get("sourceTitle") or "").strip(),
                    "event": ev,
                    "quality": ((r.get("quality") or {}).get("quality") or {}).get("name", ""),
                    "indexer": data.get("indexer") or "",
                    "at": _ts(r.get("date") or ""),
                })
        except Exception as exc:
            record_error("media", exc, op="history_grabs", app=which)
            out["errors"][which] = str(exc)[:200]
    out["grabs"].sort(key=lambda x: x["at"], reverse=True)
    out["grabs"] = out["grabs"][:limit]
    out["now"] = int(_time.time())
    return jsonify(out)
