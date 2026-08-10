#!/usr/bin/env python3
"""
Hornear anime con Anime4K — la otra mitad del escalador que esta app ya es.

**Qué hace**: coge un episodio de la biblioteca y lo vuelve a codificar con la cadena de shaders
`Mode A+A UL + Thin` aplicada, dejando un fichero HERMANO `<nombre>.a4k.mkv`. A partir de ahí ese
fichero ES el episodio (lo resuelve `anime._find_video`), y el original se queda intacto al lado.

**Por qué existe**: Anime4K en vivo es una red convolucional sobre cada fotograma; una tablet no la
mueve en la cadena buena. Horneando una vez en la RTX, cualquier aparato ve la calidad del PC sin
gastar nada — y el reproductor del móvil puede apagar sus shaders del todo.

## Tres cosas que no son negociables

1. **El original NUNCA se toca.** Es lo que siembra qBittorrent: reescribirlo en el sitio le cambia
   el tamaño y el hash y rompe el torrent en silencio. Salida a fichero aparte, siempre.
2. **Un trabajo a la vez, y no a la vez que el escalador de manga.** MEDIDO en la RTX 5070: con la
   cadena UL la GPU va al 70 % de media y 89 % de pico; lanzar 2 procesos en paralelo sólo da ×1,18
   y 3 dan ×1,19. No hay paralelismo que rascar (los 8 shaders van encadenados, cada uno necesita el
   fotograma entero del anterior). Así que la cola es de uno y punto.
3. **Vulkan sólo existe en Windows.** libplacebo desde esta WSL da `VK_ERROR_INCOMPATIBLE_DRIVER`,
   así que se lanza `ffmpeg.exe` por interop.

## Trampas del filtro (costaron un rato)

- libplacebo acepta **UN SOLO** `custom_shader_path` → los 8 `.glsl` de la cadena se **concatenan**
  en uno. El formato `//!HOOK` de mpv permite varios hooks por fichero, así que basta pegarlos.
- En la cadena de filtros de ffmpeg, `\\` y `:` son separadores → una ruta Windows dentro del filtro
  no hay forma de escaparla bien. Solución: el proceso corre **con el cwd en la carpeta del shader**
  y el filtro lleva sólo el nombre del fichero.

Endpoints (prefijo /api/anime/upscale):
  POST /start   { path }            → { id }        encola un episodio
  POST /cancel  { id }              → mata el trabajo y borra el fichero a medias
  POST /discard { path }            → borra la horneada (se vuelve al original)
  GET  /status                      → { actual, cola, hechos, disponible }
"""
import os
import re
import queue
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path

from flask import Blueprint, jsonify, request

from api.observability import record_error

anime_upscale_bp = Blueprint('anime_upscale', __name__)

# ── La cadena ────────────────────────────────────────────────────────────────
# Literalmente el CTRL+9 del `input.conf` del PC, en el mismo orden. Es la que el usuario eligió
# tras comparar A HQ y ésta a pantalla partida en la tablet.
CADENA = [
    'Anime4K_Clamp_Highlights',
    'Anime4K_Restore_CNN_UL',
    'Anime4K_Upscale_CNN_x2_UL',
    'Anime4K_AutoDownscalePre_x2',
    'Anime4K_AutoDownscalePre_x4',
    'Anime4K_Restore_CNN_M',
    'Anime4K_Upscale_CNN_x2_M',
    'Anime4K_Thin_HQ',
]

_SHADER_DIRS = [
    '/mnt/c/Program Files (x86)/mpv/mpv/shaders',
    '/mnt/c/Program Files/mpv/mpv/shaders',
    '/mnt/c/Program Files (x86)/mpv/shaders',
]

# 2880 de ancho = el panel de la Pad 6. Subir a 3840 no aporta nada en un aparato de mano y cuesta
# un 78 % más de tiempo (la pasada cara corre a la resolución de SALIDA).
ANCHO, ALTO = 2880, 1620
CQ = 20                     # ~1,1 GB por episodio de 24 min. Bajarlo engorda rápido.
PRESET = 'p5'

_VIDEO_EXTS = {'.mkv', '.mp4', '.avi', '.mov', '.m4v', '.webm', '.ts', '.m2ts'}


