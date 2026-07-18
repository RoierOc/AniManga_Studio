"""Adapter MangaUpdates — refuerzo de taxonomía y votos (Fase 0).

api.mangaupdates.com/v1 · La búsqueda y la ficha son PÚBLICAS (sin auth); sólo la lista
personal exigiría login, y eso queda FUERA de alcance por decisión. Ojo: la búsqueda es
**POST** (GET da 405). MangaUpdates es *la* referencia de tipo (manhwa/manhua/novela) y
aporta rating bayesiano + nº de votos, así que se usa como enriquecimiento en la ficha.

Búsqueda: POST /v1/series/search  body {"search": q, "perpage": n}  → {results:[{record:…}]}
Detalle:  GET  /v1/series/{id}                                       → record directo
"""
from __future__ import annotations

from api.metasource import schema
from api.metasource.identity import canonical_id, title_matches
from api.metasource.sources import base

NAME = "mangaupdates"
_BASE = "https://api.mangaupdates.com/v1"


def _to_partial(record: dict) -> dict | None:
    sid = record.get("series_id")
    if sid is None:
        return None
    img = ((record.get("image") or {}).get("url") or {})
    genres = [g.get("genre") for g in (record.get("genres") or []) if isinstance(g, dict) and g.get("genre")]
    year = record.get("year")
    try:
        year = int(year) if year else None
    except (TypeError, ValueError):
        year = None
    return {
        "id": canonical_id(NAME, sid),
        "ids": {"mangaupdates": sid},
        "title": (record.get("title") or "").strip(),
        "type": schema.norm_type(record.get("type")),
        "status": schema.norm_status(record.get("status"), completed=record.get("completed")),
        "year": year,
        "cover": img.get("original") or img.get("thumb") or "",
        "synopsis": record.get("description") or "",
        "synopsis_lang": "en",
        "genres": genres,
        "rating": schema.norm_rating(record.get("bayesian_rating")),
        "rating_votes": record.get("rating_votes") or 0,
        "authors": [a.get("name") for a in (record.get("authors") or [])
                    if isinstance(a, dict) and a.get("type") == "Author" and a.get("name")],
    }


def search(query: str, limit: int = 10) -> list[dict]:
    body = base.post_json(f"{_BASE}/series/search", "mangaupdates_search",
                          json={"search": query, "perpage": max(1, min(limit, 30))})
    if not body:
        return []
    out = []
    for row in (body.get("results") or [])[:limit]:
        rec = row.get("record") if isinstance(row, dict) else None
        p = _to_partial(rec or {})
        if p:
            out.append(p)
    return out


def fetch(ext_id: str) -> dict | None:
    body = base.get_json(f"{_BASE}/series/{ext_id}", "mangaupdates_fetch")
    if not body:
        return None
    return _to_partial(body)


def enrich_for(title: str, variants: list[str]) -> dict | None:
    """Resuelve la obra en MangaUpdates por título y devuelve su parcial, sólo si el
    resultado casa de verdad (fuzzy) con las variantes conocidas → evita enriquecer con
    una obra equivocada. Una sola llamada (usa el propio record de búsqueda)."""
    for p in search(title, limit=5):
        if title_matches(p.get("title", ""), variants):
            return p
    return None
