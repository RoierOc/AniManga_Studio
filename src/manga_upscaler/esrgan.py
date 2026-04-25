#!/usr/bin/env python3
"""Simple ESRGAN upscaler using PyTorch directly."""

import torch
import torch.nn as nn
import cv2
import numpy as np
import time
import os
from pathlib import Path


class ESRGAN(nn.Module):
    def __init__(self, in_channels=3, out_channels=3, num_channels=64, num_blocks=23):
        super().__init__()
        
        self.conv_first = nn.Conv2d(in_channels, num_channels, 3, padding=1)
        
        self.body = nn.Sequential(*[
            self._make_block(num_channels) for _ in range(num_blocks)
        ])
        
        self.conv_up = nn.Sequential(
            nn.Conv2d(num_channels, num_channels * 4, 3, padding=1),
            nn.PixelShuffle(2),
            nn.LeakyReLU(0.2, True)
        )
        
        self.conv_last = nn.Conv2d(num_channels, out_channels, 3, padding=1)
    
    def _make_block(self, channels):
        return nn.Sequential(
            nn.Conv2d(channels, channels, 3, padding=1),
            nn.LeakyReLU(0.2, True)
        )
    
    def forward(self, x):
        feat = self.conv_first(x)
        body = self.body(feat)
        out = self.conv_up(body + feat)
        out = self.conv_last(out)
        return out


def load_model(model_path):
    """Load ESRGAN model from checkpoint."""
    checkpoint = torch.load(model_path, map_location='cpu', weights_only=False)
    
    if isinstance(checkpoint, dict) and 'state_dict' in checkpoint:
        state_dict = checkpoint['state_dict']
    else:
        state_dict = checkpoint
    
    # Clean state dict keys
    new_state_dict = {}
    for k, v in state_dict.items():
        if k.startswith('module.'):
            k = k[7:]
        new_state_dict[k] = v
    
    # Create model and load weights
    model = ESRGAN()
    model.load_state_dict(new_state_dict, strict=False)
    model.eval()
    
    return model


def preprocess(img):
    """Convert image to tensor."""
    img = img.astype(np.float32) / 255.0
    if len(img.shape) == 2:
        img = np.stack([img] * 3, axis=-1)
    elif img.shape[2] == 4:
        img = img[:, :, :3]
    
    img = torch.from_numpy(img).permute(2, 0, 1).unsqueeze(0)
    return img


def postprocess(tensor):
    """Convert tensor to image."""
    img = tensor.squeeze(0).permute(1, 2, 0).numpy()
    img = (img * 255.0).clip(0, 255).astype(np.uint8)
    return img


def upscale_esrgan(model_path, input_path, output_path, scale=4):
    """Upscale with ESRGAN model."""
    print(f"Loading model: {model_path}")
    model = load_model(model_path)
    
    print(f"Loading image: {input_path}")
    img = cv2.imread(input_path, cv2.IMREAD_COLOR)
    
    h, w = img.shape[:2]
    new_h, new_w = h * scale, w * scale
    
    print(f"Processing: {w}x{h} -> {new_w}x{new_h}")
    
    with torch.no_grad():
        tensor = preprocess(img)
        start = time.time()
        result_tensor = model(tensor)
        elapsed = (time.time() - start) * 1000
    
    result = postprocess(result_tensor)
    
    print(f"Time: {elapsed:.1f}ms")
    cv2.imwrite(output_path, result)
    print(f"Saved: {output_path}")


if __name__ == "__main__":
    import sys
    
    if len(sys.argv) < 4:
        print("Usage: python esrgan.py <model.pth> <input.png> <output.png>")
        sys.exit(1)
    
    upscale_esrgan(sys.argv[1], sys.argv[2], sys.argv[3])