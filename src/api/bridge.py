#!/usr/bin/env python3
"""Puente manga ⇄ anime — la única cosa que esta app puede hacer y ningún Plex ni Mihon puede.

Las dos bibliotecas llevan años bajo el mismo techo sin hablarse. AniList YA trae la relación en
la misma consulta que la app hacía para la franquicia, y allí se descartaba a propósito:
`_FRANCHISE_REL` de `anime.py` ignora `ADAPTATION` y `SOURCE` porque para ordenar temporadas son
ruido. Para lo que hay aquí son exactamente el dato.

Responde a las dos preguntas que uno se hace de verdad:
  · leyendo un manga → «¿tiene anime? ¿lo tengo? ¿está emitiéndose?»
  · terminando un anime → «¿por dónde sigo en el manga? ¿lo tengo?»

**Por qué capítulo va el anime** lo publica MangaUpdates y sólo MangaUpdates — AniList y MangaDex
no lo tienen — en un campo de texto libre que aquí se parsea (`mangaupdates.anime_coverage`). Es
LA pregunta de después de una temporada, así que vale la llamada extra. Cuando no se puede saber,
la frase simplemente no aparece: nunca se rellena con un número plausible.

Endpoints (prefijo /api/bridge):
  GET /counterpart?al_id=<int>&from=manga|anime → contrapartes + veredicto + si ya las tienes
"""
from pathlib import Path
import json as _json

from flask import Blueprint, jsonify, request

from api.resilient_http import http as http_requests
from api.observability import record_error
from api.anilist import _cover as _al_cover  # AniList: extraLarge (460x650) antes que large (230x325)
from api.runtime import cache_get, cache_set, manga_dir

bridge_bp = Blueprint("bridge", __name__)

_AL = "https://graphql.anilist.co"
_TTL = 24 * 3600          # un día: una adaptación no se anuncia dos veces en una tarde

# `type` es el del NODO relacionado, no el de la obra: pidiendo un manga, su adaptación es ANIME.
_Q = """query($id:Int,$type:MediaType){Media(id:$id,type:$type){
  id status chapters episodes
  title{romaji english}
  relations{edges{relationType node{
    id type format status episodes chapters seasonYear
    startDate{year}
    title{romaji english}
    coverImage{ extraLarge large medium }
  }}}
}}"""

# ADAPTATION = «de esto salió aquello» · SOURCE = «esto salió de aquello». AniList no siempre
# etiqueta en el sentido que uno espera (hay manga cuya adaptación aparece como SOURCE del anime
# y viceversa), así que se aceptan las dos y se filtra por el TIPO del nodo, que sí es fiable.
_REL = {"ADAPTATION", "SOURCE"}
# Artículo contraído incluido: sin él salía «Sale del novela ligera», que es de las cosas que
# hacen que una ficha entera parezca generada por una máquina.
_KIND_ES = {"MANGA": "del manga", "NOVEL": "de la novela ligera", "ONE_SHOT": "del one-shot"}


def _anime_al_ids() -> set | None:
    """al_id de las series en la biblioteca de anime, o `None` si no se pudo leer.

    `None` y `set()` no son lo mismo: si el fichero está ilegible, decir «no la tienes» es
    mentira, y la UI prefiere callarse (regla «falló ≠ no había»).
    """
    try:
        from api.anime import _lib_read
        return {int(a["al_id"]) for a in _lib_read().values() if a.get("al_id")}
    except Exception as e:
        record_error("bridge", e, op="anime_ids")
        return None


def _manga_al_ids() -> set | None:
    """al_id de la biblioteca de manga: los descargados (`.identity.json` por carpeta) más los
    seguidos sin descargar (`local_library.json`). Un manga que sigues cuenta como «lo tienes»."""
    try:
        root = Path(manga_dir())
        ids = set()
        for p in root.glob("*/.identity.json"):
            try:
                al = _json.loads(p.read_text(encoding="utf-8")).get("al_id")
            except Exception as e:
                # Una identidad ilegible es UNA obra que no sabremos cruzar, no un fallo del
                # barrido: se registra y se sigue con las demás.
                record_error("bridge", e, op="read_identity", folder=p.parent.name)
                continue
            if al:
                ids.add(int(al))

        lib = root / "local_library.json"
        if lib.exists():
            def walk(o):
                if isinstance(o, dict):
                    al = o.get("al_id") or o.get("anilist")
                    if al:
                        try:
                            ids.add(int(al))
                        except (TypeError, ValueError):
                            pass
                    for v in o.values():
                        walk(v)
                elif isinstance(o, list):
                    for v in o:
                        walk(v)
            walk(_json.loads(lib.read_text(encoding="utf-8")))
        return ids
    except Exception as e:
        record_error("bridge", e, op="manga_ids")
        return None


