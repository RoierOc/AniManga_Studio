"""Fase 0 — la regla de oro del proyecto: «falló ≠ vacío».

Una fuente que revienta NO puede producir el mismo resultado que una fuente que legítimamente
no tiene la obra: debe quedar VISIBLE (record_error → contador/SSE) y NO tumbar la cascada.

  · Un fallo de red en un adapter → devuelve []/None Y registra el error (no silencioso).
  · La ficha se arma igual desde MangaBaka aunque el enriquecimiento de MangaUpdates no esté.

Puro Python (sin red real: se monkeypatchea el cliente HTTP). Corre en el CI ligero.
Correr:  .venv/bin/python -m pytest tests/test_metasource_failsafe.py -q
"""
import requests

from api import observability as obs
from api.metasource.sources import base, mangabaka, mangaupdates


def _reset_counts():
    obs._error_counts.clear()


def test_network_failure_is_recorded_not_silent(monkeypatch):
    _reset_counts()

    def boom(*a, **k):
        raise requests.RequestException("network down")

    monkeypatch.setattr(base.http, "get", boom)
    result = mangabaka.search("solo leveling", 5)
    assert result == []                                   # vacío, sí…
    assert obs.error_counts().get("discovery", 0) >= 1    # …pero VISIBLE (no silencioso)


def test_http_error_status_is_recorded(monkeypatch):
    _reset_counts()

    class _Resp:
        ok = False
        status_code = 503

        def json(self):
            return {}

    monkeypatch.setattr(base.http, "get", lambda *a, **k: _Resp())
    assert mangabaka.fetch("3397") is None
    assert obs.error_counts().get("discovery", 0) >= 1


def test_legitimate_empty_is_NOT_recorded(monkeypatch):
    _reset_counts()

    class _Resp:
        ok = True
        status_code = 200

        def json(self):
            return {"status": 200, "data": []}            # sin resultados, pero OK

    monkeypatch.setattr(base.http, "get", lambda *a, **k: _Resp())
    assert mangabaka.search("obra inexistente zzz", 5) == []
    assert obs.error_counts().get("discovery", 0) == 0    # "no había" NO se loguea


def test_work_survives_mangaupdates_being_down(monkeypatch):
    # MangaBaka responde; MangaUpdates cae (fetch/enrich → None). La ficha sale completa.
    import api.metasource as ms

    monkeypatch.setattr(ms, "cache_get", lambda *a, **k: None)
    monkeypatch.setattr(ms, "cache_set", lambda *a, **k: None)
    monkeypatch.setattr(mangabaka, "fetch", lambda ext: {
        "id": "mb:3397", "ids": {"mangaupdates": "6z1uqw7"}, "title": "Solo Leveling",
        "type": "manhwa", "cover": "c", "rating": 86,
    })
    monkeypatch.setattr(mangaupdates, "fetch", lambda ext: None)
    monkeypatch.setattr(mangaupdates, "enrich_for", lambda title, variants: None)

    w = ms.get_work("mb:3397")
    assert w is not None
    assert w["title"] == "Solo Leveling"
    assert w["type"] == "manhwa"
    assert w["readable"] is True
