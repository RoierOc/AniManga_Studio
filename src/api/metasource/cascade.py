"""Motor de fusión «mejor versión» a nivel de campo (Fase 0).

Recibe parciales por fuente ({"mangabaka": {...}, "mangaupdates": {...}}) y produce una
Obra. Para cada campo, la primera fuente (según prioridad) con valor NO vacío gana; se
anota la procedencia. Listas (géneros/tags/títulos alt) se UNEN. Los IDs se combinan.

Regla de oro (falló ≠ vacío): una fuente que reventó simplemente no aparece en `partials`
(su adapter devolvió None y lo registró). Aquí NUNCA un valor vacío pisa uno lleno, así que
la Obra sale completa desde las demás fuentes aunque una caiga.
"""
from __future__ import annotations

import time

from api.metasource import schema

# Orden de preferencia por campo. MangaBaka es la columna vertebral (ya agrega AniList/MAL/
# Kitsu), MangaUpdates manda en taxonomía de tipo y votos, MangaDex en portada (Fase 2).
FIELD_PRIORITY = {
    "id":              ["mangabaka", "mangaupdates", "mangadex"],
    "title":           ["mangabaka", "mangaupdates", "mangadex"],
    "title_native":    ["mangabaka", "mangadex", "mangaupdates"],
    "type":            ["mangaupdates", "mangabaka"],
    "status":          ["mangabaka", "mangaupdates"],
    "year":            ["mangabaka", "mangaupdates"],
    "cover":           ["mangadex", "mangabaka", "mangaupdates"],
    "synopsis":        ["mangabaka", "mangadex", "mangaupdates"],
    "synopsis_lang":   ["mangabaka", "mangadex", "mangaupdates"],
    "rating":          ["mangabaka", "mangaupdates"],
    "rating_votes":    ["mangaupdates", "mangabaka"],
    "popularity_rank": ["mangabaka", "mangaupdates"],
    "authors":         ["mangabaka", "mangaupdates"],
    "artists":         ["mangabaka", "mangaupdates"],
}

# Campos-lista que se UNEN entre fuentes (dedup preservando orden/casing del primero).
UNION_FIELDS = ("genres", "tags", "titles_alt")


def _is_empty(v) -> bool:
    return v is None or v == "" or v == [] or v == {} or v == 0


def _dedup(seq) -> list:
    seen, out = set(), []
    for x in seq or []:
        k = x.lower() if isinstance(x, str) else x
        if k in seen:
            continue
        seen.add(k)
        out.append(x)
    return out


def combine(partials: dict[str, dict]) -> dict:
    """Funde parciales por fuente en una Obra final con procedencia."""
    work = schema.blank_work()
    prov = {}

    # Campos escalares por prioridad: primera fuente con valor no vacío gana.
    for field, order in FIELD_PRIORITY.items():
        for src in order:
            p = partials.get(src)
            if p and not _is_empty(p.get(field)):
                work[field] = p[field]
                prov[field] = src
                break

    # Campos-lista: unión sobre TODAS las fuentes presentes.
    for field in UNION_FIELDS:
        merged = []
        for src, p in partials.items():
            if p and p.get(field):
                merged.extend(p[field])
        if merged:
            work[field] = _dedup(merged)

    # IDs cruzados: unión de todos los `ids` + `cover_sources`.
    ids, covers = {}, {}
    for src, p in partials.items():
        if not p:
            continue
        for k, v in (p.get("ids") or {}).items():
            if v and k not in ids:
                ids[k] = v
        cov = p.get("cover")
        if cov:
            covers.setdefault(src, cov)
    work["ids"] = ids
    work["cover_sources"] = covers

    # Derivados y metadatos.
    work["readable"] = schema.readable(work["type"])
    work["provenance"] = prov
    work["fetched_ts"] = time.time()
    return work
