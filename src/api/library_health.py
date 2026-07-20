"""Salud de la biblioteca de manga — un solo sitio donde ver (y reparar) los problemas que hoy
solo se detectaban en barridos manuales o en silencio.

Reúne los tres controles ya existentes, cada uno en su módulo:
  - **Fuentes cruzadas**: el mangaId de Suwayomi derivó a otra obra ([[project_source_id_drift]]).
  - **Identidad MangaDex**: la obra no se pudo verificar en MangaDex ([[project_manga_canonical_identity]]).
  - **Errores en vivo**: contador por costura de `observability` (fallos recientes visibles).

El CHECK es de solo lectura y rápido (lee la DB local de Suwayomi y las cachés `.identity.json`, sin
re-resolver por red); la REPARACIÓN sí ejecuta los fixers reales (pueden tocar la red). No duplica la
integridad de ficheros: esa vive en `/api/storage/integrity` (panel Almacenamiento)."""
import json as _json
from pathlib import Path

from flask import Blueprint, request, jsonify

from api.runtime import manga_dir
from api.observability import record_error

health_bp = Blueprint("health", __name__)


def _identity_snapshot() -> dict:
    """Estado de identidad MangaDex por obra, leído de la caché `.identity.json` (sin red). Una
    obra sin caché aún no se ha resuelto (no cuenta como problema); una CON caché pero sin
    `md_uuid` es 'sin identidad verificable'."""
    root = Path(manga_dir())
    unresolved, unchecked = [], 0
    if not root.exists():
        return {"unresolved": [], "unchecked": 0, "total": 0}
    folders = [d for d in root.iterdir() if d.is_dir() and not d.name.startswith(".")]
    for d in folders:
        p = d / ".identity.json"
        if not p.exists():
            unchecked += 1
            continue
        try:
            ident = _json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:
            record_error("library_health", e, op="read_identity", folder=d.name)
            continue
        if not ident.get("md_uuid"):
            unresolved.append(d.name)
    return {"unresolved": unresolved, "unchecked": unchecked, "total": len(folders)}


@health_bp.route("/check", methods=["GET"])
def check():
    """Instantánea de salud (solo lectura, rápida). Fuentes cruzadas + identidad sin verificar +
    errores por costura desde el arranque. No modifica nada."""
    # Fuentes: report-only (fix=False). Consulta la DB local de Suwayomi por ref (rápido).
    try:
        from api.source_identity import verify_source_refs
        src = verify_source_refs(fix=False)
    except Exception as e:
        record_error("library_health", e, op="check_sources")
        src = []
    # Solo 'roto' (no re-resoluble) es un PROBLEMA. 'auto' = el id derivó pero la obra se
    # re-resuelve sola al abrir (el número de Suwayomi es efímero, no es un cruce real).
    broken = [r for r in src if r.get("status") == "roto"]
    autoheal = [r for r in src if r.get("status") == "auto"]
    unreachable = [r for r in src if r.get("status") == "no-consultable"]

    ident = _identity_snapshot()

    try:
        from api.observability import error_counts
        errors = error_counts()
    except Exception:
        errors = {}

    problems = len(broken) + len(ident["unresolved"])
    return jsonify({
        "ok": True,
        "problems": problems,
        "sources": {"broken": broken, "autoheal": autoheal, "unreachable": unreachable},
        "identity": ident,
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
