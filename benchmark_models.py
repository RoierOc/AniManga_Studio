#!/usr/bin/env python3
"""
Benchmark multiple models
Usage: python benchmark_models.py
"""

import time
import torch
from PIL import Image
import numpy as np
from spandrel import ModelLoader
import os

def process_image(model, img_tensor):
    with torch.no_grad():
        return model(img_tensor)

def test_model(model_path, image_path, device='cuda', half=True):
    print(f"\n{'='*50}")
    print(f"Model: {os.path.basename(model_path)}")
    print(f"{'='*50}")
    
    model_desc = ModelLoader().load_from_file(model_path)
    model = model_desc.model.to(device).eval()
    
    if half:
        model = model.half()
    
    print(f"Architecture: {model_desc.architecture}")
    print(f"Scale: {model_desc.scale}x")
    
    img = Image.open(image_path).convert('RGB')
    print(f"Input: {img.size}")
    
    img_np = np.array(img).astype(np.float32) / 255.0
    img_tensor = torch.from_numpy(img_np).permute(2, 0, 1).unsqueeze(0).to(device)
    
    if half:
        img_tensor = img_tensor.half()
    
    warmup = 2
    for _ in range(warmup):
        _ = process_image(model, img_tensor)
    
    torch.cuda.synchronize() if device == 'cuda' else None
    
    runs = 3
    times = []
    for _ in range(runs):
        start = time.time()
        output = process_image(model, img_tensor)
        torch.cuda.synchronize() if device == 'cuda' else None
        elapsed = time.time() - start
        times.append(elapsed * 1000)
    
    avg_time = sum(times) / len(times)
    output_size = (img.size[0] * model_desc.scale, img.size[1] * model_desc.scale)
    
    print(f"Output: {output_size}")
    print(f"Time: {avg_time:.1f}ms (avg of {runs} runs)")
    
    output_np = output.squeeze(0).permute(1, 2, 0).cpu().float().numpy()
    output_np = (output_np * 255).clip(0, 255).astype(np.uint8)
    
    return output_np, avg_time

def main():
    device = 'cuda' if torch.cuda.is_available() else 'cpu'
    print(f"Using device: {device}")
    
    models = [
        '/root/workspace/OPENMODELDB/4x-eula-digimanga-MiA-65k.pth',
        '/root/workspace/OPENMODELDB/new_models/4x_wtp_manga_p_omni.pth',
        '/root/workspace/OPENMODELDB/new_models/2x_MangaScaleV3.pth',
    ]
    
    image_path = '/root/workspace/samples/rdw.png'
    output_dir = '/root/workspace/manga-upscaler/benchmark_results'
    os.makedirs(output_dir, exist_ok=True)
    
    results = []
    for model_path in models:
        if not os.path.exists(model_path):
            print(f"Model not found: {model_path}")
            continue
            
        try:
            output_np, avg_time = test_model(model_path, image_path, device)
            
            model_name = os.path.basename(model_path).replace('.pth', '')
            output_path = os.path.join(output_dir, f"{model_name}_output.png")
            
            from PIL import Image
            output_img = Image.fromarray(output_np)
            output_img.save(output_path)
            print(f"Saved: {output_path}")
            
            results.append((model_name, avg_time))
        except Exception as e:
            print(f"Error: {e}")
    
    print(f"\n{'='*50}")
    print("SUMMARY")
    print(f"{'='*50}")
    for name, time_ms in sorted(results, key=lambda x: x[1]):
        print(f"{name}: {time_ms:.1f}ms")

if __name__ == "__main__":
    main()
