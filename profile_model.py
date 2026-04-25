#!/usr/bin/env python3
"""Measure model resources"""
import torch
from spandrel import ModelLoader
import os
os.environ['CUDA_LAUNCH_BLOCKING'] = '1'

def get_gpu_memory():
    if torch.cuda.is_available():
        return torch.cuda.memory_allocated() / 1024**2, torch.cuda.max_memory_allocated() / 1024**2
    return 0, 0

def profile_model(model_path, img_path='/root/workspace/samples/aaa.png'):
    from PIL import Image
    import numpy as np
    
    name = os.path.basename(model_path)
    print(f"\n{'='*50}")
    print(f"Model: {name}")
    print(f"{'='*50}")
    
    # Load model
    torch.cuda.reset_peak_memory_stats()
    model_desc = ModelLoader().load_from_file(model_path)
    model = model_desc.model.cuda().eval()
    
    mem_model, _ = get_gpu_memory()
    print(f"VRAM model: {mem_model:.1f} MB")
    
    # Load image
    img = Image.open(img_path).convert('RGB')
    img_np = np.array(img).astype(np.float32) / 255.0
    img_tensor = torch.from_numpy(img_np).permute(2,0,1).unsqueeze(0).cuda()
    
    mem_input, _ = get_gpu_memory()
    print(f"VRAM input: {mem_input - mem_model:.1f} MB")
    
    # Forward
    torch.cuda.synchronize()
    with torch.no_grad():
        out = model(img_tensor)
    torch.cuda.synchronize()
    
    mem_total, mem_max = get_gpu_memory()
    print(f"VRAM total: {mem_max:.1f} MB")
    print(f"Output shape: {out.shape}")
    print(f"Scale: {model_desc.scale}x")
    
    # File size
    size_mb = os.path.getsize(model_path) / 1024**2
    print(f"Model file: {size_mb:.1f} MB")
    
    del model, img_tensor, out
    torch.cuda.empty_cache()
    
    return mem_max, size_mb

if __name__ == "__main__":
    models = [
        '/root/workspace/MangaJaNai/models/2x_IllustrationJaNai_V2standard_FDAT_M_unshuffle_40k.safetensors',
        '/root/workspace/MangaJaNai/models/4x_IllustrationJaNai_V2standard_FDAT_M_52k.safetensors',
        '/root/workspace/OPENMODELDB/new_models/4x_wtp_manga_p_omni.pth',
    ]
    
    results = []
    for m in models:
        if os.path.exists(m):
            vram, size = profile_model(m)
            results.append((os.path.basename(m), vram, size))
    
    print(f"\n{'='*50}")
    print("COMPARISON")
    print(f"{'='*50}")
    print(f"{'Model':<50} {'VRAM':>10} {'File':>10}")
    print(f"{'-'*50}")
    for n, v, s in results:
        print(f"{n:<50} {v:>9.1f}MB {s:>9.1f}MB")