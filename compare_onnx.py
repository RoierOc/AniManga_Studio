#!/usr/bin/env python3
"""Compare ONNX vs PyTorch"""
import onnxruntime as ort
import numpy as np
import time
from PIL import Image
import torch
from spandrel import ModelLoader
import os

os.chdir('/root/workspace/manga-upscaler')

# Load image
img = Image.open('/root/workspace/samples/aaa.png').convert('RGB')
img_np = np.array(img).astype(np.float32) / 255.0
img_tensor = torch.from_numpy(img_np).permute(2,0,1).unsqueeze(0)

# ============== ONNX ==============
print("="*50)
print("ONNX 2x")
print("="*50)

sess = ort.InferenceSession('/root/workspace/manga-upscaler/model_2x.onnx', providers=['CUDAExecutionProvider', 'CPUExecutionProvider'])
print("Providers:", sess.get_providers())

x_onnx = img_tensor.permute(0,2,3,1).numpy().astype(np.float32)

for _ in range(2):
    out_onnx = sess.run(None, {'input': x_onnx})

times = []
for _ in range(3):
    start = time.time()
    out_onnx = sess.run(None, {'input': x_onnx})
    times.append((time.time() - start) * 1000)

print(f"Time: {sum(times)/len(times):.1f}ms")
print(f"Output shape: {out_onnx[0].shape}")

# ============== PyTorch ==============
print("\n" + "="*50)
print("PyTorch 2x_FDAT")
print("="*50)

model_path = '/root/workspace/MangaJaNai/models/2x_IllustrationJaNai_V2standard_FDAT_M_unshuffle_40k.safetensors'
model_desc = ModelLoader().load_from_file(model_path)
model = model_desc.model.cuda().eval()

with torch.no_grad():
    out_torch = model(img_tensor.cuda())

times = []
for _ in range(3):
    start = time.time()
    with torch.no_grad():
        out_torch = model(img_tensor.cuda())
    torch.cuda.synchronize()
    times.append((time.time() - start) * 1000)

print(f"Time: {sum(times)/len(times):.1f}ms")
print(f"Output shape: {out_torch.shape}")

# Compare outputs
out_np = out_torch.squeeze().permute(1,2,0).cpu().detach().numpy()
out_onnx_np = out_onnx[0][0].transpose(2,0,1)

# Simple diff
diff = np.abs(out_np - out_onnx_np).mean()
print(f"\nMean output difference: {diff:.4f}")