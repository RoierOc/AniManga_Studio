"""Enlazar una carpeta desde la ficha conserva todo el contenido local existente."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))


class _Reply:
    def json(self):
        return {'data': {'Media': {}}}


def _app():
    from flask import Flask
    from api import anime

    app = Flask(__name__)
    app.register_blueprint(anime.anime_bp, url_prefix='/api/anime')
    return app


def _configure_files(monkeypatch, tmp_path, library, scanpaths=None):
    from api import anime

    lib_path = tmp_path / 'anime_library.json'
    scan_path = tmp_path / 'anime_scan_paths.json'
    lib_path.write_text(json.dumps(library), encoding='utf-8')
    scan_path.write_text(json.dumps(scanpaths or {'paths': [], 'mappings': {}}), encoding='utf-8')
    monkeypatch.setattr(anime, '_lib_path', lambda: lib_path)
    monkeypatch.setattr(anime, '_scanpaths_path', lambda: scan_path)
    monkeypatch.setattr(anime._rhttp, 'post', lambda *args, **kwargs: _Reply())
    return lib_path, scan_path


def test_ficha_suma_una_segunda_carpeta_sin_sobrescribir_la_primera(monkeypatch, tmp_path):
    from api import anime

    vieja = tmp_path / 'temporada-1'
    nueva = tmp_path / 'temporada-2'
    vieja.mkdir()
    nueva.mkdir()
    lib_path, scan_path = _configure_files(monkeypatch, tmp_path, {
        'clave-mal': {
            'al_id': 110277,
            'title': 'Attack on Titan Final Season',
            'cover': 'cover.jpg',
            'local_path': str(vieja),
            'episodes': {},
        },
    })

    response = _app().test_client().post('/api/anime/scan/match', json={
        'folder': str(nueva),
        'anilist_id': 110277,
        'title': 'Attack on Titan Final Season',
        'cover': 'cover.jpg',
    })

    assert response.status_code == 200
    saved = json.loads(lib_path.read_text(encoding='utf-8'))['clave-mal']
    assert saved['local_path'] == str(vieja)
    assert saved['local_paths'] == [str(nueva)]
    mappings = json.loads(scan_path.read_text(encoding='utf-8'))['mappings']
    assert mappings[str(nueva)] == '110277'


def test_ficha_no_puede_robar_una_carpeta_de_otra_serie(monkeypatch, tmp_path):
    from api import anime

    carpeta = tmp_path / 'ya-enlazada'
    carpeta.mkdir()
    lib_path, scan_path = _configure_files(monkeypatch, tmp_path, {
        '100': {'al_id': 100, 'title': 'Otra serie', 'episodes': {}},
        '110277': {'al_id': 110277, 'title': 'Attack on Titan Final Season', 'episodes': {}},
    }, {'paths': [], 'mappings': {str(carpeta): '100'}})

    response = _app().test_client().post('/api/anime/scan/match', json={
        'folder': str(carpeta),
        'anilist_id': 110277,
        'title': 'Attack on Titan Final Season',
        'cover': 'cover.jpg',
    })

    assert response.status_code == 409
    assert json.loads(scan_path.read_text(encoding='utf-8'))['mappings'][str(carpeta)] == '100'
    saved = json.loads(lib_path.read_text(encoding='utf-8'))
    assert 'local_path' not in saved['110277']


def test_ficha_rechaza_una_carpeta_inexistente(monkeypatch, tmp_path):
    _configure_files(monkeypatch, tmp_path, {'110277': {'al_id': 110277, 'episodes': {}}})
    response = _app().test_client().post('/api/anime/scan/match', json={
        'folder': str(tmp_path / 'no-existe'),
        'anilist_id': 110277,
        'title': 'Attack on Titan Final Season',
    })
    assert response.status_code == 400


def test_desenlazar_una_carpeta_secundaria_no_quita_la_principal(monkeypatch, tmp_path):
    from api import anime

    principal = tmp_path / 'principal'
    secundaria = tmp_path / 'secundaria'
    principal.mkdir()
    secundaria.mkdir()
    lib_path, scan_path = _configure_files(monkeypatch, tmp_path, {
        '110277': {
            'al_id': 110277,
            'title': 'Attack on Titan Final Season',
            'local_path': str(principal),
            'local_paths': [str(secundaria)],
            'episodes': {},
        },
    }, {'paths': [], 'mappings': {str(secundaria): '110277'}})

    response = _app().test_client().post('/api/anime/scan/unmatch', json={
        'folder': str(secundaria),
    })

    assert response.status_code == 200
    saved = json.loads(lib_path.read_text(encoding='utf-8'))['110277']
    assert saved['local_path'] == str(principal)
    assert 'local_paths' not in saved
    assert json.loads(scan_path.read_text(encoding='utf-8'))['mappings'] == {}
