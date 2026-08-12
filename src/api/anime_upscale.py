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

# ── Las cadenas ──────────────────────────────────────────────────────────────
# Tres, porque «lo mejor» y «lo quiero ya» son necesidades distintas y las dos son legítimas: para
# una serie que vas a ver esta noche, esperar 11 minutos por episodio no compensa.
#
# `min_por_min` = minutos de horno por minuto de vídeo. MEDIDO en la RTX 5070 con la GPU libre y el
# emulador cerrado, las tres seguidas sobre el mismo clip (ver PROJECT_STATE). De ahí sale el tiempo
# que la interfaz enseña ANTES de encolar: un número inventado es peor que ninguno.
#
# ⚠️ Y va CALIBRADO con un episodio entero, no con el clip. La «rápida» medía 3,4 min extrapolando
# desde 60 s y tardó **4,2 min** de verdad: el clip no paga el arranque de Vulkan (~20 s), ni la
# compilación de los shaders, ni el muxado de 9 pistas de subtítulos y 19 fuentes. Prometer de menos
# es peor que prometer de más, así que los tres llevan el factor de abajo.
_CALIBRACION = 4.2 / 3.4    # medido: episodio real / lo que predecía el clip
PRESETS = {
    'rapida': {
        'etiqueta': 'Rápida',
        'detalle': 'Mode A con la red mediana. Limpia y escala; sin la segunda pasada ni el afinado '
                   'de líneas.',
        'min_por_min': 8.5 / 60 * _CALIBRACION,
        'cadena': [
            'Anime4K_Clamp_Highlights',
            'Anime4K_Restore_CNN_M',
            'Anime4K_Upscale_CNN_x2_M',
            'Anime4K_AutoDownscalePre_x2',
            'Anime4K_AutoDownscalePre_x4',
        ],
    },
    'equilibrada': {
        'etiqueta': 'Equilibrada',
        'detalle': 'Red grande y líneas afinadas. La mitad de tiempo que la máxima y muy cerca en '
                   'imagen.',
        'min_por_min': 17.1 / 60 * _CALIBRACION,
        'cadena': [
            'Anime4K_Clamp_Highlights',
            'Anime4K_Restore_CNN_VL',
            'Anime4K_Upscale_CNN_x2_VL',
            'Anime4K_AutoDownscalePre_x2',
            'Anime4K_AutoDownscalePre_x4',
            'Anime4K_Upscale_CNN_x2_M',
            'Anime4K_Thin_HQ',
        ],
    },
    'maxima': {
        'etiqueta': 'Máxima',
        'detalle': 'A+A con la red UL y las líneas afinadas. Es el CTRL+9 de mpv y el techo de '
                   'Anime4K: no hay nada por encima.',
        'min_por_min': 28.0 / 60 * _CALIBRACION,
        'cadena': [
            'Anime4K_Clamp_Highlights',
            'Anime4K_Restore_CNN_UL',
            'Anime4K_Upscale_CNN_x2_UL',
            'Anime4K_AutoDownscalePre_x2',
            'Anime4K_AutoDownscalePre_x4',
            'Anime4K_Restore_CNN_M',
            'Anime4K_Upscale_CNN_x2_M',
            'Anime4K_Thin_HQ',
        ],
    },
}
PRESET_POR_DEFECTO = 'maxima'

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


def _shader_unido(preset: str) -> str:
    """Concatena la cadena del preset en un solo .glsl y devuelve su RUTA."""
    d = _dir_shaders()
    destino = Path(d) / f'_animanga_{preset}.glsl'
    piezas = [Path(d) / f'{n}.glsl' for n in PRESETS[preset]['cadena']]
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
            tmp = Path(_temp_windows()) / f'_animanga_{preset}.glsl'
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


