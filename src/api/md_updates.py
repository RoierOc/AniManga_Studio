"""Novedades de MangaDex por obra ("nuevo capítulo listo").

MangaDex como FUENTE DE VERDAD para saber qué capítulos hay disponibles y cuáles son NUEVOS
respecto a lo que el usuario ya tiene descargado y a la última vez que miró. Se eligió MangaDex
frente a las fuentes de Suwayomi porque su API es limpia (sin Cloudflare), su numeración es
consistente y expone `publishAt` por capítulo — los fansubs de Suwayomi no dan nada de eso de
forma fiable. Ver [[project_online_read_multisource_fallback]] [[project_manga_versions]].

No descarga páginas: solo compara catálogos. La descarga la hace el frontend reusando la
maquinaria existente (`/api/transplant/chapter_urls` + descarga agnóstica de origen).

Módulo propio (regla del proyecto: no apilar features nuevas en el god-module transplant.py);
reusa sus helpers de feed/local/meta para no duplicar lógica.
"""
import time

from flask import Blueprint, request, jsonify

from api.runtime import normalize_chapter
# Reusa helpers ya probados de transplant (feed MangaDex, ficheros locales, meta por obra).
from api.transplant import (
    _md_feed, _local_chapter_files,
    _read_source_meta, _write_source_meta,
)
from api.manga_identity import resolve_identity, verify_library

md_updates_bp = Blueprint("md_updates", __name__)

# Idiomas que cuentan como "verdad" por defecto: el adelantado no siempre es el inglés, así que
# se considera la UNIÓN de en+es y se marca "nuevo" en cuanto aparece en CUALQUIERA de los dos.
_DEFAULT_LANGS = ("en", "es", "es-la", "es-419")
_SEEN_KEY = "md_updates_seen"   # baseline ISO por obra (legado; ya no gatea el conteo)


def _fmt_num(x: float) -> str:
    """Número de capítulo legible: entero sin `.0` (49.0→"49"), con decimal si lo tiene (49.1)."""
    return str(int(x)) if float(x).is_integer() else ("%g" % x)


def _resolve_uuid(title: str, uuid_hint: str = "", al_id=None) -> str | None:
    """UUID CANÓNICO de MangaDex de la obra, VERIFICADO por identidad propia (alias de título y/o
    cruce del AniList-id contra `links.al`) — nunca por la fuente de descarga (el pin/Cobertura
    puede ser cualquier versión, incluso de otra obra por un emparejamiento malo) ni por un match
    de título débil. Si no se puede verificar → None (la obra no muestra novedades; jamás se asocia
    la obra equivocada). Ver `api.manga_identity`."""
    ident = resolve_identity(title, al_id=al_id, uuid_hint=uuid_hint)
    return ident.get("md_uuid") if ident else None


