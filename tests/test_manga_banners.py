"""Banners de manga: lo que se cachea y lo que NO.

La costura peligrosa es una sola: un fallo de red no puede cachearse como «esta obra no tiene
banner», porque lo congelaría 7 días. Ver `manga_banners.py`.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from api import manga_banners as mb  # noqa: E402


class _Resp:
    def __init__(self, status, payload=None):
        self.status_code = status
        self._p = payload if payload is not None else {}

    def json(self):
        return self._p


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    """Caché en memoria por test: no toca el disco ni arrastra estado entre casos."""
    store = {}
    monkeypatch.setattr(mb, 'cache_get', lambda ns, k, ttl: store.get(k))
    monkeypatch.setattr(mb, 'cache_set', lambda ns, k, v, **kw: store.__setitem__(k, v))
    return store


def _http_returning(*responses):
    calls = []

    class _H:
        def post(self, url, **kw):
            calls.append(kw['json']['variables']['s'])
            r = responses[min(len(calls) - 1, len(responses) - 1)]
            if isinstance(r, Exception):
                raise r
            return r

    return _H(), calls


def test_devuelve_solo_los_que_tienen_banner(monkeypatch, _isolate):
    h, _ = _http_returning(_Resp(200, {'data': {'Media': {'bannerImage': 'u.jpg'}}}))
    monkeypatch.setattr(mb, '_http', h)
    assert mb.banners_for(['Ao no Hako']) == {'Ao no Hako': 'u.jpg'}


def test_obra_sin_banner_se_cachea_como_fallo(monkeypatch, _isolate):
    h, calls = _http_returning(_Resp(200, {'data': {'Media': {'bannerImage': None}}}))
    monkeypatch.setattr(mb, '_http', h)
    assert mb.banners_for(['X']) == {}
    assert mb.banners_for(['X']) == {}
    assert len(calls) == 1, 'una obra sin banner no debe reconsultarse: se come el rate limit'


def test_404_es_respuesta_no_averia(monkeypatch, _isolate):
    """404 = «no existe en AniList». Se cachea; no se reintenta."""
    h, calls = _http_returning(_Resp(404, {'errors': [{'status': 404}]}))
    monkeypatch.setattr(mb, '_http', h)
    assert mb.banners_for(['Asagiiro no Saudade']) == {}
    mb.banners_for(['Asagiiro no Saudade'])
    assert len(calls) == 1


def test_fallo_de_red_NO_se_cachea(monkeypatch, _isolate):
    """El caso caro: si esto se cachea, la obra pierde su banner durante 7 días."""
    h, calls = _http_returning(ConnectionError('sin red'),
                               _Resp(200, {'data': {'Media': {'bannerImage': 'u.jpg'}}}))
    monkeypatch.setattr(mb, '_http', h)
    assert mb.banners_for(['Ao no Hako']) == {}          # falla ahora…
    assert mb.banners_for(['Ao no Hako']) == {'Ao no Hako': 'u.jpg'}   # …y se recupera después
    assert len(calls) == 2


def test_un_titulo_roto_no_se_lleva_al_resto(monkeypatch, _isolate):
    """Lo que SÍ pasaba agrupando por alias: un 404 anulaba todo el lote (ver docstring)."""
    resp = {
        'Ao no Hako': _Resp(200, {'data': {'Media': {'bannerImage': 'a.jpg'}}}),
        'Asagiiro no Saudade': _Resp(404, {}),
        'Amayo no Tsuki': _Resp(200, {'data': {'Media': {'bannerImage': 'b.jpg'}}}),
    }

    class _H:
        def post(self, url, **kw):
            return resp[kw['json']['variables']['s']]

    monkeypatch.setattr(mb, '_http', _H())
    out = mb.banners_for(['Ao no Hako', 'Asagiiro no Saudade', 'Amayo no Tsuki'])
    assert out == {'Ao no Hako': 'a.jpg', 'Amayo no Tsuki': 'b.jpg'}


def test_acota_el_lote(monkeypatch, _isolate):
    h, calls = _http_returning(_Resp(200, {'data': {'Media': {'bannerImage': None}}}))
    monkeypatch.setattr(mb, '_http', h)
    mb.banners_for([f't{i}' for i in range(80)])
    assert len(calls) == mb._MAX
