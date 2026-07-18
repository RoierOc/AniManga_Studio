"""Detección robusta de subtítulos en español (y latino) por CÓDIGO y TÍTULO.

El bug real: la lógica antigua sólo miraba el código ('spa'/'es'), así que 'LAT', 'SPA-LAT',
'Spanish (LAT)', 'Español Latino'… (que muchas veces van SÓLO en el título) no se detectaban ni
se auto-seleccionaban. Aquí se blindan esos casos + que latino gane a genérico y castellano, y
que no haya falsos positivos ('translate', 'related', inglés/portugués).

Correr:  .venv/bin/python -m pytest tests/test_sub_lang.py -q
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from api import sub_lang  # noqa: E402


# ── Latino detectado por CÓDIGO o por TÍTULO (los casos que reportó el usuario) ────────────
@pytest.mark.parametrize('lang,title', [
    ('es-419', ''),
    ('spa', 'LAT'),
    ('', 'SPA-LAT'),
    ('und', 'Spanish (LAT)'),
    ('', 'Español Latino'),
    ('spa', 'Latino'),
    ('es-LA', ''),
    ('', 'Latinoamérica'),
    ('', 'Spanish [Latin America]'),
    ('es-MX', ''),
    ('', 'Subtítulos en español (México)'),
    ('lat', ''),
])
def test_latino_detected(lang, title):
    assert sub_lang.classify_es(lang, title) == 'lat', f'{lang!r}/{title!r} debería ser latino'
    assert sub_lang.is_es(lang, title)


# ── Castellano / España ───────────────────────────────────────────────────────────────────
@pytest.mark.parametrize('lang,title', [
    ('es-ES', ''), ('', 'Castellano'), ('cas', ''), ('', 'Español (España)'),
])
def test_castellano_detected(lang, title):
    assert sub_lang.classify_es(lang, title) == 'cas'
    assert sub_lang.is_es(lang, title)


# ── Español genérico ──────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize('lang,title', [
    ('es', ''), ('spa', ''), ('', 'Spanish'), ('', 'Español'), ('esp', 'Full Subs'),
])
def test_generic_es_detected(lang, title):
    assert sub_lang.classify_es(lang, title) == 'es'


# ── NO español: sin falsos positivos ──────────────────────────────────────────────────────
@pytest.mark.parametrize('lang,title', [
    ('eng', 'English'),
    ('en', 'Signs & Songs'),
    ('por', 'Português'),
    ('ja', ''),
    ('', 'Translated by group'),   # 'translate' contiene 'lat' — no debe casar
    ('', 'Related notes'),
    ('', ''),
    ('fre', 'Français'),
])
def test_non_spanish_rejected(lang, title):
    assert sub_lang.classify_es(lang, title) is None
    assert not sub_lang.is_es(lang, title)


# ── Prioridad: latino < genérico < castellano; y best_es_index estable ────────────────────
def test_ranking_prefers_latino():
    assert sub_lang.es_rank('es-419') < sub_lang.es_rank('es') < sub_lang.es_rank('es-ES')
    assert sub_lang.es_rank('eng') == 99


def test_best_index_picks_latino_over_generic():
    tracks = [
        {'lang': 'eng', 'title': 'English'},
        {'lang': 'spa', 'title': ''},               # genérico
        {'lang': 'und', 'title': 'Spanish (LAT)'},  # latino, sólo por título
        {'lang': 'es-ES', 'title': ''},             # castellano
    ]
    assert sub_lang.best_es_index(tracks) == 2      # el latino, aunque no sea el primero


def test_best_index_none_when_no_spanish():
    tracks = [{'lang': 'eng', 'title': 'English'}, {'lang': 'ja', 'title': ''}]
    assert sub_lang.best_es_index(tracks) == -1


def test_best_index_accepts_ffprobe_and_mkvmerge_shapes():
    # ffprobe usa {lang}; mkvmerge normalizado usa {language}/{track_name}.
    assert sub_lang.best_es_index([{'language': 'es-419'}]) == 0
    assert sub_lang.best_es_index([{'lang': 'und', 'track_name': 'Latino'}]) == 0
