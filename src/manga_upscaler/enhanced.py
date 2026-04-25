#!/usr/bin/env python3
"""Enhanced manga upscalers with sharpen filters."""

import cv2
import numpy as np
import time


def upscale_sharpen(img: np.ndarray, scale: int = 2, strength: float = 0.5) -> np.ndarray:
    """Upscale with unsharp mask for edge enhancement."""
    # First upscale with bicubic
    h, w = img.shape[:2]
    upscaled = cv2.resize(img, (w * scale, h * scale), interpolation=cv2.INTER_CUBIC)
    
    # Apply unsharp mask
    blur = cv2.GaussianBlur(upscaled, (0, 0), 3.0)
    sharpened = cv2.addWeighted(upscaled, 1.0 + strength, blur, -strength, 0)
    
    return np.clip(sharpened, 0, 255).astype(np.uint8)


def upscale_sharpen_alt(img: np.ndarray, scale: int = 2, passes: int = 1) -> np.ndarray:
    """Multiple sharpen passes for more detail."""
    h, w = img.shape[:2]
    result = cv2.resize(img, (w * scale, h * scale), interpolation=cv2.INTER_CUBIC)
    
    for _ in range(passes):
        kernel = np.array([[-1, -1, -1], 
                         [-1,  9, -1], 
                         [-1, -1, -1]], dtype=np.float32) / 1.0
        result = cv2.filter2D(result, -1, kernel)
        result = np.clip(result, 0, 255).astype(np.uint8)
    
    return result


def upscale_clahe(img: np.ndarray, scale: int = 2) -> np.ndarray:
    """Upscale with CLAHE for contrast enhancement."""
    h, w = img.shape[:2]
    result = cv2.resize(img, (w * scale, h * scale), interpolation=cv2.INTER_CUBIC)
    
    # Apply CLAHE per channel
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    
    if len(result.shape) == 3:
        for c in range(result.shape[2]):
            result[:,:,c] = clahe.apply(result[:,:,c])
    else:
        result = clahe.apply(result)
    
    return result.astype(np.uint8)


def upscale_detail_enhance(img: np.ndarray, scale: int = 2) -> np.ndarray:
    """Upscale and enhance details."""
    h, w = img.shape[:2]
    upscaled = cv2.resize(img, (w * scale, h * scale), interpolation=cv2.INTER_CUBIC)
    
    # Detail enhance
    result = cv2.detailEnhance(upscaled, sigma_s=10, sigma_r=0.15)
    
    return result.astype(np.uint8)


def upscale_edge_preserve(img: np.ndarray, scale: int = 2) -> np.ndarray:
    """Upscale trying to preserve edges."""
    h, w = img.shape[:2]
    
    # Use Lanczos first
    result = cv2.resize(img, (w * scale, h * scale), interpolation=cv2.INTER_LANCZOS4)
    
    # Then edge-preserving smoothing
    result = cv2.edgePreservingFilter(result, flags=cv2.RECURS_FILTER, sigmaColor=10, sigmaSpace=10)
    
    return result.astype(np.uint8)


UPSCALERS = {
    "sharpen": ("Sharp contrast", upscale_sharpen),
    "sharpen-alt": ("Multi-pass sharpen", upscale_sharpen_alt),
    "clahe": ("CLAHE contrast", upscale_clahe),
    "detail": ("Detail enhance", upscale_detail_enhance),
}


def benchmark_sample(img_path: str, scale: int = 2):
    """Benchmark all upscalers on a sample."""
    img = cv2.imread(img_path)
    if img is None:
        print(f"Cannot load: {img_path}")
        return
    
    print(f"\n=== {img_path} ===")
    print(f"Input: {img.shape[1]}x{img.shape[0]} ({img.shape[1]*img.shape[0]*scale*scale/1e6:.1f}MP output)")
    
    for name, (desc, func) in UPSCALERS.items():
        start = time.time()
        result = func(img.copy(), scale)
        elapsed = (time.time() - start) * 1000
        print(f"{name:12} ({desc:18}): {elapsed:6.1f}ms")


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python enhanced.py <image.png> [scale]")
        sys.exit(1)
    
    scale = int(sys.argv[2]) if len(sys.argv) > 2 else 2
    benchmark_sample(sys.argv[1], scale)