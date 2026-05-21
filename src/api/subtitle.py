import os
import re
import json
import time
import tempfile
import subprocess
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from flask import Blueprint, request, jsonify

subtitle_bp = Blueprint('subtitle', __name__)

_tasks: dict = {}
_cancel_flags: dict = {}
BATCH_SIZE = 60
OLLAMA_BATCH_SIZE = 60          # ~60 lines fits comfortably in 2048-token context
GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY', '')
_OLLAMA_URL    = os.environ.get('OLLAMA_URL',   'http://localhost:11434')
_OLLAMA_MODEL  = os.environ.get('OLLAMA_MODEL', 'qwen2.5:14b')
_OLLAMA_TIMEOUT = 300           # seconds per batch (generous for 60 lines)
# 'ollama' → Qwen local first, Gemini fallback if Ollama fails
# 'gemini' → Gemini first, Qwen fallback on quota exhaustion
# 'auto'   → same as gemini (kept for compatibility)
_ENGINE = os.environ.get('TRANSLATION_ENGINE', 'ollama')

# Reference counter — model is only unloaded when the LAST concurrent task finishes
_OLLAMA_TASK_COUNT = 0
_OLLAMA_TASK_LOCK  = threading.Lock()

_LANG_NAMES: dict[str, str] = {
    'eng': 'inglés',   'en':  'inglés',
    'jpn': 'japonés',  'ja':  'japonés',
    'por': 'portugués','pt':  'portugués',
    'fre': 'francés',  'fra': 'francés',   'fr': 'francés',
    'ger': 'alemán',   'deu': 'alemán',    'de': 'alemán',
    'ita': 'italiano', 'it':  'italiano',
    'kor': 'coreano',  'ko':  'coreano',
    'chi': 'chino',    'zho': 'chino',     'zh': 'chino',
}


def _build_prompt(src_lang: str = 'eng') -> str:
    """Build translation prompt for the given ISO 639-2/1 language tag."""
    lang = _LANG_NAMES.get((src_lang or 'eng').lower(), src_lang or 'inglés')
    return (
        f'Traduce subtítulos de anime del {lang} al español latinoamericano neutro.\n\n'
        'REGLAS:\n'
        '1. Traducción natural y fluida. Tuteo casual; usted con personajes formales o de alto rango.\n'
        '2. NO traduzcas: honoríficos (-san, -kun, -chan, -sama, -senpai, -sensei, -dono, -nii, -nee, -tan), '
        'nombres de ataques/técnicas/magia, nombres propios de personajes y lugares.\n'
        '3. Adapta registro: arcaico→arcaico, dialectal→equivalente, GRITOS→MAYÚSCULAS, susurros→minúsculas.\n'
        '4. COPIA EXACTO sin ninguna modificación:\n'
        '   - \\N  (salto de línea técnico de subtítulos)\n'
        '   - Cualquier bloque {…}: {\\an8} {\\i1} {\\pos(x,y)} {\\t(...)} {\\move(...)} {\\alpha(...)} etc.\n'
        '     Son tags ASS críticos para posición, timing y efectos — tocarlos rompe la sincronía.\n'
        '   - Símbolos musicales ♪ ♫ y caracteres especiales — … — « »\n'
        '5. Líneas con solo puntuación, ♪, o vacías → cópialas idénticas.\n\n'
        'FORMATO ESTRICTO:\n'
        '- Entrada: N|texto  →  Salida: N|traducción  (exactamente el mismo número N, empezando en 1)\n'
        '- Líneas de salida = líneas de entrada EXACTAMENTE. Ni una más ni una menos.\n'
        '- Responde ÚNICAMENTE con las líneas numeradas. Sin explicaciones ni texto adicional.'
    )


# ── Subtitle detection & extraction ──────────────────────────────────────────

