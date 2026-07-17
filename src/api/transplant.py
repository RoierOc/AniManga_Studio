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
from urllib.parse import quote
import json as _json
import os
import re
import shutil
import sys
import tempfile
import threading
import time
import uuid

import numpy as np
import cv2
from api.resilient_http import http as http_requests  # retry + backoff + per-host rate limiting

from api.runtime import (manga_dir, upscaled_dir, QA_DIR, DATA_ROOT, normalize_chapter, build_task_id,
                         push_sse_event, cache_get, cache_set, cache_invalidate)
from api.sources import _gql, SUWAYOMI_URL, SUWAYOMI_BASE, ensure_suwayomi
from api.download import _fetch_with_retry, _dl_semaphore, _chapter_file_prefix
from api.anilist import title_variants
from api import versions_db

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
_SAMPLE_CHAPTERS = 2   # (legado) usado por la comparación A|B; el ranking usa _SCORE_CHAPTERS
_SAMPLE_START = 8      # arrancar el muestreo en una página PROFUNDA (~10): las primeras suelen
                       # ser portada/créditos del scan y no sirven para comparar calidad
_COLOR_MARGIN = 3      # páginas extra de margen: algunas profundas pueden seguir siendo a color
# Ranking de calidad EXHAUSTIVO (decisión del usuario 2026-06-27): no basta con inicio+medio,
# una fuente puede tener un cap 1 horrible y ganar por la fuerza de un cap intermedio bueno.
# Se muestrean capítulos DISTRIBUIDOS por toda la serie (incl. los más adelantados) y se
# puntúa POR capítulo, penalizando la IRREGULARIDAD (mezcla mediana + peor capítulo).
_SCORE_CHAPTERS = 8                              # capítulos a muestrear, repartidos por la serie
_SCORE_FRACS = (0.0, 0.14, 0.28, 0.43, 0.57, 0.71, 0.86, 0.97)  # inicio … tramo final
_SCORE_PAGES = 4                                # páginas B/N a medir por capítulo (exhaustivo)
_WORST_WEIGHT = 0.45    # peso del PEOR capítulo frente a la mediana de capítulos (irregularidad)
_COLOR_DIFF = 15       # umbral de diferencia entre canales (mismo criterio que export/upscale)
_COLOR_FRAC = 0.10     # fracción de píxeles a color → página a color (portada/créditos)
_SHARP_REF = 500.0     # varianza Laplaciana de referencia (nitidez "buena")
_BPP_REF   = 0.10      # bytes/px de referencia (calidad de compresión "buena")
_FUZZY_MIN = 0.60      # ratio mínimo título-candidato vs variante (descubrimiento/Versiones)
_RANK_CAP = 15         # máx candidatos a muestrear (acota descargas de calidad)
_RANK_HARD_CAP = 40    # tope duro de seguridad al rankear por match de título (coste de páginas)
# Cobertura/completado/actualizaciones (Capítulos multi-fuente): a diferencia de Versiones
# (que muestra TODO lo que pasa _FUZZY_MIN y deja que el usuario juzgue con "Leer muestra"),
# aquí el usuario pidió explícitamente un filtro más estricto — así una fuente con match de
# título bajo (probable obra distinta, típicamente con un nº de capítulos absurdo) no le
# llena la pestaña Capítulos de ruido. Instrucción del usuario (2026-07-10): 85%.
_COVERAGE_MATCH_MIN = 0.85
_SEARCH_TIMEOUT = 7    # s por fuente: una fuente más lenta no vale la espera (era 20s de _gql)
_SEARCH_WORKERS = 64   # concurrencia del fan-out (peticiones I/O-bound vía Suwayomi)
_LATIN_CAP = 2         # sólo romaji+inglés van a TODAS las fuentes; los sinónimos latinos
                       # localizados (ES/DE/VI…) inflaban el barrido x185 sin ganar hits
_SAMPLE_TIMEOUT = 8    # s por página de muestra en el ranking (sin reintentos)


def _set_status(task_id: str, **fields):
    with _status_lock:
        cur = transplant_status.get(task_id, {})
        cur.update(fields)
        cur["_ts"] = time.time()
        # Stamp completion time once, on entering a terminal state — feeds Activity history.
        if cur.get("status") in ("done", "cancelled", "error") and not cur.get("ended_at"):
            cur["ended_at"] = time.time()
        transplant_status[task_id] = cur
        # RAM acotada: purga tareas TERMINADAS (done/error) de hace > 30 min. Las activas
        # nunca se tocan. Evita que el dict de estado crezca sin tope en un proceso de días.
        if len(transplant_status) > 40:
            cutoff = time.time() - 1800
            for tid in [t for t, s in transplant_status.items()
                        if s.get("status") in ("done", "error") and s.get("_ts", 0) < cutoff]:
                transplant_status.pop(tid, None)
    push_sse_event("transplant", task_id=task_id, **fields)


# Kinds de tarea-trabajo que el Centro de Actividad global vigila: la traducción (run) y la
# descarga de versión (dlversion). Los pasos cortos discover/versions son de ranking y solo
# importan dentro del modal, así que NO se exponen en el snapshot agregado. El sufijo viene de
# build_task_id(title, "run"|"dlversion", "transplant") → "..._transplant_chrun"/"...chdlversion".
_CENTER_TASK_SUFFIXES = ("_transplant_chrun", "_transplant_chdlversion")


def get_transplant_tasks() -> dict:
    """Estado de las tareas de traducción/descarga-de-versión para el snapshot SSE agregado.
    Espeja cómo download/upscale exponen sus dicts de estado (status.py los junta en un solo
    payload). Incluye también las terminales recientes —igual que downloads— para que el
    frontend detecte la transición a done/error UNA vez (refresco de capítulos + historial);
    el purgado de >30 min de `_set_status` evita que crezcan sin tope."""
    with _status_lock:
        return {
            tid: {k: v for k, v in st.items() if k != "_ts"}
            for tid, st in transplant_status.items()
            if tid.endswith(_CENTER_TASK_SUFFIXES)
        }


def _meta_path(title: str) -> Path:
    return Path(manga_dir()) / title / ".transplant_meta.json"


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


def _search_source(source: dict, query: str):
    """Búsqueda metadata-only en UNA fuente (post directo, timeout corto).

    Devuelve **None** si la fuente no se pudo consultar (timeout, HTTP != 200, Cloudflare), frente
    a **[]** = "respondió y no tiene nada que casé". Quien barre necesita esa diferencia: si un
    puñado de fuentes falla, el barrido NO vio todo lo que hay y no puede cachearse como si sí."""
    try:
        resp = http_requests.post(
            SUWAYOMI_URL,
            json={"query": _SEARCH_Q, "variables": {"source": source["id"], "query": query, "page": 1}},
            timeout=_SEARCH_TIMEOUT,
            retries=1,  # ranking path: a slow/failing source must fail fast, not retry
        )
        if resp.status_code != 200:
            return None
        data = resp.json()
        mangas = (((data.get("data") or {}).get("fetchSourceManga") or {}).get("mangas")) or []
    except Exception:
        return None
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


def _discover_candidates(variants: list, source_ids=None, on_progress=None, stats=None) -> list:
    """Fan-out metadata-only; fusiona y filtra por título fuzzy. No descarga imágenes.
    `source_ids` (opcional) restringe a un subconjunto de fuentes (palanca de velocidad).
    Las variantes se asignan POR FUENTE según su idioma para recortar el fan-out.

    `stats` (dict opcional, out-param) recibe {sources, tasks, failed, listing_error}. Sin él este
    barrido no puede decir si vio TODO o sólo un trozo, y un trozo cacheado es indistinguible de la
    verdad. MEDIDO: recién reiniciada Suwayomi, `_all_sources()` agota su timeout mientras carga las
    extensiones — el barrido salió con 6 fuentes en vez de 72 y NADIE falló: sólo se miró una lista
    corta. Por eso el problema no se detecta contando excepciones, hay que contar el DENOMINADOR."""
    st = stats if stats is not None else {}
    st.update({"sources": 0, "tasks": 0, "failed": 0, "listing_error": None})
    try:
        sources = _all_sources()
    except Exception as e:
        # Sin lista de fuentes no hay barrido posible. Antes esto subía como excepción y el
        # llamante lo convertía en `candidates = []` → "este manga no está en ninguna fuente".
        st["listing_error"] = f"{type(e).__name__}: {str(e)[:70]}"
        print(f"[transplant] no se pudo LISTAR las fuentes ({st['listing_error']}) — "
              f"barrido imposible; esto NO significa 'no hay fuentes'", file=sys.stderr, flush=True)
        raise
    st["sources"] = len(sources)
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
    st["tasks"] = total
    with ThreadPoolExecutor(max_workers=_SEARCH_WORKERS) as pool:
        futs = {pool.submit(_search_source, s, v): (s, v) for (s, v) in tasks}
        for fut in as_completed(futs):
            done += 1
            if on_progress and done % 20 == 0:
                on_progress(done, total)
            found = fut.result()
            if found is None:            # la fuente no respondió: no es "no tiene nada"
                st["failed"] += 1
                continue
            for m in found:
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
    resp = http_requests.post(SUWAYOMI_URL, json={"query": query, "variables": variables},
                              timeout=timeout, retries=1)  # fast ranking path: no retry latency
    resp.raise_for_status()
    d = resp.json()
    if "errors" in d:
        raise RuntimeError(d["errors"])
    return d["data"]


def _chapters_map(manga_id: int, timeout: float = None, strict: bool = False) -> dict:
    """{chapter_norm: {id, pageCount, number}} para una manga de Suwayomi.
    `timeout` (camino de ranking) usa GraphQL con timeout corto.

    `strict=True` PROPAGA el error de lectura en vez de devolver {}. Lo necesita quien tenga que
    distinguir "la fuente no tiene capítulos" de "no pude preguntárselo" — sobre todo si va a
    CACHEAR la respuesta. Por defecto False para no cambiar el comportamiento de los 13 llamantes
    que ya tratan {} como 'nada usable'."""
    q1 = "mutation($id: Int!){ fetchChapters(input:{mangaId:$id}){ chapters { id } } }"
    q2 = """
            query($mangaId: Int!) {
              chapters(condition: { mangaId: $mangaId }, orderBy: CHAPTER_NUMBER, orderByType: ASC) {
                nodes { id chapterNumber pageCount }
              }
            }
            """
    # q1 REFRESCA desde la web de la fuente; q2 lee la BD LOCAL de Suwayomi. El refresco es
    # best-effort a propósito: si la fuente está tras Cloudflare ("Cloudflare bypass currently
    # disabled"), caída o lenta, q1 revienta — pero los capítulos ya sincronizados siguen en local
    # y q2 los devuelve igual. Antes un fallo de q1 se llevaba por delante a q2 y la función
    # devolvía {}, que el llamante lee como "esa fuente NO tiene el capítulo". MEDIDO: Mangas.in
    # daba "no tiene el cap 1" con sus 29 capítulos intactos en la BD local. Es "falló ≠ no había"
    # otra vez: un fallo de RED no puede disfrazarse de ausencia de contenido.
    try:
        try:
            _gql_fast(q1, {"id": int(manga_id)}, timeout) if timeout else _gql(q1, {"id": int(manga_id)})
        except Exception as e:
            print(f"[transplant] refresco de capítulos falló para manga {manga_id} "
                  f"({type(e).__name__}: {str(e)[:80]}) — sigo con lo sincronizado en local",
                  file=sys.stderr, flush=True)
        data = _gql_fast(q2, {"mangaId": int(manga_id)}, timeout) if timeout else _gql(q2, {"mangaId": int(manga_id)})
    except Exception as e:
        print(f"[transplant] no se pudo LEER la lista de capítulos de manga {manga_id} "
              f"({type(e).__name__}: {str(e)[:80]})"
              f"{'' if strict else ' — devuelvo vacío, que NO significa no tiene'}",
              file=sys.stderr, flush=True)
        if strict:
            raise
        return {}
    out = {}
    for c in data["chapters"]["nodes"]:
        key = normalize_chapter(c.get("chapterNumber"))
        if key and key not in out:
            out[key] = {"id": c["id"], "pageCount": c.get("pageCount"), "number": c.get("chapterNumber")}
    return out


_LOCAL_CH_RE = re.compile(r'^(ch\d+(?:\.\d+)?)_')


def _local_chapter_files(title: str) -> dict:
    """{chapter_norm: [Path,...]} páginas locales agrupadas por capítulo — para mangas
    importados (CBZ/CBR) donde el ARTE es el contenido local, no una fuente de Suwayomi."""
    folder = Path(manga_dir()) / title
    out: dict = {}
    if not folder.is_dir():
        return out
    for f in sorted(folder.iterdir()):
        if not f.is_file():
            continue
        m = _LOCAL_CH_RE.match(f.name)
        if not m:
            continue
        chn = normalize_chapter(m.group(1))
        out.setdefault(chn, []).append(f)
    return out


def _local_chapters_map(title: str) -> dict:
    """Mismo shape que `_chapters_map` pero para capítulos locales (arte importado)."""
    return {chn: {"pageCount": len(files)} for chn, files in _local_chapter_files(title).items()}


def _local_art_files(title: str) -> dict:
    """Como `_local_chapter_files` pero PREFIRIENDO las páginas ya ESCALADAS (4K) por capítulo:
    si un capítulo tiene versión en UPSCALED_DIR la usa como arte base; si no, cae a la original.
    Es el arte cuando el usuario elige traducir sobre su copia ya descargada+escalada en vez de
    volver a buscar una fuente externa (evita re-descargar arte de menor calidad).

    Un capítulo YA TRADUCIDO es el caso delicado: sus páginas (y su copia escalada, re-generada
    después) están en ESPAÑOL, así que usarlas como arte trasplantaría español sobre español.
    Para esos manda `.original_art/` — el arte pre-traducción — que por eso se conserva."""
    orig = _local_chapter_files(title)
    up_root = Path(upscaled_dir()) / title
    out: dict = {}
    for chn, files in orig.items():
        prefix = _chapter_file_prefix(chn)
        pristine = original_art_files(title, prefix)
        if pristine:
            out[chn] = pristine            # capítulo ya traducido -> arte original guardado
            continue
        up = sorted(p for p in up_root.glob(prefix + "_*") if p.is_file()) if up_root.is_dir() else []
        out[chn] = up if up else files
    return out


# ── Tomos importados que abarcan varios capítulos reales ──────────────────────
# Un CBZ/CBR puede ser un TOMO (varios capítulos). import_cbz.py guarda esas
# páginas bajo un único prefijo "ch####" y registra el rango real en
# `.source_meta.json` → pending_volumes. Antes de poder traducir por capítulo
# real hay que repartir ese prefijo en los `ch####` reales — una vez hecho,
# el resto del pipeline (lector/upscale/export/transplant) no necesita saber
# que vino de un tomo: ve capítulos `ch####` normales.

def _source_meta_path(title: str) -> Path:
    return Path(manga_dir()) / title / ".source_meta.json"


def _read_source_meta(title: str) -> dict:
    p = _source_meta_path(title)
    if p.exists():
        try:
            return _json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _write_source_meta(title: str, meta: dict):
    p = _source_meta_path(title)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(_json.dumps(meta, ensure_ascii=False), encoding="utf-8")


def _pending_volumes(title: str) -> list:
    return _read_source_meta(title).get("pending_volumes", [])


def _write_pending_volumes(title: str, volumes: list):
    meta = _read_source_meta(title)
    meta["pending_volumes"] = volumes
    _write_source_meta(title, meta)


def _split_volume(title: str, vol: dict, chapter_pages: list, source_prefix: str = None) -> dict:
    """Reparte las páginas planas de un tomo (`{source_prefix or vol['prefix']}_NNN.ext`)
    en capítulos reales: `chapter_pages` es [(chapter_norm, page_count), ...] EN ORDEN
    (mismo orden que las páginas del tomo). La suma de page_count debe coincidir
    exactamente con el nº de páginas del tomo — si no, no se toca nada.
    `source_prefix` permite leer desde un prefijo temporal (ver `_stage_volume`) en
    vez de `vol['prefix']` directamente — necesario para que el rename hacia el
    capítulo real no pise el placeholder de OTRO tomo aún pendiente."""
    folder = Path(manga_dir()) / title
    prefix = source_prefix or vol['prefix']
    files = sorted(folder.glob(f"{prefix}_*.*"))
    total_wanted = sum(c for _, c in chapter_pages)
    if total_wanted != len(files):
        return {"ok": False, "error": f"page count mismatch: tomo={len(files)} páginas, suma capítulos={total_wanted}"}
    idx = 0
    for chn, count in chapter_pages:
        new_prefix = _chapter_file_prefix(chn)
        for j in range(count):
            f = files[idx]; idx += 1
            f.rename(folder / f"{new_prefix}_{j + 1:03d}{f.suffix}")
    return {"ok": True}


