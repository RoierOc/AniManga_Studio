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
import numpy as np
from decimal import Decimal, InvalidOperation

from api.runtime import (
    MANGA_DIR,
    UPSCALED_DIR,
    MODEL_PATH_EULA_4X,
    build_task_id,
    normalize_chapter,
)

upscale_status = {}
ACTIVE_MODEL_PATH = MODEL_PATH_EULA_4X
UPSCALE_SCALE = 4

COLOR_DIFF_THRESHOLD = int(os.environ.get("COLOR_DIFF_THRESHOLD", "15"))
COLOR_PIXEL_FRACTION = float(os.environ.get("COLOR_PIXEL_FRACTION", "0.10"))

TILE_SIZE = int(os.environ.get("UPSCALE_TILE_SIZE", "384"))
TILE_OVERLAP = int(os.environ.get("UPSCALE_TILE_OVERLAP", "24"))
GPU_BATCH_SIZE = int(os.environ.get("GPU_BATCH_SIZE", "8"))


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


def _gpu_worker_loop():
    """Single daemon thread: owns CUDA context, processes tile batches."""
    import torch
    from spandrel import ModelLoader

    try:
        if not torch.cuda.is_available():
            raise RuntimeError("CUDA no disponible. El upscale requiere GPU NVIDIA.")
        if not Path(ACTIVE_MODEL_PATH).exists():
            raise FileNotFoundError(f"Modelo no encontrado: {ACTIVE_MODEL_PATH}")

        torch.backends.cudnn.benchmark = True
        torch.backends.cuda.matmul.allow_tf32 = True
        torch.set_float32_matmul_precision('high')

        print(f"GPU worker: cargando {Path(ACTIVE_MODEL_PATH).name}...", flush=True)
        model_desc = ModelLoader().load_from_file(str(ACTIVE_MODEL_PATH))
        model = model_desc.model.cuda().eval().half().to(memory_format=torch.channels_last)
        in_channels = getattr(model_desc, 'input_channels', 3)
        _gpu_in_channels[0] = in_channels

        # Warmup pass (cuDNN algorithm selection)
        dummy = torch.randn(1, in_channels, TILE_SIZE, TILE_SIZE, device='cuda', dtype=torch.float16).to(memory_format=torch.channels_last)
        with torch.inference_mode():
            _ = model(dummy)
        torch.cuda.synchronize()
        del dummy

        # torch.compile default: kernel fusion without CUDA graphs — avoids per-batch-size
        # VRAM pre-allocation that reduce-overhead causes (~4 GB extra on 12 GB cards).
        try:
            model = torch.compile(model, mode='default')
            dummy = torch.randn(1, in_channels, TILE_SIZE, TILE_SIZE, device='cuda', dtype=torch.float16).to(memory_format=torch.channels_last)
            with torch.inference_mode():
                _ = model(dummy)
            torch.cuda.synchronize()
            del dummy
            print("GPU worker: torch.compile activo (mode=default)", flush=True)
        except Exception as ce:
            print(f"GPU worker: torch.compile omitido ({type(ce).__name__})", flush=True)

        _gpu_worker_ready.set()
        print(f"GPU worker listo — tile={TILE_SIZE}px, overlap={TILE_OVERLAP}px, batch={GPU_BATCH_SIZE}", flush=True)

        # ── Main loop: drain queue in batches ──
        while True:
            pending = []

            # Block for first item
            try:
                item = _gpu_queue.get(timeout=5.0)
                if item is None:
                    break
                pending.append(item)
            except queue.Empty:
                continue

            # Drain up to GPU_BATCH_SIZE-1 more without blocking
            while len(pending) < GPU_BATCH_SIZE:
                try:
                    pending.append(_gpu_queue.get_nowait())
                except queue.Empty:
                    break

            try:
                batch = torch.cat([p[0] for p in pending], dim=0)
                with torch.inference_mode():
                    out = model(batch)
                for i, (_, fut) in enumerate(pending):
                    fut.set_result(out[i:i + 1].clone())
            except Exception as e:
                for _, fut in pending:
                    fut.set_exception(e)
            finally:
                for _ in pending:
                    _gpu_queue.task_done()

    except Exception as startup_err:
        _gpu_worker_error[0] = startup_err
        _gpu_worker_ready.set()
        # Drain pending queue so chapter threads don't hang
        while True:
            try:
                item = _gpu_queue.get_nowait()
                if item:
                    item[1].set_exception(startup_err)
                _gpu_queue.task_done()
            except queue.Empty:
                break


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


def _submit_tile(tile_tensor):
    """Submit a CUDA tile tensor to the GPU worker; returns a _TileFuture."""
    fut = _TileFuture()
    _gpu_queue.put((tile_tensor, fut))
    return fut


# ── Tiled upscale ────────────────────────────────────────────────────────────