def _ffprobe_tracks(path: str) -> list:
    """Return list of subtitle stream dicts from an MKV."""
    cmd = ['ffprobe', '-v', 'quiet', '-print_format', 'json',
           '-show_streams', path]
    r = subprocess.run(cmd, capture_output=True, text=True)
    try:
        data = json.loads(r.stdout or '{}')
    except json.JSONDecodeError:
        return []
    streams = data.get('streams', [])
    sub_idx = 0
    subs = []
    for s in streams:
        if s.get('codec_type') == 'subtitle':
            tags = s.get('tags', {})
            subs.append({
                'index':        s.get('index'),        # global stream index
                'sub_index':    sub_idx,               # 0-based among subtitle streams
                'codec':        s.get('codec_name', 'srt'),
                'language':     tags.get('language', 'und'),
                'title':        tags.get('title', ''),
            })
            sub_idx += 1
    return subs


def _extract_sub(mkv_path: str, sub_index: int, codec: str, tmpdir: str) -> str:
    """Extract subtitle track to a temp file. Returns path to the file."""
    ext = '.ass' if codec in ('ass', 'ssa') else '.srt'
    out = os.path.join(tmpdir, f'sub{ext}')
    cmd = ['ffmpeg', '-y', '-i', mkv_path, '-map', f'0:s:{sub_index}', out]
    subprocess.run(cmd, capture_output=True, check=True)
    return out


def _inject_sub(mkv_path: str, sub_path: str, n_existing_subs: int) -> str:
    """Insert translated subtitle into the original MKV in-place using mkvmerge.
    mkvmerge writes proper cue entries (index) so MPV can seek all subtitle packets.
    ffmpeg -c copy omits the cue index, causing MPV to only read the first packet.
    """
    tmp_out = mkv_path + '._tmp_spa.mkv'

    # Identify tracks via mkvmerge; collect subtitle track IDs to exclude existing spa ones
    id_result = subprocess.run(
        ['mkvmerge', '--identify', '--identification-format', 'json', mkv_path],
        capture_output=True, text=True, env={**os.environ, 'LC_ALL': 'C'},
    )
    id_data = json.loads(id_result.stdout or '{}')
    tracks = id_data.get('tracks', [])

    # Build --subtitle-tracks arg: keep non-spa subtitle tracks from source
    keep_sub_ids = [
        str(t['id']) for t in tracks
        if t.get('type') == 'subtitles'
        and t.get('properties', {}).get('language', '') not in ('spa', 'es')
    ]
    keep_audio_ids  = [str(t['id']) for t in tracks if t.get('type') == 'audio']
    keep_video_ids  = [str(t['id']) for t in tracks if t.get('type') == 'video']

    cmd = [
        'mkvmerge', '-o', tmp_out,
    ]
    if keep_video_ids:
        cmd += ['--video-tracks', ','.join(keep_video_ids)]
    if keep_audio_ids:
        cmd += ['--audio-tracks', ','.join(keep_audio_ids)]
    if keep_sub_ids:
        cmd += ['--subtitle-tracks', ','.join(keep_sub_ids)]
    else:
        cmd += ['--no-subtitles']
    cmd += [mkv_path]
    # New Spanish subtitle
    cmd += [
        '--language', '0:spa',
        '--track-name', '0:Español',
        '--default-track', '0:yes',
        sub_path,
    ]

    result = subprocess.run(cmd, capture_output=True, text=True,
                            env={**os.environ, 'LC_ALL': 'C'})
    if result.returncode not in (0, 1):   # mkvmerge returns 1 for warnings (ok)
        if os.path.exists(tmp_out):
            os.remove(tmp_out)
        raise RuntimeError(f'mkvmerge inject failed: {result.stderr[-500:]}')

    os.replace(tmp_out, mkv_path)

    # Clear default on other subtitle tracks
    n_kept_subs = len(keep_sub_ids)
    mkvprop_args = ['mkvpropedit', mkv_path]
    for i in range(1, n_kept_subs + 1):
        mkvprop_args += ['--edit', f'track:s{i}', '--set', 'flag-default=0']
    mkvprop_args += ['--edit', f'track:s{n_kept_subs + 1}', '--set', 'flag-default=1']
    subprocess.run(mkvprop_args, capture_output=True,
                   env={**os.environ, 'LC_ALL': 'C'})

    return mkv_path


