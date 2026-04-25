#!/usr/bin/env python3
"""
Upscale worker - runs in a separate process with correct venv
"""

import sys
import os
from pathlib import Path
import numpy as np
import json

CURRENT_DIR = Path(__file__).resolve().parent
SRC_DIR = CURRENT_DIR.parent
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from api.runtime import MODEL_PATH_EULA_4X

ACTIVE_MODEL_PATH = MODEL_PATH_EULA_4X
UPSCALE_SCALE = 4
TILE_SIZE = int(os.environ.get("UPSCALE_TILE_SIZE", "384"))
TILE_OVERLAP = 24


def upscale_tiled(model, img_pil, in_channels=1, tile_size=TILE_SIZE, overlap=TILE_OVERLAP, scale=UPSCALE_SCALE):
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


def main():
    if len(sys.argv) < 5:
        print("Usage: upscale_worker.py <output_folder> <upscale_id> <status_root> <images_csv>")
        sys.exit(1)

    output_folder = Path(sys.argv[1])
    upscale_id = sys.argv[2]
    status_root = Path(sys.argv[3])
    image_paths = [Path(p) for p in sys.argv[4].split(',') if p]

    output_folder.mkdir(parents=True, exist_ok=True)

    print(f"DEBUG: Starting upscale with {len(image_paths)} images", flush=True)

    status_files = [
        status_root / f'.{upscale_id}_status.json',
        output_folder / f'.{upscale_id}_status.json',
    ]

    def write_status(payload):
        data = dict(payload)
        data['task_id'] = upscale_id
        for file_path in status_files:
            try:
                file_path.parent.mkdir(parents=True, exist_ok=True)
                with open(file_path, 'w', encoding='utf-8') as f:
                    json.dump(data, f)
            except Exception:
                pass

    try:
        import torch
        from spandrel import ModelLoader
        from PIL import Image

        print(f"DEBUG: PyTorch version: {torch.__version__}", flush=True)

        if not Path(ACTIVE_MODEL_PATH).exists():
            raise FileNotFoundError(f"Modelo no encontrado: {ACTIVE_MODEL_PATH}")
        if not torch.cuda.is_available():
            raise RuntimeError("CUDA no disponible. El upscale requiere GPU NVIDIA.")

        torch.backends.cudnn.benchmark = True
        torch.backends.cuda.matmul.allow_tf32 = True
        torch.set_float32_matmul_precision('high')

        print(f"DEBUG: Loading model {ACTIVE_MODEL_PATH.name}...", flush=True)
        model_desc = ModelLoader().load_from_file(str(ACTIVE_MODEL_PATH))
        model = model_desc.model.cuda().eval().half().to(memory_format=torch.channels_last)
        in_channels = getattr(model_desc, 'input_channels', 3)

        dummy = torch.randn(1, in_channels, 256, 256, device='cuda', dtype=torch.float16).to(memory_format=torch.channels_last)
        with torch.inference_mode():
            _ = model(dummy)
        torch.cuda.synchronize()
        del dummy

        processed = 0
        total = len(image_paths)

        write_status({
            'status': 'upscaling',
            'current': 0,
            'progress': 0,
            'total': total,
            'percent': 0,
            'model': ACTIVE_MODEL_PATH.name,
        })

        for img_path in image_paths:
            try:
                print(f"DEBUG: Processing {img_path.name}...", flush=True)

                current = processed + 1
                percent = round((current * 100.0) / total, 2) if total else 0
                write_status({
                    'status': 'upscaling',
                    'current': current,
                    'progress': current,
                    'total': total,
                    'percent': percent,
                    'model': ACTIVE_MODEL_PATH.name,
                })

                img = Image.open(img_path)
                out_pil = upscale_tiled(model, img, in_channels=in_channels)

                out_path = output_folder / (img_path.stem + ".jpg")
                out_pil.save(out_path, quality=95, optimize=True, progressive=True)
                processed += 1

                print(f"DEBUG: Saved {out_path.name}", flush=True)
            except Exception as e:
                print(f"ERROR: {img_path}: {e}", flush=True)

        if processed == total:
            write_status({
                'status': 'complete',
                'current': processed,
                'progress': processed,
                'total': total,
                'percent': 100,
                'model': ACTIVE_MODEL_PATH.name,
            })
        else:
            percent = round((processed * 100.0) / total, 2) if total else 0
            write_status({
                'status': 'error',
                'current': processed,
                'progress': processed,
                'total': total,
                'percent': percent,
                'model': ACTIVE_MODEL_PATH.name,
            })

        print(f"DEBUG: Complete - {processed} images processed", flush=True)

    except Exception as e:
        print(f"ERROR: {e}", flush=True)
        write_status({'status': 'error', 'error': str(e), 'model': ACTIVE_MODEL_PATH.name})
        import traceback
        traceback.print_exc()


if __name__ == '__main__':
    main()
