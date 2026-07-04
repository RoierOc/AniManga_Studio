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
import os
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from pathlib import Path

from flask import Blueprint, jsonify, request, send_from_directory

from api.runtime import DATA_ROOT

stream_bp = Blueprint('stream', __name__)


def _pick_cache_root():
    """Dónde guardar los segmentos HLS (una peli entera son varios GB).
    Prioridad:
      1) STREAM_CACHE_DIR (override manual).
      2) DATA_ROOT/_stream_cache — en DISCO, no /tmp (que suele ser tmpfs=RAM):
         remuxar GBs en RAM competiría con el upscaler/MPV.
      3) …salvo que DATA_ROOT caiga en un drive de Windows montado en WSL
         (/mnt/...): drvfs escribe a decenas de MB/s → mataría el streaming.
         En ese caso, un temp local ext4 del propio WSL (rápido, en disco)."""
    override = os.environ.get('STREAM_CACHE_DIR')
    if override:
        return Path(override).expanduser() / 'animanga_stream'
    if str(DATA_ROOT).startswith(('/mnt/', '\\\\')):   # WSL drvfs o UNC de Windows
        # /var/tmp = ext4 del propio WSL (disco, rápido) y NO tmpfs como /tmp
        # (con systemd /tmp suele ser RAM). Si no se puede, cae a gettempdir.
        for cand in (Path('/var/tmp'), Path(tempfile.gettempdir())):
            if cand.is_dir() and os.access(cand, os.W_OK):
                return cand / 'animanga_stream'
    return DATA_ROOT / '_stream_cache'


_SESS_ROOT = _pick_cache_root()
# Sesiones huérfanas de arranques anteriores (el server murió con streams vivos)
shutil.rmtree(_SESS_ROOT, ignore_errors=True)
_lock = threading.Lock()
# stream_proc = ffmpeg del HLS activo; thumb_proc = pase de miniaturas.
# seek_ctx guarda lo necesario para relanzar el troceo desde otro punto (salto)
# sin re-resolver el vídeo ni tocar thumbs/subs/fuentes; gen numera los relanzos.
_current = {'sid': None, 'stream_proc': None, 'thumb_proc': None,
            'sess': None, 'seek_ctx': None, 'gen': 0}

# Miniaturas de la barra de progreso (preview al hacer hover, como Crunchyroll):
# 1 frame cada N segundos, generadas en segundo plano al abrir la sesión.
_THUMB_IV = 10


