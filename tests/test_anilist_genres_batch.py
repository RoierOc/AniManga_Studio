"""Géneros en lote: lo cacheado NO se vuelve a pedir.

La tarjeta de la biblioteca pinta géneros, y una petición por obra agota la cuota de AniList
(~30/min) antes de terminar la primera pantalla. La costura que puede fallar en silencio es la
caché: si no se consulta, el lote «funciona» igual pero pide 28 fichas cada vez que abres la
biblioteca, y eso sólo se nota como un 429 semanas después.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

import pytest                       # noqa: E402
import api.anilist as A             # noqa: E402


@pytest.fixture
def cliente(monkeypatch, tmp_path):
    monkeypatch.setattr(A, '_cache_get', lambda ns, k, ttl: _CACHE.get(k))
    monkeypatch.setattr(A, '_cache_set', lambda ns, k, v, **kw: _CACHE.__setitem__(k, v))
    from flask import Flask
    app = Flask(__name__)
    app.register_blueprint(A.anilist_bp, url_prefix='/api/anilist')
    return app.test_client()


_CACHE: dict = {}


def test_una_consulta_para_todos_y_luego_ninguna(cliente, monkeypatch):
    _CACHE.clear()
    llamadas = []

    def fake_ql(q, v):
        llamadas.append(v['ids'])
        return {'Page': {'media': [{'id': i, 'genres': ['Action', 'Drama']} for i in v['ids']]}}

    monkeypatch.setattr(A, '_ql', fake_ql)
    r = cliente.post('/api/anilist/genres_by_id', json={'al_ids': [1, 2, 2, 3]})

    assert r.get_json() == {'1': ['Action', 'Drama'], '2': ['Action', 'Drama'], '3': ['Action', 'Drama']}
    assert llamadas == [[1, 2, 3]]                 # una sola consulta, sin el id repetido

    r2 = cliente.post('/api/anilist/genres_by_id', json={'al_ids': [1, 2, 3]})
    assert r2.get_json() == r.get_json()
    assert len(llamadas) == 1                      # todo cacheado: cero peticiones


def test_si_anilist_falla_no_revienta_ni_cachea_el_fallo(cliente, monkeypatch):
    _CACHE.clear()
    monkeypatch.setattr(A, '_ql', lambda q, v: {'_error': 'timeout'})
    r = cliente.post('/api/anilist/genres_by_id', json={'al_ids': [7]})
    assert r.status_code == 200 and r.get_json() == {}
    assert _CACHE == {}                            # un fallo no se guarda como «sin géneros»


def test_por_titulo_resuelve_en_lotes_y_recuerda_el_fallo_en_blanco(cliente, monkeypatch):
    _CACHE.clear()
    llamadas = []

    def fake_ql(q, v):
        llamadas.append(sorted(v.values()))
        # `m0` existe, `m1` no: con Page, el que falta es una lista vacía y NO tumba a su vecino.
        # El candidato lleva su título porque desde el fix de «Real» hay que COMPROBAR que es la
        # obra pedida — sin título no casa, y esa es justo la garantía que se quiere aquí.
        return {'m0': {'media': [{'genres': ['Romance'], 'isAdult': False,
                                  'title': {'romaji': 'Ao no Hako'}, 'tags': []}]},
                'm1': {'media': []}}

    monkeypatch.setattr(A, '_ql', fake_ql)
    r = cliente.post('/api/anilist/genres_by_id', json={'titles': ['Ao no Hako', 'Obra Fantasma']})

    assert r.get_json() == {'Ao no Hako': ['Romance']}       # la que no existe NO inventa géneros
    assert len(llamadas) == 1

    r2 = cliente.post('/api/anilist/genres_by_id', json={'titles': ['Ao no Hako', 'Obra Fantasma']})
    assert r2.get_json() == r.get_json()
    assert len(llamadas) == 1        # ni la encontrada ni la ausente se vuelven a buscar


def test_no_se_traga_el_primer_resultado_de_la_busqueda(cliente, monkeypatch):
    """AniList ordena por POPULARIDAD, no por parecido.

    Buscando «Real» devolvía primero un doujin adulto que ni se llama así, y tres obras de la
    biblioteca acabaron marcadas «Hentai». El fallo es mudo: la obra sale con géneros, sólo que
    de otra obra."""
    _CACHE.clear()
    monkeypatch.setattr(A, '_ql', lambda q, v: {'m0': {'media': [
        {'isAdult': True,  'genres': ['Hentai'], 'synonyms': [],
         'title': {'romaji': 'Ero Gal Iru tte Hontou desu ka!?'}, 'tags': []},
        {'isAdult': False, 'genres': ['Drama', 'Sports'], 'synonyms': [],
         'title': {'romaji': 'Real'}, 'tags': [{'name': 'Seinen', 'rank': 88}]},
    ]}})
    r = cliente.post('/api/anilist/genres_by_id', json={'titles': ['Real']})

    # La que COINCIDE de nombre, y sus etiquetas curadas junto a los géneros.
    assert r.get_json() == {'Real': ['Drama', 'Sports', 'Seinen']}


def test_una_obra_adulta_que_SI_es_la_tuya_no_se_descarta(cliente, monkeypatch):
    """`isAdult` sólo desempata entre las que casan de nombre. Descartarla de entrada tiró 4
    obras correctas de la biblioteca real («A Girl on the Shore» está marcada adulta en AniList
    y es exactamente la que el usuario tiene)."""
    _CACHE.clear()
    monkeypatch.setattr(A, '_ql', lambda q, v: {'m0': {'media': [
        {'isAdult': True, 'genres': ['Drama'], 'synonyms': [],
         'title': {'romaji': 'Umibe no Onnanoko', 'english': 'A Girl on the Shore'}, 'tags': []},
    ]}})
    r = cliente.post('/api/anilist/genres_by_id', json={'titles': ['A Girl on the Shore']})
    assert r.get_json() == {'A Girl on the Shore': ['Drama']}


def test_sin_coincidencia_de_titulo_se_queda_SIN_generos(cliente, monkeypatch):
    """Un género equivocado es peor que ninguno: se cree, y encima se cachea 30 días."""
    _CACHE.clear()
    monkeypatch.setattr(A, '_ql', lambda q, v: {'m0': {'media': [
        {'isAdult': False, 'genres': ['Comedy'], 'synonyms': [],
         'title': {'romaji': 'Otra Obra Distinta'}, 'tags': []},
    ]}})
    assert cliente.post('/api/anilist/genres_by_id', json={'titles': ['Mi Obra']}).get_json() == {}
