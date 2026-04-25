#!/usr/bin/env python3
"""Real-ESRGAN PyTorch implementation - works with official models."""

import torch
import torch.nn as nn
import torch.nn.functional as F
import cv2
import numpy as np
import time


class DenseLayer(nn.Module):
    def __init__(self, in_ch, grow_ch):
        super().__init__()
        self.conv = nn.Conv2d(in_ch, grow_ch, 3, 1, 1)
        self.act = nn.LeakyReLU(0.2, True)
    
    def forward(self, x):
        return self.act(self.conv(x))


class ResidualDenseBlock(nn.Module):
    def __init__(self, num_feat=64, num_grow_ch=32):
        super().__init__()
        self.conv1 = DenseLayer(num_feat, num_grow_ch)
        self.conv2 = DenseLayer(num_feat + num_grow_ch, num_grow_ch)
        self.conv3 = DenseLayer(num_feat + num_grow_ch*2, num_grow_ch)
        self.conv4 = DenseLayer(num_feat + num_grow_ch*3, num_grow_ch)
        self.conv5 = nn.Conv2d(num_feat + num_grow_ch*4, num_feat, 3, 1, 1)
    
    def forward(self, x):
        x1 = self.conv1(x)
        x2 = self.conv2(torch.cat([x, x1], 1))
        x3 = self.conv3(torch.cat([x, x1, x2], 1))
        x4 = self.conv4(torch.cat([x, x1, x2, x3], 1))
        x5 = self.conv5(torch.cat([x, x1, x2, x3, x4], 1))
        return x5 * 0.2 + x


class RRDB(nn.Module):
    def __init__(self, num_feat, num_grow_ch=32):
        super().__init__()
        self.rdb1 = ResidualDenseBlock(num_feat, num_grow_ch)
        self.rdb2 = ResidualDenseBlock(num_feat, num_grow_ch)
        self.rdb3 = ResidualDenseBlock(num_feat, num_grow_ch)
    
    def forward(self, x):
        out = self.rdb1(x)
        out = self.rdb2(out)
        out = self.rdb3(out)
        return out * 0.2 + x


class RRDBNet(nn.Module):
    def __init__(self, num_in_ch=3, num_out_ch=3, scale=4, num_feat=64, num_block=23, num_grow_ch=32):
        super().__init__()
        self.scale = scale
        
        # First conv
        self.conv_first = nn.Conv2d(num_in_ch, num_feat, 3, 1, 1)
        
        # Body - RRDB blocks
        self.body = nn.Sequential(*[RRDB(num_feat, num_grow_ch) for _ in range(num_block)])
        
        # Body conv
        self.conv_body = nn.Conv2d(num_feat, num_feat, 3, 1, 1)
        
        # Upsample
        self.conv_up = nn.Conv2d(num_feat, num_feat * 4, 3, 1, 1)
        self.pixel_shuffle = nn.PixelShuffle(2)
        self.act = nn.LeakyReLU(0.2, True)
        
        # Output
        self.conv_last = nn.Conv2d(num_feat, num_feat, 3, 1, 1)
        self.conv_final = nn.Conv2d(num_feat, num_out_ch, 3, 1, 1)
    
    def forward(self, x):
        feat = self.conv_first(x)
        body_feat = self.body(feat)
        body_feat = self.conv_body(body_feat) + feat
        body_feat = self.conv_up(body_feat)
        body_feat = self.act(self.pixel_shuffle(body_feat))
        body_feat = self.conv_last(body_feat)
        out = self.conv_final(body_feat)
        return out


def to_tensor(img):
    # OpenCV loads as BGR, convert to RGB for the model
    img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
    img = img.astype(np.float32) / 255.0
    if img.ndim == 2:
        img = np.stack([img] * 3, axis=-1)
    return torch.from_numpy(img).permute(2, 0, 1).unsqueeze(0)


def to_image(tensor):
    # Model outputs RGB, convert back to BGR for OpenCV
    img = tensor.squeeze(0).permute(1, 2, 0).numpy()
    img = (img * 255.0).clip(0, 255).astype(np.uint8)
    img = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
    return img


def load_model(model_path):
    print(f"Loading: {model_path}")
    state = torch.load(model_path, map_location='cpu', weights_only=False)
    
    # Handle different key formats
    new_state = {}
    for k, v in state.items():
        if k.startswith('params_ema.'):
            k = k[10:]  # Remove params_ema.
        elif k.startswith('net_g.'):
            k = k[6:]  # Remove net_g.
        new_state[k] = v
    
    # Create model
    model = RRDBNet(num_in_ch=3, num_out_ch=3, scale=4, num_feat=64, num_block=23)
    
    # Load with filter
    model_dict = model.state_dict()
    filtered = {}
    for k, v in new_state.items():
        if k in model_dict:
            if v.shape == model_dict[k].shape:
                filtered[k] = v
    
    model.load_state_dict(filtered, strict=False)
    model.eval()
    return model


def upscale(model_path, input_path, output_path):
    print(f"\n=== Real-ESRGAN Upscaler ===")
    model = load_model(model_path)
    
    img = cv2.imread(input_path)
    h, w = img.shape[:2]
    print(f"Input: {w}x{h}")
    
    with torch.no_grad():
        tensor = to_tensor(img)
        start = time.time()
        result = model(tensor)
        elapsed = (time.time() - start) * 1000
    
    print(f"Output: {result.shape[3]}x{result.shape[2]}")
    print(f"Time: {elapsed:.1f}ms")
    
    cv2.imwrite(output_path, to_image(result))
    print(f"Saved: {output_path}")


if __name__ == "__main__":
    import sys
    upscale(sys.argv[1], sys.argv[2], sys.argv[3])