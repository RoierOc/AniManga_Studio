#!/usr/bin/env python3
"""Test ONNX model"""
import onnxruntime as ort
import numpy as np
import time

print("Loading ONNX model...")
sess = ort.InferenceSession('/root/workspace/manga-upscaler/model_2x.onnx', providers=['CPUExecutionProvider'])
print("Providers:", sess.get_providers())

x = np.random.randn(1, 3, 256, 256).astype(np.float32)

# Warmup
for _ in range(2):
    out = sess.run(None, {'input': x})

# Time
times = []
for _ in range(5):
    start = time.time()
    out = sess.run(None, {'input': x})
    times.append((time.time() - start) * 1000)

print(f'CPU time: {sum(times)/len(times):.1f}ms')
print("Output shape:", out[0].shape)