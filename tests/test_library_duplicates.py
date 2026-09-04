"""Contratos del auditor de duplicados de Biblioteca."""
from flask import Flask

from api import library
from api.library_duplicates import canonical_title, scan_duplicates


MD_ONE = "11111111-1111-4111-8111-111111111111"
MD_TWO = "22222222-2222-4222-8222-222222222222"


def folder(name, **extra):
    return {
        "id": name,
        "name": name,
        "chapter_count": 4,
        "page_count": 40,
        "upscaled": 0,
        "cover": None,
        **extra,
    }


def test_titulo_canonico_ignora_acentos_puntuacion_y_espacios():
    assert canonical_title("  Witch-Hát Atelier! ") == "witchhatatelier"


def test_identidad_mangadex_marca_dos_carpetas_como_exactas():
    identities = {
        "Witch Hat Atelier": {"md_uuid": MD_ONE, "method": "alias+al"},
        "Witch Hat Atelier (backup)": {"md_uuid": MD_ONE, "method": "hint"},
    }
    result = scan_duplicates(
        [folder("Witch Hat Atelier"), folder("Witch Hat Atelier (backup)")],
        [],
        identity_loader=identities.get,
    )

    assert result["count"] == 1
    group = result["groups"][0]
    assert group["scope"] == "folders"
    assert group["confidence"] == "exact"
    assert group["reason"] == "Identidad MangaDex compartida"
    assert {record["name"] for record in group["records"]} == {
        "Witch Hat Atelier",
        "Witch Hat Atelier (backup)",
    }


def test_titulo_igual_sin_identidad_es_revision_y_no_auto_fusion():
    result = scan_duplicates(
        [folder("Dorohedoro"), folder("Dorohedoro!")],
        [],
        identity_loader=lambda _name: {},
    )

    assert result["count"] == 1
    assert result["groups"][0]["confidence"] == "review"
    assert "revisar" in result["groups"][0]["reason"].lower()


def test_uuid_distinto_no_se_confunde_por_titulo():
    identities = {
        "Serie": {"md_uuid": MD_ONE},
        "Serie!": {"md_uuid": MD_TWO},
    }
    result = scan_duplicates(
        [folder("Serie"), folder("Serie!")],
        [],
        identity_loader=identities.get,
    )

    assert result["count"] == 0


def test_seguimientos_duplicados_por_anilist_se_marcan_sin_mezclar_carpetas():
    result = scan_duplicates(
        [],
        [
            {"id": "src_1_a", "title": "Yotsuba&!", "kind": "source", "al_id": 1234},
            {"id": "src_2_b", "title": "Yotsuba &!", "kind": "source", "al_id": 1234},
            {"id": "novel-1", "title": "Yotsuba novela", "kind": "novel", "novel": True},
        ],
    )

    assert result["checked"] == {"folders": 0, "tracking": 2}
    assert result["count"] == 1
    assert result["groups"][0]["scope"] == "tracking"
    assert result["groups"][0]["confidence"] == "exact"
    assert result["groups"][0]["reason"] == "Identidad AniList compartida"


def test_una_carpeta_y_su_seguimiento_no_son_un_duplicado():
    result = scan_duplicates(
        [folder("Frieren")],
        [{"id": MD_ONE, "title": "Frieren", "kind": "mangadex"}],
        identity_loader=lambda _name: {"md_uuid": MD_ONE},
    )

    assert result["count"] == 0


def test_endpoint_devuelve_un_informe_de_solo_lectura(monkeypatch):
    app = Flask(__name__)
    app.register_blueprint(library.library_bp, url_prefix="/api/library")
    monkeypatch.setattr(library, "_scan_folders", lambda: [folder("Berserk")])
    monkeypatch.setattr("api.mangadex.load_local_library", lambda: [])

    response = app.test_client().get("/api/library/duplicates")

    assert response.status_code == 200
    assert response.get_json() == {
        "groups": [],
        "count": 0,
        "checked": {"folders": 1, "tracking": 0},
    }
