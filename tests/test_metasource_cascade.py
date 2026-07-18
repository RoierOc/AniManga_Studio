"""Fase 0 — motor de fusión «mejor versión» a nivel de campo.

Contrato: la prioridad por campo se respeta, las listas se unen, los IDs se combinan, y la
procedencia queda anotada. Puro Python (sin red).
Correr:  .venv/bin/python -m pytest tests/test_metasource_cascade.py -q
"""
from api.metasource import cascade


def _mb():
    return {
        "id": "mb:1", "ids": {"anilist": 100}, "title": "Obra", "title_native": "作品",
        "type": "manga", "status": "ongoing", "year": 2019,
        "cover": "mb_cover", "synopsis": "sinopsis mb", "rating": 80,
        "genres": ["accion"], "tags": ["a"], "authors": ["Autor"], "titles_alt": ["Alt1"],
    }


def _mu():
    return {
        "id": "mu:9", "ids": {"mangaupdates": 9}, "title": "Obra",
        "type": "manhwa", "rating": 70, "rating_votes": 1234,
        "genres": ["fantasia"], "titles_alt": ["Alt2"],
    }


def test_type_prefers_mangaupdates_over_mangabaka():
    w = cascade.combine({"mangabaka": _mb(), "mangaupdates": _mu()})
    assert w["type"] == "manhwa"              # MangaUpdates manda en taxonomía
    assert w["provenance"]["type"] == "mangaupdates"
    assert w["readable"] is True             # manhwa → legible


def test_rating_prefers_mangabaka_votes_prefer_mangaupdates():
    w = cascade.combine({"mangabaka": _mb(), "mangaupdates": _mu()})
    assert w["rating"] == 80                  # MangaBaka (agregado) gana la nota
    assert w["rating_votes"] == 1234          # MangaUpdates gana los votos


def test_lists_are_unioned_and_ids_merged():
    w = cascade.combine({"mangabaka": _mb(), "mangaupdates": _mu()})
    assert set(w["genres"]) == {"accion", "fantasia"}
    assert set(w["titles_alt"]) == {"Alt1", "Alt2"}
    assert w["ids"] == {"anilist": 100, "mangaupdates": 9}


def test_empty_value_never_overwrites_filled_one():
    # MangaUpdates sin sinopsis NO debe borrar la de MangaBaka.
    mu = _mu(); mu["synopsis"] = ""
    w = cascade.combine({"mangabaka": _mb(), "mangaupdates": mu})
    assert w["synopsis"] == "sinopsis mb"
    assert w["provenance"]["synopsis"] == "mangabaka"


def test_single_source_still_produces_complete_work():
    w = cascade.combine({"mangabaka": _mb()})
    assert w["title"] == "Obra"
    assert w["type"] == "manga"
    assert w["cover"] == "mb_cover"
    assert w["fetched_ts"] > 0