def _titulo(n: dict) -> str:
    t = n.get("title") or {}
    return t.get("english") or t.get("romaji") or ""


def _obra_original(items: list) -> dict | None:
    """La serialización de la que salió el anime, entre todo lo que AniList cuelga de él.

    Medido con One Piece: la relación trae «Romance Dawn» (one-shot de 1996), «Wanted!» (recopilatorio
    de one-shots) y el manga de verdad. Ordenar por año dejaba de portada el one-shot, y decir «One
    Piece sale de un one-shot de 1 capítulo» es exactamente el tipo de dato que hace desconfiar de
    toda la ficha. Los one-shots se apartan y de lo que queda manda el más antiguo, que es el
    original — las secuelas y spin-offs vienen después por definición.
    """
    serios = [i for i in items if i["format"] != "ONE_SHOT"] or items
    return min(serios, key=lambda i: (i["year"] or 9999), default=None)


def _cobertura(titulo_manga: str, variantes: list) -> dict | None:
    """Por qué capítulo del manga va la adaptación. `None` = no se pudo saber.

    Lo publica MangaUpdates y sólo MangaUpdates (ver `metasource/sources/mangaupdates.py`):
    ni AniList ni MangaDex lo tienen. Es la respuesta a la pregunta real de después de una
    temporada, así que vale la llamada extra — que además va cacheada 24 h con el resto.
    """
    if not titulo_manga:
        return None
    try:
        from api.metasource.sources import mangaupdates as MU
        return MU.anime_coverage(titulo_manga, [v for v in variantes if v])
    except Exception as e:
        record_error("bridge", e, op="coverage", titulo=titulo_manga[:60])
        return None


def _frase_cobertura(cov: dict | None) -> str:
    """La frase de «por dónde sigo», o vacía si el dato no la sostiene.

    `latest` de MangaUpdates cuenta capítulos TRADUCIDOS, no publicados en Japón, así que va por
    detrás: medido con Medalist, el anime llega al 28 y MangaUpdates dice que el manga va por el
    26. Restar ahí daría «te quedan -2». Cuando el manga no va por delante, se dice sólo dónde
    acaba el anime — que sigue siendo el dato útil — y no se inventa el resto.
    """
    if not cov:
        return ""
    fin = cov["ends_at"]
    fin_txt = f"{fin:g}"
    latest = cov.get("latest")
    if latest and latest > fin:
        return (f" El anime llega hasta el capítulo {fin_txt} y el manga va por el {latest}: "
                f"te quedan {latest - int(fin)} por leer.")
    return f" El anime llega hasta el capítulo {fin_txt}."


def _veredicto(origen: str, obra: dict, items: list, cov: dict | None = None) -> str:
    """Una frase, la que se pinta en la ficha. Vacía si no hay nada honesto que decir.

    Sólo afirma lo que el dato sostiene. En particular «el manga sigue» se dice cuando el manga
    está RELEASING y TODAS las adaptaciones han terminado — ahí sí es seguro que la historia
    continúa donde el anime la dejó, aunque no sepamos en qué capítulo exacto.
    """
    if not items:
        return ""
    if origen == "manga":
        emitiendo = [i for i in items if i["status"] == "RELEASING"]
        if emitiendo:
            return (f"Su anime se está emitiendo ahora mismo: "
                    f"{emitiendo[0]['title']}.{_frase_cobertura(cov)}")
        pronto = [i for i in items if i["status"] == "NOT_YET_RELEASED"]
        terminadas = [i for i in items if i["status"] == "FINISHED"]
        if terminadas and obra.get("status") == "RELEASING":
            n = len(terminadas)
            temporadas = "1 temporada" if n == 1 else f"{n} temporadas"
            extra = " Hay otra anunciada." if pronto else ""
            return (f"El anime tiene {temporadas} y ya terminó, pero el manga sigue "
                    f"publicándose.{_frase_cobertura(cov)}{extra}")
        if pronto and not terminadas:
            return f"Tiene anime anunciado: {pronto[0]['title']}."
        return f"Tiene anime: {items[0]['title']}.{_frase_cobertura(cov)}"
    # origen == "anime"
    src = _obra_original(items)
    if not src:
        return ""
    tipo = _KIND_ES.get(src["format"], "del manga")
    n = src.get("chapters")
    caps = f" · {n} capítulo{'' if n == 1 else 's'}" if n else ""
    if src["status"] == "RELEASING":
        return f"Sale {tipo} «{src['title']}», que sigue publicándose{caps}.{_frase_cobertura(cov)}"
    return f"Sale {tipo} «{src['title']}»{caps}.{_frase_cobertura(cov)}"


