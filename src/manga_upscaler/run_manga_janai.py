#!/usr/bin/env python3
"""ESRGAN upscaler for MangaJaNai model."""

import torch
import torch.nn as nn
import torch.nn.functional as F
import cv2
import numpy as np
import time
from functools import partial


def make_model():
    class ResBlock(nn.Module):
        def __init__(self, channels):
            super().__init__()
            self.conv1 = nn.Conv2d(channels, channels, 3, padding=1)
            self.conv2 = nn.Conv2d(channels, channels, 3, padding=1)
            self.relu = nn.LeakyReLU(0.2, True)
        
        def forward(self, x):
            return self.relu(self.conv2(self.conv1(x))) + x
    
    class ESRGAN(nn.Module):
        def __init__(self, in_ch=3, out_ch=3, base_ch=64, num_blocks=23):
            super().__init__()
            self.first = nn.Conv2d(in_ch, base_ch, 3, padding=1)
            
            self.body = nn.Sequential(*[
                ResBlock(base_ch) for _ in range(num_blocks)
            ])
            
            self.upconv = nn.Conv2d(base_ch, base_ch * 4, 3, padding=1)
            self.pixel_shuffle = nn.PixelShuffle(2)
            self.activation = nn.LeakyReLU(0.2, True)
            
            self.last = nn.Conv2d(base_ch, out_ch, 3, padding=1)
        
        def forward(self, x):
            x = self.first(x)
            x = x + self.body(x)
            x = self.activation(self.pixel_shuffle(self.upconv(x)))
            x = self.last(x)
            return x
    
    return ESRGAN()


def load_gan(model_path):
    """Load MangaJaNai model."""
    state_dict = torch.load(model_path, map_location='cpu')
    
    # Create model and load
    model = make_model()
    
    # Reload state dict
    new_state_dict = {}
    for k, v in state_dict.items():
        if k.startswith('model.'):
            new_state_dict[k[6:]] = v
        else:
            new_state_dict[k] = v
    
    model.load_state_dict(new_state_dict, strict=False)
    model.eval()
    
    return model


def to_tensor(img):
    """Image to tensor."""
    img = img.astype(np.float32) / 255.0
    if img.ndim == 2:
        img = np.stack([img] * 3, axis=-1)
    elif img.shape[2] == 4:
        img = img[:, :, :3]
    return torch.from_numpy(img).permute(2, 0, 1).unsqueeze(0)


def to_image(tensor):
    """Tensor to image."""
    img = tensor.squeeze(0).permute(1, 2, 0).numpy()
    return (img * 255.0).clip(0, 255).astype(np.uint8)


def upscale(model_path, input_path, output_path):
    """Upscale with MangaJaNai."""
    print(f"Model: {model_path}")
    model = load_gan(model_path)
    
    print(f"Image: {input_path}")
    img = cv2.imread(input_path)
    h, w = img.shape[:2]
    
    print(f"Input: {w}x{h}")
    
    with torch.no_grad():
        tensor = to_tensor(img)
        start = time.time()
        result = model(tensor)
        elapsed = (time.time() - start) * 1000
    
    print(f"Output: {result.shape[2]}x{result.shape[1]}")
    print(f"Time: {elapsed:.1f}ms")
    
    output = to_image(result)
    cv2.imwrite(output_path, output)
    print(f"Saved: {output_path}")


if __name__ == "__main__":
    import sys
    
    if len(sys.argv) < 4:
        print("Usage: python run_manga_janai.py <model.pth> <input.png> <output.png>")
        sys.exit(1)
    
    upscale(sys.argv[1], sys.argv[2], sys.argv[3])