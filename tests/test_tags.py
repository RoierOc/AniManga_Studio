"""Etiquetas propias sobre obras (`src/api/tags.py`).

Lo que se fija aquí son las decisiones que fallarían EN SILENCIO: una etiqueta duplicada por
mayúsculas se partiría en dos pills sin que nada se queje, y una entrada vacía que no se borra
hace crecer el fichero para siempre sin cambiar nada visible.
"""
import json

import pytest

from api import tags as T


@pytest.fixture
def cliente(tmp_path, monkeypatch):
    monkeypatch.setattr(T, '_TAGS_FILE', tmp_path / 'tags.json')
    from flask import Flask
    app = Flask(__name__)
    app.register_blueprint(T.tags_bp, url_prefix='/api/tags')
    return app.test_client()


def poner(c, kind, work, tags):
    return c.post('/api/tags/set', json={'kind': kind, 'id': work, 'tags': tags})


def test_sin_fichero_devuelve_vacio_no_error(cliente):
    # "No hay etiquetas todavía" NO puede parecerse a un fallo de lectura.
    assert cliente.get('/api/tags').get_json() == {'manga': {}, 'anime': {}, 'media': {}}


def test_guarda_y_devuelve(cliente):
    poner(cliente, 'manga', 'Witch Hat Atelier', ['releer'])
    assert cliente.get('/api/tags').get_json()['manga'] == {'Witch Hat Atelier': ['releer']}


def test_duplicados_sin_distinguir_mayusculas(cliente):
    # «Releer» y «releer» son la MISMA etiqueta: si no, salen dos pills que filtran distinto.
    r = poner(cliente, 'manga', 'X', ['releer', 'RELEER', '  Releer  '])
    assert r.get_json()['tags'] == ['releer']


def test_recorta_espacios_y_tira_vacias(cliente):
    r = poner(cliente, 'manga', 'X', ['  finde  ', '', '   ', 'ligero'])
    assert r.get_json()['tags'] == ['finde', 'ligero']


def test_lista_vacia_borra_la_entrada(cliente):
    poner(cliente, 'manga', 'X', ['a'])
    poner(cliente, 'manga', 'X', [])
    # No basta con que la respuesta sea []: la fila debe DESAPARECER del fichero.
    assert cliente.get('/api/tags').get_json()['manga'] == {}


def test_manga_y_anime_no_se_pisan(cliente):
    # Un anime con id "123" y una carpeta llamada "123" son obras distintas.
    poner(cliente, 'manga', '123', ['carpeta'])
    poner(cliente, 'anime', '123', ['anilist'])
    d = cliente.get('/api/tags').get_json()
    assert d['manga']['123'] == ['carpeta']
    assert d['anime']['123'] == ['anilist']


@pytest.mark.parametrize('cuerpo', [
    {'kind': 'pelicula', 'id': 'x', 'tags': ['a']},   # dominio inexistente
    {'kind': 'manga', 'id': '', 'tags': ['a']},       # sin obra
    {'kind': 'manga', 'id': '   ', 'tags': ['a']},
])
def test_peticiones_invalidas_dan_400(cliente, cuerpo):
    assert cliente.post('/api/tags/set', json=cuerpo).status_code == 400


def test_topes(cliente):
    r = poner(cliente, 'manga', 'X', ['t' * 80])
    assert len(r.get_json()['tags'][0]) == T._MAX_LEN
    r = poner(cliente, 'manga', 'Y', [f'e{i}' for i in range(40)])
    assert len(r.get_json()['tags']) == T._MAX_PER_WORK


def test_no_revienta_con_un_fichero_corrupto(cliente, tmp_path):
    # Un JSON ilegible no puede tumbar la biblioteca entera: se parte de vacío.
    (tmp_path / 'tags.json').write_text('{esto no es json')
    assert cliente.get('/api/tags').get_json() == {'manga': {}, 'anime': {}, 'media': {}}


def test_el_fichero_es_json_valido_en_disco(cliente, tmp_path):
    poner(cliente, 'anime', '21', ['clásico'])
    d = json.loads((tmp_path / 'tags.json').read_text())
    assert d['anime']['21'] == ['clásico']
