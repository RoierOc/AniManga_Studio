"""Fase 3 — CABLEADO: el contrato está enchufado en el endpoint real y respeta el modo
"validar-y-loguear, NO rechazar".

Prueba de frontera con `app.test_client()` sobre `/api/transplant/coverage` (elegido porque NO
depende de Suwayomi, a diferencia de /versions y /download_version):
  · un body que VIOLA el contrato pero trae `title` NO se rechaza — sigue devolviendo 200 +
    task_id (la sonda es una sonda, no una barrera todavía);
  · esa misma petición INCREMENTA el contador "contract" → la violación quedó registrada.

Importar el blueprint arrastra cv2/numpy (transplant.py), que el CI ligero no tiene → este archivo
se SALTA en CI y corre en el suite local. `_run_coverage` se anula (monkeypatch) para no lanzar el
hilo de red. Ver [[project_hardening_phases]].

Correr:  .venv/bin/python -m pytest tests/test_contracts_wiring.py -q
"""
import pytest

pytest.importorskip("pydantic")
pytest.importorskip("cv2")

from flask import Flask  # noqa: E402

from api import observability as obs  # noqa: E402


@pytest.fixture()
def client(monkeypatch):
    import api.transplant as T
    # No lanzar el hilo real (evita red/Suwayomi); solo nos importa el camino del request.
    monkeypatch.setattr(T.threading, "Thread", lambda *a, **k: type("_T", (), {"start": lambda self: None})())
    monkeypatch.setattr(T, "_set_status", lambda *a, **k: None)
    obs.reset_error_counts()
    app = Flask(__name__)
    app.register_blueprint(T.transplant_bp, url_prefix="/api/transplant")
    yield app.test_client()
    obs.reset_error_counts()


def test_bad_body_is_logged_but_not_rejected(client):
    # sourceIds debería ser lista; lo mandamos como string → viola el contrato.
    r = client.post("/api/transplant/coverage", json={"title": "Naruto", "sourceIds": "no-es-lista"})
    assert r.status_code == 200            # NO rechaza: sigue sirviendo
    assert r.get_json().get("task_id")     # arrancó la tarea como siempre
    assert obs.error_counts().get("contract") == 1   # pero la violación quedó registrada


def test_valid_body_logs_nothing(client):
    r = client.post("/api/transplant/coverage", json={"title": "Naruto", "sourceIds": ["a"]})
    assert r.status_code == 200
    assert obs.error_counts().get("contract") is None


def test_empty_title_still_400_and_not_a_contract_false_positive(client):
    """El title vacío lo sigue rechazando el propio endpoint (400), como antes; el contrato no
    duplica esa regla (title:'' es un str válido de tipo)."""
    r = client.post("/api/transplant/coverage", json={"title": ""})
    assert r.status_code == 400
