#!/usr/bin/env python3
"""
2x + bilinear -> 4x equivalent
"""
import time
import torch
from PIL import Image
import numpy as np
from spandrel import ModelLoader

def upscale_2x_bilinear(model_path, img_path, output_path, half=True):
    from PIL import ImageFilter
    
    print(f"Model: {model_path.split('/')[-1]}")
    
    img = Image.open(img_path)
    orig_size = img.size
    
    if img.mode == 'RGBA':
        img = img.convert('RGB')
    elif img.mode != 'RGB':
        img = img.convert('RGB')
    
    print(f"Input: {orig_size}")
    
    # Load model
    model_desc = ModelLoader().load_from_file(model_path)
    model = model_desc.model.cuda().eval()
    
    if half:
        model = model.half()
    
    img_np = np.array(img).astype(np.float32) / 255.0
    img_tensor = torch.from_numpy(img_np).permute(2,0,1).unsqueeze(0).cuda()
    
    if half:
        img_tensor = img_tensor.half()
    
    # 2x upscale
    with torch.no_grad():
        start = time.time()
        out = model(img_tensor)
    torch.cuda.synchronize()
    t2x = (time.time() - start) * 1000
    
    out_np = out.squeeze(0).permute(1,2,0).cpu().float().numpy()
    out_np = (out_np * 255).clip(0,255).astype(np.uint8)
    img_2x = Image.fromarray(out_np)
    
    # Bilinear 2x more
    bilinear = img_2x.resize((orig_size[0]*4, orig_size[1]*4), Image.BILINEAR)
    
    print(f"2x time: {t2x:.1f}ms")
    print(f"Bilinear 2x: {bilinear.size}")
    print(f"Final: {bilinear.size}")
    
    bilinear.save(output_path, quality=95)
    print(f"Saved: {output_path}")

if __name__ == "__main__":
    import sys
    if len(sys.argv) < 4:
        print("Usage: python upscale_2x_bilinear.py input.png output.png [model]")
        sys.exit(1)
    
    input_path = sys.argv[1]
    output_path = sys.argv[2]
    model_path = sys.argv[3] if len(sys.argv) > 3 else '/root/workspace/MangaJaNai/models/2x_IllustrationJaNai_V2standard_FDAT_M_unshuffle_40k.safetensors'
    
    upscale_2x_bilinear(model_path, input_path, output_path)