"""La ficha de un manga de TU biblioteca ya cuenta algo de la obra.

`/api/library` sólo devuelve contadores (capítulos, páginas, 4K), así que un manga DESCARGADO no
tenía sinopsis, ni autor, ni géneros, ni año — mientras que uno que NO tienes sí los tenía
(`WorkInfoModal` de Descubrir). `/api/anilist/manga/<al_id>` cierra ese hueco.

Lo que se fija aquí es lo que puede romperse en silencio: que AniList caído NO se disfrace de
«obra sin ficha» (regla del repo «falló ≠ no había»), que el reparto se filtre a quien firma la
obra (AniList mete asistentes y editores), y que la sinopsis ESPAÑOLA de MangaDex mande sobre la
inglesa de AniList cuando existe — pero sólo cuando existe de verdad.

Correr:  .venv/bin/python -m pytest tests/test_manga_work_info.py -q
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from api import anilist as al  # noqa: E402


_MEDIA = {
    "description": "Una <i>chica</i> y su piano.<br>Nada más.",
    "genres": ["Drama", "Romance"],
    "meanScore": 86, "chapters": None, "volumes": None,
    "status": "RELEASING", "format": "MANGA", "countryOfOrigin": "JP",
    "startDate": {"year": 2021}, "endDate": {"year": None},
    "staff": {"edges": [
        {"role": "Story & Art", "node": {"name": {"full": "Kuzushiro"}}},
        {"role": "Story & Art", "node": {"name": {"full": "Kuzushiro"}}},   # duplicado real
        {"role": "Assistant",   "node": {"name": {"full": "Nadie"}}},
        {"role": "Editor",      "node": {"name": {"full": "Tampoco"}}},
    ]},
    "siteUrl": "https://anilist.co/manga/1",
}


@pytest.fixture
def client(monkeypatch):
    from flask import Flask
    # Caché en disco fuera: si no, el primer test siembra los siguientes.
    monkeypatch.setattr(al, "_cache_get", lambda *a, **k: None)
    monkeypatch.setattr(al, "_cache_set", lambda *a, **k: None)
    app = Flask(__name__)
    app.register_blueprint(al.anilist_bp, url_prefix="/api/anilist")
    return app.test_client()


def test_anilist_caido_no_se_disfraza_de_obra_sin_ficha(client, monkeypatch):
    monkeypatch.setattr(al, "_ql", lambda *a, **k: {"_error": "timeout"})
    r = client.get("/api/anilist/manga/1")
    assert r.status_code == 502
    assert "timeout" in r.get_json()["error"]


def test_solo_firman_los_autores_y_sin_repetir(client, monkeypatch):
    monkeypatch.setattr(al, "_ql", lambda *a, **k: {"Media": _MEDIA})
    d = client.get("/api/anilist/manga/1").get_json()
    assert d["authors"] == ["Kuzushiro"]
    assert d["synopsis"] == "Una chica y su piano.\nNada más."   # sin HTML
    assert d["status"] == "En curso" and d["years"] == "2021" and d["kind"] == "Manga"


def test_la_sinopsis_espanola_de_mangadex_manda_sobre_la_inglesa(client, monkeypatch):
    monkeypatch.setattr(al, "_ql", lambda *a, **k: {"Media": _MEDIA})
    import api.mangadex as md
    monkeypatch.setattr(md, "description_es", lambda _id: "Una chica y su piano, en español.")
    d = client.get("/api/anilist/manga/1?md=abc").get_json()
    assert d["synopsis"] == "Una chica y su piano, en español."


def test_sin_traduccion_espanola_se_queda_la_de_anilist(client, monkeypatch):
    monkeypatch.setattr(al, "_ql", lambda *a, **k: {"Media": _MEDIA})
    import api.mangadex as md
    monkeypatch.setattr(md, "description_es", lambda _id: "")   # MangaDex no la tiene en ES
    d = client.get("/api/anilist/manga/1?md=abc").get_json()
    assert d["synopsis"].startswith("Una chica y su piano.")


def test_description_es_no_cae_al_ingles(monkeypatch):
    """Caer al inglés aquí sería inútil: quien llama YA tiene el inglés de AniList."""
    import api.mangadex as md

    class _R:
        status_code = 200
        @staticmethod
        def raise_for_status(): pass
        @staticmethod
        def json():
            return {"data": {"attributes": {"description": {"en": "Only english"}}}}

    monkeypatch.setattr(md, "_SESSION", type("S", (), {"get": staticmethod(lambda *a, **k: _R())}))
    assert md.description_es("abc") == ""
