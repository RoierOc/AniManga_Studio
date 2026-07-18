"""Blueprint del Manga Hub — descubrimiento centralizado de manga/manhwa/manhua/novelas.

Transporte fino: valida parámetros y delega en la capa `metasource`. Sin lógica de fusión
aquí. Prefijo /api/discovery. Gateado por la env MANGA_HUB (rollback en caliente → 503).
Fase 0: sólo backend, ninguna vista lo consume todavía. Ver [[project_manga_hub_discovery]].
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

from api import metasource
from api.observability import error_counts

discovery_bp = Blueprint("discovery", __name__)


@discovery_bp.before_request
def _gate():
    if not metasource.ENABLED:
        return jsonify({"error": "manga hub disabled"}), 503


@discovery_bp.route("/search")
def search():
    q = (request.args.get("q") or "").strip()
    if not q:
        return jsonify({"works": []})
    type_filter = (request.args.get("type") or "").strip() or None
    try:
        limit = int(request.args.get("limit", 20))
    except (TypeError, ValueError):
        limit = 20
    works = metasource.search_works(q, type_filter=type_filter, limit=limit)
    return jsonify({"works": works, "query": q})


@discovery_bp.route("/trending")
def trending():
    work_type = (request.args.get("type") or "manhwa").strip()
    try:
        limit = int(request.args.get("limit", 18))
    except (TypeError, ValueError):
        limit = 18
    return jsonify({"type": work_type, "works": metasource.trending_works(work_type, limit)})


@discovery_bp.route("/genres")
def genres():
    return jsonify({"genres": metasource.list_genres()})


@discovery_bp.route("/browse")
def browse():
    args = request.args
    try:
        page = int(args.get("page", 1))
        limit = int(args.get("limit", 24))
    except (TypeError, ValueError):
        page, limit = 1, 24
    out = metasource.browse(
        q=(args.get("q") or "").strip(),
        work_type=(args.get("type") or "").strip(),
        genres=args.getlist("genre"),
        status=(args.get("status") or "").strip(),
        sort=(args.get("sort") or "popularity").strip(),
        page=page, limit=limit,
    )
    return jsonify(out)


@discovery_bp.route("/work/<path:work_id>")
def work(work_id):
    w = metasource.get_work(work_id)
    if not w:
        return jsonify({"error": "not found", "id": work_id}), 404
    return jsonify({"work": w})


@discovery_bp.route("/health")
def health():
    """Diagnóstico: errores por costura (los del hub salen bajo 'discovery')."""
    return jsonify({
        "enabled": metasource.ENABLED,
        "errors": error_counts().get("discovery", 0),
        "error_counts": error_counts(),
    })
