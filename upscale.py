#!/usr/bin/env python3
"""
Manga Upscaler Wrapper for waifu2x-ncnn-vulkan
Usage: python upscale.py input.png [output.png] [options]
"""

import os
import sys
import subprocess
import time
from pathlib import Path

DEFAULT_WAIFU2X = "waifu2x-ncnn-vulkan"


def find_waifu2x():
    """Find waifu2x executable"""
    # Check local directory
    if os.path.exists(DEFAULT_WAIFU2X):
        return DEFAULT_WAIFU2X
    
    # Check common locations
    locations = [
        "/tmp/waifu2x-ncnn-vulkan-20250915-linux/waifu2x-ncnn-vulkan",
        "/root/workspace/waifu2x-ncnn-vulkan/waifu2x-ncnn-vulkan",
        "waifu2x-ncnn-vulkan",
    ]
    
    for loc in locations:
        if os.path.exists(loc):
            return loc
    
    return None


def upscale(input_file, output_file=None, scale=2, noise=0, model="models-cunet", gpu=0, tilesize=0):
    """Run waifu2x-ncnn-vulkan"""
    waifu_path = find_waifu2x()
    
    if not waifu_path:
        raise FileNotFoundError("waifu2x-ncnn-vulkan not found! Download from: https://github.com/nihui/waifu2x-ncnn-vulkan/releases")
    
    # Auto-set output
    if not output_file:
        input_path = Path(input_file)
        output_file = f"{input_path.stem}_upscaled{input_path.suffix}"
    
    print(f"Input: {input_file}")
    print(f"Output: {output_file}")
    print(f"Scale: {scale}x, Noise: {noise}, Model: {model}, GPU: {gpu}")
    
    # Build command
    cmd = [
        waifu_path,
        "-i", input_file,
        "-o", output_file,
        "-s", str(scale),
        "-n", str(noise),
        "-m", model,
        "-g", str(gpu),
        "-t", str(tilesize),
        "-f", "png",
    ]
    
    # Run
    start = time.time()
    result = subprocess.run(cmd, capture_output=True, text=True)
    elapsed = time.time() - start
    
    if result.returncode != 0:
        print(f"Error: {result.stderr}")
        return False
    
    print(f"Done! Time: {elapsed:.2f}s")
    return True


def main():
    if len(sys.argv) < 2:
        print("Manga Upscaler - waifu2x-ncnn-vulkan")
        print(f"Usage: {sys.argv[0]} input.png [output.png] [options]")
        print("")
        print("Options:")
        print("  --scale N     Scale factor (default: 2)")
        print("  --noise N     Noise reduction (0-3, default: 0)")
        print("  --model NAME  Model name (default: models-cunet)")
        print("               Other: models-upconv_7_anime_style_art_rgb, models-upconv_7_photo")
        print("  --gpu N       GPU device (default: 0)")
        print("  --tile N      Tile size (default: 0=auto)")
        print("")
        print("Examples:")
        print(f"  {sys.argv[0]} manga.png              # 2x upscale")
        print(f"  {sys.argv[0]} manga.png out.png       # Custom output")
        print(f"  {sys.argv[0]} manga.png --noise 1     # With denoising")
        print(f"  {sys.argv[0]} manga.png -m models-upconv_7_anime_style_art_rgb  # Anime model")
        sys.exit(1)
    
    input_file = sys.argv[1]
    output_file = None
    
    # Parse arguments
    scale = 2
    noise = 0
    model = "models-cunet"
    gpu = 0
    tilesize = 0
    
    i = 2
    while i < len(sys.argv):
        arg = sys.argv[i]
        if arg == "-o" or arg == "--output":
            output_file = sys.argv[i+1]
            i += 2
        elif arg == "-s" or arg == "--scale":
            scale = int(sys.argv[i+1])
            i += 2
        elif arg == "-n" or arg == "--noise":
            noise = int(sys.argv[i+1])
            i += 2
        elif arg == "-m" or arg == "--model":
            model = sys.argv[i+1]
            i += 2
        elif arg == "-g" or arg == "--gpu":
            gpu = int(sys.argv[i+1])
            i += 2
        elif arg == "-t" or arg == "--tile":
            tilesize = int(sys.argv[i+1])
            i += 2
        elif not arg.startswith("-"):
            output_file = arg
            i += 1
        else:
            i += 1
    
    upscale(input_file, output_file, scale, noise, model, gpu, tilesize)


if __name__ == "__main__":
    main()