# ── SRT parsing ───────────────────────────────────────────────────────────────

def _parse_srt(content: str) -> list:
    """Parse SRT → list of {idx, timestamp, text} blocks. Handles LF and CRLF."""
    blocks = []
    # Normalize CRLF to LF before splitting
    content = content.replace('\r\n', '\n').replace('\r', '\n')
    for block in re.split(r'\n{2,}', content.strip()):
        lines = block.strip().splitlines()
        if len(lines) < 3:
            continue
        # Strip HTML tags; join multi-line text with \N so each block fits on one "NUMBER|text" line for Gemini
        text_lines = lines[2:]
        text = r'\N'.join(l.strip() for l in text_lines)
        text = re.sub(r'<[^>]+>', '', text).strip()
        blocks.append({
            'idx':       lines[0].strip(),
            'timestamp': lines[1].strip(),
            'text':      text,
        })
    return blocks


def _rebuild_srt(blocks: list, translated: list) -> str:
    parts = []
    for b, t in zip(blocks, translated):
        # \N was used as a single-line separator for Gemini; restore real newlines for SRT output
        text = (t or b['text']).replace(r'\N', '\n')
        parts.append(f"{b['idx']}\n{b['timestamp']}\n{text}\n")
    return '\n'.join(parts)


# ── ASS parsing ───────────────────────────────────────────────────────────────

def _parse_ass(content: str):
    """
    Split ASS into header lines and a list of event records.
    Each event: {prefix, text, passthrough}
      passthrough=True  → non-Dialogue line, copy as-is
      passthrough=False → Dialogue line; 'text' is the full text WITH ASS tags
                          (Gemini is instructed to preserve {…} blocks)
    """
    lines = content.splitlines(keepends=True)
    header = []
    events = []
    in_events = False
    text_col = 9  # default for standard ASS (Layer…Effect,Text)

    for line in lines:
        stripped = line.rstrip('\n\r')
        low = stripped.lower().strip()

        if low == '[events]':
            in_events = True
            header.append(line)
            continue

        if not in_events:
            header.append(line)
            continue

        if low.startswith('format:'):
            cols = [c.strip() for c in stripped.split(':', 1)[1].split(',')]
            if 'Text' in cols:
                text_col = cols.index('Text')
            header.append(line)
            continue

        if low.startswith('dialogue:'):
            parts = stripped.split(',', text_col)
            if len(parts) > text_col:
                prefix = ','.join(parts[:text_col]) + ','
                text   = ','.join(parts[text_col:])
                events.append({
                    'passthrough': False,
                    'prefix':      prefix,
                    'text':        text,   # full text WITH tags — Gemini preserves {…}
                    'eol':         '\n' if line.endswith('\n') else '',
                })
            else:
                events.append({'passthrough': True, 'raw': line})
        else:
            events.append({'passthrough': True, 'raw': line})

    return header, events


def _rebuild_ass(header: list, events: list, translated: list) -> str:
    out = ''.join(header)
    t_iter = iter(translated)
    for ev in events:
        if ev['passthrough']:
            out += ev['raw']
        else:
            t = next(t_iter, ev['text'])
            out += ev['prefix'] + (t or ev['text']) + ev['eol']
    return out


# ── Gemini translation ────────────────────────────────────────────────────────