# ── Estado ───────────────────────────────────────────────────────────────────
_cola: queue.Queue = queue.Queue()
_trabajos: dict = {}                 # id -> dict visible en /status
_orden: list = []                    # ids en el orden en que se encolaron
_lock = threading.Lock()
_hilo: threading.Thread | None = None
_proc_actual: subprocess.Popen | None = None
_cancelados: set = set()


def _pon(tid: str, **campos):
    with _lock:
        t = _trabajos.setdefault(tid, {'id': tid})
        t.update(campos)


# ── Entorno Windows ──────────────────────────────────────────────────────────

def _dir_shaders() -> str:
    for d in _SHADER_DIRS:
        if os.path.isdir(d):
            return d
    return ''


_ffmpeg_cache: str | None = None


def _ffmpeg() -> str:
    """Ruta a ffmpeg.exe (Windows). '' si no está: Vulkan no existe en esta WSL."""
    global _ffmpeg_cache
    if _ffmpeg_cache is not None:
        return _ffmpeg_cache
    from api.anime import _repair_win_env
    _repair_win_env()
    _ffmpeg_cache = ''
    hallado = shutil.which('ffmpeg.exe')
    if hallado:
        _ffmpeg_cache = hallado
    else:
        try:
            salida = subprocess.run(['/mnt/c/Windows/System32/where.exe', 'ffmpeg'],
                                    capture_output=True, text=True, timeout=10).stdout.strip()
            linea = salida.splitlines()[0].strip() if salida else ''
            if linea:
                _ffmpeg_cache = subprocess.check_output(
                    ['wslpath', '-u', linea], stderr=subprocess.DEVNULL, timeout=5).decode().strip()
        except (OSError, subprocess.SubprocessError, IndexError):
            pass
    return _ffmpeg_cache


def _a_windows(ruta: str) -> str:
    try:
        return subprocess.check_output(['wslpath', '-w', ruta],
                                       stderr=subprocess.DEVNULL, timeout=5).decode().strip()
    except (OSError, subprocess.SubprocessError):
        return ruta


def disponible() -> tuple[bool, str]:
    """(se puede hornear, motivo si no). Lo consulta la UI para no ofrecer un botón muerto."""
    if not _dir_shaders():
        return False, 'No encuentro los shaders de Anime4K (carpeta shaders de mpv).'
    if not _ffmpeg():
        return False, 'Falta ffmpeg en Windows (hace falta Vulkan, que en WSL no hay).'
    return True, ''


def _shader_unido() -> str:
    """Concatena la cadena en un solo .glsl junto a los originales, y devuelve su NOMBRE."""
    d = _dir_shaders()
    destino = Path(d) / '_animanga_aa_ul_thin.glsl'
    piezas = [Path(d) / f'{n}.glsl' for n in CADENA]
    faltan = [p.name for p in piezas if not p.exists()]
    if faltan:
        raise FileNotFoundError(f'faltan shaders: {", ".join(faltan)}')
    # Se rehace sólo si alguna pieza es más nueva: son 700 KB de texto.
    if not destino.exists() or destino.stat().st_mtime < max(p.stat().st_mtime for p in piezas):
        try:
            destino.write_text('\n'.join(p.read_text(encoding='utf-8', errors='replace')
                                         for p in piezas), encoding='utf-8')
        except PermissionError:
            # `Program Files` sin permiso de escritura: se cae al TEMP de Windows.
            tmp = Path(_temp_windows()) / '_animanga_aa_ul_thin.glsl'
            tmp.write_text('\n'.join(p.read_text(encoding='utf-8', errors='replace')
                                     for p in piezas), encoding='utf-8')
            return str(tmp)
    return str(destino)


def _temp_windows() -> str:
    for c in ('/mnt/c/Windows/Temp',):
        if os.path.isdir(c):
            return c
    return '/tmp'


# ── Duración y progreso ──────────────────────────────────────────────────────

def _duracion(video: str) -> float:
    try:
        from api.anime import _video_duration
        return float(_video_duration(video) or 0)
    except Exception:
        return 0.0


_RE_TIEMPO = re.compile(r'^out_time_us=(\d+)', re.M)


# ── El horno ─────────────────────────────────────────────────────────────────

