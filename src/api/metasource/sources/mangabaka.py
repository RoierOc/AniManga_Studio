"""Adapter MangaBaka — fuente PRIMARIA del Manga Hub (Fase 0).

api.mangabaka.org · REST público, sin auth. Es un AGREGADOR: cada serie ya incrusta los
IDs cruzados y ratings normalizados de AniList, MyAnimeList, MangaUpdates, Kitsu y
Shikimori (bloque `source`). Por eso NO llamamos a esas APIs en vivo: MangaBaka las trae.

Búsqueda: GET /v1/series/search?q=…  →  {status, pagination, data:[serie…]}
Detalle:  GET /v1/series/{id}        →  serie (posiblemente envuelta en {data:…})

El parseo es defensivo (.get encadenados): si MangaBaka cambia un campo, el peor caso es
un valor vacío que la cascada rellena desde otra fuente — nunca una excepción.
"""
from __future__ import annotations

from api.metasource import schema
from api.metasource.identity import canonical_id
from api.metasource.sources import base

NAME = "mangabaka"
_BASE = "https://api.mangabaka.org/v1"

# Mapa bloque `source` de MangaBaka → nuestras claves de `ids`.
_SOURCE_IDS = {
    "anilist": "anilist",
    "my_anime_list": "mal",
    "manga_updates": "mangaupdates",
    "kitsu": "kitsu",
    "shikimori": "shikimori",
}


def _cover(series: dict) -> str:
    # Preferir la variante DIMENSIONADA del CDN (x350@2 ≈ 700px): nítida y ligera. El `raw`
    # puede ser enorme (cientos de KB) y, servido a tamaño de tarjeta, ni mejora la calidad ni
    # compensa el peso. Solo cae a `raw` si no hubiera variantes.
    cov = series.get("cover") or {}
    for sz in ("x350", "x250"):
        node = cov.get(sz) or {}
        for dens in ("x2", "x3", "x1"):
            if node.get(dens):
                return node[dens]
    return (cov.get("raw") or {}).get("url") or ""


def _alt_titles(series: dict) -> list[str]:
    out = []
    rt = series.get("romanized_title")
    if rt:
        out.append(rt)
    sec = series.get("secondary_titles") or {}
    if isinstance(sec, dict):
        for entries in sec.values():
            for e in entries or []:
                t = (e or {}).get("title")
                if t:
                    out.append(t)
    return out


def _ids(series: dict) -> dict:
    ids = {}
    src = series.get("source") or {}
    for mb_key, our_key in _SOURCE_IDS.items():
        node = src.get(mb_key) or {}
        if node.get("id") is not None:
            ids[our_key] = node["id"]
    return ids


def _to_partial(series: dict) -> dict | None:
    sid = series.get("id")
    if sid is None:
        return None
    pop = (((series.get("popularity") or {}).get("global") or {}).get("current"))
    return {
        "id": canonical_id(NAME, sid),
        "ids": _ids(series),
        "title": series.get("title") or "",
        "title_native": series.get("native_title") or "",
        "titles_alt": _alt_titles(series),
        "type": schema.norm_type(series.get("type")),
        "status": schema.norm_status(series.get("status")),
        "year": series.get("year"),
        "cover": _cover(series),
        "synopsis": series.get("description") or "",
        "synopsis_lang": "en",
        "genres": [g for g in (series.get("genres") or []) if g],
        "tags": [t for t in (series.get("tags") or []) if isinstance(t, str)],
        "rating": schema.norm_rating(series.get("rating")),
        "popularity_rank": pop,
        "authors": [a for a in (series.get("authors") or []) if a],
        "artists": [a for a in (series.get("artists") or []) if a],
    }


def search(query: str, limit: int = 20, type_filter: str | None = None,
           genres: list[str] | None = None, status: str | None = None,
           page: int | None = None) -> list[dict]:
    params: dict = {"q": query, "limit": limit}
    if type_filter:                    # 'manga'|'manhwa'|'manhua'|'novel' — validado por la API
        params["type"] = type_filter
    if genres:                         # `genre` repetido = filtro AND (requests lo codifica así)
        params["genre"] = genres
    if status:
        params["status"] = status
    if page and page > 1:
        params["page"] = page
    body = base.get_json(f"{_BASE}/series/search", "mangabaka_search", params=params)
    if not body:                       # None = falló (logueado) · lista vacía = sin resultados
        return []
    data = body.get("data") if isinstance(body, dict) else body
    out = []
    for series in (data or [])[:limit]:
        p = _to_partial(series)
        if p:
            out.append(p)
    return out


def genres() -> list[dict]:
    """Catálogo de géneros de MangaBaka → [{label, value}]. [] si falla (logueado)."""
    body = base.get_json(f"{_BASE}/genres", "mangabaka_genres")
    data = (body or {}).get("data") if isinstance(body, dict) else None
    return [{"label": g.get("label"), "value": g.get("value")}
            for g in (data or []) if g.get("value")]


def fetch(ext_id: str) -> dict | None:
    body = base.get_json(f"{_BASE}/series/{ext_id}", "mangabaka_fetch")
    if not body:
        return None
    series = body.get("data") if isinstance(body, dict) and "data" in body else body
    return _to_partial(series or {})
