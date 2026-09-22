#!/usr/bin/env python3
"""
Storage API — uso de disco por serie y limpiezas seguras.

Da visibilidad de lo que ocupa `data/` (que crece a ciegas con escalados 4x y
tomos) y permite recuperar espacio SIN tocar la fuente de verdad:
  - la copia escalada (UPSCALED_DIR) es regenerable → se puede borrar por serie
    o entera;
  - la caché de streaming (data/_stream_cache) es efímera → se regenera al
    reproducir.
Nunca se ofrece borrar los originales descargados desde aquí (irreversibles:
las páginas a color no se escalan, y el escalado es una reconstrucción, no el
original).
"""
import json as _json
import os
import re
import shutil
from pathlib import Path

# Capítulos = archivos planos ch{####}_{###}.ext dentro de la carpeta de la serie
# (no subcarpetas); el número de capítulo es el primer grupo.
_CH_RE = re.compile(r"ch(\d+)_\d+", re.IGNORECASE)

from flask import Blueprint, jsonify, request

from api.runtime import manga_dir, upscaled_dir, get_library_mode, QA_DIR, safe_child
from api.index_db import cached_measure, drop, prune
from api.observability import record_error
from api.storage_cache import summary as cache_summary, purge as purge_cache
from api.library_events import mark_changed

storage_bp = Blueprint("storage", __name__)


def _tree_bytes(path: str) -> int:
    total = 0
    try:
        for entry in os.scandir(path):
            try:
                if entry.is_dir(follow_symlinks=False):
                    total += _tree_bytes(entry.path)
                else:
                    total += entry.stat(follow_symlinks=False).st_size
            except OSError:
                pass
    except OSError:
        pass
    return total


def _measure_series(series_dir: Path):
    """(bytes, nº de capítulos) de una serie. Capítulo = prefijo ch#### distinto
    entre los archivos ch####_###.ext (planos en la carpeta de la serie)."""
    total = 0
    chapters = set()

    def _scan(path):
        nonlocal total
        try:
            for entry in os.scandir(path):
                try:
                    if entry.is_dir(follow_symlinks=False):
                        _scan(entry.path)  # tolera estructuras anidadas si las hubiera
                    else:
                        total += entry.stat(follow_symlinks=False).st_size
                        m = _CH_RE.match(entry.name)
                        if m:
                            chapters.add(m.group(1))
                except OSError:
                    pass
        except OSError:
            pass

    _scan(str(series_dir))
    return total, len(chapters)


_VIDEO_EXTS = {".mkv", ".mp4", ".avi", ".webm", ".mov", ".m4v"}


def _anime_dirs() -> dict:
    """Carpetas de series de anime a medir, REPARTIDAS EN VARIOS DISCOS.

    Fuente autoritativa = la BIBLIOTECA de anime: cada serie guarda su propio
    `local_path`, que puede estar en cualquier disco/partición (aquí D: y E:).
    Esto es lo que arregla el bug: antes se medía solo el `download_path` como una
    sola raíz, y encima sin convertir `D:\\`→`/mnt/d`, así que `scandir` fallaba y
    NO aparecía ningún anime.

    Deliberadamente NO se escanea el `download_path`/scan-paths a lo bruto: el
    usuario los tiene apuntando a RAÍCES DE DISCO enteras (`D:\\`, `E:\\`), donde
    conviven juegos, películas y descargas sueltas — listar sus subcarpetas metía
    Steam/Genshin/Downloads como si fueran anime e inflaba el total. La biblioteca
    da exactamente las series que el usuario reconoce como anime.

    `_lib_read` ya está namespaceada por modo (normal/oculto), así que iterarla no
    cruza ni delata la biblioteca oculta. Devuelve {nombre: Path}, deduplicado por
    ruta real; convierte rutas Windows a WSL cuando hace falta.
    """
    out: dict = {}
    seen: set = set()  # rutas ya añadidas (resueltas), para deduplicar

    try:
        from api.anime import _lib_read, _is_wsl, _win_to_wsl
    except Exception:
        return out

    def _to_local(raw: str) -> Path:
        raw = raw.strip()
        if _is_wsl() and re.match(r"^[A-Za-z]:[/\\]", raw):
            raw = _win_to_wsl(raw)
        return Path(raw)

    try:
        for anime in _lib_read().values():
            lp = (anime.get("local_path") or "").strip()
            if not lp:
                continue
            p = _to_local(lp)
            try:
                if not p.is_dir():
                    continue
                rp = str(p.resolve())
            except OSError:
                continue
            if rp in seen:
                continue
            seen.add(rp)
            # Si dos discos tuvieran una serie con el MISMO nombre de carpeta, se
            # desempata con un sufijo de ruta para no pisarse. Raro, pero seguro.
            name = p.name
            if name in out:
                name = f"{name} ({p.parent.name})"
            out[name] = p
    except Exception:
        pass

    return out


