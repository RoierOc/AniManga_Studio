#!/usr/bin/env python3
"""Raíces de biblioteca de manga repartidas en VARIOS DISCOS.

Hasta aquí la biblioteca de manga era UNA carpeta (`MANGA_DIR`) más su gemela escalada
(`UPSCALED_DIR`). Eso se rompe en cuanto el disco se llena: el usuario quiere seguir
descargando y escalando en otro disco sin que la obra se parta en dos obras distintas.
El anime ya vivía repartido (`storage._anime_dirs`, un `local_path` por serie); el manga no.

## El modelo

Una **raíz** es un PAR de carpetas en el mismo disco: originales + escalados.
Van juntas a propósito — si mañana desenchufas ese disco, se va la obra ENTERA (arte y
escalado), no media. Nunca se escala en un disco distinto del que tiene el original.

Una **obra** es su NOMBRE DE CARPETA, exactamente como antes. Si la misma carpeta existe en
dos raíces, es LA MISMA OBRA y sus capítulos se ven unidos: `series_pages()` funde los
ficheros de todas las raíces. Por eso descargar los caps 1-10 en C: y los 11-20 en D: no
crea dos entradas ni parte el tomo al exportar — es una sola obra con las páginas repartidas.

## Dónde se ESCRIBE

`series_dir(title)` = la raíz que ya tiene esa obra (la que más páginas suyas tiene, para
no dispersar más de lo necesario); si no está en ninguna, la raíz ACTIVA. Se puede forzar
por petición (`root_id`), que es lo que usa el selector de carpeta al descargar/escalar.

## Lo que NO se reparte

El ESTADO (progreso, historial, identidad, cachés) sigue viviendo solo en la raíz base, vía
`manga_dir()`. Repartirlo no daría nada y multiplicaría por N los sitios donde se corrompe.

## Biblioteca oculta

En modo oculto hay UNA sola raíz, la oculta. Las raíces extra son de la biblioteca normal y
no se consultan: mezclarlas delataría la oculta al listar/medir. Ver `runtime.get_library_mode`.
"""
from __future__ import annotations

import os
import re
import shutil
import threading
from pathlib import Path

from flask import Blueprint, jsonify, request

from api.observability import record_error
from api.platform import is_wsl
from api.runtime import (DATA_ROOT, MANGA_DIR, UPSCALED_DIR, get_library_mode,
                         manga_dir, read_json_safe, upscaled_dir, write_json_atomic)

roots_bp = Blueprint('roots', __name__)

# Machine-specific a propósito: las rutas de disco NO se sincronizan entre equipos
# (el perfil de sync.py no lo incluye) — el D: de este PC no es el D: de otro.
_ROOTS_FILE = Path(DATA_ROOT) / 'manga_roots.json'
_lock = threading.RLock()

BASE_ID = 'base'
_IMAGE_EXTS = ('jpg', 'jpeg', 'png', 'webp')


def _to_local(raw: str) -> str:
    """`D:\\Manga` → `/mnt/d/Manga` cuando corremos bajo WSL. El usuario escribe la ruta
    como la ve en Windows; nadie va a teclear /mnt/d a mano."""
    raw = (raw or '').strip().strip('"')
    if is_wsl() and re.match(r'^[A-Za-z]:[/\\]', raw):
        try:
            import subprocess
            return subprocess.check_output(
                ['wslpath', '-u', raw], stderr=subprocess.DEVNULL, timeout=3).decode().strip()
        except Exception:
            pass
    return raw


def _read() -> dict:
    return read_json_safe(_ROOTS_FILE, {}, component='roots') or {}


def _write(data: dict) -> None:
    write_json_atomic(_ROOTS_FILE, data, indent=2, keep_backup=True)


def _base_root() -> dict:
    return {'id': BASE_ID, 'label': 'Principal',
            'manga': str(MANGA_DIR), 'upscaled': str(UPSCALED_DIR), 'removable': False}