def _translate_batch(texts: list, client, model_name: str, src_lang: str = 'eng') -> list:
    """Translate a list of strings via Gemini. Returns same-length list."""
    if not texts:
        return []

    numbered = '\n'.join(f'{i+1}|{t}' for i, t in enumerate(texts))
    prompt = f'{_build_prompt(src_lang)}\n\nTraduce estas {len(texts)} líneas:\n\n{numbered}'

    for attempt in range(3):
        try:
            response = client.models.generate_content(model=model_name, contents=prompt)
            raw = (response.text or '').strip()
            break
        except Exception as e:
            err = str(e)
            if '429' in err or 'RESOURCE_EXHAUSTED' in err:
                if 'limit: 0' in err:
                    raise RuntimeError(
                        'Cuota Gemini es 0 — activa la API en tu proyecto: '
                        'console.cloud.google.com → APIs & Services → Library → '
                        '"Generative Language API" → Enable. '
                        'O crea una nueva key en aistudio.google.com con "Create API key in new project".'
                    ) from e
                delay = re.search(r'(\d+)s', err)
                wait = int(delay.group(1)) + 2 if delay else (15 * (attempt + 1))
                print(f'[subtitle] 429 on {model_name}, waiting {wait}s (attempt {attempt+1})')
                time.sleep(wait)
                if attempt == 2:
                    raise
            elif '400' in err or 'BAD_REQUEST' in err:
                raise RuntimeError(f'Bad request al modelo {model_name}: {err[:200]}') from e
            elif '404' in err or 'NOT_FOUND' in err:
                raise RuntimeError(f'Modelo {model_name} no disponible') from e
            else:
                raise
    else:
        return list(texts)

    result = list(texts)
    for line in raw.splitlines():
        m = re.match(r'^(\d+)\|(.*)$', line)
        if m:
            idx = int(m.group(1)) - 1
            if 0 <= idx < len(texts):
                result[idx] = m.group(2)
    return result


# ── Main worker ───────────────────────────────────────────────────────────────

def _resolve_video_path(info_hash: str, episode: int, anime_id: str) -> str | None:
    """Resolve MKV path via qBittorrent, reusing anime.py helpers."""
    try:
        from api.anime import _q, _find_video, _lib_read
        torrents = _q('get', '/torrents/info', params={'hashes': info_hash}).json()
        if not torrents and anime_id:
            lib = _lib_read()
            batch_hash = (lib.get(anime_id) or {}).get('episodes', {}).get('0', {}).get('info_hash', '')
            if batch_hash and batch_hash != info_hash:
                torrents = _q('get', '/torrents/info', params={'hashes': batch_hash}).json()
        if not torrents:
            return None
        return _find_video(torrents[0].get('content_path', ''), episode)
    except Exception as e:
        print(f'[subtitle] resolve path error: {e}')
        return None


# Best free-tier models ordered by RPD (requests/day): flash-lite ~1500, flash ~250
# Models ordered by free-tier RPD (highest first):
# gemma-4-26b-a4b-it  ~1500 RPD  (MoE: 26B total / 4B active, instruction-tuned)
# gemma-4-31b-it      ~1500 RPD
# gemini-2.5-flash-lite ~1000 RPD
# gemini-2.5-flash      ~500 RPD
_MODELS = [
    'models/gemma-4-26b-a4b-it',
    'models/gemma-4-31b-it',
    'models/gemini-2.5-flash-lite',
    'models/gemini-2.5-flash',
    'models/gemini-flash-lite-latest',
]
_PARALLEL_WORKERS = 6  # concurrent batch requests


def _translate_batch_with_fallback(texts: list, client, src_lang: str = 'eng') -> list:
    """Try each model in order; return translated list."""
    last_err = None
    for mn in _MODELS:
        try:
            return _translate_batch(texts, client, mn, src_lang)
        except RuntimeError as e:
            if 'Cuota Gemini es 0' in str(e):
                raise
            last_err = e
            print(f'[subtitle] {mn} failed: {e} — trying next model')
    raise last_err or RuntimeError('Todos los modelos fallaron')


# ── Ollama / local-model translation ─────────────────────────────────────────

def _is_quota_error(exc: Exception) -> bool:
    err = str(exc)
    return ('429' in err or 'RESOURCE_EXHAUSTED' in err
            or 'quota' in err.lower() or 'exhausted' in err.lower())


def _ollama_available() -> bool:
    import urllib.request as _ur
    try:
        _ur.urlopen(f'{_OLLAMA_URL}/', timeout=3)
        return True
    except Exception:
        return False


