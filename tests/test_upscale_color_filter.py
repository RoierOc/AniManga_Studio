"""El filtro por capítulos del selector de páginas a color.

Va ANTES de detectar color porque detectar es lo caro (abre cada página). MEDIDO en la biblioteca
real (Amayo no Tsuki): el manga entero tarda 53,7 s; acotado a 3 capítulos marcados, 2,8 s.

El riesgo que cubren estos tests es de emparejado: si un lado normaliza el número de capítulo y el
otro no, el filtro devuelve [] y el selector sale VACÍO — que es indistinguible de "este capítulo
no tiene páginas a color". Otra vez "falló == no había".

Correr:  python -m pytest tests/test_upscale_color_filter.py -q
"""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from api.upscale import _chapter_norm_of, filter_by_chapters  # noqa: E402

PAGES = [Path(n) for n in (
    'ch0001_001.jpg', 'ch0001_002.jpg',
    'ch0002_001.jpg',
    'ch0012.5_003.jpg',
    'ch0045_010.png',
)]


def _names(paths):
    return [p.name for p in paths]


def test_empty_want_means_everything():
    assert _names(filter_by_chapters(PAGES, set())) == _names(PAGES)


def test_filters_to_the_marked_chapters():
    assert _names(filter_by_chapters(PAGES, {'1'})) == ['ch0001_001.jpg', 'ch0001_002.jpg']
    assert _names(filter_by_chapters(PAGES, {'1', '2'})) == [
        'ch0001_001.jpg', 'ch0001_002.jpg', 'ch0002_001.jpg']


def test_subchapters_are_their_own_chapter():
    """12.5 no puede colarse al pedir el 12, ni al revés."""
    assert _names(filter_by_chapters(PAGES, {'12.5'})) == ['ch0012.5_003.jpg']
    assert filter_by_chapters(PAGES, {'12'}) == []


def test_zero_padding_matches_the_filename():
    """El fichero es ch0045 y la UI manda '45': si no normalizaran igual, el selector saldría
    vacío y parecería que no hay páginas a color."""
    assert _names(filter_by_chapters(PAGES, {_chapter_norm_of('ch0045_010.png')})) == ['ch0045_010.png']
    assert _names(filter_by_chapters(PAGES, {'45'})) == ['ch0045_010.png']


@pytest.mark.parametrize('fname, chapter', [
    ('ch0001_001.jpg', '1'),
    ('ch0045_010.png', '45'),
    ('ch0012.5_003.jpg', '12.5'),
    ('portada.jpg', ''),          # sin patrón de capítulo
])
def test_chapter_norm_of(fname, chapter):
    assert _chapter_norm_of(fname) == chapter


def test_unknown_chapter_is_not_silently_everything():
    """Pedir un capítulo que no existe devuelve [], NUNCA la lista entera: un filtro que ante la
    duda no filtra te haría escalar el manga completo creyendo que escalas uno."""
    assert filter_by_chapters(PAGES, {'999'}) == []
