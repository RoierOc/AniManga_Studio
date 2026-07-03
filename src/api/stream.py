"""Streaming local para el player web embebido (estilo Crunchyroll).

El navegador no reproduce MKV directamente, así que ffmpeg remuxa al vuelo a
HLS (fMP4) SIN recodificar cuando el códec es compatible (h264/av1/vp9 +
aac/opus/mp3/flac) — copia pura, sin pérdida ni CPU. Solo si el códec no lo
soporta Chromium (p.ej. HEVC) se transcodifica con NVENC (fallback libx264).

Una sesión activa a la vez (app personal): abrir un episodio mata la sesión
anterior. Los segmentos viven en <tmp>/animanga_stream/<sid>/ y se limpian al
cerrar o al abrir la siguiente.
"""
import json
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from pathlib import Path

from flask import Blueprint, jsonify, request, send_from_directory

stream_bp = Blueprint('stream', __name__)

_SESS_ROOT = Path(tempfile.gettempdir()) / 'animanga_stream'
# Sesiones huérfanas de arranques anteriores (el server murió con streams vivos)
shutil.rmtree(_SESS_ROOT, ignore_errors=True)
_lock = threading.Lock()
_current = {'sid': None, 'proc': None}

# Códecs que Chromium reproduce en fMP4/MSE sin recodificar
_VIDEO_COPY = {'h264', 'av1', 'vp9'}
_AUDIO_COPY = {'aac', 'opus', 'mp3', 'flac'}


def _ffprobe(path: str) -> dict:
    r = subprocess.run(
        ['ffprobe', '-v', 'error', '-print_format', 'json',
         '-show_streams', '-show_format', path],
        capture_output=True, text=True, timeout=30)
    return json.loads(r.stdout or '{}')


def _kill_current():
    """Mata la sesión ffmpeg activa y borra sus segmentos. Llamar con _lock."""
    proc = _current.get('proc')
    if proc and proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
    sid = _current.get('sid')
    if sid:
        shutil.rmtree(_SESS_ROOT / sid, ignore_errors=True)
    _current['sid'] = None
    _current['proc'] = None