def _ollama_preload():
    """Load model into VRAM once; keep alive 30 min. May take 30-60 s on first call."""
    import urllib.request as _ur
    payload = json.dumps({
        'model': _OLLAMA_MODEL,
        'messages': [{'role': 'user', 'content': '1|ok'}],
        'stream': False,
        'keep_alive': '30m',
        'options': {'num_predict': 3},
    }).encode()
    req = _ur.Request(f'{_OLLAMA_URL}/api/chat', data=payload,
                      headers={'Content-Type': 'application/json'}, method='POST')
    try:
        _ur.urlopen(req, timeout=90)
    except Exception as e:
        raise RuntimeError(f'No se pudo cargar {_OLLAMA_MODEL} en VRAM: {e}') from e


def _ollama_unload():
    """Release model from VRAM immediately."""
    import urllib.request as _ur
    try:
        payload = json.dumps({'model': _OLLAMA_MODEL, 'keep_alive': 0}).encode()
        req = _ur.Request(f'{_OLLAMA_URL}/api/generate', data=payload,
                          headers={'Content-Type': 'application/json'}, method='POST')
        _ur.urlopen(req, timeout=15)
    except Exception:
        pass


def _translate_batch_ollama(texts: list, src_lang: str = 'eng') -> list:
    """Translate a batch via local Ollama. Retries once if model returns < 85% of lines."""
    if not texts:
        return []
    import urllib.request as _ur

    n = len(texts)
    # num_ctx: prompt ~200t + 60 lines ~600t input + ~720t output ≈ 1520 → 2048 fits
    # num_predict: generous ceiling so the model never truncates mid-batch
    n_predict = min(n * 40, 3000)

    def _call() -> tuple[list, int]:
        numbered = '\n'.join(f'{i+1}|{t}' for i, t in enumerate(texts))
        prompt = f'{_build_prompt(src_lang)}\n\nTraduce estas {n} líneas:\n\n{numbered}'
        payload = json.dumps({
            'model': _OLLAMA_MODEL,
            'messages': [{'role': 'user', 'content': prompt}],
            'stream': False,
            'keep_alive': '30m',
            'options': {'temperature': 0.05, 'num_predict': n_predict, 'num_ctx': 2048},
        }).encode()
        req = _ur.Request(f'{_OLLAMA_URL}/api/chat', data=payload,
                          headers={'Content-Type': 'application/json'}, method='POST')
        try:
            with _ur.urlopen(req, timeout=_OLLAMA_TIMEOUT) as resp:
                data = json.loads(resp.read())
        except Exception as e:
            raise RuntimeError(f'Ollama: {e}') from e
        raw = ((data.get('message') or {}).get('content') or '').strip()
        result = list(texts)
        found = 0
        for line in raw.splitlines():
            m = re.match(r'^(\d+)\|(.*)$', line)
            if m:
                idx = int(m.group(1)) - 1
                if 0 <= idx < n:
                    result[idx] = m.group(2)
                    found += 1
        return result, found

    result, found = _call()
    if found < n * 0.85:
        print(f'[subtitle] Ollama returned {found}/{n} lines — retrying batch…')
        result, _ = _call()
    return result


