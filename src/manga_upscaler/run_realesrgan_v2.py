#!/usr/bin/env python3
"""Real-ESRGAN with proper RGBA/PNG support using Pillow."""

import torch
import torch.nn as nn
import torch.nn.functional as F
from PIL import Image
import numpy as np
import time
import io


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
    def __init__(self, num_in_ch=3, num_out_ch=3, num_feat=64, num_block=23):
        super().__init__()
        self.conv_first = nn.Conv2d(num_in_ch, num_feat, 3, 1, 1)
        self.body = nn.Sequential(*[RRDB(num_feat) for _ in range(num_block)])
        self.conv_body = nn.Conv2d(num_feat, num_feat, 3, 1, 1)
        self.conv_up = nn.Conv2d(num_feat, num_feat * 4, 3, 1, 1)
        self.pixel_shuffle = nn.PixelShuffle(2)
        self.act = nn.LeakyReLU(0.2, True)
        self.conv_last = nn.Conv2d(num_feat, num_feat, 3, 1, 1)
        self.conv_final = nn.Conv2d(num_feat, num_out_ch, 3, 1, 1)
    def forward(self, x):
        feat = self.conv_first(x)
        body_feat = self.body(feat)
        body_feat = self.conv_body(body_feat) + feat
        body_feat = self.conv_up(body_feat)
        body_feat = self.act(self.pixel_shuffle(body_feat))
        body_feat = self.conv_last(body_feat)
        return self.conv_final(body_feat)


def load_model(model_path):
    print(f"Loading: {model_path}")
    state = torch.load(model_path, map_location='cpu', weights_only=False)
    
    # Extract weights from params_ema
    if isinstance(state, dict) and 'params_ema' in state:
        state = state['params_ema']
    
    new_state = {}
    for k, v in state.items():
        if k.startswith('params_ema.'):
            k = k[10:]
        elif k.startswith('net_g.'):
            k = k[6:]
        new_state[k] = v
    
    model = RRDBNet()
    model_dict = model.state_dict()
    filtered = {k: v for k, v in new_state.items() if k in model_dict and v.shape == model_dict[k].shape}
    model.load_state_dict(filtered, strict=False)
    model.eval()
    return model


def upscale(model_path, input_path, output_path):
    print(f"\n=== Real-ESRGAN ===")
    model = load_model(model_path)
    
    # Load with Pillow (handles RGBA correctly)
    img = Image.open(input_path)
    print(f"Mode: {img.mode}, Size: {img.size}")
    
    # Convert to RGB if RGBA
    if img.mode == 'RGBA':
        # Create white background
        background = Image.new('RGB', img.size, (255, 255, 255))
        background.paste(img, mask=img.split()[3])
        img = background
    elif img.mode != 'RGB':
        img = img.convert('RGB')
    
    # To numpy
    img_np = np.array(img).astype(np.float32) / 255.0
    tensor = torch.from_numpy(img_np).permute(2, 0, 1).unsqueeze(0)
    
    h, w = img.size
    print(f"Input: {w}x{h}")
    
    with torch.no_grad():
        start = time.time()
        result = model(tensor)
        elapsed = (time.time() - start) * 1000
    
    # Back to PIL
    result_np = result.squeeze(0).permute(1, 2, 0).numpy()
    result_np = (result_np * 255.0).clip(0, 255).astype(np.uint8)
    result_img = Image.fromarray(result_np)
    
    print(f"Output: {result_img.size[0]}x{result_img.size[1]}")
    print(f"Time: {elapsed:.1f}ms")
    
    result_img.save(output_path)
    print(f"Saved: {output_path}")


if __name__ == "__main__":
    import sys
    upscale(sys.argv[1], sys.argv[2], sys.argv[3])