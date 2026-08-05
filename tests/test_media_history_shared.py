"""Cine escribe en el historial COMPARTIDO, y por eso la Retrospectiva deja de ignorarlo.

Sólo `anime.py` alimentaba `history_store('watch')`, así que «tu mes» / «tu año» hacía como si las
series y las películas no existieran. El almacén ya era genérico: heredarlo era escribir en él.

Lo que se fija aquí:
  · el id lleva prefijo `media:` (no puede chocar con un id de AniList) y agrupa por OBRA;
  · se apunta SÓLO en la transición a visto, no en cada latido del reproductor;
  · viaja la duración REAL, o una película de 2 h 46 contaría como un episodio de anime.

Correr:  python -m pytest tests/test_media_history_shared.py -q
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Todo el estado (progreso e historial) en un directorio de usar y tirar."""
    import api.runtime as R
    monkeypatch.setattr(R, 'MANGA_DIR', str(tmp_path), raising=False)
    import api.history_store as H
    monkeypatch.setattr(H, 'manga_dir', lambda: str(tmp_path))
    import api.media as M
    monkeypatch.setattr(M, '_prog_path', lambda: tmp_path / 'media_progress.json')

    from flask import Flask
    app = Flask(__name__)
    app.register_blueprint(M.media_bp, url_prefix='/api/media')
    c = app.test_client()
    c._hist = lambda: H.read_live('watch')
    return c


def test_terminar_un_episodio_lo_apunta_en_el_historial(client):
    client.post('/api/media/progress', json={
        'key': 'series:5:182', 'position': 1660, 'duration': 1668, 'ended': True,
        'title': 'The Bear', 'cover': 'c.jpg', 'episode': 1,
    })
    h = client._hist()
    assert len(h) == 1
    assert h[0]['anime_id'] == 'media:series:5'     # agrupa por OBRA, como cuenta la retrospectiva
    assert h[0]['title'] == 'The Bear' and h[0]['episode'] == 1
    assert h[0]['duration'] == 1668                 # la real, no una estimación


def test_una_pelicula_cuenta_como_una_cosa_vista(client):
    client.post('/api/media/progress', json={
        'key': 'movie:4', 'position': 0, 'duration': 9960, 'ended': True, 'title': 'Dune',
    })
    h = client._hist()
    assert [(e['anime_id'], e['episode'], e['duration']) for e in h] == [('media:movie:4', 1, 9960)]


def test_los_latidos_del_reproductor_no_escriben_una_entrada_cada_uno(client):
    """El reproductor manda progreso cada pocos segundos. Sólo la TRANSICIÓN a visto se apunta:
    si no, el último minuto de un episodio dejaría una ristra de entradas y la retrospectiva
    contaría visionados que no existieron."""
    base = {'key': 'series:5:182', 'duration': 1668, 'title': 'The Bear', 'episode': 1}
    for pos in (1600, 1650, 1660):        # los tres pasan del umbral de «visto»
        client.post('/api/media/progress', json={**base, 'position': pos, 'ended': False})
    assert len(client._hist()) == 1


def test_ir_por_la_mitad_no_apunta_nada(client):
    """Un episodio a medias es «seguir viendo», no historial."""
    client.post('/api/media/progress', json={
        'key': 'series:5:182', 'position': 300, 'duration': 1668, 'ended': False, 'title': 'The Bear',
    })
    assert client._hist() == []


def test_un_fallo_del_historial_no_pierde_el_progreso(client, monkeypatch):
    """El progreso es el dato irremplazable; el historial, un registro. Si apuntar revienta, la
    posición tiene que quedar guardada igual — y el fallo, contado (no tragado en silencio)."""
    import api.history_store as H
    monkeypatch.setattr(H, 'append', lambda *a, **k: (_ for _ in ()).throw(RuntimeError('disco')))

    r = client.post('/api/media/progress', json={
        'key': 'movie:4', 'position': 0, 'duration': 9960, 'ended': True, 'title': 'Dune',
    })
    assert r.status_code == 200 and r.get_json()['watched'] is True

    import api.media as M
    assert M._prog_read()['movie:4']['watched'] is True