def _measure_anime(series_dir: Path):
    """(bytes, nº de episodios) de una carpeta de anime."""
    total = 0
    episodes = 0
    def _scan(path):
        nonlocal total, episodes
        try:
            for entry in os.scandir(path):
                if entry.is_dir(follow_symlinks=False):
                    _scan(entry.path)
                elif entry.is_file(follow_symlinks=False):
                    total += entry.stat().st_size
                    if os.path.splitext(entry.name)[1].lower() in _VIDEO_EXTS:
                        episodes += 1
        except OSError:
            pass
    _scan(series_dir)
    return total, episodes


def _translated_count(title: str) -> int:
    from api.roots import series_dir
    meta = series_dir(title) / ".transplant_meta.json"
    if not meta.exists():
        return 0
    try:
        data = _json.loads(meta.read_text())
        return len(data.get("translated", []) or [])
    except Exception:
        return 0


def _stream_cache_root() -> Path:
    # Importa perezoso: stream.py ya resolvió la raíz (con la guarda de WSL/drvfs).
    try:
        from api.stream import _SESS_ROOT
        return Path(_SESS_ROOT)
    except Exception:
        from api.runtime import DATA_ROOT
        return Path(DATA_ROOT) / "_stream_cache"


# ── Integridad: ¿lo que hay en disco sigue siendo lo que el torrent dice? ─────────────────────
def _qbt_wsl_path(win_path: str) -> str:
    from api.anime import _win_to_wsl
    from api.platform import is_wsl
    if is_wsl() and re.match(r"^[A-Za-z]:[/\\]", win_path or ""):
        return _win_to_wsl(win_path)
    return win_path


def torrent_integrity() -> dict:
    """Compara el tamaño en disco con el que DECLARA cada torrent, fichero a fichero.

    Por qué existe: los vídeos de la biblioteca son el contenido que qBittorrent siembra, y
    cualquier escritura sobre ellos rompe el hash EN SILENCIO — qBittorrent sigue diciendo
    `stalledUP` como si nada, y sólo te enteras cuando un `recheck` te obliga a re-descargar
    varios GB. Este chequeo encontró 2 archivos rotos (de 75) que llevaban semanas así.

    Fichero a fichero y NO por carpeta a propósito: los sidecars que dejamos al traducir
    (`<vídeo>.spa.ass`) viven junto al vídeo, así que sumar la carpeta los contaría como bytes
    de más y daría falsos positivos. Lo que el torrent no declara, no se mira.

    Sólo LEE. Nunca dispara un recheck: eso lo decide el usuario (implica re-descargar).
    """
    from api.anime import _q

    try:
        r = _q("get", "/torrents/info")
        torrents = r.json()
    except Exception as e:
        return {"available": False, "reason": f"qBittorrent no responde: {e}"}

    checked = ok = 0
    skipped = 0
    bad = []
    for t in torrents:
        cp = _qbt_wsl_path(t.get("content_path", ""))
        entries = []
        if cp and os.path.isfile(cp):
            entries = [(cp, t.get("total_size") or 0)]            # torrent de 1 archivo
        else:
            try:                                                   # multi-archivo: pedir su lista
                files = _q("get", "/torrents/files", params={"hash": t.get("hash", "")}).json()
                root = _qbt_wsl_path(t.get("save_path", ""))
                entries = [(os.path.join(root, f["name"].replace("/", os.sep)), f["size"])
                           for f in files]
            except Exception:
                skipped += 1
                continue
        for path, declared in entries:
            if not os.path.isfile(path):
                skipped += 1                     # movido/borrado: no es corrupción, no alarmar
                continue
            checked += 1
            actual = os.path.getsize(path)
            if actual == declared:
                ok += 1
            else:
                bad.append({"name": t.get("name", ""), "file": os.path.basename(path),
                            "declared": declared, "actual": actual,
                            "diff": actual - declared, "state": t.get("state", "")})
    return {"available": True, "checked": checked, "ok": ok,
            "skipped": skipped, "mismatched": bad}