def roots() -> list[dict]:
    """Raíces activas, la base siempre la primera. En modo oculto, SOLO la oculta."""
    if get_library_mode() != 'normal':
        return [{'id': 'hidden', 'label': 'Oculta', 'manga': str(manga_dir()),
                 'upscaled': str(upscaled_dir()), 'removable': False}]
    out = [_base_root()]
    for r in _read().get('roots', []):
        try:
            out.append({'id': str(r['id']), 'label': r.get('label') or Path(r['manga']).name,
                        'manga': str(r['manga']), 'upscaled': str(r['upscaled']),
                        'removable': True})
        except Exception:
            continue
    return out


def active_id() -> str:
    """Raíz por defecto para obras NUEVAS. Si la guardada ya no existe, la base."""
    want = _read().get('active') or BASE_ID
    return want if any(r['id'] == want for r in roots()) else BASE_ID


def root_by_id(rid: str | None) -> dict | None:
    if not rid:
        return None
    return next((r for r in roots() if r['id'] == rid), None)


def manga_roots() -> list[Path]:
    return [Path(r['manga']) for r in roots()]


def upscaled_roots() -> list[Path]:
    return [Path(r['upscaled']) for r in roots()]


# ── Resolver dónde vive (y dónde va) una obra ────────────────────────────────

def _count_pages(folder: Path) -> int:
    try:
        return sum(1 for e in os.scandir(folder)
                   if e.is_file() and e.name.rsplit('.', 1)[-1].lower() in _IMAGE_EXTS)
    except OSError:
        return 0


def titulo_en_disco(title: str) -> str:
    """El título de una obra como **nombre de carpeta, jamás como ruta**.

    Es la única puerta por la que un título del cliente se convierte en un sitio del disco, y
    tiene que estar aquí y no en cada endpoint: `series_dir`, `series_up_dir` y sus plurales son
    el cuello por el que pasan el lector, la descarga, el escalado, el trasplante y la
    exportación. Una guarda por ruta de entrada es una guarda que el próximo endpoint se olvida.

    🔴 **Sin esto se pueden escribir ficheros donde sea.** `Path('/biblioteca') / title` NO
    contiene nada: en `pathlib`, si `title` es absoluto **gana él** (`Path('/a') / '/etc/x'` ==
    `Path('/etc/x')`), y `../` atraviesa igual. Como `/api/download/download_source_chapter`
    recibe además las URLs de las páginas, quien llame elige el contenido Y el destino. Que haga
    falta el token no lo arregla: la app está publicada en el tailnet y la regla del proyecto es
    que **un endpoint no acepta un destino del cliente**, sea cual sea la excusa.

    Se queda con el ÚLTIMO tramo en vez de rechazar, porque un nombre de carpeta no puede llevar
    barras en ningún sistema: para un título legítimo esto no cambia nada, y para uno con truco
    lo deja dentro de la biblioteca, que es la propiedad que importa.
    """
    limpio = (title or '').replace('\\', '/').split('/')[-1].strip().strip('.')
    if not limpio:
        raise ValueError(f'título no válido como carpeta: {title!r}')
    return limpio


def series_dirs(title: str) -> list[Path]:
    """Carpetas de originales que EXISTEN para esta obra, en orden de raíz."""
    title = titulo_en_disco(title)
    return [p for p in (Path(r['manga']) / title for r in roots()) if p.is_dir()]


def series_up_dirs(title: str) -> list[Path]:
    title = titulo_en_disco(title)
    return [p for p in (Path(r['upscaled']) / title for r in roots()) if p.is_dir()]


def series_root(title: str, root_id: str | None = None) -> dict:
    """La raíz donde ESCRIBIR esta obra.

    `root_id` explícito manda (el selector de carpeta). Si no, la raíz que ya tiene más
    páginas suyas — así lo nuevo cae junto a lo viejo en vez de dispersarse más. Y si la
    obra no está en ninguna, la raíz activa.
    """
    title = titulo_en_disco(title)
    forced = root_by_id(root_id)
    if forced:
        return forced
    best, best_n = None, 0
    for r in roots():
        n = _count_pages(Path(r['manga']) / title)
        if n > best_n:
            best, best_n = r, n
    if best:
        return best
    # Sin páginas en ninguna: respeta una carpeta ya creada (descarga a medias) antes de
    # mandar la obra a otro disco.
    for r in roots():
        if (Path(r['manga']) / title).is_dir():
            return r
    return root_by_id(active_id()) or _base_root()


