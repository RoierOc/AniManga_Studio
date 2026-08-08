"""Páginas online de MangaDex: el proxy tiene que HONRAR `?w=`.

Sin esto servía el PNG original de 2039×2894 (5,9 MP ≈ 23 MB descomprimidos) para pintarlo a
~1400 px: con el capítulo entero en el lector, el WebView descartaba decodificaciones y las
páginas salían EN BLANCO. Es un fallo silencioso perfecto — el servidor devuelve 200 y la
imagen correcta, sólo que 10× más grande de lo que nadie pidió.
"""
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

import pytest                       # noqa: E402
from PIL import Image               # noqa: E402
import api.mangadex as M            # noqa: E402

URL = 'https://nodo1.mangadex.network/data/abc/x1-deadbeef.png'


class _Resp:
    def __init__(self, content):
        self.content, self.status_code, self.headers = content, 200, {'Content-Type': 'image/png'}

    def iter_content(self, n):
        yield self.content

    def close(self):
        pass


@pytest.fixture
def cliente(monkeypatch, tmp_path):
    monkeypatch.setattr(M, '_PG_DIR', tmp_path / 'mdpages')
    from flask import Flask
    app = Flask(__name__)
    app.register_blueprint(M.auth_bp, url_prefix='/api/mangadex')
    return app.test_client()


def _pagina(w=2039, h=2894):
    buf = io.BytesIO()
    Image.new('RGB', (w, h), 'white').save(buf, 'PNG')
    return buf.getvalue()


def test_reescala_al_peldano_pedido_y_lo_cachea(cliente, monkeypatch):
    fetches = []

    def fake(url, stream):
        fetches.append(stream)
        return _Resp(_pagina()), 200

    monkeypatch.setattr(M, '_md_page_fetch', fake)
    r = cliente.get(f'/api/mangadex/page_proxy?u={URL}&w=1300')
    assert r.status_code == 200
    im = Image.open(io.BytesIO(r.data))
    assert im.width == 1400 and im.format == 'JPEG'      # sube al peldaño, no sirve 2039
    assert im.height == 1987                             # y conserva la proporción de la página

    cliente.get(f'/api/mangadex/page_proxy?u={URL}&w=1300')
    assert len(fetches) == 1                              # la segunda sale del disco


def test_sin_w_o_mas_pequena_que_lo_pedido_sirve_el_original(cliente, monkeypatch):
    original = _pagina(800, 1200)
    monkeypatch.setattr(M, '_md_page_fetch', lambda url, stream: (_Resp(original), 200))

    # `w=0` (zoom / ajuste original) → tal cual, sin recodificar.
    r = cliente.get(f'/api/mangadex/page_proxy?u={URL}')
    assert r.data == original

    # Pedir MÁS ancho del que tiene no puede inventar píxeles: original, no un JPEG ampliado.
    r = cliente.get(f'/api/mangadex/page_proxy?u={URL}&w=1800')
    assert Image.open(io.BytesIO(r.data)).format == 'PNG'


def test_host_ajeno_rechazado(cliente):
    assert cliente.get('/api/mangadex/page_proxy?u=https://evil.example/x.png&w=640').status_code == 400
