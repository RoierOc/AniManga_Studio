#!/usr/bin/env python3
"""
Upscale API - single GPU worker thread with tile batching.
"""

from flask import Blueprint, jsonify, request
from pathlib import Path
import threading
import queue
import json
import os
import shutil
import numpy as np
from decimal import Decimal, InvalidOperation
from concurrent.futures import ThreadPoolExecutor

from api.runtime import (
    MANGA_DIR,
    UPSCALED_DIR,
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
VRAM_LIMIT_PCT = int(os.environ.get("VRAM_LIMIT_PCT", "74"))       # 74% = ~8.8GB allocator + ~1.2GB overhead CUDA/cuDNN ≈ 10GB total (RTX 5070)
GPU_THROTTLE_MS = int(os.environ.get("GPU_THROTTLE_MS", "0"))     # 0ms en modo full — sin sleep entre batches

# Eco/full mode — eco adds throttle so MPV can run alongside
_ECO_THROTTLE_MS = 80      # ms sleep after each batch of tiles
_ECO_TILE_THROTTLE_MS = 8  # additional ms sleep between individual tiles in the batch
_FULL_THROTTLE_MS = 0
_gpu_throttle_ms = [GPU_THROTTLE_MS]   # mutable; worker reads this after each batch
_gpu_tile_throttle_ms = [0]            # mutable; worker reads this between tiles

# Eco concurrency limit: max 2 chapters at a time in eco mode.
# Full mode is unlimited — chapters race for the GPU worker queue.
_eco_semaphore = threading.Semaphore(4)

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

        for entry in cfg['models']:
            sub_key, path = entry['sub_key'], entry['file']
            if not Path(path).exists():
                raise FileNotFoundError(
                    f"Modelo no encontrado: {path} — colócalo ahí o ajusta "
                    f"models/registry.json (ver docs/MODELS.md)"
                )
            print(f"GPU worker: cargando {Path(path).name}...", flush=True)
            desc = ModelLoader().load_from_file(str(path))
            m = desc.model.cuda().eval().half().to(memory_format=torch.channels_last)
            m.forward = torch.compile(m.forward, mode='default', fullgraph=False)
            loaded_models[sub_key] = m
            primary_in_channels = getattr(desc, 'input_channels', 1)
            primary_scale = getattr(desc, 'scale', 4)

        _gpu_in_channels[0] = primary_in_channels
        _gpu_scale[0] = primary_scale

        # Warmup — varios pasos para que torch.compile termine de compilar kernels
        first_model = next(iter(loaded_models.values()))
        dummy = torch.randn(1, primary_in_channels, TILE_SIZE, TILE_SIZE,
                            device='cuda', dtype=torch.float16).to(memory_format=torch.channels_last)
        print("GPU worker: compilando kernels (torch.compile warmup)...", flush=True)
        with torch.inference_mode():
            for _ in range(3):
                _ = first_model(dummy)
        torch.cuda.synchronize()
        del dummy

        _gpu_worker_ready.set()
        print(f"GPU worker listo — modelo={active_key}, tile={TILE_SIZE}px, batch={GPU_BATCH_SIZE}, compile=ON, cudnn.benchmark=ON", flush=True)

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
            t = t.to(device='cuda', dtype=torch.float16, non_blocking=True)
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
    old_status = upscale_status.get(task_id, {}).get('status')
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
        input_folder = Path(MANGA_DIR) / actual_folder
        output_folder = Path(UPSCALED_DIR) / actual_folder

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
        input_folder = Path(MANGA_DIR) / actual_folder
        output_folder = Path(UPSCALED_DIR) / actual_folder
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


@upscale_bp.route('/upscale_manga', methods=['POST'])
def upscale_manga():
    data = request.get_json()
    title = data.get('title')

    if not title:
        return jsonify({'status': 'error', 'message': 'title required'}), 400

    from api.library import find_manga_folder
    actual_folder = find_manga_folder(title)
    input_folder = Path(MANGA_DIR) / actual_folder
    output_folder = Path(UPSCALED_DIR) / actual_folder
    output_folder.mkdir(parents=True, exist_ok=True)

    if not input_folder.exists():
        return jsonify({'status': 'error', 'message': 'Folder not found'}), 404

    images = sorted(input_folder.glob('*.jpg')) + sorted(input_folder.glob('*.png'))

    if not images:
        return jsonify({'status': 'error', 'message': 'No images found'}), 404

    # Manual page exclusion (e.g. color pages reviewed by the user): copy them
    # as-is into output_folder and drop them from the work list — additive only,
    # the GPU worker never sees these files.
    exclude_pages = set(data.get('exclude_pages') or data.get('excludePages') or [])
    if exclude_pages:
        import shutil as _shutil
        kept = []
        for p in images:
            if p.name in exclude_pages:
                if not (output_folder / p.name).exists():
                    _shutil.copy2(p, output_folder / p.name)
            else:
                kept.append(p)
        images = kept
        if not images:
            return jsonify({'status': 'error', 'message': 'No images left to upscale after exclusions'}), 404

    total = len(images)
    upscale_id = build_task_id(actual_folder, 'all', 'upscale')

    set_upscale_status(upscale_id, {'status': 'upscaling', 'current': 0, 'progress': 0, 'total': total, 'title': actual_folder})

    threading.Thread(target=run_upscale_all, args=(input_folder, output_folder, images, upscale_id), daemon=True).start()

    return jsonify({'status': 'started', 'total': total, 'upscale_id': upscale_id})


# ── Worker threads (chapter threads submit tiles to GPU worker) ───────────────

def run_upscale_chapter(input_folder, output_folder, images, upscale_id, eco=False, fast=False):
    if eco:
        # Only 1 eco chapter runs at a time — acquire before touching the GPU queue
        _eco_semaphore.acquire()
    try:
        from PIL import Image

        _ensure_gpu_worker()
        in_channels = _gpu_in_channels[0]

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
                if fast:
                    img = img.resize((max(1, img.width // 2), max(1, img.height // 2)), Image.LANCZOS)
                if is_color_page(img):
                    print(f"Copy color (no upscale): {img_path.name}", flush=True)
                    out_path = output_folder / (img_path.stem + img_path.suffix)
                    shutil.copy2(img_path, out_path)
                    skipped_color += 1
                    processed += 1
                    continue

                sub_key = _get_sub_key(img.height)
                _t0 = _time.perf_counter()
                out_pil = _upscale_tiled(img, in_channels=in_channels, sub_key=sub_key)
                _dt = _time.perf_counter() - _t0
                _page_times.append(_dt)
                _avg = sum(_page_times) / len(_page_times)
                print(f"[bench] {'2x⚡' if fast else '4x '} {img_path.name} — {_dt:.2f}s  avg={_avg:.2f}s  (n={len(_page_times)})", flush=True)
                out_path = output_folder / (img_path.stem + ".jpg")
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
        if eco:
            _eco_semaphore.release()


def run_upscale_all(input_folder, output_folder, images, upscale_id):
    try:
        from PIL import Image

        _ensure_gpu_worker()
        in_channels = _gpu_in_channels[0]
        processed = 0

        for img_path in images:
            try:
                set_upscale_status(upscale_id, {
                    'status': 'upscaling',
                    'current': processed,
                    'progress': processed,
                    'total': len(images),
                    'model': MODEL_REGISTRY[_active_model_key[0]]['label'],
                    'scale': _gpu_scale[0],
                })

                img = Image.open(img_path)
                if is_color_page(img):
                    out_path = output_folder / (img_path.stem + img_path.suffix)
                    shutil.copy2(img_path, out_path)
                    processed += 1
                    continue

                out_pil = _upscale_tiled(img, in_channels=in_channels)
                out_path = output_folder / (img_path.stem + ".jpg")
                _save_pool.submit(_resize_save, out_pil, out_path, OUTPUT_DOWNSCALE, JPEG_QUALITY)
                processed += 1
            except Exception as e:
                print(f"Error: {e}", flush=True)

        try:
            import torch
            torch.cuda.empty_cache()
        except Exception:
            pass

        set_upscale_status(upscale_id, {
            'status': 'complete',
            'current': processed,
            'progress': processed,
            'total': len(images),
            'model': MODEL_REGISTRY[_active_model_key[0]]['label'],
            'scale': _gpu_scale[0],
        })

    except Exception as e:
        set_upscale_status(upscale_id, {'status': 'error', 'error': str(e)})
