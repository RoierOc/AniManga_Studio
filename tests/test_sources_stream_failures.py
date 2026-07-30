"""Una fuente que REVIENTA no puede confundirse con una que no tiene el título.

`search_all_stream` recorre ~15 extensiones en paralelo. Hasta ahora, la que fallaba (timeout,
Cloudflare, extensión rota) devolvía `results: []` — exactamente lo mismo que la que respondió
bien y no tenía coincidencias. La búsqueda "terminaba" y la UI mostraba lo que hubiera sin decir
que media docena de fuentes no contestó: un resultado incompleto disfrazado de completo. Es la
regla del repo ("falló" ≠ "no había") en la pantalla de descubrimiento.

Lo que se comprueba es la equivalencia, no el texto: el evento de una fuente caída lleva `failed`
con su nombre y la causa; el de una fuente vacía-pero-viva, no.

Correr:  .venv/bin/python -m pytest tests/test_sources_stream_failures.py -q
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))


@pytest.fixture()
def app_client(monkeypatch):
    import api.sources as S

    # Dos fuentes: una revienta, la otra responde bien pero sin resultados.
    monkeypatch.setattr(S, "_gql", lambda *a, **k: {
        "sources": {"nodes": [
            {"id": "1", "name": "FuenteRota", "lang": "es"},
            {"id": "2", "name": "FuenteViva", "lang": "es"},
        ]}
    })

    class _Resp:
        def __init__(self, payload):
            self._p = payload

        def raise_for_status(self):
            pass

        def json(self):
            return self._p

    def _post(url, json=None, **kw):
        if json["variables"]["source"] == "1":
            raise RuntimeError("Cloudflare challenge")
        return _Resp({"data": {"fetchSourceManga": {"mangas": []}}})

    monkeypatch.setattr(S.http_requests, "post", _post)

    from flask import Flask
    app = Flask(__name__)
    app.register_blueprint(S.sources_bp, url_prefix="/api/sources")
    return app.test_client()


def _events(client):
    body = client.get("/api/sources/search_all_stream?q=prueba").get_data(as_text=True)
    return [json.loads(line[6:]) for line in body.splitlines() if line.startswith("data: ")]


def test_la_fuente_caida_se_declara(app_client):
    failed = [e["failed"] for e in _events(app_client) if e.get("failed")]
    assert len(failed) == 1, "la fuente que reventó debe salir declarada, no como progreso mudo"
    assert failed[0]["source"]["name"] == "FuenteRota"
    assert "Cloudflare" in failed[0]["error"], "la causa se conserva, no se aplana a un booleano"


def test_la_fuente_viva_sin_resultados_NO_es_un_fallo(app_client):
    events = _events(app_client)
    progresos = [e for e in events if e["type"] == "progress"]
    assert len(progresos) == 2, "ambas fuentes avanzan el contador"
    assert sum(1 for e in progresos if "failed" not in e) == 1, \
        "vacío legítimo y fallo NO pueden ser el mismo evento"
    assert events[-1]["type"] == "done"
