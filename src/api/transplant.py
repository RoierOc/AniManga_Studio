#!/usr/bin/env python3
"""
Transplant API — "traducción por trasplante" multi-fuente.

Para una serie: descubre en las extensiones de Suwayomi la versión con MEJOR
ARTE (cualquier idioma) y una versión en ESPAÑOL, trasplanta el texto ES sobre
el arte HD (núcleo en `transplant_core`) y escribe el resultado como capítulo
principal en MANGA_DIR. El resultado pasa por reader/upscale/export sin cambios.

Estrategia de coste (nunca se escanea "imagen por imagen" toda la red):
  1. variantes de título (AniList synonyms + título)  -> metadata
  2. descubrimiento de candidatos en Suwayomi          -> metadata, sin imágenes
  3. filtrado fuzzy por título + split arte/ES por idioma
  4. ranking de calidad por MUESTREO (2-3 páginas)     -> decenas de imágenes
  5. caché por serie en `.transplant_meta.json`        -> capas 1-4 una vez
  6. composición por capítulo bajo demanda; se borran los scans tras componer.
"""
from flask import Blueprint, jsonify, request
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from difflib import SequenceMatcher
import json as _json
import shutil
import tempfile
import threading
import time

import numpy as np
import cv2
import requests as http_requests

from api.runtime import MANGA_DIR, UPSCALED_DIR, normalize_chapter, build_task_id, push_sse_event
from api.sources import _gql, SUWAYOMI_URL, SUWAYOMI_BASE, _suwayomi_online
from api.download import _fetch_with_retry, _dl_semaphore, _chapter_file_prefix
from api.anilist import title_variants

transplant_bp = Blueprint("transplant", __name__)

# ── Estado de tareas (mismo patrón que download/upscale) ──────────────────────
transplant_status: dict = {}
_status_lock = threading.Lock()
# flags de cancelación por task_id (espejo de _download_cancel_flags en download.py)
_transplant_cancel: dict = {}


def _chnum(x):
    """Clave de orden numérico de un nº de capítulo ('12.5' -> 12.5; basura -> 0)."""
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0

# Idiomas considerados "español" en Suwayomi (lang codes)
_ES_LANGS = {"es", "es-419", "es-es", "es-la", "es-mx"}
# muestreo de calidad: nº de páginas por capítulo y nº de capítulos a muestrear
_SAMPLE_PAGES = 3
_SAMPLE_CHAPTERS = 2   # uno del principio + uno del medio de la serie (evita sesgo del cap 1)
_SHARP_REF = 500.0     # varianza Laplaciana de referencia (nitidez "buena")
_FUZZY_MIN = 0.60      # ratio mínimo título-candidato vs variante
_RANK_CAP = 15         # máx candidatos a muestrear (acota descargas de calidad)
_SEARCH_TIMEOUT = 7    # s por fuente: una fuente más lenta no vale la espera (era 20s de _gql)
_SEARCH_WORKERS = 64   # concurrencia del fan-out (peticiones I/O-bound vía Suwayomi)
_LATIN_CAP = 2         # sólo romaji+inglés van a TODAS las fuentes; los sinónimos latinos
                       # localizados (ES/DE/VI…) inflaban el barrido x185 sin ganar hits
_SAMPLE_TIMEOUT = 8    # s por página de muestra en el ranking (sin reintentos)


def _set_status(task_id: str, **fields):
    with _status_lock:
        cur = transplant_status.get(task_id, {})
        cur.update(fields)
        transplant_status[task_id] = cur
    push_sse_event("transplant", task_id=task_id, **fields)


def _meta_path(title: str) -> Path:
    return Path(MANGA_DIR) / title / ".transplant_meta.json"


def _read_meta(title: str) -> dict:
    p = _meta_path(title)
    if p.exists():
        try:
            return _json.loads(p.read_text())
        except Exception:
            pass
    return {}


def _write_meta(title: str, meta: dict):
    p = _meta_path(title)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(_json.dumps(meta, ensure_ascii=False, indent=2))


def _norm_key(s: str) -> str:
    return ''.join(ch for ch in (s or "").lower() if ch.isalnum())


# ── Capa 1-3: descubrimiento de candidatos ────────────────────────────────────

def _all_sources() -> list:
    data = _gql("{ sources { nodes { id name lang } } }")
    return [n for n in data["sources"]["nodes"] if n["id"] != "0"]


