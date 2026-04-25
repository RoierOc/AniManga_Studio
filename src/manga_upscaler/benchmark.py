#!/usr/bin/env python3
"""Quick test suite for all samples."""

import os
import time
import cv2
import numpy as np


UPSCALERS = {
    "fast": lambda img, s: upscale_fast_sharpen(img, s),
    "clahe": lambda img, s: upscale_clahe(img, s),
    "lanczos": lambda img, s: upscale_lanczos(img, s),
}


def upscale_fast_sharpen(img, scale):
    h, w = img.shape[:2]
    result = cv2.resize(img, (w * scale, h * scale), interpolation=cv2.INTER_CUBIC)
    kernel = np.array([[-1, -1, -1], [-1, 9, -1], [-1, -1, -1]], dtype=np.float32)
    result = cv2.filter2D(result, -1, kernel)
    return np.clip(result, 0, 255).astype(np.uint8)


def upscale_clahe(img, scale):
    h, w = img.shape[:2]
    result = cv2.resize(img, (w * scale, h * scale), interpolation=cv2.INTER_CUBIC)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    if len(result.shape) == 3:
        for c in range(result.shape[2]):
            result[:,:,c] = clahe.apply(result[:,:,c])
    else:
        result = clahe.apply(result)
    return result.astype(np.uint8)


def upscale_lanczos(img, scale):
    h, w = img.shape[:2]
    return cv2.resize(img, (w * scale, h * scale), interpolation=cv2.INTER_LANCZOS4)


def benchmark_dir(directory: str, scale: int = 2):
    """Benchmark all PNGs in directory."""
    files = sorted([f for f in os.listdir(directory) if f.endswith('.png')])
    
    print(f"\n{'='*60}")
    print(f"Benchmarking: {directory}")
    print(f"Scale: {scale}x")
    print(f"{'='*60}\n")
    
    results = {name: [] for name in UPSCALERS}
    
    for fname in files:
        fpath = os.path.join(directory, fname)
        img = cv2.imread(fpath)
        if img is None:
            continue
        
        mp = img.shape[0] * img.shape[1] * scale * scale / 1e6
        print(f"{fname:30} {img.shape[1]:4}x{img.shape[0]:4} ({mp:.1f}MP)")
        
        for name, func in UPSCALERS.items():
            start = time.time()
            result = func(img, scale)
            elapsed = (time.time() - start) * 1000
            results[name].append(elapsed)
    
    print(f"\n{'='*60}")
    print("AVG TIME (ms):")
    for name, times in results.items():
        avg = sum(times) / len(times) if times else 0
        print(f"  {name:10}: {avg:6.1f}ms")


if __name__ == "__main__":
    import sys
    directory = sys.argv[1] if len(sys.argv) > 1 else "/root/workspace/samples"
    scale = int(sys.argv[2]) if len(sys.argv) > 2 else 2
    benchmark_dir(directory, scale)