@md_updates_bp.route("/chapters", methods=["GET"])
def updates():
    """Capítulos de MangaDex de una obra, marcando cuáles VAS POR DETRÁS (más allá de tu alcance).
    Query: title (req), uuid?, al?, langs? (csv), have? (nº del capítulo MÁS ALTO de tu sección de
    Capítulos = local ∪ fuente activa ∪ MangaDex, no solo lo descargado). No baja páginas."""
    title = (request.args.get("title") or "").strip()
    if not title:
        return jsonify({"error": "title required"}), 400
    uuid = _resolve_uuid(title, request.args.get("uuid") or "", request.args.get("al") or None)
    if not uuid:
        return jsonify({"ok": True, "uuid": None, "chapters": [], "newCount": 0,
                        "reason": "no-mangadex-match"})
    langs = [l.strip().lower() for l in (request.args.get("langs") or "").split(",") if l.strip()] \
        or list(_DEFAULT_LANGS)

    local = set(_local_chapter_files(title).keys())          # capítulos ya descargados (norm)
    # ALCANCE del usuario = capítulo MÁS ALTO al que YA tiene acceso. NO son solo los descargados:
    # el usuario puede tener 1-250 en su SECCIÓN DE CAPÍTULOS vía la fuente activa (p.ej. mangas.in)
    # sin haberlos bajado — esos NO deben recomendarse. El frontend manda ese tope en `have` (máx de
    # local ∪ fuente activa ∪ MangaDex). El anclaje es max(descargado, have); si falta `have` se cae
    # a solo lo descargado (comportamiento previo). Anclar a un nº (no a match exacto) es a propósito:
    # las fuentes trocean distinto (tu "49" vs "49.1/49.2" de MangaDex) y un match exacto marcaría
    # como faltantes capítulos que ya tienes.
    local_nums = []
    for c in local:
        try:
            local_nums.append(float(c))
        except (TypeError, ValueError):
            pass
    max_local = max(local_nums) if local_nums else -1.0
    try:
        have = float(request.args.get("have"))
    except (TypeError, ValueError):
        have = -1.0
    reach = max(max_local, have)

    # Agrupa el feed por número normalizado; por capítulo se queda con la publicación MÁS RECIENTE
    # entre los idiomas pedidos (esa es la versión "lista" más fresca) y recuerda su lang/id.
    best: dict = {}
    for c in _md_feed(uuid):
        if c.get("lang") not in langs or not c.get("number"):
            continue
        chn = normalize_chapter(c["number"])
        if not chn:
            continue
        pub = c.get("publishedAt") or ""
        cur = best.get(chn)
        if cur is None or pub > (cur.get("publishedAt") or ""):
            best[chn] = {"chapter": chn, "number": c["number"], "lang": c["lang"],
                         "chapterId": c["id"], "publishedAt": pub}

    # Modelo "vas N por detrás" (elección del usuario): un ÚNICO indicador que cuenta TODO lo que
    # MangaDex tiene por delante de tu último capítulo descargado, sin distinguir "nuevo tras la
    # visita" de "atraso disponible". Antes se sembraba un baseline (`seen_iso`) para ocultar el
    # back-catálogo; el usuario prefiere ver el atraso, así que ese gating se elimina y el conteo
    # solo se limpia al DESCARGAR. `seen_iso` queda sin uso funcional (se conserva el /seen por
    # compatibilidad, pero ya no afecta al conteo).
    out = []
    behind_nums = []          # nº (float) de los que van por delante y no tienes
    for chn, e in best.items():
        downloaded = chn in local
        try:
            beyond_reach = float(chn) > reach
        except (TypeError, ValueError):
            beyond_reach = not downloaded
        # "Por detrás" = por DELANTE de tu ALCANCE (lo más alto de tu sección de capítulos, no solo
        # lo descargado) y NO descargado. Así, si tienes 1-250 en tu sección vía la fuente activa,
        # los caps ≤250 de MangaDex NO se recomiendan; solo lo genuinamente más nuevo. `isNew`
        # mantiene el nombre por compatibilidad con el frontend, pero significa "te falta / por detrás".
        is_behind = beyond_reach and (not downloaded)
        if is_behind:
            try:
                behind_nums.append(float(chn))
            except (TypeError, ValueError):
                pass
        out.append({**e, "downloaded": downloaded, "isNew": is_behind})

    # Más reciente primero: lo que te falta queda arriba, natural para "qué hay por delante".
    out.sort(key=lambda x: x.get("publishedAt") or "", reverse=True)
    behind_nums.sort()
    # Rango contiguo legible para el aviso "del X al Y" (el frontend muestra el conteo + rango).
    behind_from = _fmt_num(behind_nums[0]) if behind_nums else None
    behind_to = _fmt_num(behind_nums[-1]) if behind_nums else None
    return jsonify({"ok": True, "uuid": uuid, "langs": langs,
                    "chapters": out, "newCount": len(behind_nums),
                    "behindFrom": behind_from, "behindTo": behind_to})


@md_updates_bp.route("/seen", methods=["POST"])
def mark_seen():
    """Fija el baseline de "visto" a AHORA para una obra: los capítulos publicados hasta este
    instante dejan de contar como nuevos. El frontend lo llama al abrir/reconocer las novedades."""
    body = request.get_json(silent=True) or {}
    title = (body.get("title") or "").strip()
    if not title:
        return jsonify({"error": "title required"}), 400
    meta = _read_source_meta(title)
    meta[_SEEN_KEY] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    _write_source_meta(title, meta)
    return jsonify({"ok": True, "seen": meta[_SEEN_KEY]})


@md_updates_bp.route("/verify_identities", methods=["POST"])
def verify_identities():
    """Barrido de verificación de identidades: resuelve+cachea la identidad canónica MangaDex de
    cada obra (`.identity.json`) y corrige los `.source_meta.json` con pin __mangadex__ cruzado.
    Idempotente. `?fix=0` para solo informar sin tocar nada."""
    fix = (request.args.get("fix") or "1") != "0"
    report = verify_library(fix_source_meta=fix)
    changed = [r for r in report if r.get("fixed_source_meta")]
    resolved = [r for r in report if r.get("verified")]
    return jsonify({"ok": True, "total": len(report), "resolved": len(resolved),
                    "fixed": len(changed), "report": report})
