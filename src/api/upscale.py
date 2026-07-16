#!/usr/bin/env python3
"""
Upscale API - single GPU worker thread with tile batching.
"""

from flask import Blueprint, jsonify, request
from pathlib import Path
import re
import threading
import queue
import json
import os
import shutil
import time
import numpy as np
from decimal import Decimal, InvalidOperation
from concurrent.futures import ThreadPoolExecutor

from api.runtime import (
    MANGA_DIR,
    UPSCALED_DIR,
    manga_dir,
    upscaled_dir,
    MODELS_DIR,
    build_task_id,
    normalize_chapter,
)

_UPSCALE_STATUS_FILE = Path(UPSCALED_DIR) / '.upscale_status.json'
_UPSCALE_IN_FLIGHT = {'upscaling', 'started', 'starting'}


def _load_upscale_status() -> dict:
    try:
        if _UPSCALE_STATUS_FILE.exists():
            data = json.loads(_UPSCALE_STATUS_FILE.read_text(encoding='utf-8'))
            for v in data.values():
                if isinstance(v, dict) and v.get('status') in _UPSCALE_IN_FLIGHT:
                    v['status'] = 'interrupted'
            return data
    except Exception:
        pass
    return {}


def _persist_upscale_status():
    try:
        _UPSCALE_STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
        _UPSCALE_STATUS_FILE.write_text(
            json.dumps(upscale_status, ensure_ascii=False), encoding='utf-8'
        )
    except Exception:
        pass


upscale_status: dict = _load_upscale_status()

# ── Model registry ("bring your own model") ────────────────────────────────────
# Data-driven: read from MODELS_DIR/registry.json so adding a new spandrel-
# compatible model is "drop the file in models/ + add a JSON entry", no code
# changes. Each entry: label, list of {sub_key, file, height_max?} dicts.
# Adaptive models have multiple entries; a page's height selects the sub_key
# (see _get_sub_key). `file` resolves relative to MODELS_DIR unless absolute.
_FALLBACK_REGISTRY = {
    'eula': {
        'label': 'eula-digimanga (B&W)',
        'models': [{'sub_key': 'eula', 'file': '4x-eula-digimanga-bw-v2-nc1.pth'}],
        'adaptive': False,
    },
}


def _load_model_registry() -> dict:
    registry_path = MODELS_DIR / 'registry.json'
    try:
        raw = json.loads(registry_path.read_text(encoding='utf-8'))
    except Exception:
        print(f"upscale: {registry_path} no encontrado o inválido — usando registro por defecto. "
              f"Ver docs/MODELS.md.", flush=True)
        raw = _FALLBACK_REGISTRY

    registry = {}
    for key, cfg in raw.items():
        models = []
        for m in cfg.get('models', []):
            path = Path(m['file'])
            if not path.is_absolute():
                path = MODELS_DIR / m['file']
            models.append({**m, 'file': str(path)})
        registry[key] = {
            'label': cfg.get('label', key),
            'models': models,
            'adaptive': cfg.get('adaptive', False),
            # Modelo a color (3 canales): NO se saltan las páginas a color; al revés,
            # son justo para eso. Los modelos B&N (1 canal) sí las saltan.
            'color': cfg.get('color', False),
            # fp16 + torch.compile por defecto; ponlo en false para modelos que fallan en
            # half (p.ej. APISR/GRL da "mat1 and mat2 dtype float != Half") → corren en fp32.
            'half': cfg.get('half', True),
        }
    return registry


MODEL_REGISTRY = _load_model_registry()
_active_model_key = ['eula' if 'eula' in MODEL_REGISTRY else next(iter(MODEL_REGISTRY), 'eula')]

COLOR_DIFF_THRESHOLD = int(os.environ.get("COLOR_DIFF_THRESHOLD", "15"))
COLOR_PIXEL_FRACTION = float(os.environ.get("COLOR_PIXEL_FRACTION", "0.10"))

TILE_SIZE = int(os.environ.get("UPSCALE_TILE_SIZE", "256"))
TILE_OVERLAP = int(os.environ.get("UPSCALE_TILE_OVERLAP", "16"))
GPU_BATCH_SIZE = int(os.environ.get("GPU_BATCH_SIZE", "8"))
OUTPUT_DOWNSCALE = int(os.environ.get("UPSCALE_OUTPUT_DOWNSCALE", "1"))
JPEG_QUALITY = int(os.environ.get("UPSCALE_JPEG_QUALITY", "95"))
# Páginas que YA vienen en alta resolución (p.ej. el resultado del trasplante de traducción, que
# ahora puede usar el arte 4K local como base → páginas de ~4600×6500) NO deben re-escalarse: un
# 4x sobre una página de ese tamaño pide ~2 GB de buffers de reconstrucción POR PÁGINA
# (out_sum/out_wgt en _upscale_tiled) y con varios capítulos en paralelo agota la RAM del sistema
# → el WSL entero crashea (OOM). Si el lado mayor de la página ya supera este umbral, está a
# resolución de lectura y se copia tal cual al directorio escalado (mismo trato que una página a
# color en el modelo B&N). Umbral holgado sobre el ~4K objetivo del 4x de una página normal.
UPSCALE_MAX_INPUT_LONG_SIDE = int(os.environ.get("UPSCALE_MAX_INPUT_LONG_SIDE", "3000"))
VRAM_LIMIT_PCT = int(os.environ.get("VRAM_LIMIT_PCT", "74"))       # 74% = ~8.8GB allocator + ~1.2GB overhead CUDA/cuDNN ≈ 10GB total (RTX 5070)
GPU_THROTTLE_MS = int(os.environ.get("GPU_THROTTLE_MS", "0"))     # 0ms en modo full — sin sleep entre batches

# Eco/full mode — eco adds throttle so MPV can run alongside
_ECO_THROTTLE_MS = 80      # ms sleep after each batch of tiles
_ECO_TILE_THROTTLE_MS = 8  # additional ms sleep between individual tiles in the batch
_FULL_THROTTLE_MS = 0
_gpu_throttle_ms = [GPU_THROTTLE_MS]   # mutable; worker reads this after each batch
_gpu_tile_throttle_ms = [0]            # mutable; worker reads this between tiles

# Límite de capítulos escalándose EN PARALELO (cualquier modo). Cada hilo de capítulo
# mantiene sus PROPIOS buffers de reconstrucción (out_sum/out_wgt float32 ≈ 800MB para una
# página B&N normal a 4x) además de sus tiles en VRAM. La RAM de WSL es ~6GB, así que varios
# capítulos a la vez la agotan y tiran el WSL entero (OOM) — justo lo que pasa al "escalar un
# manga" (upscaleChapters lanza /upscale_chapter en ráfaga, uno por capítulo). El worker GPU
# es una cola ÚNICA, así que serializar capítulos NO baja el rendimiento real de la GPU: solo
# acota el pico de RAM del sistema. Ajustable por env (subir solo si hay mucha RAM).
_UPSCALE_MAX_PARALLEL = max(1, int(os.environ.get("UPSCALE_MAX_PARALLEL_CHAPTERS", "2")))
_chapter_semaphore = threading.Semaphore(_UPSCALE_MAX_PARALLEL)

# Guardarraíl por página: si UNA sola página proyecta más de esto en buffers de
# reconstrucción, se copia sin escalar (como una página ya-hi-res) en vez de arriesgar el OOM.
# Backstop para páginas patológicas por debajo del umbral de lado (UPSCALE_MAX_INPUT_LONG_SIDE).
_MAX_RECON_MB = int(os.environ.get("UPSCALE_MAX_RECON_MB", "1600"))


