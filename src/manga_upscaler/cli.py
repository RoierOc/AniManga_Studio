#!/usr/bin/env python3
"""Manga Upscaler CLI - Fast and efficient upscaling for manga/comics."""

import sys
import time
import cv2
import numpy as np
from pathlib import Path


def upscale_bicubic(img: np.ndarray, scale: int = 2, **kwargs) -> np.ndarray:
    """Standard bicubic upscaling."""
    h, w = img.shape[:2]
    return cv2.resize(img, (w * scale, h * scale), interpolation=cv2.INTER_CUBIC)


def upscale_lanczos(img: np.ndarray, scale: int = 2, **kwargs) -> np.ndarray:
    """Lanczos4 - sharper than bicubic."""
    h, w = img.shape[:2]
    return cv2.resize(img, (w * scale, h * scale), interpolation=cv2.INTER_LANCZOS4)


def upscale_sharpen(img: np.ndarray, scale: int = 2, strength: float = 1.0, **kwargs) -> np.ndarray:
    """Upscale + sharpen. Fast and effective."""
    h, w = img.shape[:2]
    upscaled = cv2.resize(img, (w * scale, h * scale), interpolation=cv2.INTER_CUBIC)
    
    # Unsharp mask
    blur = cv2.GaussianBlur(upscaled, (0, 0), 3.0)
    sharpened = cv2.addWeighted(upscaled, 1.0 + strength, blur, -strength, 0)
    
    return np.clip(sharpened, 0, 255).astype(np.uint8)


def upscale_fast_sharpen(img: np.ndarray, scale: int = 2, passes: int = 1, **kwargs) -> np.ndarray:
    """Fast sharpen - multiple passes."""
    h, w = img.shape[:2]
    result = cv2.resize(img, (w * scale, h * scale), interpolation=cv2.INTER_CUBIC)
    
    kernel = np.array([[-1, -1, -1], 
                      [-1,  9, -1], 
                      [-1, -1, -1]], dtype=np.float32) / 1.0
    
    for _ in range(passes):
        result = cv2.filter2D(result, -1, kernel)
        result = np.clip(result, 0, 255).astype(np.uint8)
    
    return result


def upscale_clahe(img: np.ndarray, scale: int = 2, **kwargs) -> np.ndarray:
    """Upscale + contrast enhancement (CLAHE)."""
    h, w = img.shape[:2]
    result = cv2.resize(img, (w * scale, h * scale), interpolation=cv2.INTER_CUBIC)
    
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    
    if len(result.shape) == 3:
        for c in range(result.shape[2]):
            result[:,:,c] = clahe.apply(result[:,:,c])
    else:
        result = clahe.apply(result)
    
    return result.astype(np.uint8)


UPSCALERS = {
    "bicubic": (upscale_bicubic, "Standard bicubic interpolation"),
    "lanczos": (upscale_lanczos, "Lanczos4 - sharper than bicubic"),
    "sharpen": (upscale_sharpen, "Bicubic + unsharp mask"),
    "fast": (upscale_fast_sharpen, "Fast sharpen (default for manga)"),
    "clahe": (upscale_clahe, "CLAHE contrast enhancement"),
}


def process_image(input_path: str, output_path: str, method: str = "fast", scale: int = 2, **kwargs):
    """Process single image."""
    img = cv2.imread(input_path)
    if img is None:
        raise ValueError(f"Cannot load: {input_path}")
    
    print(f"Input: {img.shape[1]}x{img.shape[0]}")
    
    if method not in UPSCALERS:
        print(f"Unknown method: {method}, using 'fast'")
        method = "fast"
    
    func, desc = UPSCALERS[method]
    
    start = time.time()
    result = func(img, scale, **kwargs)
    elapsed = (time.time() - start) * 1000
    
    print(f"Output: {result.shape[1]}x{result.shape[0]}")
    print(f"Time: {elapsed:.1f}ms ({desc})")
    
    cv2.imwrite(output_path, result)
    print(f"Saved: {output_path}")
    
    return result, elapsed


def main():
    if len(sys.argv) < 3:
        print("Usage: manga-upscaler <input.png> <output.png> [options]")
        print()
        print("Options:")
        print("  --method NAME   upscaling method (default: fast)")
        print("    bicubic      - standard bicubic")
        print("    lanczos     - lanczos4")
        print("    sharpen    - bicubic + unsharp mask")
        print("    fast        - fast sharpen (recommended for manga)")
        print("    clahe      - CLAHE contrast")
        print("  --scale N     scale factor (default: 2)")
        sys.exit(1)
    
    input_path = sys.argv[1]
    output_path = sys.argv[2]
    
    method = "fast"
    scale = 2
    
    # Parse args
    i = 3
    while i < len(sys.argv):
        if sys.argv[i] == "--method" and i + 1 < len(sys.argv):
            method = sys.argv[i + 1]
            i += 2
        elif sys.argv[i] == "--scale" and i + 1 < len(sys.argv):
            scale = int(sys.argv[i + 1])
            i += 2
        else:
            i += 1
    
    try:
        process_image(input_path, output_path, method, scale)
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()