@storage_bp.route("/integrity", methods=["GET"])
def integrity_route():
    return jsonify(torrent_integrity())


@storage_bp.route("/summary", methods=["GET"])
def summary():
    """Desglose de disco: por serie (original vs escalado) + totales + cachés."""
    return jsonify(_build_summary())


def _build_summary() -> dict:
    """Cálculo compartido del desglose de disco (lo consume /summary y /stats)."""
    manga_root = Path(manga_dir())
    up_root = Path(upscaled_dir())

    # Namespacea las claves de caché por modo para que las mediciones de la
    # biblioteca oculta nunca se crucen (ni delaten su existencia) en la normal.
    _suf = '' if get_library_mode() == 'normal' else f':{get_library_mode()}'
    _k_manga, _k_up, _k_anime = f"manga{_suf}", f"upscaled{_suf}", f"anime{_suf}"

    series = {}  # name -> dict

    manga_names, up_names, anime_names = [], [], []

    # Una obra repartida entre discos se mide SUMANDO sus carpetas: si no, el panel diría que
    # ocupa la mitad de lo que ocupa y no cuadraría con el espacio libre real.
    from api.roots import roots as _roots
    for _r in _roots():
        _mr = Path(_r["manga"])
        if not _mr.is_dir():
            continue
        for entry in os.scandir(_mr):
            if not entry.is_dir(follow_symlinks=False) or entry.name.startswith("."):
                continue
            key = f'{entry.name}@{_r["id"]}'
            manga_names.append(key)
            b, ch, _ = cached_measure(_k_manga, key, entry.path,
                                      lambda p: (*_measure_series(Path(p)), {}))
            row = series.setdefault(entry.name, {
                "name": entry.name,
                "original_bytes": 0,
                "original_chapters": 0,
                "upscaled_bytes": 0,
                "upscaled_chapters": 0,
                "translated_chapters": _translated_count(entry.name),
            })
            row["original_bytes"] += b
            row["original_chapters"] += ch

    for _r in _roots():
        _ur = Path(_r["upscaled"])
        if not _ur.is_dir():
            continue
        for entry in os.scandir(_ur):
            if not entry.is_dir(follow_symlinks=False) or entry.name.startswith("."):
                continue
            key = f'{entry.name}@{_r["id"]}'
            up_names.append(key)
            b, ch, _ = cached_measure(_k_up, key, entry.path,
                                      lambda p: (*_measure_series(Path(p)), {}))
            row = series.setdefault(entry.name, {
                "name": entry.name, "original_bytes": 0, "original_chapters": 0,
                "upscaled_bytes": 0, "upscaled_chapters": 0,
                "translated_chapters": _translated_count(entry.name),
            })
            row["upscaled_bytes"] += b
            row["upscaled_chapters"] += ch

    for r in series.values():
        r["kind"] = "manga"

    # Anime: vídeos repartidos en VARIOS discos (download_path + local_path de cada
    # serie de la biblioteca), aparte de MANGA_DIR.
    total_anime = 0
    for name, adir in _anime_dirs().items():
        anime_names.append(name)
        b, _, extra = cached_measure(
            _k_anime, name, str(adir),
            lambda p: (lambda r: (r[0], 0, {"episodes": r[1]}))(_measure_anime(Path(p))),
        )
        eps = extra.get("episodes", 0)
        if b <= 0:
            continue
        total_anime += b
        series[f"\x00anime\x00{name}"] = {
            "name": name,
            "kind": "anime",
            "original_bytes": b,      # tamaño total de la serie (para orden/rowsize en la UI)
            "upscaled_bytes": 0,
            "original_chapters": 0,
            "upscaled_chapters": 0,
            "translated_chapters": 0,
            "episodes": eps,
        }

    # Drop cached rows for series that no longer exist on disk.
    prune(_k_manga, manga_names)
    prune(_k_up, up_names)
    prune(_k_anime, anime_names)

    rows = sorted(
        series.values(),
        key=lambda r: r["original_bytes"] + r["upscaled_bytes"],
        reverse=True,
    )

    total_original = sum(r["original_bytes"] for r in rows if r.get("kind") != "anime")
    total_upscaled = sum(r["upscaled_bytes"] for r in rows)
    stream_cache = _tree_bytes(str(_stream_cache_root()))
    qa = _tree_bytes(str(QA_DIR))
    caches = cache_summary()

    disk = {}
    try:
        du = shutil.disk_usage(str(manga_root if manga_root.exists() else Path(manga_dir()).parent))
        disk = {"disk_total": du.total, "disk_free": du.free, "disk_used": du.used}
    except Exception:
        pass

    return {
        "series": rows,
        "totals": {
            "original": total_original,
            "upscaled": total_upscaled,
            "anime": total_anime,
            "stream_cache": stream_cache,
            "qa": qa,
            "image_cache": caches["image_cache"],
            "export_temp": caches["export_temp"],
            "export_orphan": caches["export_orphan"],
            "cache_total": stream_cache + qa + caches["total"],
            "total": total_original + total_upscaled + total_anime + stream_cache + qa + caches["total"],
            **disk,
        },
    }


