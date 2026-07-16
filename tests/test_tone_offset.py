"""A1: igualar el tono del parche trasplantado con el de la página de destino.

El usuario veía "un cuadro más oscuro o más claro" donde se pegaba el texto. El pegado era
`es*α + en*(1-α)`: los píxeles del scan ES entraban con SU curva. MEDIDO sobre el corpus
(672 págs / 7170 cajas): el 30% de las cajas pasa de 4 niveles de diferencia — el umbral en que
el ojo lo ve.

Lo que estos tests fijan, que son las decisiones que la medición justificó:
  · offset, NO ganancia (la pendiente no es medible: el anillo suele ser blanco plano);
  · sólo si |delta| >= 4 (el 70% ya está bien y tocarlo sólo puede empeorarlo);
  · sólo si el anillo CASA (si no, el delta no es de tono sino homografía torcida);
  · se mide en el ANILLO (arte, el mismo en ambos scans), nunca dentro de la caja (texto, que
    es distinto por definición: uno está en inglés y el otro en español).

Correr:  python -m pytest tests/test_tone_offset.py -q
"""
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from transplant_core import TONE_MIN_DELTA, tone_offset  # noqa: E402

BOX = (40, 40, 90, 90)          # caja de 50x50 con anillo de 10 px alrededor


def page(bg=200, box_fill=None):
    """Página gris uniforme; opcionalmente el interior de la caja con otro valor."""
    g = np.full((160, 160), bg, np.uint8)
    if box_fill is not None:
        x0, y0, x1, y1 = BOX
        g[y0:y1, x0:x1] = box_fill
    return g


def test_no_difference_no_correction():
    assert tone_offset(page(200), page(200), *BOX) == 0.0


def test_darker_patch_is_raised():
    """El ES 12 niveles más oscuro -> hay que SUMARLE 12."""
    assert tone_offset(page(200), page(188), *BOX) == pytest.approx(12.0)


def test_lighter_patch_is_lowered():
    """Y al revés: el signo importa. `_ring_mae` (valor absoluto) no puede dar esto."""
    assert tone_offset(page(200), page(212), *BOX) == pytest.approx(-12.0)


@pytest.mark.parametrize('delta', [1, 2, 3])
def test_small_differences_are_left_alone(delta):
    """p50 del corpus = 1 nivel: invisible. Corregir el 70% que ya está bien sólo arriesga
    recortar el blanco a 255 sin ganar nada."""
    assert tone_offset(page(200), page(200 - delta), *BOX) == 0.0


@pytest.mark.parametrize('delta', [4, 5, 20, 60])
def test_visible_differences_are_corrected(delta):
    assert tone_offset(page(200), page(200 - delta), *BOX) == pytest.approx(float(delta))


def test_threshold_is_the_measured_one():
    assert TONE_MIN_DELTA == 4.0


def test_a_huge_but_uniform_shift_is_still_corrected():
    """EL TEST QUE ENCONTRÓ EL BUG. Medir el MAE del anillo SIN quitar el offset descartaba los
    deltas grandes (un shift de 60 da un MAE de 60 > umbral 26) — o sea, mataba el arreglo justo
    en los casos que el usuario VE (corpus: p99=43, máx=242). La guarda va sobre el RESIDUO."""
    assert tone_offset(page(200), page(140), *BOX) == pytest.approx(60.0)


def test_ring_that_does_not_match_is_not_corrected():
    """Si el anillo no casa, la diferencia NO es la curva del scan: es que la homografía está
    torcida ahí y estamos comparando otro dibujo. Ante la duda, no tocar."""
    en = page(200)
    es = page(200)
    rng = np.random.default_rng(0)
    es[:] = rng.integers(0, 255, es.shape, dtype=np.uint8)   # arte totalmente distinto
    assert tone_offset(en, es, *BOX) == 0.0


def test_the_text_inside_the_box_does_not_pollute_the_measure():
    """Dentro de la caja el contenido es DISTINTO por definición (inglés vs español). Si se
    midiera ahí, el offset perseguiría letras. El interior se excluye."""
    en = page(200, box_fill=0)      # texto EN negro que llena la caja
    es = page(200, box_fill=255)    # texto ES blanco: opuesto
    assert tone_offset(en, es, *BOX) == 0.0     # el anillo casa -> no hay nada que corregir


def test_box_in_the_corner_still_has_a_ring():
    """Una caja pegada al borde conserva anillo por dos lados (la 'L'), y con eso basta."""
    assert tone_offset(page(200), page(180), 0, 0, 4, 4) == pytest.approx(20.0)


def test_box_covering_everything_has_no_ring():
    """Sin anillo medible no se inventa un offset."""
    assert tone_offset(page(200), page(180), 0, 0, 160, 160) == 0.0


def test_mismatched_shapes_do_not_crash():
    assert tone_offset(page(200), np.full((10, 10), 180, np.uint8), *BOX) == 0.0