def _do_translate(task_id: str, mkv_path: str, sub_index: int, codec: str, n_subs: int, src_lang: str = 'eng'):
    global _OLLAMA_TASK_COUNT
    task = _tasks[task_id]
    _cancel_flags.setdefault(task_id, False)
    done_count  = threading.Lock()
    ollama_used = [False]

    def upd(status, progress, message, engine=None):
        patch = dict(status=status, progress=progress, message=message)
        if engine:
            patch['engine'] = engine
            task['engine']  = engine
        task.update(patch)

    def _cancelled_now():
        return bool(_cancel_flags.get(task_id))

    try:
        upd('extracting', 5, 'Extrayendo subtítulos del MKV…')

        with tempfile.TemporaryDirectory() as tmpdir:
            sub_file = _extract_sub(mkv_path, sub_index, codec, tmpdir)
            with open(sub_file, encoding='utf-8', errors='replace') as f:
                content = f.read()

            is_ass = codec in ('ass', 'ssa')
            if is_ass:
                header, events = _parse_ass(content)
                dialogue = [e for e in events if not e['passthrough']]
                texts = [e['text'] for e in dialogue]
            else:
                blocks = _parse_srt(content)
                texts = [b['text'] for b in blocks]

            total = len(texts)
            if total == 0:
                raise RuntimeError('El archivo de subtítulos no tiene líneas de diálogo')

            # position → translated string  (filled incrementally by either engine)
            translated_map: dict[int, str] = {}

            # ── Phase 1: Gemini (parallel) — only when engine != 'ollama' ────────
            key = GEMINI_API_KEY or os.environ.get('GEMINI_API_KEY', '')
            quota_hit  = False
            _cancelled = False

            if key and _ENGINE != 'ollama':
                g_batches = [texts[i:i + BATCH_SIZE] for i in range(0, total, BATCH_SIZE)]
                n_g = len(g_batches)
                try:
                    from google import genai
                    client = genai.Client(api_key=key)
                except Exception as ie:
                    print(f'[subtitle] Gemini import error: {ie}')
                    client = None

                if client:
                    completed_g = [0]
                    upd('translating', 10,
                        f'Traduciendo {total} líneas con Gemini…', engine='gemini')
                    pool = ThreadPoolExecutor(max_workers=min(_PARALLEL_WORKERS, n_g))
                    try:
                        futures = {
                            pool.submit(_translate_batch_with_fallback, batch, client, src_lang): (idx, i_start)
                            for idx, (i_start, batch) in enumerate(
                                (i * BATCH_SIZE, g_batches[i]) for i in range(n_g)
                            )
                        }
                        for fut in as_completed(futures):
                            if _cancelled_now():
                                _cancelled = True
                                pool.shutdown(wait=False, cancel_futures=True)
                                break
                            idx, i_start = futures[fut]
                            try:
                                for j, t in enumerate(fut.result()):
                                    translated_map[i_start + j] = t
                            except Exception as e:
                                if _is_quota_error(e):
                                    quota_hit = True
                                    pool.shutdown(wait=False, cancel_futures=True)
                                    pct = 10 + int(78 * len(translated_map) / total)
                                    upd('translating', pct,
                                        '⚠ Gemini agotado — cambiando a Qwen local…',
                                        engine='gemini')
                                    break
                                raise
                            with done_count:
                                completed_g[0] += 1
                            pct = 10 + int(78 * len(translated_map) / total)
                            upd('translating', pct,
                                f'Gemini: {len(translated_map)}/{total} líneas…',
                                engine='gemini')
                        if not quota_hit and not _cancelled:
                            pool.shutdown(wait=True)
                    except Exception:
                        pool.shutdown(wait=False, cancel_futures=True)
                        raise

            if _cancelled or _cancel_flags.pop(task_id, False):
                _cancel_flags.pop(task_id, None)
                task.update(status='cancelled', progress=0, message='Traducción cancelada')
                return

            # ── Phase 2: Ollama for any position not yet translated ────────────
            missing_positions = [i for i in range(total) if i not in translated_map]

            if missing_positions:
                ollama_used[0] = True
                if _ENGINE == 'ollama':
                    reason = 'motor local'
                elif not key:
                    reason = 'sin clave Gemini'
                else:
                    reason = 'cuota Gemini agotada'

                if not _ollama_available():
                    raise RuntimeError(
                        f'Ollama no disponible en {_OLLAMA_URL}. '
                        'Instala Ollama (ollama.com) y ejecuta: '
                        f'ollama pull {_OLLAMA_MODEL}'
                    )

                # Increment reference counter before loading model into VRAM
                with _OLLAMA_TASK_LOCK:
                    _OLLAMA_TASK_COUNT += 1

                upd('translating', 10 + int(78 * len(translated_map) / total),
                    f'Cargando {_OLLAMA_MODEL} en VRAM ({reason})…', engine='ollama')
                _ollama_preload()

                lang_label = _LANG_NAMES.get((src_lang or 'eng').lower(), src_lang or 'inglés')
                upd('translating', 10 + int(78 * len(translated_map) / total),
                    f'Traduciendo {len(missing_positions)} líneas ({lang_label} → español)…',
                    engine='ollama')

                # Re-batch only the missing positions (contiguous groups → fewer requests)
                o_batches = []
                buf = [missing_positions[0]]
                for pos in missing_positions[1:]:
                    if pos == buf[-1] + 1 and len(buf) < OLLAMA_BATCH_SIZE:
                        buf.append(pos)
                    else:
                        o_batches.append(buf)
                        buf = [pos]
                o_batches.append(buf)

                done_o = [0]
                for o_batch_positions in o_batches:
                    if _cancelled_now():
                        _cancelled = True
                        break
                    o_texts = [texts[i] for i in o_batch_positions]
                    o_translated = _translate_batch_ollama(o_texts, src_lang)
                    for pos, t in zip(o_batch_positions, o_translated):
                        translated_map[pos] = t
                    with done_count:
                        done_o[0] += 1
                    pct = 10 + int(78 * len(translated_map) / total)
                    upd('translating', min(pct, 88),
                        f'Qwen: {len(translated_map)}/{total} líneas…', engine='ollama')

            if _cancelled or _cancel_flags.pop(task_id, False):
                _cancel_flags.pop(task_id, None)
                task.update(status='cancelled', progress=0, message='Traducción cancelada')
                return

            # ── Rebuild subtitle with translations ─────────────────────────────
            translated = [translated_map.get(i, texts[i]) for i in range(total)]

            upd('injecting', 90, 'Añadiendo track español al MKV…')

            if is_ass:
                out_content = _rebuild_ass(header, events, translated)
                out_sub = os.path.join(tmpdir, 'translated.ass')
            else:
                out_content = _rebuild_srt(blocks, translated)
                out_sub = os.path.join(tmpdir, 'translated.srt')

            with open(out_sub, 'w', encoding='utf-8') as f:
                f.write(out_content)

            out_mkv = _inject_sub(mkv_path, out_sub, n_subs)

            if _cancel_flags.pop(task_id, False):
                task.update(status='cancelled', progress=0, message='Traducción cancelada')
                return

        engine_label = 'Qwen local' if ollama_used[0] else 'Gemini'
        task.update(status='done', progress=100,
                    message=f'¡Completado! ({engine_label})', output=out_mkv)

    except Exception as e:
        task.update(status='error', progress=0,
                    message=f'Error: {e}', error=str(e))
        print(f'[subtitle] translation error: {e}')
    finally:
        _cancel_flags.pop(task_id, None)
        if ollama_used[0]:
            with _OLLAMA_TASK_LOCK:
                _OLLAMA_TASK_COUNT -= 1
                should_unload = (_OLLAMA_TASK_COUNT == 0)
            if should_unload:
                threading.Thread(target=_ollama_unload, daemon=True).start()


