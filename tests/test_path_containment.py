"""Fronteras de archivos: ninguna entrada de cliente puede escapar de su raíz."""

from flask import Flask
import pytest
import zipfile

from api import cbz, roots, webdav
from api.runtime import safe_child


@pytest.fixture
def manga_roots(tmp_path, monkeypatch):
    base = tmp_path / "base"
    upscaled = tmp_path / "base-up"
    extra = tmp_path / "extra"
    extra_upscaled = tmp_path / "extra-up"
    for folder in (base, upscaled, extra, extra_upscaled):
        folder.mkdir()
    monkeypatch.setattr(roots, "MANGA_DIR", base)
    monkeypatch.setattr(roots, "UPSCALED_DIR", upscaled)
    monkeypatch.setattr(roots, "_ROOTS_FILE", tmp_path / "roots.json")
    monkeypatch.setattr(roots, "get_library_mode", lambda: "normal")
    roots._write({"roots": [{"id": "extra", "label": "Extra",
                               "manga": str(extra), "upscaled": str(extra_upscaled)}]})
    return base


def test_safe_child_rejects_absolute_parent_and_symlink(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("secret")

    assert safe_child(root, "inside.txt") == root / "inside.txt"
    assert safe_child(root, "../outside.txt") is None
    assert safe_child(root, str(outside)) is None
    link = root / "link.txt"
    link.symlink_to(outside)
    assert safe_child(root, "link.txt") is None


def test_manga_find_file_rejects_parent_escape(manga_roots):
    outside = manga_roots.parent / "outside.jpg"
    outside.write_bytes(b"not a library page")

    assert roots.find_file("../outside.jpg") is None


def test_upload_pages_reject_parent_escape(tmp_path, monkeypatch):
    import app as app_module

    root = tmp_path / "manga"
    root.mkdir()
    outside = tmp_path / "outside.webp"
    outside.write_bytes(b"not a library page")
    monkeypatch.setattr(roots, "roots", lambda: [{"manga": str(root), "upscaled": str(root / "up")}])

    assert app_module._find_page_in("../outside.webp", "manga") is None


def test_cbz_volumes_and_pages_reject_absolute_paths(tmp_path, monkeypatch):
    library = tmp_path / "mangas"
    library.mkdir()
    outside_manga = tmp_path / "outside-manga"
    outside_manga.mkdir()
    archive = outside_manga / "Tomo 1.cbz"
    archive.write_bytes(b"not a real zip")
    monkeypatch.setattr(cbz, "DOCS_MANGA_DIR", library)

    app = Flask(__name__)
    app.register_blueprint(cbz.cbz_bp, url_prefix="/api/cbz")
    client = app.test_client()

    assert client.get("/api/cbz/volumes", query_string={"manga": str(outside_manga)}).status_code == 400
    assert client.get("/api/cbz/pages", query_string={
        "manga": "Manga", "volume": str(archive),
    }).status_code == 404


def test_cbz_cover_ignores_archive_symlink_outside_root(tmp_path, monkeypatch):
    library = tmp_path / "mangas"
    manga = library / "Manga"
    manga.mkdir(parents=True)
    external = tmp_path / "outside.cbz"
    with zipfile.ZipFile(external, "w") as archive:
        archive.writestr("page.jpg", b"outside")
    (manga / "01.cbz").symlink_to(external)
    monkeypatch.setattr(cbz, "DOCS_MANGA_DIR", library)

    app = Flask(__name__)
    app.register_blueprint(cbz.cbz_bp, url_prefix="/api/cbz")

    response = app.test_client().get("/api/cbz/cover", query_string={"manga": "Manga"})

    assert response.status_code == 404


def test_webdav_save_rejects_title_escape(monkeypatch, tmp_path):
    export_dir = tmp_path / "exports"
    temporary = tmp_path / "ready.cbz"
    temporary.write_bytes(b"cbz")
    task_id = "path-containment-test"
    from api import export as export_api

    export_api._export_tasks[task_id] = {
        "status": "complete", "tmp_path": str(temporary), "filename": "Tomo 1.cbz",
        "title": "../outside",
    }
    monkeypatch.setattr(webdav, "get_export_dir", lambda: export_dir)
    app = Flask(__name__)
    app.register_blueprint(webdav.webdav_bp, url_prefix="/api/webdav")
    try:
        response = app.test_client().post("/api/webdav/save", json={"task_id": task_id})
    finally:
        export_api._export_tasks.pop(task_id, None)

    assert response.status_code == 400
    assert temporary.exists()
    assert not (tmp_path / "outside" / "Tomo 1.cbz").exists()


def test_webdav_downloads_ignore_archive_symlinks(monkeypatch, tmp_path):
    from api import webdav

    export_dir = tmp_path / "exports"
    folder = export_dir / "Manga"
    folder.mkdir(parents=True)
    external = tmp_path / "outside.cbz"
    with zipfile.ZipFile(external, "w") as archive:
        archive.writestr("page.jpg", b"outside")
    (folder / "Secret.cbz").symlink_to(external)
    monkeypatch.setattr(webdav, "get_export_dir", lambda: export_dir)
    app = Flask(__name__)
    app.register_blueprint(webdav.webdav_bp, url_prefix="/api/webdav")
    client = app.test_client()

    assert client.get("/api/webdav/folder/Manga").status_code == 404
    assert client.get("/api/webdav/all").status_code == 404


def test_subtitle_batch_rejects_remote_direct_local_path(tmp_path):
    from api import subtitle_batch

    app = Flask(__name__)
    app.register_blueprint(subtitle_batch.subbatch_bp, url_prefix="/api/subtitle/batch")
    remote = {"REMOTE_ADDR": "10.0.0.8", "SERVER_PORT": "5103"}
    payload = {"items": [{"episode": 1, "local_path": str(tmp_path / "secret.mkv")}]}
    client = app.test_client()

    assert client.post("/api/subtitle/batch/scan", json=payload,
                       environ_overrides=remote).status_code == 403
    assert client.post("/api/subtitle/batch/start", json=payload,
                       environ_overrides=remote).status_code == 403


def test_video_resolver_rejects_unregistered_local_path(monkeypatch, tmp_path):
    from api import anime

    library = tmp_path / "library"
    library.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "episode.mkv").write_bytes(b"video")
    monkeypatch.setattr(anime, "_lib_read", lambda: {
        "42": {"local_path": str(library), "episodes": {}},
    })

    video, error = anime.resolve_episode_video({
        "anime_id": "42", "episode": 1, "local_path": str(outside),
    })

    assert video is None
    assert error and error[1] == 403


