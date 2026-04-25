#!/usr/bin/env python3
"""Waifu2x implementation for manga upscaling."""

import cv2
import numpy as np
from pathlib import Path


class Waifu2x:
    """Waifu2x upscaler using SRMD model."""
    
    SCALES = [2]
    
    def __init__(self, scale: int = 2, noise: int = 1):
        if scale not in self.SCALES:
            raise ValueError(f"Scale {scale} not supported. Use {self.SCALES}")
        if not 0 <= noise <= 3:
            raise ValueError("Noise must be 0-3")
        
        self.scale = scale
        self.noise = noise
        self._model = None
        self._model_path = None
    
    def _load_model(self):
        """Load SRMD model for super-resolution."""
        # Note: In production, download models from:
        # https://github.com/nihui/waifu2x-caffe/releases
        # Models: manga-xs, manga, anime-xs, anime
        pass
    
    def upscale(self, img: np.ndarray) -> np.ndarray:
        """Upscale image using Waifu2x.
        
        Args:
            img: Input image as numpy array (H, W, C) in BGR format
            
        Returns:
            Upscaled image
        """
        if self._model is None:
            # Fallback to bicubic if no model
            h, w = img.shape[:2]
            new_h, new_w = h * self.scale, w * self.scale
            return cv2.resize(img, (new_w, new_h), interpolation=cv2.INTER_CUBIC)
        
        # Apply model (if loaded)
        return self._apply_model(img)
    
    def _apply_model(self, img: np.ndarray) -> np.ndarray:
        """Apply SRMD model."""
        # TODO: Implement model inference
        raise NotImplementedError("Model not loaded")


def upscale_image(input_path: str, output_path: str, scale: int = 2, noise: int = 1):
    """Upscale a single image."""
    img = cv2.imread(input_path)
    if img is None:
        raise ValueError(f"Cannot load image: {input_path}")
    
    waifu = Waifu2x(scale=scale, noise=noise)
    result = waifu.upscale(img)
    
    cv2.imwrite(output_path, result)
    return result


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 3:
        print("Usage: python -m manga_upscaler.waifu2x <input.png> <output.png>")
        sys.exit(1)
    
    upscale_image(sys.argv[1], sys.argv[2])
    print(f"Upscaled: {sys.argv[1]} -> {sys.argv[2]}")