def series_dir(title: str, root_id: str | None = None) -> Path:
    return Path(series_root(title, root_id)['manga']) / titulo_en_disco(title)


def series_up_dir(title: str, root_id: str | None = None) -> Path:
    return Path(series_root(title, root_id)['upscaled']) / titulo_en_disco(title)


def up_dir_for(page_path) -> Path:
    """Carpeta escalada que le toca a una página ORIGINAL concreta: la del MISMO disco.

    Escalar nunca cruza de disco — si no, quitar el disco D dejaría el original en C y su
    escalado en D (o al revés) y la obra quedaría a medias sin que nada avise.
    """
    p = Path(page_path).resolve()
    for r in roots():
        try:
            rel = p.relative_to(Path(r['manga']).resolve())
        except (ValueError, OSError):
            continue
        return Path(r['upscaled']) / rel.parts[0]
    return Path(upscaled_dir()) / Path(page_path).parent.name


# ── Vista unificada: la obra es UNA aunque esté repartida ────────────────────

def series_titles() -> dict[str, list[Path]]:
    """{nombre de obra: [carpetas de originales]} — unión de todas las raíces.

    Deduplica por NOMBRE: la misma carpeta en dos discos es la misma obra, no dos.
    """
    out: dict[str, list[Path]] = {}
    for root in manga_roots():
        try:
            entries = sorted(os.scandir(root), key=lambda e: e.name)
        except OSError:
            continue          # raíz en un disco desenchufado: se ignora, no se rompe
        for e in entries:
            if e.is_dir() and not e.name.startswith('.'):
                out.setdefault(e.name, []).append(Path(e.path))
    return out


def series_pages(title: str, upscaled: bool = False, prefix: str = '') -> dict[str, Path]:
    """{nombre de fichero: ruta} de las páginas de una obra, FUNDIDAS entre discos.

    Ésta es la pieza que hace que «caps 1-10 en C: y 11-20 en D:» sea una sola obra: quien
    lista páginas (lector, exportador, escalado, contadores) ve un único conjunto.
    Si el mismo nombre aparece en dos raíces gana la primera — un duplicado real es un
    accidente, y elegir siempre la misma evita que la página baile entre recargas.
    """
    out: dict[str, Path] = {}
    dirs = series_up_dirs(title) if upscaled else series_dirs(title)
    for d in dirs:
        try:
            entries = os.scandir(d)
        except OSError:
            continue
        for e in entries:
            if not e.is_file() or e.name in out:
                continue
            if prefix and not e.name.startswith(prefix):
                continue
            if e.name.rsplit('.', 1)[-1].lower() in _IMAGE_EXTS:
                out[e.name] = Path(e.path)
    return out


def chapter_dir(title: str, prefix: str) -> Path | None:
    """La carpeta (disco) donde viven las páginas de ESTE capítulo, o None si no está.

    Un capítulo se descarga entero de una vez, así que vive en un solo disco aunque la obra
    esté repartida. Quien REESCRIBE páginas en sitio — la traducción — necesita esto y no
    `series_dir()`: escribir el capítulo traducido en el disco «principal» de la obra cuando su
    original está en el otro no lo traduce, lo DUPLICA, y deja la obra con el mismo capítulo dos
    veces, en dos idiomas y en dos discos.
    """
    for d in series_dirs(title):
        try:
            if next((e for e in os.scandir(d) if e.is_file() and e.name.startswith(prefix)), None):
                return d
        except OSError:
            continue
    return None


