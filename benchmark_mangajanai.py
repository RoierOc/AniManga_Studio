#!/usr/bin/env python3
"""Benchmark MangaJaNai models"""
import time, os
from PIL import Image
import numpy as np
from spandrel import ModelLoader

def test_model(model_path, img_path, runs=2):
    name = os.path.basename(model_path)
    print(f"\n{'='*50}")
    print(f"Model: {name}")
    print(f"{'='*50}")
    
    try:
        model_desc = ModelLoader().load_from_file(model_path)
        model = model_desc.model.cuda().eval()
        print(f"Architecture: {model_desc.architecture}")
        print(f"Scale: {model_desc.scale}x")
        
        img = Image.open(img_path).convert('RGB')
        print(f"Input: {img.size}")
        
        img_np = np.array(img).astype(np.float32) / 255.0
        img_tensor = torch.from_numpy(img_np).permute(2,0,1).unsqueeze(0).cuda()
        
        for _ in range(runs):
            with torch.no_grad():
                out = model(img_tensor)
        
        times = []
        for _ in range(runs):
            start = time.time()
            with torch.no_grad():
                out = model(img_tensor)
            torch.cuda.synchronize()
            times.append((time.time() - start) * 1000)
        
        avg = sum(times) / len(times)
        print(f"Output: {out.shape[3]*model_desc.scale}x{out.shape[2]*model_desc.scale}")
        print(f"Time: {avg:.1f}ms (avg of {runs})")
        return avg
    except Exception as e:
        print(f"Error: {e}")
        return None

if __name__ == "__main__":
    import torch
    os.chdir('/root/workspace/manga-upscaler')
    
    models = [
        '/root/workspace/MangaJaNai/models/2x_IllustrationJaNai_V2standard_FDAT_M_unshuffle_40k.safetensors',
        '/root/workspace/MangaJaNai/models/4x_IllustrationJaNai_V2standard_DAT2_27k.safetensors',
        '/root/workspace/MangaJaNai/models/4x_IllustrationJaNai_V2standard_FDAT_M_52k.safetensors',
        '/root/workspace/MangaJaNai/models/4x_IllustrationJaNai_V2standard_FDAT_XL_18k.safetensors',
    ]
    
    results = []
    for m in models:
        if os.path.exists(m):
            t = test_model(m, '/root/workspace/samples/aaa.png')
            if t:
                results.append((os.path.basename(m), t))
    
    print(f"\n{'='*50}")
    print("SUMMARY (lower is better)")
    print(f"{'='*50}")
    for n, t in sorted(results, key=lambda x: x[1]):
        print(f"{n}: {t:.1f}ms")