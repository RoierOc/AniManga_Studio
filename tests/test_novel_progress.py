"""El progreso de novelas debe sobrevivir al WebView y fusionarse por fecha."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))


def _app():
    from flask import Flask
    from api import novels

    app = Flask(__name__)
    app.register_blueprint(novels.novels_bp, url_prefix='/api/novels')
    return app


def test_progreso_fusiona_el_punto_mas_reciente(monkeypatch, tmp_path):
    from api import novels

    monkeypatch.setattr(novels, '_PROGRESS_PATH', tmp_path / 'novel_progress.json')
    client = _app().test_client()

    first = client.post('/api/novels/progress', json={
        'n1': {'title': 'Obra', 'chapterIndex': 2, 'scroll': 20, 'at': 200},
    })
    second = client.post('/api/novels/progress', json={
        'n1': {'title': 'Obra', 'chapterIndex': 1, 'scroll': 90, 'at': 100},
        'n2': {'title': 'Otra', 'chapterIndex': 0, 'at': 300},
    })

    assert first.status_code == 200
    assert second.get_json()['n1']['chapterIndex'] == 2
    assert second.get_json()['n2']['title'] == 'Otra'
    assert client.get('/api/novels/progress').get_json()['n1']['scroll'] == 20


def test_backup_incluye_y_restaura_el_progreso_de_novelas(monkeypatch, tmp_path):
    from api import backup, novels

    monkeypatch.setattr(novels, '_PROGRESS_PATH', tmp_path / 'novel_progress.json')
    monkeypatch.setattr(backup, 'load_local_library', lambda: [])
    monkeypatch.setattr(backup, 'save_local_library', lambda _value: None)
    monkeypatch.setattr(backup, '_lib_read', lambda: {})
    monkeypatch.setattr(backup, '_lib_write', lambda _value: None)

    novels._PROGRESS_PATH.write_text('{"n1": {"title": "Obra", "chapterIndex": 4, "at": 400}}')
    payload = backup.build_payload()
    assert payload['novel_progress']['n1']['chapterIndex'] == 4

    counts = backup.apply_payload({'novel_progress': {
        'n1': {'title': 'Obra', 'chapterIndex': 1, 'at': 100},
        'n2': {'title': 'Otra', 'chapterIndex': 0, 'at': 500},
    }})
    saved = novels._progress_read()
    assert counts['novels'] == {'added': 1, 'merged': 1}
    assert saved['n1']['chapterIndex'] == 4
    assert saved['n2']['title'] == 'Otra'


def test_payload_invalido_no_escribe_progreso(monkeypatch, tmp_path):
    from api import novels

    monkeypatch.setattr(novels, '_PROGRESS_PATH', tmp_path / 'novel_progress.json')
    response = _app().test_client().post('/api/novels/progress', json=[])
    assert response.status_code == 400
    assert not novels._PROGRESS_PATH.exists()


def test_perfil_de_sync_incluye_el_progreso_de_novelas(monkeypatch, tmp_path):
    from api import backup, novels, reader, sync

    monkeypatch.setattr(sync, '_PROFILE_DIR', tmp_path / 'profile')
    monkeypatch.setattr(backup, 'build_payload', lambda: {
        'manga': [], 'anime': [], 'novel_progress': {'n1': {'title': 'Obra', 'at': 7}},
    })
    monkeypatch.setattr(reader, '_history_read', lambda: [])
    monkeypatch.setattr(reader, '_progress_read', lambda: {})
    monkeypatch.setattr(novels, '_progress_read', lambda: {'n1': {'title': 'Obra', 'at': 7}})
    written = {}
    monkeypatch.setattr(sync, '_write_json', lambda name, value: written.setdefault(name, value))

    sync._collect_profile()

    assert written['novel_progress.json']['n1']['title'] == 'Obra'