def test_video_resolver_accepts_completed_torrent_path_outside_registered_folder(monkeypatch, tmp_path):
    from api import anime

    library = tmp_path / "library"
    library.mkdir()
    torrent_root = tmp_path / "qbt"
    torrent_root.mkdir()
    video_file = torrent_root / "episode.mkv"
    video_file.write_bytes(b"video")

    class Reply:
        def json(self):
            return [{"content_path": str(torrent_root), "save_path": str(tmp_path)}]

    monkeypatch.setattr(anime, "_lib_read", lambda: {
        "42": {"local_path": str(library), "episodes": {}},
    })
    monkeypatch.setattr(anime, "_q", lambda *_args, **_kwargs: Reply())

    video, error = anime.resolve_episode_video({
        "anime_id": "42", "episode": 1, "info_hash": "a" * 40,
        "local_path": str(video_file),
    })

    assert error is None
    assert video == str(video_file)


def test_video_resolver_accepts_a4k_derivative_of_single_file_torrent(monkeypatch, tmp_path):
    from api import anime

    torrent_root = tmp_path / "qbt"
    torrent_root.mkdir()
    original = torrent_root / "Movie.mkv"
    original.write_bytes(b"video")
    derivative = torrent_root / "Movie.a4k.mkv"
    derivative.write_bytes(b"a4k")

    class Reply:
        def json(self):
            return [{"content_path": str(original), "save_path": str(torrent_root)}]

    monkeypatch.setattr(anime, "_lib_read", lambda: {
        "42": {"local_path": str(tmp_path / "other"), "episodes": {}},
    })
    monkeypatch.setattr(anime, "_q", lambda *_args, **_kwargs: Reply())

    video, error = anime.resolve_episode_video({
        "anime_id": "42", "episode": 1, "info_hash": "b" * 40,
        "local_path": str(derivative),
    })

    assert error is None
    assert video == str(derivative)