_SEARCH_Q = """mutation S($source: LongString!, $query: String, $page: Int!) {
  fetchSourceManga(input: { source: $source, type: SEARCH, query: $query, page: $page }) {
    mangas { id title thumbnailUrl }
  }
}"""


def _script(s: str) -> str:
    """Sistema de escritura dominante de un texto, para no buscar un título japonés
    en una fuente que sólo indexa en latín (y viceversa)."""
    for ch in s or "":
        o = ord(ch)
        if 0x3040 <= o <= 0x30FF or 0x4E00 <= o <= 0x9FFF or 0x3400 <= o <= 0x4DBF:
            return "cjk"      # hiragana/katakana/han
        if 0xAC00 <= o <= 0xD7A3:
            return "ko"       # hangul
        if 0x0400 <= o <= 0x04FF:
            return "ru"       # cirílico
    return "latin"


def _variants_for_source(source: dict, by_script: dict) -> list:
    """Subconjunto de variantes que tiene sentido buscar en ESTA fuente, según su
    idioma. Latín a todas (casi todas indexan romaji/inglés); el script no-latino
    sólo a las fuentes de ese idioma. Esto recorta el fan-out ~3x sin perder hits."""
    lang = (source.get("lang") or "").lower()
    # romaji + inglés (los 2 primeros latinos) a TODAS; los sinónimos latinos localizados
    # NO se difunden a las 185 fuentes (un título español sólo ayuda a fuentes españolas y
    # éstas casi siempre indexan también por romaji/inglés).
    out = list(by_script.get("latin", [])[:_LATIN_CAP])
    if lang.startswith(("ja", "zh")):
        out += by_script.get("cjk", [])
    elif lang.startswith("ko"):
        out += by_script.get("ko", [])
    elif lang.startswith("ru"):
        out += by_script.get("ru", [])
    # si una fuente no es de ningún script no-latino conocido, queda sólo con latín
    return out or list(by_script.get("latin", [])) or [source.get("_fallback_q", "")]


def _search_source(source: dict, query: str) -> list:
    """Búsqueda metadata-only en UNA fuente (post directo, timeout corto)."""
    try:
        resp = http_requests.post(
            SUWAYOMI_URL,
            json={"query": _SEARCH_Q, "variables": {"source": source["id"], "query": query, "page": 1}},
            timeout=_SEARCH_TIMEOUT,
        )
        if resp.status_code != 200:
            return []
        data = resp.json()
        mangas = (((data.get("data") or {}).get("fetchSourceManga") or {}).get("mangas")) or []
    except Exception:
        return []
    out = []
    for m in mangas:
        out.append({
            "id": m["id"],
            "title": m["title"],
            "thumbnailUrl": SUWAYOMI_BASE + m["thumbnailUrl"] if m.get("thumbnailUrl") else None,
            "sourceId": source["id"],
            "sourceName": source["name"],
            "sourceLang": source["lang"],
        })
    return out


def _discover_candidates(variants: list, source_ids=None, on_progress=None) -> list:
    """Fan-out metadata-only; fusiona y filtra por título fuzzy. No descarga imágenes.
    `source_ids` (opcional) restringe a un subconjunto de fuentes (palanca de velocidad).
    Las variantes se asignan POR FUENTE según su idioma para recortar el fan-out."""
    sources = _all_sources()
    if source_ids:
        wanted = {str(s) for s in source_ids}
        sources = [s for s in sources if str(s["id"]) in wanted]
    var_keys = [_norm_key(v) for v in variants if v]
    # agrupar variantes por sistema de escritura
    by_script: dict = {}
    for v in variants:
        if v:
            by_script.setdefault(_script(v), []).append(v)
    # tareas (fuente, variante) ya recortadas por idioma
    tasks = []
    for s in sources:
        for v in _variants_for_source(s, by_script):
            if v:
                tasks.append((s, v))
    seen: dict = {}
    done = 0
    total = len(tasks)
    with ThreadPoolExecutor(max_workers=_SEARCH_WORKERS) as pool:
        futs = {pool.submit(_search_source, s, v): (s, v) for (s, v) in tasks}
        for fut in as_completed(futs):
            done += 1
            if on_progress and done % 20 == 0:
                on_progress(done, total)
            for m in fut.result():
                # fuzzy-match contra cualquier variante para descartar falsos positivos
                tkey = _norm_key(m["title"])
                ratio = max((SequenceMatcher(None, tkey, vk).ratio() for vk in var_keys), default=0.0)
                if ratio < _FUZZY_MIN and not any(vk in tkey or tkey in vk for vk in var_keys):
                    continue
                key = (m["sourceId"], m["id"])
                if key not in seen or ratio > seen[key]["match"]:
                    m = dict(m, match=round(ratio, 3))
                    seen[key] = m
    if on_progress:
        on_progress(total, total)
    return list(seen.values())


