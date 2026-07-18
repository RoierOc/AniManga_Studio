"""Fase 1 — la observabilidad convierte el fallo SILENCIOSO en fallo visible.

Se comprueba el contrato que importa, no la implementación:
  · record_error incrementa el contador del componente y emite un evento SSE 'error';
  · record_error NUNCA propaga (una costura de error no puede crear otro fallo);
  · thread_guard deja pasar el camino feliz TAL CUAL y, si el target revienta, emite el
    evento en vez de matar el hilo en silencio (que es el bug que atacamos).

Correr:  .venv/bin/python -m pytest tests/test_observability.py -q
"""
import pytest

from api import observability as obs
from api import runtime


@pytest.fixture(autouse=True)
def _clean():
    obs.reset_error_counts()
    yield
    obs.reset_error_counts()


def _drain_error_events():
    return [e for e in runtime.get_sse_events_since(0) if e.get("type") == "error"]


def test_record_error_counts_and_emits_sse():
    base = runtime.get_current_seq()
    obs.record_error("download", ValueError("boom"), task_id="t1")

    assert obs.error_counts().get("download") == 1
    new = [e for e in runtime.get_sse_events_since(base) if e.get("type") == "error"]
    assert len(new) == 1
    ev = new[0]
    assert ev["component"] == "download"
    assert "boom" in ev["message"]
    assert ev["task_id"] == "t1"


def test_record_error_accepts_plain_string():
    obs.record_error("sources", "catálogo no consultable", host="suwayomi")
    assert obs.error_counts().get("sources") == 1


def test_record_error_never_raises(monkeypatch):
    # Aunque el bus SSE esté roto, registrar un error no debe propagar.
    def _boom(*a, **k):
        raise RuntimeError("bus caído")
    monkeypatch.setattr(runtime, "push_sse_event", _boom)
    # No debe lanzar:
    obs.record_error("upscale", RuntimeError("x"))
    assert obs.error_counts().get("upscale") == 1


def test_thread_guard_passes_happy_path():
    @obs.thread_guard("download")
    def work(a, b):
        return a + b
    assert work(2, 3) == 5
    assert obs.error_counts().get("download") is None  # sin errores registrados


def test_swallow_happy_path_logs_nothing():
    with obs.swallow("anime", "history_write"):
        x = 1 + 1
    assert x == 2
    assert obs.error_counts().get("anime") is None   # sin error → sin ruido


def test_swallow_surfaces_error_but_does_not_propagate():
    base = runtime.get_current_seq()
    # No debe propagar (mismo comportamiento que `except: pass`), pero SÍ registrar:
    with obs.swallow("anime", "history_write", path="/x/watch_history.json"):
        raise OSError("disco lleno")
    assert obs.error_counts().get("anime") == 1
    new = [e for e in runtime.get_sse_events_since(base) if e.get("type") == "error"]
    assert len(new) == 1
    assert "disco lleno" in new[0]["message"]
    assert new[0]["op"] == "history_write"


def test_thread_guard_surfaces_crash_instead_of_silence():
    base = runtime.get_current_seq()

    @obs.thread_guard("download")
    def work():
        raise KeyError("falta clave")

    # No propaga (el hilo habría muerto igual; ahora se ve):
    assert work() is None
    assert obs.error_counts().get("download") == 1
    new = [e for e in runtime.get_sse_events_since(base) if e.get("type") == "error"]
    assert len(new) == 1
    assert "falta clave" in new[0]["message"]