def test_video_resolver_rejects_unrelated_file_in_single_file_save_path(monkeypatch, tmp_path):
    from api import anime

    torrent_root = tmp_path / "qbt"
    torrent_root.mkdir()
    original = torrent_root / "Movie.mkv"
    original.write_bytes(b"video")
    private = torrent_root / "private" / "Secret.mkv"
    private.parent.mkdir()
    private.write_bytes(b"secret")

    class Reply:
        def json(self):
            return [{"content_path": str(original), "save_path": str(torrent_root)}]

    monkeypatch.setattr(anime, "_lib_read", lambda: {"42": {"episodes": {}}})
    monkeypatch.setattr(anime, "_q", lambda *_args, **_kwargs: Reply())

    video, error = anime.resolve_episode_video({
        "anime_id": "42", "episode": 1, "info_hash": "e" * 40,
        "local_path": str(private),
    })

    assert video is None
    assert error and error[1] == 403


def test_video_resolver_rejects_a4k_symlink_to_unrelated_file(monkeypatch, tmp_path):
    from api import anime

    torrent_root = tmp_path / "qbt"
    torrent_root.mkdir()
    original = torrent_root / "Movie.mkv"
    original.write_bytes(b"video")
    secret = torrent_root / "Secret.mkv"
    secret.write_bytes(b"secret")
    (torrent_root / "Movie.a4k.mkv").symlink_to(secret)

    class Reply:
        def json(self):
            return [{"content_path": str(original), "save_path": str(torrent_root)}]

    monkeypatch.setattr(anime, "_lib_read", lambda: {"42": {"episodes": {}}})
    monkeypatch.setattr(anime, "_q", lambda *_args, **_kwargs: Reply())

    video, error = anime.resolve_episode_video({
        "anime_id": "42", "episode": 1, "info_hash": "g" * 40,
        "local_path": str(original),
    })

    assert video is None
    assert error and error[1] == 403


def test_mobile_library_resolver_preserves_rejection_on_local_fallback(monkeypatch, tmp_path):
    from api import anime

    library = tmp_path / "library"
    library.mkdir()
    original = library / "Episode 01.mkv"
    original.write_bytes(b"video")
    outside = tmp_path / "secret.mkv"
    outside.write_bytes(b"secret")
    (library / "Episode 01.a4k.mkv").symlink_to(outside)
    record = {
        "local_path": str(library),
        "episodes": {"s01e001": {
            "info_hash": "h" * 40, "relative_path": "Episode 01.mkv",
        }},
    }
    monkeypatch.setattr(anime, "_lib_read", lambda: {"42": record})
    monkeypatch.setattr(anime, "resolve_episode_video",
                        lambda _data: (None, ("video path is outside torrent content", 403)))

    video, error = anime.video_de_biblioteca("42", 1, "s01e001")

    assert video is None
    assert error and error[1] == 403


def test_video_resolver_rejects_symlink_from_torrent_without_local_path(monkeypatch, tmp_path):
    from api import anime

    torrent_root = tmp_path / "qbt"
    torrent_root.mkdir()
    outside = tmp_path / "outside.mkv"
    outside.write_bytes(b"outside")
    (torrent_root / "Episode 01.mkv").symlink_to(outside)

    class Reply:
        def json(self):
            return [{"content_path": str(torrent_root)}]

    monkeypatch.setattr(anime, "_lib_read", lambda: {"42": {"episodes": {}}})
    monkeypatch.setattr(anime, "_q", lambda *_args, **_kwargs: Reply())

    video, error = anime.resolve_episode_video({
        "anime_id": "42", "episode": 1, "info_hash": "c" * 40,
    })

    assert video is None
    assert error and error[1] == 403


