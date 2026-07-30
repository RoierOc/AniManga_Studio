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


# ── Cobertura del anime sobre el manga ────────────────────────────────────────────────────
# MangaUpdates es la ÚNICA de nuestras fuentes que publica por dónde va la adaptación: el
# campo `anime` de la ficha trae, en texto libre, dónde empieza y acaba cada temporada, p.ej.
#   {"start": "Vol 1, Chap 1 (S1) / Vol 7, Chap 61 (S2)",
#    "end":   "Vol 7, Chap 60 (S1) / Vol 9, Chap 80 (S2)"}
# Con `latest_chapter` al lado sale la frase que de verdad se quiere leer al terminar una
# temporada: «el anime llega al 80, el manga va por el 147». Ni AniList ni MangaDex lo tienen.
import re as _re

_CHAP = _re.compile(r"chap\.?\s*([0-9]+(?:\.[0-9]+)?)", _re.I)


def _max_chap(texto: str):
    """El capítulo más ALTO citado en el campo. Es texto libre escrito por editores humanos
    («Chap 54 Page 4», «Chap 38 (S1) Skips most of Chap 2»), así que no se intenta parsear la
    estructura: se leen todos los números de capítulo y manda el mayor, que es donde llega la
    adaptación más avanzada. Si no hay ninguno, None — y entonces no se afirma nada."""
    nums = [float(m) for m in _CHAP.findall(texto or "")]
    return max(nums) if nums else None


def _mejor_serie(title: str, variants: list[str]) -> dict | None:
    """La serie de MangaUpdates que corresponde a estas variantes de título.

    Dos cuidados que costaron una obra mal emparejada (Demon Slayer):
    · Se prueba CADA variante como término de búsqueda, no sólo la primera. MangaUpdates indexa
      por el título japonés: buscar «Demon Slayer: Kimetsu no Yaiba» devolvía tres doujinshi y
      ni rastro de la serie, mientras que «Kimetsu no Yaiba» la da la primera.
    · Los doujinshi se descartan por la convención «<obra> dj - <título>» del propio sitio, y se
      exige coincidencia EXACTA antes de aceptar la difusa — `title_matches` es lo bastante laxo
      como para dar por buena «Kimetsu no Yaiba dj - Ghost».
    """
    def _norm(t):
        return "".join(c for c in (t or "").lower() if c.isalnum())

    exactos = {_norm(v) for v in variants if v}
    fallback = None
    for q in ([title] + list(variants)):
        if not q:
            continue
        for p in search(q, limit=6):
            t = p.get("title", "")
            if " dj - " in t:
                continue
            if _norm(t) in exactos:
                return p
            if fallback is None and title_matches(t, variants):
                fallback = p
    return fallback


def anime_coverage(title: str, variants: list[str]) -> dict | None:
    """{'ends_at': 80.0, 'latest': 147, 'mu_id': …} o None.

    None significa «no se pudo saber», que NO es «el anime no cubre nada»: quien llama debe
    callarse, no rellenar con un 0 (regla «falló ≠ no había»).
    """
    hit = _mejor_serie(title, variants)
    if not hit:
        return None
    sid = (hit.get("ids") or {}).get("mangaupdates")
    body = base.get_json(f"{_BASE}/series/{sid}", "mangaupdates_coverage")
    if not body:
        return None
    fin = _max_chap((body.get("anime") or {}).get("end") or "")
    if fin is None:
        return None
    try:
        latest = int(body.get("latest_chapter") or 0) or None
    except (TypeError, ValueError):
        latest = None
    return {"ends_at": fin, "latest": latest, "mu_id": sid}
