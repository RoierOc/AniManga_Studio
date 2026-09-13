from pathlib import Path

from flask import Flask

from api import export as export_api


def _client():
    app = Flask(__name__)
    app.register_blueprint(export_api.export_bp, url_prefix="/api/export")
    return app.test_client()


def test_save_ignores_a_destination_sent_by_the_client(monkeypatch, tmp_path):
    allowed = tmp_path / "tomos"
    outside = tmp_path / "outside"
    temporary = tmp_path / "ready.cbz"
    temporary.write_bytes(b"cbz")
    task_id = "path-test"
    export_api._export_tasks[task_id] = {
        "status": "complete",
        "tmp_path": str(temporary),
        "filename": "Tomo 1.cbz",
    }
    monkeypatch.setattr(export_api, "tomos_dir", lambda: allowed)

    try:
        response = _client().post(f"/api/export/save/{task_id}", json={"dir": str(outside)})
    finally:
        export_api._export_tasks.pop(task_id, None)

    assert response.status_code == 200
    assert (allowed / "Tomo 1.cbz").read_bytes() == b"cbz"
    assert not outside.exists()


def test_reveal_opens_only_the_configured_directory_and_ends_options(monkeypatch, tmp_path):
    allowed = tmp_path / "tomos"
    calls = []
    monkeypatch.setattr(export_api, "tomos_dir", lambda: allowed)
    monkeypatch.setattr(export_api, "is_wsl", lambda: False)
    monkeypatch.setattr(export_api, "is_macos", lambda: False)
    monkeypatch.setattr(export_api.subprocess, "Popen", lambda argv, **kwargs: calls.append(argv))

    response = _client().post("/api/export/reveal", json={"dir": "--help"})

    assert response.status_code == 200
    assert calls == [["xdg-open", "--", str(allowed.resolve())]]
    assert Path(response.get_json()["dir"]) == allowed.resolve()