# ── Flask endpoints ───────────────────────────────────────────────────────────

@subtitle_bp.route('/tracks')
def subtitle_tracks():
    """List subtitle tracks in an MKV given its qBittorrent info_hash."""
    info_hash = request.args.get('info_hash', '')
    episode   = int(request.args.get('episode', 1))
    anime_id  = request.args.get('anime_id', '')

    path = _resolve_video_path(info_hash, episode, anime_id)
    if not path or not os.path.exists(path):
        return jsonify({'error': 'Archivo no encontrado', 'path': path}), 404

    tracks = _ffprobe_tracks(path)
    return jsonify({'path': path, 'tracks': tracks})


@subtitle_bp.route('/translate', methods=['POST'])
def subtitle_translate():
    """Start async subtitle translation. Returns task_id."""
    data      = request.get_json(silent=True) or {}
    info_hash = data.get('info_hash', '')
    episode   = int(data.get('episode', 1))
    anime_id  = data.get('anime_id', '')
    sub_index = int(data.get('sub_index', 0))  # 0-based subtitle stream index
    force     = bool(data.get('force', False))  # replace existing spa track

    path = _resolve_video_path(info_hash, episode, anime_id)
    if not path or not os.path.exists(path):
        return jsonify({'error': 'Archivo de video no encontrado'}), 404

    tracks = _ffprobe_tracks(path)
    if not tracks:
        return jsonify({'error': 'El archivo no tiene pistas de subtítulos'}), 400

    # Block re-translation unless force=true
    if not force and any(t['language'] in ('spa', 'es') for t in tracks):
        return jsonify({'error': 'El archivo ya tiene subtítulos en español. Usa force=true para reemplazar.'}), 409

    # Find the requested subtitle track (skip existing spa tracks)
    src_tracks = [t for t in tracks if t['language'] not in ('spa', 'es')]
    track = next((t for t in src_tracks if t['sub_index'] == sub_index), src_tracks[0] if src_tracks else None)
    if not track:
        return jsonify({'error': 'No hay pista de subtítulo fuente disponible'}), 400

    codec    = track['codec']
    n_subs   = len(tracks)
    src_lang = track.get('language') or 'eng'

    task_id = uuid.uuid4().hex[:8]
    _tasks[task_id] = dict(
        status='starting', progress=0, message='Iniciando…',
        error=None, output=None,
    )

    t = threading.Thread(
        target=_do_translate,
        args=(task_id, path, track['sub_index'], codec, n_subs, src_lang),
        daemon=True,
    )
    t.start()
    return jsonify({'task_id': task_id, 'file': os.path.basename(path)})