def _mem_snapshot():
    """(avail_mb, total_mb, rss_mb) leyendo /proc — para loguear la presión de RAM y
    diagnosticar los OOM que crashean el WSL (antes no se logueaba nada)."""
    avail = total = rss = 0
    try:
        with open('/proc/meminfo') as f:
            for line in f:
                if line.startswith('MemTotal:'):
                    total = int(line.split()[1]) // 1024
                elif line.startswith('MemAvailable:'):
                    avail = int(line.split()[1]) // 1024
    except Exception:
        pass
    try:
        with open('/proc/self/status') as f:
            for line in f:
                if line.startswith('VmRSS:'):
                    rss = int(line.split()[1]) // 1024
                    break
    except Exception:
        pass
    return avail, total, rss


def _projected_recon_mb(w, h, c, scale):
    """MB aproximados de los buffers numpy de reconstrucción de una página al escalar:
    out_sum(oh*ow*c*4) + out_wgt(oh*ow*4) + result_np(oh*ow*c) — el pico de RAM por página."""
    oh, ow = h * scale, w * scale
    return (oh * ow * (c * 4 + 4 + c)) / (1024 * 1024)

# Cancel support — set contains task_ids awaiting cancellation
_upscale_cancel_requested: set = set()

# Thread pool for async JPEG saves — keeps GPU busy while CPU encodes previous image
_save_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="jpeg-save")