def _hornear(tid: str, video: str, preset: str) -> None:
    global _proc_actual
    salida = _salida_de(video)
    # ⚠️ El fichero a medias NO puede acabar en `.mkv`: vive en la carpeta de la serie, y el
    # escaneo de episodios mira por extensión — un horneado cancelado salía como episodio fantasma.
    # Con `.parcial` queda fuera por extensión Y por `_es_a4k` (su stem sigue acabando en `.a4k`).
    parcial = salida[:-4] + '.parcial'
    total = _duracion(video)
    shader = _shader_unido(preset)
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
        # 🔴 SIN ESTO EL AUDIO SE CORTA A LOS POCOS MINUTOS. La cadena de shaders va a 0,4x tiempo
        # real, así que ffmpeg va tirando del audio muy por delante del vídeo; al pasar del tope de
        # interleave (10 s por defecto) el muxer deja de esperar y suelta el audio ENTERO de golpe.
        # MEDIDO en el horneado de prueba: en el minuto 10, el vídeo estaba en el byte 536 MB y el
        # audio en el 17 MB — todo el audio en un bloque al principio. El fichero "tiene" su pista
        # completa (ffprobe la lista con 23:40), pero un reproductor lineal se queda sin audio por
        # delante y enmudece. `0` = no te rindas nunca, interleava bien.
        '-max_interleave_delta', '0',
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

    # ⚠️ `terminado` también al cancelar y al fallar, no sólo al acabar bien. El Centro de
    # Actividad poda lo terminado por ese sello y lo que no lo trae lo tira entero: sin esto, un
    # horneado cancelado o reventado no aparecía NUNCA en el historial — desaparecía sin más, que
    # es justo lo que hace pensar que el botón no hizo nada.
    if tid in _cancelados:
        _borrar(parcial)
        _pon(tid, estado='cancelado', porcentaje=0.0, terminado=time.time())
        return
    if proc.returncode != 0 or not os.path.exists(parcial):
        _borrar(parcial)
        _pon(tid, estado='error', terminado=time.time(),
             error=err[-500:] or f'ffmpeg salió con {proc.returncode}')
        record_error('anime_upscale', RuntimeError(err[-300:] or 'ffmpeg falló'), video=video)
        return
    # El rename es lo ÚLTIMO: hasta aquí `<nombre>.a4k.mkv` no existe, así que un corte de luz a
    # mitad no deja al reproductor eligiendo un fichero truncado como si fuera el episodio bueno.
    os.replace(parcial, salida)
    _pon(tid, estado='hecho', porcentaje=100.0, quedan=0,
         tamano=os.path.getsize(salida), terminado=time.time())
    # **Y ahora se dice.** Sin estas dos líneas el episodio horneado tardaba en «existir»: el
    # escaneo de la carpeta se sirve cacheado (TTL de 10 s + refresco en segundo plano) y la ficha
    # no se entera de nada, así que había que salir y volver a entrar para verlo. Se tira el caché
    # de ESA carpeta y se avisa por SSE, que es como se entera el resto de la app de lo demás.
    from api.anime import invalidar_escaneo
    from api.runtime import push_sse_event
    invalidar_escaneo(os.path.dirname(salida))
    push_sse_event('anime_upscale_done', path=salida)


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
        tid, video, preset = _cola.get()
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
            _hornear(tid, video, preset)
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

def _video_pedido(datos: dict):
    """El vídeo del trabajo. Devuelve (ruta|None, (mensaje, código)|None).

    Dos formas de nombrarlo, y **no son intercambiables según quién pregunte**:

    - `{anime_id, episode}` — la ruta la resuelve el SERVIDOR contra su biblioteca. Es la única
      que vale desde la red: lo único que el cliente controla es *qué episodio de qué serie que ya
      existe*. Misma puerta que usa el streaming del móvil (`video_de_biblioteca`).
    - `{path}` — sólo desde esta máquina. La app de escritorio ya corre aquí y tiene la ruta a
      mano de la propia lista, así que ahorrarle una resolución es gratis. **Desde fuera se
      rechaza**: aceptar una ruta de quien llama es recodificar —o borrar, en `/discard`—
      cualquier fichero del PC por HTTP.
    """
    from api.auth import peticion_local
    aid = str(datos.get('anime_id') or '').strip()
    ep = datos.get('episode')
    if aid and ep is not None:
        from api.anime import video_de_biblioteca
        try:
            ruta, err = video_de_biblioteca(aid, int(ep))
        except (TypeError, ValueError):
            return None, ('episode no es un número', 400)
        return (None, err) if err else (ruta, None)
    ruta = (datos.get('path') or '').strip()
    if not ruta:
        return None, ('falta anime_id + episode', 400)
    if not peticion_local():
        return None, ('un cliente remoto no puede mandar rutas: usa anime_id + episode', 403)
    return ruta, None