@storage_bp.route("/stats", methods=["GET"])
def stats():
    """Panel de estadísticas ligero (biblioteca + actividad de anime del mes).
    Reutiliza la medición cacheada de _build_summary + el historial de visionado."""
    import time as _time
    summ = _build_summary()
    rows = summ["series"]
    totals = summ["totals"]

    manga_rows = [r for r in rows if r.get("kind") != "anime"]
    anime_rows = [r for r in rows if r.get("kind") == "anime"]

    # El nº de series de la biblioteca sale de los METADATOS (lo que el usuario sigue/
    # registra), no de las carpetas en disco — muchas series se siguen sin descargar todo.
    try:
        from api.anime import _lib_read
        anime_lib_count = len(_lib_read())
    except Exception:
        anime_lib_count = len(anime_rows)

    lib = {
        "manga_series": len(manga_rows),
        "anime_series": anime_lib_count,
        "chapters": sum(r.get("original_chapters", 0) for r in manga_rows),
        "upscaled_chapters": sum(r.get("upscaled_chapters", 0) for r in manga_rows),
        "translated_chapters": sum(r.get("translated_chapters", 0) for r in manga_rows),
        "episodes": sum(r.get("episodes", 0) for r in anime_rows),
    }

    # Actividad de anime (historial con timestamps).
    now = int(_time.time())
    month_ago = now - 30 * 86400
    try:
        from api.anime import _history_read
        history = _history_read()
    except Exception:
        history = []
    eps_month = sum(1 for h in history if int(h.get("watched_at", 0)) >= month_ago)
    series_month = len({h.get("anime_id") for h in history
                        if int(h.get("watched_at", 0)) >= month_ago})
    last_watched = history[0] if history else None

    # Series diarias para las gráficas (últimos 14 días). Episodios vistos vienen del
    # historial; escalados/traducidos del registro de actividad (se acumula con el uso).
    from api.runtime import read_activity
    activity = read_activity()
    DAYS = 14
    import datetime as _dt
    today = _dt.date.fromtimestamp(now)
    labels, watched_series, upscale_series = [], [], []
    day_index = {}
    for i in range(DAYS - 1, -1, -1):
        d = today - _dt.timedelta(days=i)
        day_index[d.isoformat()] = len(labels)
        labels.append(d.isoformat())
        watched_series.append(0)
        upscale_series.append(0)

    def _bucket(ts, arr):
        try:
            k = _dt.date.fromtimestamp(int(ts)).isoformat()
        except Exception:
            return
        idx = day_index.get(k)
        if idx is not None:
            arr[idx] += 1

    for h in history:
        _bucket(h.get("watched_at", 0), watched_series)
    for a in activity:
        if a.get("k") == "upscale":
            _bucket(a.get("t", 0), upscale_series)

    return jsonify({
        "library": lib,
        "storage": totals,
        "activity": {
            "episodes_total": len(history),
            "episodes_month": eps_month,
            "series_month": series_month,
            "last_watched": last_watched,
        },
        "daily": {
            "labels": labels,
            "watched": watched_series,
            "upscaled": upscale_series,
        },
    })


