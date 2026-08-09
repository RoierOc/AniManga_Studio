"""`/uploads/` tiene que dar la ESCALADA aunque cambie la extensión (`src/app.py:_find_page`).

Es el fallo silencioso más caro que ha tenido el servidor de páginas: el escalador escribe siempre
`.jpg`, los originales bajados de MangaDex son `.webp`, y el listado de páginas nombra los
ORIGINALES. Buscando la extensión pedida en escalados **y** originales antes de probar otras, el
`.webp` original ganaba siempre y las horas de GPU no llegaban a la pantalla: medido, una página
que en disco mide 4500x6400 se servía como 1125x1600, sin error ninguno.
"""
import pytest

from api import roots as R
# Arriba y no dentro del test: importar `app` tarde vuelve a ejecutar sus decoradores `@route`
# cuando otra prueba ya ha servido una petición, y Flask lo rechaza.
from app import _find_page


@pytest.fixture
def biblioteca(tmp_path, monkeypatch):
    orig, esc = tmp_path / 'M', tmp_path / 'M_up'
    (orig / 'Obra').mkdir(parents=True)
    (esc / 'Obra').mkdir(parents=True)
    monkeypatch.setattr(R, 'MANGA_DIR', orig)
    monkeypatch.setattr(R, 'UPSCALED_DIR', esc)
    monkeypatch.setattr(R, '_ROOTS_FILE', tmp_path / 'manga_roots.json')
    monkeypatch.setattr(R, 'get_library_mode', lambda: 'normal')
    return orig, esc


def test_la_escalada_gana_aunque_la_extension_no_coincida(biblioteca):
    orig, esc = biblioteca
    (orig / 'Obra' / 'ch0032_002.webp').write_bytes(b'original')
    (esc / 'Obra' / 'ch0032_002.jpg').write_bytes(b'escalada')

    p = _find_page('Obra/ch0032_002.webp', prefer_upscaled=True)

    assert p.read_bytes() == b'escalada', 'se sirvió el original: las horas de GPU no llegan'


def test_sin_escalada_se_sirve_el_original(biblioteca):
    # Las páginas a color las salta el escalador a propósito: no tener versión escalada es normal
    # y NO puede convertirse en un 404.
    orig, _ = biblioteca
    (orig / 'Obra' / 'ch0032_001.webp').write_bytes(b'original')

    p = _find_page('Obra/ch0032_001.webp', prefer_upscaled=True)

    assert p.read_bytes() == b'original'


def test_el_modo_comparar_sigue_dando_el_original(biblioteca):
    orig, esc = biblioteca
    (orig / 'Obra' / 'ch0032_002.webp').write_bytes(b'original')
    (esc / 'Obra' / 'ch0032_002.jpg').write_bytes(b'escalada')

    p = _find_page('Obra/ch0032_002.webp', prefer_upscaled=False)

    assert p.read_bytes() == b'original'