def glob_series(pattern: str) -> list[Path]:
    """`*/.identity.json` (o similar) en TODAS las raíces, una entrada por OBRA.

    Deduplica por nombre de carpeta: una obra repartida tiene su `.identity.json` en cada
    disco, y los barridos de identidad/salud la contarían dos veces (y reportarían el doble
    de problemas) si vieran las dos copias.
    """
    out, seen = [], set()
    for root in manga_roots():
        try:
            hits = sorted(root.glob(pattern))
        except OSError:
            continue
        for p in hits:
            key = p.parent.name
            if key not in seen:
                seen.add(key)
                out.append(p)
    return out


def find_file(rel: str, prefer_upscaled: bool = True) -> Path | None:
    """`<obra>/<pagina.jpg>` → ruta real en cualquier raíz, o None.

    Lo usa el servidor de imágenes: la URL de una página no lleva (ni debe llevar) el disco.
    """
    rel = str(rel).lstrip('/')
    order = roots()
    for r in order:
        keys = ('upscaled', 'manga') if prefer_upscaled else ('manga', 'upscaled')
        for k in keys:
            p = Path(r[k]) / rel
            if p.exists():
                return p
    return None


# ── API ──────────────────────────────────────────────────────────────────────

_vhdx_host: list = []          # [] = sin calcular · [None] = no aplica · [Path] = punto de montaje


def _wsl_vhdx_host() -> Path | None:
    """Unidad de Windows donde vive el disco virtual de WSL, o None.

    ⚠️ Esto NO es cosmético. Bajo WSL, la raíz de Linux (`/`, ext4) es un FICHERO `.vhdx` que
    vive dentro de una unidad de Windows — normalmente C:. `shutil.disk_usage('/')` informa del
    espacio libre DENTRO de ese fichero, que puede ser enorme mientras la unidad que lo aloja
    está a rebosar: MEDIDO en la máquina del usuario, 813 GiB «libres» en `/` con **19 GiB**
    reales en C:. El .vhdx sólo puede crecer mientras quede sitio en C:, así que 19 es el techo.

    Enseñar 813 sería exactamente el número que empuja a seguir llenando el disco que ya está
    al 99% — y el usuario añadió un segundo disco PRECISAMENTE porque se había quedado sin
    espacio. Un dato que miente en la dirección que hace daño es peor que no dar dato.

    Búsqueda acotada (globs concretos, no un `find` por todo /mnt) y cacheada: la ruta no
    cambia mientras el proceso vive. Si no se encuentra, se devuelve None y NO se acota nada —
    adivinar mal sería otra mentira.
    """
    if _vhdx_host:
        return _vhdx_host[0]
    host = None
    if is_wsl():
        pats = ('Users/*/AppData/Local/wsl/*/ext4.vhdx',
                'Users/*/AppData/Local/Packages/*/LocalState/ext4.vhdx')
        try:
            for mnt in sorted(Path('/mnt').iterdir()):
                if len(mnt.name) != 1:
                    continue
                if any(next(mnt.glob(p), None) for p in pats):
                    host = mnt
                    break
        except OSError:
            pass
    _vhdx_host.append(host)
    return host


def _usage(path: str) -> dict:
    """Espacio del disco de una raíz. `free` es el que se puede USAR de verdad (ver arriba)."""
    p = Path(path)
    try:
        du = shutil.disk_usage(str(p) if p.exists() else (p.anchor or '/'))
    except OSError:
        return {'free': 0, 'total': 0}
    out = {'free': du.free, 'total': du.total}

    # Sólo para rutas que NO están en /mnt/<letra>: ésas ya son unidades de Windows reales y su
    # cifra es la buena. Una ruta de la raíz de Linux está dentro del .vhdx.
    try:
        if not str(p.resolve()).startswith('/mnt/'):
            host = _wsl_vhdx_host()
            if host is not None:
                hdu = shutil.disk_usage(str(host))
                if hdu.free < out['free']:
                    out['free'] = hdu.free
                    # Se dice POR QUÉ el número es más bajo de lo que canta el sistema de
                    # archivos: sin explicación parece un error nuestro.
                    out['capped_by'] = f'{host.name.upper()}:'
                    out['fs_free'] = du.free
    except OSError:
        pass
    return out


