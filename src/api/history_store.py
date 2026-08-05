"""Historial de consumo con ARCHIVO por meses.

Por qué existe este módulo
──────────────────────────
`anime.py` guardaba el historial con `history[:500]` y `library.py` con `[:300]`. Medido el
2026-07-31: `watch_history.json` tenía EXACTAMENTE 500 entradas y cubría sólo **21 días**
(9-jul → 30-jul). O sea que el tope no era un margen de sobra, estaba lleno y **cada episodio
que se veía borraba el más antiguo, en silencio y para siempre**. Cualquier resumen del estilo
"tu año" era imposible por construcción.

Subir el tope a secas no vale: `append()` se llama cada pocos segundos mientras se reproduce
(el progreso nativo late así), y lee + reescribe el fichero entero en cada llamada. Con 20.000
entradas eso son ~4 MB de ida y vuelta por latido.

La solución es rotar, no truncar: el fichero VIVO se queda pequeño y rápido, y lo que se sale
se funde en `<nombre>_archive/YYYY-MM.json`. Nada se borra nunca.

    watch_history.json                 ← las ~1200 más recientes (lectura/escritura caliente)
    watch_history_archive/2026-07.json ← el resto, por mes, sólo se toca al archivar o al leer todo

`read_all()` reconstruye la serie completa para la retrospectiva; el camino caliente
(`append`/`read_live`) nunca toca el archivo.
"""
from __future__ import annotations

import re
import time
from pathlib import Path

from api.observability import record_error, swallow
from api.runtime import manga_dir, read_json_safe, write_json_atomic

# Cuántas entradas se quedan en el fichero vivo, y a cuántas se recorta cuando se pasa.
# 1200 ≈ dos meses del ritmo real del usuario; el recorte deja 800 para no archivar en
# cada escritura (se archiva una vez cada 400 entradas, no 1200 veces).
LIVE_CAP = 1200
LIVE_KEEP = 800

# Cada tipo de historial trae su propio nombre de campo para la marca de tiempo.
KINDS = {
    'watch': ('watch_history.json', 'watched_at'),
    'reading': ('reading_history.json', 'read_at'),
}

_MONTH_RE = re.compile(r'^\d{4}-\d{2}\.json$')


def _paths(kind: str) -> tuple[Path, Path, str]:
    """(fichero vivo, carpeta de archivo, campo de fecha) para un tipo de historial."""
    name, ts_field = KINDS[kind]
    root = Path(manga_dir())
    return root / name, root / f'{name[:-5]}_archive', ts_field


def read_live(kind: str) -> list:
    """Las entradas recientes. Fichero ausente = historial vacío, no un fallo."""
    live, _, _ = _paths(kind)
    return read_json_safe(live, default=[], component='history') or []


def _archive_month(kind: str, month: str, entries: list) -> None:
    """Funde `entries` en el fichero del mes, sin perder lo que ya hubiera."""
    _, arch_dir, ts_field = _paths(kind)
    arch_dir.mkdir(parents=True, exist_ok=True)
    target = arch_dir / f'{month}.json'
    prev = read_json_safe(target, default=[], component='history') or []
    merged = prev + entries
    merged.sort(key=lambda e: int(e.get(ts_field) or 0), reverse=True)
    write_json_atomic(target, merged, indent=2, keep_backup=True)


def _roll(kind: str, history: list) -> list:
    """Saca del vivo lo que sobra y lo reparte por meses. Devuelve el vivo recortado.

    Si archivar falla, se DEVUELVE la lista entera sin recortar: más vale un fichero vivo
    grande que perder entradas (regla del repo: «falló» no puede acabar como «no había»).
    """
    if len(history) <= LIVE_CAP:
        return history
    _, _, ts_field = _paths(kind)
    overflow = history[LIVE_KEEP:]
    by_month: dict[str, list] = {}
    for e in overflow:
        ts = int(e.get(ts_field) or 0)
        # Sin fecha no se puede clasificar; va a un cajón aparte en vez de perderse.
        month = time.strftime('%Y-%m', time.localtime(ts)) if ts else '0000-00'
        by_month.setdefault(month, []).append(e)
    try:
        for month, entries in by_month.items():
            _archive_month(kind, month, entries)
    except Exception as e:
        record_error('history', e, op='archive', kind=kind, entries=len(overflow))
        return history
    return history[:LIVE_KEEP]


