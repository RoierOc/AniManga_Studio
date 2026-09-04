"""Invalidación ligera de la instantánea de la Biblioteca.

Las operaciones que escriben páginas no deben conocer los detalles del caché de
`library.py`. Este módulo mantiene una revisión en memoria y publica un evento
SSE sin incluir títulos ni rutas, por lo que también sirve para la Biblioteca
oculta sin filtrar su contenido.
"""
from __future__ import annotations

import threading


_revision = 0
_lock = threading.Lock()


def current_revision() -> int:
    """Devuelve la revisión actual de los datos locales de manga."""
    with _lock:
        return _revision


def mark_changed() -> int:
    """Avanza la revisión y avisa a los clientes conectados.

    El aviso SSE es una optimización: si el stream no está disponible, la
    revisión sigue invalidando el caché para la siguiente petición.
    """
    global _revision
    with _lock:
        _revision += 1
        revision = _revision

    try:
        from api.runtime import push_sse_event
        push_sse_event("library_changed", revision=revision)
    except Exception:
        # Una notificación caída nunca debe convertir una escritura ya hecha en
        # un fallo de descarga/importación/escalado.
        pass
    return revision
