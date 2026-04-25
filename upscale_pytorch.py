#!/usr/bin/env python3
"""
Manga Upscaler - Optimized for RTX GPUs
Usage: python upscale_pytorch.py input.png output.png [model.pth] [--half] [--tile] [--tile_size]
"""

import sys
import time
import argparse
import torch
from PIL import Image
import numpy as np
from spandrel import ModelLoader

DEFAULT_MODEL = '/root/workspace/OPENMODELDB/new_models/4x_wtp_manga_p_omni.pth'

def detect_grayscale(img):
    if img.mode == 'L':
        return True
    arr = np.array(img)
    if img.mode == 'RGB':
        return np.allclose(arr[:,:,0], arr[:,:,1]) and np.allclose(arr[:,:,1], arr[:,:,2])
    if img.mode == 'RGBA':
        rgb = arr[:,:,:3]
        return np.allclose(rgb[:,:,0], rgb[:,:,1]) and np.allclose(rgb[:,:,1], rgb[:,:,2])
    return False

def to_ycbcr(img):
    img = img.convert('RGB')
    arr = np.array(img).astype(np.float32)
    R, G, B = arr[:,:,0], arr[:,:,1], arr[:,:,2]
    Y = 0.299 * R + 0.587 * G + 0.114 * B
    Cb = 128 - 0.168736 * R - 0.331264 * G + 0.5 * B
    Cr = 128 + 0.5 * R - 0.418688 * G - 0.081312 * B
    return Y.astype(np.float32), Cb.astype(np.float32), Cr.astype(np.float32)

def from_ycbcr(Y, Cb, Cr):
    Y, Cb, Cr = Y.astype(np.float32), Cb.astype(np.float32), Cr.astype(np.float32)
    R = Y + 1.402 * (Cr - 128)
    G = Y - 0.344136 * (Cb - 128) - 0.714136 * (Cr - 128)
    B = Y + 1.772 * (Cb - 128)
    R = np.clip(R, 0, 255).astype(np.uint8)
    G = np.clip(G, 0, 255).astype(np.uint8)
    B = np.clip(B, 0, 255).astype(np.uint8)
    return Image.fromarray(np.stack([R, G, B], axis=2), mode='RGB')

def process_tiled(model, img_tensor, tile_size=512, overlap=32, device='cuda'):
    _, _, h, w = img_tensor.shape
    scale = model.extra_state.get('scale', 4)
    
    if h <= tile_size and w <= tile_size:
        with torch.no_grad():
            return model(img_tensor)
    
    output_h, output_w = h * scale, w * scale
    output = torch.zeros(1, 3, output_h, output_w, device=device)
    weight = torch.zeros(1, 3, output_h, output_w, device=device)
    
    for y in range(0, h, tile_size - overlap):
        for x in range(0, w, tile_size - overlap):
            y_end = min(y + tile_size, h)
            x_end = min(x + tile_size, w)
            
            tile = img_tensor[:, :, y:y_end, x:x_end]
            tile = tile.to(device)
            
            with torch.no_grad():
                out_tile = model(tile)
            
            out_y = y * scale
            out_x = x * scale
            out_y_end = min((y + tile_size) * scale, output_h)
            out_x_end = min((x + tile_size) * scale, output_w)
            
            output[:, :, out_y:out_y_end, out_x:out_x_end] += out_tile[:, :, :out_y_end-out_y, :out_x_end-out_x]
            weight[:, :, out_y:out_y_end, out_x:out_x_end] += 1
    
    output = output / weight
    return output

def process_image(model, model_desc, image_path, output_path, device='cuda', half_precision=False, use_tile=False, tile_size=512):
    img = Image.open(image_path)
    
    if img.mode == 'RGBA':
        img = img.convert('RGB')
    elif img.mode != 'RGB' and img.mode != 'L':
        img = img.convert('RGB')
    
    is_gray = detect_grayscale(img)
    scale = model_desc.scale
    
    if is_gray:
        print(f"Detected grayscale, using Y channel only")
        Y, Cb_fixed, Cr_fixed = to_ycbcr(img)
        Y_tensor = torch.from_numpy(Y / 255.0).unsqueeze(0).unsqueeze(0)
        img_tensor = Y_tensor.repeat(1, 3, 1, 1).to(device)
    else:
        img_np = np.array(img).astype(np.float32) / 255.0
        img_tensor = torch.from_numpy(img_np).permute(2, 0, 1).unsqueeze(0).to(device)
        Cb_fixed = Cr_fixed = None
    
    print(f"Input: {img.size}, Output: {img.size[0]*scale}x{img.size[1]*scale}")
    
    if half_precision and device == 'cuda':
        img_tensor = img_tensor.half()
        model = model.half()
        print("Using FP16")
    
    with torch.no_grad():
        start = time.time()
        
        if use_tile:
            output = process_tiled(model, img_tensor, tile_size=tile_size, device=device)
        else:
            output = model(img_tensor)
        
        elapsed = time.time() - start
    
    output_np = output.squeeze(0).permute(1, 2, 0).cpu().float().numpy()
    output_np = (output_np * 255).clip(0, 255).astype(np.uint8)
    
    if is_gray and Cb_fixed is not None:
        cb_img = Image.fromarray(Cb_fixed.astype(np.uint8))
        cr_img = Image.fromarray(Cr_fixed.astype(np.uint8))
        Cb_up = np.array(cb_img.resize((output_np.shape[1], output_np.shape[0]), Image.BILINEAR))
        Cr_up = np.array(cr_img.resize((output_np.shape[1], output_np.shape[0]), Image.BILINEAR))
        output_img = from_ycbcr(output_np, Cb_up, Cr_up)
    else:
        output_img = Image.fromarray(output_np)
    
    output_img.save(output_path, quality=95)
    print(f"Output: {output_img.size}, Time: {elapsed*1000:.1f}ms, Saved: {output_path}")
    
    return output_img

def load_model(model_path, device='cuda', half_precision=False):
    print(f"Loading: {model_path}")
    model_desc = ModelLoader().load_from_file(model_path)
    model = model_desc.model.to(device).eval()
    
    if half_precision and device == 'cuda':
        model = model.half()
    
    print(f"Architecture: {model_desc.architecture}, Scale: {model_desc.scale}x")
    return model, model_desc

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('input', help='Input image')
    parser.add_argument('output', help='Output image')
    parser.add_argument('model', nargs='?', default=DEFAULT_MODEL)
    parser.add_argument('--half', action='store_true', help='FP16')
    parser.add_argument('--tile', action='store_true', help='Tiled processing')
    parser.add_argument('--tile_size', type=int, default=512, help='Tile size')
    args = parser.parse_args()
    
    device = 'cuda' if torch.cuda.is_available() else 'cpu'
    print(f"Device: {device}")
    
    if device == 'cuda':
        torch.backends.cudnn.benchmark = True
    
    model, model_desc = load_model(args.model, device, args.half)
    process_image(model, model_desc, args.input, args.output, device, args.half, args.tile, args.tile_size)