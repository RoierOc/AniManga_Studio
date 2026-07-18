"""Manga Hub · capa meta-source (Fase 0) — API pública del paquete.

`search_works()` y `get_work()` son lo único que consume el blueprint `discovery.py`. Todo
lo demás (adapters, cascada, identidad) es interno. Filosofía «mejor versión» a nivel obra.

Eficiencia (decisión de diseño):
  · BÚSQUEDA = 1 sola llamada upstream (MangaBaka, que ya es el agregador) + caché 6 h.
    Nada de fan-out a varias fuentes por búsqueda.
  · FICHA = MangaBaka + enriquecimiento MangaUpdates bajo demanda (≤ 2 llamadas) + caché 7 d.
  · Nunca descarga portadas (sólo URLs; el frontend usa el proxy /api/img).

Fail-safe: los adapters devuelven [] / None si fallan (y lo registran en `record_error`);
la cascada arma la Obra con lo que haya. «Falló» jamás produce el mismo vacío que «no había».
"""
from __future__ import annotations

import os
import time

from api.runtime import cache_get, cache_set
from api.metasource import cascade, schema
from api.metasource.sources import base, mangabaka, mangaupdates

# Bandera de rollback en caliente: MANGA_HUB=0 apaga el hub sin tocar código.
ENABLED = os.getenv("MANGA_HUB", "1") == "1"

_SEARCH_TTL = 6 * 3600          # los resultados de búsqueda cambian poco
_WORK_TTL = 7 * 24 * 3600       # los metadatos de una obra cambian muy lento
_TREND_TTL = 12 * 3600          # los rieles de tendencia cambian lento (recalcular 2×/día basta)
_SEARCH_NS = "disco_search"
_WORK_NS = "disco_work"
_TREND_NS = "disco_trending"

# Tipos válidos para los rieles de tendencia (enum del esquema Obra).
TREND_TYPES = ("manhwa", "manga", "manhua", "novel")


def _norm_query(q: str) -> str:
    return " ".join((q or "").lower().split())


def search_works(query: str, type_filter: str | None = None, limit: int = 20) -> list[dict]:
    """Busca obras y devuelve WorkSummary deduplicados (MangaBaka ya deduplica por obra)."""
    q = (query or "").strip()
    if not q:
        return []
    limit = max(1, min(limit, 50))
    key = f"{_norm_query(q)}|{type_filter or ''}|{limit}"
    cached = cache_get(_SEARCH_NS, key, ttl=_SEARCH_TTL)
    if cached is not None:
        base.log_timing("search", 0, q=q, cache="hit", n=len(cached))
        return cached

    t0 = time.monotonic()
    hits = mangabaka.search(q, limit)                      # 1 llamada upstream
    works = []
    for partial in hits:
        work = cascade.combine({mangabaka.NAME: partial})   # normaliza + deriva readable
        if type_filter and work.get("type") != type_filter:
            continue
        works.append(schema.to_summary(work))
    cache_set(_SEARCH_NS, key, works, ttl=_SEARCH_TTL, max_entries=300)
    base.log_timing("search", (time.monotonic() - t0) * 1000, q=q, cache="miss", n=len(works))
    return works


def trending_works(work_type: str = "manhwa", limit: int = 20) -> list[dict]:
    """Riel de descubrimiento: obras de un tipo ordenadas por popularidad (1 = más popular).
    MangaBaka no tiene endpoint de 'browse', pero una búsqueda vacía con `type` devuelve un
    conjunto navegable; lo ordenamos por su rank de popularidad global. 1 sola llamada + caché 12 h."""
    if work_type not in TREND_TYPES:
        return []
    limit = max(1, min(limit, 40))
    key = f"{work_type}|{limit}"
    cached = cache_get(_TREND_NS, key, ttl=_TREND_TTL)
    if cached is not None:
        base.log_timing("trending", 0, type=work_type, cache="hit", n=len(cached))
        return cached

    t0 = time.monotonic()
    hits = mangabaka.search("", limit=max(limit * 2, 40), type_filter=work_type)
    hits.sort(key=lambda p: p.get("popularity_rank") or 10 ** 9)     # rank asc; sin rank al final
    works = [schema.to_summary(cascade.combine({mangabaka.NAME: p})) for p in hits[:limit]]
    cache_set(_TREND_NS, key, works, ttl=_TREND_TTL, max_entries=len(TREND_TYPES) * 2)
    base.log_timing("trending", (time.monotonic() - t0) * 1000, type=work_type, cache="miss", n=len(works))
    return works


