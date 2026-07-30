"""qBittorrent caído ya no se disfraza de «no hay descargas».

`/api/anime/qbt/list` hacía `except Exception: return jsonify([])`: con qBittorrent apagado o la
VPN caída, la lista vacía y un 200 son EXACTAMENTE lo que devuelve una cola vacía de verdad, así
que la vista de Descargas decía «No hay descargas activas» y parecía que se habían perdido los 87
torrents. El front ya sabía distinguirlo (`qbtError` en el store) — sólo faltaba que el backend
se lo dijera. Es la regla del repo «falló ≠ no había».

Correr:  .venv/bin/python -m pytest tests/test_qbt_list_failure.py -q
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from api import anime as an  # noqa: E402


@pytest.fixture
def client():
    from flask import Flask
    app = Flask(__name__)
    app.register_blueprint(an.anime_bp, url_prefix="/api/anime")
    return app.test_client()


def test_qbittorrent_caido_devuelve_502_y_no_una_lista_vacia(client, monkeypatch):
    def boom(*a, **k):
        raise ConnectionError("Connection refused")
    monkeypatch.setattr(an, "_q", boom)

    r = client.get("/api/anime/qbt/list")
    assert r.status_code == 502, "un 200 con [] es indistinguible de una cola vacía"
    assert "refused" in (r.get_json() or {}).get("error", "")


def test_la_cola_vacia_de_verdad_sigue_siendo_200_con_lista_vacia(client, monkeypatch):
    class _Resp:
        @staticmethod
        def json():
            return []
    monkeypatch.setattr(an, "_q", lambda *a, **k: _Resp())

    r = client.get("/api/anime/qbt/list")
    assert r.status_code == 200 and r.get_json() == []
