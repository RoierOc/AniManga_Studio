"""Fase 3 — la sonda de contratos convierte un body malformado (que hoy se degrada en SILENCIO)
en una violación VISIBLE, sin rechazar todavía.

Se comprueba el contrato que importa, no la implementación de pydantic:
  · un body válido devuelve la instancia tipada y NO registra error;
  · un body que viola el contrato devuelve None y lo REGISTRA por observabilidad (contador
    "contract" + evento SSE), pero NUNCA propaga ni rechaza — el endpoint sigue con su parseo;
  · los campos extra se toleran (un cliente nuevo no dispara falsos positivos);
  · un body que ni siquiera es objeto JSON (lista/None) también se registra, no revienta.

`api.contracts` solo arrastra pydantic + observability (sin cv2/torch), así que corre en el CI
ligero (que ahora instala pydantic). Ver [[project_hardening_phases]].

Correr:  .venv/bin/python -m pytest tests/test_contracts.py -q
"""
import pytest

pytest.importorskip("pydantic")

from api import observability as obs  # noqa: E402
from api.contracts import (  # noqa: E402
    CoverageBody, VersionsBody, DownloadVersionBody, NativeProgressBody, validate_and_log,
)


@pytest.fixture(autouse=True)
def _clean():
    obs.reset_error_counts()
    yield
    obs.reset_error_counts()


# ── camino feliz: instancia tipada, cero ruido ────────────────────────────────
def test_valid_coverage_returns_instance_and_logs_nothing():
    m = validate_and_log(
        CoverageBody,
        {"title": "Naruto", "anilistId": 20, "sourceIds": ["a", "b"], "refresh": True},
        "transplant/coverage",
    )
    assert isinstance(m, CoverageBody)
    assert m.title == "Naruto"
    assert m.refresh is True
    assert obs.error_counts().get("contract") is None


def test_versions_shares_coverage_shape():
    assert VersionsBody is CoverageBody
    m = validate_and_log(VersionsBody, {"title": "Bleach"}, "transplant/versions")
    assert isinstance(m, CoverageBody)


def test_extra_fields_are_tolerated():
    """El contrato vigila lo que USAMOS; un campo de más no es una violación."""
    m = validate_and_log(CoverageBody, {"title": "x", "campoNuevoDelFront": 1}, "transplant/coverage")
    assert isinstance(m, CoverageBody)
    assert obs.error_counts().get("contract") is None


# ── violaciones: None + registrado, nunca propaga ─────────────────────────────
def test_title_as_object_is_flagged_not_coerced():
    base = obs_seq()
    m = validate_and_log(CoverageBody, {"title": {"nested": 1}}, "transplant/coverage")
    assert m is None
    assert obs.error_counts().get("contract") == 1
    assert _new_error_events(base)  # emitió evento SSE 'error'


def test_missing_title_is_flagged():
    m = validate_and_log(CoverageBody, {"anilistId": 20}, "transplant/coverage")
    assert m is None
    assert obs.error_counts().get("contract") == 1


def test_download_version_requires_source_mangaid():
    assert validate_and_log(DownloadVersionBody, {"title": "x", "source": {}},
                            "transplant/download_version") is None
    assert validate_and_log(DownloadVersionBody, {"title": "x", "source": {"mangaId": 7}},
                            "transplant/download_version") is not None
    # una sola violación registrada (la del source vacío)
    assert obs.error_counts().get("contract") == 1


def test_native_progress_flags_non_numeric_position():
    """`position` no numérica: hoy `float(...)` la convertiría en 0 en silencio (marca 'no visto'
    una reproducción que sí avanzó). La sonda la hace visible."""
    m = validate_and_log(NativeProgressBody,
                         {"anime_id": "a", "episode": 3, "position": "doce"},
                         "anime/native/progress")
    assert m is None
    assert obs.error_counts().get("contract") == 1


def test_native_progress_coerces_numeric_strings():
    """El reproductor puede mandar números como string; eso NO es una violación (float() los
    aceptaría igual). Solo lo no-numérico debe saltar."""
    m = validate_and_log(NativeProgressBody,
                         {"anime_id": "a", "episode": "3", "position": "12.5", "duration": 1400},
                         "anime/native/progress")
    assert isinstance(m, NativeProgressBody)
    assert m.position == 12.5
    assert obs.error_counts().get("contract") is None


# ── robustez: la sonda no puede crear un fallo nuevo ──────────────────────────
def test_non_dict_body_is_logged_not_crashing():
    assert validate_and_log(CoverageBody, ["not", "a", "dict"], "transplant/coverage") is None
    assert validate_and_log(CoverageBody, None, "transplant/coverage") is None
    assert obs.error_counts().get("contract") == 2


# ── helpers ───────────────────────────────────────────────────────────────────
def obs_seq():
    from api import runtime
    return runtime.get_current_seq()


def _new_error_events(base):
    from api import runtime
    return [e for e in runtime.get_sse_events_since(base) if e.get("type") == "error"]