def _salida_de(video: str) -> str:
    p = Path(video)
    return str(p.with_name(p.stem + '.a4k.mkv'))


def _manga_ocupado() -> bool:
    """El escalador de manga y esto se pelean por la misma GPU: si van a la vez no acaba ninguno."""
    try:
        from api.upscale import get_upscale_status
        estados = get_upscale_status() or {}
        return any((v or {}).get('status') in ('processing', 'running', 'upscaling', 'queued')
                   for v in estados.values() if isinstance(v, dict))
    except Exception:
        return False


def _hornear(tid: str, video: str) -> None:
    global _proc_actual
    salida = _salida_de(video)
    # ⚠️ El fichero a medias NO puede acabar en `.mkv`: vive en la carpeta de la serie, y el
    # escaneo de episodios mira por extensión — un horneado cancelado salía como episodio fantasma.
    # Con `.parcial` queda fuera por extensión Y por `_es_a4k` (su stem sigue acabando en `.a4k`).
    parcial = salida[:-4] + '.parcial'
    total = _duracion(video)
    shader = _shader_unido()
    cwd = os.path.dirname(shader)

    cmd = [
        _ffmpeg(), '-hide_banner', '-nostdin', '-loglevel', 'error', '-y',
        '-init_hw_device', 'vulkan=vk:0', '-filter_hw_device', 'vk',
        '-i', _a_windows(video),
        '-map', '0',                                   # vídeo + audios + subs + fuentes adjuntas
        '-vf', ('format=yuv420p,hwupload,'
                f'libplacebo=w={ANCHO}:h={ALTO}:custom_shader_path={os.path.basename(shader)},'
                'hwdownload,format=yuv420p'),
        '-c', 'copy', '-c:v', 'hevc_nvenc', '-preset', PRESET, '-cq', str(CQ),
        '-progress', 'pipe:1', '-nostats',
        '-f', 'matroska',                  # el nombre acaba en `.parcial`: hay que decirle el envase
        _a_windows(parcial),
    ]

    _pon(tid, estado='horneando', comenzado=time.time(), porcentaje=0.0, segundos=0.0,
         total=total, salida=salida)
    proc = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, bufsize=1)
    _proc_actual = proc
    try:
        for linea in proc.stdout:
            m = _RE_TIEMPO.match(linea)
            if m and total > 0:
                seg = int(m.group(1)) / 1_000_000
                t0 = _trabajos.get(tid, {}).get('comenzado') or time.time()
                trans = max(time.time() - t0, 0.001)
                # Lo que queda se estima con el ritmo REAL de este episodio, no con una constante:
                # el ritmo depende del grano y de si la GPU está compartida.
                restante = (total - seg) / (seg / trans) if seg > 1 else 0
                _pon(tid, segundos=seg, porcentaje=min(seg * 100.0 / total, 99.9),
                     quedan=round(restante))
        proc.wait()
        err = (proc.stderr.read() or '').strip()
    finally:
        _proc_actual = None

    if tid in _cancelados:
        _borrar(parcial)
        _pon(tid, estado='cancelado', porcentaje=0.0)
        return
    if proc.returncode != 0 or not os.path.exists(parcial):
        _borrar(parcial)
        _pon(tid, estado='error', error=err[-500:] or f'ffmpeg salió con {proc.returncode}')
        record_error('anime_upscale', RuntimeError(err[-300:] or 'ffmpeg falló'), video=video)
        return
    # El rename es lo ÚLTIMO: hasta aquí `<nombre>.a4k.mkv` no existe, así que un corte de luz a
    # mitad no deja al reproductor eligiendo un fichero truncado como si fuera el episodio bueno.
    os.replace(parcial, salida)
    _pon(tid, estado='hecho', porcentaje=100.0, quedan=0,
         tamano=os.path.getsize(salida), terminado=time.time())


def _borrar(p: str) -> None:
    # Windows suelta el fichero un instante DESPUÉS de matar el proceso: al primer intento el
    # borrado fallaba y el `.parcial` (1 GB) se quedaba ahí para siempre. Medido: sale al 2º intento.
    for _ in range(10):
        try:
            os.remove(p)
            return
        except FileNotFoundError:
            return
        except OSError:
            time.sleep(0.5)


