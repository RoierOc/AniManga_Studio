"""Puente manga ⇄ anime: las tres costuras que fallarían en silencio.

1. **Qué obra es «la original».** AniList cuelga del anime de One Piece un one-shot de 1996
   («Romance Dawn»), un recopilatorio y el manga de verdad. Ordenando por año ganaba el one-shot
   y la ficha decía «One Piece sale de un one-shot de 1 capítulo» — un dato falso pero
   plausible, que es la clase que nadie reporta y que hace desconfiar de toda la sección.
2. **«Falló» ≠ «no tiene adaptación».** Si AniList no responde, devolver una lista vacía sería
   indistinguible de una obra original sin manga detrás. Tiene que ser un 502.
3. **`in_library` ausente ≠ `False`.** Si no se pudo leer la otra biblioteca, la UI no puede
   decir «no la tienes».

Correr:  .venv/bin/python -m pytest tests/test_bridge_counterpart.py -q
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))


@pytest.fixture
def client(monkeypatch):
    import api.bridge as B
    from flask import Flask
    # Sin caché de disco entre tests: si no, el primero sella la respuesta para los demás.
    monkeypatch.setattr(B, 'cache_get', lambda *a, **k: None)
    monkeypatch.setattr(B, 'cache_set', lambda *a, **k: None)
    # Sin red hacia MangaUpdates: la cobertura tiene sus propios tests, aquí sólo estorbaría
    # (comparte el cliente HTTP con AniList y se comería el mock).
    monkeypatch.setattr(B, '_cobertura', lambda *a, **k: None)
    app = Flask(__name__)
    app.register_blueprint(B.bridge_bp, url_prefix='/api/bridge')
    return app.test_client()


def _al(monkeypatch, media):
    """Sustituye la llamada a AniList por una respuesta fija."""
    import api.bridge as B

    class R:
        def raise_for_status(self): pass
        def json(self): return {'data': {'Media': media}}

    monkeypatch.setattr(B.http_requests, 'post', lambda *a, **k: R())


def _nodo(nid, tipo, fmt, titulo, year, **extra):
    return {'relationType': 'SOURCE',
            'node': {'id': nid, 'type': tipo, 'format': fmt, 'status': 'FINISHED',
                     'seasonYear': year, 'startDate': {'year': year},
                     'title': {'romaji': titulo, 'english': titulo},
                     'coverImage': {'large': ''}, **extra}}


def test_el_one_shot_no_se_confunde_con_la_obra_original(client, monkeypatch):
    """El caso One Piece, con los datos reales de AniList."""
    import api.bridge as B
    monkeypatch.setattr(B, '_manga_al_ids', lambda: set())
    _al(monkeypatch, {
        'id': 21, 'status': 'RELEASING',
        'title': {'romaji': 'One Piece', 'english': 'One Piece'},
        'relations': {'edges': [
            _nodo(30013, 'MANGA', 'ONE_SHOT', 'Romance Dawn', 1996, chapters=1),
            _nodo(30002, 'MANGA', 'MANGA', 'One Piece', 1997, status='RELEASING'),
            _nodo(97008, 'MANGA', 'ONE_SHOT', 'Wanted!', 1998, chapters=5),
        ]},
    })
    d = client.get('/api/bridge/counterpart?al_id=21&from=anime').get_json()

    assert 'One Piece' in d['verdict'] and 'one-shot' not in d['verdict']
    assert 'Romance Dawn' not in d['verdict']
    # Y la obra original va la PRIMERA de la lista, aunque no sea la más antigua.
    assert d['counterparts'][0]['title'] == 'One Piece'


def test_anilist_caido_es_502_no_una_lista_vacia(client, monkeypatch):
    """Una obra original (sin manga) y un AniList caído NO pueden dar la misma respuesta."""
    import api.bridge as B

    def boom(*a, **k):
        raise RuntimeError('sin red')

    monkeypatch.setattr(B.http_requests, 'post', boom)
    r = client.get('/api/bridge/counterpart?al_id=21&from=anime')
    assert r.status_code == 502
    assert 'counterparts' not in (r.get_json() or {})

    # En cambio una obra SIN adaptación es un 200 con la lista vacía: eso sí es "no hay".
    _al(monkeypatch, {'id': 20, 'status': 'FINISHED', 'title': {'romaji': 'x'},
                      'relations': {'edges': []}})
    monkeypatch.setattr(B, '_manga_al_ids', lambda: set())
    d = client.get('/api/bridge/counterpart?al_id=20&from=anime').get_json()
    assert d['counterparts'] == [] and d['verdict'] == ''


def test_biblioteca_ilegible_no_dice_no_la_tienes(client, monkeypatch):
    """`in_library` viaja sólo si se pudo preguntar. Ausente ≠ False."""
    import api.bridge as B
    _al(monkeypatch, {
        'id': 5, 'status': 'RELEASING', 'title': {'romaji': 'm'},
        'relations': {'edges': [_nodo(999, 'ANIME', 'TV', 'Su anime', 2025, episodes=12)]},
    })

    monkeypatch.setattr(B, '_anime_al_ids', lambda: {999})
    d = client.get('/api/bridge/counterpart?al_id=5&from=manga').get_json()
    assert d['counterparts'][0]['in_library'] is True

    monkeypatch.setattr(B, '_anime_al_ids', lambda: set())
    d = client.get('/api/bridge/counterpart?al_id=5&from=manga').get_json()
    assert d['counterparts'][0]['in_library'] is False

    monkeypatch.setattr(B, '_anime_al_ids', lambda: None)   # no se pudo leer
    d = client.get('/api/bridge/counterpart?al_id=5&from=manga').get_json()
    assert 'in_library' not in d['counterparts'][0]


def test_el_manga_sigue_solo_si_el_dato_lo_sostiene(client, monkeypatch):
    """La frase «la historia continúa aquí» sólo se dice con el anime TERMINADO y el manga
    todavía publicándose. Con el anime en emisión sería mentira."""
    import api.bridge as B
    monkeypatch.setattr(B, '_anime_al_ids', lambda: set())

    _al(monkeypatch, {
        'id': 7, 'status': 'RELEASING', 'title': {'romaji': 'm'},
        'relations': {'edges': [_nodo(1, 'ANIME', 'TV', 'T1', 2024, episodes=12)]},
    })
    assert 'el manga sigue publicándose' in client.get(
        '/api/bridge/counterpart?al_id=7&from=manga').get_json()['verdict']

    emitiendo = _nodo(1, 'ANIME', 'TV', 'T1', 2026, episodes=12)
    emitiendo['node']['status'] = 'RELEASING'
    _al(monkeypatch, {'id': 7, 'status': 'RELEASING', 'title': {'romaji': 'm'},
                      'relations': {'edges': [emitiendo]}})
    v = client.get('/api/bridge/counterpart?al_id=7&from=manga').get_json()['verdict']
    assert 'emitiendo' in v and 'sigue publicándose' not in v


def test_la_cobertura_nunca_resta_en_negativo():
    """MangaUpdates cuenta capítulos TRADUCIDOS, no publicados en Japón, así que su
    `latest_chapter` puede ir por DETRÁS de donde llega el anime: medido con Medalist, anime 28 /
    manga 26. Restar ahí daría «te quedan -2» — y una cifra imposible tira la credibilidad de
    toda la sección. Cuando el manga no va por delante, se dice sólo dónde acaba el anime."""
    from api.bridge import _frase_cobertura

    assert _frase_cobertura(None) == ''
    adelante = _frase_cobertura({'ends_at': 80.0, 'latest': 147})
    assert 'capítulo 80' in adelante and 'te quedan 67' in adelante

    detras = _frase_cobertura({'ends_at': 28.0, 'latest': 26})
    assert 'capítulo 28' in detras and 'te quedan' not in detras and '-' not in detras

    # Sin `latest` tampoco se inventa nada, pero el dato útil (dónde acaba) sí se da.
    assert _frase_cobertura({'ends_at': 54.0, 'latest': None}) == ' El anime llega hasta el capítulo 54.'


def test_el_capitulo_final_se_lee_del_texto_libre_de_mangaupdates():
    """El campo lo escriben editores humanos: «Vol 7, Chap 54 Page 4 (S1) / Vol 11, Chap 97 (S2)».
    No se parsea la estructura (cambia de obra a obra): se toma el capítulo MÁS ALTO citado, que
    es donde llega la adaptación más avanzada."""
    from api.metasource.sources.mangaupdates import _max_chap

    assert _max_chap('Vol 7, Chap 54 Page 4 (S1) / Vol 11, Chap 97 (S2)') == 97
    assert _max_chap('Vol 5, Chap 38 (S1) Skips most of Chap 2') == 38
    assert _max_chap('Chap 12.5') == 12.5
    assert _max_chap('') is None
    assert _max_chap('Vol 3') is None        # sin capítulo NO se afirma nada
