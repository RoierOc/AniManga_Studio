import json
import zipfile

from flask import Flask

from api import cbz


def _client(tmp_path, monkeypatch):
    library = tmp_path / "Mangas"
    manga = library / "Solo Leveling"
    manga.mkdir(parents=True)
    with zipfile.ZipFile(manga / "Tomo 1.cbz", "w") as archive:
        for page in range(3):
            archive.writestr(f"{page + 1:03}.jpg", b"page")

    state = tmp_path / "cbz_progress.json"
    monkeypatch.setattr(cbz, "DOCS_MANGA_DIR", library)
    monkeypatch.setattr(cbz, "_progress_file", lambda: state, raising=False)
    app = Flask(__name__)
    app.register_blueprint(cbz.cbz_bp, url_prefix="/api/cbz")
    return app.test_client(), state


def test_cbz_progress_is_saved_and_returned_with_the_matching_archive(tmp_path, monkeypatch):
    client, state = _client(tmp_path, monkeypatch)

    response = client.post("/api/cbz/progress", json={
        "manga": "Solo Leveling", "volume": "Tomo 1.cbz", "page": 1,
        "page_count": 3, "read": False,
    })

    assert response.status_code == 200
    assert json.loads(state.read_text())["Solo Leveling"]["Tomo 1.cbz"]["page"] == 1
    volume = client.get("/api/cbz/volumes", query_string={"manga": "Solo Leveling"}).get_json()[0]
    assert volume["progress_page"] == 1
    assert volume["in_progress"] is True
    assert volume["read"] is False

    archive = state.parent / "Mangas" / "Solo Leveling" / "Tomo 1.cbz"
    archive.rename(archive.with_name("Tomo 1 - alterno.cbz"))
    renamed = client.get("/api/cbz/volumes", query_string={"manga": "Solo Leveling"}).get_json()[0]
    assert renamed["name"] == "Tomo 1 - alterno.cbz"
    assert renamed["progress_page"] == 0
    assert renamed["read"] is False


def test_cbz_progress_marks_read_and_ignores_a_page_outside_current_volume(tmp_path, monkeypatch):
    client, _ = _client(tmp_path, monkeypatch)
    payload = {"manga": "Solo Leveling", "volume": "Tomo 1.cbz", "page": 2,
               "page_count": 3, "read": True}
    assert client.post("/api/cbz/progress", json=payload).status_code == 200
    volume = client.get("/api/cbz/volumes", query_string={"manga": "Solo Leveling"}).get_json()[0]
    assert volume["progress_page"] == 2
    assert volume["in_progress"] is False
    assert volume["read"] is True

    archive = tmp_path / "Mangas" / "Solo Leveling" / "Tomo 1.cbz"
    with zipfile.ZipFile(archive, "w") as changed:
        changed.writestr("001.jpg", b"page")
        changed.writestr("002.jpg", b"page")
    volume = client.get("/api/cbz/volumes", query_string={"manga": "Solo Leveling"}).get_json()[0]
    assert volume["progress_page"] == 0
    assert volume["in_progress"] is False
    assert volume["read"] is False
    payload.update(page=0, page_count=2, read=False)
    assert client.post("/api/cbz/progress", json=payload).status_code == 200
    assert client.get("/api/cbz/volumes", query_string={"manga": "Solo Leveling"}).get_json()[0]["read"] is False


def test_cbz_progress_rejects_paths_and_invalid_page_values(tmp_path, monkeypatch):
    client, state = _client(tmp_path, monkeypatch)

    escaped = client.post("/api/cbz/progress", json={
        "manga": "../outside", "volume": "Tomo 1.cbz", "page": 0, "page_count": 3,
    })
    invalid_page = client.post("/api/cbz/progress", json={
        "manga": "Solo Leveling", "volume": "Tomo 1.cbz", "page": -1, "page_count": 3,
    })
    page_past_end = client.post("/api/cbz/progress", json={
        "manga": "Solo Leveling", "volume": "Tomo 1.cbz", "page": 3, "page_count": 3,
    })

    assert escaped.status_code == 400
    assert invalid_page.status_code == 400
    assert page_past_end.status_code == 400
    assert not state.exists()


def test_cbz_progress_rejects_a_declared_cross_origin(tmp_path, monkeypatch):
    client, state = _client(tmp_path, monkeypatch)

    response = client.post("/api/cbz/progress", json={
        "manga": "Solo Leveling", "volume": "Tomo 1.cbz", "page": 0, "page_count": 3,
    }, headers={"Origin": "https://attacker.example"})

    assert response.status_code == 403
    assert not state.exists()
