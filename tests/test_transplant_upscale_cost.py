"""El aviso de "vas a perder N páginas 4K" tiene que contar EXACTAMENTE lo que el borrado se lleva.

Si `upscaled_cost` y `_invalidate_upscaled` se desincronizan, el aviso miente — y un aviso que
miente sobre horas de GPU es peor que no tener aviso. El test que manda es
`test_cost_equals_what_invalidate_deletes`: no comprueba un número, comprueba la EQUIVALENCIA
entre las dos funciones, así que sigue valiendo aunque cambie la convención de nombres.

Correr:  python -m pytest tests/test_transplant_upscale_cost.py -q
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))


@pytest.fixture()
def up(tmp_path, monkeypatch):
    """Mirror upscaled sintético. NUNCA la biblioteca real."""
    import api.transplant as T
    monkeypatch.setattr(T, 'upscaled_dir', lambda: tmp_path)
    return T, tmp_path


def _mk(root, title, spec):
    d = root / title
    d.mkdir(parents=True, exist_ok=True)
    for prefix, n in spec.items():
        for i in range(n):
            (d / f'{prefix}_{i:03d}.jpg').write_bytes(b'x')
    return d


def test_counts_pages_and_chapters(up):
    T, root = up
    _mk(root, 'Amayo no Tsuki', {'ch0036': 3, 'ch0037': 2})
    c = T.upscaled_cost('Amayo no Tsuki')
    assert c['pages'] == 5
    assert c['chapters'] == 2
    assert c['byChapter'] == {'ch0036': 3, 'ch0037': 2}


def test_empty_when_nothing_upscaled(up):
    T, _ = up
    c = T.upscaled_cost('No Existe')
    assert c == {'pages': 0, 'chapters': 0, 'byChapter': {}}


def test_filters_by_requested_chapters(up):
    T, root = up
    _mk(root, 'Amayo no Tsuki', {'ch0036': 3, 'ch0037': 2, 'ch0049': 4})
    c = T.upscaled_cost('Amayo no Tsuki', ['36'])
    assert c['pages'] == 3 and c['byChapter'] == {'ch0036': 3}


def test_none_means_every_chapter(up):
    T, root = up
    _mk(root, 'Amayo no Tsuki', {'ch0036': 3, 'ch0037': 2})
    assert T.upscaled_cost('Amayo no Tsuki', None)['pages'] == 5


def test_subchapter_prefix_not_confused_with_its_parent(up):
    """ch0036 y ch0036.5 son capítulos DISTINTOS y no deben sumarse el uno al otro."""
    T, root = up
    _mk(root, 'X', {'ch0036': 2, 'ch0036.5': 3})
    c = T.upscaled_cost('X')
    assert c['byChapter'] == {'ch0036': 2, 'ch0036.5': 3}
    assert T.upscaled_cost('X', ['36'])['pages'] == 2
    assert T.upscaled_cost('X', ['36.5'])['pages'] == 3


def test_underscore_and_space_folder_conventions(up):
    """El mirror usa dos convenciones ('A_B' y 'A B'); invalidate borra en ambas -> contar ambas."""
    T, root = up
    _mk(root, 'Amayo_no_Tsuki', {'ch0001': 2})
    _mk(root, 'Amayo no Tsuki', {'ch0002': 3})
    assert T.upscaled_cost('Amayo_no_Tsuki')['pages'] == 5


@pytest.mark.parametrize('chapters', [None, ['36'], ['36', '37'], ['36.5']])
def test_cost_equals_what_invalidate_deletes(up, chapters):
    """EL INVARIANTE: lo anunciado == lo destruido."""
    T, root = up
    spec = {'ch0036': 3, 'ch0036.5': 2, 'ch0037': 4, 'ch0099': 1}
    d = _mk(root, 'T', spec)

    announced = T.upscaled_cost('T', chapters)['pages']

    before = len(list(d.glob('*.jpg')))
    targets = chapters if chapters is not None else [p[2:] for p in spec]
    for ch in targets:
        T._invalidate_upscaled('T', T._chapter_file_prefix(ch))
    deleted = before - len(list(d.glob('*.jpg')))

    assert announced == deleted


def test_other_titles_are_never_counted(up):
    T, root = up
    _mk(root, 'A', {'ch0001': 2})
    _mk(root, 'B', {'ch0001': 9})
    assert T.upscaled_cost('A')['pages'] == 2