@roots_bp.route('')
@roots_bp.route('/')
def list_roots():
    """Raíces + espacio libre. El espacio libre no es adorno: es EL motivo por el que
    alguien añade un disco, y elegir a ciegas es cómo se llena el que ya estaba lleno."""
    out = []
    for r in roots():
        d = dict(r)
        d['online'] = Path(r['manga']).is_dir()
        d.update(_usage(r['manga']))
        d['active'] = r['id'] == active_id()
        out.append(d)
    return jsonify({'roots': out, 'active': active_id()})


@roots_bp.route('/add', methods=['POST'])
def add_root():
    data = request.get_json(silent=True) or {}
    manga = _to_local(data.get('manga') or '')
    if not manga:
        return jsonify({'error': 'Falta la carpeta'}), 400
    # La gemela escalada por defecto va AL LADO de la de originales, mismo disco.
    up = _to_local(data.get('upscaled') or '') or f'{manga}_Upscaled'
    try:
        Path(manga).mkdir(parents=True, exist_ok=True)
        Path(up).mkdir(parents=True, exist_ok=True)
    except OSError as e:
        # "No se pudo crear" ≠ "no existe": el usuario necesita saber cuál de las dos es.
        record_error('roots', e, manga=manga, upscaled=up)
        return jsonify({'error': f'No se pudo usar esa carpeta: {e}'}), 400

    with _lock:
        data_all = _read()
        existing = data_all.get('roots', [])
        known = {str(Path(r['manga']).resolve()) for r in roots()}
        try:
            if str(Path(manga).resolve()) in known:
                return jsonify({'error': 'Esa carpeta ya está en la biblioteca'}), 400
        except OSError:
            pass
        rid = f'r{max([0] + [int(r["id"][1:]) for r in existing if r["id"][1:].isdigit()]) + 1}'
        existing.append({'id': rid, 'label': data.get('label') or Path(manga).name,
                         'manga': manga, 'upscaled': up})
        data_all['roots'] = existing
        _write(data_all)
    return jsonify({'ok': True, 'id': rid})


@roots_bp.route('/remove', methods=['POST'])
def remove_root():
    """Quita la raíz de la biblioteca. NO borra nada del disco: dejar de mirar un disco y
    borrarlo no son la misma acción, y la segunda no se hace desde un botón de ajustes."""
    rid = (request.get_json(silent=True) or {}).get('id') or ''
    if rid == BASE_ID:
        return jsonify({'error': 'La raíz principal no se puede quitar'}), 400
    with _lock:
        data = _read()
        before = len(data.get('roots', []))
        data['roots'] = [r for r in data.get('roots', []) if str(r.get('id')) != rid]
        if len(data['roots']) == before:
            return jsonify({'error': 'No existe esa carpeta'}), 404
        if data.get('active') == rid:
            data['active'] = BASE_ID
        _write(data)
    return jsonify({'ok': True})


@roots_bp.route('/active', methods=['POST'])
def set_active():
    rid = (request.get_json(silent=True) or {}).get('id') or BASE_ID
    if not root_by_id(rid):
        return jsonify({'error': 'No existe esa carpeta'}), 404
    with _lock:
        data = _read()
        data['active'] = rid
        _write(data)
    return jsonify({'ok': True, 'active': rid})


@roots_bp.route('/where/<path:title>')
def where(title):
    """En qué discos vive una obra y cuántas páginas hay en cada uno.

    Es la respuesta a «¿por qué esta obra pesa distinto de lo que creo?»: sin esto, una obra
    repartida se ve unida en todas partes y no hay forma de saber qué disco tiene qué.
    """
    out = []
    for r in roots():
        d = Path(r['manga']) / title
        u = Path(r['upscaled']) / title
        n, nu = _count_pages(d), _count_pages(u)
        if n or nu or d.is_dir():
            out.append({'id': r['id'], 'label': r['label'], 'pages': n, 'upscaled': nu,
                        'online': Path(r['manga']).is_dir()})
    return jsonify({'title': title, 'roots': out,
                    'target': series_root(title)['id'], 'split': len(out) > 1})
