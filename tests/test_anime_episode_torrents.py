"""Los torrents de UN episodio, para el móvil.

La consulta se arma con los alias de la ficha del SERVIDOR. Si el cliente pudiera mandar el título,
esto sería una búsqueda libre en Nyaa con la app de puente, que no es lo que se está construyendo.
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


def _torrent(titulo, ep, seeders=1, trusted=False, ih=''):
    return {
        'title': titulo, 'episode': ep, 'seeders': seeders, 'trusted': trusted,
        'info_hash': ih or titulo, 'magnet': '', 'torrent_url': '',
    }


def test_la_consulta_sale_de_la_ficha_del_servidor(monkeypatch):
    monkeypatch.setattr(anime, '_lib_read', lambda: {
        '42': {'title': 'VTuber Legend', 'title_romaji': 'Vtuber Nandaga', 'al_id': None},
    })
    consultas = []

    def falso_nyaa(q, cat='1_2', f='0'):
        consultas.append((q, cat))
        return [_torrent('x - 04', 4)]

    monkeypatch.setattr(anime, '_nyaa_search', falso_nyaa)

    r = _app().get('/api/anime/episode_torrents/42/4')

    assert r.status_code == 200
    assert 'Vtuber Nandaga - 04' in [q for q, _ in consultas], consultas
    # cat 1_0 y no 1_2: con el filtro de inglés las releases en español no salen nunca.
    assert all(c == '1_0' for _, c in consultas)


def test_se_queda_el_episodio_y_los_lotes_y_fuera_lo_demas(monkeypatch):
    monkeypatch.setattr(anime, '_lib_read', lambda: {'42': {'title': 'X', 'al_id': None}})
    monkeypatch.setattr(anime, '_nyaa_search', lambda q, cat='1_2', f='0': [
        _torrent('el bueno', 4, ih='a'),
        _torrent('el lote', 0, ih='b'),
        _torrent('otro episodio', 7, ih='c'),
    ])

    datos = _app().get('/api/anime/episode_torrents/42/4').get_json()

    assert [t['info_hash'] for t in datos] == ['a', 'b']


def test_ordena_por_confianza_y_semillas(monkeypatch):
    monkeypatch.setattr(anime, '_lib_read', lambda: {'42': {'title': 'X', 'al_id': None}})
    monkeypatch.setattr(anime, '_nyaa_search', lambda q, cat='1_2', f='0': [
        _torrent('sin semillas ni sello', 4, seeders=0, ih='c'),
        _torrent('muchas semillas', 4, seeders=99, ih='b'),
        _torrent('de confianza', 4, seeders=2, trusted=True, ih='a'),
    ])

    datos = _app().get('/api/anime/episode_torrents/42/4').get_json()

    assert [t['info_hash'] for t in datos] == ['a', 'b', 'c']


def test_serie_que_no_esta_en_la_biblioteca_es_404(monkeypatch):
    # No es una búsqueda libre: se piden episodios de algo que ya tienes.
    monkeypatch.setattr(anime, '_lib_read', lambda: {})

    assert _app().get('/api/anime/episode_torrents/99/1').status_code == 404
    assert _app().post('/api/anime/episode_download/99/1', json={'magnet': 'x'}).status_code == 404


def test_el_destino_lo_pone_el_servidor_no_el_cliente(monkeypatch):
    lib = {'42': {'title': 'X', 'episodes': {}}}
    monkeypatch.setattr(anime, '_lib_read', lambda: lib)
    monkeypatch.setattr(anime, '_lib_write', lambda d: None)
    monkeypatch.setattr(anime, '_anime_save_dir', lambda t: r'D:\Anime\X')
    visto = {}

    def falso_add(link, save_dir):
        visto.update(link=link, save_dir=save_dir)
        return True, 'Ok.'

    monkeypatch.setattr(anime, '_qbt_add_link', falso_add)

    r = _app().post('/api/anime/episode_download/42/4', json={
        'magnet': 'magnet:?xt=1', 'save_path': '/etc', 'savepath': '/etc',
        'torrent_title': 'X - 04', 'info_hash': 'ABC',
    })

    assert r.get_json() == {'ok': True}
    assert visto['save_dir'] == r'D:\Anime\X', 'el destino sale de la biblioteca del servidor'
    assert '/etc' not in visto['save_dir']


def test_el_episodio_queda_apuntado_en_la_biblioteca(monkeypatch):
    lib = {'42': {'title': 'X'}}
    escrito = {}
    monkeypatch.setattr(anime, '_lib_read', lambda: lib)
    monkeypatch.setattr(anime, '_lib_write', lambda d: escrito.update(d))
    monkeypatch.setattr(anime, '_anime_save_dir', lambda t: '')
    monkeypatch.setattr(anime, '_qbt_add_link', lambda l, s: (True, 'Ok.'))

    _app().post('/api/anime/episode_download/42/4',
                json={'magnet': 'm', 'torrent_title': 'X - 04', 'info_hash': 'ABC'})

    ep = escrito['42']['episodes']['4']
    assert ep['info_hash'] == 'abc', 'el hash se guarda en minúsculas, como el resto del módulo'
    assert ep['title'] == 'X - 04'


def test_si_qbittorrent_no_lo_acepta_no_se_apunta_nada(monkeypatch):
    # Apuntar el episodio de una descarga que no arrancó deja la ficha diciendo que viene algo
    # que no viene, y eso no se cae solo: se queda ahí para siempre.
    lib = {'42': {'title': 'X', 'episodes': {}}}
    monkeypatch.setattr(anime, '_lib_read', lambda: lib)
    monkeypatch.setattr(anime, '_anime_save_dir', lambda t: '')
    monkeypatch.setattr(anime, '_qbt_add_link', lambda l, s: (False, 'Fails.'))

    def explota(_):
        raise AssertionError('no debería escribir la biblioteca')

    monkeypatch.setattr(anime, '_lib_write', explota)

    r = _app().post('/api/anime/episode_download/42/4', json={'magnet': 'm'})

    assert r.status_code == 502
    assert r.get_json()['ok'] is False
