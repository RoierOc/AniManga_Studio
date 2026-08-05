"""Arte de alta resolución para Series y Películas, desde TMDB.

Por qué existe — MEDIDO sobre la biblioteca real (4-ago-2026):

| Fuente | Póster | Fondo |
|---|---|---|
| TVDB (lo que da Sonarr, series) | **680×1000** | **1920×1080** |
| TMDB (lo que da Radarr, películas) | 2000×3000 | 3840×2160 |

Es decir: **las series iban a un tercio de resolución que las películas**, y el hero se pinta A
SANGRE — en un monitor de 2560 ese fondo de 1920 se estira ×1,33 y se ve blando. Es exactamente el
caso de la regla de resolución máxima del proyecto.

Además, ni Sonarr ni Radarr tienen **logo** (el rótulo del título en PNG transparente), que es lo
que hace que el hero de Mi Anime parezca Crunchyroll y el de Cine pareciera una plantilla.

El trabajo pesado ya estaba resuelto en `anime.py::_tmdb_images` (elige el backdrop SIN texto, el
mejor logo PNG y el mejor póster). Aquí no se reimplementa: se reutiliza. Este módulo sólo aporta
el cruce con la biblioteca, la caché y el paralelismo.

**Fuera del camino crítico a propósito**: `/api/media/library` sigue tardando lo que tardaba y la
rejilla se pinta con el arte de Sonarr; esto llega después y mejora lo que ya se ve.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from flask import Blueprint, jsonify

from api.media import KINDS, _get
from api.observability import record_error
from api.runtime import cache_get, cache_set

media_art_bp = Blueprint("media_art", __name__)

# 7 días: el arte de una obra no cambia de una semana para otra, y lo caro es preguntar.
_TTL = 7 * 86400
# Seis en paralelo: TMDB aguanta de sobra y una biblioteca de 100 obras baja de ~100 viajes en
# serie a ~17. No más, porque el límite de TMDB es por ventana y no hay prisa (esto va detrás).
_WORKERS = 6


def _art_de(kind: str, tmdb_id: int) -> dict | None:
    """Arte de UNA obra, cacheado en disco. `None` = no se pudo preguntar (≠ «no tiene arte»)."""
    clave = f"{kind}:{tmdb_id}"
    en_cache = cache_get("media_art", clave, _TTL)
    if en_cache is not None:
        return en_cache
    from api.anime import _tmdb_images        # perezoso: anime.py es pesado y esto es opcional
    art = _tmdb_images(tmdb_id, "tv" if kind == "series" else "movie")
    if not any(art.values()):
        # Ni backdrop ni logo ni póster: puede ser que TMDB no tenga arte (legítimo) o que la
        # llamada fallara (`_tmdb_images` se traga la excepción). No se cachea un vacío para no
        # congelar durante una semana lo que quizá sólo fue un fallo de red.
        return None
    cache_set("media_art", clave, art, ttl=_TTL, max_entries=2000)
    return art


@media_art_bp.route("/art")
def media_art():
    """`{ "series:3": {poster, banner, logo}, … }` para toda la biblioteca.

    Sólo devuelve las claves que MEJORAN algo; una obra sin arte en TMDB no aparece, y la UI se
    queda con lo que ya tenía en vez de borrar una portada buena por una respuesta vacía.
    """
    obras, errors = [], {}
    for kind, k in KINDS.items():
        try:
            for x in _get(k["app"], k["res"]):
                if x.get("tmdbId"):
                    obras.append((kind, x["id"], x["tmdbId"]))
        except Exception as e:
            record_error("media", e, op="art_lib", kind=kind)
            errors[k["app"]] = str(e)[:200]

    out = {}
    if obras:
        with ThreadPoolExecutor(max_workers=_WORKERS) as pool:
            for (kind, item_id, _), art in zip(obras, pool.map(lambda o: _art_de(o[0], o[2]), obras)):
                if not art:
                    continue
                fila = {}
                # El póster sólo se sustituye si TMDB tiene uno: el de Sonarr ya es correcto, sólo
                # que más pequeño. Cambiarlo por nada sería empeorar.
                if art.get("poster"):
                    fila["poster"] = art["poster"]
                if art.get("backdrop"):
                    fila["banner"] = art["backdrop"]
                if art.get("logo"):
                    fila["logo"] = art["logo"]
                if fila:
                    out[f"{kind}:{item_id}"] = fila
    return jsonify({"art": out, "errors": errors})