def _stage_volume(title: str, vol: dict) -> str:
    """Aparta las páginas planas de un tomo pendiente a un prefijo temporal que NO
    puede coincidir con ningún `ch####` real ni con el placeholder de otro tomo —
    libera el namespace `{vol['prefix']}_*` ANTES de repartir nada. Sin esto, si el
    rango real resuelto de un tomo cae sobre el nº de capítulo que otro tomo
    todavía pendiente usa como placeholder, el rename in-situ lo sobrescribe en
    silencio (corrupción de páginas detectada en pruebas con tomos encadenados)."""
    folder = Path(manga_dir()) / title
    files = sorted(folder.glob(f"{vol['prefix']}_*.*"))
    tmp_prefix = f"__staging{uuid.uuid4().hex[:10]}"
    for i, f in enumerate(files, 1):
        f.rename(folder / f"{tmp_prefix}_{i:03d}{f.suffix}")
    return tmp_prefix


def _free_chapter_prefix(title: str) -> str:
    """Siguiente prefijo ch#### libre en el manga — para reasignar el placeholder
    de un tomo pendiente si su nº original quedó ocupado por un capítulo real
    resuelto en esta misma pasada (ver `_unstage_volume`)."""
    folder = Path(manga_dir()) / title
    best = 0
    if folder.is_dir():
        for f in folder.iterdir():
            m = re.match(r'ch(\d+)', f.stem)
            if m:
                best = max(best, int(m.group(1)))
    return f"ch{best + 1:04d}"


def _unstage_volume(title: str, tmp_prefix: str, original_prefix: str) -> str:
    """Inverso de `_stage_volume`: si el tomo sigue sin resolverse, sus páginas
    vuelven a su prefijo placeholder — al original si sigue libre, o a uno nuevo
    si OTRO tomo resuelto en esta misma pasada se quedó justo con ese nº de
    capítulo real (mismo riesgo de colisión que en el split, visto en pruebas).
    Devuelve el prefijo final usado, para que el caller actualice los metadatos."""
    folder = Path(manga_dir()) / title
    target_prefix = original_prefix
    if list(folder.glob(f"{original_prefix}_*.*")):
        target_prefix = _free_chapter_prefix(title)
    files = sorted(folder.glob(f"{tmp_prefix}_*.*"))
    for i, f in enumerate(files, 1):
        f.rename(folder / f"{target_prefix}_{i:03d}{f.suffix}")
    return target_prefix


def _next_expected_chapter(title: str, pending: list):
    """Mayor capítulo real ya colocado (excluyendo los tomos aún pendientes) + 1,
    o None si no hay ninguno todavía (se busca desde el principio de la serie).
    Sirve de punto de partida por defecto cuando un tomo no trae una pista
    explícita de en qué capítulo empieza."""
    pending_norms = {normalize_chapter(v["prefix"]) for v in pending}
    real_keys = [k for k in _local_chapter_files(title) if k not in pending_norms]
    if not real_keys:
        return None
    return _chnum(max(real_keys, key=_chnum)) + 1


def _find_chapter_window(es_sorted: list, start_idxs: list, pages_wanted: int):
    """Busca, probando cada índice de inicio candidato en orden, una racha
    CONTIGUA de capítulos cuya suma de páginas sea EXACTAMENTE `pages_wanted`.
    Las páginas siempre son > 0, así que para un inicio fijo la suma crece
    monótonamente — hay como mucho un final posible por inicio. Devuelve la
    lista de claves de capítulo de la primera racha que cuadra, o None."""
    for i in start_idxs:
        total = 0
        chs = []
        for j in range(i, len(es_sorted)):
            chn, pages = es_sorted[j]
            if pages is None:
                break
            total += pages
            chs.append(chn)
            if total == pages_wanted:
                return chs
            if total > pages_wanted:
                break
    return None


def _resolve_pending_volumes(title: str, es_manga_id) -> dict:
    """Intenta repartir TODOS los tomos pendientes de `title` SIN que el usuario
    declare dónde termina cada uno: busca en la fuente ES ya elegida una racha de
    capítulos consecutivos (a partir de la pista del tomo, o encadenando desde
    donde terminó el anterior) cuya suma de páginas cuadre EXACTO con las páginas
    del tomo. Lo que no cuadra se deja pendiente con una sugerencia (para ajuste
    manual) en vez de adivinar."""
    pending = _pending_volumes(title)
    if not pending:
        return {"resolved": [], "unresolved": []}
    es_map = _chapters_map(int(es_manga_id))
    es_sorted = sorted(((k, v.get("pageCount")) for k, v in es_map.items()), key=lambda kv: _chnum(kv[0]))

    # Aparta TODOS los tomos pendientes antes de repartir ninguno (ver _stage_volume):
    # el rango real de uno puede coincidir con el placeholder de otro.
    staged = {vol["prefix"]: _stage_volume(title, vol) for vol in pending}

    cursor = _next_expected_chapter(title, pending)   # se encadena tomo a tomo
    resolved, remaining, pending_info = [], [], {}
    for vol in pending:
        hint = vol.get("chapterStartHint") or cursor
        if hint is not None:
            start_idxs = [i for i, (k, _) in enumerate(es_sorted) if _chnum(k) >= _chnum(hint)][:1]
        else:
            start_idxs = list(range(len(es_sorted)))

        chs = _find_chapter_window(es_sorted, start_idxs, vol["pages"])
        if chs is None:
            from_idx = start_idxs[0] if start_idxs else 0
            suggestion, total = [], 0
            for k, p in es_sorted[from_idx:]:
                suggestion.append({"chapter": k, "pageCount": p})
                total += p or 0
                if p is None or total >= vol["pages"]:
                    break
            pending_info[vol["prefix"]] = {"reason": "no_exact_match", "esChapters": suggestion}
            remaining.append(vol)
            cursor = None   # no sabemos dónde quedó -> no encadenar el siguiente a ciegas
            continue

        chapter_pages = [(k, dict(es_sorted)[k]) for k in chs]
        res = _split_volume(title, vol, chapter_pages, source_prefix=staged[vol["prefix"]])
        if res["ok"]:
            resolved.append({**vol, "chapters": chs})
            cursor = _chnum(chs[-1]) + 1
        else:
            pending_info[vol["prefix"]] = {
                "reason": res["error"],
                "esChapters": [{"chapter": k, "pageCount": dict(es_sorted)[k]} for k in chs],
            }
            remaining.append(vol)
            cursor = None

    # Lo que no se repartió vuelve a un placeholder libre — el original si sigue
    # libre, o uno nuevo si otro tomo resuelto en esta pasada se quedó con ese nº
    # (solo se sabe aquí, tras intentar TODOS los splits).
    unresolved = []
    for vol in remaining:
        original_prefix = vol["prefix"]
        vol["prefix"] = _unstage_volume(title, staged[original_prefix], original_prefix)
        unresolved.append({**vol, **pending_info[original_prefix]})

    _write_pending_volumes(title, remaining)
    return {"resolved": resolved, "unresolved": unresolved}


def _chapter_page_urls(chapter_id: int, timeout: float = None) -> list:
    q = "mutation($id: Int!){ fetchChapterPages(input:{chapterId:$id}){ pages } }"
    data = _gql_fast(q, {"id": int(chapter_id)}, timeout) if timeout else _gql(q, {"id": int(chapter_id)})
    pages = data["fetchChapterPages"]["pages"]
    return [SUWAYOMI_BASE + p if p.startswith("/") else p for p in pages]


