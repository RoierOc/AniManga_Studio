"""Cada fuente de subtítulos declara CÓMO le fue, no sólo qué encontró.

Por qué existe: MEDIDO el 29-jul-2026 en `/tmp/flask.log`, Subdivx devolvió **HTTP 403 en 22 de 22
búsquedas** (está detrás de Cloudflare) y la UI decía «no se encontraron subtítulos» — exactamente
igual que si la fuente hubiera respondido bien y no tuviera nada. Con dos de tres fuentes rotas, el
usuario no tenía forma de saberlo ni de decidir reintentar.

Regla del repo: «falló» y «no había» nunca pueden ser el mismo valor de retorno.

Correr:  .venv/bin/python -m pytest tests/test_subtitle_source_report.py -q
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import api.subtitle as S  # noqa: E402


@pytest.fixture()
def sin_red(monkeypatch):
    """Ninguna fuente sale a internet; cada test decide qué hace la suya."""
    monkeypatch.setattr(S, '_ext_search_opensubtitles', lambda *a, **k: [])
    monkeypatch.setattr(S, '_ext_search_subdl', lambda *a, **k: [])
    monkeypatch.setattr(S, '_ext_search_subdivx', lambda *a, **k: [])


def test_una_fuente_que_revienta_se_DISTINGUE_de_una_vacia():
    vacia, rota = [], []
    S._note_source(vacia, 'subdl', 0)
    S._note_source(rota, 'subdivx', 0, error='HTTP Error 403: Forbidden')
    assert vacia[0]['status'] == 'ok', 'responder sin resultados NO es un fallo'
    assert rota[0]['status'] == 'error' and '403' in rota[0]['error']


def test_sin_api_key_no_es_ni_fallo_ni_vacio():
    r = []
    S._note_source(r, 'subdl', 0, unconfigured=True)
    assert r[0]['status'] == 'unconfigured', 'no configurarla es del usuario, no un fallo de la fuente'


def test_si_encontro_algo_pese_a_un_error_parcial_cuenta_como_ok():
    """Dos títulos, uno falla y el otro trae resultados: la fuente sirvió."""
    r = []
    S._note_source(r, 'opensubtitles', 2, error='timeout en el 2º título')
    assert r[0]['status'] == 'ok' and r[0]['found'] == 2


def test_subdivx_403_llega_al_parte(monkeypatch):
    """De punta a punta: la excepción real de la fuente acaba en el parte, no en un vacío mudo."""
    def revienta(req, timeout=None):
        raise OSError('HTTP Error 403: Forbidden')
    monkeypatch.setattr(S._ur, 'urlopen', revienta)
    report = []
    out = S._ext_search_subdivx(['Terror in Resonance'], 2, report=report)
    assert out == []
    assert report == [{'source': 'subdivx', 'found': 0, 'status': 'error',
                       'error': 'HTTP Error 403: Forbidden'}]


def test_el_parte_recoge_las_TRES_fuentes(monkeypatch, sin_red):
    def ost(titles, ep, languages='', season=1, report=None):
        S._note_source(report, 'opensubtitles', 2)
        return [{'source': 'opensubtitles'}, {'source': 'opensubtitles'}]

    def subdl(titles, ep, season=1, report=None):
        S._note_source(report, 'subdl', 0, unconfigured=True)
        return []

    def subdivx(titles, ep, report=None):
        S._note_source(report, 'subdivx', 0, error='403')
        return []

    monkeypatch.setattr(S, '_ext_search_opensubtitles', ost)
    monkeypatch.setattr(S, '_ext_search_subdl', subdl)
    monkeypatch.setattr(S, '_ext_search_subdivx', subdivx)

    report = []
    found = S._ext_find_spanish_subs(['Show'], 2, report=report)
    assert len(found) == 2
    assert {r['source']: r['status'] for r in report} == {
        'opensubtitles': 'ok', 'subdl': 'unconfigured', 'subdivx': 'error'}


def test_sin_report_las_fuentes_siguen_funcionando(monkeypatch):
    """El parte es opcional: quien no lo pida no cambia de comportamiento."""
    monkeypatch.setattr(S._ur, 'urlopen', lambda *a, **k: (_ for _ in ()).throw(OSError('403')))
    assert S._ext_search_subdivx(['X'], 1) == []


# ── La pista fuente: carteles ≠ diálogo ───────────────────────────────────────

def test_no_se_elige_la_pista_de_CARTELES_como_fuente():
    """MEDIDO en Terror in Resonance [Judas]: dos pistas inglesas, `[Signs/Lyrics]` (69 líneas, sólo
    carteles y karaoke) y `[Full]` (339, el diálogo). El lote elegía la primera POR IR PRIMERA y
    producía un subtítulo español sin una sola línea de diálogo — mudo hasta reproducirlo."""
    from api.subtitle_batch import _pick_source_track
    tracks = [
        {'sub_index': 0, 'language': 'eng', 'title': 'English [Signs/Lyrics]', 'codec': 'ass'},
        {'sub_index': 1, 'language': 'eng', 'title': 'English [Full]', 'codec': 'ass'},
    ]
    assert _pick_source_track(tracks)['sub_index'] == 1


def test_la_disposicion_forced_tambien_delata_los_carteles():
    from api.subtitle_batch import _pick_source_track
    tracks = [
        {'sub_index': 0, 'language': 'eng', 'title': '', 'codec': 'ass', 'forced': True},
        {'sub_index': 1, 'language': 'eng', 'title': '', 'codec': 'ass', 'forced': False},
    ]
    assert _pick_source_track(tracks)['sub_index'] == 1


def test_si_TODAS_son_de_carteles_se_traduce_igual():
    """Quedarse sin fuente sería peor: traducir carteles es mejor que no traducir nada."""
    from api.subtitle_batch import _pick_source_track
    tracks = [{'sub_index': 0, 'language': 'eng', 'title': 'Signs', 'codec': 'ass'}]
    assert _pick_source_track(tracks)['sub_index'] == 0


def test_sigue_prefiriendo_ingles_sobre_otros_idiomas():
    from api.subtitle_batch import _pick_source_track
    tracks = [
        {'sub_index': 0, 'language': 'ara', 'title': '', 'codec': 'ass'},
        {'sub_index': 1, 'language': 'eng', 'title': '', 'codec': 'ass'},
    ]
    assert _pick_source_track(tracks)['sub_index'] == 1


# ── El aviso de "no se tradujo" tiene que significar algo ─────────────────────

def test_las_lineas_cortas_no_cuentan_como_sin_traducir():
    """«Hmm.», «Nueve», un nombre propio: idénticas por legítimas, no por fallo."""
    orig = ['Hmm.', 'Nine', 'Lisa', 'Tokyo']
    assert S.untranslated_dialogue(orig, list(orig)) == 0


def test_el_dialogo_largo_devuelto_tal_cual_SI_cuenta():
    orig = ['That detective should have figured it out long ago.',
            'What do you wanna do about it?']
    assert S.untranslated_dialogue(orig, list(orig)) == 2


def test_lo_realmente_traducido_no_cuenta():
    orig = ['That detective should have figured it out long ago.']
    trad = ['Ese detective debería haberlo descubierto hace mucho.']
    assert S.untranslated_dialogue(orig, trad) == 0


def test_un_episodio_bien_traducido_NO_dispara_el_aviso():
    """Reproduce la medición real: 948 líneas, 12 % idénticas pero casi todas cortas/karaoke.
    Con el umbral viejo (15 % de TODO) el aviso saltaba en todos los episodios."""
    orig = ['Hmm.'] * 100 + [f'This is dialogue line number {i} of the episode.' for i in range(100)]
    trad = ['Hmm.'] * 100 + [f'Esta es la línea de diálogo número {i} del episodio.' for i in range(100)]
    assert S.untranslated_dialogue(orig, trad) == 0
