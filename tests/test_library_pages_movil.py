"""Las páginas de un capítulo, para el cliente de Android.

Una obra descargada puede estar en dos formatos —páginas planas en la carpeta de la serie o una
subcarpeta por capítulo— y repartida entre discos. Reproducir esa lógica en el móvil sería copiarla
mal y descubrirlo el día que abra una obra del otro formato; estos tests fijan que la resuelva el
servidor, que es quien tiene los ficheros delante.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

import api.library as library


def _app():
    from flask import Flask
    app = Flask(__name__)
    app.register_blueprint(library.library_bp, url_prefix='/api/library')
    return app.test_client()


def _obra(tmp_path, nombre='Obra'):
    d = tmp_path / nombre
    d.mkdir()
    return d


def test_paginas_planas_en_orden(tmp_path, monkeypatch):
    d = _obra(tmp_path)
    # A propósito creadas al revés: el orden no puede depender de lo que devuelva el sistema.
    for n in (3, 1, 2):
        (d / f'ch0036_00{n}.png').write_bytes(b'x')
    (d / 'ch0037_001.png').write_bytes(b'x')
    monkeypatch.setattr(library, 'series_dirs', lambda t: [d], raising=False)
    monkeypatch.setitem(sys.modules['api.roots'].__dict__, 'series_dirs', lambda t: [d])

    r = _app().get('/api/library/pages/Obra?chapter=36')

    assert r.status_code == 200
    assert r.get_json()['pages'] == [
        'Obra/ch0036_001.png', 'Obra/ch0036_002.png', 'Obra/ch0036_003.png',
    ], 'sólo las de ese capítulo, y ordenadas'


def test_una_subcarpeta_por_capitulo(tmp_path, monkeypatch):
    d = _obra(tmp_path)
    sub = d / 'ch0012'
    sub.mkdir()
    (sub / '002.jpg').write_bytes(b'x')
    (sub / '001.jpg').write_bytes(b'x')
    # Los directorios ocultos son metadatos, no capítulos.
    (d / '.original_art').mkdir()
    monkeypatch.setitem(sys.modules['api.roots'].__dict__, 'series_dirs', lambda t: [d])

    r = _app().get('/api/library/pages/Obra?chapter=12')

    assert r.get_json()['pages'] == ['Obra/ch0012/001.jpg', 'Obra/ch0012/002.jpg']


def test_la_obra_repartida_entre_discos_es_una_sola(tmp_path, monkeypatch):
    # Es la razón de ser de `series_dirs`: caps en C: y en D: se leen como una obra.
    a, b = _obra(tmp_path, 'A'), _obra(tmp_path, 'B')
    (a / 'ch0005_001.png').write_bytes(b'x')
    (b / 'ch0005_002.png').write_bytes(b'x')
    monkeypatch.setitem(sys.modules['api.roots'].__dict__, 'series_dirs', lambda t: [a, b])

    paginas = _app().get('/api/library/pages/A?chapter=5').get_json()['pages']

    assert len(paginas) == 2, 'faltan las páginas del otro disco'


def test_un_capitulo_que_no_esta_es_404_no_una_lista_vacia(tmp_path, monkeypatch):
    # Una lista vacía con 200 abriría el lector en blanco sin decir nada: «falló» y «no había»
    # otra vez, en su versión más silenciosa.
    d = _obra(tmp_path)
    (d / 'ch0001_001.png').write_bytes(b'x')
    monkeypatch.setitem(sys.modules['api.roots'].__dict__, 'series_dirs', lambda t: [d])

    assert _app().get('/api/library/pages/Obra?chapter=99').status_code == 404


def test_sin_capitulo_no_se_adivina(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules['api.roots'].__dict__, 'series_dirs', lambda t: [_obra(tmp_path)])

    assert _app().get('/api/library/pages/Obra').status_code == 400
