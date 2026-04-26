#!/usr/bin/env python3
"""
Upscale API - Upscale manga images using MangaJaNai models
"""

from flask import Blueprint, jsonify, request
from pathlib import Path
import threading
import json
import os
import numpy as np
import sys
from decimal import Decimal, InvalidOperation

from api.runtime import (
    MANGA_DIR,
    UPSCALED_DIR,
    MODEL_PATH_EULA_4X,
    PROJECT_ROOT,
    build_task_id,
    normalize_chapter,
)

upscale_status = {}
ACTIVE_MODEL_PATH = MODEL_PATH_EULA_4X
UPSCALE_SCALE = 4

COLOR_DIFF_THRESHOLD = int(os.environ.get("COLOR_DIFF_THRESHOLD", "15"))
COLOR_PIXEL_FRACTION = float(os.environ.get("COLOR_PIXEL_FRACTION", "0.10"))


def is_color_page(img_pil, threshold=COLOR_DIFF_THRESHOLD, min_fraction=COLOR_PIXEL_FRACTION):
    """Returns True if image has significant non-grayscale content.
    Uses a thumbnail for speed (< 5ms). Skips color pages during upscaling."""
    thumb = img_pil.copy()
    thumb.thumbnail((256, 256))
    arr = np.asarray(thumb.convert('RGB'), dtype=np.int16)
    r, g, b = arr[:, :, 0], arr[:, :, 1], arr[:, :, 2]
    max_diff = np.maximum(np.maximum(np.abs(r - g), np.abs(r - b)), np.abs(g - b))
    return float(np.mean(max_diff > threshold)) > min_fraction

TILE_SIZE = int(os.environ.get("UPSCALE_TILE_SIZE", "384"))
TILE_OVERLAP = 24

# Singleton: keeps the model in VRAM between chapter calls
_model_cache: dict = {}


def _get_model():
    """Load model once and keep it cached in VRAM. Returns (model, in_channels)."""
    import torch
    from spandrel import ModelLoader

    key = str(ACTIVE_MODEL_PATH)
    if key not in _model_cache:
        torch.backends.cudnn.benchmark = True
        torch.backends.cuda.matmul.allow_tf32 = True
        torch.set_float32_matmul_precision('high')

        model_desc = ModelLoader().load_from_file(key)
        model = model_desc.model.cuda().eval().half().to(memory_format=torch.channels_last)
        in_channels = getattr(model_desc, 'input_channels', 3)

        # Warmup with correct channel count
        dummy = torch.randn(1, in_channels, 256, 256, device='cuda', dtype=torch.float16).to(memory_format=torch.channels_last)
        with torch.inference_mode():
            _ = model(dummy)
        torch.cuda.synchronize()
        del dummy

        _model_cache[key] = (model, in_channels)
    return _model_cache[key]


def _upscale_tiled(model, img_pil, in_channels=1, tile_size=TILE_SIZE, overlap=TILE_OVERLAP, scale=UPSCALE_SCALE):
    """Upscale img_pil in tiles. Handles both grayscale (1ch) and RGB (3ch) models."""
    import torch
    from PIL import Image

    if in_channels == 1:
        img = img_pil.convert('L')
        img_np = np.asarray(img, dtype=np.float32) / 255.0
        img_np = img_np[:, :, np.newaxis]  # (H, W, 1)
    else:
        img = img_pil.convert('RGB')
        img_np = np.asarray(img, dtype=np.float32) / 255.0  # (H, W, 3)

    h, w, c = img_np.shape
    oh, ow = h * scale, w * scale

    out_sum = np.zeros((oh, ow, c), dtype=np.float32)
    out_wgt = np.zeros((oh, ow, 1), dtype=np.float32)

    stride = tile_size - overlap

    def make_starts(length):
        starts = list(range(0, length, stride))
        last = max(0, length - tile_size)
        if not starts or starts[-1] != last:
            starts.append(last)
        return starts

    for y0 in make_starts(h):
        for x0 in make_starts(w):
            y0c = max(0, min(y0, h - 1))
            x0c = max(0, min(x0, w - 1))
            y1 = min(y0c + tile_size, h)
            x1 = min(x0c + tile_size, w)

            tile = img_np[y0c:y1, x0c:x1]
            th, tw = tile.shape[:2]

            if th < tile_size or tw < tile_size:
                padded = np.zeros((tile_size, tile_size, c), dtype=np.float32)
                padded[:th, :tw] = tile
                tile_in = padded
            else:
                tile_in = tile

            # tile_in: (tile_size, tile_size, c) → (1, c, tile_size, tile_size)
            t = torch.from_numpy(tile_in).permute(2, 0, 1).unsqueeze(0)
            t = t.to(device='cuda', dtype=torch.float16, non_blocking=True)
            t = t.contiguous(memory_format=torch.channels_last)

            with torch.inference_mode():
                out_t = model(t)

            # out_t: (1, c_out, th*scale, tw*scale)
            out_squeezed = out_t.squeeze(0)  # (c_out, th*scale, tw*scale)
            if out_squeezed.ndim == 2:
                # single channel squeezed to 2D
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
                if 'task_id' not in data:
                    data['task_id'] = task_id
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
            chapter_int = int(Decimal(chapter_norm))
            ch_prefix = f"ch{chapter_int:04d}_"
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


def run_upscale_chapter(input_folder, output_folder, images, upscale_id):
    try:
        import torch
        from PIL import Image

        if not Path(ACTIVE_MODEL_PATH).exists():
            raise FileNotFoundError(f'Modelo no encontrado: {ACTIVE_MODEL_PATH}')
        if not torch.cuda.is_available():
            raise RuntimeError('CUDA no disponible. El upscale requiere GPU NVIDIA.')

        model, in_channels = _get_model()

        processed = 0
        total = len(images)

        skipped_color = 0
        for img_path in images:
            try:
                current = processed + 1
                set_upscale_status(upscale_id, {
                    'status': 'upscaling',
                    'current': current,
                    'progress': current,
                    'total': total,
                    'model': ACTIVE_MODEL_PATH.name,
                    'scale': UPSCALE_SCALE,
                })

                img = Image.open(img_path)
                if is_color_page(img):
                    print(f"Skip color page: {img_path.name}")
                    skipped_color += 1
                    processed += 1
                    continue

                out_pil = _upscale_tiled(model, img, in_channels=in_channels)
                out_path = output_folder / (img_path.stem + ".jpg")
                out_pil.save(out_path, quality=95, optimize=True, progressive=True)
                processed += 1
            except Exception as e:
                print(f"Error: {img_path}: {e}")

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
        import torch
        from PIL import Image

        if not torch.cuda.is_available():
            raise RuntimeError('CUDA no disponible.')

        model, in_channels = _get_model()
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
                out_pil = _upscale_tiled(model, img, in_channels=in_channels)

                out_path = output_folder / (img_path.stem + ".jpg")
                out_pil.save(out_path, quality=95, optimize=True, progressive=True)
                processed += 1
            except Exception as e:
                print(f"Error: {e}")

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