@storage_bp.route("/series", methods=["GET"])
def series_size():
    """Desglose de disco de UNA serie (para el modal de la serie). ?title=<nombre>."""
    title = (request.args.get("title") or "").strip()
    if not title:
        return jsonify({"error": "falta title"}), 400
    # Suma de todos los discos donde viva la obra: el modal debe decir lo que ocupa ENTERA.
    from api.roots import roots as _roots
    ob = oc = ub = uc = 0
    for _r in _roots():
        o = safe_child(Path(_r["manga"]), title)
        u = safe_child(Path(_r["upscaled"]), title)
        if o and o.is_dir():
            b, c = _measure_series(o); ob += b; oc += c
        if u and u.is_dir():
            b, c = _measure_series(u); ub += b; uc += c
    return jsonify({
        "name": title,
        "original_bytes": ob, "original_chapters": oc,
        "upscaled_bytes": ub, "upscaled_chapters": uc,
        "translated_chapters": _translated_count(title),
    })


@storage_bp.route("/series/delete", methods=["POST"])
def series_delete():
    """Borra los archivos descargados de un manga para liberar espacio, como en el
    anime. Body: {title, scope}. scope:
      - 'upscaled' → solo la copia 4K (regenerable) — recomendado.
      - 'original' → solo los originales descargados.
      - 'all'      → ambos (deja la serie sin páginas en disco).
    El registro de la biblioteca/seguimiento NO se toca aquí; el usuario puede
    re-descargar/re-escalar. Devuelve los bytes liberados."""
    body = request.get_json(silent=True) or {}
    title = (body.get("title") or "").strip()
    scope = (body.get("scope") or "upscaled").strip()
    if not title:
        return jsonify({"error": "falta title"}), 400
    if scope not in ("upscaled", "original", "all"):
        return jsonify({"error": "scope inválido"}), 400

    # Liberar espacio borra en TODOS los discos: si sólo se vaciara uno, el usuario vería
    # menos espacio liberado del que pidió y la obra quedaría a medias.
    from api.roots import roots as _roots
    targets = []
    for _r in _roots():
        if scope in ("upscaled", "all"):
            targets.append(safe_child(Path(_r["upscaled"]), title))
        if scope in ("original", "all"):
            targets.append(safe_child(Path(_r["manga"]), title))

    freed = 0
    deleted_any = False
    for d in targets:
        if d and d.is_dir():
            deleted_any = True
            freed += _tree_bytes(str(d))
            shutil.rmtree(d, ignore_errors=True)
    # Invalidar la medición cacheada por mtime (index_db) para que summary/series reflejen el
    # borrado ya. NO se puede esperar a un cambio de mtime: la carpeta ya no existe, así que su
    # mtime no va a cambiar nunca más y la fila seguiría contando bytes que ya no están.
    #
    # ⚠️ Esto estaba escrito `prune()`, sin argumentos — un TypeError que el `except` de al lado
    # se tragaba entero: liberar espacio NUNCA invalidaba nada y el panel seguía enseñando el
    # tamaño viejo. Ejemplo de libro de `except: pass` sobre un camino que DECIDE algo.
    # Mismo namespaceado por modo que `_build_summary`, o se borraría una fila que no es.
    _suf = '' if get_library_mode() == 'normal' else f':{get_library_mode()}'
    for _r in _roots():
        for _ns in (f"manga{_suf}", f"upscaled{_suf}"):
            try:
                drop(_ns, f'{title}@{_r["id"]}')
            except Exception as e:
                record_error("storage", e, op="drop_measure", title=title, root=_r["id"])
    if deleted_any:
        mark_changed()
    return jsonify({"ok": True, "freed": freed, "scope": scope})


@storage_bp.route("/purge", methods=["POST"])
def purge():
    """Limpieza segura. Body: {target='stream_cache'} → vacía la caché de
    streaming (efímera, se regenera al reproducir). NO se ofrece borrar el
    escalado 4K: es imprescindible para el comparador original/4K."""
    body = request.get_json(silent=True) or {}
    target = (body.get("target") or "").strip()

    if target == "stream_cache":
        root = _stream_cache_root()
        freed = _tree_bytes(str(root))
        if root.is_dir():
            for child in root.iterdir():
                shutil.rmtree(child, ignore_errors=True) if child.is_dir() else child.unlink(missing_ok=True)
        return jsonify({"ok": True, "freed": freed})

    if target in ("image_cache", "export_temp"):
        try:
            return jsonify(purge_cache(target))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:
            record_error("storage", exc, op="purge_cache", target=target)
            return jsonify({"error": str(exc)}), 500

    return jsonify({"error": "target inválido"}), 400
