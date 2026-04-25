#!/usr/bin/env python3
"""
Simple Manga Pipeline CLI
"""

import os
import sys
import argparse
import requests
import time
from pathlib import Path

MANGA_DIR = os.path.expanduser("~/MangaLibrary")
UPSCALED_DIR = os.path.expanduser("~/MangaLibrary_Upscaled")

def cmd_list(args):
    """List manga library"""
    print(f"\n📚 Library: {MANGA_DIR}")
    print("="*50)
    
    if not Path(MANGA_DIR).exists():
        print("Library empty - no folders yet")
        return
    
    folders = sorted([f for f in Path(MANGA_DIR).iterdir() if f.is_dir()])
    if not folders:
        print("No manga found")
        return
    
    for f in folders:
        images = list(f.glob('*.png')) + list(f.glob('*.jpg'))
        print(f"  📖 {f.name} ({len(images)} pages)")
    
    print(f"\nTotal: {len(folders)} manga")

def cmd_add(args):
    """Add manga (create folder)"""
    name = args.name
    
    folder = Path(MANGA_DIR) / name
    folder.mkdir(parents=True, exist_ok=True)
    print(f"✅ Added: {name}")
    print(f"   Path: {folder}")

def cmd_download(args):
    """Download manga from MangaDex"""
    import requests
    import time
    
    title = args.name
    max_chapters = args.chapters
    
    print(f"📥 Downloading: {title}")
    
    # Search
    url = "https://api.mangadex.org/manga"
    params = {"title": title, "limit": 3}
    r = requests.get(url, params=params)
    data = r.json()
    
    if not data.get('data'):
        print("❌ Manga not found")
        return
    
    m = data['data'][0]
    manga_id = m['id']
    manga_title = m['attributes']['title'].get('en') or list(m['attributes']['title'].values())[0]
    
    folder = Path(MANGA_DIR) / manga_title.replace('/', '-')[:50]
    folder.mkdir(parents=True, exist_ok=True)
    
    print(f"   Found: {manga_title}")
    print(f"   Folder: {folder}")
    
    # Get chapters
    url = f"https://api.mangadex.org/manga/{manga_id}/feed"
    all_chapters = []
    offset = 0
    
    while len(all_chapters) < 500:
        params = {"limit": 100, "offset": offset}
        r = requests.get(url, params=params)
        items = r.json().get('data', [])
        if not items:
            break
        all_chapters.extend(items)
        offset += 100
        if len(items) < 100:
            break
    
    # Filter English
    chapters = {}
    for item in all_chapters:
        ch = item['attributes']
        num = ch.get('chapter', '0')
        if 'en' in ch.get('translatedLanguage', []) and num not in chapters:
            chapters[num] = item['id']
    
    chapters = sorted(chapters.items(), key=lambda x: float(x[0]) if x[0].isdigit() else 0, reverse=True)
    print(f"   {len(chapters)} English chapters")
    
    if max_chapters:
        chapters = chapters[:max_chapters]
    
    # Download
    for i, (ch_num, ch_id) in enumerate(chapters, 1):
        print(f"   [{i}/{len(chapters)}] Ch{ch_num}...", end=" ", flush=True)
        
        r = requests.get(f"https://api.mangadex.org/at-home/server/{ch_id}")
        data = r.json()
        
        base = data['baseUrl']
        hash_val = data['chapter']['hash']
        pages = data['chapter']['data']
        
        for j, page in enumerate(pages, 1):
            img_url = f"{base}/data/{hash_val}/{page}"
            time.sleep(0.25)
            r = requests.get(img_url)
            if r.status_code == 200:
                ext = page.split('.')[-1]
                try:
                    cnum = int(float(ch_num))
                except:
                    cnum = 0
                filename = f"ch{cnum:04d}_{j:03d}.{ext}"
                with open(folder / filename, 'wb') as f:
                    f.write(r.content)
        
        print(f"{len(pages)} pages")
    
    print(f"\n✅ Complete! {len(chapters)} chapters → {folder}")