def _is_color_bytes(content: bytes) -> bool:
    """True si la página es a COLOR (portada/créditos del scan, casi siempre a color).
    Mismo criterio que export/upscale (`is_color_page`): >10% de píxeles con diferencia
    de canal >15. Se evalúa sobre un thumbnail 256px para que sea barato."""
    img = cv2.imdecode(np.frombuffer(content, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        return False
    h, w = img.shape[:2]
    scale = 256.0 / max(h, w, 1)
    if scale < 1:
        img = cv2.resize(img, (max(1, int(w * scale)), max(1, int(h * scale))))
    b, g, r = (img[:, :, 0].astype(np.int16),
               img[:, :, 1].astype(np.int16),
               img[:, :, 2].astype(np.int16))
    max_diff = np.maximum(np.maximum(np.abs(r - g), np.abs(r - b)), np.abs(g - b))
    return float(np.mean(max_diff > _COLOR_DIFF)) > _COLOR_FRAC


def _deep_slice(seq: list, count: int, extra_offset: int = 0) -> list:
    """Devuelve hasta `count` elementos arrancando en una página PROFUNDA (~_SAMPLE_START).
    `extra_offset` rota el punto de inicio entre capítulos (mejora C): si un cap tiene
    créditos fijos en las páginas 8-10, no se muestrea siempre el mismo punto.
    El inicio se clampea para no salirse del capítulo."""
    if not seq:
        return []
    base = _SAMPLE_START if len(seq) > _SAMPLE_START + 1 else len(seq) // 2
    start = min(base + extra_offset, max(0, len(seq) - count))
    return seq[start:start + count] or seq[start:] or seq[:count]


def _filter_noncolor(urls: list, want: int) -> list:
    """Descarga las URLs en paralelo y devuelve hasta `want` páginas NO a color, en orden.
    Sirve para que la comparación A|B caiga en páginas de historia (B/N) y no en
    portada/créditos (a color), que difieren entre scans y harían la comparación inútil."""
    if not urls:
        return []
    def _grab(u):
        return (u, _fetch_ref(u))   # http (Suwayomi/MangaDex) o ruta local servida
    keep = []
    with ThreadPoolExecutor(max_workers=len(urls) or 1) as pool:
        for u, content in pool.map(_grab, urls):
            if content and not _is_color_bytes(content):
                keep.append(u)
                if len(keep) >= want:
                    break
    return keep


def _sharp_factor(sharp: float) -> float:
    """Factor de nitidez SATURANTE y estrictamente creciente: sharp/(sharp+ref) ∈ (0,1).
    Piso 0.35 (antes 0.5): castiga MÁS los upscales borrosos — un scan grande pero blando
    (típico de agregadores) NO debe ganarle a un nativo nítido más pequeño. La resolución
    sigue mandando, pero la nitidez ya no es solo un desempate."""
    return 0.35 + 0.65 * (sharp / (sharp + _SHARP_REF))


def _bpp_factor(bpp: float) -> float:
    """Factor de calidad de compresión: bytes/px sigmoide con piso 0.70.
    Penaliza scans re-comprimidos agresivamente (JPEG 60-70%) sin castigar demasiado
    las diferencias entre formatos (JPEG 90% vs PNG lossless)."""
    return 0.70 + 0.30 * (bpp / (bpp + _BPP_REF))


def _spread_pick(seq: list, n: int = _SCORE_CHAPTERS, fracs=_SCORE_FRACS) -> list:
    """Hasta `n` elementos de `seq` DISTRIBUIDOS por toda la serie (incl. los más
    adelantados), no solo inicio+medio. Dedup de índices en series cortas."""
    if not seq:
        return []
    L = len(seq)
    use = list(fracs)[:n]
    idxs = sorted({min(L - 1, max(0, int(round(f * (L - 1))))) for f in use})
    return [seq[i] for i in idxs]


def _measure_chapter(contents) -> dict | None:
    """Mide UN capítulo: mediana de altura/nitidez/bytes-px sobre sus páginas B/N
    (descarta color: portada/créditos falsean el muestreo). Sub-score del capítulo =
    altura × sharp_factor × bpp_factor."""
    heights, sharps, bpp_vals = [], [], []
    for content in contents:
        if not content or _is_color_bytes(content):
            continue
        img = cv2.imdecode(np.frombuffer(content, np.uint8), cv2.IMREAD_GRAYSCALE)
        if img is None:
            continue
        h, w = img.shape[:2]
        heights.append(h)
        sharps.append(float(cv2.Laplacian(img, cv2.CV_64F).var()))
        bpp_vals.append(len(content) / max(1, h * w))
    if not heights:
        return None
    height = float(np.median(heights))
    sharp  = float(np.median(sharps))
    bpp    = float(np.median(bpp_vals))
    return {"height": height, "sharp": sharp, "bpp": bpp,
            "n": len(heights), "score": height * _sharp_factor(sharp) * _bpp_factor(bpp)}


def _combine_quality(chapters: list) -> dict | None:
    """Combina los sub-scores POR capítulo penalizando la IRREGULARIDAD: el resultado
    mezcla la mediana de capítulos con el PEOR capítulo (`_WORST_WEIGHT`), así una fuente
    con algún capítulo malo no gana por la fuerza de los buenos. Expone el rango de altura
    y un índice de consistencia (worst/median, 1.0 = uniforme) para la UI."""
    chs = [c for c in chapters if c and c.get("n")]
    if not chs:
        return None
    scores = sorted(c["score"] for c in chs)
    heights = [c["height"] for c in chs]
    median_s = float(np.median(scores))
    worst_s = scores[0]
    combined = (1 - _WORST_WEIGHT) * median_s + _WORST_WEIGHT * worst_s
    return {
        "height": round(float(np.median(heights))),
        "heightMin": round(min(heights)),
        "heightMax": round(max(heights)),
        "sharpness": round(float(np.median([c["sharp"] for c in chs])), 1),
        "bytesPerPx": round(float(np.median([c["bpp"] for c in chs])), 4),
        "samples": sum(c["n"] for c in chs),
        "chapters": len(chs),
        "worstScore": round(worst_s, 1),
        "medianScore": round(median_s, 1),
        "consistency": round(worst_s / median_s, 2) if median_s else 1.0,
        "score": round(combined, 1),
    }


def _grab_url(u):
    """Baja una página de muestra (sin reintentos). NO usa el semáforo de descargas
    pesadas (_dl_semaphore, para capítulos enteros)."""
    try:
        r = http_requests.get(u, timeout=_SAMPLE_TIMEOUT)
        return r.content if (r and r.status_code == 200 and r.content) else None
    except Exception:
        return None


def _score_url_chapters(chapter_url_lists: list) -> dict | None:
    """`chapter_url_lists` = una lista de URLs por capítulo. Baja TODAS las páginas EN
    PARALELO (un solo pool, así una fuente lenta cuelga ~_SAMPLE_TIMEOUT y no en serie),
    mide por capítulo y combina con penalización de irregularidad."""
    flat = [(ci, u) for ci, urls in enumerate(chapter_url_lists) for u in (urls or [])]
    if not flat:
        return None
    buckets = [[] for _ in chapter_url_lists]
    with ThreadPoolExecutor(max_workers=len(flat)) as pool:
        for ci, content in pool.map(lambda iu: (iu[0], _grab_url(iu[1])), flat):
            buckets[ci].append(content)
    return _combine_quality([_measure_chapter(b) for b in buckets])


def _score_source(manga_id: int) -> dict | None:
    """Puntúa la calidad de una fuente SIN descargar capítulos enteros: muestrea páginas
    profundas de _SCORE_CHAPTERS capítulos DISTRIBUIDOS por toda la serie (no solo
    inicio+medio) y combina por capítulo penalizando la irregularidad."""
    chmap = _chapters_map(manga_id, timeout=_SAMPLE_TIMEOUT)
    if not chmap:
        return None
    ordered = sorted(chmap.values(),
                     key=lambda c: float(c["number"]) if c["number"] is not None else 0)
    with_pages = [c for c in ordered if (c.get("pageCount") or 1) > 0] or ordered
    if not with_pages:
        return None
    # páginas PROFUNDAS de cada capítulo (saltando portada/créditos); margen extra para las
    # que sigan a color y se descarten al medir. Rotación de offset entre capítulos (mejora C).
    chapter_urls = []
    for i, ch in enumerate(_spread_pick(with_pages)):
        try:
            urls = _chapter_page_urls(ch["id"], timeout=_SAMPLE_TIMEOUT)
        except Exception:
            continue
        sl = _deep_slice(urls, _SCORE_PAGES + _COLOR_MARGIN, extra_offset=(i % 4) * 3)
        if sl:
            chapter_urls.append(sl)
    return _score_url_chapters(chapter_urls)


def _score_local(title: str) -> dict | None:
    """Puntúa la versión LOCAL con el MISMO algoritmo que `_score_source`, muestreando
    capítulos locales distribuidos. Permite mostrar la versión actual como línea base
    ([ACTUAL]) en el ranking de versiones — "¿vale la pena cambiar?"."""
    chfiles = _local_chapter_files(title)
    if not chfiles:
        return None
    ordered = [chfiles[k] for k in sorted(chfiles.keys(), key=_chnum)]
    with_pages = [f for f in ordered if f] or ordered
    if not with_pages:
        return None
    chapters = []
    for i, files in enumerate(_spread_pick(with_pages)):
        if not files:
            continue
        contents = []
        for f in _deep_slice(files, _SCORE_PAGES + _COLOR_MARGIN, extra_offset=(i % 4) * 3):
            try:
                contents.append(f.read_bytes())
            except Exception:
                contents.append(None)
        m = _measure_chapter(contents)
        if m:
            chapters.append(m)
    return _combine_quality(chapters)


# ── MangaDex NATIVO como fuente de versiones (su propia API, no vía Suwayomi) ──
_MD_API = "https://api.mangadex.org"
_MD_RATINGS = ["safe", "suggestive", "erotica", "pornographic"]
# Idiomas que pedimos a MangaDex. OJO: su `translatedLanguage[]` exige el patrón
# ^[a-z]{2}(-[a-z]{2})?$ — códigos como "es-419"/"es-es"/"es-mx" (que usa Suwayomi) dan
# HTTP 400 y vacían el feed entero. MangaDex usa "es" (castellano) y "es-la" (latino).
_MD_LANGS_OK = {"en", "ja", "ko", "zh", "zh-hk", "pt-br", "fr", "ru", "es", "es-la"}


def _md_get(path, **params):
    try:
        r = http_requests.get(f"{_MD_API}{path}", params=params, timeout=15)
        return r.json() if r.status_code == 200 else None
    except Exception:
        return None


_md_feed_cache: dict = {}          # uuid -> (ts, feed) — memoiza el feed durante un barrido
_MD_FEED_TTL = 300                  # 5 min: el feed apenas cambia dentro de una sesión

def _md_feed(manga_uuid) -> list:
    """Capítulos de una obra MangaDex: [{id, number, lang, group, groupName}] (paginado).
    Incluye el GRUPO de scanlation para poder separar cada versión (p.ej. cada grupo ES) como
    candidato propio. Memoizado por uuid — durante un ranking se pide el mismo feed muchas veces."""
    manga_uuid = str(manga_uuid).split("@@")[0]
    hit = _md_feed_cache.get(manga_uuid)
    if hit and (time.time() - hit[0]) < _MD_FEED_TTL:
        return hit[1]
    out, offset = [], 0
    while True:
        d = _md_get(f"/manga/{manga_uuid}/feed", **{
            "limit": 500, "offset": offset, "order[chapter]": "asc",
            "translatedLanguage[]": list(_MD_LANGS_OK), "contentRating[]": _MD_RATINGS,
            "includes[]": ["scanlation_group"]})
        data = (d or {}).get("data", []) if d else []
        for ch in data:
            a = ch.get("attributes", {})
            gid, gname = "", ""
            for rel in (ch.get("relationships") or []):
                if rel.get("type") == "scanlation_group":
                    gid = rel.get("id") or ""
                    gname = ((rel.get("attributes") or {}).get("name")) or ""
                    break
            out.append({"id": ch["id"], "number": a.get("chapter"),
                        "lang": (a.get("translatedLanguage") or "").lower(),
                        "group": gid, "groupName": gname,
                        "publishedAt": a.get("publishAt")})
        if len(data) < 500:
            break
        offset += 500
    _md_feed_cache[manga_uuid] = (time.time(), out)
    return out


def _md_feed_subset(manga_uuid, lang=None, group=None) -> list:
    """Feed filtrado por idioma/grupo (con fallback a todo si el filtro deja vacío)."""
    feed = _md_feed(manga_uuid)
    if lang:
        same = [c for c in feed if c["lang"] == lang.lower()]
        feed = same or feed
    if group:
        g = [c for c in feed if (c.get("group") or "") == group]
        feed = g or feed
    return feed


def _split_part_units(entries, num_of) -> list:
    """Colapsa las PARTES de un mismo capítulo en una sola unidad (agnóstico de fuente).

    Fuentes (MangaDex, Suwayomi…) suben a menudo el mismo capítulo troceado (45, 45.1, 45.2 …).
    Aquí se agrupan esas partes contiguas (.1/.2/.3…) bajo su nº entero para tratarlas como UN
    capítulo (páginas concatenadas). Un decimal suelto que NO es parte contigua (típico omake
    `.5`) se conserva aparte. `num_of(e)` extrae el nº de cada entrada. Devuelve
    [{number, members:[e,...]}] ordenado ascendente.

    La familia se parte en dos: el PREFIJO contiguo (el entero si está, más .1, .2, .3… sin
    huecos) = las partes del capítulo; y el resto = capítulos propios. Mirar sólo el prefijo
    (en vez de exigir que TODA la familia sea contigua) permite que un omake convivan con las
    partes: `4.1` + `4.5` da "4" (la parte 1) y "4.5" (el omake) — antes la familia entera se
    declaraba no-contigua y el capítulo 4 DESAPARECÍA de la lista."""
    by_base: dict = {}
    for e in entries:
        n = num_of(e)
        if n is None or n == "":
            continue
        by_base.setdefault(int(_chnum(n)), []).append(e)
    units = []
    for base, fam in by_base.items():
        fam = sorted(fam, key=lambda e: _chnum(num_of(e)))
        members, rest, nxt = [], [], 1
        for e in fam:
            frac = round(_chnum(num_of(e)) - base, 3)
            if frac == 0:                                    # el entero (45)
                members.append(e)
            elif abs(frac - round(nxt / 10, 3)) < 1e-6:      # la siguiente parte contigua
                members.append(e); nxt += 1
            else:
                rest.append(e)                               # omake/decimal suelto
        if members:
            units.append({"number": str(base), "members": members})
        for e in rest:
            units.append({"number": num_of(e), "members": [e]})
    return sorted(units, key=lambda u: _chnum(u["number"]))


def _md_chapter_units(manga_uuid, lang=None, group=None) -> list:
    """Unidades de capítulo de MangaDex con partes fusionadas → [{number, ids:[...]}]."""
    feed = _md_feed_subset(manga_uuid, lang, group)
    return [{"number": u["number"], "ids": [c["id"] for c in u["members"]]}
            for u in _split_part_units(feed, lambda c: c.get("number"))]


def _md_pick_chapter(manga_uuid, lang=None, want_num=None, group=None):
    """Ids de las páginas que componen un capítulo (fusiona partes). Devuelve (ids, number).
    Sin want_num: la primera unidad numerada (representativa para muestreo de calidad)."""
    units = _md_chapter_units(manga_uuid, lang, group)
    if want_num is not None:
        wn = _chnum(str(want_num))
        for u in units:
            if _chnum(u["number"]) == wn:
                return u["ids"], u["number"]
        return [], None
    pick = units[0] if units else None
    return (pick["ids"], pick["number"]) if pick else ([], None)


def _md_page_urls(chapter_id) -> list:
    """URLs de página vía MangaDex@Home (baseUrl de un solo uso, nunca se cachea)."""
    d = _md_get(f"/at-home/server/{chapter_id}")
    if not d:
        return []
    base = d.get("baseUrl"); ch = d.get("chapter", {})
    h = ch.get("hash"); pages = ch.get("data", [])
    return [f"{base}/data/{h}/{p}" for p in pages] if base and h else []


def _md_candidate_chapter_urls(manga_uuid, lang, want_num, group=None):
    ids, num = _md_pick_chapter(manga_uuid, lang, want_num, group)
    if not ids:
        return (None, [])
    urls = []
    for cid in ids:            # concatena las partes (45, 45.1, 45.2 …) en orden
        urls += _md_page_urls(cid)
    return (num, urls)


def _md_search(query) -> list:
    """Busca en MangaDex y devuelve, por obra, TODOS sus títulos (principal + altTitles en
    todos los idiomas) para poder casar contra cualquier sinónimo/variante AniList."""
    d = _md_get("/manga", **{"limit": 10, "title": query, "contentRating[]": _MD_RATINGS})
    out = []
    for m in (d or {}).get("data", []) if d else []:
        attrs = m.get("attributes", {}) or {}
        t = attrs.get("title") or {}
        titles = [v for v in t.values() if v]
        for alt in (attrs.get("altTitles") or []):
            titles += [v for v in (alt or {}).values() if v]
        name = t.get("en") or (next(iter(t.values()), "") if t else "")
        out.append({"id": m["id"], "title": name, "titles": titles})
    return out


def _md_candidates(variants, cur_lang="") -> list:
    """Candidatos NATIVOS de MangaDex (vía su API). Busca por las variantes AniList (romaji/
    inglés/nativo/sinónimos), filtra por parecido contra CUALQUIER título de la obra (misma
    lógica fuzzy+substring que el discover de Suwayomi) y crea un candidato por idioma
    disponible (acotado), para que MangaDex entre al ranking aunque no esté su extensión en
    Suwayomi. Cada candidato lleva el id del capítulo representativo (`_mdChapterId`)."""
    var_keys = [_norm_key(v) for v in variants if v]
    if not var_keys:
        return []
    seen = {}
    for q in [v for v in (variants or []) if v][:4]:   # romaji + inglés + nativo + 1er sinónimo
        for m in _md_search(q):
            if m["id"] in seen:
                continue
            best = 0.0
            for cand_title in (m.get("titles") or [m["title"]]):
                tkey = _norm_key(cand_title)
                r = max((SequenceMatcher(None, tkey, vk).ratio() for vk in var_keys), default=0.0)
                if r < _FUZZY_MIN and any(vk in tkey or tkey in vk for vk in var_keys):
                    r = _FUZZY_MIN   # substring (un título contiene a la variante) → válido
                best = max(best, r)
            if best >= _FUZZY_MIN:
                seen[m["id"]] = (m, round(best, 3))
    cands = []
    for uuid, (m, match) in list(seen.items())[:2]:   # como mucho 2 obras MangaDex
        langs = {}          # idioma NO-español -> 1er capítulo numerado (uno por idioma)
        es_groups = {}      # (lang, groupId) -> {id, count, name} — español separado por GRUPO
        for c in _md_feed(uuid):
            l = c["lang"]
            if l not in _MD_LANGS_OK or not c["number"]:
                continue
            if l in _ES_LANGS:
                # El español NO se colapsa a un candidato: cada grupo de scanlation (Platinum
                # Lily, etc.) es una versión distinta con su propia calidad → un candidato cada
                # uno, para que Traducir los liste TODOS ordenados por calidad, no solo el mejor.
                gid = c.get("group") or ""
                g = es_groups.setdefault((l, gid), {"id": c["id"], "count": 0, "name": c.get("groupName") or ""})
                g["count"] += 1
                if not g["name"] and c.get("groupName"):
                    g["name"] = c["groupName"]
            else:
                langs.setdefault(l, c["id"])   # 1er capítulo numerado de ese idioma
        # Idiomas no-español: idioma actual PRIMERO + el resto (acotado), uno por idioma.
        cur = (cur_lang or "").lower()
        wanted = [l for l in langs if l == cur] + [l for l in langs if l != cur]
        for l in wanted[:4]:
            # id ÚNICO por idioma (`uuid@@lang`): todas las versiones de idioma de una misma
            # obra MangaDex comparten uuid; sin esto colisionan en dedup y en las keys del
            # v-for del frontend (Vue renderizaría solo una). El routing parte por '@@'.
            cands.append({"sourceId": "__mangadex__", "id": f"{uuid}@@{l}", "sourceName": "MangaDex",
                          "sourceLang": l, "title": m["title"], "match": match,
                          "_mdChapterId": langs[l]})
        # Español: un candidato POR GRUPO, los más completos primero (cap 8 por coste de ranking).
        # El grupo va como 3ª parte del id (`uuid@@lang@@grupo`) → viaja opaco por front y descarga.
        es_sorted = sorted(es_groups.items(), key=lambda kv: kv[1]["count"], reverse=True)[:8]
        for (l, gid), g in es_sorted:
            gname = g["name"] or "Sin grupo"
            cands.append({"sourceId": "__mangadex__",
                          "id": f"{uuid}@@{l}@@{gid}" if gid else f"{uuid}@@{l}",
                          "sourceName": f"MangaDex · {gname}",
                          "sourceLang": l, "title": m["title"], "match": match,
                          "_mdChapterId": g["id"], "_group": gid, "_groupName": gname})
    return cands


def _score_md(manga_uuid, lang=None, chapter_id=None) -> dict | None:
    """Puntúa una versión de MangaDex con el MISMO motor exhaustivo: capítulos
    DISTRIBUIDOS del feed (de ese idioma) + penalización de irregularidad."""
    try:
        parts = str(manga_uuid).split("@@")   # id puede venir como `uuid@@lang@@grupo`
        manga_uuid = parts[0]
        grp = parts[2] if len(parts) > 2 else None
        feed = _md_feed(manga_uuid)
        if lang:
            same = [c for c in feed if c["lang"] == lang.lower()]
            feed = same or feed
        if grp:   # puntuar SOLO los capítulos de ese grupo (su calidad propia)
            g = [c for c in feed if (c.get("group") or "") == grp]
            feed = g or feed
        numbered = [c for c in feed if c["number"]] or feed
        chapter_urls = []
        for i, ch in enumerate(_spread_pick(numbered)):
            sl = _deep_slice(_md_page_urls(ch["id"]), _SCORE_PAGES + _COLOR_MARGIN, extra_offset=(i % 4) * 3)
            if sl:
                chapter_urls.append(sl)
        if not chapter_urls:   # fallback al capítulo representativo
            cid = chapter_id or (_md_pick_chapter(manga_uuid, lang)[0] or [None])[0]
            sl = _deep_slice(_md_page_urls(cid), _SCORE_PAGES + _COLOR_MARGIN) if cid else []
            if sl:
                chapter_urls.append(sl)
        return _score_url_chapters(chapter_urls)
    except Exception:
        return None


_QUALITY_TTL = 86400   # 24h: el score de una versión (resolución/nitidez) no cambia

def _score_candidate(c, use_cache: bool = True) -> dict | None:
    """Enruta el scoring por tipo de fuente (MangaDex nativo vs Suwayomi). Memoiza el score
    —lo MÁS caro: descarga páginas de muestra— por (sourceId, mangaId, lang), así Versiones y
    Traducir reutilizan el mismo cálculo y un re-barrido no vuelve a bajar muestras. Resultado
    idéntico (el score es determinista); solo los fallos (None) no se cachean → se reintentan."""
    # `v2`: versión del algoritmo de scoring (muestreo exhaustivo distribuido + penalización
    # de irregularidad). Bumpea la clave para NO reusar scores cacheados del algoritmo viejo.
    qkey = f"v3|{c.get('sourceId')}|{c.get('id')}|{(c.get('sourceLang') or '').lower()}"
    if use_cache:
        cached = cache_get("quality", qkey, _QUALITY_TTL)
        if cached is not None:
            return cached
    if str(c.get("sourceId")) == "__mangadex__":
        q = _score_md(c["id"], c.get("sourceLang"), c.get("_mdChapterId"))
    else:
        q = _score_source(int(c["id"]))
    if q is not None:
        cache_set("quality", qkey, q, ttl=_QUALITY_TTL, max_entries=600)
    return q


# ── Emparejado de páginas por hash perceptual (comparar la MISMA página entre scans) ──
# Umbral 0.75: páginas DISTINTAS dan ~0.40-0.65; la MISMA página (aunque cambie resolución,
# recompresión o el TEXTO del globo por traducción ES↔EN) da ~0.80-0.95. 0.75 separa con
# margen. La ventana de B es ANCHA porque los conteos de página difieren mucho entre scans
# (p.ej. 51 vs 59 páginas) → el desfase puede ser de muchas páginas.
_MATCH_MIN_SIM = 0.75      # similitud mínima (1 - hamming/64) para aceptar "misma página"
_MATCH_REF_PAGES = 6       # pares objetivo a devolver
_MATCH_A_WINDOW = 10       # ventana profunda de A de la que salen las referencias
_MATCH_SEARCH_PAGES = 32   # ventana ANCHA de B: cubre casi todo el capítulo (absorbe el desfase)


def _dhash_bytes(content: bytes):
    """Hash perceptual diferencial de 64 bits (resize 9×8 gris, compara vecinos en fila).
    Casi invariante a resolución y recompresión JPEG → dos scans de la MISMA página dan
    hashes muy próximos (distancia de Hamming pequeña)."""
    img = cv2.imdecode(np.frombuffer(content, np.uint8), cv2.IMREAD_GRAYSCALE)
    if img is None:
        return None
    small = cv2.resize(img, (9, 8), interpolation=cv2.INTER_AREA)
    diff = small[:, 1:] > small[:, :-1]
    h = 0
    for bit in diff.flatten():
        h = (h << 1) | int(bit)
    return h


def _hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def _fetch_ref(ref: str):
    """Bytes de una página: URL http (Suwayomi/MangaDex) o ruta local servida 'titulo/archivo'."""
    if ref.startswith("http"):
        try:
            r = http_requests.get(ref, timeout=_SAMPLE_TIMEOUT)
            return r.content if (r and r.status_code == 200 and r.content) else None
        except Exception:
            return None
    p = Path(manga_dir()) / ref
    try:
        return p.read_bytes() if p.exists() else None
    except Exception:
        return None


def _hash_refs(refs: list) -> list:
    """Baja/lee cada ref en paralelo, descarta páginas a color y devuelve [(ref, dhash)]."""
    def _one(ref):
        content = _fetch_ref(ref)
        if not content or _is_color_bytes(content):
            return None
        h = _dhash_bytes(content)
        return (ref, h) if h is not None else None
    out = []
    with ThreadPoolExecutor(max_workers=len(refs) or 1) as pool:
        for res in pool.map(_one, refs):
            if res:
                out.append(res)
    return out


def _candidate_chapter_urls(cand, title, want_num=None):
    """(numero, [refs]) de UN capítulo del candidato. Enruta local / MangaDex / Suwayomi.
    `want_num` intenta alinear ambos lados al MISMO capítulo."""
    sid = str(cand.get("sourceId") or "")
    mid = cand.get("mangaId")
    if str(mid) == "__local__" or sid == "__local__":
        chfiles = _local_chapter_files(title)
        if not chfiles:
            return (None, [])
        keys = sorted(chfiles.keys(), key=_chnum)
        key = None
        if want_num is not None:
            key = next((k for k in keys if _chnum(k) == _chnum(str(want_num))), None)
        key = key or next((k for k in keys if chfiles[k]), keys[0])
        return (key, [f"{title}/{f.name}" for f in chfiles[key]])
    if sid == "__mangadex__":
        parts = str(mid).split("@@")   # id viene como `uuid@@lang` o `uuid@@lang@@grupo`
        uuid = parts[0]
        group = parts[2] if len(parts) > 2 else None
        return _md_candidate_chapter_urls(uuid, cand.get("sourceLang"), want_num, group)
    cmap = _chapters_map(int(mid), timeout=_SAMPLE_TIMEOUT)
    if not cmap:
        return (None, [])
    if want_num is not None:
        # El capítulo pedido puede venir troceado en la fuente (45, 45.1, 45.2 …): fusiona sus
        # partes contiguas y concatena sus páginas. Si no existe → vacío (NUNCA sustituir por
        # otro capítulo; antes caía al 1º → trasplantaba el capítulo equivocado).
        wn = _chnum(str(want_num))
        unit = next((u for u in _split_part_units(cmap.values(), lambda c: c.get("number"))
                     if _chnum(u["number"]) == wn), None)
        if not unit:
            return (None, [])
        urls = []
        for c in unit["members"]:
            try:
                urls += _chapter_page_urls(c["id"], timeout=_SAMPLE_TIMEOUT)
            except Exception:
                pass
        return (unit["number"], urls)
    # Sin capítulo pedido (muestreo de calidad): uno representativo.
    ordered = sorted(cmap.values(),
                     key=lambda c: float(c["number"]) if c["number"] is not None else 0)
    with_pages = [c for c in ordered if (c.get("pageCount") or 1) > 0] or ordered
    ch = with_pages[0] if with_pages else None
    if not ch:
        return (None, [])
    try:
        return (ch.get("number"), _chapter_page_urls(ch["id"], timeout=_SAMPLE_TIMEOUT))
    except Exception:
        return (ch.get("number"), [])


def _candidate_chapter_numbers(cand, title) -> list:
    """Lista ASC de números de capítulo del candidato (para muestrear varios al comparar)."""
    sid = str(cand.get("sourceId") or ""); mid = cand.get("mangaId")
    if str(mid) == "__local__" or sid == "__local__":
        return sorted(_local_chapter_files(title).keys(), key=_chnum)
    if sid == "__mangadex__":
        parts = str(mid).split("@@")
        uuid = parts[0]
        group = parts[2] if len(parts) > 2 else None
        lang = (cand.get("sourceLang") or "").lower()
        # unidades ya fusionadas: las partes 45/45.1/45.2 salen como un único "45"
        return [u["number"] for u in _md_chapter_units(uuid, lang, group)]
    cmap = _chapters_map(int(mid), timeout=_SAMPLE_TIMEOUT)
    withp = [c for c in (cmap or {}).values() if (c.get("pageCount") or 1) > 0 and c.get("number") is not None]
    # fusiona partes 45/45.1/45.2 en un único "45"
    return [u["number"] for u in _split_part_units(withp, lambda c: c.get("number"))]


def _distributed_picks(seq, fracs=(0.25, 0.5, 0.75)) -> list:
    """Elige elementos repartidos (p.ej. ~25/50/75%) — evita el sesgo del cap 1 (suele ser el
    de mejor calidad) y retrata mejor la calidad real de toda la serie."""
    out, seen = [], set()
    for f in fracs:
        if not seq:
            break
        i = min(len(seq) - 1, int(len(seq) * f))
        if i not in seen:
            seen.add(i); out.append(seq[i])
    return out


def _match_pairs(urls_a, urls_b, want_pairs):
    """Empareja páginas equivalentes entre dos capítulos por dHash. Devuelve
    [{left, right, sim}] en orden de A; greedy (cada página de B se usa una vez) y solo
    pares con similitud ≥ _MATCH_MIN_SIM, para comparar SIEMPRE la misma imagen."""
    a_win = _deep_slice(urls_a, _MATCH_A_WINDOW)            # referencia profunda de A
    b_start = max(0, _SAMPLE_START - 5)                     # arranca antes
    b_win = urls_b[b_start:b_start + _MATCH_SEARCH_PAGES] or urls_b   # ventana ANCHA de B
    a_h = _hash_refs(a_win)
    b_h = _hash_refs(b_win)
    # Empareja cada página de A con la de B más parecida. MONÓTONO: como las páginas van en
    # orden, una vez A[i]→B[j], A[i+1] solo busca en B[>j] (evita falsos cruces y respeta el
    # desfase acumulado). Solo se aceptan pares con similitud ≥ _MATCH_MIN_SIM.
    pairs, last_j = [], -1
    for ref_a, ha in a_h:
        best_j, best_d = None, 999
        for j in range(last_j + 1, len(b_h)):
            d = _hamming(ha, b_h[j][1])
            if d < best_d:
                best_d, best_j = d, j
        if best_j is not None and (1 - best_d / 64) >= _MATCH_MIN_SIM:
            last_j = best_j
            pairs.append({"left": ref_a, "right": b_h[best_j][0], "sim": round(1 - best_d / 64, 3)})
            if len(pairs) >= want_pairs:
                break
    return pairs


def _rank(candidates: list, on_progress=None, use_cache: bool = True) -> list:
    """Puntúa cada candidato (muestreo) y devuelve ordenado desc por score. `use_cache=False`
    ('Buscar de nuevo') fuerza re-bajar muestras saltando la memoización del score."""
    out = []
    done = 0
    with ThreadPoolExecutor(max_workers=10) as pool:
        futs = {pool.submit(_score_candidate, c, use_cache): c for c in candidates}
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


# ── Búsqueda de fuentes memoizada (compartida por Versiones y Traducir) ───────────
_CANDIDATES_TTL = 6 * 3600   # 6h: el fan-out de búsqueda (~185 fuentes) se cachea por título


def _search_key(title, al_id, source_ids):
    return f"{title}|{al_id or ''}|{','.join(map(str, source_ids or []))}"


def _candidates_cached(title, al_id, variants, source_ids, on_progress=None, refresh=False,
                       stats=None):
    """`_discover_candidates` (barrido de fuentes) memoizado en disco por (title, al_id,
    source_ids). Resultado idéntico; evita re-barrer ~185 fuentes en cada apertura de
    Versiones/Traducir o en re-barridos del mismo título. `refresh` lo salta y re-cachea.

    SÓLO se cachea un barrido que vio TODAS las fuentes. `if cands:` ya evitaba guardar el vacío,
    pero no el PARCIAL — y un parcial es peor: parece un resultado. MEDIDO: 6 candidatos en vez de
    72 (Suwayomi cargando extensiones) se habrían servido `_CANDIDATES_TTL` entero como la lista
    real de fuentes del manga."""
    key = _search_key(title, al_id, source_ids)
    if not refresh:
        cached = cache_get("candidates", key, _CANDIDATES_TTL)
        if cached is not None:
            if on_progress:
                try: on_progress(len(cached), len(cached))
                except Exception: pass
            return cached
    st = stats if stats is not None else {}
    cands = _discover_candidates(variants, source_ids=source_ids, on_progress=on_progress, stats=st)
    if cands and not st.get("failed"):
        cache_set("candidates", key, cands, ttl=_CANDIDATES_TTL, max_entries=120)
    elif cands:
        print(f"[transplant] barrido de {title!r} INCOMPLETO ({st.get('failed')} de "
              f"{st.get('tasks')} búsquedas fallaron) — no se cachea", file=sys.stderr, flush=True)
    return cands


# ── Cobertura: qué capítulos tiene cada fuente ─────────────────────────────────
# Catálogo LIVIANO (solo número de capítulo + fecha si la hay, sin bajar imágenes) por
# candidato, para calcular completitud/frecuencia y pintar el grid de cobertura. No se
# persiste en SQLite (es información volátil: la fuente añade capítulos constantemente):
# se cachea en disco con el mismo cache_get/cache_set genérico, TTL corto (30 min, más
# corto que "candidates" 6h porque "Revisar actualizaciones" espera datos frescos).
_COVERAGE_TTL = 1800
# Resultado COMPLETO de cobertura (todas las fuentes ya medidas) — TTL largo para que reabrir
# el manga lo pinte al instante; solo "Recalcular" lo refresca.
_COVERAGE_RESULT_TTL = 30 * 24 * 3600


def _coverage_key(cand) -> str:
    sid = str(cand.get("sourceId") or "")
    mid = cand.get("mangaId", cand.get("id"))
    lang = (cand.get("sourceLang") or "").lower()
    return f"{sid}|{mid}|{lang}"


def _source_kind(cand) -> str:
    sid = str(cand.get("sourceId") or "")
    mid = str(cand.get("mangaId", cand.get("id")) or "")
    if sid == "__mangadex__":
        return "mangadex"
    if sid == "__local__" or mid == "__local__":
        return "local"
    return "suwayomi"


def _source_catalog(cand, title=None, refresh=False):
    """{chapter_norm: {number, publishedAt?}} de un candidato — SIN bajar páginas.
    Enruta local/MangaDex/Suwayomi igual que `_candidate_chapter_urls`.

    Devuelve **None** si no se pudo PREGUNTAR a la fuente (red caída, Suwayomi muerta, Cloudflare),
    frente a **{}** = "la fuente existe y no tiene capítulos". La diferencia es crítica porque esta
    función CACHEA: antes un fallo de red devolvía {} y se guardaba `_COVERAGE_TTL` entero, así que
    la fuente quedaba marcada como vacía aunque tuviera 29 capítulos. Un None NUNCA se cachea."""
    key = _coverage_key(cand)
    if not refresh:
        cached = cache_get("coverage", key, _COVERAGE_TTL)
        if cached is not None:
            return cached
    kind = _source_kind(cand)
    out: dict = {}
    if kind == "local":
        for chn in _local_chapter_files(title or "").keys():
            out[chn] = {"number": chn}
    elif kind == "mangadex":
        uuid = str(cand.get("mangaId", cand.get("id"))).split("@@")[0]
        lang = (cand.get("sourceLang") or "").lower()
        for c in _md_feed(uuid):
            if lang and c.get("lang") != lang:
                continue
            n = c.get("number")
            if not n:
                continue
            chn = normalize_chapter(n)
            if chn and chn not in out:
                out[chn] = {"number": n, "publishedAt": c.get("publishedAt")}
    else:
        mid = cand.get("mangaId", cand.get("id"))
        try:
            cmap = _chapters_map(int(mid), timeout=_SAMPLE_TIMEOUT, strict=True)
        except Exception as e:
            print(f"[transplant] catálogo NO consultable para {cand.get('sourceId')}/{mid} "
                  f"({type(e).__name__}: {str(e)[:70]}) — no se cachea, no es 'está vacía'",
                  file=sys.stderr, flush=True)
            return None
        for chn, v in (cmap or {}).items():
            out[chn] = {"number": v.get("number")}
    cache_set("coverage", key, out, ttl=_COVERAGE_TTL, max_entries=300)
    return out


def _update_frequency(catalog: dict) -> dict:
    """Estima cada cuánto publica la fuente, SIN requests extra (usa solo lo ya traído
    por el catálogo). Nivel preciso: mediana de gaps entre `publishedAt` (MangaDex expone
    fecha en su feed) de los últimos capítulos → `confidence:'high'`. Nivel barato (siempre
    disponible, Suwayomi normalmente no da fecha fiable): densidad de números de capítulo
    conocidos como proxy → `confidence:'low'`."""
    dated = sorted(v["publishedAt"] for v in catalog.values() if v.get("publishedAt"))
    if len(dated) >= 3:
        try:
            import datetime
            ts = sorted(datetime.datetime.fromisoformat(d.replace("Z", "+00:00")).timestamp()
                        for d in dated[-15:])
            gaps = [b - a for a, b in zip(ts, ts[1:]) if b > a]
            if gaps:
                return {"medianDaysBetweenChapters": round(float(np.median(gaps)) / 86400, 1),
                        "lastChapterAt": dated[-1], "sampleSize": len(gaps), "confidence": "high"}
        except Exception:
            pass
    nums = sorted({_chnum(v.get("number")) for v in catalog.values() if v.get("number")})
    if len(nums) >= 2:
        gap = (nums[-1] - nums[0]) / max(1, len(nums) - 1)
        return {"medianDaysBetweenChapters": None, "chapterDensity": round(gap, 2),
                "sampleSize": len(nums), "confidence": "low"}
    return {"medianDaysBetweenChapters": None, "sampleSize": len(nums), "confidence": "unknown"}


def _score_candidate_cached_only(c):
    """Como `_score_candidate` pero SIN calcular si no está cacheado (no baja páginas) —
    para que /coverage sea rápido con TODOS los candidatos: la calidad la calcula la
    pestaña Versiones (que ya se abre normalmente antes/junto a la de cobertura) y aquí
    solo se reutiliza si ya existe."""
    qkey = f"v3|{c.get('sourceId')}|{c.get('id')}|{(c.get('sourceLang') or '').lower()}"
    return cache_get("quality", qkey, _QUALITY_TTL)


def _run_coverage(task_id: str, title: str, al_id, source_ids, cur_lang: str = "", refresh: bool = False):
    """Cobertura de capítulos por fuente (qué capítulos tiene cada una) + completitud +
    frecuencia de actualización + mapa de asignación actual — alimenta el grid de cobertura
    y el flujo de asignación por rango.

    NO usa el tope `_RANK_CAP` de Versiones (eso excluía fuentes reales que no entraban en
    el top 15 por parecido de título, p.ej. "Platinum Lily Scan"). En su lugar aplica
    `_COVERAGE_MATCH_MIN` (85%, a pedido explícito del usuario): un match de título por
    debajo de eso casi siempre es una obra DISTINTA (con su propio conteo de capítulos, a
    veces absurdamente alto) — dejarla pasar solo llena la pestaña Capítulos de ruido.
    Versiones sigue mostrando TODO lo que pasa `_FUZZY_MIN` (60%) sin este filtro extra —
    ahí el usuario ya tiene "Leer muestra"/comparar A|B para juzgar candidatos dudosos."""
    try:
        variants = title_variants(title, int(al_id) if al_id else None)
        # `degraded` marca que este barrido NO vio todo lo que hay. Un barrido a medias es
        # indistinguible de "sólo existen estas fuentes", y antes se persistía 30 días como si
        # fuera la verdad: MEDIDO, Amayo pasó de 54 fuentes (10 ES) a 34 (2 ES) porque Suwayomi se
        # murió a mitad, y la caché sirvió el resultado mutilado a la UI como definitivo.
        degraded: list = []
        disc: dict = {}
        try:
            candidates = _candidates_cached(title, al_id, variants, source_ids, refresh=refresh,
                                            stats=disc)
        except Exception as e:
            print(f"[transplant] descubrimiento de fuentes FALLÓ para {title!r} "
                  f"({type(e).__name__}: {str(e)[:70]})", file=sys.stderr, flush=True)
            candidates = []
            degraded.append(f"descubrimiento de fuentes: {type(e).__name__}")
        if disc.get("failed"):
            degraded.append(f"{disc['failed']} de {disc.get('tasks')} búsquedas no respondieron")
        try:
            candidates += _md_candidates(variants, cur_lang)
        except Exception as e:
            print(f"[transplant] candidatos de MangaDex FALLARON para {title!r} "
                  f"({type(e).__name__}: {str(e)[:70]})", file=sys.stderr, flush=True)
            degraded.append(f"candidatos MangaDex: {type(e).__name__}")
        candidates = [c for c in candidates if (c.get("match") or 0) >= _COVERAGE_MATCH_MIN]
        _set_status(task_id, phase="coverage", covered=0, coverTotal=len(candidates))
        sources_out = []
        all_chapters = set()
        for i, c in enumerate(candidates):
            cand = {"sourceId": c["sourceId"], "mangaId": c["id"], "sourceLang": c["sourceLang"]}
            catalog = _source_catalog(cand, title, refresh=refresh)
            _set_status(task_id, phase="coverage", covered=i + 1, coverTotal=len(candidates))
            if catalog is None:
                # No consultable: la fuente se cae de la lista, pero eso NO es un dato sobre ella.
                degraded.append(f"{c.get('sourceName')}: no consultable")
                continue
            if not catalog:
                continue
            chapters = sorted(catalog.keys(), key=_chnum)
            all_chapters.update(chapters)
            sources_out.append({
                "sourceKind": _source_kind(cand), "sourceId": c["sourceId"], "mangaId": c["id"],
                "sourceName": c["sourceName"], "sourceLang": c["sourceLang"], "match": c.get("match"),
                "chapters": chapters, "count": len(chapters),
                "quality": _score_candidate_cached_only(c),
                "updateFrequency": _update_frequency(catalog),
            })
        local_files = _local_chapter_files(title)
        if local_files:
            local_chapters = sorted(local_files.keys(), key=_chnum)
            all_chapters.update(local_chapters)
            sources_out.append({
                "sourceKind": "local", "sourceId": "__local__", "mangaId": "__local__",
                "sourceName": "Biblioteca local", "sourceLang": "", "match": None,
                "chapters": local_chapters, "count": len(local_chapters),
                "quality": _score_local(title), "updateFrequency": {"confidence": "unknown"},
            })
        total_known = max((_chnum(x) for x in all_chapters), default=0)
        for s in sources_out:
            s["completeness"] = round(len(s["chapters"]) / total_known, 3) if total_known else None
        # Orden: lo ya puntuado por calidad primero (desc), luego lo no puntuado por parecido
        # de título (desc) — lo confiable sube, pero nada desaparece de la lista.
        sources_out.sort(key=lambda s: (1, s["quality"]["score"]) if s.get("quality") else (0, s.get("match") or 0), reverse=True)
        # Persiste el RESULTADO de cobertura en disco para que reabrir el manga lo muestre al
        # instante sin recalcular (el usuario solo re-mide con "Recalcular"). Clave por
        # título+idioma; TTL largo (30 días) — es un mapa de "qué fuente tiene qué capítulo",
        # cambia poco; "Recalcular" (refresh=True) lo sobrescribe.
        # SÓLO se persiste un barrido COMPLETO. Si algo no se pudo consultar, este resultado no es
        # "la lista de fuentes de este manga", es "lo que alcancé a ver hoy" — y guardarlo pisaría
        # un barrido bueno anterior y lo serviría 30 días. Ante la duda, se conserva lo que había:
        # una caché vieja y completa es MUCHO mejor que una nueva y mutilada.
        if degraded:
            print(f"[transplant] cobertura DEGRADADA de {title!r} ({len(sources_out)} fuentes "
                  f"vistas; {len(degraded)} problemas: {'; '.join(degraded[:5])}) — NO se cachea",
                  file=sys.stderr, flush=True)
        else:
            cache_set("coverage_result", f"{title}||{cur_lang}",
                      {"sources": sources_out, "totalKnownChapters": total_known},
                      ttl=_COVERAGE_RESULT_TTL, max_entries=200)
        _set_status(task_id, status="done", phase="ready", variants=variants,
                    totalKnownChapters=total_known, sources=sources_out,
                    partial=bool(degraded), degraded=degraded[:10],
                    assigned=versions_db.get_assigned_map(title))
    except Exception as e:
        _set_status(task_id, status="error", phase="error", error=str(e))


@transplant_bp.route("/coverage", methods=["POST"])
def coverage():
    """Cobertura de capítulos por fuente candidata (qué caps tiene cada una, completitud,
    frecuencia de actualización) + el mapa de asignación actual por capítulo. Devuelve
    task_id de inmediato; la UI sondea /status?task_id=. Body: {title, anilistId?,
    sourceIds?[], currentLang?, refresh?}. `currentLang` debe coincidir con lo que use
    Versiones para el mismo título — así comparten la misma entrada de caché "versions" y
    Cobertura ve EXACTAMENTE el mismo conjunto de fuentes ya vetadas."""
    body = request.get_json(silent=True) or {}
    title = (body.get("title") or "").strip()
    if not title:
        return jsonify({"error": "title required"}), 400
    al_id = body.get("anilistId")
    source_ids = body.get("sourceIds") or None
    cur_lang = (body.get("currentLang") or "").strip()
    refresh = bool(body.get("refresh"))
    task_id = build_task_id(title, "coverage", "transplant")
    _set_status(task_id, status="discovering", title=title, phase="start")
    threading.Thread(target=_run_coverage, args=(task_id, title, al_id, source_ids, cur_lang, refresh), daemon=True).start()
    return jsonify({"task_id": task_id, "title": title})


@transplant_bp.route("/coverage_cached", methods=["GET"])
def coverage_cached():
    """Lectura INSTANTÁNEA (sin recalcular) del último resultado de cobertura persistido para
    un título — lo llama la UI al abrir el manga para pintar el grid de una vez. Si no hay
    caché devuelve `{cached: false}` y la UI muestra el botón "Ver cobertura". El mapa de
    asignación (chapter_sources) siempre va fresco desde SQLite."""
    title = (request.args.get("title") or "").strip()
    cur_lang = (request.args.get("currentLang") or "").strip()
    if not title:
        return jsonify({"error": "title required"}), 400
    cached = cache_get("coverage_result", f"{title}||{cur_lang}", _COVERAGE_RESULT_TTL)
    if not cached:
        return jsonify({"cached": False, "assigned": versions_db.get_assigned_map(title)})
    return jsonify({
        "cached": True,
        "sources": cached.get("sources", []),
        "totalKnownChapters": cached.get("totalKnownChapters", 0),
        "assigned": versions_db.get_assigned_map(title),
    })


# ── Endpoint: discover ────────────────────────────────────────────────────────

_ES_LIST_CAP = 30          # cuántas fuentes ES ofrecer en el selector de Traducir
_ES_UNSCORED_MIN_MATCH = 0.6   # match mínimo para incluir una fuente ES SIN puntuar (anti-ruido)

def _append_unscored_es(es_ranked: list, es_cands: list) -> list:
    """Añade al final las fuentes ES que el ranking NO pudo puntuar (scrapers que fallan al
    bajar muestras) para que el usuario las VEA y pueda elegirlas igualmente — antes se
    descartaban y solo sobrevivía 1. Las puntuadas van primero (por calidad); las no
    puntuadas después, por parecido de título, y filtrando ruido de match muy bajo."""
    scored_keys = {(c["sourceId"], c["id"]) for c in es_ranked}
    seen_names = set()   # evita 10 filas del mismo scraper (varias entradas de la misma obra)
    extra = []
    for c in sorted(es_cands, key=lambda c: c.get("match", 0), reverse=True):
        key = (c["sourceId"], c["id"])
        if key in scored_keys:
            continue
        if c.get("match", 0) < _ES_UNSCORED_MIN_MATCH:
            continue
        nkey = (c.get("sourceName") or "").lower()
        if nkey in seen_names:
            continue
        seen_names.add(nkey)
        extra.append(dict(c, quality=None, unscored=True))
    return es_ranked + extra


def _run_discover(task_id: str, title: str, al_id, source_ids, art_local: bool = False):
    """Descubre + rankea en un hilo; escribe el progreso y el resultado final en el
    estado (la UI sondea /status). Cachea en `.transplant_meta.json`.
    `art_local=True` (manga importado por CBZ/CBR): el ARTE es el contenido ya
    local, así que solo se busca/rankea la fuente ES — no se descubre ni rankea
    arte remoto."""
    try:
        _set_status(task_id, status="discovering", title=title, phase="variants")
        variants = title_variants(title, int(al_id) if al_id else None)
        _set_status(task_id, phase="searching", variants=variants, searched=0, searchTotal=0)

        candidates = _candidates_cached(
            title, al_id, variants, source_ids,
            on_progress=lambda d, t: _set_status(task_id, phase="searching", searched=d, searchTotal=t),
        )

        if art_local:
            art_best = {"local": True}
            es_cands = [c for c in candidates if (c.get("sourceLang") or "").lower() in _ES_LANGS]
            es_ranked = []
            if es_cands:
                _set_status(task_id, phase="ranking", rankTotal=len(es_cands), ranked=0)
                es_ranked = _rank(es_cands, on_progress=lambda d, t: _set_status(task_id, phase="ranking", ranked=d, rankTotal=t))
                es_ranked = _append_unscored_es(es_ranked, es_cands)
            es_best = es_ranked[0] if es_ranked else None
            meta = _read_meta(title)
            meta.update({
                "title": title, "variants": variants,
                "art": art_best,
                "es": {"sourceId": es_best["sourceId"], "mangaId": es_best["id"],
                       "sourceName": es_best["sourceName"], "sourceLang": es_best["sourceLang"]} if es_best else None,
                "discovered_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            })
            _write_meta(title, meta)
            # Tomos pendientes (CBZ que abarcan varios capítulos): ahora que hay
            # fuente ES, intentar repartirlos en capítulos reales automáticamente.
            vol_result = {"resolved": [], "unresolved": []}
            if es_best:
                try:
                    vol_result = _resolve_pending_volumes(title, es_best["id"])
                except Exception:
                    pass
            _set_status(task_id, status="done", phase=("ranked" if es_best else "empty"), variants=variants,
                        art={"candidates": [], "best": art_best},
                        es={"candidates": es_ranked[:_ES_LIST_CAP], "best": es_best},
                        resolvedVolumes=vol_result["resolved"], unresolvedVolumes=vol_result["unresolved"])
            return

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
        es_ranked = _append_unscored_es(es_ranked, es_cands)

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
                    es={"candidates": es_ranked[:_ES_LIST_CAP], "best": es_best})
    except Exception as e:
        _set_status(task_id, status="error", phase="error", error=str(e))


@transplant_bp.route("/discover", methods=["POST"])
def discover():
    """Lanza el descubrimiento en segundo plano y devuelve task_id de inmediato; la
    UI sondea /status?task_id= para ver progreso en vivo y el resultado final.
    Body: {title, anilistId?, sourceIds?[]} — sourceIds restringe el barrido."""
    if not ensure_suwayomi():
        return jsonify({"error": "Suwayomi offline"}), 503
    body = request.get_json(silent=True) or {}
    title = (body.get("title") or "").strip()
    al_id = body.get("anilistId")
    source_ids = body.get("sourceIds") or None
    art_local = bool(body.get("artLocal"))
    if not title:
        return jsonify({"error": "title required"}), 400

    task_id = build_task_id(title, "discover", "transplant")
    _set_status(task_id, status="discovering", title=title, phase="start")
    threading.Thread(target=_run_discover, args=(task_id, title, al_id, source_ids, art_local), daemon=True).start()
    return jsonify({"task_id": task_id, "title": title})


_VERSIONS_TTL = 86400   # 24h: el ranking de versiones se cachea en DISCO (caro: fan-out + descargas)


def _versions_cache_key(title, al_id, source_ids, cur_lang):
    return f"{title}|{al_id or ''}|{cur_lang or ''}|{','.join(map(str, source_ids or []))}"


def _compute_versions_payload(title: str, al_id, source_ids, cur_lang: str, refresh: bool, task_id: str = None):
    """Descubre TODAS las versiones del título en las fuentes y las rankea por calidad de
    imagen, agrupadas por idioma — el motor compartido detrás de la pestaña 'Versiones'.
    Extraído de `_run_versions` para que OTROS consumidores (Cobertura) usen EXACTAMENTE
    el mismo conjunto de candidatos ya vetado/capado (`_RANK_CAP`) y rankeado, en vez de
    volver a descubrir por su cuenta (eso permitía que apareciera algo que Versiones jamás
    mostraría). `task_id` es opcional — si se da, reporta progreso vía `_set_status` igual
    que antes; si no, se calcula en silencio.
    Devuelve (payload, empty) — `empty=True` significa "ni un candidato encontrado" (fase
    'empty' en Versiones), a diferencia de "el ranking quedó vacío tras puntuar"."""
    if task_id:
        _set_status(task_id, status="discovering", title=title, phase="variants")
    variants = title_variants(title, int(al_id) if al_id else None)
    if task_id:
        _set_status(task_id, phase="searching", variants=variants, searched=0, searchTotal=0)
    try:
        candidates = _candidates_cached(
            title, al_id, variants, source_ids, refresh=refresh,
            on_progress=(lambda d, t: _set_status(task_id, phase="searching", searched=d, searchTotal=t)) if task_id else None,
        )
    except Exception:
        candidates = []   # Suwayomi caído: aún podemos ofrecer MangaDex nativo
    # MangaDex NATIVO (su propia API): se añade al barrido aunque no esté su extensión
    # instalada en Suwayomi (o aunque Suwayomi esté offline). Fluye por el mismo ranking
    # (_score_candidate lo enruta a _score_md).
    try:
        candidates += _md_candidates(variants, cur_lang)
    except Exception:
        pass
    local = _score_local(title)
    if not candidates:
        return {"variants": variants, "versions": [], "byLang": {}, "local": local, "currentLang": cur_lang}, True
    cur = (cur_lang or "").lower()
    # Selección a rankear: MISMO criterio de match de título que Cobertura
    # (match >= _COVERAGE_MATCH_MIN) en lugar del viejo tope top-_RANK_CAP por
    # parecido — ese tope dejaba FUERA fuentes legítimas que simplemente no
    # entraban en el top 15 (el usuario reportó "no encuentra todas las fuentes").
    # Ahora aparece toda fuente con parecido de título suficiente; el coste (baja
    # 2-3 páginas por candidato) se acota con un tope duro de seguridad.
    by_match = sorted(candidates, key=lambda c: c.get("match", 0), reverse=True)
    to_rank = [c for c in by_match if (c.get("match") or 0) >= _COVERAGE_MATCH_MIN][:_RANK_HARD_CAP]
    seen = {(c["sourceId"], c["id"]) for c in to_rank}
    for c in candidates:
        # incluir SIEMPRE: (a) todas las versiones del idioma actual del usuario (su caso
        # estrella) y (b) TODAS las de MangaDex nativo (son pocas y el usuario las pidió
        # explícitamente), aunque queden fuera del top _RANK_CAP por parecido de título.
        if (c["sourceId"], c["id"]) in seen:
            continue
        is_cur = cur and (c.get("sourceLang") or "").lower() == cur
        is_md = str(c.get("sourceId")) == "__mangadex__"
        if is_cur or is_md:
            to_rank.append(c); seen.add((c["sourceId"], c["id"]))
    if task_id:
        _set_status(task_id, phase="ranking", rankTotal=len(to_rank), ranked=0)
    ranked = _rank(to_rank, on_progress=(lambda d, t: _set_status(task_id, phase="ranking", ranked=d, rankTotal=t)) if task_id else None,
                   use_cache=not refresh)

    def _slim(c):
        return {"sourceId": c["sourceId"], "mangaId": c["id"],
                "sourceName": c["sourceName"], "sourceLang": c["sourceLang"],
                "match": c.get("match"), "quality": c["quality"]}
    # `ranked` ya viene desc por score global → cada grupo de idioma hereda el orden
    by_lang: dict = {}
    for c in ranked:
        by_lang.setdefault((c.get("sourceLang") or "?").lower(), []).append(_slim(c))
    payload = {"variants": variants, "versions": [_slim(c) for c in ranked],
               "byLang": by_lang, "local": local, "currentLang": cur_lang}
    return payload, False


def _run_versions(task_id: str, title: str, al_id, source_ids, cur_lang: str = "", refresh: bool = False):
    """Wrapper de `/versions`: caché en DISCO (24h) → reabrir la pestaña es instantáneo y no
    re-barre ~185 fuentes ni re-descarga páginas de muestra (menos rate-limits). `refresh`
    la salta. El descubrimiento/ranking real vive en `_compute_versions_payload`."""
    if not refresh:
        cached = cache_get("versions", _versions_cache_key(title, al_id, source_ids, cur_lang), _VERSIONS_TTL)
        if cached:
            _set_status(task_id, status="done", phase="ranked", cached=True, **cached)
            return
    try:
        payload, empty = _compute_versions_payload(title, al_id, source_ids, cur_lang, refresh, task_id=task_id)
        if empty:
            _set_status(task_id, status="done", phase="empty", **payload)
            return
        _set_status(task_id, status="done", phase="ranked", **payload)
        # cachea en disco solo rankings reales (no vacíos/errores), TTL 24h
        cache_set("versions", _versions_cache_key(title, al_id, source_ids, cur_lang),
                  payload, ttl=_VERSIONS_TTL, max_entries=120)
    except Exception as e:
        _set_status(task_id, status="error", phase="error", error=str(e))


@transplant_bp.route("/versions", methods=["POST"])
def versions():
    """Descubre y rankea TODAS las versiones del título por calidad de imagen,
    agrupadas por idioma (pestaña 'Versiones'). Devuelve task_id de inmediato; la UI
    sondea /status?task_id=. Body: {title, anilistId?, sourceIds?[], currentLang?}."""
    if not ensure_suwayomi():
        return jsonify({"error": "Suwayomi offline"}), 503
    body = request.get_json(silent=True) or {}
    title = (body.get("title") or "").strip()
    if not title:
        return jsonify({"error": "title required"}), 400
    al_id = body.get("anilistId")
    source_ids = body.get("sourceIds") or None
    cur_lang = (body.get("currentLang") or "").strip()
    refresh = bool(body.get("refresh"))   # "Buscar de nuevo" salta la caché
    task_id = build_task_id(title, "versions", "transplant")
    _set_status(task_id, status="discovering", title=title, phase="start")
    threading.Thread(target=_run_versions, args=(task_id, title, al_id, source_ids, cur_lang, refresh), daemon=True).start()
    return jsonify({"task_id": task_id, "title": title})


def _run_download_version(task_id: str, title: str, manga_id):
    """Descarga en la biblioteca los capítulos de una versión (fuente Suwayomi) que el
    usuario AÚN NO tiene localmente. NO sobrescribe lo existente (no destructivo): solo
    rellena los capítulos que faltan. Progreso vía /status (chapterDone/chapterTotal)."""
    try:
        _set_status(task_id, status="downloading", title=title, phase="list", chapterDone=0, chapterTotal=0)
        cmap = _chapters_map(int(manga_id))
        if not cmap:
            _set_status(task_id, status="done", phase="empty", chapterDone=0, chapterTotal=0, downloaded=0)
            return
        have = set(_local_chapter_files(title).keys())
        todo = sorted([(chn, info) for chn, info in cmap.items() if chn not in have],
                      key=lambda x: _chnum(x[0]))
        folder = Path(manga_dir()) / title
        total = len(todo)
        _set_status(task_id, phase="downloading", chapterDone=0, chapterTotal=total)
        done = 0
        for chn, info in todo:
            _set_status(task_id, phase="downloading", chapter=chn, chapterDone=done, chapterTotal=total)
            try:
                urls = _chapter_page_urls(info["id"])
                if urls:
                    _download_chapter_to(folder, urls, _chapter_file_prefix(chn))
            except Exception:
                pass
            done += 1
            _set_status(task_id, phase="downloading", chapterDone=done, chapterTotal=total)
        _set_status(task_id, status="done", phase="done", chapterDone=done, chapterTotal=total, downloaded=done)
    except Exception as e:
        _set_status(task_id, status="error", phase="error", error=str(e))


@transplant_bp.route("/download_version", methods=["POST"])
def download_version():
    """Descarga los capítulos FALTANTES de una versión en la biblioteca (no sobrescribe lo
    que ya tienes). Devuelve task_id; la UI sondea /status. Body: {title, source:{mangaId}}."""
    if not ensure_suwayomi():
        return jsonify({"error": "Suwayomi offline"}), 503
    body = request.get_json(silent=True) or {}
    title = (body.get("title") or "").strip()
    manga_id = (body.get("source") or {}).get("mangaId")
    if not title or not manga_id:
        return jsonify({"error": "title and source.mangaId required"}), 400
    task_id = build_task_id(title, "dlversion", "transplant")
    _set_status(task_id, status="downloading", title=title, phase="start")
    threading.Thread(target=_run_download_version, args=(task_id, title, manga_id), daemon=True).start()
    return jsonify({"task_id": task_id, "title": title})


def _resolve_assign_targets(title, rng, chapters, source) -> list:
    """Lista de chapter_norm a los que aplica una asignación/limpieza, según `rng`
    (`"all"` | `{from?,to?}`) o `chapters` (lista explícita). Cuando hay `source`, un
    rango/"all" se acota a lo que ESA fuente realmente tiene (vía `_source_catalog`);
    al limpiar (`source=None`) sin lista explícita, se acota a lo YA asignado en
    `chapter_sources` (nada que limpiar si no había asignación)."""
    if chapters:
        return [x for x in (normalize_chapter(c) for c in chapters) if x]
    catalog_keys = None
    if source:
        catalog = _source_catalog(source, title)
        # None = no se pudo preguntar a la fuente. Acotar el rango con un catálogo vacío daría
        # "cero capítulos que asignar" y el usuario vería una asignación que no pasó nada, o peor,
        # un rango recortado en silencio. Un fallo de red no puede parecerse a "no los tiene".
        if catalog is None:
            raise RuntimeError("no se pudo consultar el catálogo de esa fuente "
                               "(¿Suwayomi caída o fuente bloqueada?) — inténtalo de nuevo")
        catalog_keys = set(catalog.keys())
    else:
        catalog_keys = set(versions_db.get_assigned_map(title).keys())
    if rng == "all" or (isinstance(rng, dict) and rng.get("all")):
        return sorted(catalog_keys, key=_chnum)
    if isinstance(rng, dict):
        lo = _chnum(rng.get("from")) if rng.get("from") not in (None, "") else float("-inf")
        hi = _chnum(rng.get("to")) if rng.get("to") not in (None, "") else float("inf")
        return sorted((k for k in catalog_keys if lo <= _chnum(k) <= hi), key=_chnum)
    return []


def _source_row(source: dict) -> dict:
    return {
        "sourceKind": _source_kind(source), "sourceId": source.get("sourceId"),
        "mangaId": source.get("mangaId", source.get("id")),
        "sourceName": source.get("sourceName"), "sourceLang": source.get("sourceLang"),
    }


def _do_assign(title: str, rng, chapters, source) -> dict:
    """Núcleo compartido de `assign_source`/`set_primary`: upsert (o limpia) filas en
    `chapter_sources` para el conjunto de capítulos resuelto. NO borra archivos — solo
    decide de dónde se descarga lo que falte. Cuando el rango cubre TODA la fuente
    ("all"), también espeja `recommended_source` en `.source_meta.json` (compatibilidad
    con el código legado que aún lo lee, p.ej. el badge ★ de MangaCard)."""
    target = _resolve_assign_targets(title, rng, chapters, source)
    if source:
        written = versions_db.set_assignment_range(title, target, _source_row(source), assigned_by="manual")
    else:
        written = versions_db.set_assignment_range(title, target, None, assigned_by="manual")
    skipped = [c for c in (chapters or []) if normalize_chapter(c) and normalize_chapter(c) not in written]

    covers_all = rng == "all" or (isinstance(rng, dict) and rng.get("all"))
    if covers_all:
        meta = _read_source_meta(title)
        if source:
            meta["recommended_source"] = {
                "sourceId": source.get("sourceId"), "mangaId": source.get("mangaId", source.get("id")),
                "sourceName": source.get("sourceName"), "sourceLang": source.get("sourceLang"),
                "quality": source.get("quality"),
                "set_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
        else:
            meta.pop("recommended_source", None)
        _write_source_meta(title, meta)

    return {"ok": True, "assigned": written, "skipped": skipped,
            "assignedMap": versions_db.get_assigned_map(title)}


@transplant_bp.route("/assign_source", methods=["POST"])
def assign_source():
    """Asigna (o limpia) la fuente de descarga para un rango/lista de capítulos —
    generaliza 'una fuente para todo el manga' (`set_primary`) a 'una fuente por
    capítulo'. NO borra archivos ya descargados (no destructivo): solo decide de
    dónde se completará lo que falte. Body: {title, range:{from?,to?,all?}|chapters:
    [...], source:{sourceKind,sourceId?,mangaId,sourceName?,sourceLang?}|null}."""
    body = request.get_json(silent=True) or {}
    title = (body.get("title") or "").strip()
    if not title:
        return jsonify({"error": "title required"}), 400
    rng = body.get("range")
    chapters = body.get("chapters")
    if not rng and not chapters:
        return jsonify({"error": "range or chapters required"}), 400
    return jsonify(_do_assign(title, rng, chapters, body.get("source")))


@transplant_bp.route("/set_primary", methods=["POST"])
def set_primary():
    """Fija la versión RECOMENDADA de TODO un manga (solo etiqueta, NO destructivo).
    Atajo de compatibilidad de `assign_source` con `range:'all'` — se conserva la URL
    y la forma de respuesta para no romper llamadores existentes. `source=null` la
    quita. Body: {title, source?}."""
    body = request.get_json(silent=True) or {}
    title = (body.get("title") or "").strip()
    if not title:
        return jsonify({"error": "title required"}), 400
    _do_assign(title, "all", None, body.get("source"))
    return jsonify({"ok": True, "recommended_source": _read_source_meta(title).get("recommended_source")})


def _legacy_source_row(title: str):
    """Fuente única legada (recommended_source o sourceId/mangaId top-level) en forma de
    `source` genérico — usada como fallback cuando un capítulo no tiene asignación propia
    en `chapter_sources`."""
    meta = _read_source_meta(title)
    rec = meta.get("recommended_source")
    if rec and rec.get("sourceId") and rec.get("mangaId"):
        return {"sourceKind": _source_kind(rec), "sourceId": rec["sourceId"], "mangaId": rec["mangaId"],
                "sourceName": rec.get("sourceName"), "sourceLang": rec.get("sourceLang")}
    if meta.get("sourceId") and meta.get("mangaId"):
        return {"sourceKind": _source_kind(meta), "sourceId": meta["sourceId"], "mangaId": meta["mangaId"],
                "sourceName": meta.get("sourceName"), "sourceLang": meta.get("sourceLang")}
    return None


def _cand_key(c) -> tuple:
    return (str(c.get("sourceId")), str(c.get("mangaId", c.get("id"))))


@transplant_bp.route("/assigned_map", methods=["GET"])
def assigned_map():
    """Lectura LIVIANA (sin hilo/polling: es una consulta SQLite instantánea) del mapa de
    asignación por capítulo de un manga — `{chapter_norm: {sourceKind,sourceId,mangaId,
    sourceName,sourceLang,assignedAt,assignedBy}}`. Se llama SIEMPRE que se abre un manga
    (no solo cuando el usuario pulsa 'Ver cobertura') para que la selección persista de
    verdad en la UI entre sesiones/reinicios — antes solo vivía en la BD, invisible hasta
    volver a barrer fuentes. Query: ?title=."""
    title = (request.args.get("title") or "").strip()
    if not title:
        return jsonify({"error": "title required"}), 400
    return jsonify({"assigned": versions_db.get_assigned_map(title)})


# ── Detección de fuente mejor/más nueva (on-demand — el proyecto no tiene cron) ────
_FRESHNESS_QUALITY_DELTA = 0.08   # diferencia mínima de score para sugerir "mejor calidad"


def _run_check_freshness(task_id: str, title: str, al_id, cur_lang: str = ""):
    """Usa los candidatos descubiertos con match de título ≥ `_COVERAGE_MATCH_MIN` (no el
    top `_RANK_CAP` de Versiones — ver comentario largo en `_run_coverage`). `mangaId` se
    alía a `id` para que `_candidate_chapter_urls`/`_source_row` funcionen."""
    try:
        _set_status(task_id, status="discovering", title=title, phase="variants")
        variants = title_variants(title, int(al_id) if al_id else None)
        try:
            candidates = _candidates_cached(title, al_id, variants, None, refresh=True)
        except Exception:
            candidates = []
        try:
            candidates += _md_candidates(variants, cur_lang)
        except Exception:
            pass
        for c in candidates:
            c["mangaId"] = c["id"]
        candidates = [c for c in candidates if (c.get("match") or 0) >= _COVERAGE_MATCH_MIN]
        if not candidates:
            _set_status(task_id, status="done", phase="ready", suggestions=[])
            return

        _set_status(task_id, phase="coverage", covered=0, coverTotal=len(candidates))
        catalogs = {}
        for i, c in enumerate(candidates):
            # None (no consultable) se guarda como {} SOLO en este dict en memoria, que muere con
            # la petición: aquí son sugerencias, no una caché en disco que envenene 30 días.
            catalogs[_cand_key(c)] = _source_catalog(c, title, refresh=True) or {}
            _set_status(task_id, phase="coverage", covered=i + 1, coverTotal=len(candidates))

        assigned_map = versions_db.get_assigned_map(title)
        legacy_src = _legacy_source_row(title)
        have = set(_local_chapter_files(title).keys())

        # capítulos conocidos por alguna fuente pero que el usuario todavía no tiene EN NINGUNA
        all_known = set()
        for c in candidates:
            all_known.update(catalogs[_cand_key(c)].keys())
        suggestions = []
        for chn in sorted(all_known - have, key=_chnum):
            have_it = [c for c in candidates if chn in catalogs[_cand_key(c)]]
            if have_it:
                best = have_it[0]
                suggestions.append({"chapter": chn, "reason": "new",
                                     "betterSource": _source_row(best), "currentSource": None, "delta": None})

        # capítulos YA descargados: ¿hay una fuente con mejor calidad para ese mismo capítulo?
        for chn in sorted(have, key=_chnum):
            assign = assigned_map.get(chn) or legacy_src
            if not assign:
                continue
            have_it = [c for c in candidates if chn in catalogs[_cand_key(c)]]
            if not have_it:
                continue
            cur = next((c for c in have_it
                        if str(c.get("sourceId")) == str(assign.get("sourceId"))
                        and str(c.get("mangaId", c.get("id"))) == str(assign.get("mangaId"))), None)
            cur_q = _score_candidate(cur, use_cache=True) if cur else None
            cur_score = (cur_q or {}).get("score", 0)
            scored = [(c, _score_candidate(c, use_cache=True)) for c in have_it if _cand_key(c) != (_cand_key(cur) if cur else None)]
            scored = [(c, q) for c, q in scored if q]
            scored.sort(key=lambda cq: cq[1]["score"], reverse=True)
            if scored:
                best_c, best_q = scored[0]
                if cur_score <= 0 or (best_q["score"] - cur_score) / max(cur_score, 1) >= _FRESHNESS_QUALITY_DELTA:
                    suggestions.append({"chapter": chn, "reason": "better_quality",
                                        "betterSource": _source_row(best_c), "currentSource": assign,
                                        "delta": round(best_q["score"] - cur_score, 1)})

        _set_status(task_id, status="done", phase="ready", suggestions=suggestions)
    except Exception as e:
        _set_status(task_id, status="error", phase="error", error=str(e))


@transplant_bp.route("/check_freshness", methods=["POST"])
def check_freshness():
    """On-demand (sin cron: el proyecto no tiene scheduler): refresca cobertura/candidatos
    y sugiere capítulos NUEVOS en otra fuente que aún no se tienen, o de MEJOR calidad que
    la fuente actualmente asignada. No descarga ni asigna nada por sí solo — devuelve
    sugerencias que el usuario aplica llamando a `assign_source` + descarga de ese capítulo.
    Body: {title, anilistId?, currentLang?}."""
    if not ensure_suwayomi():
        return jsonify({"error": "Suwayomi offline"}), 503
    body = request.get_json(silent=True) or {}
    title = (body.get("title") or "").strip()
    if not title:
        return jsonify({"error": "title required"}), 400
    al_id = body.get("anilistId")
    cur_lang = (body.get("currentLang") or "").strip()
    task_id = build_task_id(title, "freshness", "transplant")
    _set_status(task_id, status="discovering", title=title, phase="start")
    threading.Thread(target=_run_check_freshness, args=(task_id, title, al_id, cur_lang), daemon=True).start()
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


@transplant_bp.route("/resolve_volumes", methods=["POST"])
def resolve_volumes():
    """Reintenta el reparto automático de tomos pendientes (por si el primer
    intento falló porque la fuente ES aún no tenía todos los capítulos, etc.)."""
    body = request.get_json(silent=True) or {}
    title = (body.get("title") or "").strip()
    if not title:
        return jsonify({"error": "title required"}), 400
    meta = _read_meta(title)
    es = meta.get("es")
    if not es:
        return jsonify({"error": "no hay fuente ES elegida; ejecuta /discover primero"}), 400
    if not ensure_suwayomi():
        return jsonify({"error": "Suwayomi offline"}), 503
    return jsonify(_resolve_pending_volumes(title, es["mangaId"]))


@transplant_bp.route("/resolve_volume_manual", methods=["POST"])
def resolve_volume_manual():
    """Reparto manual de un tomo cuando el automático no cuadra: el usuario da
    explícitamente cuántas páginas tiene cada capítulo real (en orden).
    Body: {title, prefix, pageCounts: [{chapter, pages}, ...]}"""
    body = request.get_json(silent=True) or {}
    title = (body.get("title") or "").strip()
    prefix = (body.get("prefix") or "").strip()
    page_counts = body.get("pageCounts")
    if not title or not prefix or not isinstance(page_counts, list) or not page_counts:
        return jsonify({"error": "title, prefix y pageCounts son obligatorios"}), 400
    pending = _pending_volumes(title)
    vol = next((v for v in pending if v["prefix"] == prefix), None)
    if not vol:
        return jsonify({"error": "tomo no encontrado o ya repartido"}), 404
    try:
        chapter_pages = [(normalize_chapter(p["chapter"]), int(p["pages"])) for p in page_counts]
    except (KeyError, TypeError, ValueError):
        return jsonify({"error": "pageCounts inválido"}), 400

    # Aparta TODOS los pendientes (no solo este) antes de repartir: el rango que el
    # usuario declaró a mano puede coincidir con el placeholder de otro tomo aún sin
    # resolver (mismo riesgo que en _resolve_pending_volumes, ver _stage_volume).
    staged = {v["prefix"]: _stage_volume(title, v) for v in pending}
    others = [v for v in pending if v["prefix"] != prefix]
    res = _split_volume(title, vol, chapter_pages, source_prefix=staged[prefix])
    for v in others:
        original_prefix = v["prefix"]
        v["prefix"] = _unstage_volume(title, staged[original_prefix], original_prefix)
    if not res["ok"]:
        _unstage_volume(title, staged[prefix], prefix)
        return jsonify({"error": res["error"]}), 400
    _write_pending_volumes(title, others)
    return jsonify({"ok": True, "chapters": [c for c, _ in chapter_pages]})


@transplant_bp.route("/chapters/<path:title>", methods=["GET"])
def list_chapters(title):
    """Capítulos traducibles (disponibles en AMBAS fuentes elegidas) con su estado
    (hecho/falló/pendiente). Para la lista de la pestaña Traducir."""
    title = title.strip()
    meta = _read_meta(title)
    art, es = meta.get("art"), meta.get("es")
    pending = _pending_volumes(title)
    if not art or not es:
        return jsonify({"chapters": [], "needsDiscover": True, "pendingVolumes": pending})
    if not ensure_suwayomi():
        return jsonify({"error": "Suwayomi offline"}), 503
    try:
        # Sin timeout corto aquí: el endpoint de capítulos NO está en el camino del
        # ranking. La primera vez Suwayomi necesita sincronizar capítulos desde la
        # fuente externa (fetchChapters mutation), lo que puede tardar >8s; con
        # _SAMPLE_TIMEOUT retornaba {} → common vacío → "sin capítulos".
        art_map = _local_chapters_map(title) if art.get("local") else _chapters_map(int(art["mangaId"]))
        es_map = _chapters_map(int(es["mangaId"]))
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    translated = set(meta.get("translated", []))
    failed = set(meta.get("failed", []))
    # Fusiona las partes (45.1/45.2 → "45") en AMBOS lados antes de cruzar: si no, un capítulo
    # troceado en una fuente y entero en la otra no intersecta y DESAPARECE de la lista.
    def _units(m):
        return {u["number"]: u for u in _split_part_units([{"number": k} for k in m],
                                                          lambda e: e["number"])}
    art_u, es_u = _units(art_map), _units(es_map)
    common = sorted(set(art_u) & set(es_u), key=_chnum)
    chapters = [{
        "chapter": chn,
        "status": ("done" if chn in translated else "failed" if chn in failed else "pending"),
    } for chn in common]
    return jsonify({"chapters": chapters, "art": art, "es": es,
                    "translated": sorted(translated, key=_chnum), "pendingVolumes": pending})


@transplant_bp.route("/preview/<path:title>/<chapter>", methods=["GET"])
def preview_chapter(title, chapter):
    """URLs de páginas de muestra de un capítulo (para que el usuario revise la
    calidad por sí mismo). `which=art|es` (por defecto art = lo que se leerá)."""
    title = title.strip()
    chn = normalize_chapter(chapter)
    which = (request.args.get("which") or "art").lower()
    meta = _read_meta(title)
    if which == "art" and (meta.get("art") or {}).get("local"):
        files = _local_chapter_files(title).get(chn, [])
        if not files:
            return jsonify({"pages": [], "missing": True})
        urls = [f"/uploads/{quote(title, safe='')}/{f.name}" for f in files]
        return jsonify({"pages": urls, "count": len(urls), "source": {"name": "Local", "lang": None}})
    src = meta.get("es" if which == "es" else "art")
    if not src:
        return jsonify({"error": "no source chosen"}), 400
    if not ensure_suwayomi():
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


@transplant_bp.route("/preview_candidate", methods=["GET"])
def preview_candidate():
    """Páginas de muestra de un candidato CUALQUIERA del ranking de versiones (no la
    selección confirmada): se identifica por `mangaId` de su fuente. `chapter` opcional
    — si no se da, se muestrea el primer capítulo con páginas. Para que el usuario
    juzgue la calidad a ojo antes de decidir."""
    manga_id = request.args.get("mangaId")
    if not manga_id:
        return jsonify({"error": "mangaId required"}), 400
    sid = request.args.get("sourceId") or ""
    cand = {"mangaId": manga_id, "sourceId": sid, "sourceLang": request.args.get("lang") or ""}
    # MangaDex/local no necesitan Suwayomi; solo exigimos Suwayomi online para fuentes Suwayomi.
    if sid != "__mangadex__" and str(manga_id) != "__local__" and not ensure_suwayomi():
        return jsonify({"error": "Suwayomi offline"}), 503
    try:
        _, urls = _candidate_chapter_urls(cand, request.args.get("title") or "",
                                          want_num=request.args.get("chapter"))
    except Exception as ex:
        return jsonify({"error": str(ex)}), 500
    if not urls:
        return jsonify({"pages": [], "missing": True})
    # muestra de páginas PROFUNDAS y B/N: saltamos portada/créditos (primeras páginas, casi
    # siempre a color) y descartamos las que sean a color, para que la comparación A|B caiga
    # en la MISMA página de historia entre scans distintos.
    window = _deep_slice(urls, _SAMPLE_PAGES * 2 + _COLOR_MARGIN)
    sample = (_filter_noncolor(window, _SAMPLE_PAGES * 2)
              or window[:_SAMPLE_PAGES * 2] or urls[:_SAMPLE_PAGES * 2])
    return jsonify({"pages": sample, "count": len(sample)})


@transplant_bp.route("/chapter_urls", methods=["POST"])
def chapter_urls():
    """URLs de página de UN capítulo completo de un candidato CUALQUIERA (local/MangaDex/
    Suwayomi) — no una muestra. Solo RESUELVE, no descarga: el frontend reutiliza el
    endpoint de descarga ya existente (`/api/download/download_source_chapter`, agnóstico
    de origen y ya enganchado al progreso SSE) con las URLs devueltas aquí. Es lo que
    permite que la pestaña Capítulos descargue un capítulo desde CUALQUIER fuente de
    `chapter_sources`/cobertura, no solo la fuente única legada.
    Body: {title, chapter, source:{sourceKind?, sourceId, mangaId, sourceLang?}}."""
    body = request.get_json(silent=True) or {}
    title = (body.get("title") or "").strip()
    chapter = body.get("chapter")
    source = body.get("source") or {}
    if not title or chapter is None or not source.get("mangaId"):
        return jsonify({"error": "title, chapter and source.mangaId required"}), 400
    sid = str(source.get("sourceId") or "")
    if sid != "__mangadex__" and str(source.get("mangaId")) != "__local__" and not ensure_suwayomi():
        return jsonify({"error": "Suwayomi offline"}), 503
    try:
        number, urls = _candidate_chapter_urls(source, title, want_num=chapter)
    except Exception as ex:
        return jsonify({"error": str(ex)}), 500
    return jsonify({"ok": True, "number": number, "urls": urls})


@transplant_bp.route("/compare_pages", methods=["POST"])
def compare_pages():
    """Empareja por HASH PERCEPTUAL las páginas equivalentes de DOS versiones y devuelve
    pares {left,right,sim} para el comparador A|B — así ambos lados muestran la MISMA página
    de historia (no créditos/portadas distintas, que falseaban la comparación). Combina el
    muestreo profundo + descarte de color con el emparejado por dHash.
    Body: {title, a, b, chapter?}; a/b = {sourceId, mangaId, sourceLang} (Suwayomi,
    MangaDex '__mangadex__', o local '__local__')."""
    body = request.get_json(silent=True) or {}
    title = (body.get("title") or "").strip()
    a = body.get("a") or {}
    b = body.get("b") or {}
    if not a.get("mangaId") or not b.get("mangaId"):
        return jsonify({"error": "a.mangaId and b.mangaId required"}), 400
    chapter = body.get("chapter")
    try:
        # MUESTRA DISTRIBUIDA: salvo que se pida un capítulo concreto, comparar páginas de
        # ~3 capítulos repartidos por la serie (25/50/75%) — no solo el cap 1 (suele ser el de
        # mejor calidad). Se empareja por capítulo y se combinan los pares + promedio de similitud.
        if chapter is not None:
            nums = [chapter]
        else:
            nums = _distributed_picks(_candidate_chapter_numbers(a, title)) or [None]
        per = max(2, -(-_MATCH_REF_PAGES // len(nums)))   # reparte el objetivo entre capítulos
        all_pairs, used = [], []
        for n in nums:
            num_a, urls_a = _candidate_chapter_urls(a, title, want_num=n)
            num_b, urls_b = _candidate_chapter_urls(b, title, want_num=(num_a if num_a is not None else n))
            if not urls_a or not urls_b:
                continue
            pairs = _match_pairs(urls_a, urls_b, per)
            if pairs:
                all_pairs += pairs
                used.append(num_a)
        if not all_pairs:
            return jsonify({"pairs": [], "matched": 0, "asked": _MATCH_REF_PAGES,
                            "chapter": None, "chapters": [], "sim_avg": 0, "reason": "sin-paginas"})
        sim_avg = round(sum(p["sim"] for p in all_pairs) / len(all_pairs), 3)
    except Exception as ex:
        return jsonify({"error": str(ex)}), 500
    return jsonify({"pairs": all_pairs, "matched": len(all_pairs), "asked": _MATCH_REF_PAGES,
                    "chapter": used[0] if used else None, "chapters": used, "sim_avg": sim_avg})


@transplant_bp.route("/status", methods=["GET"])
def get_status():
    tid = request.args.get("task_id")
    with _status_lock:
        if tid:
            return jsonify(transplant_status.get(tid, {}))
        return jsonify(transplant_status)


# ── Endpoint: run (descarga EN+ES, compone, escribe, limpia) ──────────────────

def _work_root() -> Path:
    """Dónde montar el staging de un capítulo (páginas EN + ES + salida compuesta: cientos de MB).

    En DISCO, nunca en `tempfile.mkdtemp()` a secas: en WSL/Arch `/tmp` es **tmpfs = RAM** (3.4G
    aquí). Un capítulo entero en RAM compite con el modelo de detección y con la JVM de Suwayomi
    —que muere por OOM sin dejar rastro, tirando la descarga ES a media faena— y al llenarse
    corrompe las escrituras a mitad ("libpng error: Write Error"). Mismo criterio que el caché de
    streaming (`stream.py:_pick_cache_root`). `TRANSPLANT_WORK_DIR` permite forzarlo."""
    override = os.environ.get("TRANSPLANT_WORK_DIR")
    root = Path(override).expanduser() if override else (DATA_ROOT / "_tp_staging")
    root.mkdir(parents=True, exist_ok=True)
    return root


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


ORIG_ART_DIR = ".original_art"   # arte pre-traducción, dentro de la carpeta del manga


def _orig_art_dir(out_dir: Path) -> Path:
    return out_dir / ORIG_ART_DIR


def original_art_files(title: str, prefix: str) -> list:
    """Páginas del arte ORIGINAL (pre-traducción) de un capítulo, si se conservaron."""
    d = _orig_art_dir(Path(manga_dir()) / title)
    if not d.is_dir():
        return []
    return sorted(p for ext in _IMG_EXT for p in d.glob(f"{prefix}_*.{ext}"))


def _preserve_original_art(out_dir: Path, prefix: str):
    """Guarda el arte original del capítulo en `.original_art/` ANTES de pisarlo.

    La traducción reemplaza las páginas EN SITIO, así que sin esto el arte original se
    PIERDE: no se puede re-traducir (se trasplantaría español sobre español), ni comparar,
    ni revertir, sin volver a descargarlo de la fuente. Sólo copia la primera vez — si ya
    hay un original guardado NO se sobrescribe (lo de dentro sería ya una traducción)."""
    dest = _orig_art_dir(out_dir)
    if any(dest.glob(f"{prefix}_*")):
        return
    cur = [p for ext in _IMG_EXT for p in out_dir.glob(f"{prefix}_*.{ext}")]
    if not cur:
        return
    dest.mkdir(parents=True, exist_ok=True)
    for p in cur:
        try:
            shutil.copy2(p, dest / p.name)
        except OSError:
            pass


def _replace_chapter_in_place(out_dir: Path, prefix: str, stage_dir: Path, page_names: list):
    """REEMPLAZO EN SITIO atómico-por-capítulo: borra las páginas existentes del
    capítulo (cualquier extensión = el idioma original) y mueve las traducidas del
    staging. Solo se llama cuando el capítulo se compuso COMPLETO.
    El arte original se conserva antes en `.original_art/` (ver _preserve_original_art)."""
    _preserve_original_art(out_dir, prefix)
    for ext in _IMG_EXT:
        for old in out_dir.glob(f"{prefix}_*.{ext}"):
            old.unlink(missing_ok=True)
    for name in page_names:
        src = stage_dir / name
        if src.exists():
            shutil.move(str(src), str(out_dir / name))


def _upscaled_folders(title: str):
    """Las dos convenciones de nombre del mirror upscaled (con '_' y con espacios)."""
    return {Path(upscaled_dir()) / title, Path(upscaled_dir()) / title.replace("_", " ")}


def _invalidate_upscaled(title: str, prefix: str):
    """El arte cambió → el capítulo escalado quedó obsoleto: se borra para que se
    re-escale. Cubre las dos convenciones de nombre del mirror upscaled."""
    for folder in _upscaled_folders(title):
        if folder.exists():
            for ext in _IMG_EXT:
                for f in folder.glob(f"{prefix}_*.{ext}"):
                    f.unlink(missing_ok=True)


def upscaled_cost(title: str, chapters=None) -> dict:
    """Cuántas páginas 4K se van a TIRAR si se traduce `chapters` (None/'all' = todo el título).

    Traducir reescribe el arte, así que `_invalidate_upscaled` borra el escalado del capítulo y
    hay que re-escalar desde cero. Escalar ANTES de traducir tira las horas de GPU, y hasta ahora
    pasaba EN SILENCIO. Esto es lo que el front usa para avisar antes de lanzar la traducción.

    Cuenta exactamente los ficheros que `_invalidate_upscaled` borraría: mismo glob, mismas
    carpetas, mismo prefijo. Si una cambia sin la otra, el aviso miente — por eso viven juntas.
    """
    prefixes = None
    if isinstance(chapters, list) and chapters:
        prefixes = {_chapter_file_prefix(c) for c in chapters}
    per: dict = {}
    for folder in _upscaled_folders(title):
        if not folder.exists():
            continue
        for ext in _IMG_EXT:
            for f in folder.glob(f"*.{ext}"):
                pre = f.name.rsplit("_", 1)[0]      # ch0036_024.jpg -> ch0036
                if prefixes is not None and pre not in prefixes:
                    continue
                per[pre] = per.get(pre, 0) + 1
    return {"pages": sum(per.values()), "chapters": len(per),
            "byChapter": dict(sorted(per.items()))}


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


RESCUE_MAX_SOURCES = 3   # fuentes ES alternativas que se prueban por capítulo con páginas en inglés.
                         # Sólo se tocan las páginas YA fallidas (1-3 de ~35), así que el coste es
                         # una descarga extra de capítulo, y sólo cuando hace falta.


def only_trailing_pages(pend, pages) -> bool:
    """¿Las páginas pendientes son EXCLUSIVAMENTE el bloque FINAL contiguo del capítulo?

    Si lo son, casi con certeza es la HOJA DE CRÉDITOS del grupo del scan EN (a veces 2-3 páginas):
    no existe en NINGÚN release ES porque el ES es de otro grupo, así que el rescate no puede
    salvarla y descargar 3 capítulos alternativos para intentarlo es puro gasto. Medido sobre el
    corpus (870 págs, 3 títulos): **17/17**: las 16 hojas de créditos caen todas en el bloque final
    y la ÚNICA página real fallida (`ch0036_024`) está en mitad del capítulo.

    NO se usa "la página es a COLOR" como condición aunque los créditos suelan serlo: medido, **5 de
    las 16 son en B/N** (Amayo ch36 p033-034, ch40 p034, ch42 p035) y exigir color las dejaría
    disparando descargas. El color corrobora, no decide.

    Función con NOMBRE y no un `if` embebido a propósito: es una decisión que se va a medir, y
    parchear el call-site en vez de la decisión es como se falseó el A/B de `bg` (E-1).

    Modo de fallo: si `pages` no viene (llamadas que no lo pasan), devuelve False -> se rescata como
    siempre. Prefiere gastar descargas antes que perder un rescate.

    COSTE ACEPTADO: una página de HISTORIA real que fuese la última del capítulo ya no se
    rescataría (se quedaría en inglés, igual que antes de existir el rescate). No se ha observado
    ni una vez en el corpus.
    """
    if not pages or not pend:
        return False
    order = list(pages)
    idx = {f: i for i, f in enumerate(order)}
    pend_i = {idx[p["file"]] for p in pend if p.get("file") in idx}
    if not pend_i or len(pend_i) != len(pend):
        return False        # alguna pendiente no está en el listado -> no arriesgar, rescatar
    return min(pend_i) + len(pend_i) == len(order)   # bloque contiguo que llega hasta el final


def _rescue_alt_es(task_id, title, chn, tmp_dir, stage, res, used_es, ci, total):
    """Rescata las páginas que quedaron en INGLÉS buscándolas en otras fuentes ES.

    Una fuente ES puede sencillamente NO TENER una página (paginación propia, huecos): medido en
    Amayo ch36 p024, donde NINGUNA de las 37 págs de LeerCapitulo alinea. Otra fuente sí puede
    traerla. No arriesga precisión: decide la homografía SIFT (ver `rescue_english_pages`); si
    ninguna alternativa es la misma página, todo se queda como estaba."""
    from transplant_core import rescue_english_pages
    pend = list(res.get("english_pages") or [])
    if not pend:
        return res
    if only_trailing_pages(pend, res.get("pages")):
        return res      # sólo créditos pendientes -> no gastar 3 descargas en algo irrescatable
    meta = _read_meta(title)
    variants = meta.get("variants") or title_variants(title, None)
    # REGLA: "falló" y "no había nada que rescatar" NO pueden devolver lo mismo. Devolver `res`
    # a secas ante un error es indistinguible de un rescate que miró y no encontró — y eso ya
    # costó una conclusión falsa (E-12: el banco reportó 0/11 con Suwayomi MUERTA, y como
    # `rescued=0` es lo que sale también cuando no hay nada, se leyó como "el rescate no sirve").
    # `rescue_error` / `rescue_tried` dejan esa diferencia por escrito para quien mida.
    try:
        cands = _candidates_cached(title, meta.get("al_id"), variants, None)
    except Exception as e:
        print(f"[transplant] rescate: no se pudieron listar candidatos para {title!r}: {e!r}",
              file=sys.stderr, flush=True)
        return {**res, "rescue_error": f"candidatos: {e}"}
    used = {(str((used_es or {}).get("sourceId")), str((used_es or {}).get("mangaId")))}
    # Sólo fuentes REALMENTE instaladas: la lista de candidatos está cacheada en disco y arrastra
    # fuentes ya desinstaladas; pedirles capítulos lanza SourceNotInstalledException y gasta
    # intentos del cupo sin poder rescatar nada.
    try:
        installed = {str(s["id"]) for s in _all_sources()}
    except Exception as e:
        # No saber qué hay instalado NO es "no hay nada instalado": se sigue con el filtro
        # desactivado (installed=None), pero quedando constancia de que se está a ciegas.
        print(f"[transplant] rescate: no se pudo listar fuentes instaladas ({e!r}) — "
              f"se prueban todas las candidatas", file=sys.stderr, flush=True)
        installed = None
    alts = [c for c in cands
            if (c.get("sourceLang") or "").lower() in _ES_LANGS
            and (str(c.get("sourceId")), str(c.get("id"))) not in used
            and (installed is None or str(c.get("sourceId")) in installed)]
    rescued = 0
    tried = 0
    errors = []
    for alt in alts[:RESCUE_MAX_SOURCES]:
        if not pend or _transplant_cancel.get(task_id):
            break
        tried += 1
        alt_dir = Path(tmp_dir) / f"es_alt_{alt.get('sourceId')}_{alt.get('id')}"
        try:
            # Los candidatos del barrido traen la manga en `id`; `_candidate_chapter_urls` la
            # espera en `mangaId` (mismo mapeo que hace discover al guardar el meta).
            ref = {"sourceId": alt.get("sourceId"), "mangaId": alt.get("id"),
                   "sourceLang": alt.get("sourceLang")}
            urls = _candidate_chapter_urls(ref, title, want_num=chn)[1]
            if not urls:
                continue
            with _dl_semaphore:
                if not _download_chapter_to(alt_dir, urls, "es"):
                    continue
            _set_status(task_id, phase="rescue", chapter=chn, chapterDone=ci, chapterTotal=total,
                        note=f"{len(pend)} pág. en inglés → probando {alt.get('sourceName')}")
            before = len(pend)
            pend = rescue_english_pages(pend, alt_dir, stage,
                                        should_cancel=lambda: bool(_transplant_cancel.get(task_id)))
            rescued += before - len(pend)
        except Exception as e:
            # Una fuente alternativa que falle no rompe el capítulo, pero NO se traga el error:
            # un except mudo aquí ocultó que el rescate no funcionaba en absoluto (los candidatos
            # traen `id`, no `mangaId`) y reportaba "0 rescatadas" como si fuese normal.
            print(f"[transplant] rescate: fuente {alt.get('sourceName')} falló en cap {chn}: {e!r}",
                  file=sys.stderr, flush=True)
            errors.append(f"{alt.get('sourceName')}: {e}")
            continue
        finally:
            shutil.rmtree(alt_dir, ignore_errors=True)
    res = dict(res)
    if rescued:
        res["english"] = len(pend)
        res["english_pages"] = pend
        res["rescued"] = rescued
    # Siempre, aunque no se rescatara nada: sin esto, "probé 3 fuentes y ninguna tenía la página"
    # y "no pude probar ninguna porque el servidor estaba caído" son el MISMO dato.
    res["rescue_tried"] = tried
    if errors and not rescued:
        res["rescue_error"] = "; ".join(errors[:3])
    return res


def _run_chapters(task_id: str, title: str, chapters: list, art: dict, es: dict, qa: bool = False):
    from transplant_core import transplant_chapter
    _transplant_cancel[task_id] = False
    art_local = bool((art or {}).get("local"))
    local_files = _local_art_files(title) if art_local else {}
    art_map = None if art_local else _chapters_map(int(art["mangaId"]))
    # `es` global puede faltar cuando la traducción se lanza APOYÁNDOSE solo en las fuentes
    # ancladas por capítulo (chapter_sources): en ese caso cada capítulo resuelve su fuente ES
    # desde su asignación (abajo). El es_map global sigue siendo el respaldo por defecto.
    es_map = _chapters_map(int(es["mangaId"])) if es and es.get("mangaId") else {}
    # La fuente ES puede subir el capítulo TROCEADO (45.1, 45.2 …) sin un "45" entero: fusiona
    # esas partes en una unidad por nº entero (páginas concatenadas), igual que en el discover.
    es_units = {_chnum(u["number"]): u
                for u in _split_part_units(es_map.values(), lambda c: c.get("number"))}
    # El ARTE también puede venir troceado (descargas locales ch48.1/ch48.2 o una fuente EN que
    # partió el capítulo): misma fusión por nº entero, páginas concatenadas en orden.
    _art_keys = list(local_files) if art_local else list(art_map or {})
    art_units = {_chnum(u["number"]): [m["number"] for m in u["members"]]
                 for u in _split_part_units([{"number": k} for k in _art_keys],
                                            lambda e: e["number"])}
    out_dir = Path(manga_dir()) / title
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
        e_unit = es_units.get(_chnum(chn))   # unidad ES (una o varias partes fusionadas)
        e_ch = es_map.get(chn) or (e_unit["members"][0] if e_unit else None)
        # Fuente ANCLADA por capítulo (chapter_sources) — la que el usuario fijó en el grid de
        # Cobertura. Según su idioma juega uno de dos papeles para ESTE capítulo:
        #  • NO-española → fuente de ARTE (mejor calidad en inglés/otro) en vez del arte global.
        #  • Española    → fuente ES de ESE capítulo en vez del ES global (traducir directamente
        #                   desde la fuente anclada en la vista de Capítulos).
        # Sin anclaje se cae exactamente al arte/ES global de siempre.
        per_ch = versions_db.get_assignment(title, chn)
        per_ch_is_es = bool(per_ch and (per_ch.get("sourceLang") or "").lower() in _ES_LANGS)
        per_ch_art = per_ch if (per_ch and not per_ch_is_es) else None
        per_ch_es = per_ch if per_ch_is_es else None
        use_override = bool(per_ch_art)
        has_es = bool(e_ch) or bool(per_ch_es)
        a_keys = art_units.get(_chnum(chn)) or ([chn] if chn in _art_keys else [])
        if art_local:
            a_local = [f for k in a_keys for f in local_files[k]] or None
            a_ch = a_local
        else:
            a_local = None
            a_ch = art_map.get(chn) or (art_map.get(a_keys[0]) if a_keys else None)
        if not use_override and not a_ch:
            failed.add(chn)
            _set_status(task_id, phase="skip", chapter=chn, note="falta capítulo en arte",
                        chapterDone=ci, chapterTotal=total)
            _persist_run_meta(title, translated, failed)
            continue
        if not has_es:
            failed.add(chn)
            _set_status(task_id, phase="skip", chapter=chn, note="falta capítulo en ES",
                        chapterDone=ci, chapterTotal=total)
            _persist_run_meta(title, translated, failed)
            continue
        tmp = Path(tempfile.mkdtemp(prefix="transplant_", dir=str(_work_root())))
        try:
            en_dir = tmp / "en"; es_dir = tmp / "es"; stage = tmp / "out"
            _set_status(task_id, phase="download", chapter=chn, chapterDone=ci, chapterTotal=total)
            with _dl_semaphore:
                art_urls = _candidate_chapter_urls(per_ch_art, title, want_num=chn)[1] if use_override else []
                if art_urls:
                    _download_chapter_to(en_dir, art_urls, "en")
                elif art_local:
                    en_dir.mkdir(parents=True, exist_ok=True)
                    for f in a_local:
                        shutil.copy2(f, en_dir / f.name)
                elif a_ch:
                    urls = []
                    for k in (a_keys if len(a_keys) > 1 else []):
                        urls += _chapter_page_urls(art_map[k]["id"])
                    _download_chapter_to(en_dir, urls or _chapter_page_urls(a_ch["id"]), "en")
                else:
                    # asignación por capítulo sin páginas Y sin arte global de respaldo
                    failed.add(chn)
                    _set_status(task_id, phase="skip", chapter=chn, note="sin páginas de arte disponibles",
                                chapterDone=ci, chapterTotal=total)
                    _persist_run_meta(title, translated, failed)
                    continue
                if _cancelled():
                    cancelled = True
                else:
                    # ES desde la fuente anclada por capítulo si la hay; si no, desde el ES global
                    # (concatenando las partes 45.1/45.2 … cuando el capítulo viene troceado).
                    if per_ch_es:
                        es_urls = _candidate_chapter_urls(per_ch_es, title, want_num=chn)[1]
                    elif e_unit and len(e_unit["members"]) > 1:
                        es_urls = []
                        for m in e_unit["members"]:
                            es_urls += _chapter_page_urls(m["id"])
                    else:
                        es_urls = _chapter_page_urls(e_ch["id"])
                    if not es_urls:
                        failed.add(chn)
                        _set_status(task_id, phase="skip", chapter=chn, note="sin páginas ES disponibles",
                                    chapterDone=ci, chapterTotal=total)
                        _persist_run_meta(title, translated, failed)
                        continue
                    _download_chapter_to(es_dir, es_urls, "es")
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
                # Modo QA: conserva arte EN + ES emparejada + overlay + stats por página.
                # Subcarpeta = título crudo (igual que MANGA_DIR/<title>) para mapear desde el flag.
                debug_dir=(QA_DIR / title / prefix) if qa else None,
            )
            if res.get("cancelled"):
                cancelled = True            # staging se descarta en finally -> sin capítulo a medias
            elif res.get("pages"):
                # 2ª pasada: las páginas que la fuente ES no cubre se buscan en OTRAS fuentes ES
                # (sobre el MISMO staging, antes de instalar el capítulo).
                res = _rescue_alt_es(task_id, title, chn, tmp, stage, res,
                                     per_ch_es or es, ci, total)
                _replace_chapter_in_place(out_dir, prefix, stage, res["pages"])
                _invalidate_upscaled(title, prefix)
                translated.add(chn)
                done_ch.append({"chapter": chn, **{k: res[k] for k in
                                ("trans", "fallback", "english", "en_pages", "es_pages", "rescued") if k in res}})
                # AVISO de incompletitud: la fuente ES no cubre el capítulo → el usuario debería
                # elegir otra (no es un fallo del algoritmo).
                # NO se dispara por `eng > 0`: casi TODO capítulo acaba con 1-2 páginas en inglés
                # que son la HOJA DE CRÉDITOS del grupo del scan EN — no existe en el ES (es de otro
                # grupo) y dejarla intacta es lo CORRECTO (traducirla atribuiría el trabajo al equipo
                # equivocado). Medido: HDWR ch7-12, 1 pág/cap, siempre la última. Con `eng > 0` el
                # aviso saltaba en casi todos los capítulos y el usuario aprendía a ignorarlo.
                # El síntoma FIABLE de fuente incompleta es el DÉFICIT DE PÁGINAS.
                enp, esp, eng = res.get("en_pages", 0), res.get("es_pages", 0), res.get("english", 0)
                if enp and esp < 0.85 * enp:
                    _set_status(task_id, phase="warn", chapter=chn, chapterDone=ci, chapterTotal=total,
                                note=f"fuente ES incompleta: {esp}/{enp} pág, {eng} sin traducir (elige otra fuente ES)")
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


@transplant_bp.route("/upscale_cost", methods=["POST"])
def upscale_cost_route():
    """Páginas 4K que se perderían al traducir. El front avisa con esto ANTES de lanzar /run.

    NO toca Suwayomi ni resuelve 'all' contra las fuentes: sólo mira el disco, así que es
    instantáneo y sirve para pintar un diálogo sin hacer esperar al usuario. Con chapters=None
    o 'all' cuenta TODO el escalado del título, que es justo lo que /run acabaría invalidando.
    """
    body = request.get_json(silent=True) or {}
    title = (body.get("title") or "").strip()
    if not title:
        return jsonify({"error": "title required"}), 400
    chapters = body.get("chapters")
    return jsonify(upscaled_cost(title, None if chapters == "all" else chapters))


@transplant_bp.route("/run", methods=["POST"])
def run():
    """Trasplanta uno o más capítulos usando las fuentes ya elegidas (discover/confirm)."""
    if not ensure_suwayomi():
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
        art_map = _local_chapters_map(title) if art.get("local") else _chapters_map(int(art["mangaId"]))
        es_map = _chapters_map(int(es["mangaId"]))
        chapters = sorted(set(art_map) & set(es_map),
                          key=lambda x: float(x) if x.replace('.', '', 1).isdigit() else 0)
    if not isinstance(chapters, list) or not chapters:
        return jsonify({"error": "no chapters to process"}), 400

    qa = bool(body.get("qa"))   # modo QA (testing): conserva artefactos de debug por página
    task_id = build_task_id(title, "run", "transplant")
    _set_status(task_id, status="running", title=title, phase="start",
                chapterTotal=len(chapters), art=art, es=es)
    threading.Thread(target=_run_chapters, args=(task_id, title, chapters, art, es),
                     kwargs={"qa": qa}, daemon=True).start()
    return jsonify({"task_id": task_id, "title": title, "chapters": chapters})


# ── Modo QA de traducción (SOLO testing): marcar páginas malas + datos de depuración ──────────
_qa_lock = threading.Lock()
_QA_FLAGS = QA_DIR / "flags.json"


def _qa_read_flags() -> list:
    try:
        return _json.loads(_QA_FLAGS.read_text())
    except Exception:
        return []


def _dir_size(path: Path) -> int:
    total = 0
    if path.exists():
        for p in path.rglob("*"):
            if p.is_file():
                try: total += p.stat().st_size
                except OSError: pass
    return total


@transplant_bp.route("/qa/flag", methods=["POST"])
def qa_flag():
    """Marca una página mal traducida: copia el caso (salida + arte EN + ES + overlay + stats)
    a QA_DIR/_flagged/<case> y lo añade a flags.json. Body: {title, chapter, page, reason, note}."""
    body = request.get_json(silent=True) or {}
    title = (body.get("title") or "").strip()
    chapter = body.get("chapter")
    page = (body.get("page") or "").strip()
    reason = (body.get("reason") or "otro").strip()
    note = (body.get("note") or "").strip()
    if not title or chapter is None or not page:
        return jsonify({"error": "title, chapter and page required"}), 400

    prefix = _chapter_file_prefix(normalize_chapter(chapter))
    bundle = QA_DIR / title / prefix
    stem = page[:-4] if page.lower().endswith(".png") else Path(page).stem
    stats = None
    try:
        stats = _json.loads((bundle / "pages.json").read_text()).get(page)
    except Exception:
        pass

    case_id = uuid.uuid4().hex[:12]
    case_dir = QA_DIR / "_flagged" / case_id
    case_dir.mkdir(parents=True, exist_ok=True)
    # La página de salida (lo que el usuario ve mal) vive en la biblioteca.
    out_src = Path(manga_dir()) / title / page
    if out_src.exists():
        shutil.copy2(out_src, case_dir / f"output{out_src.suffix}")
    # Artefactos de depuración del bundle del capítulo (si la traducción fue en modo QA).
    for suffix in ("__en.png", "__es.png", "__overlay.png"):
        src = bundle / f"{stem}{suffix}"
        if src.exists():
            shutil.copy2(src, case_dir / f"{stem}{suffix}")

    at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    case = {"case_id": case_id, "title": title, "chapter": normalize_chapter(chapter),
            "page": page, "reason": reason, "note": note, "stats": stats,
            "has_artifacts": (bundle / f"{stem}__en.png").exists(), "at": at}
    _json.dump(case, open(case_dir / "case.json", "w"), ensure_ascii=False, indent=2)
    with _qa_lock:
        flags = _qa_read_flags()
        flags.append(case)
        QA_DIR.mkdir(parents=True, exist_ok=True)
        _json.dump(flags, open(_QA_FLAGS, "w"), ensure_ascii=False, indent=2)
    return jsonify(case)


@transplant_bp.route("/qa/flags", methods=["GET"])
def qa_flags():
    """Lista los casos marcados + conteo por motivo (para la fase de mejora)."""
    flags = _qa_read_flags()
    by_reason = {}
    for f in flags:
        by_reason[f.get("reason", "otro")] = by_reason.get(f.get("reason", "otro"), 0) + 1
    return jsonify({"flags": flags, "count": len(flags), "by_reason": by_reason})


@transplant_bp.route("/qa/size", methods=["GET"])
def qa_size():
    """Tamaño en disco de los datos QA (para el contador de Ajustes)."""
    return jsonify({"bytes": _dir_size(QA_DIR), "flags": len(_qa_read_flags())})


@transplant_bp.route("/qa/clear", methods=["POST"])
def qa_clear():
    """Borra TODOS los datos QA (artefactos + casos + manifiesto)."""
    with _qa_lock:
        freed = _dir_size(QA_DIR)
        shutil.rmtree(QA_DIR, ignore_errors=True)
    return jsonify({"ok": True, "freed_bytes": freed})
