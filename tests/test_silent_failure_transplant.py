"""transplant.py: "fuente caída ≠ sin capítulos" ahora es CONTABLE, no solo texto en el log.

El caso E-12 (el más caro del proyecto): si listar las fuentes falla, el barrido es imposible —
pero antes se leía como "este manga no está en ninguna fuente". `_discover_candidates` ya lo
marcaba (`listing_error`) y relanzaba; este test fija que además queda en el CONTADOR de
observabilidad (`/api/status/errors`) + SSE, para poder verlo sin leer el log entero.

Importar el blueprint arrastra cv2/numpy → se SALTA en el CI ligero, corre en el suite local.

Correr:  .venv/bin/python -m pytest tests/test_silent_failure_transplant.py -q
"""
import pytest

pytest.importorskip("cv2")
pytest.importorskip("torch")

from api import observability as obs  # noqa: E402


@pytest.fixture(autouse=True)
def _clean():
    obs.reset_error_counts()
    yield
    obs.reset_error_counts()


def test_listing_failure_is_counted_and_reraised(monkeypatch):
    import api.transplant as T

    def _boom():
        raise ConnectionError("Suwayomi offline")

    monkeypatch.setattr(T, "_all_sources", _boom)

    # Debe RELANZAR (el llamante no puede leerlo como "0 fuentes")...
    with pytest.raises(ConnectionError):
        T._discover_candidates(["Naruto"], stats={})
    # ...y quedar registrado como fallo de la costura 'transplant'.
    assert obs.error_counts().get("transplant") == 1
