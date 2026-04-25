#!/usr/bin/env python3
"""Manga Upscaler - Compare multiple methods and save results with originals."""

import cv2
import numpy as np
import time
import os
from pathlib import Path


def resize_lanczos(img, scale):
    """Lanczos4 - standard upscaling."""
    h, w = img.shape[:2]
    return cv2.resize(img, (w * scale, h * scale), interpolation=cv2.INTER_LANCZOS4)


def resize_cubic(img, scale):
    """Cubic - standard."""
    h, w = img.shape[:2]
    return cv2.resize(img, (w * scale, h * scale), interpolation=cv2.INTER_CUBIC)


def resize_fast_sharpen(img, scale):
    """Upscale + sharpen kernel."""
    h, w = img.shape[:2]
    result = cv2.resize(img, (w * scale, h * scale), interpolation=cv2.INTER_CUBIC)
    kernel = np.array([[-1, -1, -1], [-1, 9, -1], [-1, -1, -1]], dtype=np.float32) / 1.0
    result = cv2.filter2D(result, -1, kernel)
    return np.clip(result, 0, 255).astype(np.uint8)


def resize_edgetune(img, scale):
    """Upscale with edge-aware processing."""
    h, w = img.shape[:2]
    result = cv2.resize(img, (w * scale, h * scale), interpolation=cv2.INTER_LANCZOS4)
    
    # Edge-preserving smoothing
    result = cv2.edgePreservingFilter(result, flags=cv2.RECURS_FILTER, sigmaColor=15, sigmaSpace=15)
    
    return result.astype(np.uint8)


def resize_detail(img, scale):
    """Detail enhancement."""
    h, w = img.shape[:2]
    result = cv2.resize(img, (w * scale, h * scale), interpolation=cv2.INTER_CUBIC)
    result = cv2.detailEnhance(result, sigma_s=10, sigma_r=0.15)
    return result.astype(np.uint8)


def resize_unsharp(img, scale):
    """Unsharp mask for sharper edges."""
    h, w = img.shape[:2]
    result = cv2.resize(img, (w * scale, h * scale), interpolation=cv2.INTER_CUBIC)
    blur = cv2.GaussianBlur(result, (5, 5), 2.0)
    result = cv2.addWeighted(result, 1.5, blur, -0.5, 0)
    return np.clip(result, 0, 255).astype(np.uint8)


# Try pyanime4k if available
def resize_pyanime4k(img, scale):
    """Anime4K via pyanime4k library."""
    try:
        import pyanime4k
        p = pyanime4k.Processor()
        return p.process(img, float(scale))
    except:
        return None


UPSCALERS = {
    "orig": ("Original", None),  # Skip - just copy
    "lanczos": ("Lanczos4", resize_lanczos),
    "cubic": ("Cubic", resize_cubic),
    "fast_sharpen": ("Fast Sharpen", resize_fast_sharpen),
    "unsharp": ("Unsharp Mask", resize_unsharp),
    "detail": ("Detail Enhance", resize_detail),
    "anime4k": ("Anime4K (pyanime4k)", resize_pyanime4k),
}


def process_sample(input_path, output_dir, scale=2):
    """Process one sample with all methods."""
    img = cv2.imread(input_path)
    if img is None:
        print(f"Cannot read: {input_path}")
        return
    
    base_name = Path(input_path).stem
    
    print(f"\n{'='*60}")
    print(f"Processing: {base_name}")
    print(f"Input: {img.shape[1]}x{img.shape[0]} -> {img.shape[1]*scale}x{img.shape[0]*scale}")
    print(f"{'='*60}")
    
    for name, (desc, func) in UPSCALERS.items():
        if name == "orig":
            continue
        
        output_path = os.path.join(output_dir, f"{base_name}_{name}.png")
        
        if func is None:
            print(f"  {name:15} - skipped")
            continue
        
        try:
            start = time.time()
            result = func(img, scale)
            elapsed = (time.time() - start) * 1000
            
            if result is not None:
                cv2.imwrite(output_path, result)
                print(f"  {name:15}: {elapsed:6.1f}ms -> {output_path}")
            else:
                print(f"  {name:15}: failed/skipped")
        except Exception as e:
            print(f"  {name:15}: error - {e}")


def benchmark_samples(samples_dir, output_dir, scale=2):
    """Process all samples."""
    os.makedirs(output_dir, exist_ok=True)
    
    # Find all PNG files
    png_files = sorted(Path(samples_dir).glob("*.png"))
    
    for f in png_files:
        process_sample(str(f), output_dir, scale)
    
    print(f"\n{'='*60}")
    print(f"Done! Results in: {output_dir}")
    print(f"{'='*60}")


if __name__ == "__main__":
    import sys
    
    if len(sys.argv) < 3:
        print("Usage: python compare.py <samples_dir> <output_dir> [scale]")
        sys.exit(1)
    
    samples_dir = sys.argv[1]
    output_dir = sys.argv[2]
    scale = int(sys.argv[3]) if len(sys.argv) > 3 else 2
    
    benchmark_samples(samples_dir, output_dir, scale)