_META_NS = "disco_meta"
_BROWSE_NS = "disco_browse"
_GENRES_TTL = 7 * 24 * 3600
_BROWSE_TTL = 3 * 3600
SORTS = ("popularity", "rating", "year")


def list_genres() -> list[dict]:
    """Catálogo de géneros para el filtro (cacheado 7 días)."""
    cached = cache_get(_META_NS, "genres", ttl=_GENRES_TTL)
    if cached is not None:
        return cached
    g = mangabaka.genres()
    if g:                                  # no cachees una lista vacía por un fallo puntual
        cache_set(_META_NS, "genres", g, ttl=_GENRES_TTL, max_entries=4)
    return g


def _sort_key(sort: str):
    if sort == "rating":
        return lambda p: -(p.get("rating") or 0)
    if sort == "year":
        return lambda p: -(p.get("year") or 0)
    return lambda p: p.get("popularity_rank") or 10 ** 9     # popularidad: rank asc (1 = top)


def browse(q: str = "", work_type: str = "", genres: list[str] | None = None, status: str = "",
           sort: str = "popularity", page: int = 1, limit: int = 24) -> dict:
    """Explorar estilo AniList: populares filtrables por texto/tipo/género/estado y ordenables.
    Devuelve {works, page, has_more}. 1 llamada a MangaBaka + caché 3 h por combinación."""
    q = (q or "").strip()
    work_type = work_type if work_type in TREND_TYPES else ""
    sort = sort if sort in SORTS else "popularity"
    genres = [g for g in (genres or []) if g][:6]
    limit = max(1, min(limit, 40))
    page = max(1, page)
    key = f"{_norm_query(q)}|{work_type}|{','.join(sorted(genres))}|{status}|{sort}|{page}|{limit}"
    cached = cache_get(_BROWSE_NS, key, ttl=_BROWSE_TTL)
    if cached is not None:
        base.log_timing("browse", 0, cache="hit", n=len(cached.get("works", [])))
        return cached

    t0 = time.monotonic()
    hits = mangabaka.search(q, limit=limit, type_filter=work_type or None,
                            genres=genres or None, status=status or None, page=page)
    has_more = len(hits) >= limit                    # ¿probablemente hay otra página?
    hits.sort(key=_sort_key(sort))
    works = [schema.to_summary(cascade.combine({mangabaka.NAME: p})) for p in hits]
    out = {"works": works, "page": page, "has_more": has_more}
    cache_set(_BROWSE_NS, key, out, ttl=_BROWSE_TTL, max_entries=120)
    base.log_timing("browse", (time.monotonic() - t0) * 1000, cache="miss", n=len(works))
    return out


def get_work(work_id: str, enrich: bool = True) -> dict | None:
    """Ficha completa de una obra (cascada de fuentes). None si no existe en ninguna."""
    if not work_id:
        return None
    cached = cache_get(_WORK_NS, work_id, ttl=_WORK_TTL)
    if cached is not None:
        base.log_timing("work", 0, id=work_id, cache="hit")
        return cached

    t0 = time.monotonic()
    partials: dict[str, dict] = {}
    prefix, _, ext = work_id.partition(":")

    if prefix == "mb":
        p = mangabaka.fetch(ext)
        if p:
            partials[mangabaka.NAME] = p
    elif prefix == "mu":
        p = mangaupdates.fetch(ext)
        if p:
            partials[mangaupdates.NAME] = p

    if not partials:
        base.log_timing("work", (time.monotonic() - t0) * 1000, id=work_id, cache="miss", found=0)
        return None

    # Enriquecimiento MangaUpdates: taxonomía de tipo + votos, verificado por título.
    if enrich and mangabaka.NAME in partials and mangaupdates.NAME not in partials:
        mb = partials[mangabaka.NAME]
        variants = [mb.get("title"), mb.get("title_native"), *(mb.get("titles_alt") or [])]
        variants = [v for v in variants if v]
        mu_id = (mb.get("ids") or {}).get("mangaupdates")
        mu = None
        if isinstance(mu_id, int):                          # id numérico usable directo
            mu = mangaupdates.fetch(str(mu_id))
        if not mu and mb.get("title"):                      # si no, resolver por título (fuzzy)
            mu = mangaupdates.enrich_for(mb["title"], variants)
        if mu:
            partials[mangaupdates.NAME] = mu

    work = cascade.combine(partials)
    cache_set(_WORK_NS, work_id, work, ttl=_WORK_TTL, max_entries=500)
    base.log_timing("work", (time.monotonic() - t0) * 1000,
                    id=work_id, cache="miss", srcs="+".join(partials))
    return work
