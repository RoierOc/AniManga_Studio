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
    v_copy = vcodec in _VIDEO_COPY
    a_copy = acodec in _AUDIO_COPY

    sid = uuid.uuid4().hex[:12]
    sess = _SESS_ROOT / sid
    sess.mkdir(parents=True, exist_ok=True)

    cmd = ['ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'warning', '-y',
           '-i', video, '-map', '0:v:0']
    if astreams:
        cmd += ['-map', f'0:a:{audio_idx}']
    if v_copy:
        cmd += ['-c:v', 'copy']
    else:
        # HEVC u otro no soportado → NVENC (la 3050 codifica sin tocar el
        # límite de VRAM del upscaler); si no hay NVENC, libx264 veryfast.
        cmd += ['-c:v', 'h264_nvenc', '-preset', 'p4', '-cq', '21']
    if astreams:
        cmd += (['-c:a', 'copy'] if a_copy else ['-c:a', 'aac', '-b:a', '192k', '-ac', '2'])
    cmd += ['-sn', '-dn',
            '-f', 'hls', '-hls_time', '6', '-hls_playlist_type', 'vod',
            '-hls_segment_type', 'fmp4', '-hls_fmp4_init_filename', 'init.mp4',
            '-hls_segment_filename', str(sess / 'seg_%05d.m4s'),
            str(sess / 'index.m3u8')]

    with _lock:
        _kill_current()
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.PIPE, text=True)
        except Exception as e:
            return jsonify({'error': f'ffmpeg: {e}'}), 500
        _current['sid'] = sid
        _current['proc'] = proc

    # Espera a que el playlist tenga el primer segmento (remux copy: <1 s)
    playlist = sess / 'index.m3u8'
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if playlist.exists() and 'seg_' in playlist.read_text(errors='ignore'):
            break
        if proc.poll() is not None:  # ffmpeg murió — NVENC ausente u otro error
            tail = (proc.stderr.read() or '')[-400:] if proc.stderr else ''
            if not v_copy and 'h264_nvenc' in ' '.join(cmd):
                # reintento único con libx264
                cmd[cmd.index('h264_nvenc')] = 'libx264'
                cmd[cmd.index('-preset') + 1] = 'veryfast'
                with _lock:
                    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                                            stderr=subprocess.PIPE, text=True)
                    _current['proc'] = proc
                v_copy = True  # no volver a reintentar
                continue
            with _lock:
                _kill_current()
            return jsonify({'error': f'ffmpeg falló: {tail}'}), 500
        time.sleep(0.2)
    else:
        with _lock:
            _kill_current()
        return jsonify({'error': 'timeout preparando el stream'}), 504

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


@stream_bp.route('/close', methods=['POST'])
def stream_close():
    with _lock:
        _kill_current()
    return jsonify({'ok': True})