def _bucle() -> None:
    while True:
        tid, video = _cola.get()
        try:
            if tid in _cancelados:
                _pon(tid, estado='cancelado')
                continue
            # espera activa cada 5 s en vez de un candado compartido con upscale.py.
            # Si algún día hay más productores de trabajo GPU, sacar un semáforo a un módulo común.
            esperado = 0
            while _manga_ocupado() and tid not in _cancelados and esperado < 3600:
                _pon(tid, estado='esperando', motivo='el escalador de manga tiene la GPU')
                time.sleep(5)
                esperado += 5
            if tid in _cancelados:
                _pon(tid, estado='cancelado')
                continue
            _hornear(tid, video)
        except Exception as e:                          # noqa: BLE001 — un fallo no mata la cola
            _pon(tid, estado='error', error=str(e))
            record_error('anime_upscale', e, video=video)
        finally:
            _cola.task_done()


def _arranca_hilo() -> None:
    global _hilo
    with _lock:
        if _hilo is None or not _hilo.is_alive():
            _hilo = threading.Thread(target=_bucle, name='anime-a4k', daemon=True)
            _hilo.start()


# ── Endpoints ────────────────────────────────────────────────────────────────

@anime_upscale_bp.route('/start', methods=['POST'])
def start():
    datos = request.get_json(silent=True) or {}
    video = (datos.get('path') or '').strip()
    if not video:
        return jsonify({'error': 'falta path'}), 400

    from api.anime import a4k_de, original_de
    # Si llega la ruta de una horneada (la lista ya devuelve ésa), se hornea el ORIGINAL.
    video = original_de(video) or video
    if not os.path.isfile(video) or Path(video).suffix.lower() not in _VIDEO_EXTS:
        return jsonify({'error': 'ese fichero no está'}), 404
    if a4k_de(video):
        return jsonify({'error': 'ya está escalado'}), 409

    ok, motivo = disponible()
    if not ok:
        return jsonify({'error': motivo}), 503

    with _lock:
        for t in _trabajos.values():
            if t.get('video') == video and t.get('estado') in ('en cola', 'esperando', 'horneando'):
                return jsonify({'id': t['id'], 'ya': True})

    tid = uuid.uuid4().hex[:12]
    _pon(tid, video=video, nombre=Path(video).name, estado='en cola', porcentaje=0.0,
         encolado=time.time())
    _orden.append(tid)
    _cola.put((tid, video))
    _arranca_hilo()
    return jsonify({'id': tid})


@anime_upscale_bp.route('/cancel', methods=['POST'])
def cancel():
    tid = ((request.get_json(silent=True) or {}).get('id') or '').strip()
    if tid not in _trabajos:
        return jsonify({'error': 'no existe'}), 404
    _cancelados.add(tid)
    if _trabajos[tid].get('estado') == 'horneando' and _proc_actual:
        try:
            _proc_actual.kill()
        except OSError:
            pass
    else:
        _pon(tid, estado='cancelado')
    return jsonify({'ok': True})


@anime_upscale_bp.route('/discard', methods=['POST'])
def discard():
    """Tirar la horneada y volver al original. Borrar 1,1 GB se pide explícitamente."""
    from api.anime import a4k_de
    video = ((request.get_json(silent=True) or {}).get('path') or '').strip()
    horneada = a4k_de(video)
    if not horneada:
        return jsonify({'error': 'no hay versión escalada'}), 404
    _borrar(horneada)
    return jsonify({'ok': True})


@anime_upscale_bp.route('/status', methods=['GET'])
def status():
    ok, motivo = disponible()
    with _lock:
        trabajos = [dict(_trabajos[t]) for t in _orden if t in _trabajos]
    activos = [t for t in trabajos if t.get('estado') in ('en cola', 'esperando', 'horneando')]
    return jsonify({
        'disponible': ok,
        'motivo': motivo,
        'actual': next((t for t in activos if t['estado'] != 'en cola'), None),
        'cola': [t for t in activos if t['estado'] == 'en cola'],
        'hechos': [t for t in trabajos if t.get('estado') in ('hecho', 'error', 'cancelado')][-20:],
        'cadena': 'Mode A+A UL + Thin',
        'resolucion': f'{ANCHO}x{ALTO}',
    })