# ── Capa 4: muestreo de calidad ───────────────────────────────────────────────

def _gql_fast(query: str, variables: dict, timeout: float):
    """GraphQL con timeout corto para el camino de RANKING: una fuente lenta no debe
    colgar la consulta de capítulos/páginas (el _gql normal usa 20s, bien para `run`)."""
    resp = http_requests.post(SUWAYOMI_URL, json={"query": query, "variables": variables}, timeout=timeout)
    resp.raise_for_status()
    d = resp.json()
    if "errors" in d:
        raise RuntimeError(d["errors"])
    return d["data"]


def _chapters_map(manga_id: int, timeout: float = None) -> dict:
    """{chapter_norm: {id, pageCount, number}} para una manga de Suwayomi.
    `timeout` (camino de ranking) usa GraphQL con timeout corto."""
    q1 = "mutation($id: Int!){ fetchChapters(input:{mangaId:$id}){ chapters { id } } }"
    q2 = """
            query($mangaId: Int!) {
              chapters(condition: { mangaId: $mangaId }, orderBy: CHAPTER_NUMBER, orderByType: ASC) {
                nodes { id chapterNumber pageCount }
              }
            }
            """
    try:
        if timeout:
            _gql_fast(q1, {"id": int(manga_id)}, timeout)
            data = _gql_fast(q2, {"mangaId": int(manga_id)}, timeout)
        else:
            _gql(q1, {"id": int(manga_id)})
            data = _gql(q2, {"mangaId": int(manga_id)})
    except Exception:
        return {}
    out = {}
    for c in data["chapters"]["nodes"]:
        key = normalize_chapter(c.get("chapterNumber"))
        if key and key not in out:
            out[key] = {"id": c["id"], "pageCount": c.get("pageCount"), "number": c.get("chapterNumber")}
    return out


def _chapter_page_urls(chapter_id: int, timeout: float = None) -> list:
    q = "mutation($id: Int!){ fetchChapterPages(input:{chapterId:$id}){ pages } }"
    data = _gql_fast(q, {"id": int(chapter_id)}, timeout) if timeout else _gql(q, {"id": int(chapter_id)})
    pages = data["fetchChapterPages"]["pages"]
    return [SUWAYOMI_BASE + p if p.startswith("/") else p for p in pages]


