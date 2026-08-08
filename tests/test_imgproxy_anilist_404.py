"""Subir la resolución de una portada de AniList supone que el tamaño grande EXISTE.

En las fichas viejas no existe: AniList da como `extraLarge` una ruta `/cover/medium/`, `hd_url`
la sube a `/cover/large/` y ese fichero **da 404**, así que la portada desaparecía de toda la app
(visto en «Kaguya-hime: Taketori Monogatari», id 14471). El fallo es silencioso: no hay excepción,
sólo un hueco donde iba la imagen.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

import api.imgproxy as I   # noqa: E402

GRANDE = 'https://s4.anilist.co/file/anilistcdn/media/anime/cover/large/14471.jpg'
MEDIA  = 'https://s4.anilist.co/file/anilistcdn/media/anime/cover/medium/14471.jpg'


class _Resp:
    def __init__(self, code, body=b''):
        self.status_code, self._b = code, body
        self.headers = {'Content-Type': 'image/jpeg'}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f'HTTP {self.status_code}')

    def iter_content(self, n):
        yield self._b


def test_cae_al_tamano_que_anilist_dio_de_verdad(tmp_path, monkeypatch):
    monkeypatch.setattr(I, '_CACHE_DIR', tmp_path)
    I._INDEX.clear() if hasattr(I, '_INDEX') else None
    pedidas = []

    def fake_get(url, **kw):
        pedidas.append(url)
        return _Resp(404) if '/cover/large/' in url else _Resp(200, b'JPEGDATA')

    monkeypatch.setattr(I.requests, 'get', fake_get)
    dest = I._fetch_and_cache(GRANDE)

    assert dest is not None and dest.read_bytes() == b'JPEGDATA'
    assert pedidas == [GRANDE, MEDIA]      # se intenta la buena primero, y sólo entonces la otra


def test_un_404_que_no_es_de_anilist_no_se_reintenta(tmp_path, monkeypatch):
    monkeypatch.setattr(I, '_CACHE_DIR', tmp_path)
    pedidas = []

    def fake_get(url, **kw):
        pedidas.append(url)
        return _Resp(404)

    monkeypatch.setattr(I.requests, 'get', fake_get)
    assert I._fetch_and_cache('https://image.tmdb.org/t/p/w780/x.jpg') is None
    assert len(pedidas) == 1               # nada que deshacer: una sola petición
