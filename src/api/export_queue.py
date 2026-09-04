"""Estado durable de la cola de exportación.

El worker de exportación sigue viviendo en ``export.py``. Este módulo sólo se ocupa de
persistir la intención del usuario y de los pequeños payloads binarios que hacen falta para
reconstruirla después de un reinicio. Mantener esta frontera evita que el API HTTP conozca el
formato del ledger o que el ledger tenga que importar el worker.
"""

import base64
import os
import re
import tempfile
from pathlib import Path

from api.observability import record_error
from api.runtime import DATA_ROOT, read_json_safe, write_json_atomic


_STATE_PATH = Path(DATA_ROOT) / "export_tasks.json"
_PAYLOAD_DIR = Path(DATA_ROOT) / "_export_task_payloads"
_STATE_VERSION = 1
_TASK_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

# Son los campos que describen una exportación. ``cover_path`` no se persiste: es una ruta
# proporcionada por el cliente y no debe convertirse en una referencia duradera a un archivo
# arbitrario. Las portadas que sí vienen del formulario se guardan en un sidecar controlado.
_REQUEST_KEYS = (
    "title", "chapters", "volume_name", "format", "quality", "codec",
    "downscale_half", "exclude_pages", "meta", "profile",
)


def _valid_task_id(task_id: str) -> str:
    value = str(task_id or "")
    if not _TASK_ID_RE.fullmatch(value):
        raise ValueError("invalid export task id")
    return value


def _payload_path(task_id: str) -> Path:
    """Devuelve siempre un hijo directo del directorio de payloads."""
    return _PAYLOAD_DIR / f"{_valid_task_id(task_id)}.cover"


def _write_bytes_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, dir=str(path.parent), prefix=f".{path.name}.") as f:
            tmp = Path(f.name)
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        if tmp:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
        raise


def _decode_cover(value: str) -> bytes:
    raw = str(value or "").strip()
    if raw.startswith("data:"):
        raw = raw.split(",", 1)[-1]
    return base64.b64decode(raw, validate=True)


def prepare_request(task_id: str, data: dict) -> dict:
    """Recorta una petición a lo necesario para reanudarla y externaliza su portada.

    El diccionario devuelto es JSON-serializable y no contiene el base64 potencialmente grande.
    Una portada inválida conserva el comportamiento anterior (la exportación puede continuar sin
    ella), pero se registra y no se guarda basura en el ledger.
    """
    task_id = _valid_task_id(task_id)
    source = data if isinstance(data, dict) else {}
    out = {}
    for key in _REQUEST_KEYS:
        if key not in source:
            continue
        value = source[key]
        if key in ("chapters", "exclude_pages") and isinstance(value, (tuple, set)):
            value = list(value)
        out[key] = value

    cover = source.get("cover_data")
    if cover:
        try:
            _write_bytes_atomic(_payload_path(task_id), _decode_cover(cover))
            out["_cover_file"] = f"{task_id}.cover"
        except (ValueError, TypeError, base64.binascii.Error) as exc:
            record_error("export", exc, op="persist_cover", task=task_id)
        except OSError:
            # Un fallo de disco sí debe impedir que se anuncie una cola supuestamente durable.
            raise
    return out


def restore_request(spec: dict) -> dict:
    """Reconstruye la petición de worker desde el ledger y su sidecar, si existe."""
    if not isinstance(spec, dict):
        return {}
    out = {k: v for k, v in spec.items() if k != "_cover_file"}
    name = str(spec.get("_cover_file") or "")
    if name and Path(name).name == name and _TASK_ID_RE.fullmatch(name.removesuffix(".cover")):
        try:
            raw = (_PAYLOAD_DIR / name).read_bytes()
            out["cover_data"] = base64.b64encode(raw).decode("ascii")
        except FileNotFoundError:
            # La exportación sigue siendo recuperable; simplemente perdió la portada opcional.
            pass
        except OSError as exc:
            record_error("export", exc, op="restore_cover", file=name)
    return out


def remove_payload(task_id: str) -> None:
    try:
        _payload_path(task_id).unlink(missing_ok=True)
    except (OSError, ValueError) as exc:
        record_error("export", exc, op="remove_cover", task=str(task_id))


def load_state() -> tuple[dict, dict]:
    """Carga ``(tasks, requests)``; un ledger ausente equivale a una cola vacía."""
    raw = read_json_safe(_STATE_PATH, {}, component="export_queue")
    if not isinstance(raw, dict):
        return {}, {}
    tasks = raw.get("tasks") or {}
    requests = raw.get("requests") or {}
    if isinstance(tasks, list):
        tasks = {str(t.get("task_id")): t for t in tasks if isinstance(t, dict) and t.get("task_id")}
    if not isinstance(tasks, dict):
        tasks = {}
    if not isinstance(requests, dict):
        requests = {}
    clean_tasks = {str(k): v for k, v in tasks.items() if isinstance(v, dict)}
    clean_requests = {str(k): v for k, v in requests.items() if isinstance(v, dict)}
    return clean_tasks, clean_requests


def save_state(tasks: dict, requests: dict, *, durable: bool = True) -> None:
    """Publica el ledger completo de forma atómica."""
    payload = {
        "version": _STATE_VERSION,
        "tasks": {
            str(task_id): {k: v for k, v in task.items() if not k.startswith("_")}
            for task_id, task in (tasks or {}).items()
            if isinstance(task, dict)
        },
        "requests": {str(task_id): spec for task_id, spec in (requests or {}).items() if isinstance(spec, dict)},
    }
    write_json_atomic(_STATE_PATH, payload, indent=2, durable=durable, keep_backup=True)


def prune_payloads(requests: dict) -> None:
    """Limpia sidecars huérfanos sin tocar los que siguen referenciados por la cola."""
    try:
        known = {str(v.get("_cover_file")) for v in (requests or {}).values() if isinstance(v, dict) and v.get("_cover_file")}
        if not _PAYLOAD_DIR.is_dir():
            return
        for path in _PAYLOAD_DIR.glob("*.cover"):
            if path.name not in known:
                path.unlink(missing_ok=True)
    except OSError as exc:
        record_error("export", exc, op="prune_cover_payloads")


def state_path() -> Path:
    return _STATE_PATH
