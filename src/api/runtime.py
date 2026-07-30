#!/usr/bin/env python3
"""
Shared runtime configuration and helpers.
"""

from __future__ import annotations

from collections import deque
from decimal import Decimal, InvalidOperation
from pathlib import Path
import os
import re
import sys
import threading


# ── SSE event bus ─────────────────────────────────────────────────────────────
# Push-based notification queue. Components call push_sse_event(); the SSE
# stream generator calls get_sse_events_since(seq) to drain only new events.
# deque(maxlen=500) acts as a ring buffer so memory never grows unbounded.

_sse_deque: deque = deque(maxlen=500)
_sse_seq: int = 0
_sse_lock = threading.Lock()


def push_sse_event(event_type: str, **payload) -> None:
    global _sse_seq
    with _sse_lock:
        _sse_seq += 1
        _sse_deque.append({"seq": _sse_seq, "type": event_type, **payload})


def get_sse_events_since(seq: int) -> list:
    with _sse_lock:
        return [e for e in _sse_deque if e["seq"] > seq]


def get_current_seq() -> int:
    with _sse_lock:
        return _sse_seq


PROJECT_ROOT = Path(__file__).resolve().parents[2]

# All user data (downloaded/upscaled manga) defaults under the repo itself so a
# fresh clone works with zero configuration. Override via env vars to point
# at any other location (e.g. a bigger disk) exactly like before.
# `or` (not a plain dict .get default) so a blank value in .env.example-derived
# .env files — MANGA_DIR= with nothing after it — falls through to the default
# instead of resolving to Path("") (cwd).
DATA_ROOT = Path(os.environ.get("DATA_ROOT") or str(PROJECT_ROOT / "data")).expanduser()
MANGA_DIR = Path(os.environ.get("MANGA_DIR") or str(DATA_ROOT / "MangaLibrary")).expanduser()
UPSCALED_DIR = Path(os.environ.get("UPSCALED_DIR") or str(DATA_ROOT / "MangaLibrary_Upscaled")).expanduser()

# Biblioteca oculta: raíces gemelas, físicamente separadas de las de arriba, con
# el mismo formato interno (carpeta por título, mismo layout de capítulos). Un
# manga/anime añadido en modo oculto vive por completo bajo estas rutas, así
# que nunca aparece al listar/medir/exportar la biblioteca normal.
HIDDEN_MANGA_DIR = Path(os.environ.get("HIDDEN_MANGA_DIR") or str(DATA_ROOT / "MangaLibrary_Hidden")).expanduser()
HIDDEN_UPSCALED_DIR = Path(os.environ.get("HIDDEN_UPSCALED_DIR") or str(DATA_ROOT / "MangaLibrary_Hidden_Upscaled")).expanduser()

# Modo de biblioteca activo para este proceso — SOLO en memoria, nunca se
# persiste a disco. Cada arranque del backend empieza siempre en "normal": no
# debe quedar ningún rastro en disco de que el modo oculto existió o estuvo
# activo. `manga_dir()`/`upscaled_dir()` son la indirección que el resto del
# backend debe usar (en vez de las constantes MANGA_DIR/UPSCALED_DIR) en
# cualquier operación que determine qué se lista/añade/borra/mide como
# contenido de biblioteca.
_library_mode = "normal"
_library_mode_lock = threading.Lock()


def get_library_mode() -> str:
    with _library_mode_lock:
        return _library_mode


def set_library_mode(mode: str) -> str:
    global _library_mode
    if mode not in ("normal", "hidden"):
        raise ValueError("mode must be 'normal' or 'hidden'")
    with _library_mode_lock:
        _library_mode = mode
    return mode


def manga_dir() -> Path:
    return HIDDEN_MANGA_DIR if get_library_mode() == "hidden" else MANGA_DIR


def upscaled_dir() -> Path:
    return HIDDEN_UPSCALED_DIR if get_library_mode() == "hidden" else UPSCALED_DIR
# Translation QA (testing-only): when the QA flag is on, the transplant run keeps per-page
# debug bundles here (EN art + matched ES + detection overlay + stats) so flagged pages can
# be diagnosed. Off by default; "Borrar datos QA" wipes this dir.
QA_DIR = Path(os.environ.get("QA_DIR") or str(DATA_ROOT / "_translation_qa")).expanduser()

