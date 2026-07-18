"""Fase 3 — contratos Pydantic en la FRONTERA de los endpoints frágiles.

Modo **"validar-y-loguear, NO rechazar"**: cada endpoint sigue parseando su body como hoy
(`data.get(...)`); ADEMÁS validamos el body crudo contra un modelo y, si no casa, lo registramos
por observabilidad (`record_error`, componente `"contract"`). NO devolvemos 4xx todavía: el
objetivo de esta fase es DESCUBRIR qué mandan los clientes reales que viola el contrato que
creíamos tener, sin arriesgar romper un flujo en producción. Cuando el log esté limpio, un paso
futuro puede pasar a rechazar.

Por qué importa: los handlers hacen `data.get('x')` y siguen. Un cliente que manda `title` como
dict, `position` como `"12,5"` o `episode` ausente NO revienta — se degrada en SILENCIO (guarda
basura o toma una rama equivocada). Es justo la clase de fallo caro del proyecto ("falló ≠ vacío").
Ver [[project_hardening_phases]] y api/observability.py.
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, ValidationError

from api.observability import record_error


class _Body(BaseModel):
    # Tolerar campos extra: el contrato vigila lo que USAMOS, no prohíbe lo que sobra. Así un
    # cliente nuevo que añada un campo no dispara ruido de falso positivo.
    model_config = ConfigDict(extra="ignore")


class CoverageBody(_Body):
    """POST /api/transplant/coverage y /versions (misma forma)."""
    title: str
    anilistId: int | str | None = None
    sourceIds: list[str | int] | None = None
    currentLang: str = ""
    refresh: bool = False


# /versions comparte EXACTAMENTE la forma de /coverage (mismo body en el frontend).
VersionsBody = CoverageBody


class _Source(_Body):
    mangaId: int | str


class DownloadVersionBody(_Body):
    """POST /api/transplant/download_version — {title, source:{mangaId}}."""
    title: str
    source: _Source


class NativeProgressBody(_Body):
    """POST /api/anime/native/progress — progreso del reproductor NATIVO.

    `position`/`duration` en segundos; el handler ya los pasa por `float(...)`, así que aquí
    aceptamos que lleguen como número o como string numérico (pydantic los coacciona) — lo que
    NO queremos colar es un `position` no numérico, que `float()` convertiría en 0 en silencio."""
    anime_id: str | int
    episode: int | str | float
    position: float = 0
    duration: float = 0
    ended: bool = False


def validate_and_log(model: type[BaseModel], payload, endpoint: str):
    """Valida `payload` contra `model`. Si casa, devuelve la instancia; si NO, lo registra por
    observabilidad y devuelve None. NUNCA propaga ni rechaza: el endpoint sigue con su parseo de
    siempre. Es una SONDA, no una barrera (todavía).

    Devolver la instancia (no solo True/None) permite que, cuando pasemos a rechazar, el endpoint
    reutilice los campos ya validados y tipados en vez de re-parsear."""
    if not isinstance(payload, dict):
        record_error("contract", f"{endpoint}: el body no es un objeto JSON "
                     f"({type(payload).__name__})", endpoint=endpoint)
        return None
    try:
        return model.model_validate(payload)
    except ValidationError as e:
        # include_input=False: no volcamos datos del usuario al log/SSE (privacidad + tamaño).
        record_error("contract", f"{endpoint}: {e.error_count()} violación(es) de contrato",
                     endpoint=endpoint,
                     errors=e.errors(include_url=False, include_input=False))
        return None
