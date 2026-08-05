"""Una PELÍCULA a medias también es «seguir viendo».

El progreso de una película se guardaba desde siempre (`movie:<id>`), pero `/api/media/continue`
sólo leía las claves `series:…`. Consecuencia: pausabas Dune en el minuto 40 y la app se olvidaba
—ni en el riel de Cine, ni en la Portada, que sale del mismo sitio—. El dato estaba; nadie lo leía.

No se puede comprobar contra la biblioteca real: hoy ninguna película tiene fichero en disco, y sin
fichero no se ofrece (un botón de reanudar que no puede reproducir es peor que nada). De ahí el test.

Correr:  python -m pytest tests/test_media_continue_movies.py -q
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))


@pytest.fixture
def client():
    import api.media as M
    from flask import Flask
    app = Flask(__name__)
    app.register_blueprint(M.media_bp, url_prefix='/api/media')
    return app.test_client()


_PELI = {'id': 7, 'title': 'Dune', 'hasFile': True, 'year': 2021,
         'images': [{'coverType': 'poster', 'remoteUrl': 'p.jpg'}]}


def _sin_series(which, path, **kw):
    return [_PELI] if which == 'radarr' else []


def test_una_pelicula_a_medias_sale_en_seguir_viendo(client, monkeypatch):
    import api.media as M
    monkeypatch.setattr(M, '_prog_read', lambda: {
        'movie:7': {'pos': 2400, 'duration': 9000, 'watched': False, 'at': 100},
    })
    monkeypatch.setattr(M, '_get', _sin_series)

    items = client.get('/api/media/continue').get_json()['items']

    assert [i['title'] for i in items] == ['Dune']
    it = items[0]
    assert it['kind'] == 'movie' and it['movie_id'] == 7
    # Sin temporada ni episodio: quien pinte esto no puede dar por hecho que todo es una serie.
    assert it['season'] is None and it['num'] is None
    assert it['pos'] == 2400 and it['duration'] == 9000


def test_una_pelicula_TERMINADA_no_se_ofrece(client, monkeypatch):
    """Terminada es historial, no «lo que estás viendo». Si se ofreciera, el riel se llenaría de
    cosas ya vistas y dejaría de responder a su propia pregunta."""
    import api.media as M
    monkeypatch.setattr(M, '_prog_read', lambda: {
        'movie:7': {'pos': 0, 'duration': 9000, 'watched': True, 'at': 100},
    })
    monkeypatch.setattr(M, '_get', _sin_series)

    assert client.get('/api/media/continue').get_json()['items'] == []


def test_sin_fichero_en_disco_no_se_ofrece(client, monkeypatch):
    """El botón reanuda de verdad: sin fichero no hay nada que reproducir."""
    import api.media as M
    monkeypatch.setattr(M, '_prog_read', lambda: {
        'movie:7': {'pos': 2400, 'duration': 9000, 'watched': False, 'at': 100},
    })
    monkeypatch.setattr(M, '_get',
                        lambda which, path, **kw: [{**_PELI, 'hasFile': False}] if which == 'radarr' else [])

    assert client.get('/api/media/continue').get_json()['items'] == []


def test_radarr_caido_no_tumba_las_series(client, monkeypatch):
    """La regla de siempre: un fallo al leer las películas no puede vaciar lo que sí se sabe."""
    import api.media as M
    monkeypatch.setattr(M, '_prog_read', lambda: {
        'series:1:19': {'pos': 300, 'duration': 3000, 'watched': False, 'at': 200},
        'movie:7': {'pos': 2400, 'duration': 9000, 'watched': False, 'at': 100},
    })

    def falla_radarr(which, path, **kw):
        if which == 'radarr':
            raise RuntimeError('Radarr caído')
        if path == 'series':
            return [{'id': 1, 'title': 'Breaking Bad', 'statistics': {}}]
        return [{'id': 19, 'seasonNumber': 1, 'episodeNumber': 1, 'title': 'Pilot', 'hasFile': True}]

    monkeypatch.setattr(M, '_get', falla_radarr)

    items = client.get('/api/media/continue').get_json()['items']
    assert [i['title'] for i in items] == ['Breaking Bad']
