"""Esquema «Obra» (Work) — la entidad fuente-agnóstica del Manga Hub (Fase 0).

Una Obra abstrae manga / manhwa / manhua / novela por encima de la fuente concreta.
Los adapters (`sources/`) devuelven *parciales* con los campos que conocen, ya
normalizados con estos helpers; `cascade.combine()` los funde en una Obra final.

Todo son funciones PURAS y dicts JSON-serializables a propósito: así el contrato viaja
tal cual al `stores/discovery.js` del frontend (WorkSummary ligero para el grid, Work
completo para la ficha) sin capa de (de)serialización.
"""
from __future__ import annotations

# Tipos que se pueden LEER dentro de la app (imágenes → lector/escalado). Las novelas y
# «other» son fichas informativas (la UI muestra "info" en vez de "leer"). readable() lo deriva.
READABLE_TYPES = {"manga", "manhwa", "manhua"}
KNOWN_TYPES = {"manga", "manhwa", "manhua", "novel", "other"}


def norm_type(raw: str | None) -> str:
    """Cualquier etiqueta de tipo de cualquier fuente → nuestro enum estable."""
    t = (raw or "").strip().lower()
    if not t:
        return ""
    if "manhwa" in t:
        return "manhwa"
    if "manhua" in t:
        return "manhua"
    if "novel" in t:                      # "novel", "light novel"
        return "novel"
    if "manga" in t:
        return "manga"
    return "other"                        # artbook, doujinshi, OEL, filipino, …


def norm_status(raw: str | None, completed: bool | None = None) -> str:
    if completed is True:
        return "completed"
    s = (raw or "").strip().lower()
    if not s:
        return "unknown"
    if "complete" in s or "finish" in s:
        return "completed"
    if "ongoing" in s or "releasing" in s or "publishing" in s or "active" in s:
        return "ongoing"
    if "hiatus" in s:
        return "hiatus"
    if "cancel" in s or "discontin" in s:
        return "cancelled"
    return "unknown"


def norm_rating(raw, force_10_scale: bool = False) -> int | None:
    """Puntuación → entero 0–100. Cada fuente puntúa distinto (0–10 o 0–100); aquí se
    unifica. Heurística: valores ≤ 10 se asumen en escala 0–10 y se multiplican."""
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return None
    if v <= 0:
        return None
    if force_10_scale or v <= 10.0:
        v *= 10.0
    return max(0, min(100, round(v)))


def readable(work_type: str | None) -> bool:
    return (work_type or "") in READABLE_TYPES


# Campos de una Obra COMPLETA con sus valores neutros. combine() parte de esto.
def blank_work() -> dict:
    return {
        "id": "",                    # id canónico ESTABLE (biblioteca/progreso se atan a él)
        "ids": {},                   # {anilist, mal, mangaupdates, mangadex, kitsu, …}
        "title": "",
        "title_native": "",
        "titles_alt": [],
        "type": "",
        "status": "unknown",
        "year": None,
        "cover": "",
        "cover_sources": {},         # {fuente: url}
        "synopsis": "",
        "synopsis_lang": "",
        "genres": [],
        "tags": [],
        "rating": None,              # 0–100
        "rating_votes": 0,
        "popularity_rank": None,
        "authors": [],
        "artists": [],
        "readable": False,
        "provenance": {},            # {campo: fuente_que_ganó} — depuración + atribución
        "fetched_ts": 0.0,
        "stale": False,
    }


_SUMMARY_KEYS = ("id", "title", "title_native", "type", "status", "year",
                 "cover", "rating", "readable")


def to_summary(work: dict) -> dict:
    """Subconjunto ligero que pinta el grid de resultados (no arrastra sinopsis/tags)."""
    return {k: work.get(k) for k in _SUMMARY_KEYS}
