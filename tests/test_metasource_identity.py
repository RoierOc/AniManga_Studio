"""Fase 0 — identidad de Obra: id canónico ESTABLE + emparejado difuso.

El id canónico se ata a biblioteca/progreso, así que debe ser determinista entre sesiones.
El fuzzy protege el enriquecimiento de emparejar la obra equivocada. Puro Python (sin red).
Correr:  .venv/bin/python -m pytest tests/test_metasource_identity.py -q
"""
from api.metasource import identity


def test_canonical_id_prefixes():
    assert identity.canonical_id("mangabaka", 3397) == "mb:3397"
    assert identity.canonical_id("mangaupdates", 9) == "mu:9"
    assert identity.canonical_id("mangadex", "uuid") == "md:uuid"


def test_fallback_id_is_stable_and_normalized():
    a = identity.fallback_id("Solo Leveling", 2018)
    b = identity.fallback_id("  solo   leveling  ", 2018)   # mismo tras normalizar
    assert a == b
    assert a.startswith("wk:")
    assert identity.fallback_id("Otra", 2018) != a          # distinto input → distinto id


def test_title_matches_substring_ratio_and_negative():
    variants = ["Solo Leveling", "Na Honjaman Lebel-eob", "I Alone Level-Up"]
    assert identity.title_matches("solo leveling", variants) is True       # exacto/substring
    assert identity.title_matches("Solo Leveling!", variants) is True      # substring tras normalizar
    assert identity.title_matches("Omniscient Reader", variants) is False  # otra obra
    assert identity.title_matches("", variants) is False
