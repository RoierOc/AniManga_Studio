#!/usr/bin/env python3
"""
MangaDex Downloader v3 - with search
"""

import os
import sys
import requests
import time
from pathlib import Path

MANGA_DIR = os.path.expanduser("~/MangaLibrary")

def search_manga(query):
    """Search manga by title"""
    print(f"Searching: {query}")
    url = "https://api.mangadex.org/manga"
    params = {"title": query, "limit": 5}
    
    r = requests.get(url, params=params)
    data = r.json()
    
    if not data.get('data'):
        print("No results found")
        return None
    
    print("Results:")
    for i, m in enumerate(data['data']):
        attrs = m['attributes']
        title = attrs['title'].get('en') or list(attrs['title'].values())[0]
        print(f"  {i+1}. {title}")
    
    return data['data'][0]['id'], data['data'][0]['attributes']['title'].get('en') or 'Unknown'

def download_manga(title_or_url, max_chapters=None):
    # Try URL first
    if 'mangadex.org' in title_or_url:
        # Extract ID
        parts = title_or_url.split('/')
        for p in parts:
            if len(p) == 36 and '-' in p:
                manga_id = p
                break
        else:
            manga_id = title_or_url
    else:
        # Search
        result = search_manga(title_or_url)
        if not result:
            print("Manga not found")
            return
        manga_id, manga_title = result
        print(f"Using: {manga_id}")
    
    # Get details
    url = f"https://api.mangadex.org/manga/{manga_id}"
    r = requests.get(url)
    data = r.json()
    
    if 'data' not in data:
        print(f"Error: {data}")
        return
    
    attrs = data['data']['attributes']
    manga_title = (attrs['title'].get('en') or 
                   attrs['title'].get('ja-ro') or 
                   list(attrs['title'].values())[0])
    
    folder = Path(MANGA_DIR) / manga_title.replace('/', '-').replace('\\', '-')[:50]
    folder.mkdir(parents=True, exist_ok=True)
    
    print(f"\n📥 Downloading: {manga_title}")
    print(f"   To: {folder}")
    
    # Get chapters - no filter
    url = f"https://api.mangadex.org/manga/{manga_id}/feed"
    all_chapters = []
    offset = 0
    
    while True:
        params = {"limit": 100, "offset": offset}
        r = requests.get(url, params=params)
        items = r.json().get('data', [])
        
        if not items:
            break
            
        all_chapters.extend(items)
        offset += 100
        
        if len(items) < 100:
            break
    
    # Group by chapter - only English
    chapters_by_num = {}
    for item in all_chapters:
        ch = item['attributes']
        num = ch.get('chapter', '0')
        langs = ch.get('translatedLanguage', [])
        
        if 'en' in langs and num not in chapters_by_num:
            chapters_by_num[num] = item['id']
    
    chapters = sorted(chapters_by_num.items(), key=lambda x: float(x[0]) if x[0].isdigit() else 0, reverse=True)
    
    print(f"   Found {len(chapters)} English chapters")
    
    if max_chapters:
        chapters = chapters[:max_chapters]
    
    # Download
    for i, (ch_num, ch_id) in enumerate(chapters, 1):
        print(f"[{i}/{len(chapters)}] Ch{ch_num}...", end=" ", flush=True)
        
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
    
    print(f"\n✅ Done! {folder}")

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('manga', help='Title or URL')
    parser.add_argument('--chapters', type=int, help='Max chapters')
    args = parser.parse_args()
    
    download_manga(args.manga, args.chapters)