def _gen_thumbs(video: str, sess: Path):
    """Genera thumbs/t_00001.jpg… en la sesión. Decode solo-keyframes: barato
    incluso en episodios largos; el player las va pidiendo según existan."""
    tdir = sess / 'thumbs'
    try:
        tdir.mkdir(exist_ok=True)
        proc = subprocess.Popen(
            ['ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'error',
             '-skip_frame', 'nokey', '-i', video,
             '-vf', f'fps=1/{_THUMB_IV},scale=240:-2', '-q:v', '5',
             str(tdir / 't_%05d.jpg')],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        _current['thumb_proc'] = proc
        proc.wait()
    except Exception:
        pass

# Códecs que Chromium reproduce en fMP4/MSE sin recodificar
_VIDEO_COPY = {'h264', 'av1', 'vp9'}
_AUDIO_COPY = {'aac', 'opus', 'mp3', 'flac'}


def _ffprobe(path: str) -> dict:
    r = subprocess.run(
        ['ffprobe', '-v', 'error', '-print_format', 'json',
         '-show_streams', '-show_format', path],
        capture_output=True, text=True, timeout=30)
    return json.loads(r.stdout or '{}')


def _term(proc):
    if proc and proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


def _kill_stream():
    """Mata SOLO el ffmpeg del HLS (deja vivas thumbs/subs/fuentes de la
    sesión). Para relanzar el troceo desde otro punto al saltar. Con _lock."""
    _term(_current.get('stream_proc'))
    _current['stream_proc'] = None


def _kill_current():
    """Mata la sesión entera (stream + thumbs) y borra sus segmentos. _lock."""
    _term(_current.get('stream_proc'))
    _term(_current.get('thumb_proc'))
    _current['stream_proc'] = None
    _current['thumb_proc'] = None
    sid = _current.get('sid')
    if sid:
        shutil.rmtree(_SESS_ROOT / sid, ignore_errors=True)
    _current['sid'] = None
    _current['sess'] = None
    _current['seek_ctx'] = None


def _keyframe_at(video, t):
    """Keyframe real (≤ t) donde ffmpeg -ss arrancará el troceo en modo copia.
    Se usa como base de la sesión para que el offset no desincronice la barra ni
    los subtítulos (los .ass van en tiempo absoluto). Probe corto y barato."""
    if not t or t <= 0:
        return 0.0
    try:
        r = subprocess.run(
            ['ffprobe', '-v', 'error',
             '-read_intervals', f'{max(0.0, t - 6):.3f}%{t + 0.1:.3f}',
             '-select_streams', 'v:0', '-show_entries', 'packet=pts_time,flags',
             '-of', 'csv=p=0', video],
            capture_output=True, text=True, timeout=15)
        best = 0.0
        for line in (r.stdout or '').splitlines():
            parts = line.split(',')
            if len(parts) >= 2 and 'K' in parts[1]:
                try:
                    pt = float(parts[0])
                except ValueError:
                    continue
                if pt <= t + 0.05 and pt > best:
                    best = pt
        return best if best > 0 else max(0.0, t)
    except Exception:
        return max(0.0, t)


def _hls_cmd(video, out_dir, audio_idx, has_audio, v_copy, a_copy, vcodec, start_at):
    """Lista de intentos ffmpeg (por códec) que trocean el vídeo a HLS fMP4 en
    out_dir, empezando en start_at (0 = desde el principio)."""
    def _cmd(video_args, pre_input=()):
        pre = list(pre_input)
        if start_at and start_at > 0:
            pre = ['-ss', f'{start_at:.3f}'] + pre
        c = ['ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'warning', '-y',
             *pre, '-i', video, '-map', '0:v:0']
        if has_audio:
            c += ['-map', f'0:a:{audio_idx}']
        c += video_args
        if has_audio:
            c += (['-c:a', 'copy'] if a_copy else ['-c:a', 'aac', '-b:a', '192k', '-ac', '2'])
        # playlist_type EVENT: ffmpeg añade cada segmento al playlist al cerrarlo
        # (VOD lo escribe SOLO al terminar → un transcode largo nunca publicaba
        # el playlist y el open moría en 504). Al acabar escribe ENDLIST igual.
        c += ['-sn', '-dn',
              '-f', 'hls', '-hls_time', '4', '-hls_playlist_type', 'event',
              '-hls_segment_type', 'fmp4', '-hls_fmp4_init_filename', 'init.mp4',
              '-hls_segment_filename', str(out_dir / 'seg_%05d.m4s'),
              str(out_dir / 'index.m3u8')]
        return c

    if v_copy:
        # MSE exige el sample entry hvc1 (los MKV traen hev1) — sin el retag el
        # navegador rechaza el stream HEVC aunque sepa decodificarlo.
        vargs = ['-c:v', 'copy'] + (['-tag:v', 'hvc1'] if vcodec == 'hevc' else [])
        return [_cmd(vargs)]
    # HEVC 10-bit → yuv420p antes del encoder; cadena GPU→GPU, CPU+NVENC, todo CPU
    return [
        _cmd(['-vf', 'scale_cuda=format=yuv420p',
              '-c:v', 'h264_nvenc', '-preset', 'p4', '-cq', '21'],
             pre_input=('-hwaccel', 'cuda', '-hwaccel_output_format', 'cuda')),
        _cmd(['-vf', 'format=yuv420p', '-c:v', 'h264_nvenc', '-preset', 'p4', '-cq', '21']),
        _cmd(['-vf', 'format=yuv420p', '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '21']),
    ]


def _launch_hls(attempts, out_dir, timeout=30):
    """Lanza ffmpeg (probando cada intento de códec) troceando a out_dir. NO
    toca thumbs ni borra la sesión. Devuelve (proc, err); proc None si falla."""
    playlist = out_dir / 'index.m3u8'
    last_err = ''
    for cmd in attempts:
        out_dir.mkdir(parents=True, exist_ok=True)
        # stderr a archivo, NUNCA a PIPE sin lector: se llena el buffer y ffmpeg
        # se congela (504).
        with open(out_dir / 'ffmpeg.log', 'w') as errlog:
            try:
                proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=errlog)
            except Exception as e:
                return None, f'ffmpeg: {e}'
        _current['stream_proc'] = proc
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if playlist.exists() and 'seg_' in playlist.read_text(errors='ignore'):
                # cosecha el proceso al terminar (evita zombies <defunct>)
                threading.Thread(target=proc.wait, daemon=True).start()
                return proc, ''
            if proc.poll() is not None:       # murió: prueba el siguiente intento
                try:
                    last_err = (out_dir / 'ffmpeg.log').read_text(errors='ignore')[-400:]
                except OSError:
                    last_err = ''
                print(f'[stream] ffmpeg murió: {last_err[-200:]}', flush=True)
                break
            time.sleep(0.15)
        else:
            _term(proc)                        # timeout real: vivo pero sin segmento
            return None, 'timeout preparando el stream'
    return None, last_err or 'ffmpeg falló'


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

    # Punto de arranque: start_at explícito (cambio de audio conserva posición) o
    # el resume guardado. Se trocea directamente desde ahí para que reanudar sea
    # instantáneo aunque el archivo sea enorme (no hay que remuxar hasta ese punto).
    anime_id = data.get('anime_id', '')
    ep_str = str(data.get('episode', ''))
    resume = 0.0
    if anime_id:
        resume = float((_lib_read().get(anime_id) or {})
                       .get('positions', {}).get(ep_str, 0))
    req_start = data.get('start_at')
    want = float(req_start) if req_start is not None else resume
    base = _keyframe_at(video, want)

    sid = uuid.uuid4().hex[:12]
    sess = _SESS_ROOT / sid
    out_dir = sess / 'hls0'
    has_audio = bool(astreams)
    ctx = {'video': video, 'audio_idx': audio_idx, 'has_audio': has_audio,
           'v_copy': v_copy, 'a_copy': a_copy, 'vcodec': vcodec}
    attempts = _hls_cmd(video, out_dir, audio_idx, has_audio,
                        v_copy, a_copy, vcodec, base)

    with _lock:
        _kill_current()
        _current.update({'sid': sid, 'sess': sess, 'seek_ctx': ctx, 'gen': 0})
        sess.mkdir(parents=True, exist_ok=True)
        proc, err = _launch_hls(attempts, out_dir)
    if not proc:
        with _lock:
            _kill_current()
        code = 504 if 'timeout' in err else 500
        return jsonify({'error': err}), code

    threading.Thread(target=_gen_thumbs, args=(video, sess), daemon=True).start()

    return jsonify({
        'ok': True, 'sid': sid,
        'playlist': f'/api/stream/hls/{sid}/hls0/index.m3u8',
        'thumbs': {'url': f'/api/stream/hls/{sid}/thumbs', 'interval': _THUMB_IV},
        'duration': duration,
        'resume_pos': resume,
        'start_offset': base,
        'path': video,
        'video_codec': vcodec, 'transcode': not (v_copy and a_copy),
        'audio_tracks': [_track(s, i) for i, s in enumerate(astreams)],
        'sub_tracks': [_track(s, i) for i, s in enumerate(sstreams)],
    })


@stream_bp.route('/seek', methods=['POST'])
def stream_seek():
    """Relanza el troceo de la sesión activa desde start_at (segundos absolutos)
    para que saltar a cualquier punto cargue en ~1-2 s sin importar cuánto
    quede por remuxar. Conserva thumbs/subs/fuentes. Body: {start_at}.
    Devuelve {playlist, start_offset} — el offset real (keyframe) de la sesión."""
    data = request.get_json(silent=True) or {}
    want = float(data.get('start_at', 0) or 0)
    with _lock:
        ctx = _current.get('seek_ctx')
        sid = _current.get('sid')
        sess = _current.get('sess')
        if not ctx or not sid or not sess or not Path(sess).is_dir():
            return jsonify({'error': 'sin sesión activa'}), 409
        base = _keyframe_at(ctx['video'], want)
        _current['gen'] += 1
        gen = _current['gen']
        out_dir = Path(sess) / f'hls{gen}'
        _kill_stream()   # solo el stream; thumbs/subs/fuentes siguen vivos
        attempts = _hls_cmd(ctx['video'], out_dir, ctx['audio_idx'],
                            ctx['has_audio'], ctx['v_copy'], ctx['a_copy'],
                            ctx['vcodec'], base)
        proc, err = _launch_hls(attempts, out_dir)
        if proc:
            # Tira las generaciones anteriores (sus segmentos ya no se usan; si el
            # usuario vuelve a saltar ahí, se regeneran). Evita que una sesión con
            # muchos saltos acumule GB de segmentos abandonados.
            for d in Path(sess).glob('hls*'):
                if d.name != f'hls{gen}':
                    shutil.rmtree(d, ignore_errors=True)
    if not proc:
        return jsonify({'error': err}), (504 if 'timeout' in err else 500)
    return jsonify({'ok': True, 'start_offset': base,
                    'playlist': f'/api/stream/hls/{sid}/hls{gen}/index.m3u8'})


@stream_bp.route('/hls/<sid>/<path:fn>')
def stream_hls(sid, fn):
    sess = _SESS_ROOT / sid
    if not sess.is_dir():
        return 'session not found', 404
    # m3u8 sin caché (crece mientras ffmpeg remuxa); segmentos inmutables
    max_age = 0 if fn.endswith('.m3u8') else 3600
    return send_from_directory(str(sess), fn, max_age=max_age)


def _fallback_font():
    """Una fuente sans ESTÁTICA TrueType para .ass sin fuentes adjuntas.
    OJO: libass (wasm de jassub) no abre fuentes variables NI OpenType-CFF
    ("Error opening memory font") — solo TTF con contornos glyf. La del
    proyecto va primero (portátil al PC principal); fc-match se filtra."""
    for c in (str(Path(__file__).resolve().parents[1] / 'assets' / 'fonts'
                  / 'NotoSans-Regular.ttf'),
              '/usr/share/fonts/liberation/LiberationSans-Regular.ttf',
              '/usr/share/fonts/TTF/DejaVuSans.ttf',
              '/usr/share/fonts/noto/NotoSans-Regular.ttf',
              'C:/Windows/Fonts/arial.ttf'):
        if Path(c).is_file():
            return c
    try:
        r = subprocess.run(['fc-match', '-f', '%{file}', 'sans-serif'],
                           capture_output=True, text=True, timeout=5)
        f = (r.stdout or '').strip()
        if (f and Path(f).is_file() and 'variable' not in Path(f).name.lower()
                and Path(f).suffix.lower() == '.ttf'):
            return f
    except Exception:
        pass
    return None


def _dump_fonts(video, fontdir):
    """Extrae las fuentes adjuntas del contenedor a `fontdir`.

    OJO: `ffmpeg -dump_attachment` ABORTA en cuanto encuentra una adjunta con
    nombre "inseguro" (p.ej. 'Garamond Bold font.ttf' con espacios) y NO
    extrae ninguna de las siguientes → faltan justo Trebuchet/Times y los subs
    quedan invisibles (Wistoria: 65 adjuntas, ffmpeg solo sacaba 28). Para
    Matroska usamos mkvextract (robusto, no aborta); libass matchea por el
    nombre INTERNO de la fuente, así que el nombre de archivo da igual (se usa
    el id de la adjunta). ffmpeg queda solo de respaldo para no-MKV."""
    fontdir = Path(fontdir)
    if str(video).lower().endswith(('.mkv', '.mka', '.mks', '.webm')):
        try:
            j = subprocess.run(['mkvmerge', '-J', video],
                               capture_output=True, text=True, timeout=30)
            data = json.loads(j.stdout or '{}')
            fonts = [a for a in data.get('attachments', [])
                     if 'font' in (a.get('content_type', '') or '').lower()
                     or (a.get('file_name', '') or '').lower().endswith(
                         ('.ttf', '.otf', '.ttc'))]
            if fonts:
                specs = []
                for a in fonts:
                    ext = Path(a.get('file_name', 'f')).suffix.lower()
                    if ext not in ('.ttf', '.otf', '.ttc'):
                        ext = '.ttf'
                    specs.append(f"{a['id']}:{fontdir / (str(a['id']) + ext)}")
                subprocess.run(['mkvextract', video, 'attachments', *specs],
                               capture_output=True, timeout=120)
                return
        except Exception:
            pass   # cae al respaldo ffmpeg
    subprocess.run(
        ['ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'error',
         '-dump_attachment:t', '', '-i', video],
        cwd=str(fontdir), capture_output=True, timeout=60)


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
        _dump_fonts(video, fontdir)
        # Fallback SIEMPRE: si el archivo no trae fuentes (o el estilo usa una
        # familia que no viene adjunta), libass sin fuentes no dibuja NADA — los
        # subs quedan "seleccionados pero invisibles". El paquete jassub tampoco
        # incluye su default.woff2, así que la fuente de respaldo la pone el server.
        fb = _fallback_font()
        if fb:
            try:
                shutil.copyfile(fb, fontdir / f'_fallback{Path(fb).suffix.lower()}')
            except OSError:
                pass
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