def _score_source(manga_id: int) -> dict | None:
    """Puntúa la calidad de una fuente SIN descargar capítulos enteros: muestrea unas
    páginas de _SAMPLE_CHAPTERS capítulos (uno del principio y uno del medio de la
    serie, para no sesgar por un cap 1 de otro grupo/calidad) y mide resolución +
    nitidez. score = altura_nativa * factor_nitidez (un scan grande pero borroso pierde)."""
    chmap = _chapters_map(manga_id, timeout=_SAMPLE_TIMEOUT)
    if not chmap:
        return None
    ordered = sorted(chmap.values(),
                     key=lambda c: float(c["number"]) if c["number"] is not None else 0)
    with_pages = [c for c in ordered if (c.get("pageCount") or 1) > 0] or ordered
    if not with_pages:
        return None
    # elegir hasta _SAMPLE_CHAPTERS capítulos: el primero con páginas y uno hacia el medio
    picks = [with_pages[0]]
    if _SAMPLE_CHAPTERS > 1 and len(with_pages) > 1:
        picks.append(with_pages[len(with_pages) // 2])
    # juntar URLs de muestra (centro de cada capítulo elegido, evita portada/créditos)
    sample = []
    for ch in picks:
        try:
            urls = _chapter_page_urls(ch["id"], timeout=_SAMPLE_TIMEOUT)
        except Exception:
            continue
        if not urls:
            continue
        mid = len(urls) // 2
        sample += (urls[mid:mid + _SAMPLE_PAGES] or urls[:_SAMPLE_PAGES])
    if not sample:
        return None
    # El muestreo es ligero y se baja TODO EN PARALELO: así una fuente lenta cuelga
    # ~_SAMPLE_TIMEOUT (una página) y no Nº_páginas×timeout en serie. NO usa el semáforo
    # de descargas pesadas (_dl_semaphore, para capítulos enteros).
    def _grab(u):
        try:
            r = http_requests.get(u, timeout=_SAMPLE_TIMEOUT)
            return r.content if (r and r.status_code == 200 and r.content) else None
        except Exception:
            return None
    heights, sharps, bytes_per_px = [], [], []
    with ThreadPoolExecutor(max_workers=len(sample) or 1) as pool:
        for content in pool.map(_grab, sample):
            if not content:
                continue
            img = cv2.imdecode(np.frombuffer(content, np.uint8), cv2.IMREAD_GRAYSCALE)
            if img is None:
                continue
            h, w = img.shape[:2]
            heights.append(h)
            sharps.append(float(cv2.Laplacian(img, cv2.CV_64F).var()))
            bytes_per_px.append(len(content) / max(1, h * w))
    if not heights:
        return None
    height = float(np.median(heights))
    sharp = float(np.median(sharps))
    # factor de nitidez SATURANTE (no se clampa): sharp/(sharp+ref) ∈ (0,1). La
    # resolución manda, pero entre dos scans de la MISMA altura el más nítido SIEMPRE
    # gana (rompe empates), y un upscale borroso de igual tamaño queda penalizado.
    sharp_factor = 0.5 + 0.5 * (sharp / (sharp + _SHARP_REF))   # 0.5..~1.0, estrictamente creciente
    score = height * sharp_factor
    return {
        "height": round(height),
        "sharpness": round(sharp, 1),
        "bytesPerPx": round(float(np.median(bytes_per_px)), 4),
        "samples": len(heights),
        "score": round(score, 1),
    }


def _rank(candidates: list, on_progress=None) -> list:
    """Puntúa cada candidato (muestreo) y devuelve ordenado desc por score."""
    out = []
    done = 0
    with ThreadPoolExecutor(max_workers=10) as pool:
        futs = {pool.submit(_score_source, int(c["id"])): c for c in candidates}
        for fut in as_completed(futs):
            c = futs[fut]
            done += 1
            if on_progress:
                on_progress(done, len(candidates))
            q = fut.result()
            if q:
                out.append(dict(c, quality=q))
    out.sort(key=lambda c: c["quality"]["score"], reverse=True)
    return out


# ── Endpoint: discover ────────────────────────────────────────────────────────

def _run_discover(task_id: str, title: str, al_id, source_ids):
    """Descubre + rankea en un hilo; escribe el progreso y el resultado final en el
    estado (la UI sondea /status). Cachea en `.transplant_meta.json`."""
    try:
        _set_status(task_id, status="discovering", title=title, phase="variants")
        variants = title_variants(title, int(al_id) if al_id else None)
        _set_status(task_id, phase="searching", variants=variants, searched=0, searchTotal=0)

        candidates = _discover_candidates(
            variants, source_ids=source_ids,
            on_progress=lambda d, t: _set_status(task_id, phase="searching", searched=d, searchTotal=t),
        )
        if not candidates:
            _set_status(task_id, status="done", phase="empty", variants=variants,
                        art={"candidates": [], "best": None}, es={"candidates": [], "best": None})
            return

        es_cands = [c for c in candidates if (c.get("sourceLang") or "").lower() in _ES_LANGS]
        # COSTE: el ranking baja 2-3 páginas por candidato. Acotar a los mejores por
        # parecido de título (top _RANK_CAP) + TODOS los ES (subconjunto pequeño).
        by_match = sorted(candidates, key=lambda c: c.get("match", 0), reverse=True)
        to_rank = by_match[:_RANK_CAP]
        seen_keys = {(c["sourceId"], c["id"]) for c in to_rank}
        for c in es_cands:
            if (c["sourceId"], c["id"]) not in seen_keys:
                to_rank.append(c); seen_keys.add((c["sourceId"], c["id"]))

        _set_status(task_id, phase="ranking", rankTotal=len(to_rank), ranked=0)
        art_ranked = _rank(to_rank, on_progress=lambda d, t: _set_status(task_id, phase="ranking", ranked=d, rankTotal=t))
        scored = {(c["sourceId"], c["id"]): c for c in art_ranked}
        es_ranked = [scored[(c["sourceId"], c["id"])] for c in es_cands
                     if (c["sourceId"], c["id"]) in scored]
        es_ranked.sort(key=lambda c: c["quality"]["score"], reverse=True)

        art_best = art_ranked[0] if art_ranked else None
        es_best = es_ranked[0] if es_ranked else None

        meta = _read_meta(title)
        meta.update({
            "title": title, "variants": variants,
            "art": {"sourceId": art_best["sourceId"], "mangaId": art_best["id"],
                    "sourceName": art_best["sourceName"], "sourceLang": art_best["sourceLang"]} if art_best else None,
            "es": {"sourceId": es_best["sourceId"], "mangaId": es_best["id"],
                   "sourceName": es_best["sourceName"], "sourceLang": es_best["sourceLang"]} if es_best else None,
            "discovered_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        })
        _write_meta(title, meta)
        _set_status(task_id, status="done", phase="ranked", variants=variants,
                    art={"candidates": art_ranked[:12], "best": art_best},
                    es={"candidates": es_ranked[:12], "best": es_best})
    except Exception as e:
        _set_status(task_id, status="error", phase="error", error=str(e))


@transplant_bp.route("/discover", methods=["POST"])
def discover():
    """Lanza el descubrimiento en segundo plano y devuelve task_id de inmediato; la
    UI sondea /status?task_id= para ver progreso en vivo y el resultado final.
    Body: {title, anilistId?, sourceIds?[]} — sourceIds restringe el barrido."""
    if not _suwayomi_online():
        return jsonify({"error": "Suwayomi offline"}), 503
    body = request.get_json(silent=True) or {}
    title = (body.get("title") or "").strip()
    al_id = body.get("anilistId")
    source_ids = body.get("sourceIds") or None
    if not title:
        return jsonify({"error": "title required"}), 400

    task_id = build_task_id(title, "discover", "transplant")
    _set_status(task_id, status="discovering", title=title, phase="start")
    threading.Thread(target=_run_discover, args=(task_id, title, al_id, source_ids), daemon=True).start()
    return jsonify({"task_id": task_id, "title": title})


@transplant_bp.route("/confirm", methods=["POST"])
def confirm():
    """Fija (override) las fuentes elegidas de arte y ES en `.transplant_meta.json`."""
    body = request.get_json(silent=True) or {}
    title = (body.get("title") or "").strip()
    if not title:
        return jsonify({"error": "title required"}), 400
    meta = _read_meta(title)
    if body.get("art"):
        meta["art"] = body["art"]
    if body.get("es"):
        meta["es"] = body["es"]
    meta["title"] = title
    _write_meta(title, meta)
    return jsonify({"ok": True, "art": meta.get("art"), "es": meta.get("es")})


@transplant_bp.route("/meta/<path:title>", methods=["GET"])
def get_meta(title):
    return jsonify(_read_meta(title.strip()))


@transplant_bp.route("/chapters/<path:title>", methods=["GET"])
def list_chapters(title):
    """Capítulos traducibles (disponibles en AMBAS fuentes elegidas) con su estado
    (hecho/falló/pendiente). Para la lista de la pestaña Traducir."""
    title = title.strip()
    meta = _read_meta(title)
    art, es = meta.get("art"), meta.get("es")
    if not art or not es:
        return jsonify({"chapters": [], "needsDiscover": True})
    if not _suwayomi_online():
        return jsonify({"error": "Suwayomi offline"}), 503
    try:
        art_map = _chapters_map(int(art["mangaId"]), timeout=_SAMPLE_TIMEOUT)
        es_map = _chapters_map(int(es["mangaId"]), timeout=_SAMPLE_TIMEOUT)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    translated = set(meta.get("translated", []))
    failed = set(meta.get("failed", []))
    common = sorted(set(art_map) & set(es_map), key=_chnum)
    chapters = [{
        "chapter": chn,
        "status": ("done" if chn in translated else "failed" if chn in failed else "pending"),
    } for chn in common]
    return jsonify({"chapters": chapters, "art": art, "es": es,
                    "translated": sorted(translated, key=_chnum)})


@transplant_bp.route("/preview/<path:title>/<chapter>", methods=["GET"])
def preview_chapter(title, chapter):
    """URLs de páginas de muestra de un capítulo (para que el usuario revise la
    calidad por sí mismo). `which=art|es` (por defecto art = lo que se leerá)."""
    title = title.strip()
    chn = normalize_chapter(chapter)
    which = (request.args.get("which") or "art").lower()
    meta = _read_meta(title)
    src = meta.get("es" if which == "es" else "art")
    if not src:
        return jsonify({"error": "no source chosen"}), 400
    if not _suwayomi_online():
        return jsonify({"error": "Suwayomi offline"}), 503
    try:
        cmap = _chapters_map(int(src["mangaId"]), timeout=_SAMPLE_TIMEOUT)
        ch = cmap.get(chn)
        if not ch:
            return jsonify({"pages": [], "missing": True})
        urls = _chapter_page_urls(ch["id"], timeout=_SAMPLE_TIMEOUT)
    except Exception as ex:
        return jsonify({"error": str(ex)}), 500
    return jsonify({"pages": urls, "count": len(urls),
                    "source": {"name": src.get("sourceName"), "lang": src.get("sourceLang")}})


@transplant_bp.route("/status", methods=["GET"])
def get_status():
    tid = request.args.get("task_id")
    with _status_lock:
        if tid:
            return jsonify(transplant_status.get(tid, {}))
        return jsonify(transplant_status)


# ── Endpoint: run (descarga EN+ES, compone, escribe, limpia) ──────────────────

def _download_chapter_to(tmp: Path, urls: list, prefix: str) -> int:
    tmp.mkdir(parents=True, exist_ok=True)
    n = 0
    for i, url in enumerate(urls, 1):
        r = _fetch_with_retry(url, timeout=30)
        if not r or r.status_code != 200 or not r.content:
            continue
        ext = "png" if "png" in r.headers.get("Content-Type", "") else "jpg"
        (tmp / f"{prefix}_{i:03d}.{ext}").write_bytes(r.content)
        n += 1
    return n


_IMG_EXT = ("jpg", "jpeg", "png", "webp")


def _replace_chapter_in_place(out_dir: Path, prefix: str, stage_dir: Path, page_names: list):
    """REEMPLAZO EN SITIO atómico-por-capítulo: borra las páginas existentes del
    capítulo (cualquier extensión = el idioma original) y mueve las traducidas del
    staging. Solo se llama cuando el capítulo se compuso COMPLETO."""
    for ext in _IMG_EXT:
        for old in out_dir.glob(f"{prefix}_*.{ext}"):
            old.unlink(missing_ok=True)
    for name in page_names:
        src = stage_dir / name
        if src.exists():
            shutil.move(str(src), str(out_dir / name))


def _invalidate_upscaled(title: str, prefix: str):
    """El arte cambió → el capítulo escalado quedó obsoleto: se borra para que se
    re-escale. Cubre las dos convenciones de nombre del mirror upscaled."""
    for folder in {Path(UPSCALED_DIR) / title, Path(UPSCALED_DIR) / title.replace("_", " ")}:
        if folder.exists():
            for ext in _IMG_EXT:
                for f in folder.glob(f"{prefix}_*.{ext}"):
                    f.unlink(missing_ok=True)


def _persist_run_meta(title: str, translated: set, failed: set, task_id=None, status=None):
    meta = _read_meta(title)
    tr = set(meta.get("translated", [])) | set(translated)
    fa = (set(meta.get("failed", [])) | set(failed)) - tr
    meta["translated"] = sorted(tr, key=_chnum)
    meta["failed"] = sorted(fa, key=_chnum)
    if status:
        meta["last_run"] = {"task_id": task_id, "status": status,
                            "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    _write_meta(title, meta)


def _run_chapters(task_id: str, title: str, chapters: list, art: dict, es: dict):
    from transplant_core import transplant_chapter
    _transplant_cancel[task_id] = False
    art_map = _chapters_map(int(art["mangaId"]))
    es_map = _chapters_map(int(es["mangaId"]))
    out_dir = Path(MANGA_DIR) / title
    out_dir.mkdir(parents=True, exist_ok=True)
    done_ch, translated, failed = [], set(), set()
    total = len(chapters)
    cancelled = False

    def _cancelled():
        return bool(_transplant_cancel.get(task_id))

    for ci, ch in enumerate(chapters, 1):
        if _cancelled():
            cancelled = True
            break
        chn = normalize_chapter(ch)
        a_ch, e_ch = art_map.get(chn), es_map.get(chn)
        if not a_ch or not e_ch:
            failed.add(chn)
            _set_status(task_id, phase="skip", chapter=chn,
                        note=f"falta capítulo en {'arte' if not a_ch else 'ES'}",
                        chapterDone=ci, chapterTotal=total)
            _persist_run_meta(title, translated, failed)
            continue
        tmp = Path(tempfile.mkdtemp(prefix="transplant_"))
        try:
            en_dir = tmp / "en"; es_dir = tmp / "es"; stage = tmp / "out"
            _set_status(task_id, phase="download", chapter=chn, chapterDone=ci, chapterTotal=total)
            with _dl_semaphore:
                _download_chapter_to(en_dir, _chapter_page_urls(a_ch["id"]), "en")
                if _cancelled():
                    cancelled = True
                else:
                    _download_chapter_to(es_dir, _chapter_page_urls(e_ch["id"]), "es")
            if cancelled:
                continue
            prefix = _chapter_file_prefix(chn)
            _set_status(task_id, phase="compose", chapter=chn, chapterDone=ci, chapterTotal=total)
            res = transplant_chapter(
                es_dir, en_dir, stage, file_prefix=prefix,
                progress_cb=lambda d, t, note: _set_status(
                    task_id, phase="compose", chapter=chn, pageDone=d, pageTotal=t,
                    note=note, chapterDone=ci, chapterTotal=total),
                should_cancel=_cancelled,
            )
            if res.get("cancelled"):
                cancelled = True            # staging se descarta en finally -> sin capítulo a medias
            elif res.get("pages"):
                _replace_chapter_in_place(out_dir, prefix, stage, res["pages"])
                _invalidate_upscaled(title, prefix)
                translated.add(chn)
                done_ch.append({"chapter": chn, **{k: res[k] for k in ("trans", "fallback") if k in res}})
            else:
                failed.add(chn)
        except Exception as e:
            failed.add(chn)
            _set_status(task_id, phase="error", chapter=chn, error=str(e))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)   # descarta staging (resultado ya movido, o se cancela)
        _persist_run_meta(title, translated, failed)
        if cancelled:
            break

    _transplant_cancel.pop(task_id, None)
    status = "cancelled" if cancelled else "done"
    _persist_run_meta(title, translated, failed, task_id=task_id, status=status)
    _set_status(task_id, status=status, phase=("cancelled" if cancelled else "complete"),
                chapters=done_ch, failed=sorted(failed, key=_chnum))


@transplant_bp.route("/cancel", methods=["POST"])
def cancel():
    """Pide cancelar un run de traducción en curso. Corta a mitad de capítulo (el
    staging se descarta, no queda capítulo a medias)."""
    body = request.get_json(silent=True) or {}
    tid = (body.get("task_id") or "").strip()
    if not tid:
        return jsonify({"error": "task_id required"}), 400
    _transplant_cancel[tid] = True
    _set_status(tid, status="cancelling", phase="cancelling")
    return jsonify({"ok": True, "task_id": tid})


@transplant_bp.route("/run", methods=["POST"])
def run():
    """Trasplanta uno o más capítulos usando las fuentes ya elegidas (discover/confirm)."""
    if not _suwayomi_online():
        return jsonify({"error": "Suwayomi offline"}), 503
    body = request.get_json(silent=True) or {}
    title = (body.get("title") or "").strip()
    chapters = body.get("chapters")
    if not title or not chapters:
        return jsonify({"error": "title and chapters required"}), 400

    meta = _read_meta(title)
    art, es = meta.get("art"), meta.get("es")
    if not art or not es:
        return jsonify({"error": "no sources chosen; call /discover or /confirm first"}), 400

    if chapters == "all":
        # 'all' = capítulos disponibles en AMBAS fuentes
        art_map = _chapters_map(int(art["mangaId"]))
        es_map = _chapters_map(int(es["mangaId"]))
        chapters = sorted(set(art_map) & set(es_map),
                          key=lambda x: float(x) if x.replace('.', '', 1).isdigit() else 0)
    if not isinstance(chapters, list) or not chapters:
        return jsonify({"error": "no chapters to process"}), 400

    task_id = build_task_id(title, "run", "transplant")
    _set_status(task_id, status="running", title=title, phase="start",
                chapterTotal=len(chapters), art=art, es=es)
    threading.Thread(target=_run_chapters, args=(task_id, title, chapters, art, es), daemon=True).start()
    return jsonify({"task_id": task_id, "title": title, "chapters": chapters})