def cmd_upscale(args):
    """Upscale manga with 4x"""
    import torch
    from spandrel import ModelLoader
    from PIL import Image
    import numpy as np
    import time
    
    name = args.name
    model_path = args.model
    limit = args.limit if args.limit else 99999
    
    input_folder = Path(MANGA_DIR) / name
    output_folder = Path(UPSCALED_DIR) / name
    
    if not input_folder.exists():
        print(f"❌ Manga not found: {name}")
        return
    
    output_folder.mkdir(parents=True, exist_ok=True)
    
    # Get all images
    all_images = sorted(input_folder.glob('*.png')) + sorted(input_folder.glob('*.jpg'))
    total_all = len(all_images)
    
    if total_all == 0:
        print("❌ No images found")
        return
    
    # Apply limit
    images = all_images[:limit]
    total = len(images)
    
    # Group by chapter
    chapters = {}
    for img in images:
        parts = img.stem.split('_')
        if parts[0].startswith('ch'):
            ch = parts[0][2:]
            if ch not in chapters:
                chapters[ch] = []
            chapters[ch].append(img)
    
    chapter_list = sorted(chapters.items(), key=lambda x: float(x[0]) if x[0].isdigit() else 0, reverse=True)
    num_chapters = len(chapter_list)
    
    # Estimate time (~0.6s with 2x)
    est_per_image = 0.4 if args.half else 0.6
    est_time = (total * est_per_image) / 60
    
    model_name = model_path.split('/')[-1]
    
    # === HEADER ===
    print(f"""
╔══════════════════════════════════════════════════════════╗
║          🔥 UPSCALE: {name:^32}║
╠══════════════════════════════════════════════════════════╣
║ 📦 Input:  {str(input_folder):^42}║
║ 📦 Output: {str(output_folder):^42}║
║ 📊 Images: {total} images ({num_chapters} chapters){' '*16}║
║ ⚡ Mode:   {'FP16' if args.half else 'FP32':^42}║
║ 🖼️ Format: {args.format.upper()} @ Q{args.quality}{'^'*29}║
║ 💾 Model:  {model_name:^42}║
║ ⏱️ Est:    ~{est_time:.1f} min ({est_per_image:.1f}s/image){' '*17}║
╚══════════════════════════════════════════════════════════╝
""")
    
    # Optimize CUDA
    torch.backends.cudnn.benchmark = True
    torch.cuda.empty_cache()
    
    # Pre-load model with FP16 (default)
    
    # Load model with optional FP16
    model_desc = ModelLoader().load_from_file(model_path)
    model = model_desc.model.cuda().eval()
    
    if args.half:
        model = model.half()
    
    # Quick warmup
    dummy = torch.randn(1, 3, 256, 256).cuda()
    if args.half:
        dummy = dummy.half()
    with torch.no_grad():
        _ = model(dummy)
    torch.cuda.synchronize()
    del dummy
    
    if args.half:
        print(f"⚡ FP16 ready\n")
    
    vram = torch.cuda.memory_allocated() / 1024**3
    print(f"💾 VRAM loaded: {vram:.1f} GB\n")
    
    start_time = time.time()
    success = 0
    failed = 0
    total_processed = 0
    
    # === PROCESS ===
    for ch_num, ch_images in chapter_list:
        ch_size = len(ch_images)
        print(f"📖 Chapter {ch_num} ({ch_size} imgs)")
        
        for i, img_path in enumerate(ch_images, 1):
            img_start = time.time()
            
            try:
                img = Image.open(img_path).convert('RGB')
                img_np = np.array(img).astype(np.float32) / 255.0
                img_tensor = torch.from_numpy(img_np).permute(2, 0, 1).unsqueeze(0).cuda()
                
                if args.half:
                    img_tensor = img_tensor.half()
                
                with torch.no_grad():
                    out = model(img_tensor)
                
                out_np = (out.squeeze(0).permute(1, 2, 0).cpu().numpy() * 255).clip(0, 255).astype(np.uint8)
                out_img = Image.fromarray(out_np)
                
                # Get extension based on format
                ext = args.format
                if ext == 'jpg':
                    ext = 'jpg'
                elif ext == 'webp':
                    ext = 'webp'
                
                new_name = img_path.stem + '.' + ext
                out_path = output_folder / new_name
                
                # Debug: print actual path
                print(f"       Saving to: {out_path}")
                
                if args.format == 'jpg':
                    out_img.save(out_path, quality=args.quality, optimize=True)
                elif args.format == 'webp':
                    out_img.save(out_path, quality=args.quality)
                else:
                    out_img.save(out_path, quality=args.quality)
                
                elapsed = time.time() - img_start
                pct = i / ch_size * 100
                bar = '█' * int(pct / 5) + '░' * (20 - int(pct / 5))
                
                print(f"   [{i:02d}/{ch_size}] {bar} {pct:3.0f}% {img_path.name:30s} - {elapsed:.1f}s ✓")
                success += 1
                
            except Exception as e:
                print(f"   [{i:02d}/{ch_size}] ✗ ERROR - {img_path.name}: {e}")
                failed += 1
            
            total_processed += 1
        
        # VRAM between chapters
        vram = torch.cuda.memory_allocated() / 1024**3
        print(f"[VRAM: {vram:.1f}GB ↓]\n")
        
        torch.cuda.empty_cache()
    
    total_time = time.time() - start_time
    
    # === FOOTER ===
    avg_time = total_time / total if total > 0 else 0
    print(f"""
╔══════════════════════════════════════════════════════════╗
║                 ✅ DONE!                        ║
╠═════════════════════════════════��════════════════════════╣
║ ⏱️ Time:  {total_time/60:.1f} min ({total_time:.1f}s total){' '*19}║
║ 📊 Images: {total_processed} images processed{' '*23}║
║ ⏱️ Avg:   {avg_time:.1f}s per image{' '*28}║
║ ✓ OK:     {success:^37}{' '*33}║
║ ✗ Failed: {failed:^37}{' '*33}║
║ 💾 Saved: {str(output_folder):^43}║
╚══════════════════════════════════════════════════════════╝
""")