@anime_upscale_bp.route('/start', methods=['POST'])
def start():
    datos = request.get_json(silent=True) or {}
    video, err = _video_pedido(datos)
    if err:
        return jsonify({'error': err[0]}), err[1]

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

    preset = (datos.get('calidad') or PRESET_POR_DEFECTO).strip()
    if preset not in PRESETS:
        return jsonify({'error': f'calidad desconocida: {preset}'}), 400

    tid = uuid.uuid4().hex[:12]
    _pon(tid, video=video, nombre=Path(video).name, estado='en cola', porcentaje=0.0,
         encolado=time.time(), calidad=preset, calidad_etiqueta=PRESETS[preset]['etiqueta'])
    _orden.append(tid)
    _cola.put((tid, video, preset))
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
        _pon(tid, estado='cancelado', terminado=time.time())
    return jsonify({'ok': True})


@anime_upscale_bp.route('/discard', methods=['POST'])
def discard():
    """Tirar la horneada y volver al original. Borrar 1,1 GB se pide explícitamente."""
    from api.anime import a4k_de
    video, err = _video_pedido(request.get_json(silent=True) or {})
    if err:
        return jsonify({'error': err[0]}), err[1]
    horneada = a4k_de(video)
    if not horneada:
        return jsonify({'error': 'no hay versión escalada'}), 404
    _borrar(horneada)
    return jsonify({'ok': True})


#: Vocabulario de aquí → el del Centro de Actividad. `_mapStatus` del front habla en inglés y no
#: tiene por qué aprenderse el de este módulo; traducir en la frontera es una línea, y evita que
#: `horneando` caiga en el `return 'running'` por casualidad en vez de por decisión.
_ESTADO_ACTIVIDAD = {
    'en cola': 'queued', 'esperando': 'queued', 'horneando': 'running',
    'hecho': 'done', 'error': 'error', 'cancelado': 'cancelled',
}


def get_anime_upscale_tasks() -> dict:
    """El horneado, con la forma que consume el Centro de Actividad.

    🔴 **Esto faltaba y por eso el escalado de anime era invisible.** Todo lo que tarda en esta app
    —descargar, traducir, escalar manga, subtítulos— viaja en el MISMO retrato agregado
    (`/api/status/stream`), que es de lo que se alimenta Actividad. El horneado se escribió con su
    propio `/status` para su propia pantalla y nunca se enganchó, así que un proceso de **horas**
    no salía en el único sitio donde se mira qué está pasando: parecía colgado.

    Se traduce aquí y no en el navegador porque el resto del retrato ya llega traducido; que una
    sola fuente hablase otro idioma obligaría a que el front supiera de este módulo.
    """
    with _lock:
        crudos = [dict(_trabajos[t]) for t in _orden if t in _trabajos]
    salida = {}
    for t in crudos:
        salida[t['id']] = {
            'status': _ESTADO_ACTIVIDAD.get(t.get('estado'), 'running'),
            # El título es la SERIE (la carpeta), no el nombre del fichero: en Actividad las tareas
            # se agrupan por obra, y agrupar por nombre de fichero daría una tarjeta por episodio.
            'title': Path(t.get('video', '')).parent.name or 'Anime',
            'file': t.get('nombre', ''),
            'progress': round(t.get('porcentaje') or 0, 1),
            'quality': t.get('calidad_etiqueta', ''),
            'eta': t.get('quedan'),
            'error': t.get('error', ''),
            'ended_at': t.get('terminado'),
            '_ts': t.get('comenzado') or t.get('encolado'),
        }
    return salida


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
        # La UI no hardcodea ni las calidades ni sus tiempos: salen de aquí, que es donde están
        # los números MEDIDOS. Añadir un preset no obliga a tocar el frontend.
        'calidades': [
            {'id': k, 'etiqueta': v['etiqueta'], 'detalle': v['detalle'],
             'min_por_min': round(v['min_por_min'], 4)}
            for k, v in PRESETS.items()
        ],
        'por_defecto': PRESET_POR_DEFECTO,
        'resolucion': f'{ANCHO}x{ALTO}',
    })
