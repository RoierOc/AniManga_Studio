#!/usr/bin/env python3
"""MangaJaNai ESRGAN upscaler - proper architecture."""

import torch
import torch.nn as nn
import torch.nn.functional as F
import cv2
import numpy as np
import time


class DenseLayer(nn.Module):
    def __init__(self, in_ch, out_ch):
        super().__init__()
        self.conv = nn.Conv2d(in_ch, out_ch, 3, padding=1)
        self.relu = nn.LeakyReLU(0.2, True)
    
    def forward(self, x):
        return self.relu(self.conv(x))


class RRDB(nn.Module):
    """Residual in Residual Dense Block"""
    def __init__(self, channels, grow_ch=32):
        super().__init__()
        self.conv1 = DenseLayer(channels, grow_ch)
        self.conv2 = DenseLayer(channels + grow_ch, grow_ch)
        self.conv3 = DenseLayer(channels + grow_ch*2, grow_ch)
        self.conv4 = DenseLayer(channels + grow_ch*3, channels)
    
    def forward(self, x):
        x1 = self.conv1(x)
        x2 = self.conv2(torch.cat([x, x1], 1))
        x3 = self.conv3(torch.cat([x, x1, x2], 1))
        x4 = self.conv4(torch.cat([x, x1, x2, x3], 1))
        return x4 * 0.2 + x


class RRDN(nn.Module):
    """Residual in Residual Dense Network"""
    def __init__(self, in_ch=3, out_ch=3, base_ch=64, num_blocks=8, grow_ch=32):
        super().__init__()
        
        # First conv
        self.first = nn.Conv2d(in_ch, base_ch, 3, padding=1)
        
        # Body with RRDB blocks
        self.body = nn.Sequential(*[
            RRDB(base_ch, grow_ch) for _ in range(num_blocks)
        ])
        
        # Body conv to reduce features
        self.body_conv = nn.Conv2d(base_ch, base_ch, 3, padding=1)
        
        # Upsample
        self.upsampler = nn.Sequential(
            nn.Conv2d(base_ch, base_ch * 4, 3, padding=1),
            nn.PixelShuffle(2),
            nn.LeakyReLU(0.2, True)
        )
        
        # Output
        self.last = nn.Conv2d(base_ch, out_ch, 3, padding=1)
    
    def forward(self, x):
        feat = self.first(x)
        body = self.body(feat)
        body = self.body_conv(body) + feat
        body = self.upsampler(body)
        return self.last(body)


def load_rrdn(model_path):
    """Load RRDN model."""
    state_dict = torch.load(model_path, map_location='cpu')
    
    # Extract actual model keys
    new_state = {k.replace('model.', ''): v for k, v in state_dict.items()}
    
    # Create and load
    model = RRDN()
    
    # Try loading with partial matching
    model_dict = model.state_dict()
    filtered_dict = {}
    
    for k, v in new_state.items():
        if k in model_dict:
            if v.shape == model_dict[k].shape:
                filtered_dict[k] = v
            else:
                print(f"Shape mismatch {k}: {v.shape} vs {model_dict[k].shape}")
        else:
            filtered_dict[k] = v
    
    model.load_state_dict(filtered_dict, strict=False)
    model.eval()
    
    return model


def to_tensor(img):
    img = img.astype(np.float32) / 255.0
    if img.ndim == 2:
        img = np.stack([img] * 3, axis=-1)
    elif img.shape[2] == 4:
        img = img[:, :, :3]
    return torch.from_numpy(img).permute(2, 0, 1).unsqueeze(0)


def to_image(tensor):
    img = tensor.squeeze(0).permute(1, 2, 0).numpy()
    return (img * 255.0).clip(0, 255).astype(np.uint8)


def upscale(model_path, input_path, output_path):
    print(f"Model: {model_path}")
    model = load_rrdn(model_path)
    
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
    
    cv2.imwrite(output_path, to_image(result))
    print(f"Saved: {output_path}")


if __name__ == "__main__":
    import sys
    upscale(sys.argv[1], sys.argv[2], sys.argv[3])