def _resize_save(img, path, downscale, quality):
    from PIL import Image as _Image
    if downscale > 1:
        img = img.resize((max(1, img.width // downscale), max(1, img.height // downscale)), _Image.LANCZOS)
    img.save(path, quality=quality)


def is_color_page(img_pil, threshold=COLOR_DIFF_THRESHOLD, min_fraction=COLOR_PIXEL_FRACTION):
    """Returns True if image has significant non-grayscale content."""
    thumb = img_pil.copy()
    thumb.thumbnail((256, 256))
    arr = np.asarray(thumb.convert('RGB'), dtype=np.int16)
    r, g, b = arr[:, :, 0], arr[:, :, 1], arr[:, :, 2]
    max_diff = np.maximum(np.maximum(np.abs(r - g), np.abs(r - b)), np.abs(g - b))
    return float(np.mean(max_diff > threshold)) > min_fraction


def is_already_hires(img_pil, long_side=UPSCALE_MAX_INPUT_LONG_SIDE):
    """True si la página ya está a resolución de lectura (lado mayor ≥ umbral) y NO debe
    re-escalarse — evita el OOM de reconstruir un 4x sobre una página ya-4K (ver
    UPSCALE_MAX_INPUT_LONG_SIDE)."""
    return max(img_pil.width, img_pil.height) >= long_side


# ── GPU worker ──────────────────────────────────────────────────────────────

class _TileFuture:
    """Lightweight future for getting a GPU tile result back to a chapter thread."""
    def __init__(self):
        self._event = threading.Event()
        self._result = None
        self._exc = None

    def set_result(self, r):
        self._result = r
        self._event.set()

    def set_exception(self, e):
        self._exc = e
        self._event.set()

    def result(self, timeout=120):
        if not self._event.wait(timeout):
            raise TimeoutError("GPU tile timed out after 120s")
        if self._exc:
            raise self._exc
        return self._result


_gpu_queue: queue.Queue = queue.Queue()
_gpu_worker_thread = None
_gpu_worker_lock = threading.Lock()
_gpu_worker_ready = threading.Event()
_gpu_worker_error: list = [None]
_gpu_in_channels: list = [1]   # updated by worker on startup
_gpu_scale: list = [4]         # updated by worker on startup from the model's own spandrel descriptor
_gpu_half: list = [True]       # fp16 (True) o fp32 (False) según el modelo activo — lo fija el worker


def _gpu_worker_loop():
    """Single daemon thread: owns CUDA context, processes tile batches.
    Queue items: (tile_tensor, future, sub_key) where sub_key selects the model."""
    import torch
    import time
    from spandrel import ModelLoader

    try:
        if not torch.cuda.is_available():
            raise RuntimeError("CUDA no disponible. El upscale requiere GPU NVIDIA.")

        total_vram = torch.cuda.get_device_properties(0).total_memory
        fraction = min(VRAM_LIMIT_PCT / 100.0, 1.0)
        torch.cuda.set_per_process_memory_fraction(fraction, 0)
        print(f"GPU worker: VRAM cap {fraction*100:.0f}% ({fraction*total_vram/1024**3:.1f}GB de {total_vram/1024**3:.1f}GB)", flush=True)

        torch.backends.cudnn.benchmark = True
        torch.backends.cuda.matmul.allow_tf32 = True
        torch.set_float32_matmul_precision('high')
        # Cache kernels compilados en disco — elimina el overhead JIT en reinicios
        import torch._inductor.config as _ind_cfg
        _ind_cfg.fx_graph_cache = True
        # Usar directorio persistente en lugar de /tmp (que se borra al reiniciar WSL)
        os.environ.setdefault('TORCHINDUCTOR_CACHE_DIR', '/root/.cache/torchinductor')

        # Load all models for the active key
        active_key = _active_model_key[0]
        cfg = MODEL_REGISTRY[active_key]
        loaded_models = {}
        primary_in_channels = 1
        primary_scale = 4
        # Algunos modelos (APISR/GRL) fallan en fp16 ("mat1/mat2 dtype float != Half") y con
        # torch.compile → se cargan en fp32 eager. El resto sigue en fp16+compile (rápido).
        use_half = cfg.get('half', True)
        _gpu_half[0] = use_half
        _dtype = torch.float16 if use_half else torch.float32

        for entry in cfg['models']:
            sub_key, path = entry['sub_key'], entry['file']
            if not Path(path).exists():
                raise FileNotFoundError(
                    f"Modelo no encontrado: {path} — colócalo ahí o ajusta "
                    f"models/registry.json (ver docs/MODELS.md)"
                )
            print(f"GPU worker: cargando {Path(path).name}...", flush=True)
            desc = ModelLoader().load_from_file(str(path))
            m = desc.model.cuda().eval().to(memory_format=torch.channels_last)
            if use_half:
                m = m.half()
                m.forward = torch.compile(m.forward, mode='default', fullgraph=False)
            loaded_models[sub_key] = m
            primary_in_channels = getattr(desc, 'input_channels', 1)
            primary_scale = getattr(desc, 'scale', 4)

        _gpu_in_channels[0] = primary_in_channels
        _gpu_scale[0] = primary_scale

        # Warmup — varios pasos para que torch.compile termine de compilar kernels (solo fp16)
        first_model = next(iter(loaded_models.values()))
        dummy = torch.randn(1, primary_in_channels, TILE_SIZE, TILE_SIZE,
                            device='cuda', dtype=_dtype).to(memory_format=torch.channels_last)
        print(f"GPU worker: preparando ({'fp16+compile' if use_half else 'fp32'})...", flush=True)
        with torch.inference_mode():
            for _ in range(3 if use_half else 1):
                _ = first_model(dummy)
        torch.cuda.synchronize()
        del dummy

        _gpu_worker_ready.set()
        print(f"GPU worker listo — modelo={active_key}, tile={TILE_SIZE}px, batch={GPU_BATCH_SIZE}, half={use_half}, cudnn.benchmark=ON", flush=True)

        # ── Main loop ──────────────────────────────────────────────────────────
        while True:
            pending = []
            try:
                item = _gpu_queue.get(timeout=5.0)
                if item is None:
                    break
                pending.append(item)
            except queue.Empty:
                torch.cuda.empty_cache()
                continue

            # Drain up to GPU_BATCH_SIZE-1 more without blocking
            while len(pending) < GPU_BATCH_SIZE:
                try:
                    pending.append(_gpu_queue.get_nowait())
                except queue.Empty:
                    break

            try:
                _process_batch(pending, loaded_models, torch, time)
            except Exception as e:
                for _, fut, _ in pending:
                    if not fut._event.is_set():
                        fut.set_exception(e)
            finally:
                for _ in pending:
                    _gpu_queue.task_done()

    except Exception as startup_err:
        _gpu_worker_error[0] = startup_err
        _gpu_worker_ready.set()
        while True:
            try:
                item = _gpu_queue.get_nowait()
                if item:
                    item[1].set_exception(startup_err)
                _gpu_queue.task_done()
            except queue.Empty:
                break


def _process_batch(pending, loaded_models, torch, time):
    """Process a batch of tiles, grouped by sub_key. OOM-safe: retries one-by-one."""
    import collections

    tile_t_ms = _gpu_tile_throttle_ms[0]

    # Group by sub_key so each model processes its tiles as a batch
    by_model = collections.defaultdict(list)
    for item in pending:
        tensor, fut, sub_key = item
        by_model[sub_key].append((tensor, fut))

    for sub_key, items in by_model.items():
        model = loaded_models.get(sub_key) or next(iter(loaded_models.values()))

        if tile_t_ms > 0:
            # Eco: one tile at a time with sleep
            for tensor, fut in items:
                try:
                    with torch.inference_mode():
                        r = model(tensor)
                    torch.cuda.synchronize()
                    fut.set_result(r[0:1].clone())
                    time.sleep(tile_t_ms / 1000.0)
                except Exception as e:
                    fut.set_exception(e)
        else:
            # Full: batch all tiles for this model
            try:
                batch = torch.cat([t for t, _ in items], dim=0)
                with torch.inference_mode():
                    out = model(batch)
                torch.cuda.synchronize()
                for i, (_, fut) in enumerate(items):
                    fut.set_result(out[i:i + 1].clone())
            except torch.cuda.OutOfMemoryError:
                # OOM: retry each tile individually
                torch.cuda.empty_cache()
                print(f"GPU OOM en batch de {len(items)} tiles — reintentando 1×1", flush=True)
                for tensor, fut in items:
                    try:
                        with torch.inference_mode():
                            r = model(tensor)
                        torch.cuda.synchronize()
                        fut.set_result(r[0:1].clone())
                        torch.cuda.empty_cache()
                    except Exception as e2:
                        fut.set_exception(e2)

    t_ms = _gpu_throttle_ms[0]
    if t_ms > 0:
        time.sleep(t_ms / 1000.0)


def _ensure_gpu_worker():
    """Start GPU worker if not alive and wait until model is loaded."""
    global _gpu_worker_thread
    with _gpu_worker_lock:
        if _gpu_worker_thread is None or not _gpu_worker_thread.is_alive():
            _gpu_worker_error[0] = None
            _gpu_worker_ready.clear()
            _gpu_worker_thread = threading.Thread(
                target=_gpu_worker_loop, daemon=True, name="gpu-worker"
            )
            _gpu_worker_thread.start()
    _gpu_worker_ready.wait(timeout=120)
    if _gpu_worker_error[0]:
        raise _gpu_worker_error[0]


def _submit_tile(tile_tensor, sub_key='eula'):
    """Submit a CUDA tile tensor to the GPU worker; returns a _TileFuture."""
    fut = _TileFuture()
    _gpu_queue.put((tile_tensor, fut, sub_key))
    return fut


def _get_sub_key(img_height: int) -> str:
    """Return the correct sub_key for the active model given a page height.
    Adaptive models pick among their sub-models by `height_max` (ascending);
    the one entry without a height_max is the catch-all for anything taller —
    this works for any registry.json adaptive set, not just MangaJaNai's."""
    key = _active_model_key[0]
    cfg = MODEL_REGISTRY.get(key, next(iter(MODEL_REGISTRY.values())))
    models = cfg['models']
    if not cfg.get('adaptive') or len(models) == 1:
        return models[0]['sub_key']
    bounded = sorted((m for m in models if m.get('height_max')), key=lambda m: m['height_max'])
    for m in bounded:
        if img_height < m['height_max']:
            return m['sub_key']
    catch_all = next((m for m in models if not m.get('height_max')), None)
    return (catch_all or models[-1])['sub_key']


# ── Tiled upscale ────────────────────────────────────────────────────────────

def _upscale_tiled(img_pil, in_channels=1, tile_size=TILE_SIZE, overlap=TILE_OVERLAP, scale=None, sub_key='eula'):
    """Upscale img_pil via the shared GPU worker queue.
    Phase 1: submit ALL tiles at once (non-blocking) so the worker can fill full batches.
    Phase 2: collect results and reconstruct the image."""
    import torch
    from PIL import Image

    if scale is None:
        scale = _gpu_scale[0]   # whatever scale the currently-loaded model actually reports

    if in_channels == 1:
        img_np = np.asarray(img_pil.convert('L'), dtype=np.float32) / 255.0
        img_np = img_np[:, :, np.newaxis]
    else:
        img_np = np.asarray(img_pil.convert('RGB'), dtype=np.float32) / 255.0

    h, w, c = img_np.shape
    oh, ow = h * scale, w * scale
    stride = tile_size - overlap

    def make_starts(length):
        starts = list(range(0, length, stride))
        last = max(0, length - tile_size)
        if not starts or starts[-1] != last:
            starts.append(last)
        return starts

    # Phase 1 — enqueue every tile before waiting on any result.
    # With the old blocking .result() call, only 1 tile was ever in the queue at a time
    # so GPU_BATCH_SIZE was effectively 1. Submitting all tiles up-front lets the worker
    # drain the queue in full batches without waiting on Python round-trips.
    tile_meta = []
    for y0 in make_starts(h):
        for x0 in make_starts(w):
            y0c = max(0, min(y0, h - 1))
            x0c = max(0, min(x0, w - 1))
            y1 = min(y0c + tile_size, h)
            x1 = min(x0c + tile_size, w)
            th, tw = y1 - y0c, x1 - x0c

            tile = img_np[y0c:y1, x0c:x1]
            if th < tile_size or tw < tile_size:
                padded = np.zeros((tile_size, tile_size, c), dtype=np.float32)
                padded[:th, :tw] = tile
                tile_in = padded
            else:
                tile_in = tile

            t = torch.from_numpy(tile_in).permute(2, 0, 1).unsqueeze(0)
            t = t.to(device='cuda', dtype=(torch.float16 if _gpu_half[0] else torch.float32), non_blocking=True)
            t = t.contiguous(memory_format=torch.channels_last)
            tile_meta.append((y0c, x0c, y1, x1, th, tw, _submit_tile(t, sub_key)))

    # Phase 2 — collect results in submission order and reconstruct.
    out_sum = np.zeros((oh, ow, c), dtype=np.float32)
    out_wgt = np.zeros((oh, ow, 1), dtype=np.float32)

    for y0c, x0c, y1, x1, th, tw, fut in tile_meta:
        out_t = fut.result()

        out_squeezed = out_t.squeeze(0)
        if out_squeezed.ndim == 2:
            out_tile = out_squeezed.cpu().float().numpy()[:, :, np.newaxis]
        else:
            out_tile = out_squeezed.permute(1, 2, 0).cpu().float().numpy()
        out_tile = out_tile[:th * scale, :tw * scale]

        fade = min(overlap * scale, th * scale // 2, tw * scale // 2)
        wgt = np.ones((th * scale, tw * scale), dtype=np.float32)
        if fade > 1:
            if y0c > 0:
                ramp = np.linspace(0.0, 1.0, fade, dtype=np.float32)
                wgt[:fade, :] *= ramp[:, None]
            if x0c > 0:
                ramp = np.linspace(0.0, 1.0, fade, dtype=np.float32)
                wgt[:, :fade] *= ramp[None, :]

        oy0, ox0 = y0c * scale, x0c * scale
        oy1, ox1 = y1 * scale, x1 * scale
        out_sum[oy0:oy1, ox0:ox1] += out_tile * wgt[:, :, None]
        out_wgt[oy0:oy1, ox0:ox1] += wgt[:, :, None]

    out_wgt = np.maximum(out_wgt, 1e-6)
    result_np = np.clip(out_sum / out_wgt * 255, 0, 255).astype(np.uint8)
    if c == 1:
        return Image.fromarray(result_np[:, :, 0], 'L')
    return Image.fromarray(result_np, 'RGB')


# ── Status helpers ────────────────────────────────────────────────────────────

def set_upscale_status(task_id, status):
    payload = dict(status)
    total = payload.get('total')
    progress = payload.get('progress', payload.get('current'))
    if total not in (None, 0) and progress is not None:
        try:
            payload.setdefault('percent', round((float(progress) * 100.0) / float(total), 2))
        except (TypeError, ValueError, ZeroDivisionError):
            pass
    payload.setdefault('task_id', task_id)
    old = upscale_status.get(task_id, {})
    old_status = old.get('status')
    # Stamp completion time once, on entering a terminal state — feeds the Activity history.
    if payload.get('status') in ('done', 'complete', 'error', 'cancelled', 'interrupted'):
        payload.setdefault('ended_at', old.get('ended_at') or time.time())
    upscale_status[task_id] = payload
    if payload.get('status') != old_status:
        _persist_upscale_status()


def _status_file_candidates(task_id):
    candidates = [Path(UPSCALED_DIR) / f'.{task_id}_status.json']
    folder_hint = task_id.rsplit('_ch', 1)[0]
    if folder_hint:
        candidates.append(Path(UPSCALED_DIR) / folder_hint / f'.{task_id}_status.json')
    return candidates


def _read_status_file(task_id):
    for status_file in _status_file_candidates(task_id):
        if not status_file.exists():
            continue
        try:
            with open(status_file, encoding='utf-8') as f:
                data = json.load(f)
                data.setdefault('task_id', task_id)
                return data
        except Exception:
            continue
    return None


def _clear_status_files(task_id):
    for status_file in _status_file_candidates(task_id):
        try:
            status_file.unlink()
        except (FileNotFoundError, Exception):
            continue


def get_upscale_status(task_id=None):
    if task_id:
        live = upscale_status.get(task_id)
        if live and live.get('status') in {'upscaling', 'starting', 'started'}:
            return live
        file_data = _read_status_file(task_id)
        if file_data:
            set_upscale_status(task_id, file_data)
            return file_data
        return live or {'status': 'not_found', 'task_id': task_id}
    return upscale_status.copy()


# ── Blueprint routes ──────────────────────────────────────────────────────────

upscale_bp = Blueprint('upscale', __name__)


@upscale_bp.route('/status/<upscale_id>', methods=['GET'])
def get_status(upscale_id):
    return jsonify(get_upscale_status(upscale_id))


@upscale_bp.route('/status', methods=['GET'])
def get_all_status():
    return jsonify(get_upscale_status())


@upscale_bp.route('/cancel/<path:upscale_id>', methods=['POST'])
def cancel_upscale(upscale_id):
    _upscale_cancel_requested.add(upscale_id)
    return jsonify({'status': 'cancel_requested', 'task_id': upscale_id})


@upscale_bp.route('/repair_chapter', methods=['POST'])
def repair_chapter():
    """Upscale only the pages that are present in the download folder but missing from the upscaled folder."""
    try:
        data = request.get_json()
        title = data.get('title')
        chapter = data.get('chapter')
        mode = _mode_from(data)

        if not title or chapter is None:
            return jsonify({'status': 'error', 'message': 'title and chapter required'}), 400

        from api.library import find_manga_folder
        actual_folder = find_manga_folder(title)
        input_folder = Path(manga_dir()) / actual_folder
        output_folder = Path(upscaled_dir()) / actual_folder

        if not input_folder.exists():
            return jsonify({'status': 'error', 'message': 'Folder not found'}), 404

        chapter_norm = normalize_chapter(chapter)
        try:
            value = Decimal(chapter_norm)
            int_part = int(value.to_integral_value(rounding='ROUND_FLOOR'))
            ch_prefix = f"ch{int_part:04d}.{chapter_norm.split('.')[-1]}_" if '.' in chapter_norm else f"ch{int_part:04d}_"
        except (InvalidOperation, ValueError):
            ch_prefix = f"ch{chapter_norm}_"

        all_images = sorted(input_folder.glob(ch_prefix + "*.*"))

        # Find images whose stem is absent from the upscaled folder (check all extensions)
        output_folder.mkdir(parents=True, exist_ok=True)
        _IMAGE_EXTS = ('jpg', 'jpeg', 'png', 'webp')
        up_stems = set()
        for ext in _IMAGE_EXTS:
            for f in output_folder.glob(f'{ch_prefix}*.{ext}'):
                up_stems.add(f.stem)

        missing_images = [p for p in all_images if p.stem not in up_stems]

        if not missing_images:
            return jsonify({'status': 'nothing_to_repair', 'chapter': chapter_norm, 'task_id': None})

        upscale_id = build_task_id(actual_folder, chapter_norm, 'upscale')

        current_status = get_upscale_status(upscale_id)
        if current_status.get('status') in {'starting', 'started', 'upscaling'}:
            return jsonify({'status': 'already_running', 'task_id': upscale_id}), 200

        _clear_status_files(upscale_id)
        set_upscale_status(upscale_id, {
            'status': 'upscaling',
            'current': 0,
            'progress': 0,
            'total': len(missing_images),
            'percent': 0,
            'title': actual_folder,
            'chapter': chapter_norm,
            'model': MODEL_REGISTRY[_active_model_key[0]]['label'],
            'repair': True,
        })

        if mode == 'eco':
            _gpu_throttle_ms[0] = _ECO_THROTTLE_MS
            _gpu_tile_throttle_ms[0] = _ECO_TILE_THROTTLE_MS
        else:
            _gpu_throttle_ms[0] = _FULL_THROTTLE_MS
            _gpu_tile_throttle_ms[0] = 0

        threading.Thread(
            target=run_upscale_chapter,
            args=(input_folder, output_folder, missing_images, upscale_id),
            kwargs={'eco': mode == 'eco'},
            daemon=True,
        ).start()

        return jsonify({
            'status': 'started',
            'total': len(missing_images),
            'chapter': chapter_norm,
            'task_id': upscale_id,
            'repair': True,
        })
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@upscale_bp.route('/models', methods=['GET'])
def get_models():
    """List available models and which is active."""
    return jsonify({
        'active': _active_model_key[0],
        'models': {k: v['label'] for k, v in MODEL_REGISTRY.items()},
        'color': {k: bool(v.get('color')) for k, v in MODEL_REGISTRY.items()},
    })


@upscale_bp.route('/set_model', methods=['POST'])
def set_model():
    """Switch upscale model. Restarts GPU worker to load new model(s)."""
    global _gpu_worker_thread
    data = request.get_json(silent=True) or {}
    key = data.get('model', 'eula')
    if key not in MODEL_REGISTRY:
        return jsonify({'status': 'error', 'message': f'Modelo desconocido: {key}'}), 400

    cfg = MODEL_REGISTRY[key]
    missing = [m['file'] for m in cfg['models'] if not Path(m['file']).exists()]
    if missing:
        return jsonify({'status': 'error', 'message': 'Archivos de modelo no encontrados', 'missing': missing}), 404

    _active_model_key[0] = key

    # Stop current worker so it reloads with new model on next request
    with _gpu_worker_lock:
        if _gpu_worker_thread and _gpu_worker_thread.is_alive():
            _gpu_queue.put(None)   # poison pill
            _gpu_worker_thread.join(timeout=10)
        _gpu_worker_thread = None
        _gpu_worker_ready.clear()
        _gpu_worker_error[0] = None

    return jsonify({'status': 'ok', 'model': key, 'label': cfg['label']})


def _mode_from(data):
    """Normalize the throttle mode from either contract.

    The Vite frontend sends {eco: bool}; the legacy UI sends {mode: 'eco'|'full'}.
    Accept both so eco mode (MPV-friendly GPU throttling) actually takes effect.
    """
    if 'mode' in data and data.get('mode'):
        return data['mode']
    if 'eco' in data:
        return 'eco' if data.get('eco') else 'full'
    return 'full'


def _fast_from(data):
    """fast_mode (legacy) or fast (Vite)."""
    return bool(data.get('fast_mode', data.get('fast', False)))


@upscale_bp.route('/mode', methods=['POST'])
def set_upscale_mode():
    data = request.get_json(silent=True) or {}
    mode = _mode_from(data)
    if mode == 'eco':
        _gpu_throttle_ms[0] = _ECO_THROTTLE_MS
        _gpu_tile_throttle_ms[0] = _ECO_TILE_THROTTLE_MS
    else:
        _gpu_throttle_ms[0] = _FULL_THROTTLE_MS
        _gpu_tile_throttle_ms[0] = 0
    return jsonify({'mode': mode, 'throttle_ms': _gpu_throttle_ms[0], 'tile_throttle_ms': _gpu_tile_throttle_ms[0]})


@upscale_bp.route('/upscale_chapter', methods=['POST'])
def upscale_chapter():
    try:
        data = request.get_json()
        title = data.get('title')
        chapter = data.get('chapter')
        mode = _mode_from(data)
        fast_mode = _fast_from(data)

        if not title or chapter is None:
            return jsonify({'status': 'error', 'message': 'title required'}), 400

        cfg = MODEL_REGISTRY.get(_active_model_key[0], MODEL_REGISTRY['eula'])
        missing = [m['file'] for m in cfg['models'] if not Path(m['file']).exists()]
        if missing:
            return jsonify({
                'status': 'error',
                'message': f'Modelo no encontrado: {cfg["label"]}',
                'missing_paths': missing,
            }), 404

        from api.library import find_manga_folder
        actual_folder = find_manga_folder(title)
        input_folder = Path(manga_dir()) / actual_folder
        output_folder = Path(upscaled_dir()) / actual_folder
        output_folder.mkdir(parents=True, exist_ok=True)

        if not input_folder.exists():
            return jsonify({'status': 'error', 'message': 'Folder not found'}), 404

        chapter_norm = normalize_chapter(chapter)
        try:
            value = Decimal(chapter_norm)
            int_part = int(value.to_integral_value(rounding='ROUND_FLOOR'))
            if '.' in chapter_norm:
                ch_prefix = f"ch{int_part:04d}.{chapter_norm.split('.')[-1]}_"
            else:
                ch_prefix = f"ch{int_part:04d}_"
        except (InvalidOperation, ValueError):
            ch_prefix = f"ch{chapter_norm}_"

        images = sorted(input_folder.glob(ch_prefix + "*.*"))

        if not images:
            return jsonify({'status': 'error', 'message': 'No images found', 'task_id': build_task_id(actual_folder, chapter_norm, 'upscale')}), 404

        # Manual page exclusion (e.g. color pages reviewed by the user): copy them
        # as-is into output_folder so the resume-skip below treats them exactly
        # like already-upscaled pages — no change to the GPU worker/selection logic.
        exclude_pages = set(data.get('exclude_pages') or data.get('excludePages') or [])
        if exclude_pages:
            import shutil as _shutil
            for p in images:
                if p.name in exclude_pages and not (output_folder / p.name).exists():
                    _shutil.copy2(p, output_folder / p.name)

        # Skip pages already upscaled (resume support)
        _IMAGE_EXTS = ('jpg', 'jpeg', 'png', 'webp')
        up_stems = {f.stem for ext in _IMAGE_EXTS for f in output_folder.glob(f'{ch_prefix}*.{ext}')}
        images_todo = [p for p in images if p.stem not in up_stems]

        if not images_todo:
            upscale_id = build_task_id(actual_folder, chapter_norm, 'upscale')
            return jsonify({'status': 'complete', 'message': 'Ya completamente escalado', 'task_id': upscale_id, 'total': len(images), 'resumed': True}), 200

        skipped_already = len(images) - len(images_todo)
        images = images_todo
        total = len(images)
        upscale_id = build_task_id(actual_folder, chapter_norm, 'upscale')

        current_status = get_upscale_status(upscale_id)
        if current_status.get('status') in {'starting', 'started', 'upscaling'}:
            return jsonify({
                'status': 'already_running',
                'upscale_id': upscale_id,
                'task_id': upscale_id,
                'total': current_status.get('total', total),
                'current': current_status.get('progress', current_status.get('current', 0)),
                'percent': current_status.get('percent', 0),
            }), 200

        _clear_status_files(upscale_id)

        set_upscale_status(upscale_id, {
            'status': 'upscaling',
            'current': 0,
            'progress': 0,
            'total': total,
            'percent': 0,
            'title': actual_folder,
            'chapter': chapter_norm,
            'model': MODEL_REGISTRY[_active_model_key[0]]['label'],
            'scale': _gpu_scale[0] // 2 if fast_mode else _gpu_scale[0],
            'skipped_existing': skipped_already,
            'fast': fast_mode,
        })

        if mode == 'eco':
            _gpu_throttle_ms[0] = _ECO_THROTTLE_MS
            _gpu_tile_throttle_ms[0] = _ECO_TILE_THROTTLE_MS
        else:
            _gpu_throttle_ms[0] = _FULL_THROTTLE_MS
            _gpu_tile_throttle_ms[0] = 0

        threading.Thread(
            target=run_upscale_chapter,
            args=(input_folder, output_folder, images, upscale_id),
            kwargs={'eco': mode == 'eco', 'fast': fast_mode},
            daemon=True,
        ).start()

        return jsonify({'status': 'started', 'total': total, 'chapter': chapter_norm, 'upscale_id': upscale_id, 'task_id': upscale_id, 'async': True, 'mode': mode, 'resumed': skipped_already > 0, 'skipped_existing': skipped_already})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


def _switch_model(key):
    """Cambia el modelo activo y reinicia el worker (misma lógica que /set_model).
    Devuelve (ok, mensaje)."""
    global _gpu_worker_thread
    if key not in MODEL_REGISTRY:
        return False, f'Modelo desconocido: {key}'
    if key == _active_model_key[0] and _gpu_worker_ready.is_set():
        return True, 'ya activo'
    cfg = MODEL_REGISTRY[key]
    missing = [m['file'] for m in cfg['models'] if not Path(m['file']).exists()]
    if missing:
        return False, f'Archivos de modelo no encontrados: {missing}'
    _active_model_key[0] = key
    with _gpu_worker_lock:
        if _gpu_worker_thread and _gpu_worker_thread.is_alive():
            _gpu_queue.put(None)
            _gpu_worker_thread.join(timeout=10)
        _gpu_worker_thread = None
        _gpu_worker_ready.clear()
        _gpu_worker_error[0] = None
    return True, 'ok'


def _color_prefix(chapter):
    """Prefijo de archivo (ch####_) para un capítulo, como en upscale_chapter."""
    chapter_norm = normalize_chapter(chapter)
    try:
        value = Decimal(chapter_norm)
        int_part = int(value.to_integral_value(rounding='ROUND_FLOOR'))
        if '.' in chapter_norm:
            return chapter_norm, f"ch{int_part:04d}.{chapter_norm.split('.')[-1]}_"
        return chapter_norm, f"ch{int_part:04d}_"
    except (InvalidOperation, ValueError):
        return chapter_norm, f"ch{chapter_norm}_"


def _color_marker(output_folder, chapter_norm):
    return output_folder / f'.color_done_{chapter_norm}'


def filter_by_chapters(paths, want):
    """Deja sólo las páginas de los capítulos `want` (ya normalizados). `want` vacío = todo.

    Se filtra ANTES de detectar color porque detectar es lo caro: `_detect_color` abre cada
    página. MEDIDO en Amayo no Tsuki: el manga entero tarda 53,7 s; acotado a 3 capítulos, 2,8 s.
    Ambos lados normalizan (`normalize_chapter`), así que '01', '1' y '1.0' son el mismo capítulo.
    """
    if not want:
        return list(paths)
    return [p for p in paths if _chapter_norm_of(p.name) in want]


def _chapter_norm_of(filename):
    """Extrae el capítulo normalizado del nombre 'ch0001_001.jpg' → '1', 'ch0012.5_003.jpg' → '12.5'.
    Inverso de _color_prefix; se usa para agrupar páginas por capítulo en el flujo masivo."""
    m = re.match(r'ch0*(\d+(?:\.\d+)?)_', filename)
    if not m:
        return ''
    return normalize_chapter(m.group(1))


def _run_color_job(input_folder, output_folder, images, upscale_id, chapter_norms):
    """Ejecuta el escalado a color (síncrono en su hilo) y, si termina bien, deja un
    marcador '.color_done_<cap>' en la carpeta 4K por CADA capítulo tocado → el botón de
    color de ese capítulo desaparece (como el 4K). El marcador es verdad en disco (sobrevive
    reinicios). `chapter_norms` = iterable de capítulos (uno, o varios en el flujo masivo)."""
    run_upscale_chapter(input_folder, output_folder, images, upscale_id, eco=False, fast=False)
    try:
        if get_upscale_status(upscale_id).get('status') == 'complete':
            for cn in chapter_norms:
                if cn:
                    _color_marker(output_folder, cn).touch()
    except Exception:
        pass


def _measure_page_color(path):
    """Mide UNA página. Formato de `cached_measure`: (bytes, chapters, extra)."""
    from PIL import Image
    try:
        return 0, 0, {'c': bool(is_color_page(Image.open(path)))}
    except Exception:
        # Una página ilegible NO es a color; se cachea igual para no reintentar abrirla
        # en cada apertura del selector.
        return 0, 0, {'c': False}


def _detect_color(candidates):
    """Devuelve la sublista de rutas que el detector considera a color.

    Cacheado POR PÁGINA (SQLite en disco, `index_db`), invalidado por el mtime del archivo: si
    la página no cambió, sigue siendo a color o no. Abrir y decodificar cada imagen costaba
    **53,7 s en el manga entero** (Amayo, 1861 págs) EN CADA apertura del selector.

    Por página y no por carpeta a propósito: así el filtro por capítulos sigue sirviendo (medir
    sólo lo que marcaste: 2,8 s para 3 capítulos) en vez de obligar a medir el manga completo la
    primera vez. Y en disco, no en RAM: la memoria de esta máquina es limitada y esto es
    puramente un memo — borrar index.db sólo fuerza un remedido.
    """
    from api.index_db import cached_measure
    out = []
    for p in candidates:
        _, _, extra = cached_measure('colorpage', str(p), str(p), _measure_page_color)
        if extra.get('c'):
            out.append(p)
    return out


def _start_color(actual_folder, input_folder, output_folder, images, chapter_norms, id_suffix, model_key):
    """Arranca el hilo de escalado a color con estado inicial. Devuelve (json, code).
    `chapter_norms` = lista de capítulos tocados (uno en el flujo por capítulo, varios en el
    masivo); se usa para borrar/escribir los marcadores '.color_done_<cap>'."""
    chapter_norms = [c for c in (chapter_norms or []) if c]
    upscale_id = build_task_id(actual_folder, id_suffix, 'upscale')
    current_status = get_upscale_status(upscale_id)
    if current_status.get('status') in {'starting', 'started', 'upscaling'}:
        return {'status': 'already_running', 'task_id': upscale_id}, 200
    ok, msg = _switch_model(model_key)
    if not ok:
        return {'status': 'error', 'message': msg}, 404
    output_folder.mkdir(parents=True, exist_ok=True)
    # Un re-escalado a color rehace las páginas → borra los marcadores previos hasta que acabe.
    for cn in chapter_norms:
        try: _color_marker(output_folder, cn).unlink()
        except OSError: pass
    _clear_status_files(upscale_id)
    set_upscale_status(upscale_id, {
        'status': 'upscaling', 'current': 0, 'progress': 0, 'total': len(images),
        'percent': 0, 'title': actual_folder,
        'chapter': chapter_norms[0] if len(chapter_norms) == 1 else 'todo',
        'model': MODEL_REGISTRY[model_key]['label'], 'scale': _gpu_scale[0], 'color': True,
    })
    _gpu_throttle_ms[0] = _FULL_THROTTLE_MS
    _gpu_tile_throttle_ms[0] = 0
    threading.Thread(
        target=_run_color_job,
        args=(input_folder, output_folder, images, upscale_id, chapter_norms),
        daemon=True,
    ).start()
    return {'status': 'started', 'total': len(images), 'task_id': upscale_id,
            'model': model_key, 'label': MODEL_REGISTRY[model_key]['label']}, 200


def _color_ctx(data):
    """Resuelve (actual_folder, input_folder, output_folder, model_key) o (None, error, code)."""
    title = data.get('title')
    if not title:
        return None, ({'status': 'error', 'message': 'title requerido'}, 400)
    color_default = next((k for k, v in MODEL_REGISTRY.items() if v.get('color')), _active_model_key[0])
    model_key = data.get('model') or color_default
    if not MODEL_REGISTRY.get(model_key, {}).get('color'):
        return None, ({'status': 'error', 'message': f'{model_key} no es un modelo a color'}, 400)
    from api.library import find_manga_folder
    actual_folder = find_manga_folder(title)
    input_folder = Path(manga_dir()) / actual_folder
    output_folder = Path(upscaled_dir()) / actual_folder
    if not input_folder.exists():
        return None, ({'status': 'error', 'message': 'Folder not found'}, 404)
    return (actual_folder, input_folder, output_folder, model_key), None


@upscale_bp.route('/color_pages', methods=['GET'])
def color_pages():
    """Datos para el SELECTOR de páginas a color de un capítulo: cada página con su nombre,
    URL y si el detector la considera a color (pre-marcada). `done` = ya escaladas a color."""
    title = request.args.get('title')
    chapter = request.args.get('chapter')
    if not title or chapter is None:
        return jsonify({'error': 'title y chapter requeridos'}), 400
    from api.library import find_manga_folder
    actual_folder = find_manga_folder(title)
    input_folder = Path(manga_dir()) / actual_folder
    output_folder = Path(upscaled_dir()) / actual_folder
    if not input_folder.exists():
        return jsonify({'error': 'Folder not found'}), 404
    chapter_norm, prefix = _color_prefix(chapter)
    imgs = sorted(input_folder.glob(prefix + '*.*'))
    # Solo se muestran las páginas a COLOR (las B&N no son relevantes para este flujo).
    color_imgs = _detect_color(imgs)
    pages = [{'name': p.name, 'url': f'{actual_folder}/{p.name}', 'color': True} for p in color_imgs]
    return jsonify({'folder': actual_folder, 'chapter': chapter_norm,
                    'done': _color_marker(output_folder, chapter_norm).exists(), 'pages': pages})


@upscale_bp.route('/color_pages_all', methods=['GET'])
def color_pages_all():
    """Como /color_pages pero para VARIOS capítulos: devuelve, agrupadas por capítulo, SOLO las
    páginas a color detectadas (las B&N se excluyen). Alimenta el selector masivo del MangaModal,
    que reutiliza la misma selección manual que el selector de un capítulo.

    `chapters` (CSV, opcional) lo acota a los capítulos MARCADOS en la lista; sin él mira el manga
    entero. Se filtra ANTES de detectar color, que es lo caro: `_detect_color` abre cada página."""
    title = request.args.get('title')
    if not title:
        return jsonify({'error': 'title requerido'}), 400
    want = {normalize_chapter(c) for c in (request.args.get('chapters') or '').split(',') if c.strip()}
    from api.library import find_manga_folder
    actual_folder = find_manga_folder(title)
    input_folder = Path(manga_dir()) / actual_folder
    output_folder = Path(upscaled_dir()) / actual_folder
    if not input_folder.exists():
        return jsonify({'error': 'Folder not found'}), 404
    color_imgs = _detect_color(filter_by_chapters(sorted(input_folder.glob('ch*_*.*')), want))
    groups = {}
    for p in color_imgs:
        cn = _chapter_norm_of(p.name)
        if not cn:
            continue
        groups.setdefault(cn, []).append(
            {'name': p.name, 'url': f'{actual_folder}/{p.name}', 'color': True})
    done_set = {m.name[len('.color_done_'):] for m in output_folder.glob('.color_done_*')} \
        if output_folder.exists() else set()
    chapters = [{'chapter': cn, 'done': cn in done_set, 'pages': groups[cn]}
                for cn in sorted(groups, key=lambda c: Decimal(c) if c else Decimal(0))]
    return jsonify({'folder': actual_folder, 'chapters': chapters})


@upscale_bp.route('/color_status', methods=['GET'])
def color_status():
    """Capítulos con marcador de color hecho (para ocultar el botón en la lista de capítulos)."""
    title = request.args.get('title')
    if not title:
        return jsonify({'done': []}), 200
    from api.library import find_manga_folder
    actual_folder = find_manga_folder(title)
    output_folder = Path(upscaled_dir()) / actual_folder
    done = []
    if output_folder.exists():
        for m in output_folder.glob('.color_done_*'):
            done.append(m.name[len('.color_done_'):])
    return jsonify({'done': done})


@upscale_bp.route('/upscale_pages', methods=['POST'])
def upscale_pages():
    """Escala SOLO las páginas ELEGIDAS por el usuario (nombres de archivo) de un capítulo,
    con el modelo a color. Es el flujo del selector del MangaModal."""
    try:
        data = request.get_json() or {}
        chapter = data.get('chapter')
        pages = data.get('pages') or []
        if chapter is None or not pages:
            return jsonify({'status': 'error', 'message': 'chapter y pages requeridos'}), 400
        ctx, err = _color_ctx(data)
        if err:
            return jsonify(err[0]), err[1]
        actual_folder, input_folder, output_folder, model_key = ctx
        chapter_norm, prefix = _color_prefix(chapter)
        wanted = {p if isinstance(p, str) else str(p) for p in pages}
        images = [p for p in sorted(input_folder.glob(prefix + '*.*')) if p.name in wanted]
        if not images:
            return jsonify({'status': 'error', 'message': 'Ninguna de las páginas elegidas existe'}), 404
        body, code = _start_color(actual_folder, input_folder, output_folder, images,
                                  [chapter_norm], chapter_norm + '_color', model_key)
        return jsonify(body), code
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@upscale_bp.route('/upscale_pages_multi', methods=['POST'])
def upscale_pages_multi():
    """Escala las páginas a color ELEGIDAS por el usuario a lo largo de VARIOS capítulos, en un
    solo trabajo. `selections` = [{chapter, pages:[nombres]}]. Es el flujo masivo con la MISMA
    selección manual que el selector de un capítulo. task_id sufijo 'all_color'."""
    try:
        data = request.get_json() or {}
        selections = data.get('selections') or []
        if not selections:
            return jsonify({'status': 'error', 'message': 'selections requerido'}), 400
        ctx, err = _color_ctx(data)
        if err:
            return jsonify(err[0]), err[1]
        actual_folder, input_folder, output_folder, model_key = ctx
        images, norms = [], []
        for sel in selections:
            chapter = sel.get('chapter')
            wanted = {str(p) for p in (sel.get('pages') or [])}
            if chapter is None or not wanted:
                continue
            chapter_norm, prefix = _color_prefix(chapter)
            found = [p for p in sorted(input_folder.glob(prefix + '*.*')) if p.name in wanted]
            if found:
                images.extend(found)
                norms.append(chapter_norm)
        if not images:
            return jsonify({'status': 'error', 'message': 'Ninguna de las páginas elegidas existe'}), 404
        body, code = _start_color(actual_folder, input_folder, output_folder, images,
                                  norms, 'all_color', model_key)
        return jsonify(body), code
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@upscale_bp.route('/upscale_color', methods=['POST'])
def upscale_color():
    """Auto-detecta las páginas a COLOR y las escala con el modelo a color (APISR).
    `chapter` opcional: un capítulo; si falta, TODO el manga. Es el flujo automático."""
    try:
        data = request.get_json() or {}
        ctx, err = _color_ctx(data)
        if err:
            return jsonify(err[0]), err[1]
        actual_folder, input_folder, output_folder, model_key = ctx
        chapter = data.get('chapter')
        if chapter is not None and str(chapter) != '':
            chapter_norm, prefix = _color_prefix(chapter)
            candidates = sorted(input_folder.glob(prefix + '*.*'))
            id_suffix = chapter_norm + '_color'
        else:
            chapter_norm = None
            candidates = sorted(input_folder.glob('ch*_*.*'))
            id_suffix = 'all_color'
        color_imgs = _detect_color(candidates)
        if not color_imgs:
            return jsonify({'status': 'none', 'total': 0, 'message': 'No se detectaron páginas a color'}), 200
        norms = [chapter_norm] if chapter_norm else sorted({_chapter_norm_of(p.name) for p in color_imgs})
        body, code = _start_color(actual_folder, input_folder, output_folder, color_imgs,
                                  norms, id_suffix, model_key)
        return jsonify(body), code
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


# ── Worker threads (chapter threads submit tiles to GPU worker) ───────────────

def run_upscale_chapter(input_folder, output_folder, images, upscale_id, eco=False, fast=False):
    # Acota los capítulos concurrentes (cualquier modo) → evita el OOM de RAM por buffers
    # de reconstrucción en paralelo que crashea el WSL. Cola GPU única = sin pérdida de
    # rendimiento real. Se bloquea aquí hasta que haya un hueco de los _UPSCALE_MAX_PARALLEL.
    _chapter_semaphore.acquire()
    _avail0, _total0, _rss0 = _mem_snapshot()
    print(f"[upscale] inicio {upscale_id} — modelo={_active_model_key[0]} páginas={len(images)} "
          f"RAM disp={_avail0}MB/{_total0}MB rss={_rss0}MB "
          f"(máx {_UPSCALE_MAX_PARALLEL} cap. en paralelo)", flush=True)
    try:
        from PIL import Image

        _ensure_gpu_worker()
        in_channels = _gpu_in_channels[0]
        # Saltar páginas a color depende de si el modelo activo es B&N (flag `color` del
        # registro), NO de sus canales: MangaJaNai/DWTP son modelos B&N de manga pero cargan
        # como 3 canales (in_channels==3), así que el viejo `in_channels==1` NO saltaba color
        # con ellos → escalaba páginas a color (mal) y gastaba el triple de RAM. Los modelos a
        # color (APISR/DAT/RCAN) SÍ deben escalar las páginas a color.
        skip_color = not MODEL_REGISTRY.get(_active_model_key[0], {}).get('color', False)

        import time as _time
        processed = 0
        skipped_color = 0
        total = len(images)
        save_futures = []
        effective_scale = _gpu_scale[0] // 2 if fast else _gpu_scale[0]
        _page_times = []

        for img_path in images:
            if upscale_id in _upscale_cancel_requested:
                _upscale_cancel_requested.discard(upscale_id)
                set_upscale_status(upscale_id, {
                    'status': 'cancelled',
                    'current': processed,
                    'progress': processed,
                    'total': total,
                    'model': MODEL_REGISTRY[_active_model_key[0]]['label'],
                })
                return

            try:
                set_upscale_status(upscale_id, {
                    'status': 'upscaling',
                    'current': processed,
                    'progress': processed,
                    'total': total,
                    'model': MODEL_REGISTRY[_active_model_key[0]]['label'],
                    'scale': effective_scale,
                })

                img = Image.open(img_path)
                # Página ya a resolución de lectura (p.ej. resultado del trasplante con arte 4K):
                # copiarla tal cual — re-escalarla 4x agotaría la RAM y tira el WSL (ver
                # UPSCALE_MAX_INPUT_LONG_SIDE). Antes del posible downscale de `fast` para no perder
                # la resolución del arte HD.
                if is_already_hires(img):
                    print(f"Copy hi-res (no upscale): {img_path.name} {img.width}x{img.height}", flush=True)
                    out_path = output_folder / (img_path.stem + img_path.suffix)
                    for _old in output_folder.glob(img_path.stem + '.*'):
                        if _old.suffix.lower() != img_path.suffix.lower():
                            try: _old.unlink()
                            except OSError: pass
                    shutil.copy2(img_path, out_path)
                    processed += 1
                    continue
                if fast:
                    img = img.resize((max(1, img.width // 2), max(1, img.height // 2)), Image.LANCZOS)
                # Los modelos B&N saltan (copian) las páginas a color; un modelo a color
                # (APISR/DAT/RCAN) SÍ las escala — es su propósito. Se decide por el flag del
                # registro, no por los canales (MangaJaNai/DWTP son B&N pero de 3 canales).
                if skip_color and is_color_page(img):
                    print(f"Copy color (no upscale): {img_path.name}", flush=True)
                    out_path = output_folder / (img_path.stem + img_path.suffix)
                    shutil.copy2(img_path, out_path)
                    skipped_color += 1
                    processed += 1
                    continue

                # Guardarraíl de RAM (backstop ABSOLUTO, no relativo): las páginas realmente
                # enormes ya las cubre is_already_hires (lado ≥3000). Esto solo atrapa un caso
                # patológico que se cuele por debajo de ese umbral pero proyecte una reconstrucción
                # descomunal (> _MAX_RECON_MB). NO se usa la RAM disponible como criterio: en un WSL
                # con poca RAM libre eso copiaba páginas NORMALES sin escalar → "4K falso". Una
                # página normal <3000px proyecta como mucho ~1.3GB y escala bien; solo se salta lo
                # verdaderamente absurdo.
                _proj = _projected_recon_mb(img.width, img.height, in_channels, _gpu_scale[0])
                if _proj > _MAX_RECON_MB:
                    _av, _tot, _rss = _mem_snapshot()
                    print(f"[upscale] WARN página descomunal {img_path.name} {img.width}x{img.height} "
                          f"→ recon~{_proj:.0f}MB > cap {_MAX_RECON_MB}MB (RAM disp={_av}MB): "
                          f"se copia SIN escalar (backstop anti-OOM)", flush=True)
                    out_path = output_folder / (img_path.stem + img_path.suffix)
                    for _old in output_folder.glob(img_path.stem + '.*'):
                        if _old.suffix.lower() != img_path.suffix.lower():
                            try: _old.unlink()
                            except OSError: pass
                    shutil.copy2(img_path, out_path)
                    processed += 1
                    continue

                sub_key = _get_sub_key(img.height)
                _t0 = _time.perf_counter()
                out_pil = _upscale_tiled(img, in_channels=in_channels, sub_key=sub_key)
                _dt = _time.perf_counter() - _t0
                _page_times.append(_dt)
                _avg = sum(_page_times) / len(_page_times)
                _avN, _totN, _rssN = _mem_snapshot()
                print(f"[bench] {'2x⚡' if fast else '4x '} {img_path.name} {img.width}x{img.height} "
                      f"recon~{_proj:.0f}MB — {_dt:.2f}s avg={_avg:.2f}s (n={len(_page_times)})  "
                      f"RAM disp={_avN}MB rss={_rssN}MB", flush=True)
                out_path = output_folder / (img_path.stem + ".jpg")
                # Evita que una copia previa con OTRA extensión (p.ej. el .png de una página a
                # color que el modelo B&N copió tal cual) conviva con el .jpg recién escalado y
                # lo duplique/ensombrezca en el lector.
                for _old in output_folder.glob(img_path.stem + '.*'):
                    if _old.suffix.lower() != '.jpg':
                        try: _old.unlink()
                        except OSError: pass
                save_futures.append(_save_pool.submit(_resize_save, out_pil, out_path, OUTPUT_DOWNSCALE, JPEG_QUALITY))
                processed += 1
            except Exception as e:
                import traceback
                print(f"Error: {img_path}: {e}", flush=True)
                traceback.print_exc()

        # Wait for all pending saves before marking complete
        for fut in save_futures:
            try:
                fut.result()
            except Exception as e:
                print(f"Save error: {e}", flush=True)

        # Release CUDA + Python memory back to the OS
        try:
            import torch, gc
            torch.cuda.empty_cache()
            gc.collect()
            import ctypes
            ctypes.CDLL('libc.so.6').malloc_trim(0)
        except Exception:
            pass

        if processed == total:
            set_upscale_status(upscale_id, {
                'status': 'complete',
                'current': processed,
                'progress': processed,
                'total': total,
                'percent': 100,
                'skipped_color': skipped_color,
                'model': MODEL_REGISTRY[_active_model_key[0]]['label'],
                'scale': _gpu_scale[0],
            })
        else:
            set_upscale_status(upscale_id, {
                'status': 'error',
                'current': processed,
                'progress': processed,
                'total': total,
                'error': f'Upscale incompleto: {processed}/{total}',
                'skipped_color': skipped_color,
                'model': MODEL_REGISTRY[_active_model_key[0]]['label'],
            })

    except Exception as e:
        set_upscale_status(upscale_id, {'status': 'error', 'error': str(e), 'model': MODEL_REGISTRY[_active_model_key[0]]['label']})
    finally:
        _avail1, _total1, _rss1 = _mem_snapshot()
        print(f"[upscale] fin {upscale_id} — RAM disp={_avail1}MB rss={_rss1}MB", flush=True)
        _chapter_semaphore.release()
