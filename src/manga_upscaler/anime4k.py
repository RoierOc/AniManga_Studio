#!/usr/bin/env python3
"""Anime4K-style upscaling for manga - focuses on line enhancement."""

import cv2
import numpy as np
import time


def sobel_edge(img: np.ndarray) -> np.ndarray:
    """Detect edges using Sobel."""
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if len(img.shape) == 3 else img
    
    sobel_x = cv2.Sobel(gray, cv2.CV_64F, 1, 0, ksize=3)
    sobel_y = cv2.Sobel(gray, cv2.CV_64F, 0, 1, ksize=3)
    
    magnitude = np.sqrt(sobel_x**2 + sobel_y**2)
    magnitude = np.clip(magnitude, 0, 255).astype(np.uint8)
    
    return magnitude


def thin_edges(img: np.ndarray, threshold: int = 30) -> np.ndarray:
    """Thin edges to single pixel lines - key for manga."""
    edges = sobel_edge(img)
    _, binary = cv2.threshold(edges, threshold, 255, cv2.THRESH_BINARY)
    
    # Morphological thinning to get single-pixel lines
    skeleton = np.zeros_like(binary)
    element = cv2.getStructuringElement(cv2.MORPH_CROSS, (3, 3))
    
    temp = binary.copy()
    while True:
        eroded = cv2.erode(temp, element)
        opened = cv2.dilate(eroded, element)
        opened_not = cv2.bitwise_not(opened)
        temp_and = cv2.bitwise_and(temp, opened_not)
        skeleton = cv2.bitwise_or(skeleton, temp_and)
        temp = eroded.copy()
        
        if cv2.countNonZero(temp) == 0:
            break
    
    return skeleton


def upscale_anime4k_lines(img: np.ndarray, scale: int = 2) -> np.ndarray:
    """Anime4K-style: upscale + enhance line art.
    
    This implements the core Anime4K idea:
    1. Upscale with bicubic
    2. Detect edges in original
    3. Restore edges after upscaling
    """
    # First upscale
    h, w = img.shape[:2]
    upscaled = cv2.resize(img, (w * scale, h * scale), interpolation=cv2.INTER_CUBIC)
    
    # Get edges from original (at original resolution)
    gray_orig = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if len(img.shape) == 3 else img
    edges_orig = sobel_edge(gray_orig)
    
    # Upscale edges
    edges_up = cv2.resize(edges_orig, (w * scale, h * scale), interpolation=cv2.INTER_NEAREST)
    
    # Enhance: combine upscaled image with strong edges
    result = upscaled.copy()
    
    # For B&W manga: darken where edges are
    edge_mask = edges_up > 40
    
    # If grayscale manga
    if len(img.shape) == 2 or (len(img.shape) == 3 and img.shape[2] == 1):
        result[edge_mask] = np.clip(result[edge_mask] * 0.7, 0, 255).astype(np.uint8)
    else:
        # Convert to grayscale for masking
        gray_up = cv2.cvtColor(upscaled, cv2.COLOR_BGR2GRAY)
        gray_up[edge_mask] = np.clip(gray_up[edge_mask] * 0.5, 0, 255).astype(np.uint8)
        result = cv2.cvtColor(gray_up, cv2.COLOR_GRAY2BGR)
    
    return result


def upscale_manga_plus(img: np.ndarray, scale: int = 2, iterations: int = 2) -> np.ndarray:
    """Enhanced manga upscaling - multiple passes for better lines."""
    
    h, w = img.shape[:2]
    result = cv2.resize(img, (w * scale, h * scale), interpolation=cv2.INTER_CUBIC)
    
    # Convert to grayscale for processing
    if len(result.shape) == 3:
        gray = cv2.cvtColor(result, cv2.COLOR_BGR2GRAY)
    else:
        gray = result.copy()
    
    # Iterative edge enhancement
    for _ in range(iterations):
        # Detect edges using unsharp mask (Laplacian)
        laplacian = cv2.Laplacian(gray, cv2.CV_64F)
        
        # Enhance edges
        enhanced = gray - laplacian * 0.5
        gray = np.clip(enhanced, 0, 255).astype(np.uint8)
    
    # Denoise with bilateral (preserves edges)
    denoised = cv2.bilateralFilter(gray, 5, 50, 50)
    
    if len(img.shape) == 3:
        result = cv2.cvtColor(denoised, cv2.COLOR_GRAY2BGR)
    else:
        result = denoised
    
    return result


