#!/usr/bin/env python3
"""Simple baseline upscalers for manga."""

import cv2
import numpy as np
from pathlib import Path


def upscale_bicubic(img: np.ndarray, scale: int = 2) -> np.ndarray:
    """Upscale using bicubic interpolation."""
    h, w = img.shape[:2]
    new_h, new_w = h * scale, w * scale
    return cv2.resize(img, (new_w, new_h), interpolation=cv2.INTER_CUBIC)


def upscale_lanczos(img: np.ndarray, scale: int = 2) -> np.ndarray:
    """Upscale using Lanczos algorithm (similar to bicubic but sharper)."""
    h, w = img.shape[:2]
    new_h, new_w = h * scale, w * scale
    return cv2.resize(img, (new_w, new_h), interpolation=cv2.INTER_LANCZOS4)


def upscale_nn(img: np.ndarray, scale: int = 2) -> np.ndarray:
    """Nearest neighbor - for comparison."""
    h, w = img.shape[:2]
    new_h, new_w = h * scale, w * scale
    return cv2.resize(img, (new_w, new_h), interpolation=cv2.INTER_NEAREST)


# Anime4K-inspired algorithm: simple CNN with few layers
def anime4k_upscale(img: np.ndarray, scale: int = 2, mode: str = "fast") -> np.ndarray:
    """Anime4K-style upscaling.
    
    Args:
        img: Input BGR image
        scale: Scale factor (2)
        mode: "fast" or "quality"
    
    Returns:
        Upscaled image
    """
    # First upscale
    h, w = img.shape[:2]
    new_h, new_w = h * scale, w * scale
    upscaled = cv2.resize(img, (new_w, new_h), interpolation=cv2.INTER_CUBIC)
    
    if mode == "fast":
        # Simple 3x3 convolutions - fast pass
        kernel = np.array([
            [-0.5, 1.0, -0.5],
            [1.0, -5.0, 1.0],
            [-0.5, 1.0, -0.5]
        ], dtype=np.float32) / 4.0
    else:
        # More passes for quality
        kernel = np.array([
            [-0.25, 0.5, -0.25],
            [0.5, -3.0, 0.5],
            [-0.25, 0.5, -0.25]
        ], dtype=np.float32) / 4.0
    
    # Apply kernel
    if len(img.shape) == 3:
        # Color: split channels
        result = np.zeros_like(upscaled)
        for c in range(upscaled.shape[2]):
            filtered = cv2.filter2D(upscaled[:,:,c], -1, kernel)
            # Blend
            result[:,:,c] = np.clip(filtered * 0.1 + upscaled[:,:,c] * 0.9, 0, 255).astype(np.uint8)
    else:
        filtered = cv2.filter2D(upscaled, -1, kernel)
        result = np.clip(filtered * 0.1 + upscaled * 0.9, 0, 255).astype(np.uint8)
    
    return result


def upscale_image(input_path: str, output_path: str, method: str = "bicubic", scale: int = 2, mode: str = "fast"):
    """Upscale single image."""
    img = cv2.imread(input_path)
    if img is None:
        raise ValueError(f"Cannot load: {input_path}")
    
    if method == "bicubic":
        result = upscale_bicubic(img, scale)
    elif method == "lanczos":
        result = upscale_lanczos(img, scale)
    elif method == "nn":
        result = upscale_nn(img, scale)
    elif method == "anime4k":
        result = anime4k_upscale(img, scale, mode)
    else:
        raise ValueError(f"Unknown method: {method}")
    
    cv2.imwrite(output_path, result)
    return result


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 3:
        print("Usage: python -m manga_upscaler.baseline <input.png> <output.png> [method]")
        print("Methods: bicubic, lanczos, nn, anime4k")
        sys.exit(1)
    
    method = sys.argv[2] if len(sys.argv) > 2 else "bicubic"
    upscale_image(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else sys.argv[1].replace(".png", "_upscaled.png"), method)
    print(f"Upscaled {method}: {sys.argv[1]}")