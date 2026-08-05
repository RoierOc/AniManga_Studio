"""Estabilidad de la FUENTE DE DESCARGA de una obra (mangaId de Suwayomi anclado por url).

Problema: el `.source_meta.json` guarda el mangaId NUMÉRICO de Suwayomi (tanto el de origen como
el de la versión FIJADA en `recommended_source`). Ese id es una fila de la DB de Suwayomi y se
REASIGNA cuando la DB se reconstruye/purga o se reinstala una fuente → un id guardado puede pasar a
apuntar a OTRA obra. Caso real: Ao no Hako fijado en "LeerCapitulo" tenía `mangaId=14936`, que tras
un re-fetch pasó a ser "Cantarella" (MangaFire) → la ficha leía y descargaba la obra equivocada.

Esto es distinto de [[project_manga_canonical_identity]] (`manga_identity.py`), que resuelve "qué obra
es" en MangaDex. Aquí el objetivo es "de dónde bajo": mantener el mangaId de Suwayomi VÁLIDO,
re-resolviéndolo por su `url` de fuente (el slug es estable) o por título cuando el número derive.

Este módulo aporta el BARRIDO de verificación/reparación; la corrección en caliente (al abrir un
manga) vive en `sources.py` (`_source_manga_db` + guard de cruce en `/manga/<id>/chapters`)."""
from pathlib import Path

from api.runtime import manga_dir
from api.roots import glob_series, series_dir, series_up_dir  # resuelven el DISCO de la obra
from api.observability import record_error


def _refs(meta: dict) -> list:
    """Referencias de fuente Suwayomi (numéricas) de una obra: origen top-level y pin. Cada una
    es (clase, sourceId, mangaId, sourceName, url)."""
    out = []
    if str(meta.get("sourceId") or "").isdigit() and meta.get("mangaId"):
        out.append(("origen", str(meta["sourceId"]), int(meta["mangaId"]),
                    meta.get("sourceName"), meta.get("mangaUrl", "")))
    rec = meta.get("recommended_source") or {}
    if str(rec.get("sourceId") or "").isdigit() and rec.get("mangaId"):
        out.append(("pin", str(rec["sourceId"]), int(rec["mangaId"]),
                    rec.get("sourceName"), rec.get("url", "")))
    return out


def verify_source_refs(fix: bool = True) -> list:
    """Barrido de salud de las FUENTES de descarga. Clave: el mangaId numérico de Suwayomi es
    EFÍMERO — se reasigna en cada reindexación, así que el número guardado deriva constantemente a
    otra obra. Eso NO rompe la lectura: el guard de `/manga/<id>/chapters` re-resuelve por url→título
    en cada apertura. Por eso aquí NO alarmamos por "el número cambió"; clasificamos por si la obra
    sigue siendo LOCALIZABLE en su fuente:
      · (omitido)   el id aún apunta a la obra correcta → nada que hacer.
      · 'auto'      el id derivó PERO la obra se re-resuelve por url/título → se auto-repara al abrir.
                    Con `fix`, se refresca el id y se rellena la ancla `url` (para futuros matches EXACTOS).
      · 'roto'      el id derivó y NO hay re-resolución fiable → problema real (revisar a mano).
      · 'no-consultable'  la fuente no responde ahora (caída ≠ cruce).
    Devuelve informe [{title, kind, old, now_title, status, new, fixed}]. Solo 'roto' cuenta como
    problema; 'auto'/'no-consultable' son informativos."""
    from api import sources as S
    import json as _json
    report = []
    for meta_path in glob_series("*/.source_meta.json"):
        folder = meta_path.parent.name
        try:
            meta = _json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception as e:
            record_error("source_identity", e, op="read_meta", folder=folder)
            continue
        want_title = meta.get("title") or folder
        variants = _title_variants_for(folder, want_title, meta)
        for kind, sid, mid, sname, url in _refs(meta):
            db = S._source_manga_db(mid)
            if not db.get("title"):
                report.append({"title": folder, "kind": kind, "old": mid,
                               "status": "no-consultable", "fixed": False})
                continue
            # ¿El id aún apunta a esta obra? Casa contra el título Y sus VARIANTES (romaji/inglés/
            # nativo/sinónimos) — una fuente puede listarla en otro idioma ("Houseki no Kuni" =
            # "Land of the Lustrous") y un match de string simple lo tomaría por cruce falso.
            if any(S._titles_match(db["title"], n) for n in [want_title, *variants]):
                continue
            # El id derivó de verdad. ¿Sigue LOCALIZABLE la obra en su fuente? (url→título/variantes).
            new_id, new_url, status = S._resolve_source_manga_id(sid, want_title, url,
                                                                 strict=True, variants=variants)
            if status == "failed":
                # La BÚSQUEDA reventó (fuente caída/Cloudflare) → NO es "no está" (falló≠no-había).
                report.append({"title": folder, "kind": kind, "old": mid, "now_title": db["title"],
                               "status": "no-consultable", "fixed": False})
                continue
            if not new_id:
                report.append({"title": folder, "kind": kind, "old": mid, "now_title": db["title"],
                               "status": "roto", "new": None, "fixed": False})
                continue
            entry = {"title": folder, "kind": kind, "old": mid, "now_title": db["title"],
                     "status": "auto", "new": new_id, "fixed": False}
            if fix and S._persist_source_meta_id(mid, new_id, want_title, new_url or url):
                entry["fixed"] = True                       # refresca id + rellena ancla url
                S.cache_set("sources_chapters", str(mid), None, 1)
            report.append(entry)
    return report


def _title_variants_for(folder: str, title: str, meta: dict) -> list:
    """Variantes de nombre de la obra (romaji/inglés/nativo/sinónimos) vía AniList, para casar el
    título que la fuente muestre en OTRO idioma. al_id: de `.identity.json` (ya cacheado) o del
    source_meta. Sin al_id, title_variants aún expande por título. Best-effort (nunca lanza)."""
    import json as _json
    al = meta.get("al_id") or meta.get("anilist")
    try:
        p = series_dir(folder) / ".identity.json"
        if not al and p.exists():
            al = (_json.loads(p.read_text(encoding="utf-8")) or {}).get("al_id")
    except Exception:
        pass
    try:
        from api.anilist import title_variants
        return [v for v in (title_variants(title, int(al) if al else None) or []) if v]
    except Exception as e:
        record_error("source_identity", e, op="variants", folder=folder)
        return []
