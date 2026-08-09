"""El endpoint que sirve un episodio al móvil NO acepta rutas del cliente.

`resolve_episode_video` sí las acepta, y es razonable para el front del PC. Pero el mismo atajo en
un endpoint que devuelve BYTES a un cliente de la red es leer cualquier fichero de la máquina por
HTTP. Estos tests fijan que la ruta salga siempre de la biblioteca del servidor.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

import api.anime as anime


def _app():
    from flask import Flask
    app = Flask(__name__)
    app.register_blueprint(anime.anime_bp, url_prefix='/api/anime')
    return app.test_client()


def test_la_ruta_sale_de_la_biblioteca_no_del_cliente(tmp_path, monkeypatch):
    video = tmp_path / 'S01E03.mkv'
    video.write_bytes(b'0123456789')
    monkeypatch.setattr(anime, '_lib_read', lambda: {
        '42': {'local_path': str(tmp_path), 'episodes': {}},
    })
    visto = {}

    def falso_resolve(datos):
        visto.update(datos)
        return str(video), None

    monkeypatch.setattr(anime, 'resolve_episode_video', falso_resolve)

    r = _app().get('/api/anime/stream/42/3?local_path=/etc')

    assert r.status_code == 200
    assert visto['local_path'] == str(tmp_path), 'el local_path debe venir de la biblioteca'
    assert '/etc' not in str(visto), 'el cliente no puede colar una ruta'


def test_serie_desconocida_es_404_no_un_fichero(monkeypatch):
    monkeypatch.setattr(anime, '_lib_read', lambda: {})

    assert _app().get('/api/anime/stream/99/1').status_code == 404


def test_episodio_sin_origen_no_llega_a_resolver(monkeypatch):
    # Ni carpeta local ni torrent: no hay nada que servir, y decirlo es distinto de fallar.
    monkeypatch.setattr(anime, '_lib_read', lambda: {'42': {'episodes': {'1': {}}}})

    def explota(_):
        raise AssertionError('no debería intentar resolver sin origen')

    monkeypatch.setattr(anime, 'resolve_episode_video', explota)

    assert _app().get('/api/anime/stream/42/1').status_code == 404


def test_sirve_por_rangos(tmp_path, monkeypatch):
    # Sin rangos no hay saltar al minuto 12: mpv tendría que descargar el MKV entero.
    video = tmp_path / 'x.mkv'
    video.write_bytes(b'0123456789')
    monkeypatch.setattr(anime, '_lib_read', lambda: {'42': {'local_path': str(tmp_path), 'episodes': {}}})
    monkeypatch.setattr(anime, 'resolve_episode_video', lambda d: (str(video), None))

    r = _app().get('/api/anime/stream/42/1', headers={'Range': 'bytes=4-6'})

    assert r.status_code == 206
    assert r.data == b'456'
