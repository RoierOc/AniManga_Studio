"""AniSkip distingue una respuesta válida vacía de un fallo del servicio."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))


def _client():
    from flask import Flask
    from api import anime

    app = Flask(__name__)
    app.register_blueprint(anime.anime_bp, url_prefix='/api/anime')
    return app.test_client()


def test_ani_skip_sin_intervalos_es_un_vacio_valido(monkeypatch):
    from api import anime

    monkeypatch.setattr(anime, '_aniskip_cache', {})

    class Response:
        @staticmethod
        def raise_for_status():
            pass

        @staticmethod
        def json():
            return {'results': []}

    monkeypatch.setattr(anime._http, 'get', lambda *args, **kwargs: Response())

    response = _client().get('/api/anime/skip_times/42/3')

    assert response.status_code == 200
    assert response.get_json() == {}
    assert (42, 3) in anime._aniskip_cache


def test_fallo_de_ani_skip_es_502_y_queda_observable(monkeypatch):
    from api import anime

    monkeypatch.setattr(anime, '_aniskip_cache', {})
    errors = []
    monkeypatch.setattr(anime, 'record_error', lambda *args, **kwargs: errors.append((args, kwargs)))

    def fail(*args, **kwargs):
        raise ConnectionError('AniSkip fuera de línea')

    monkeypatch.setattr(anime._http, 'get', fail)

    response = _client().get('/api/anime/skip_times/42/3')

    assert response.status_code == 502
    assert 'AniSkip fuera de línea' in response.get_json()['error']
    assert errors and errors[0][1]['op'] == 'aniskip'
    assert (42, 3) not in anime._aniskip_cache