def test_video_resolver_distinguishes_qbt_failure_from_unregistered_path(monkeypatch, tmp_path):
    from api import anime

    outside = tmp_path / "episode.mkv"
    outside.write_bytes(b"outside")
    monkeypatch.setattr(anime, "_lib_read", lambda: {"42": {"episodes": {}}})
    monkeypatch.setattr(anime, "_q", lambda *_args, **_kwargs: (_ for _ in ()).throw(
        TimeoutError("qBittorrent down")))

    video, error = anime.resolve_episode_video({
        "anime_id": "42", "episode": 1, "info_hash": "d" * 40,
        "local_path": str(outside),
    })

    assert video is None
    assert error and error[1] == 502


def test_video_resolver_rejects_a4k_symlink_outside_registered_folder(monkeypatch, tmp_path):
    from api import anime

    library = tmp_path / "library"
    library.mkdir()
    original = library / "episode.mkv"
    original.write_bytes(b"video")
    outside = tmp_path / "outside.mkv"
    outside.write_bytes(b"outside")
    (library / "episode.a4k.mkv").symlink_to(outside)
    monkeypatch.setattr(anime, "_lib_read", lambda: {
        "42": {"local_path": str(library), "episodes": {}},
    })

    video, error = anime.resolve_episode_video({
        "anime_id": "42", "episode": 1, "local_path": str(original),
    })

    assert video is None
    assert error and error[1] == 403


def test_subtitle_resolver_does_not_fallback_after_video_path_rejection(monkeypatch, tmp_path):
    from api import anime, subtitle

    library = tmp_path / "library"
    library.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "episode.mkv").write_bytes(b"outside")
    monkeypatch.setattr(anime, "_lib_read", lambda: {
        "42": {"local_path": str(library), "episodes": {}},
    })
    monkeypatch.setattr(anime, "_find_video", lambda *_args: str(outside / "episode.mkv"))

    assert subtitle._resolve_video_path("", 1, "42", str(outside)) is None


def test_subtitle_resolver_falls_back_to_registered_library_when_qbt_is_down(monkeypatch, tmp_path):
    from api import anime, subtitle

    library = tmp_path / "library"
    library.mkdir()
    video = library / "Episode 01.mkv"
    video.write_bytes(b"video")
    calls = []

    def resolve(data):
        calls.append(data)
        if data.get("local_path"):
            return str(video), None
        return None, ("qBittorrent unavailable", 502)

    monkeypatch.setattr(anime, "_lib_read", lambda: {"42": {"local_path": str(library)}})
    monkeypatch.setattr(anime, "resolve_episode_video", resolve)

    assert subtitle._resolve_video_path("a" * 40, 1, "42") == str(video)
    assert calls[1]["local_path"] == str(library)


def test_subtitle_remote_request_rejects_direct_local_path(monkeypatch, tmp_path):
    from api import subtitle
    from flask import Flask

    outside = tmp_path / "episode.mkv"
    outside.write_bytes(b"outside")
    app = Flask(__name__)
    with app.test_request_context("/", environ_base={"REMOTE_ADDR": "10.0.0.8",
                                                       "SERVER_PORT": "5103"}):
        from api import anime
        monkeypatch.setattr(anime, "_find_video", lambda *_args: str(outside))
        assert subtitle._resolve_video_path("", 1, "", str(outside)) is None


def test_subtitle_legacy_qbt_resolution_uses_safe_video_resolver(monkeypatch, tmp_path):
    from api import anime, subtitle

    torrent_root = tmp_path / "qbt"
    torrent_root.mkdir()
    outside = tmp_path / "outside.mkv"
    outside.write_bytes(b"outside")
    (torrent_root / "Episode 01.mkv").symlink_to(outside)

    class Reply:
        def json(self):
            return [{"content_path": str(torrent_root)}]

    monkeypatch.setattr(anime, "_lib_read", lambda: {"42": {"episodes": {}}})
    monkeypatch.setattr(anime, "_q", lambda *_args, **_kwargs: Reply())

    assert subtitle._resolve_video_path("f" * 40, 1, "42") is None
