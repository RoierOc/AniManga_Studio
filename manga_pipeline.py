#!/usr/bin/env python3
"""
Manga Pipeline - Complete manga management
Usage: python manga_pipeline.py [command] [options]

Commands:
  download  - Download manga from MangaDex
  upscale   - Upscale manga folder(s)
  organize  - Organize manga library
  viewer    - Start local viewer
  sync      - Sync to Google Drive
"""

import os
import sys
import argparse
import subprocess
from pathlib import Path

MANGA_DIR = os.path.expanduser("~/MangaLibrary")
UPSCALED_DIR = os.path.expanduser("~/MangaLibrary_Upscaled")

# Default model for upscaling
DEFAULT_MODEL = '/root/workspace/MangaJaNai/models/4x_IllustrationJaNai_V2standard_FDAT_M_52k.safetensors'

def cmd_download(args):
    """Download manga from MangaDex"""
    print("=== DOWNLOAD from MangaDex ===")
    
    try:
        subprocess.run(['mangadex-downloader', '--version'], capture_output=True)
    except FileNotFoundError:
        print("Installing mangadex-downloader...")
        subprocess.run([sys.executable, '-m', 'pip', 'install', 'mangadex-dl', '-q'])
    
    # Determine URL/id
    if args.manga_id:
        url_or_id = args.manga_id
    else:
        url_or_id = input("Enter MangaDex URL or ID: ")
    
    output_dir = os.path.join(MANGA_DIR)
    os.makedirs(output_dir, exist_ok=True)
    
    cmd = [
        'mangadex-dl',
        url_or_id,
        '-o', output_dir,
        '--folder-structure', '{manga}',
        '--(save-as)' if args.format == 'cbz' else '--format', 'raw' if args.format == 'cbz' else 'raw',
        '--language', args.language or 'es',
    ]
    
    if args.search:
        cmd.insert(2, '--search')
    
    if args.unread:
        cmd.append('--download-mode')
        cmd.append('unread')
    
    print(f"Running: {' '.join(cmd)}")
    subprocess.run(cmd)

def cmd_upscale(args):
    """Upscale manga folders"""
    print("=== UPSCALE ===")
    from manga_batch import process_folder
    
    input_dir = args.input or MANGA_DIR
    output_dir = args.output or UPSCALED_DIR
    
    model = args.model or '/root/workspace/MangaJaNai/models/4x_IllustrationJaNai_V2standard_FDAT_M_52k.safetensors'
    
    if args.folder:
        folders = [args.folder]
    else:
        folders = [d for d in Path(input_dir).iterdir() if d.is_dir()]
    
    for folder in folders:
        output = os.path.join(output_dir, folder.name)
        print(f"\nProcessing: {folder.name}")
        process_folder(str(folder), output, model, args.device)

def cmd_organize(args):
    """Organize manga library"""
    print("=== ORGANIZE ===")
    
    source = args.source or UPSCALED_DIR
    
    print(f"Library: {source}")
    print("\nManga folders:")
    
    for folder in sorted(Path(source).iterdir()):
        if folder.is_dir():
            images = list(folder.glob('*.png')) + list(folder.glob('*.jpg'))
            print(f"  {folder.name}: {len(images)} images")

def cmd_viewer(args):
    """Start viewer"""
    print("=== VIEWER ===")
    from manga_viewer import MangaViewer
    
    folder = args.folder or UPSCALED_DIR
    MangaViewer(folder)

def cmd_sync(args):
    """Sync to Google Drive"""
    print("=== SYNC ===")
    
    if args.action == 'upload':
        print("Uploading to Google Drive...")
        
        try:
            subprocess.run(['rclone', 'version'], capture_output=True)
        except FileNotFoundError:
            print("Installing rclone...")
            print("Download from: https://rclone.org/downloads/")
            return
        
        cmd = [
            'rclone', 'sync',
            UPSCALED_DIR,
            'gdrive:/MangaUpscaled',
            '--progress',
            '--transfers', '2'
        ]
        subprocess.run(cmd)
        
    elif args.action == 'download':
        cmd = [
            'rclone', 'sync',
            'gdrive:/MangaUpscaled',
            UPSCALED_DIR,
            '--progress',
            '--transfers', '2'
        ]
        subprocess.run(cmd)

def main():
    parser = argparse.ArgumentParser(description='Manga Pipeline')
    subparsers = parser.add_subparsers(dest='command', help='Commands')
    
    # Download
    dl_parser = subparsers.add_parser('download', help='Download manga')
    dl_parser.add_argument('manga_id', nargs='?', help='Manga ID or URL')
    dl_parser.add_argument('--search', '-s', action='store_true', help='Search manga')
    dl_parser.add_argument('--language', '-lang', default='es', help='Language (default: es)')
    dl_parser.add_argument('--unread', action='store_true', help='Download only unread')
    dl_parser.add_argument('--format', '-f', default='raw', choices=['raw', 'png', 'cbz'], help='Format')
    
    # Upscale
    up_parser = subparsers.add_parser('upscale', help='Upscale manga')
    up_parser.add_argument('--input', help='Input folder')
    up_parser.add_argument('--output', help='Output folder')
    up_parser.add_argument('--folder', help='Specific folder to process')
    up_parser.add_argument('--model', help='Model path')
    up_parser.add_argument('--device', default='cuda', help='Device')
    
    # Organize
    org_parser = subparsers.add_parser('organize', help='Organize library')
    org_parser.add_argument('--source', help='Source folder')
    
    # Viewer
    view_parser = subparsers.add_parser('viewer', help='Start viewer')
    view_parser.add_argument('--folder', help='Folder to view')
    
    # Sync
    sync_parser = subparsers.add_parser('sync', help='Sync with Google Drive')
    sync_parser.add_argument('action', choices=['upload', 'download'], help='Sync action')
    
    args = parser.parse_args()
    
    if not args.command:
        print("Usage: python manga_pipeline.py [command]")
        print("\nCommands:")
        print("  download <manga-id>  - Download from MangaDex")
        print("  upscale              - Upscale manga")
        print("  organize             - Organize library")
        print("  viewer               - Start viewer")
        print("  sync upload          - Upload to Google Drive")
        print("  sync download        - Download from Google Drive")
        
        print(f"\nDefault directories:")
        print(f"  Manga: {MANGA_DIR}")
        print(f"  Upscaled: {UPSCALED_DIR}")
        return
    
    if args.command == 'download':
        cmd_download(args)
    elif args.command == 'upscale':
        cmd_upscale(args)
    elif args.command == 'organize':
        cmd_organize(args)
    elif args.command == 'viewer':
        cmd_viewer(args)
    elif args.command == 'sync':
        cmd_sync(args)

if __name__ == "__main__":
    main()