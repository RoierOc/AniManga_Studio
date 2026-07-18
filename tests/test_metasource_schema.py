"""Fase 0 — normalización del esquema «Obra» y parseo del adapter MangaBaka.

Comprueba el contrato que importa: cualquier etiqueta cruda de cualquier fuente cae en
nuestro enum estable, y la respuesta REAL de MangaBaka (shape verificado en vivo) se mapea
a un parcial correcto — incluidos los IDs cruzados que trae incrustados y el tipo manhwa.

Puro Python (sin red, sin cv2/torch) → corre en el CI ligero.
Correr:  .venv/bin/python -m pytest tests/test_metasource_schema.py -q
"""
from api.metasource import schema
from api.metasource.sources import mangabaka


def test_norm_type_covers_all_kinds():
    assert schema.norm_type("Manhwa") == "manhwa"
    assert schema.norm_type("manhua") == "manhua"
    assert schema.norm_type("Light Novel") == "novel"
    assert schema.norm_type("Novel") == "novel"
    assert schema.norm_type("Manga") == "manga"
    assert schema.norm_type("Doujinshi") == "other"
    assert schema.norm_type("") == ""


def test_norm_status():
    assert schema.norm_status("Completed") == "completed"
    assert schema.norm_status("Ongoing") == "ongoing"
    assert schema.norm_status("active") == "ongoing"
    assert schema.norm_status("on hiatus") == "hiatus"
    assert schema.norm_status("Cancelled") == "cancelled"
    assert schema.norm_status(None, completed=True) == "completed"
    assert schema.norm_status("") == "unknown"


def test_norm_rating_unifies_scales():
    assert schema.norm_rating(86.19) == 86      # ya 0–100
    assert schema.norm_rating(8.47) == 85       # 0–10 → *10
    assert schema.norm_rating(0) is None
    assert schema.norm_rating(None) is None


def test_readable_only_for_image_types():
    assert schema.readable("manhwa") is True
    assert schema.readable("manga") is True
    assert schema.readable("novel") is False
    assert schema.readable("other") is False


# Fragmento representativo de la respuesta real de api.mangabaka.org/v1 (verificada en vivo).
_MB_SERIES = {
    "id": 3397,
    "title": "Solo Leveling",
    "native_title": "나 혼자만 레벨업",
    "romanized_title": "Na Honjaman Lebel-eob",
    "secondary_titles": {"unknown": [{"type": "unknown", "title": "I Alone Level-Up"}]},
    "cover": {"raw": {"url": "https://images.mangabaka.dev/cover.jpg"}},
    "authors": ["Chu-Gong"], "artists": ["Seong-Rak Jang"],
    "description": "Hunters battle deadly monsters...",
    "year": 2018, "status": "completed", "type": "manhwa", "rating": 86.19,
    "popularity": {"global": {"current": 1}},
    "genres": ["action", "adventure", "fantasy"],
    "tags": ["Dungeon Exploring", "Training"],
    "source": {
        "anilist": {"id": 105398}, "my_anime_list": {"id": 121496},
        "manga_updates": {"id": "6z1uqw7"}, "kitsu": {"id": 54114},
    },
}


def test_mangabaka_partial_maps_real_shape():
    p = mangabaka._to_partial(_MB_SERIES)
    assert p["id"] == "mb:3397"
    assert p["title"] == "Solo Leveling"
    assert p["type"] == "manhwa"
    assert p["year"] == 2018
    assert p["rating"] == 86
    assert p["cover"] == "https://images.mangabaka.dev/cover.jpg"
    assert p["popularity_rank"] == 1
    assert "I Alone Level-Up" in p["titles_alt"]
    assert "Na Honjaman Lebel-eob" in p["titles_alt"]
    # IDs cruzados incrustados → sin llamar a AniList/MAL en vivo.
    assert p["ids"]["anilist"] == 105398
    assert p["ids"]["mal"] == 121496
    assert p["ids"]["mangaupdates"] == "6z1uqw7"


def test_mangabaka_partial_survives_missing_fields():
    # Serie mínima (solo id): no debe reventar, sólo campos vacíos.
    p = mangabaka._to_partial({"id": 9})
    assert p["id"] == "mb:9"
    assert p["type"] == ""
    assert p["ids"] == {}
    assert mangabaka._to_partial({}) is None       # sin id → None