def upscale_manga_sharp(img: np.ndarray, scale: int = 2) -> np.ndarray:
    """Sharp manga upscaling with edge preservation."""
    
    h, w = img.shape[:2]
    
    # Upscale
    result = cv2.resize(img, (w * scale, h * scale), interpolation=cv2.INTER_LANCZOS4)
    
    # Edge-preserving sharpening
    if len(result.shape) == 3:
        for i in range(3):
            result[:,:,i] = cv2.bilateralFilter(result[:,:,i], 3, 30, 30)
    else:
        result = cv2.bilateralFilter(result, 3, 30, 30)
    
    # Add contrast
    if len(result.shape) == 3:
        for i in range(3):
            result[:,:,i] = cv2.equalizeHist(result[:,:,i])
    else:
        result = cv2.equalizeHist(result)
    
    return result.astype(np.uint8)


UPSCALERS = {
    "anime4k": upscale_anime4k_lines,
    "manga+": upscale_manga_plus,
    "manga_sharp": upscale_manga_sharp,
}


def process_image(input_path: str, output_path: str, method: str = "anime4k", scale: int = 2):
    """Process single image."""
    img = cv2.imread(input_path)
    if img is None:
        raise ValueError(f"Cannot load: {input_path}")
    
    print(f"Input: {img.shape[1]}x{img.shape[0]}")
    
    if method not in UPSCALERS:
        print(f"Unknown: {method}")
        method = "anime4k"
    
    func = UPSCALERS[method]
    
    start = time.time()
    result = func(img, scale)
    elapsed = (time.time() - start) * 1000
    
    desc = method
    if method == "anime4k":
        desc = "Anime4K style"
    elif method == "manga+":
        desc = "Manga enhanced"
    elif method == "manga_sharp":
        desc = "Sharp manga"
    
    print(f"Output: {result.shape[1]}x{result.shape[0]}")
    print(f"Time: {elapsed:.1f}ms ({desc})")
    
    cv2.imwrite(output_path, result)
    print(f"Saved: {output_path}")


def benchmark_dir(directory: str, scale: int = 2):
    """Benchmark all methods."""
    import os
    
    files = sorted([f for f in os.listdir(directory) if f.endswith('.png')])[:10]
    
    print(f"\n{'='*50}")
    print(f"Benchmark: {directory}")
    print(f"{'='*50}")
    
    results = {name: [] for name in UPSCALERS}
    
    for fname in files:
        fpath = os.path.join(directory, fname)
        img = cv2.imread(fpath)
        if img is None:
            continue
        
        print(f"\n{fname}:")
        
        for name, func in UPSCALERS.items():
            start = time.time()
            result = func(img.copy(), scale)
            elapsed = (time.time() - start) * 1000
            results[name].append(elapsed)
            print(f"  {name:12}: {elapsed:6.1f}ms")
    
    print(f"\n{'='*50}")
    print("AVG:")
    for name, times in results.items():
        avg = sum(times) / len(times) if times else 0
        print(f"  {name:12}: {avg:6.1f}ms")


if __name__ == "__main__":
    import sys
    
    if len(sys.argv) < 3:
        print("Usage: python manga_upscale.py <input.png> <output.png> [method]")
        print("Methods:", list(UPSCALERS.keys()))
        sys.exit(1)
    
    input_path = sys.argv[1]
    output_path = sys.argv[2]
    method = sys.argv[3] if len(sys.argv) > 3 else "anime4k"
    
    process_image(input_path, output_path, method)