def _upscale_tiled(img_pil, in_channels=1, tile_size=TILE_SIZE, overlap=TILE_OVERLAP, scale=UPSCALE_SCALE):
    """Upscale img_pil via the shared GPU worker queue.
    Phase 1: submit ALL tiles at once (non-blocking) so the worker can fill full batches.
    Phase 2: collect results and reconstruct the image."""
    import torch
    from PIL import Image

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
            tile_meta.append((y0c, x0c, y1, x1, th, tw, _submit_tile(t)))

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
    upscale_status[task_id] = payload


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


@upscale_bp.route('/upscale_chapter', methods=['POST'])
def upscale_chapter():
    try:
        data = request.get_json()
        title = data.get('title')
        chapter = data.get('chapter')

        if not title or chapter is None:
            return jsonify({'status': 'error', 'message': 'title required'}), 400

        if not Path(ACTIVE_MODEL_PATH).exists():
            return jsonify({
                'status': 'error',
                'message': f'Modelo 4x no encontrado: {ACTIVE_MODEL_PATH.name}',
                'expected_path': str(ACTIVE_MODEL_PATH),
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
            'model': ACTIVE_MODEL_PATH.name,
            'scale': UPSCALE_SCALE,
        })

        threading.Thread(
            target=run_upscale_chapter,
            args=(input_folder, output_folder, images, upscale_id),
            daemon=True,
        ).start()

        return jsonify({'status': 'started', 'total': total, 'chapter': chapter_norm, 'upscale_id': upscale_id, 'task_id': upscale_id, 'async': True})
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

    total = len(images)
    upscale_id = build_task_id(actual_folder, 'all', 'upscale')

    set_upscale_status(upscale_id, {'status': 'upscaling', 'current': 0, 'progress': 0, 'total': total, 'title': actual_folder})

    threading.Thread(target=run_upscale_all, args=(input_folder, output_folder, images, upscale_id), daemon=True).start()

    return jsonify({'status': 'started', 'total': total, 'upscale_id': upscale_id})


# ── Worker threads (chapter threads submit tiles to GPU worker) ───────────────

def run_upscale_chapter(input_folder, output_folder, images, upscale_id):
    try:
        from PIL import Image

        _ensure_gpu_worker()
        in_channels = _gpu_in_channels[0]

        processed = 0
        skipped_color = 0
        total = len(images)

        for img_path in images:
            try:
                set_upscale_status(upscale_id, {
                    'status': 'upscaling',
                    'current': processed + 1,
                    'progress': processed + 1,
                    'total': total,
                    'model': ACTIVE_MODEL_PATH.name,
                    'scale': UPSCALE_SCALE,
                })

                img = Image.open(img_path)
                if is_color_page(img):
                    print(f"Skip color: {img_path.name}", flush=True)
                    skipped_color += 1
                    processed += 1
                    continue

                out_pil = _upscale_tiled(img, in_channels=in_channels)
                out_path = output_folder / (img_path.stem + ".jpg")
                out_pil.save(out_path, quality=95)
                processed += 1
            except Exception as e:
                print(f"Error: {img_path}: {e}", flush=True)

        if processed == total:
            set_upscale_status(upscale_id, {
                'status': 'complete',
                'current': processed,
                'progress': processed,
                'total': total,
                'percent': 100,
                'skipped_color': skipped_color,
                'model': ACTIVE_MODEL_PATH.name,
                'scale': UPSCALE_SCALE,
            })
        else:
            set_upscale_status(upscale_id, {
                'status': 'error',
                'current': processed,
                'progress': processed,
                'total': total,
                'error': f'Upscale incompleto: {processed}/{total}',
                'skipped_color': skipped_color,
                'model': ACTIVE_MODEL_PATH.name,
            })

    except Exception as e:
        set_upscale_status(upscale_id, {'status': 'error', 'error': str(e), 'model': ACTIVE_MODEL_PATH.name})


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
                    'current': processed + 1,
                    'progress': processed + 1,
                    'total': len(images),
                    'model': ACTIVE_MODEL_PATH.name,
                    'scale': UPSCALE_SCALE,
                })

                img = Image.open(img_path)
                if is_color_page(img):
                    processed += 1
                    continue

                out_pil = _upscale_tiled(img, in_channels=in_channels)
                out_path = output_folder / (img_path.stem + ".jpg")
                out_pil.save(out_path, quality=95, optimize=True, progressive=True)
                processed += 1
            except Exception as e:
                print(f"Error: {e}", flush=True)

        set_upscale_status(upscale_id, {
            'status': 'complete',
            'current': processed,
            'progress': processed,
            'total': len(images),
            'model': ACTIVE_MODEL_PATH.name,
            'scale': UPSCALE_SCALE,
        })

    except Exception as e:
        set_upscale_status(upscale_id, {'status': 'error', 'error': str(e)})