@stream_bp.route('/open', methods=['POST'])
def stream_open():
    """Abre una sesión de streaming para un episodio.
    Body: igual que /api/anime/play ({anime_id, episode, local_path?/info_hash?})
    + audio (índice relativo de pista, def. 0). Devuelve playlist + metadatos."""
    from api.anime import resolve_episode_video, _lib_read
    data = request.get_json(silent=True) or {}
    video, err = resolve_episode_video(data)
    if err:
        return jsonify({'error': err[0]}), err[1]

    try:
        info = _ffprobe(video)
    except Exception as e:
        return jsonify({'error': f'ffprobe: {e}'}), 500
    streams = info.get('streams') or []
    duration = float((info.get('format') or {}).get('duration') or 0)

    vstreams = [s for s in streams if s.get('codec_type') == 'video'
                and s.get('disposition', {}).get('attached_pic', 0) != 1]
    astreams = [s for s in streams if s.get('codec_type') == 'audio']
    sstreams = [s for s in streams if s.get('codec_type') == 'subtitle']
    if not vstreams:
        return jsonify({'error': 'sin pista de vídeo'}), 415

    def _track(s, rel):
        tags = s.get('tags') or {}
        return {'index': rel, 'codec': s.get('codec_name', ''),
                'lang': tags.get('language', ''), 'title': tags.get('title', '')}

    audio_idx = int(data.get('audio', 0))
    audio_idx = max(0, min(audio_idx, max(len(astreams) - 1, 0)))

    vcodec = vstreams[0].get('codec_name', '')
    acodec = astreams[audio_idx].get('codec_name', '') if astreams else ''
    # HEVC se COPIA (cero pérdida) si el navegador declara que puede decodificarlo
    # (hevc_ok, sondeado con MediaSource.isTypeSupported — real con el driver
    # VAAPI de NVIDIA + flags del shell). Si no, transcode de respaldo.
    v_copy = vcodec in _VIDEO_COPY or (vcodec == 'hevc' and bool(data.get('hevc_ok')))
    # El player lo pide cuando el navegador ACEPTÓ el códec pero no logró
    # decodificar ni un frame (p.ej. HEVC sin NVDEC): reabrir transcodificando.
    if data.get('force_transcode'):
        v_copy = False
    a_copy = acodec in _AUDIO_COPY

    sid = uuid.uuid4().hex[:12]
    sess = _SESS_ROOT / sid
    sess.mkdir(parents=True, exist_ok=True)

    def _cmd(video_args, pre_input=()):
        c = ['ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'warning', '-y',
             *pre_input, '-i', video, '-map', '0:v:0']
        if astreams:
            c += ['-map', f'0:a:{audio_idx}']
        c += video_args
        if astreams:
            c += (['-c:a', 'copy'] if a_copy else ['-c:a', 'aac', '-b:a', '192k', '-ac', '2'])
        # playlist_type EVENT: ffmpeg añade cada segmento al playlist al cerrarlo
        # (VOD lo escribe SOLO al terminar → un transcode largo nunca publicaba
        # el playlist y el open moría en 504). Al acabar escribe ENDLIST igual.
        c += ['-sn', '-dn',
              '-f', 'hls', '-hls_time', '4', '-hls_playlist_type', 'event',
              '-hls_segment_type', 'fmp4', '-hls_fmp4_init_filename', 'init.mp4',
              '-hls_segment_filename', str(sess / 'seg_%05d.m4s'),
              str(sess / 'index.m3u8')]
        return c

    if v_copy:
        # MSE exige el sample entry hvc1 (los MKV traen hev1) — sin el retag el
        # navegador rechaza el stream HEVC aunque sepa decodificarlo.
        vargs = ['-c:v', 'copy'] + (['-tag:v', 'hvc1'] if vcodec == 'hevc' else [])
        attempts = [_cmd(vargs)]
    else:
        # El anime HEVC suele ser 10-bit y h264_nvenc solo codifica 8-bit →
        # SIEMPRE convertir a yuv420p antes del encoder (los navegadores tampoco
        # reproducen H.264 High10). Cadena de intentos:
        #  1) GPU completa: NVDEC decodifica + NVENC codifica (el decode 10-bit
        #     por CPU era el cuello: el primer segmento tardaba >30 s → 504).
        #  2) decode CPU + NVENC   3) todo CPU (libx264).
        attempts = [
            _cmd(['-vf', 'scale_cuda=format=yuv420p',
                  '-c:v', 'h264_nvenc', '-preset', 'p4', '-cq', '21'],
                 pre_input=('-hwaccel', 'cuda', '-hwaccel_output_format', 'cuda')),
            _cmd(['-vf', 'format=yuv420p', '-c:v', 'h264_nvenc', '-preset', 'p4', '-cq', '21']),
            _cmd(['-vf', 'format=yuv420p', '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '21']),
        ]

    playlist = sess / 'index.m3u8'
    proc = None
    last_err = ''
    for cmd in attempts:
        with _lock:
            _kill_current()
            _current['sid'] = sid  # _kill_current borra la sesión: recrear dir
            sess.mkdir(parents=True, exist_ok=True)
            # stderr a archivo, NUNCA a PIPE sin lector: archivos con muchos
            # warnings llenan el buffer de 64 KB y ffmpeg se congela (504).
            errlog = open(sess / 'ffmpeg.log', 'w')
            try:
                proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=errlog)
            except Exception as e:
                return jsonify({'error': f'ffmpeg: {e}'}), 500
            finally:
                errlog.close()
            _current['proc'] = proc
        deadline = time.monotonic() + 30
        ok = False
        while time.monotonic() < deadline:
            if playlist.exists() and 'seg_' in playlist.read_text(errors='ignore'):
                ok = True
                break
            if proc.poll() is not None:   # murió — prueba el siguiente intento
                try:
                    last_err = (sess / 'ffmpeg.log').read_text(errors='ignore')[-400:]
                except OSError:
                    last_err = ''
                print(f'[stream] ffmpeg murió: {last_err[-200:]}', flush=True)
                break
            time.sleep(0.15)
        if ok:
            # cosecha el proceso al terminar el remux (evita zombies <defunct>)
            threading.Thread(target=proc.wait, daemon=True).start()
            break
        if proc.poll() is None:           # sigue vivo pero sin segmento: timeout real
            with _lock:
                _kill_current()
            return jsonify({'error': 'timeout preparando el stream'}), 504
    else:
        with _lock:
            _kill_current()
        return jsonify({'error': f'ffmpeg falló: {last_err}'}), 500

    anime_id = data.get('anime_id', '')
    ep_str = str(data.get('episode', ''))
    resume = 0
    if anime_id:
        resume = float((_lib_read().get(anime_id) or {})
                       .get('positions', {}).get(ep_str, 0))

    return jsonify({
        'ok': True, 'sid': sid,
        'playlist': f'/api/stream/hls/{sid}/index.m3u8',
        'duration': duration,
        'resume_pos': resume,
        'path': video,
        'video_codec': vcodec, 'transcode': not (v_copy and a_copy),
        'audio_tracks': [_track(s, i) for i, s in enumerate(astreams)],
        'sub_tracks': [_track(s, i) for i, s in enumerate(sstreams)],
    })


@stream_bp.route('/hls/<sid>/<path:fn>')
def stream_hls(sid, fn):
    sess = _SESS_ROOT / sid
    if not sess.is_dir():
        return 'session not found', 404
    # m3u8 sin caché (crece mientras ffmpeg remuxa); segmentos inmutables
    max_age = 0 if fn.endswith('.m3u8') else 3600
    return send_from_directory(str(sess), fn, max_age=max_age)


@stream_bp.route('/subs', methods=['POST'])
def stream_subs():
    """Extrae una pista de subtítulos (y las fuentes adjuntas del MKV) para
    renderizarla en el navegador. Body: {path, index} (índice relativo)."""
    data = request.get_json(silent=True) or {}
    video = data.get('path', '')
    idx = int(data.get('index', 0))
    if not video or not Path(video).exists():
        return jsonify({'error': 'path inválido'}), 400

    sid = _current.get('sid')
    if not sid:
        return jsonify({'error': 'sin sesión activa'}), 409
    sess = _SESS_ROOT / sid
    subdir = sess / 'subs'
    subdir.mkdir(exist_ok=True)

    # Formato de la pista → extensión (ass conserva estilos/karaoke)
    try:
        info = _ffprobe(video)
        sstreams = [s for s in (info.get('streams') or [])
                    if s.get('codec_type') == 'subtitle']
        codec = sstreams[idx].get('codec_name', 'ass') if idx < len(sstreams) else 'ass'
    except Exception:
        codec = 'ass'
    # ass conserva estilos (JASSUB); todo lo demás sale como vtt, que es lo
    # único que el <track> nativo del navegador reproduce (srt NO).
    ext = {'ass': 'ass', 'ssa': 'ass'}.get(codec, 'vtt')
    out = subdir / f'track{idx}.{ext}'
    if not out.exists():
        r = subprocess.run(
            ['ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'error', '-y',
             '-i', video, '-map', f'0:s:{idx}', str(out)],
            capture_output=True, text=True, timeout=120)
        if r.returncode != 0 or not out.exists():
            return jsonify({'error': f'extracción falló: {r.stderr[-200:]}'}), 500

    # Fuentes adjuntas del MKV (para que JASSUB renderice fiel los .ass)
    fontdir = sess / 'fonts'
    if not fontdir.exists():
        fontdir.mkdir()
        subprocess.run(
            ['ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'error',
             '-dump_attachment:t', '', '-i', video],
            cwd=str(fontdir), capture_output=True, timeout=60)
    fonts = [f'/api/stream/hls/{sid}/fonts/{f.name}' for f in fontdir.iterdir()
             if f.suffix.lower() in ('.ttf', '.otf', '.ttc')]

    return jsonify({'ok': True, 'format': ext,
                    'url': f'/api/stream/hls/{sid}/subs/{out.name}',
                    'fonts': fonts})


@stream_bp.route('/progress', methods=['POST'])
def stream_progress():
    """Progreso del player web — misma semántica que el tracker de MPV:
    guarda posición para reanudar; al terminar (ended o ≥85%) marca visto.
    Body: {anime_id, episode, position, duration, ended?}"""
    from api.anime import (_lib_read, _lib_write, _history_append,
                           _WATCHED_THRESHOLD)
    from api.runtime import push_sse_event
    data = request.get_json(silent=True) or {}
    anime_id = data.get('anime_id', '')
    ep_str = str(data.get('episode', ''))
    position = float(data.get('position', 0))
    duration = float(data.get('duration', 0))
    ended = bool(data.get('ended'))
    if not anime_id or not ep_str:
        return jsonify({'error': 'anime_id y episode requeridos'}), 400

    watched = ended or (duration > 0 and position / duration >= _WATCHED_THRESHOLD)
    save_pos = 0 if watched else int(position)

    lib = _lib_read()
    if anime_id not in lib:
        return jsonify({'ok': False, 'error': 'anime no está en la biblioteca'}), 404
    if duration > 0:
        lib[anime_id].setdefault('durations', {})[ep_str] = int(duration)
    if save_pos > 30:
        lib[anime_id].setdefault('positions', {})[ep_str] = save_pos
    else:
        lib[anime_id].get('positions', {}).pop(ep_str, None)
    if watched and not lib[anime_id].get('watched', {}).get(ep_str):
        lib[anime_id].setdefault('watched', {})[ep_str] = True
        now = int(time.time())
        lib[anime_id]['last_watched_at'] = now
        _lib_write(lib)
        _history_append(anime_id, lib[anime_id].get('title', anime_id),
                        int(float(ep_str)), lib[anime_id].get('cover', ''))
        push_sse_event('watched', anime_id=anime_id, ep_str=ep_str,
                       last_watched_at=now, watched=True,
                       duration=int(duration), from_mpv=False)
    else:
        _lib_write(lib)
        push_sse_event('position', anime_id=anime_id, ep_str=ep_str,
                       position=save_pos, duration=int(duration))
    return jsonify({'ok': True, 'watched': watched})


_browser_caps = {}


@stream_bp.route('/caps', methods=['POST'])
def stream_caps():
    """El frontend reporta qué códecs decodifica su navegador (diagnóstico +
    telemetría local para decidir copy vs transcode sin adivinar)."""
    global _browser_caps
    _browser_caps = request.get_json(silent=True) or {}
    print(f'[stream] caps del navegador: {_browser_caps}', flush=True)
    return jsonify({'ok': True})


@stream_bp.route('/close', methods=['POST'])
def stream_close():
    with _lock:
        _kill_current()
    return jsonify({'ok': True})
