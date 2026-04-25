#!/usr/bin/env python3
"""
Search API - Search manga on MangaDex
"""

from flask import Blueprint, jsonify, request
import requests

search_bp = Blueprint('search', __name__)

@search_bp.route('')
def search_manga():
    query = request.args.get('q', '')
    if len(query) < 2:
        return jsonify([])
    
    r = requests.get('https://api.mangadex.org/manga', params={
        'title': query, 
        'limit': 15,
        'includes[]': 'cover_art'
    })
    data = r.json()
    
    results = []
    for m in data.get('data', []):
        attrs = m['attributes']
        title = attrs['title'].get('en') or list(attrs['title'].values())[0]
        manga_id = m['id']
        
        # Get cover - use direct upload endpoint
        cover_url = None
        relationships = m.get('relationships', [])
        for rel in relationships:
            if rel.get('type') == 'cover_art':
                filename = rel.get('attributes', {}).get('fileName')
                if filename:
                    # Use/uploads.mangadex.org for direct access
                    cover_url = f"https://uploads.mangadex.org/covers/{manga_id}/{filename}.256.jpg"
                    break
        
        results.append({
            'id': manga_id, 
            'title': title,
            'cover': cover_url
        })
    
    return jsonify(results)

@search_bp.route('/chapters/<manga_id>')
def get_chapters(manga_id):
    import re
    uuid_pattern = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', re.I)
    
    if not uuid_pattern.match(manga_id):
        return jsonify({'error': 'Invalid manga ID'}), 400
    
    chapters_by_num = {}
    offset = 0
    
    while len(chapters_by_num) < 100:
        r = requests.get(f'https://api.mangadex.org/manga/{manga_id}/feed', 
                       params={'limit': 100, 'offset': offset})
        data = r.json()
        items = data.get('data', [])
        
        if not items:
            break
        
        for item in items:
            ch = item['attributes']
            num = ch.get('chapter', '0')
            lang = ch.get('translatedLanguage', 'unknown')
            
            if not num:
                continue
            
            if num not in chapters_by_num:
                chapters_by_num[num] = []
            
            lang_exists = any(l['language'] == lang for l in chapters_by_num[num])
            if not lang_exists:
                chapters_by_num[num].append({'id': item['id'], 'chapter': num, 'language': lang})
        
        offset += 100
        if len(items) < 100:
            break
    
    chapters = []
    for num, langs in chapters_by_num.items():
        for lang_info in langs:
            display_name = f"{lang_info['chapter']} ({lang_info['language']})"
            chapters.append({
                'id': lang_info['id'], 
                'chapter': lang_info['chapter'], 
                'language': lang_info['language'],
                'display': display_name
            })
    
    chapters.sort(key=lambda x: float(x['chapter']) if x['chapter'].replace('.','').isdigit() else 0, reverse=True)
    return jsonify(chapters[:50])