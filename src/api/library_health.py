"""Comprobaciones de salud de la biblioteca de manga, solo lectura.

Reúne controles existentes, cada uno en su módulo:
  - **Fuentes cruzadas**: el mangaId de Suwayomi derivó a otra obra ([[project_source_id_drift]]).
  - **Identidad MangaDex**: la obra no se pudo verificar en MangaDex ([[project_manga_canonical_identity]]).
  - **Errores en vivo**: contador por costura de `observability` (fallos recientes visibles).

El check no re-resuelve por red. La interfaz combina este resultado con la integridad de archivos
de qBittorrent (`/api/storage/integrity`), que también es explícita y de solo lectura. El endpoint
de reparación se conserva por compatibilidad, pero el panel no lo ejecuta ni lo expone."""
import json as _json

from flask import Blueprint, request, jsonify

from api.roots import series_dirs, series_titles
from api.observability import record_error

health_bp = Blueprint("health", __name__)


def _identity_snapshot() -> dict:
    """Estado de identidad MangaDex por obra, leído de la caché `.identity.json` (sin red). Una
    obra sin caché aún no se ha resuelto (no cuenta como problema); una CON caché pero sin
    `md_uuid` es 'sin identidad verificable'."""
    # Todas las raíces: una obra que vive sólo en el segundo disco también tiene identidad
    # que revisar, y antes ni se miraba. Ver `api/roots.py`.
    unresolved, unchecked, failed = [], 0, []
    folders = [dirs[0] for dirs in series_titles().values()]
    if not folders:
        return {"unresolved": [], "unchecked": 0, "total": 0, "failed": []}
    for d in folders:
        p = next((x / ".identity.json" for x in series_dirs(d.name)
                  if (x / ".identity.json").exists()), None)
        if p is None:
            unchecked += 1
            continue
        try:
            ident = _json.loads(p.read_text(encoding="utf-8"))
            if not isinstance(ident, dict):
                raise ValueError("identity must be a JSON object")
        except Exception as e:
            record_error("library_health", e, op="read_identity", folder=d.name)
            failed.append(d.name)
            continue
        if not ident.get("md_uuid"):
            unresolved.append(d.name)
    return {"unresolved": unresolved, "unchecked": unchecked, "total": len(folders),
            "failed": failed}


def _services_snapshot() -> list:
    """¿Está escuchando cada servicio local? Sonda TCP, no llamada a la API, a propósito:

    no necesita claves, no despierta a nadie y **acota el coste** — un puerto que no acepta la
    conexión responde en microsegundos, mientras que preguntar por HTTP a un Sonarr que aún
    arranca cuesta segundos. Aquí sólo interesa la respuesta binaria "hay alguien"; los detalles
    (versión, error) los da cada módulo en su propio `/status`.

    Hasta ahora sólo Suwayomi tenía dónde verse: que Sonarr, Radarr, Prowlarr o qBittorrent
    estuvieran caídos se notaba únicamente por una lista vacía. Un fallo de conexión debe distinguirse
    de una respuesta vacía legítima.
    """
    import socket
    from urllib.parse import urlparse
    from api.config_store import get_secret

    qbt = urlparse(get_secret("QBT_URL", "http://localhost:8080"))
    servicios = [
        ("Suwayomi", "localhost", 4567, "Fuentes de manga", "suwayomi/start.sh"),
        ("Sonarr", "localhost", 8989, "Series", "servarr/start.sh"),
        ("Radarr", "localhost", 7878, "Películas", "servarr/start.sh"),
        ("Prowlarr", "localhost", 9696, "Indexers de torrents", "servarr/start.sh"),
        ("qBittorrent", qbt.hostname or "localhost", qbt.port or 8080, "Descargas", None),
    ]
    out = []
    for nombre, host, port, para, arranque in servicios:
        try:
            with socket.create_connection((host, port), timeout=0.5):
                online = True
        except OSError:
            online = False        # rehusada o sin respuesta: para la UI es lo mismo, no está
        out.append({"name": nombre, "port": port, "online": online,
                    "for": para, "start": arranque})
    return out


@health_bp.route("/check", methods=["GET"])
def check():
    """Instantánea de salud (solo lectura). Cada consulta informa si pudo completarse."""
    # Fuentes: report-only (fix=False). Consulta la DB local de Suwayomi por ref (rápido).
    checks = {"sources": {"ok": True}, "identity": {"ok": True},
              "services": {"ok": True}, "errors": {"ok": True}}
    try:
        from api.source_identity import verify_source_refs
        src = verify_source_refs(fix=False)
    except Exception as e:
        record_error("library_health", e, op="check_sources")
        src = []
        checks["sources"]["ok"] = False
    # Solo 'roto' (no re-resoluble) es un PROBLEMA. 'auto' = el id derivó pero la obra se
    # re-resuelve sola al abrir (el número de Suwayomi es efímero, no es un cruce real).
    broken = [r for r in src if r.get("status") == "roto"]
    autoheal = [r for r in src if r.get("status") == "auto"]
    unreachable = [r for r in src if r.get("status") == "no-consultable"]

    try:
        ident = _identity_snapshot()
        checks["identity"]["ok"] = not ident.get("failed")
    except Exception as e:
        record_error("library_health", e, op="check_identity")
        ident = {"unresolved": [], "unchecked": 0, "total": 0, "failed": []}
        checks["identity"]["ok"] = False

    try:
        from api.observability import error_counts
        errors = error_counts()
    except Exception:
        errors = {}
        checks["errors"]["ok"] = False

    try:
        services = _services_snapshot()
    except Exception as e:
        record_error("library_health", e, op="check_services")
        services = []
        checks["services"]["ok"] = False
    caidos = [s for s in services if not s["online"]]

    # Un servicio caído CUENTA como problema: es la causa más frecuente de "no me sale nada".
    problems = len(broken) + len(ident["unresolved"]) + len(caidos)
    return jsonify({
        "ok": all(check["ok"] for check in checks.values()),
        "problems": problems,
        "checks": checks,
        "sources": {"broken": broken, "autoheal": autoheal, "unreachable": unreachable},
        "identity": ident,
        "services": services,
        "errors": errors,
    })


@health_bp.route("/repair", methods=["POST"])
def repair():
    """Ejecuta los fixers reales (pueden tocar la red). `?what=sources,identity` limita el alcance;
    por defecto ambos. Devuelve un resumen de lo corregido."""
    what = (request.args.get("what") or "sources,identity").split(",")
    out = {"ok": True}
    if "sources" in what:
        try:
            from api.source_identity import verify_source_refs
            rep = verify_source_refs(fix=True)
            out["sources"] = {"fixed": sum(1 for r in rep if r.get("fixed")),
                              "crossed": sum(1 for r in rep if str(r.get("status", "")).startswith("cruzado")),
                              "report": rep}
        except Exception as e:
            record_error("library_health", e, op="repair_sources")
            out["sources"] = {"error": str(e)}
    if "identity" in what:
        try:
            from api.manga_identity import verify_library
            rep = verify_library(fix_source_meta=True)
            out["identity"] = {"resolved": sum(1 for r in rep if r.get("verified")),
                               "fixed": sum(1 for r in rep if r.get("fixed_source_meta")),
                               "report": rep}
        except Exception as e:
            record_error("library_health", e, op="repair_identity")
            out["identity"] = {"error": str(e)}
    return jsonify(out)