# Upscale model weights live here by default (gitignored — see docs/MODELS.md
# for download links and the registry.json format that makes them pluggable).
# Per-model height thresholds for adaptive registries live in registry.json
# itself (each sub-model's "height_max"), not here.
MODELS_DIR = Path(os.environ.get("MODELS_DIR") or str(PROJECT_ROOT / "models")).expanduser()

PYTHON_EXECUTABLE = os.environ.get("PYTHON_EXECUTABLE", sys.executable)


def normalize_chapter(chapter) -> str:
    """Normalize chapter values so different sources can be compared reliably."""
    if chapter is None:
        return ""

    raw = str(chapter).strip()
    if not raw:
        return ""

    if raw.lower() == "one_shot":
        return "one_shot"

    if raw.lower().startswith("ch"):
        raw = raw[2:]

    try:
        value = Decimal(raw)
        if value == value.to_integral():
            return str(int(value))
        normalized = format(value.normalize(), "f").rstrip("0").rstrip(".")
        return normalized or "0"
    except InvalidOperation:
        trimmed = raw.lstrip("0")
        return trimmed or "0"


def sanitize_title_for_id(title: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", (title or "").strip())
    cleaned = re.sub(r"_+", "_", cleaned).strip("_")
    return cleaned or "manga"


def build_task_id(title: str, chapter, task_type: str) -> str:
    chapter_part = normalize_chapter(chapter) or str(chapter)
    return f"{sanitize_title_for_id(title)}_{task_type}_ch{chapter_part}"


# ── Escritura de estado sin ventana de fichero a medias ───────────────────────
# `Path.write_text` (y `open(p, "w")`) TRUNCAN el fichero antes de escribir: entre esa
# truncada y el último byte, el fichero está VACÍO en disco. Si en esa ventana se cierra
# WSL, se para el server, muere el proceso o se va la luz, no pierdes la última escritura:
# pierdes el fichero ENTERO. Y ahí vive estado que no se puede volver a descargar — el
# progreso de lectura, el historial, la identidad canónica, los ajustes.
# No es teórico: ya apareció un `media_progress` corrupto (revisión de julio de 2026).
#
# `os.replace` es ATÓMICO sobre ext4 y sobre NTFS: o está el fichero viejo entero, o el
# nuevo entero. Nunca medio. El temporal va en la MISMA carpeta a propósito (renombrar
# entre sistemas de archivos no es atómico y `os.replace` degradaría a copiar).
import json as _json
import time as _time


def write_json_atomic(path, obj, *, ensure_ascii=False, indent=None,
                      durable=True, keep_backup=False) -> None:
    """Escribe un JSON de forma que nunca quede a medias.

    durable=False evita el fsync (~9 ms sobre ext4) para ficheros PRESCINDIBLES —
    cachés y logs de uso, donde perder la última escritura tras un corte no cuesta nada;
    la atomicidad se mantiene igual. Para estado del usuario, durable=True.
    keep_backup=True conserva el último bueno en `<fichero>.bak` (ver read_json_safe).
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    # El nombre del temporal lleva PID **e id de hilo**: Waitress sirve con 8 hilos, así que dos
    # peticiones a la vez compartían el mismo `.fichero.<pid>.tmp` — uno hacía `replace` y al otro
    # le estallaba un FileNotFoundError (medido: 87 de 240 escrituras concurrentes, y 2 casos
    # reales de `manga_progress.json` en el log). Atómico no era: era atómico entre PROCESOS.
    tmp = p.with_name(f".{p.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(_json.dumps(obj, ensure_ascii=ensure_ascii, indent=indent))
            if durable:
                f.flush()
                os.fsync(f.fileno())      # sin esto, `replace` puede publicar un fichero vacío
        if keep_backup and p.exists():
            try:
                os.replace(p, p.with_name(p.name + ".bak"))
            except OSError:
                pass                       # el respaldo es un extra, nunca bloquea la escritura
        os.replace(tmp, p)
    except BaseException:
        try:
            os.unlink(tmp)                 # no dejar basura si falla a medio camino
        except OSError:
            pass
        raise


def read_json_safe(path, default=None, *, component: str = "state"):
    """Lee un JSON escrito con write_json_atomic, cayendo al `.bak` si el bueno está roto.

    Distingue "no había" de "falló" (regla del repo): fichero ausente devuelve el default en
    silencio; fichero ilegible se REGISTRA antes de caer al respaldo o al default.
    """
    p = Path(path)
    if not p.exists():
        return default
    try:
        return _json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        bak = p.with_name(p.name + ".bak")
        try:
            from api.observability import record_error
            record_error(component, e, path=str(p), recovered=bak.exists())
        except Exception:
            pass
        if bak.exists():
            try:
                return _json.loads(bak.read_text(encoding="utf-8"))
            except Exception:
                pass
        return default


# ── Caché en DISCO (no RAM) ───────────────────────────────────────────────────
# Para resultados caros de búsquedas online (ranking de versiones, portadas, etc.).
# RAM plana: solo se lee/escribe un JSON por namespace; el archivo se acota por TTL y
# por max_entries (se descartan las entradas más viejas), así el disco tampoco crece sin fin.

_CACHE_DIR = MANGA_DIR / ".cache"
_cache_lock = threading.Lock()


def cache_get(namespace: str, key: str, ttl: float):
    """Valor cacheado si tiene < ttl segundos; si no, None."""
    p = _CACHE_DIR / f"{namespace}.json"
    try:
        with _cache_lock:
            if not p.exists():
                return None
            data = _json.loads(p.read_text(encoding="utf-8"))
        entry = data.get(key)
        if not entry or (_time.time() - entry.get("ts", 0)) > ttl:
            return None
        return entry.get("value")
    except Exception:
        return None


def cache_set(namespace: str, key: str, value, ttl: float = 0, max_entries: int = 200):
    """Guarda en disco y purga expiradas (si ttl) + acota a max_entries (descarta las más viejas)."""
    p = _CACHE_DIR / f"{namespace}.json"
    try:
        with _cache_lock:
            _CACHE_DIR.mkdir(parents=True, exist_ok=True)
            data = {}
            if p.exists():
                try:
                    data = _json.loads(p.read_text(encoding="utf-8"))
                except Exception:
                    data = {}
            now = _time.time()
            data[key] = {"ts": now, "value": value}
            if ttl:
                data = {k: v for k, v in data.items() if (now - v.get("ts", 0)) <= ttl}
            if len(data) > max_entries:
                for k in sorted(data, key=lambda k: data[k].get("ts", 0))[:len(data) - max_entries]:
                    data.pop(k, None)
            write_json_atomic(p, data, durable=False)   # caché: perder la última escritura no cuesta nada
    except Exception:
        pass


# ── Registro de actividad (para las gráficas de uso del panel de estadísticas) ──
# Log append-only de eventos ligeros {t: unix, k: kind, n: título}. No requiere que el
# usuario inicie sesión; se acumula solo con el uso. Se poda a los últimos ~4000 eventos.
_activity_lock = threading.Lock()

def _activity_path() -> Path:
    return DATA_ROOT / "activity_log.json"

def log_activity(kind: str, name: str = "") -> None:
    """Anota un evento de uso (kind: 'upscale' | 'download' | 'translate' | 'watch').
    Barato y tolerante a fallo — nunca debe romper el flujo que lo llama."""
    try:
        p = _activity_path()
        with _activity_lock:
            try:
                data = _json.loads(p.read_text(encoding="utf-8")) if p.exists() else []
            except Exception:
                data = []
            data.append({"t": int(_time.time()), "k": kind, "n": (name or "")[:120]})
            if len(data) > 4000:
                data = data[-4000:]
            write_json_atomic(p, data, durable=False)
    except Exception:
        pass

def read_activity() -> list:
    try:
        return _json.loads(_activity_path().read_text(encoding="utf-8"))
    except Exception:
        return []


def cache_invalidate(namespace: str, key: str = None):
    """Borra una clave (o todo el namespace) — para forzar refresco."""
    p = _CACHE_DIR / f"{namespace}.json"
    try:
        with _cache_lock:
            if key is None:
                p.unlink(missing_ok=True)
                return
            if not p.exists():
                return
            data = _json.loads(p.read_text(encoding="utf-8"))
            if data.pop(key, None) is not None:
                write_json_atomic(p, data, durable=False)
    except Exception:
        pass
