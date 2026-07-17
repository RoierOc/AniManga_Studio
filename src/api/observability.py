#!/usr/bin/env python3
"""Observabilidad de errores — Fase 1.

Objetivo único: que un fallo deje de ser SILENCIOSO. El error más caro del proyecto es un
`except` que se traga algo y el sistema sigue como si nada (un hilo que muere, un barrido que
falla y se lee como "no había nada"). Este módulo da un punto ÚNICO por el que pasar esos
errores para que sean visibles: log a stderr (ya enrutado al fichero rotado en app.py), un
contador por componente, y un evento SSE que el Centro de Actividad puede mostrar.

NO cambia el camino feliz: solo se invoca en ramas de error. Sin dependencias pesadas
(usa el bus SSE de runtime y el logging estándar), así que es seguro importarlo en cualquier
costura.
"""

from __future__ import annotations

import functools
import logging
import threading
import traceback
from collections import Counter
from typing import Any, Callable

_log = logging.getLogger("animanga.errors")

# Contador por componente. Es diagnóstico (cuántos errores lleva cada costura desde el
# arranque), no un mecanismo de control; por eso vive en memoria y se reinicia con el proceso.
_error_counts: Counter = Counter()
_lock = threading.Lock()


def record_error(component: str, exc: BaseException | str, **context: Any) -> None:
    """Registra un error de forma VISIBLE. Nunca lanza (una costura de error no debe crear
    otro fallo). `component` es la costura ('download', 'upscale', 'sources'…); `context`
    son datos que ayuden a diagnosticar (task_id, url, chapter…)."""
    try:
        if isinstance(exc, BaseException):
            message = f"{type(exc).__name__}: {exc}"
            tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        else:
            message = str(exc)
            tb = ""

        with _lock:
            _error_counts[component] += 1

        ctx = " ".join(f"{k}={v!r}" for k, v in context.items())
        _log.error("[%s] %s %s", component, message, ctx)
        if tb:
            _log.error("[%s] traceback:\n%s", component, tb)

        # Superficie visible: el Centro de Actividad drena el bus SSE. Import perezoso para
        # no crear un ciclo con runtime en tiempo de import.
        try:
            from api.runtime import push_sse_event
            push_sse_event("error", component=component, message=message, **context)
        except Exception:
            # El bus no debe poder tumbar el registro de errores. El log de arriba ya quedó.
            pass
    except Exception:
        # Blindaje total: registrar un error jamás debe propagar una excepción nueva.
        pass


def error_counts() -> dict[str, int]:
    """Instantánea del contador por componente (para tests y un endpoint de diagnóstico)."""
    with _lock:
        return dict(_error_counts)


def reset_error_counts() -> None:
    """Sólo para tests: deja el contador a cero."""
    with _lock:
        _error_counts.clear()


def thread_guard(component: str) -> Callable[[Callable], Callable]:
    """Envuelve el target de un hilo para que una excepción NO capturada emita un evento
    visible en vez de matar el hilo en silencio. El camino feliz queda idéntico: sólo actúa
    si el target revienta hasta arriba.

        threading.Thread(target=thread_guard('download')(run), ...).start()
    """
    def deco(fn: Callable) -> Callable:
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except Exception as e:
                record_error(component, e, target=getattr(fn, "__name__", str(fn)))
                # Se re-tragó a propósito: el hilo ya iba a terminar; ahora, al menos, se ve.
                return None
        return wrapper
    return deco
