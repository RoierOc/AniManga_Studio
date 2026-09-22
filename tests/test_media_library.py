"""`/api/media/library`: que Radarr esté caído NO puede vaciar las series de Sonarr.

Es la regla más cara del proyecto ("falló ≠ no había"): un `[]` que significa las dos cosas ya
nos hizo leer "el rescate no sirve" cuando lo que pasaba es que Suwayomi estaba muerta. Aquí las
dos listas se piden por separado y el fallo viaja aparte, en `errors`.

Correr:  python -m pytest tests/test_media_library.py -q
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


def _fake_get(series=None, movies=None, fail=()):
    def inner(which, path, **kw):
        if which in fail:
            raise RuntimeError(f'{which} caído')
        return (series if which == 'sonarr' else movies) or []
    return inner


def test_radarr_caido_no_vacia_las_series(client, monkeypatch):
    import api.media as M
    monkeypatch.setattr(M, '_get', _fake_get(
        series=[{'id': 1, 'title': 'The Wire', 'statistics': {'episodeFileCount': 60}}],
        fail=('radarr',)))
    d = client.get('/api/media/library').get_json()

    assert [s['title'] for s in d['series']] == ['The Wire']
    assert d['movies'] == []
    # …y el vacío de películas viene ETIQUETADO como fallo, no como "no tienes películas".
    assert 'radarr' in d['errors'] and d['errors']['radarr']
    assert 'sonarr' not in d['errors']


def test_biblioteca_vacia_de_verdad_no_reporta_error(client, monkeypatch):
    """El caso contrario: sin nada seguido, `errors` está vacío. Si aquí apareciera un error,
    la UI no podría distinguir "empieza a añadir series" de "algo está roto"."""
    import api.media as M
    monkeypatch.setattr(M, '_get', _fake_get())
    d = client.get('/api/media/library').get_json()
    assert d == {'series': [], 'movies': [], 'errors': {}}


def test_busqueda_fallida_da_502_no_lista_vacia(client, monkeypatch):
    """Si Sonarr no responde, la búsqueda NO puede devolver `results: []`: el usuario leería
    "esa serie no existe" cuando lo que pasa es que el servicio está caído."""
    import api.media as M
    monkeypatch.setattr(M, '_get', lambda *a, **k: (_ for _ in ()).throw(RuntimeError('caído')))
    r = client.get('/api/media/search?q=breaking+bad')
    assert r.status_code == 502
    assert 'results' not in r.get_json()


def test_alta_reenvia_el_objeto_del_lookup(client, monkeypatch):
    """El payload del alta debe ser el objeto de `lookup` ENTERO + los campos del alta.
    Reconstruirlo a mano pierde imágenes/temporadas/titleSlug y Sonarr lo rechaza o crea basura."""
    import api.media as M
    lookup = {'title': 'The Wire', 'tvdbId': 79126, 'titleSlug': 'the-wire',
              'images': [{'coverType': 'poster'}], 'seasons': [{'seasonNumber': 1}]}
    sent = {}
    monkeypatch.setattr(M, '_get', lambda which, path, **kw: (
        [lookup] if 'lookup' in path else
        [{'path': '/mnt/d/Media/TV', 'freeSpace': 1}] if path == 'rootfolder' else
        [{'id': 4, 'name': 'HD-1080p'}]))
    monkeypatch.setattr(M, '_post', lambda which, path, payload: (
        sent.update(payload) or {'id': 7, 'title': payload['title']}))

    r = client.post('/api/media/add', json={
        'kind': 'series', 'id': 79126, 'profile': 4, 'root': '/mnt/d/Media/TV',
    })
    assert r.get_json() == {'ok': True, 'already': False, 'id': 7, 'title': 'The Wire'}
    assert sent['seasons'] == [{'seasonNumber': 1}] and sent['titleSlug'] == 'the-wire'
    assert sent['rootFolderPath'] == '/mnt/d/Media/TV' and sent['qualityProfileId'] == 4
    # Nada de descargar al dar de alta: ver los dos tests de "nada se descarga solo" más abajo.
    assert sent['addOptions'] == {'searchForMissingEpisodes': False, 'monitor': 'none'}


def test_alta_rechaza_una_carpeta_raiz_que_no_ofrece_sonarr(client, monkeypatch):
    import api.media as M
    sent = []
    monkeypatch.setattr(M, '_get', lambda which, path, **kw: (
        [{'tvdbId': 79126, 'title': 'The Wire'}] if 'lookup' in path else
        [{'path': '/mnt/d/Media/TV'}] if path == 'rootfolder' else
        [{'id': 4, 'name': 'HD-1080p'}]))
    monkeypatch.setattr(M, '_post', lambda *args: (sent.append(args) or {'id': 7}))

    response = client.post('/api/media/add', json={
        'kind': 'series', 'id': 79126, 'root': '/etc',
    })

    assert response.status_code == 400
    assert not sent


def test_alta_repetida_no_duplica(client, monkeypatch):
    """Un `id` no nulo en el lookup significa que ya está en la biblioteca: hay que decirlo,
    no crear un duplicado ni lanzar otra búsqueda de releases."""
    import api.media as M
    monkeypatch.setattr(M, '_get', lambda *a, **k: [{'id': 1, 'title': 'The Wire', 'tvdbId': 79126}])
    monkeypatch.setattr(M, '_post', lambda *a, **k: pytest.fail('no debe crear nada'))
    assert client.post('/api/media/add', json={'kind': 'series', 'id': 79126}).get_json()['already'] is True


def test_progreso_umbral_y_almacen_propio(client, monkeypatch, tmp_path):
    """El progreso va a SU fichero, no a la biblioteca de anime (mezclarlos haría que un
    backup de anime arrastrase series). Y al terminar se guarda pos 0: reanudar en el
    último segundo no sirve de nada."""
    import api.media as M
    monkeypatch.setattr(M, '_prog_path', lambda: tmp_path / 'media_progress.json')

    # Faltan 100 s de 3500 → terminado (umbral: <120 s o ≥90 %).
    assert client.post('/api/media/progress',
                       json={'key': 'series:1:5', 'position': 3400, 'duration': 3500}
                       ).get_json() == {'ok': True, 'watched': True}
    # A mitad, no.
    assert client.post('/api/media/progress',
                       json={'key': 'movie:9', 'position': 600, 'duration': 5400}
                       ).get_json()['watched'] is False

    saved = M._prog_read()
    assert {k: saved['series:1:5'][k] for k in ('pos', 'watched', 'duration')} == {
        'pos': 0, 'watched': True, 'duration': 3500}
    assert saved['movie:9']['pos'] == 600
    # `at` es lo que permite ordenar "Seguir viendo" por lo más reciente: sin él, el riel
    # saldría en un orden arbitrario. Los registros VIEJOS no lo tienen y valen 0 (van al final).
    assert saved['series:1:5']['at'] > 0
    assert (tmp_path / 'media_progress.json').exists()


def test_progreso_sin_key_es_400(client, monkeypatch, tmp_path):
    """Sin clave no se puede atribuir a nada: mejor rechazarlo que escribir un registro huérfano."""
    import api.media as M
    monkeypatch.setattr(M, '_prog_path', lambda: tmp_path / 'p.json')
    assert client.post('/api/media/progress', json={'position': 10}).status_code == 400


def test_resolve_no_adivina_cuando_hay_ambiguedad(client, monkeypatch):
    """Traducir TMDB → Sonarr por título puede confundir remakes o series homónimas. Descargar
    la obra equivocada es peor que preguntar, así que sin coincidencia clara devuelve
    `match: None` + candidatos, y NO elige el primero."""
    import api.media as M
    monkeypatch.setattr(M, '_get', lambda *a, **k: [
        {'title': 'The Office', 'year': 2005, 'tvdbId': 73244},
        {'title': 'The Office', 'year': 2001, 'tvdbId': 78107},
    ])
    d = client.get('/api/media/resolve?kind=series&title=The Office').get_json()
    assert d['match'] is None
    assert len(d['candidates']) == 2

    # Con el año, deja de ser ambiguo.
    d2 = client.get('/api/media/resolve?kind=series&title=The Office&year=2001').get_json()
    assert d2['match']['ext_id'] == 78107


def test_discover_expone_el_titulo_original(client, monkeypatch):
    """TMDB responde en español y Sonarr indexa por título original: sin `title_original`,
    "La casa del dragón" jamás casaría con "House of the Dragon" (verificado en vivo)."""
    import api.media as M
    monkeypatch.setattr(M, '_tmdb', lambda path, **kw: {'results': [
        {'id': 94997, 'name': 'La casa del dragón', 'original_name': 'House of the Dragon',
         'first_air_date': '2022-08-21', 'vote_average': 8.4, 'poster_path': '/x.jpg'}]})
    r = client.get('/api/media/discover?kind=series&list=trending').get_json()['results'][0]
    assert r['title'] == 'La casa del dragón'
    assert r['title_original'] == 'House of the Dragon'
    assert r['year'] == 2022 and r['poster'].startswith('https://image.tmdb.org/')


def test_discover_aparta_anime_pero_no_animacion_occidental(client, monkeypatch):
    """El anime tiene su propia sección, así que aquí estorba. Se detecta por la INTERSECCIÓN
    japonés + animación: filtrar solo por género 16 tiraría Arcane o Rick y Morty, y filtrar
    solo por idioma tiraría el live-action japonés, que sí pertenece a esta sección."""
    import api.media as M
    monkeypatch.setattr(M, '_tmdb', lambda path, **kw: {'results': [
        {'id': 1, 'name': 'Frieren', 'original_language': 'ja', 'genre_ids': [16, 10765]},
        {'id': 2, 'name': 'Arcane', 'original_language': 'en', 'genre_ids': [16, 10765]},
        {'id': 3, 'name': 'Shogun', 'original_language': 'ja', 'genre_ids': [18]},
        {'id': 4, 'name': 'Silo', 'original_language': 'en', 'genre_ids': [18]},
    ]})
    d = client.get('/api/media/discover?kind=series&list=trending').get_json()

    assert [r['title'] for r in d['results']] == ['Arcane', 'Shogun', 'Silo']
    # El recuento viaja para que una lista corta no se lea como un fallo de TMDB.
    assert d['skipped_anime'] == 1


def test_discover_fallo_de_tmdb_es_502(client, monkeypatch):
    import api.media as M
    monkeypatch.setattr(M, '_tmdb', lambda *a, **k: (_ for _ in ()).throw(RuntimeError('sin clave')))
    r = client.get('/api/media/discover?kind=movie&list=popular')
    assert r.status_code == 502 and 'results' not in r.get_json()


def test_lista_vacia_de_releases_dice_si_el_indexer_esta_caido(client, monkeypatch):
    """MEDIDO EN VIVO: apibay.org dio timeout, Prowlarr desactivó The Pirate Bay y las búsquedas
    siguientes se hicieron contra NINGÚN indexer → 0 resultados sin un solo error. Un `[]` que
    significa "falló" y se lee como "no hay torrents". La lista vacía debe viajar con el estado
    de los indexers para que la UI pueda decir cuál es cuál."""
    import api.media as M
    monkeypatch.setattr(M, '_get_slow', lambda *a, **k: [])
    monkeypatch.setattr(M, '_get', lambda which, path, **kw: (
        [{'indexerId': 1, 'disabledTill': '2026-07-20T20:52:49Z', 'mostRecentFailure': 'x'}]
        if path == 'indexerstatus' else [{'id': 1, 'name': 'The Pirate Bay'}]))

    d = client.get('/api/media/releases?kind=episode&id=20').get_json()
    assert d['releases'] == []
    assert d['indexers_down'][0]['name'] == 'The Pirate Bay'


def test_con_releases_no_se_consulta_el_estado_de_indexers(client, monkeypatch):
    """Si hay resultados, el estado de los indexers no aporta nada: no se pide (una llamada
    menos por búsqueda) y viaja vacío."""
    import api.media as M
    monkeypatch.setattr(M, '_get_slow', lambda *a, **k: [
        {'guid': 'g1', 'indexerId': 1, 'title': 'X', 'size': 1, 'seeders': 5, 'quality': {}}])
    monkeypatch.setattr(M, '_get', lambda *a, **k: pytest.fail('no debe consultarse'))
    d = client.get('/api/media/releases?kind=episode&id=20').get_json()
    assert len(d['releases']) == 1 and d['indexers_down'] == []


def test_quitar_de_biblioteca_limpia_el_progreso_y_respeta_los_ficheros(client, monkeypatch, tmp_path):
    """Dos cosas distintas: quitar de la biblioteca (reversible) y borrar los ficheros (no).
    Por defecto NO se borran. Y el progreso SÍ se limpia siempre: si no, al volver a añadir la
    serie reaparecerían marcas de episodios que ya no existen (ya pasó en manga)."""
    import api.media as M
    monkeypatch.setattr(M, '_prog_path', lambda: tmp_path / 'p.json')
    monkeypatch.setattr(M, '_apikey', lambda app: 'k')
    seen = {}

    class _Resp:
        status_code = 200
        def raise_for_status(self): pass

    def fake_delete(url, **kw):
        seen['url'] = url
        seen['params'] = kw.get('params')
        return _Resp()
    monkeypatch.setattr(M.http_requests, 'delete', fake_delete)

    M._prog_write({'series:7:1': {'pos': 10}, 'series:7:2': {'pos': 0},
                   'series:70:1': {'pos': 5},        # otra serie: NO debe tocarse
                   'movie:7': {'pos': 3}})           # una peli con el mismo número tampoco

    d = client.delete('/api/media/library/series/7', json={}).get_json()
    assert d == {'ok': True, 'progress_cleared': 2}
    assert seen['params']['deleteFiles'] == 'false'      # por defecto se CONSERVAN
    assert sorted(M._prog_read()) == ['movie:7', 'series:70:1']

    client.delete('/api/media/library/movie/3', json={'delete_files': True})
    assert seen['params']['deleteFiles'] == 'true'
    assert '/movie/3' in seen['url']


def test_status_distingue_apagado_de_ok(client, monkeypatch):
    import api.media as M
    monkeypatch.setattr(M, '_get', _fake_get(fail=('radarr',)))
    monkeypatch.setattr(M, '_get', lambda which, path, **kw: (
        {'version': '4.0'} if which == 'sonarr' else (_ for _ in ()).throw(RuntimeError('nope'))))
    d = client.get('/api/media/status').get_json()
    assert d['sonarr'] == {'online': True, 'version': '4.0', 'error': ''}
    assert d['radarr']['online'] is False and d['radarr']['error']


# ── Nada se descarga solo ──────────────────────────────────────────────────────
# Añadir a la biblioteca es catálogo, NO una orden de descarga. Son dos interruptores y hacen
# falta los dos: `searchFor*` (la búsqueda del alta) y `monitor: none` (el RSS sync, que se pone
# a bajar por su cuenta 15 min después, cuando ya nadie mira).
def test_anadir_una_serie_no_dispara_ninguna_descarga(client, monkeypatch):
    import api.media as M
    enviado = {}
    monkeypatch.setattr(M, '_get', lambda which, path, **kw: (
        [{'tvdbId': 42, 'title': 'The Wire'}] if 'lookup' in path
        else [{'path': '/mnt/d/Media/TV'}] if path == 'rootfolder'
        else [{'id': 1}]))

    def _post(which, path, payload):
        enviado.update(payload)
        return {'id': 7, 'title': 'The Wire'}
    monkeypatch.setattr(M, '_post', _post)

    r = client.post('/api/media/add', json={'kind': 'series', 'id': 42})
    assert r.get_json()['ok'] is True
    assert enviado['addOptions']['searchForMissingEpisodes'] is False
    assert enviado['addOptions']['monitor'] == 'none'


def test_anadir_una_pelicula_entra_sin_monitorizar(client, monkeypatch):
    import api.media as M
    enviado = {}
    monkeypatch.setattr(M, '_get', lambda which, path, **kw: (
        [{'tmdbId': 99, 'title': 'Dune'}] if 'lookup' in path
        else [{'path': '/mnt/d/Media/Movies'}] if path == 'rootfolder'
        else [{'id': 1}]))
    monkeypatch.setattr(M, '_post', lambda which, path, payload: (enviado.update(payload), {'id': 3})[1])

    client.post('/api/media/add', json={'kind': 'movie', 'id': 99})
    # En Radarr el interruptor del RSS es la peli entera: sin monitorizar no se baja sola.
    assert enviado['monitored'] is False
    assert enviado['addOptions']['searchForMovie'] is False


# El reverso: sin monitorizar, Sonarr NO importa lo descargado. Elegir un torrent tiene que
# abrir la puerta del tamaño justo — ese episodio, no la serie entera.
def test_elegir_un_torrent_monitoriza_solo_ese_episodio(client, monkeypatch):
    import api.media as M
    puesto = {}
    monkeypatch.setattr(M, '_put', lambda which, path, payload: puesto.update({'path': path, **payload}))
    monkeypatch.setattr(M, '_post', lambda which, path, payload: {})

    r = client.post('/api/media/releases/grab',
                    json={'kind': 'series', 'id': 512, 'guid': 'g', 'indexer_id': 1})
    assert r.get_json()['ok'] is True
    assert puesto['path'] == 'episode/monitor'
    assert puesto['episodeIds'] == [512] and puesto['monitored'] is True


def test_una_temporada_monitoriza_sus_episodios(client, monkeypatch):
    import api.media as M
    puesto = {}
    monkeypatch.setattr(M, '_get', lambda which, path, **kw: [{'id': 11}, {'id': 12}])
    monkeypatch.setattr(M, '_put', lambda which, path, payload: puesto.update(payload))
    monkeypatch.setattr(M, '_post', lambda which, path, payload: {})

    client.post('/api/media/releases/grab',
                json={'kind': 'series', 'id': 3, 'season': 2, 'guid': 'g', 'indexer_id': 1})
    assert puesto['episodeIds'] == [11, 12]


# Si monitorizar falla, NO se descarga: el torrent bajaría para no importarse nunca, y eso es
# indistinguible de "no pasó nada" mirando la app.
def test_si_falla_monitorizar_no_se_descarga(client, monkeypatch):
    import api.media as M
    bajado = []
    monkeypatch.setattr(M, '_put', lambda *a, **k: (_ for _ in ()).throw(RuntimeError('sonarr caído')))
    monkeypatch.setattr(M, '_post', lambda which, path, payload: bajado.append(path))

    r = client.post('/api/media/releases/grab',
                    json={'kind': 'series', 'id': 5, 'guid': 'g', 'indexer_id': 1})
    assert r.status_code == 502
    assert bajado == []


def test_resolve_por_tmdb_id_no_pregunta(client, monkeypatch):
    """El «varias coincidencias, elige la correcta» salía de emparejar por TÍTULO: TMDB responde
    en español y Sonarr indexa en inglés, así que cualquier obra no inglesa acababa en el
    selector. Sonarr/Radarr aceptan `term=tmdb:<id>` y devuelven UNA obra: con el id que ya
    trae Descubrir no hay nada que adivinar."""
    import api.media as M
    seen = {}

    def fake_get(app, path, **kw):
        seen['term'] = kw.get('term')
        return [{'title': 'House of the Dragon', 'year': 2022, 'tvdbId': 371572, 'id': 0}]

    monkeypatch.setattr(M, '_get', fake_get)
    d = client.get('/api/media/resolve?kind=series&tmdb_id=94997'
                   '&title=House of the Dragon').get_json()
    assert seen['term'] == 'tmdb:94997'
    assert d['match']['ext_id'] == 371572 and d['match']['already'] is False


def test_discover_marca_lo_que_ya_tienes(client, monkeypatch):
    """Sin esto, la única forma de saber si ya tenías una serie era pulsar «Añadir». Sonarr trae
    `tmdbId` en cada serie, así que el cruce es por id exacto. Y si Sonarr no responde, el campo
    NO viaja: decir "no la tienes" cuando no se pudo preguntar es la trampa de siempre
    («falló» ≠ «no había»)."""
    import api.media as M
    monkeypatch.setattr(M, '_tmdb', lambda path, **kw: {'results': [
        {'id': 94997, 'name': 'La casa del dragón', 'first_air_date': '2022-08-21'},
        {'id': 1396, 'name': 'Breaking Bad', 'first_air_date': '2008-01-20'}]})

    monkeypatch.setattr(M, '_get', lambda *a, **k: [{'tmdbId': 1396, 'title': 'Breaking Bad'}])
    r = client.get('/api/media/discover?kind=series&list=trending').get_json()['results']
    assert [x['already'] for x in r] == [False, True]

    def boom(*a, **k):
        raise RuntimeError('Sonarr caído')

    monkeypatch.setattr(M, '_get', boom)
    r2 = client.get('/api/media/discover?kind=series&list=trending').get_json()['results']
    assert all('already' not in x for x in r2)
