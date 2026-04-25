#!/usr/bin/env python3
"""
Manga Batch Upscaler - Process manga folders with 4x MangaJaNai
Usage: python manga_batch.py input_folder output_folder [--model MODEL]
"""

import os
import sys
import time
import argparse
from pathlib import Path
from PIL import Image
import numpy as np
import torch
from spandrel import ModelLoader

DEFAULT_MODEL = '/root/workspace/MangaJaNai/models/4x_IllustrationJaNai_V2standard_FDAT_M_52k.safetensors'

def load_model(model_path, device='cuda'):
    print(f"Loading model: {model_path}")
    model_desc = ModelLoader().load_from_file(model_path)
    model = model_desc.model.to(device).eval()
    print(f"Scale: {model_desc.scale}x, Device: {device}")
    return model, model_desc

def process_image(model, img_path, output_path, device='cuda'):
    img = Image.open(img_path)
    
    if img.mode == 'RGBA':
        img = img.convert('RGB')
    elif img.mode != 'RGB':
        img = img.convert('RGB')
    
    img_np = np.array(img).astype(np.float32) / 255.0
    img_tensor = torch.from_numpy(img_np).permute(2, 0, 1).unsqueeze(0).to(device)
    
    with torch.no_grad():
        output = model(img_tensor)
    
    output_np = output.squeeze(0).permute(1, 2, 0).cpu().float().numpy()
    output_np = (output_np * 255).clip(0, 255).astype(np.uint8)
    
    output_img = Image.fromarray(output_np)
    output_img.save(output_path, quality=95)
    
    return output_img.size

def get_image_files(folder):
    extensions = {'.jpg', '.jpeg', '.png', '.webp', '.bmp', '.gif'}
    files = []
    for f in sorted(Path(folder).iterdir()):
        if f.suffix.lower() in extensions:
            files.append(f)
    return files

def process_folder(input_folder, output_folder, model_path, device='cuda'):
    model, model_desc = load_model(model_path, device)
    
    os.makedirs(output_folder, exist_ok=True)
    
    image_files = get_image_files(input_folder)
    total = len(image_files)
    
    print(f"\nFound {total} images to process")
    print(f"Output: {output_folder}")
    print("-" * 40)
    
    total_time = 0
    
    for i, img_path in enumerate(image_files, 1):
        output_name = img_path.stem + '.png'
        output_path = os.path.join(output_folder, output_name)
        
        start = time.time()
        try:
            out_size = process_image(model, str(img_path), output_path, device)
            elapsed = time.time() - start
            total_time += elapsed
            
            print(f"[{i}/{total}] {img_path.name} -> {out_size[0]}x{out_size[1]} ({elapsed:.1f}s)")
        except Exception as e:
            print(f"[{i}/{total}] ERROR: {img_path.name}: {e}")
    
    avg_time = total_time / total if total > 0 else 0
    print("-" * 40)
    print(f"Done! Total: {total_time:.1f}s, Avg: {avg_time:.1f}s/image")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Manga Batch Upscaler')
    parser.add_argument('input', help='Input folder')
    parser.add_argument('output', help='Output folder')
    parser.add_argument('--model', default=DEFAULT_MODEL, help='Model path')
    parser.add_argument('--device', default='cuda', help='Device (cuda/cpu)')
    
    args = parser.parse_args()
    
    if not os.path.exists(args.input):
        print(f"Error: Input folder not found: {args.input}")
        sys.exit(1)
    
    process_folder(args.input, args.output, args.model, args.device)
