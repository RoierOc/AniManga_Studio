"""Identidad CANÓNICA de una obra en MangaDex — separada de la fuente de descarga.

Problema que resuelve: hasta ahora "qué obra es esta en MangaDex" se leía del `.source_meta.json`,
que en realidad guarda la FUENTE DE DESCARGA (la versión/calidad elegida, incluso por-capítulo vía
Cobertura). Eso confunde *de dónde bajo* con *qué obra es*: si un capítulo se bajó de una versión
"MangaDex" mal emparejada, su uuid quedaba como identidad y la obra X mostraba capítulos de la obra
Y (bug real con Amayo no Tsuki ← uuid de Momose Akira). Ver [[project_md_updates_modal]].

Diseño: cada carpeta de manga tiene su propia identidad en `.identity.json`, resuelta por TÍTULO y
**verificada** (nunca un match débil): match de alias exacto (romaji/inglés/nativo/sinónimos vía
AniList, reusa `mangadex.resolve_manga_by_title`) y/o cruce del AniList-id contra `links.al` de
MangaDex (a prueba de balas). Si no se puede verificar → sin identidad (la obra no muestra
novedades, jamás se asocia la obra equivocada). El "Fijar" y la Cobertura por-capítulo quedan
intactos: siguen siendo SOLO fuente/calidad, nunca identidad.
"""
import json as _json
import threading
import time
from pathlib import Path

import requests as _http

from api.runtime import manga_dir
from api.mangadex import _canon, _all_titles, resolve_manga_by_title
from api.observability import record_error

_MD_API = "https://api.mangadex.org"
_IDENTITY_FILE = ".identity.json"
_VERIFIED_TTL = 30 * 24 * 3600   # una identidad verificada apenas cambia: re-resolver cada 30 días
_NEG_TTL = 24 * 3600             # sin match: reintentar en 24 h (puede aparecer al_id / editarse MangaDex)

# Marca de "falló la RED durante esta resolución" (por-hilo). Regla de oro del proyecto:
# "falló" ≠ "no había". Si la verificación no pudo hablar con MangaDex, NO debemos cachear
# "esta obra no tiene identidad" (un parpadeo de red apagaría los avisos de la obra 24 h);
# devolvemos None SIN persistir el negativo, para reintentar en la próxima apertura.
_net = threading.local()


def _net_reset():
    _net.failed = False


def _net_mark():
    _net.failed = True


def _net_failed() -> bool:
    return getattr(_net, "failed", False)


def _identity_path(title: str) -> Path:
    return Path(manga_dir()) / title / _IDENTITY_FILE


def _read_identity(title: str) -> dict:
    p = _identity_path(title)
    if p.exists():
        try:
            return _json.loads(p.read_text(encoding="utf-8"))
        except Exception as exc:
            # Fichero presente pero CORRUPTO: no es "no había", es un fallo → visible (se
            # re-resolverá, pero conviene saber que la caché de identidad se dañó).
            record_error("manga_identity", exc, op="read_identity", title=title)
    return {}