def main():
    parser = argparse.ArgumentParser(description='Manga Pipeline CLI')
    subparsers = parser.add_subparsers(dest='command', help='Commands')
    
    # list
    subparsers.add_parser('list', help='List library')
    
    # add
    add_parser = subparsers.add_parser('add', help='Add manga by name')
    add_parser.add_argument('name', help='Manga name')
    
    # download
    dl_parser = subparsers.add_parser('download', help='Download manga')
    dl_parser.add_argument('name', help='Manga title or URL')
    dl_parser.add_argument('--chapters', type=int, help='Max chapters')
    
    # upscale
    up_parser = subparsers.add_parser('upscale', help='Upscale manga')
    up_parser.add_argument('name', help='Manga name')
    up_parser.add_argument('--model', default='/root/workspace/MangaJaNai/models/2x_IllustrationJaNai_V2standard_FDAT_M_unshuffle_40k.safetensors')
    up_parser.add_argument('--limit', type=int, help='Max images to process')
    up_parser.add_argument('--half', action='store_true', help='FP16 (half precision, less VRAM)')
    up_parser.add_argument('--tile', type=int, help='Tile size for large images (0=auto)')
    up_parser.add_argument('--quality', type=int, default=95, help='JPEG quality (0-100, default: 95)')
    up_parser.add_argument('--format', default='png', choices=['png', 'jpg', 'webp'], help='Output format')
    
    args = parser.parse_args()
    
    if not args.command:
        print("Manga Pipeline CLI")
        print("="*50)
        print("Usage:")
        print("  python manga_cli.py list              # Show library")
        print("  python manga_cli.py add <name>        # Add manga folder")
        print("  python manga_cli.py download <name>    # Download instructions")
        print("  python manga_cli.py upscale <name>      # Upscale to 4x")
        print()
        print("Examples:")
        print("  python manga_cli.py list")
        print("  python manga_cli.py add one-piece")
        print("  python manga_cli.py upscale one-piece")
        return
    
    if args.command == 'list':
        cmd_list(args)
    elif args.command == 'add':
        cmd_add(args)
    elif args.command == 'download':
        cmd_download(args)
    elif args.command == 'upscale':
        cmd_upscale(args)

if __name__ == '__main__':
    main()