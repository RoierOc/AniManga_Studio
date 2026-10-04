"""Contrato e infraestructura compartida de los adapters de fuente (Fase 0).

Cada adapter expone:
    NAME: str
    search(query: str, limit: int) -> list[dict]     # parciales normalizados (con id canónico)
    fetch(ext_id: str) -> dict | None                # parcial completo de UNA obra

Reglas comunes a TODOS los adapters («falló ≠ vacío»):
  · Envuelven su red en try/except.
  · Excepción / HTTP no-ok  → `record_error('discovery', …)` y devuelven [] / None (fail-safe).
  · Respuesta vacía LEGÍTIMA (la fuente no tiene la obra) → devuelven [] / None SIN loguear.
Nunca revientan la cascada: una fuente caída simplemente no aporta parcial.
"""
from __future__ import annotations

import os
import time

from api.resilient_http import http
from api.observability import record_error

COMPONENT = "discovery"

# Timing de rendimiento OPCIONAL: coste cero salvo que se active con la env. Una sola línea
# por llamada (nunca por resultado: no es un bucle caliente).
_LOG_TIMING = os.getenv("DISCOVERY_LOG_TIMING", "0") == "1"


def log_timing(op: str, ms: float, **ctx) -> None:
    if not _LOG_TIMING:
        return
    extra = " ".join(f"{k}={v}" for k, v in ctx.items())
    print(f"[discovery] {op} {ms:.0f}ms {extra}".rstrip(), flush=True)


def get_json(url: str, op: str, *, params=None, timeout: int = 15):
    """GET → JSON con fail-safe. Devuelve (data|None). None = falló (ya logueado)."""
    t0 = time.monotonic()
    try:
        resp = http.get(url, params=params, timeout=timeout)
        if not resp.ok:
            record_error(COMPONENT, f"HTTP {resp.status_code}", op=op, url=url)
            return None
        return resp.json()
    except Exception as e:                                   # red, JSON inválido, timeout…
        record_error(COMPONENT, e, op=op, url=url)
        return None
    finally:
        log_timing(op, (time.monotonic() - t0) * 1000)


def post_json(url: str, op: str, *, json=None, timeout: int = 15):
    """POST → JSON con fail-safe (MangaUpdates usa POST para buscar)."""
    t0 = time.monotonic()
    try:
        resp = http.post(url, json=json, timeout=timeout)
        if not resp.ok:
            record_error(COMPONENT, f"HTTP {resp.status_code}", op=op, url=url)
            return None
        return resp.json()
    except Exception as e:
        record_error(COMPONENT, e, op=op, url=url)
        return None
    finally:
        log_timing(op, (time.monotonic() - t0) * 1000)
