"""Identidad de Obra — id canónico estable + emparejado difuso entre fuentes (Fase 0).

Columna vertebral: MangaBaka, que ya trae los IDs cruzados (AniList/MAL/MangaUpdates/
Kitsu). El id canónico DEBE ser estable entre sesiones porque la biblioteca y el progreso
se atarán a él. Fuzzy sólo como red de seguridad al verificar enriquecimientos por título.

El umbral 0.60 y el criterio (substring O ratio) son los MISMOS que usa el motor de
cobertura de `transplant.py` (`_FUZZY_MIN`), reproducidos aquí en local para no acoplar
este módulo ligero a ese módulo pesado (que importa numpy/cv2). Ver [[project_manga_hub_discovery]].
"""
from __future__ import annotations

import hashlib
from difflib import SequenceMatcher

_FUZZY_MIN = 0.60


def _key(s: str | None) -> str:
    """Clave insensible a mayúsculas/espacios/puntuación (solo alfanuméricos)."""
    return "".join(ch for ch in (s or "").lower() if ch.isalnum())


def canonical_id(source: str, ext_id) -> str:
    """id canónico a partir de la fuente + su id externo. Prefijo corto y estable:
    mb: MangaBaka · mu: MangaUpdates · md: MangaDex. Fallback determinista por título."""
    prefix = {"mangabaka": "mb", "mangaupdates": "mu", "mangadex": "md"}.get(source, source[:2])
    return f"{prefix}:{ext_id}"


def fallback_id(title: str, year=None) -> str:
    """Último recurso cuando no hay ningún id de fuente: hash determinista y estable del
    título normalizado + año. Mismo input → mismo id siempre (test de estabilidad)."""
    h = hashlib.sha1(f"{_key(title)}|{year or ''}".encode("utf-8")).hexdigest()[:10]
    return f"wk:{h}"


def title_matches(candidate: str, variants: list[str]) -> bool:
    """¿`candidate` es la misma obra que alguna de `variants`? Substring o ratio ≥ 0.60."""
    ak = _key(candidate)
    if not ak:
        return False
    for v in variants:
        vk = _key(v)
        if not vk:
            continue
        if vk in ak or ak in vk:
            return True
        if SequenceMatcher(None, ak, vk).ratio() >= _FUZZY_MIN:
            return True
    return False