@bridge_bp.route("/counterpart")
def counterpart():
    """Contraparte de una obra en el OTRO dominio. `from=manga` devuelve sus animes; `from=anime`,
    el manga/novela del que salió."""
    try:
        al_id = int(request.args.get("al_id") or 0)
    except (TypeError, ValueError):
        al_id = 0
    origen = request.args.get("from") or "manga"
    if not al_id or origen not in ("manga", "anime"):
        return jsonify({"error": "al_id (int) y from=manga|anime son obligatorios"}), 400

    ck = f"{origen}:{al_id}"
    data = cache_get("bridge", ck, _TTL)
    if data is None:
        try:
            r = http_requests.post(_AL, json={
                "query": _Q,
                "variables": {"id": al_id, "type": "MANGA" if origen == "manga" else "ANIME"},
            }, timeout=20)
            r.raise_for_status()
            obra = (r.json().get("data") or {}).get("Media")
            if not obra:
                # La obra no está en AniList: eso es un vacío legítimo, no un fallo.
                return jsonify({"counterparts": [], "verdict": "", "known": False})
        except Exception as e:
            record_error("bridge", e, op="counterpart", al_id=al_id, origen=origen)
            # 502 y no una lista vacía: «no pude preguntar» no puede leerse como «no tiene anime».
            return jsonify({"error": str(e)[:200]}), 502

        # Del otro dominio: pidiendo un manga queremos ANIME, y al revés.
        quiero = "ANIME" if origen == "manga" else "MANGA"
        items = []
        for e in (obra.get("relations") or {}).get("edges") or []:
            n = e.get("node") or {}
            if e.get("relationType") not in _REL or n.get("type") != quiero:
                continue
            tn = n.get("title") or {}
            items.append({
                "al_id": n.get("id"),
                "title": _titulo(n),
                # Las dos variantes viajan para el emparejamiento con MangaUpdates, que indexa por
                # el título japonés: buscar «Demon Slayer: Kimetsu no Yaiba» no casa con
                # «Kimetsu no Yaiba» y la cobertura se perdía en las obras más conocidas.
                "titles": [t for t in (tn.get("romaji"), tn.get("english")) if t],
                "media": n.get("format") or n.get("type") or "",
                "format": n.get("format") or "",
                "status": n.get("status") or "",
                "episodes": n.get("episodes"),
                "chapters": n.get("chapters"),
                "year": n.get("seasonYear") or (n.get("startDate") or {}).get("year"),
                "cover": _al_cover(n),
            })
        # Por año: las temporadas salen en orden de emisión, no en el que devuelva AniList.
        items.sort(key=lambda i: (i["year"] or 9999))

        # ¿Por dónde va la adaptación? El título del MANGA es la clave: viniendo de un manga es
        # el de la obra pedida; viniendo de un anime, el de su obra original.
        t = obra.get("title") or {}
        if origen == "manga":
            cov = _cobertura(t.get("romaji") or t.get("english") or "",
                             [t.get("romaji"), t.get("english")])
        else:
            orig0 = _obra_original(items)
            cov = _cobertura(orig0["title"], orig0["titles"]) if orig0 else None

        if origen == "anime":
            # Viniendo de un anime, la obra original va PRIMERA aunque no sea la más antigua:
            # es la que se está buscando, el resto (one-shots, spin-offs) es contexto.
            orig = _obra_original(items)
            if orig:
                items.sort(key=lambda i: i is not orig)
        data = {"counterparts": items, "known": True, "coverage": cov,
                "verdict": _veredicto(origen, obra, items, cov)}
        cache_set("bridge", ck, data, ttl=_TTL)

    # El cruce con la biblioteca NO se cachea: cambia cada vez que añades algo.
    tengo = _anime_al_ids() if origen == "manga" else _manga_al_ids()
    out = dict(data)
    out["counterparts"] = [
        # `in_library` AUSENTE (no `false`) si no se pudo leer la biblioteca: ver `_anime_al_ids`.
        {**i, **({"in_library": i["al_id"] in tengo} if tengo is not None else {})}
        for i in data["counterparts"]
    ]
    return jsonify(out)
