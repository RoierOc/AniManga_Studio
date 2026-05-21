#!/usr/bin/env python3
"""
Manga Upscaler Professional - Main Flask Application
"""

import sys
import os
from pathlib import Path

current_dir = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = Path(current_dir)
sys.path.insert(0, current_dir)

from api.runtime import MANGA_DIR, UPSCALED_DIR

os.makedirs(str(MANGA_DIR), exist_ok=True)
os.makedirs(str(UPSCALED_DIR), exist_ok=True)

from flask import Flask, render_template, jsonify, request, send_from_directory

app = Flask(__name__, 
    template_folder=str(BASE_DIR.parent / 'templates'),
    static_folder=str(BASE_DIR.parent / 'static'),
    static_url_path='/static')
app.secret_key = os.environ.get('SECRET_KEY', 'manga-secret-key-change-in-production')
app.jinja_env.auto_reload = False
app.jinja_env.cache = None

from api.library import library_bp
from api.search import search_bp
from api.download import download_bp
from api.upscale import upscale_bp
from api.reader import reader_bp
from api.status import status_bp
from api.mangadex import auth_bp
from api.export import export_bp
from api.sources import sources_bp
from api.drive import drive_bp
from api.webdav import webdav_bp
from api.cbz import cbz_bp
from api.anilist import anilist_bp
from api.anime import anime_bp
from api.subtitle import subtitle_bp

app.register_blueprint(library_bp, url_prefix='/api/library')
app.register_blueprint(search_bp, url_prefix='/api/search')
app.register_blueprint(download_bp, url_prefix='/api/download')
app.register_blueprint(upscale_bp, url_prefix='/api/upscale')
app.register_blueprint(reader_bp, url_prefix='/api/reader')
app.register_blueprint(status_bp, url_prefix='/api/status')
app.register_blueprint(auth_bp, url_prefix='/api/mangadex')
app.register_blueprint(export_bp, url_prefix='/api/export')
app.register_blueprint(sources_bp, url_prefix='/api/sources')
app.register_blueprint(drive_bp, url_prefix='/api/drive')
app.register_blueprint(webdav_bp, url_prefix='/api/webdav')
app.register_blueprint(cbz_bp, url_prefix='/api/cbz')
app.register_blueprint(anilist_bp, url_prefix='/api/anilist')
app.register_blueprint(anime_bp, url_prefix='/api/anime')
app.register_blueprint(subtitle_bp, url_prefix='/api/subtitle')


@app.route('/library')
def mobile_library():
    from flask import Response
    html_path = BASE_DIR.parent / 'templates' / 'library.html'
    with open(html_path, 'r', encoding='utf-8') as f:
        content = f.read()
    return Response(content, mimetype='text/html', headers={'Cache-Control': 'no-cache'})

@app.route('/')
def index():
    from flask import Response
    html_path = BASE_DIR.parent / 'templates' / 'index.html'
    with open(html_path, 'r', encoding='utf-8') as f:
        content = f.read()
    return Response(content, mimetype='text/html', headers={'Cache-Control': 'no-cache'})

@app.route('/sw.js')
def serve_sw():
    return send_from_directory(app.static_folder, 'sw.js',
                               mimetype='application/javascript')

@app.route('/static/<path:filename>')
def serve_static(filename):
    return send_from_directory(app.static_folder, filename)

@app.route('/uploads/original/<path:filename>')
def serve_upload_original(filename):
    """Always serve from MANGA_DIR (used by compare mode to show unscaled page)."""
    path = Path(MANGA_DIR) / filename
    if path.exists():
        return send_from_directory(str(path.parent), path.name)
    # Upscaled pages are always .jpg; originals may be .png or .webp — try alternatives
    for ext in ('.png', '.webp', '.jpg', '.jpeg'):
        alt = path.with_suffix(ext)
        if alt != path and alt.exists():
            return send_from_directory(str(alt.parent), alt.name)
    return 'Not found', 404

@app.route('/uploads/upscaled/<path:filename>')
def serve_upload_upscaled(filename):
    """Always serve from UPSCALED_DIR (used by compare mode to show upscaled page)."""
    path = Path(UPSCALED_DIR) / filename
    if path.exists():
        return send_from_directory(str(path.parent), path.name)
    return 'Not found', 404

@app.route('/uploads/<path:filename>')
def serve_upload(filename):
    # Prefer upscaled version when available; fall back to original
    for d in [UPSCALED_DIR, MANGA_DIR]:
        path = Path(d) / filename
        if path.exists():
            return send_from_directory(str(path.parent), path.name)

    parts = filename.split('/')
    if len(parts) >= 2:
        subfolder = parts[0]
        filename_only = '/'.join(parts[1:])
        for d in [UPSCALED_DIR, MANGA_DIR]:
            search_path = Path(d) / subfolder
            if search_path.is_dir():
                full_path = search_path / filename_only
                if full_path.exists():
                    return send_from_directory(str(search_path), filename_only)

    return 'Not found', 404

if __name__ == '__main__':
    print("🚀 Manga Upscaler Pro - http://localhost:5100")
    print("📡 WebDAV library   - http://localhost:5005/")
    app.run(port=5101, debug=False, host='127.0.0.1')