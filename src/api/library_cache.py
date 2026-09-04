"""Caché de snapshots pequeños con stale-while-revalidate.

El escaneo de la Biblioteca mezcla varias operaciones de filesystem. Aunque las mediciones por
obra ya viven en SQLite, repetir el ensamblado completo en cada petición sigue siendo caro sobre
DrvFS. Este módulo sólo memoiza el resultado final en memoria: la primera petición mide y las
siguientes sirven la foto durante un TTL; al caducar, una petición recibe la foto anterior y un
hilo prepara la nueva para la siguiente.

Es genérico para que la política de caché no se mezcle con la lógica de negocio de `library.py`.
No persiste nada: reiniciar el backend sólo provoca un escaneo frío.
"""
from __future__ import annotations

import threading
import time
from collections.abc import Callable, Hashable
from typing import Any


_MISSING = object()


class SnapshotCache:
    """Sirve snapshots frescos o stale y garantiza un único refresco por clave."""

    def __init__(
        self,
        *,
        ttl: float,
        max_entries: int = 8,
        clock: Callable[[], float] | None = None,
        start_thread: Callable[[Callable[[], None]], None] | None = None,
    ) -> None:
        if ttl <= 0:
            raise ValueError("ttl must be positive")
        if max_entries <= 0:
            raise ValueError("max_entries must be positive")
        self._ttl = float(ttl)
        self._max_entries = max_entries
        self._clock = clock or time.monotonic
        self._start_thread = start_thread or self._start_daemon
        self._entries: dict[Hashable, tuple[float, Any]] = {}
        self._loading: dict[Hashable, threading.Event] = {}
        self._errors: dict[Hashable, Exception] = {}
        self._lock = threading.RLock()

    @staticmethod
    def _start_daemon(target: Callable[[], None]) -> None:
        threading.Thread(
            target=target,
            daemon=True,
            name="library-snapshot-refresh",
        ).start()

    def get(
        self,
        key: Hashable,
        loader: Callable[[], Any],
        *,
        refresh_loader: Callable[[], Any] | None = None,
        on_error: Callable[[Exception], None] | None = None,
    ) -> Any:
        """Obtiene una entrada y refresca en segundo plano cuando está vencida.

        `refresh_loader` permite propagar contexto de petición al hilo de refresco. Es importante
        para la Biblioteca oculta: `threading.local` no cruza hilos y un loader normal vería la
        biblioteca pública por defecto.
        """
        owner = False
        wait_for: threading.Event | None = None
        refresh_event: threading.Event | None = None
        stale = _MISSING
        start_refresh = False

        with self._lock:
            entry = self._entries.get(key)
            if entry is not None:
                age = self._clock() - entry[0]
                if age < self._ttl:
                    return entry[1]
                stale = entry[1]
                if key not in self._loading:
                    refresh_event = threading.Event()
                    self._loading[key] = refresh_event
                    start_refresh = True
            else:
                wait_for = self._loading.get(key)
                if wait_for is None:
                    wait_for = threading.Event()
                    self._loading[key] = wait_for
                    self._errors.pop(key, None)
                    owner = True

        if stale is not _MISSING:
            if start_refresh:
                refresh = refresh_loader or loader
                try:
                    self._start_thread(
                        lambda: self._load(
                            key, refresh, refresh_event, background=True, on_error=on_error
                        )
                    )
                except Exception as error:
                    with self._lock:
                        self._loading.pop(key, None)
                        refresh_event.set()
                    if on_error is not None:
                        on_error(error)
            return stale

        if owner:
            assert wait_for is not None
            return self._load(key, loader, wait_for, background=False, on_error=on_error)

        # Otra petición está haciendo el primer escaneo. Esperarla evita inventar una biblioteca
        # vacía y mantiene la diferencia entre «no hay obras» y «el escaneo falló».
        wait_for.wait()
        with self._lock:
            entry = self._entries.get(key)
            error = self._errors.pop(key, None)
        if entry is not None:
            return entry[1]
        if error is not None:
            raise error
        return self.get(key, loader, refresh_loader=refresh_loader, on_error=on_error)

    def _load(
        self,
        key: Hashable,
        loader: Callable[[], Any],
        event: threading.Event,
        *,
        background: bool,
        on_error: Callable[[Exception], None] | None,
    ) -> Any:
        try:
            value = loader()
        except Exception as error:
            with self._lock:
                self._loading.pop(key, None)
                if not background:
                    self._errors[key] = error
                event.set()
            if background and on_error is not None:
                try:
                    on_error(error)
                except Exception:
                    pass
            if not background:
                raise
            return None

        with self._lock:
            self._entries[key] = (self._clock(), value)
            self._loading.pop(key, None)
            self._errors.pop(key, None)
            event.set()
            while len(self._entries) > self._max_entries:
                oldest = min(self._entries, key=lambda item: self._entries[item][0])
                self._entries.pop(oldest, None)
        return value