def _write_identity(title: str, data: dict):
    p = _identity_path(title)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(_json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _as_int(x):
    try:
        return int(str(x).strip())
    except (TypeError, ValueError):
        return None


def _fetch_attrs(uuid: str, title: str = "") -> dict | None:
    """Atributos de una obra MangaDex por uuid (para VERIFICAR una pista)."""
    try:
        r = _http.get(f"{_MD_API}/manga/{uuid}", timeout=15)
        if r.status_code == 200:
            return r.json().get("data", {}).get("attributes") or {}
        # 404 = ese uuid ya no existe (legítimo "no está"); otro código = anomalía visible.
        if r.status_code != 404:
            _net_mark()
            record_error("manga_identity", f"HTTP {r.status_code} verificando uuid",
                         op="fetch_attrs", uuid=uuid, title=title)
    except Exception as exc:
        # Red caída: NO es "el uuid no es esta obra"; marca fallo para no cachear un negativo.
        _net_mark()
        record_error("manga_identity", exc, op="fetch_attrs", uuid=uuid, title=title)
    return None


def _title_canon_set(title: str, al_id) -> set:
    """Conjunto canónico de todas las variantes de nombre de la obra local (para casar títulos)."""
    try:
        from api.anilist import title_variants
        variants = title_variants(title, _as_int(al_id)) or [title]
    except Exception:
        variants = [title]
    return {_canon(v) for v in variants if v}


def _verify_uuid(uuid: str, title: str, al_id) -> tuple[bool, int | None]:
    """¿El uuid dado ES realmente esta obra? Verificado si su `links.al` == al_id local, o si
    alguno de sus títulos casa (canónico) con las variantes del título local. Devuelve
    (verificado, al_id_de_mangadex)."""
    attrs = _fetch_attrs(uuid, title)
    if not attrs:
        return (False, None)
    md_al = _as_int((attrs.get("links") or {}).get("al"))
    want_al = _as_int(al_id)
    if want_al and md_al and want_al == md_al:
        return (True, md_al)                 # cruce por AniList-id: a prueba de balas
    if want_al and md_al and want_al != md_al:
        return (False, md_al)                # el uuid es de OTRA obra (distinto al) → rechazar
    canon = _title_canon_set(title, al_id)
    if canon and any(_canon(t) in canon for t in _all_titles(attrs)):
        return (True, md_al)                 # match de alias exacto de título
    return (False, md_al)


def resolve_identity(title: str, al_id=None, uuid_hint: str = "", refresh: bool = False) -> dict | None:
    """Identidad canónica MangaDex de una obra: {md_uuid, al_id, method}, o None si no se puede
    VERIFICAR (nunca adivina → nunca asocia la obra equivocada). Cacheada en `.identity.json`.

    al_id / uuid_hint son pistas opcionales (del propio manga: su AniList-id, o un uuid conocido);
    si faltan se completan desde source_meta/biblioteca. La pista uuid SOLO se acepta si se verifica
    (así el pin equivocado no contamina la identidad)."""
    al_id = _as_int(al_id)
    uuid_hint = str(uuid_hint or "").split("@@")[0]
    # Completa pistas que falten desde los datos propios de la obra (no fuente de descarga).
    if al_id is None or not uuid_hint:
        h_al, h_uuid = _hints_for(title)
        al_id = al_id if al_id is not None else h_al
        uuid_hint = uuid_hint or h_uuid

    cached = _read_identity(title)
    if cached and not refresh:
        age = time.time() - (cached.get("resolved_at") or 0)
        if cached.get("md_uuid") and age < _VERIFIED_TTL:
            return cached
        if not cached.get("md_uuid") and age < _NEG_TTL:
            return None    # "no verificable" reciente: no re-machacar la red en cada apertura

    md_uuid = None
    md_al = None
    method = None
    _net_reset()   # empieza limpio: sabremos si un fallo de RED contaminó esta resolución

    # 1) Pista uuid (si la hay y VERIFICA) — evita una búsqueda.
    if uuid_hint:
        ok, md_al = _verify_uuid(uuid_hint, title, al_id)
        if ok:
            md_uuid, method = uuid_hint, ("hint+al" if (al_id and md_al == al_id) else "hint")

    # 2) Resolver por título con match de alias EXACTO (fuzzy=False → None si no hay match fiable).
    if not md_uuid:
        try:
            r = resolve_manga_by_title(title, al_id, fuzzy=False)
        except Exception as exc:
            r = None
            _net_mark()   # la búsqueda reventó (red/API) → no es "no hay obra"
            record_error("manga_identity", exc, op="resolve_by_title", title=title)
        if r and r.get("id"):
            cand_al = _as_int(r.get("al_id"))
            if al_id and cand_al and al_id != cand_al:
                r = None                      # el alias casó pero el AniList-id NO → obra distinta
            else:
                md_uuid = r["id"]
                md_al = cand_al
                method = "alias+al" if (al_id and cand_al == al_id) else "alias"

    # "falló" ≠ "no había": si no resolvimos PERO hubo un fallo de red, NO cacheamos el negativo
    # (cachearlo apagaría los avisos de la obra durante _NEG_TTL por un parpadeo de red). Devolvemos
    # None sin escribir → la próxima apertura reintenta. Solo un "no hay match" LIMPIO se persiste.
    if not md_uuid and _net_failed():
        return None

    result = {
        "md_uuid": md_uuid,
        "al_id": al_id or md_al,
        "method": method,
        "resolved_at": time.time(),
    }
    _write_identity(title, result)
    return result if md_uuid else None


def _hints_for(title: str) -> tuple[int | None, str]:
    """Pistas de identidad PROPIAS de la obra (no fuente de descarga): su AniList-id y un uuid
    conocido. Fuentes: `.source_meta.json` (al_id; uuid solo si es una fuente __mangadex__) y la
    entrada de la biblioteca (`local_library.json`: al_id, y su `id` si es un uuid de MangaDex)."""
    al = None
    uuid = ""
    try:
        from api.transplant import _read_source_meta
        meta = _read_source_meta(title) or {}
        al = _as_int(meta.get("al_id") or meta.get("anilist"))
        if str(meta.get("sourceId")) == "__mangadex__" and meta.get("mangaId"):
            uuid = str(meta["mangaId"]).split("@@")[0]
    except Exception:
        pass
    lib_al, lib_uuid = _library_hint(title)
    al = al if al is not None else lib_al
    uuid = uuid or lib_uuid
    return (al, uuid)


_lib_cache = {"ts": 0, "map": {}}
_LIB_TTL = 60


def _library_hint(title: str) -> tuple[int | None, str]:
    """al_id y uuid (si `id` es un UUID de MangaDex) de la entrada de biblioteca por título."""
    now = time.time()
    if now - _lib_cache["ts"] > _LIB_TTL:
        m = {}
        try:
            p = Path(manga_dir()) / "local_library.json"
            data = _json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}

            def walk(o):
                if isinstance(o, dict):
                    t = str(o.get("title") or o.get("name") or "")
                    if t:
                        uid = str(o.get("id") or "")
                        is_uuid = len(uid) == 36 and uid.count("-") == 4
                        m[_canon(t)] = (_as_int(o.get("al_id") or o.get("anilist")),
                                        uid if is_uuid else "")
                    for v in o.values():
                        walk(v)
                elif isinstance(o, list):
                    for v in o:
                        walk(v)
            walk(data)
        except Exception:
            pass
        _lib_cache["ts"], _lib_cache["map"] = now, m
    return _lib_cache["map"].get(_canon(title), (None, ""))


