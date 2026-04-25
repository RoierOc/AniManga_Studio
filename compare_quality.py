#!/usr/bin/env python3
"""Simple test - resize to 512x512"""
import onnxruntime as ort
import numpy as np
from PIL import Image
import torch
from spandrel import ModelLoader
import os

os.chdir('/root/workspace/manga-upscaler')

# Resize input to exactly 512x512
img = Image.open('/root/workspace/samples/aaa.png').convert('RGB')
img = img.resize((512, 512), Image.LANCZOS)
print(f"Input: {img.size}")

img_np = np.array(img).astype(np.float32) / 255.0

# ========== ONNX ==========
print("\n--- ONNX ---")
sess = ort.InferenceSession('model_2x_fixed.onnx', providers=['CPUExecutionProvider'])
input_name = sess.get_inputs()[0].name

x = np.expand_dims(img_np.transpose(2,0,1), 0).astype(np.float32)
out = sess.run(None, {input_name: x})[0]

out_onnx = out[0].transpose(1,2,0)  # NCHW->HWC
out_onnx = (out_onnx * 255).clip(0,255).astype(np.uint8)
print(f"Output: {out_onnx.shape}")
Image.fromarray(out_onnx).save('compare_onnx_output.png')

# ========== PyTorch ==========
print("\n--- PyTorch ---")
model = ModelLoader().load_from_file('/root/workspace/MangaJaNai/models/2x_IllustrationJaNai_V2standard_FDAT_M_unshuffle_40k.safetensors').model.cuda().eval()

x_t = torch.from_numpy(img_np).permute(2,0,1).unsqueeze(0).cuda()
with torch.no_grad():
    out_t = model(x_t).squeeze().permute(1,2,0).cpu().numpy()
out_torch = (out_t * 255).clip(0,255).astype(np.uint8)
print(f"Output: {out_torch.shape}")
Image.fromarray(out_torch).save('compare_pytorch_output.png')

# ========== Compare ==========
print("\n--- Compare ---")
diff = np.abs(out_torch.astype(float) - out_onnx.astype(float))
print(f"Mean: {diff.mean():.4f}, Max: {diff.max():.4f}")

if diff.mean() < 0.1:
    print("✅ EXCELLENT!")
elif diff.mean() < 1:
    print("👍 GOOD")
else:
    print("❌ Issues")

print("\nImages saved. Check compare_onnx_output.png and compare_pytorch_output.png")