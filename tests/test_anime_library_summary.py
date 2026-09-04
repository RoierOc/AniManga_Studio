"""El pulso de la biblioteca no debe transportar rutas de detalle innecesarias."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))


class _Reply:
    def json(self):
        return []


def _app():
    from flask import Flask
    from api import anime

    app = Flask(__name__)
    app.register_blueprint(anime.anime_bp, url_prefix='/api/anime')
    return app


def test_summary_omite_rutas_de_detalle_sin_tocar_la_carga_completa(monkeypatch, tmp_path):
    from api import anime

    monkeypatch.setattr(anime, '_backfill_done', True)
    monkeypatch.setattr(anime, '_THUMBS_DIR', tmp_path / 'thumbs')
    monkeypatch.setattr(anime, '_q', lambda *args, **kwargs: _Reply())
    monkeypatch.setattr(anime, '_es_sub_injected', lambda _path: False)
    monkeypatch.setattr(anime, '_escanear_carpetas', lambda _entry: [{
        'num': 1,
        'path': str(tmp_path / 'episodio.mkv'),
        'filename': 'episodio.mkv',
        'original_path': str(tmp_path / 'original.mkv'),
        'a4k': True,
        'size': 42,
    }])
    monkeypatch.setattr(anime, '_lib_read', lambda: {
        '1': {'title': 'Serie', 'local_path': str(tmp_path), 'episodes': {}, 'watched': {}},
    })

    client = _app().test_client()
    full = client.get('/api/anime/library').get_json()
    summary = client.get('/api/anime/library?summary=1').get_json()
    full_ep = full[0]['episodes'][0]
    summary_ep = summary[0]['episodes'][0]

    assert full_ep['local_path'].endswith('episodio.mkv')
    assert full_ep['filename'] == 'episodio.mkv'
    assert full_ep['original_path'].endswith('original.mkv')
    assert summary_ep['in_local'] is True
    assert summary_ep['a4k'] is True
    assert 'local_path' not in summary_ep
    assert 'filename' not in summary_ep
    assert 'original_path' not in summary_ep