@subtitle_bp.route('/reinject', methods=['POST'])
def subtitle_reinject():
    """Re-extract the existing Spanish track and re-inject it (no Gemini). Fixes format without retranslating."""
    data      = request.get_json(silent=True) or {}
    info_hash = data.get('info_hash', '')
    episode   = int(data.get('episode', 1))
    anime_id  = data.get('anime_id', '')

    path = _resolve_video_path(info_hash, episode, anime_id)
    if not path or not os.path.exists(path):
        return jsonify({'error': 'Archivo de video no encontrado'}), 404

    tracks = _ffprobe_tracks(path)
    spa = next((t for t in tracks if t['language'] in ('spa', 'es')), None)
    if not spa:
        return jsonify({'error': 'No hay track español que re-inyectar'}), 400

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            # Extract the existing Spanish track
            ext = '.ass' if spa['codec'] in ('ass', 'ssa') else '.srt'
            extracted = os.path.join(tmpdir, f'spa{ext}')
            subprocess.run(
                ['ffmpeg', '-y', '-i', path, '-map', f'0:s:{spa["sub_index"]}', extracted],
                capture_output=True, check=True,
            )
            with open(extracted, encoding='utf-8', errors='replace') as f:
                content = f.read()

            # Parse + rebuild to ensure clean format (real newlines, no \N in SRT)
            if ext == '.srt':
                blocks = _parse_srt(content)
                clean = _rebuild_srt(blocks, [b['text'] for b in blocks])
                out_path = os.path.join(tmpdir, 'clean.srt')
            else:
                out_path = extracted
                clean = content

            with open(out_path, 'w', encoding='utf-8') as f:
                f.write(clean)

            n_non_spa = len([t for t in tracks if t['language'] not in ('spa', 'es')])
            _inject_sub(path, out_path, n_non_spa)

        return jsonify({'status': 'ok', 'file': os.path.basename(path)})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@subtitle_bp.route('/cancel/<task_id>', methods=['POST'])
def subtitle_cancel(task_id):
    _cancel_flags[task_id] = True
    task = _tasks.get(task_id)
    if task:
        task.update(status='cancelled', progress=0, message='Cancelando…')
    return jsonify({'ok': True})


@subtitle_bp.route('/status/<task_id>')
def subtitle_status(task_id):
    task = _tasks.get(task_id)
    if not task:
        return jsonify({'error': 'task not found'}), 404
    return jsonify(task)
