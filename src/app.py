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

app.register_blueprint(library_bp, url_prefix='/api/library')
app.register_blueprint(search_bp, url_prefix='/api/search')
app.register_blueprint(download_bp, url_prefix='/api/download')
app.register_blueprint(upscale_bp, url_prefix='/api/upscale')
app.register_blueprint(reader_bp, url_prefix='/api/reader')
app.register_blueprint(status_bp, url_prefix='/api/status')
app.register_blueprint(auth_bp, url_prefix='/api/mangadex')
app.register_blueprint(export_bp, url_prefix='/api/export')

@app.route('/')
def index():
    from flask import Response
    html_path = BASE_DIR.parent / 'templates' / 'index.html'
    with open(html_path, 'r', encoding='utf-8') as f:
        content = f.read()
    return Response(content, mimetype='text/html', headers={'Cache-Control': 'no-cache'})

@app.route('/static/<path:filename>')
def serve_static(filename):
    return send_from_directory(app.static_folder, filename)

@app.route('/uploads/<path:filename>')
def serve_upload(filename):
    # Prefer upscaled version when available; fall back to original
    for d in [UPSCALED_DIR, MANGA_DIR]:
        path = Path(d) / filename
        if path.exists():
            return send_from_directory(path.parent, path.name)

    parts = filename.split('/')
    if len(parts) >= 2:
        subfolder = parts[0]
        filename_only = '/'.join(parts[1:])
        for d in [UPSCALED_DIR, MANGA_DIR]:
            search_path = Path(d) / subfolder
            if search_path.is_dir():
                full_path = search_path / filename_only
                if full_path.exists():
                    return send_from_directory(search_path, filename_only)

    return 'Not found', 404

if __name__ == '__main__':
    print("🚀 Manga Upscaler Pro - http://localhost:5001")
    app.run(port=5001, debug=False, host='0.0.0.0')