def verify_library(fix_source_meta: bool = True) -> list:
    """Barrido de verificación: recorre las obras, resuelve la identidad canónica VERIFICADA y la
    cachea en `.identity.json`. Si `fix_source_meta`, corrige además el `.source_meta.json` cuando
    su fuente de descarga es __mangadex__ pero apunta a un uuid DISTINTO del canónico (cruce: eso
    también rompería leer/descargar de esa fuente). Nunca toca fuentes que no sean MangaDex.
    Devuelve un informe [{title, old, new, verified, fixed_source_meta, method}]."""
    from api.transplant import _read_source_meta, _write_source_meta
    root = Path(manga_dir())
    report = []
    if not root.exists():
        return report
    for d in sorted(root.iterdir()):
        if not d.is_dir() or d.name.startswith("."):   # salta .cache/.trash y demás no-obras
            continue
        title = d.name
        meta = _read_source_meta(title)
        old_pin = ""
        if str(meta.get("sourceId")) == "__mangadex__" and meta.get("mangaId"):
            old_pin = str(meta["mangaId"]).split("@@")[0]
        ident = resolve_identity(title, refresh=True)
        new_uuid = ident.get("md_uuid") if ident else None
        entry = {"title": title, "old": old_pin or None, "new": new_uuid,
                 "verified": bool(new_uuid), "method": ident.get("method") if ident else None,
                 "fixed_source_meta": False}
        # Corrige el pin __mangadex__ SOLO si está cruzado (uuid distinto del canónico verificado).
        if fix_source_meta and new_uuid and old_pin and old_pin != new_uuid:
            lang = ""
            if "@@" in str(meta.get("mangaId", "")):
                lang = str(meta["mangaId"]).split("@@", 1)[1]
            meta["mangaId"] = f"{new_uuid}@@{lang}" if lang else new_uuid
            meta.pop("md_updates_seen", None)   # baseline sembrado sobre la obra equivocada
            _write_source_meta(title, meta)
            entry["fixed_source_meta"] = True
        report.append(entry)
    return report