def append(kind: str, entry: dict, *, dedup_keys: tuple = (),
           dedup_window: int = 6 * 3600, dedup_scan: int = 20) -> None:
    """Inserta arriba, colapsando un re-registro de lo mismo.

    `dedup_keys` son los campos que identifican «lo mismo» (p. ej. anime_id + episode).

    Los dos historiales quieren reglas distintas y ambas se respetan:
    - **anime** (`dedup_scan=20`, ventana 6 h): el progreso nativo late cada ~5 s mientras el
      episodio está sobre el umbral de visto; sin esto se acumularían decenas de entradas
      idénticas. Pero ver el mismo episodio dentro de un año SÍ es una entrada nueva.
      ⚠️ La versión de `anime.py` colapsaba con `i == 0 or <ventana>`, es decir que la entrada
      MÁS RECIENTE se colapsaba siempre, sin límite de tiempo: revisitar tu último episodio
      meses después no dejaba rastro. Con el historial truncado a 500 daba igual; para la
      retrospectiva es un agujero, así que ahora manda sólo la ventana.
    - **lectura** (`dedup_scan=0`, `dedup_window=0`): el panel muestra «la última vez que leí
      cada capítulo», así que un capítulo no puede salir dos veces por muy lejos que quede.
    """
    live, _, ts_field = _paths(kind)
    history = read_live(kind)
    now = int(time.time())
    entry = {**entry, ts_field: entry.get(ts_field) or now}

    if dedup_keys:
        # Comparación por TEXTO a propósito: el capítulo llega unas veces como "33" y otras
        # como 33 según por dónde entre, y con `==` crudo esos dos no casaban y salía duplicado.
        same = lambda h: all(str(h.get(k)) == str(entry.get(k)) for k in dedup_keys)   # noqa: E731
        if not dedup_window:
            history = [h for h in history if not same(h)]      # una sola entrada por clave
        else:
            # La ventana se mide contra la fecha de la ENTRADA, no contra el reloj de pared: así
            # una entrada reconstruida o importada con su fecha original se agrupa donde le toca.
            ref = int(entry[ts_field])
            for i, h in enumerate(history[:dedup_scan] if dedup_scan else history):
                if same(h) and abs(ref - int(h.get(ts_field) or 0)) < dedup_window:
                    history.pop(i)      # quita la vieja; se reinserta arriba con fecha fresca
                    break

    history.insert(0, entry)
    history = _roll(kind, history)
    with swallow('history', 'write', path=str(live)):
        write_json_atomic(live, history, indent=2, keep_backup=True)


def read_all(kind: str, *, since: int = 0) -> list:
    """Historial COMPLETO (vivo + archivo), de más reciente a más antiguo.

    `since` (epoch) permite no abrir los meses que quedan fuera del periodo pedido, que es lo
    que hace barata la retrospectiva: para «este mes» se lee un fichero, no doce.
    """
    _, arch_dir, ts_field = _paths(kind)
    out = list(read_live(kind))
    if arch_dir.is_dir():
        cutoff_month = time.strftime('%Y-%m', time.localtime(since)) if since else ''
        for f in sorted(arch_dir.iterdir(), reverse=True):
            if not _MONTH_RE.match(f.name):
                continue
            # '0000-00' (entradas sin fecha) siempre entra: no se puede descartar por periodo.
            month = f.stem
            if cutoff_month and month != '0000-00' and month < cutoff_month:
                continue
            out.extend(read_json_safe(f, default=[], component='history') or [])
    if since:
        out = [e for e in out if int(e.get(ts_field) or 0) >= since]
    out.sort(key=lambda e: int(e.get(ts_field) or 0), reverse=True)
    return out


def clear(kind: str) -> None:
    """Vacía el vivo y el archivo entero. Sólo lo llama el botón explícito de la UI."""
    live, arch_dir, _ = _paths(kind)
    write_json_atomic(live, [], indent=2)
    if arch_dir.is_dir():
        for f in list(arch_dir.iterdir()):
            if _MONTH_RE.match(f.name):
                with swallow('history', 'clear', path=str(f)):
                    f.unlink()
