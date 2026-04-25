#!/usr/bin/env python3
"""
Simple Web-based Manga Pipeline UI
"""

from flask import Flask, render_template_string, request, jsonify
import threading
import os
from pathlib import Path
import time

app = Flask(__name__)

MANGA_DIR = os.path.expanduser("~/MangaLibrary")
UPSCALED_DIR = os.path.expanduser("~/MangaLibrary_Upscaled")

# Create directories
os.makedirs(MANGA_DIR, exist_ok=True)
os.makedirs(UPSCALED_DIR, exist_ok=True)

print(f"Manga Library: {MANGA_DIR}")
processing = {"status": "idle", "progress": 0, "message": ""}

HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>Manga Pipeline</title>
    <style>
        * { box-sizing: border-box; }
        body { font-family: 'Segoe UI', sans-serif; max-width: 800px; margin: 0 auto; padding: 20px; background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%); color: #eee; min-height: 100vh; }
        h1 { color: #e94560; text-align: center; font-size: 2.5em; margin-bottom: 30px; text-shadow: 2px 2px 4px rgba(0,0,0,0.3); }
        .box { background: rgba(255,255,255,0.1); padding: 25px; border-radius: 15px; margin: 15px 0; backdrop-filter: blur(10px); border: 1px solid rgba(255,255,255,0.1); }
        h2 { color: #ff6b6b; margin-top: 0; border-bottom: 1px solid rgba(255,255,255,0.2); padding-bottom: 10px; }
        .row { display: flex; gap: 10px; flex-wrap: wrap; }
        input { flex: 1; padding: 15px; border-radius: 8px; border: none; background: rgba(0,0,0,0.3); color: #fff; font-size: 14px; }
        input:focus { outline: 2px solid #e94560; }
        button { padding: 15px 25px; border-radius: 8px; border: none; cursor: pointer; font-weight: bold; transition: all 0.3s; }
        .btn-add { background: #27ae60; color: white; }
        .btn-add:hover { background: #2ecc71; transform: translateY(-2px); }
        .btn-dl { background: #3498db; color: white; }
        .btn-dl:hover { background: #2980b9; }
        .btn-up { background: #e74c3c; color: white; }
        .btn-up:hover { background: #c0392b; }
        .progress { height: 30px; background: rgba(0,0,0,0.3); border-radius: 15px; overflow: hidden; margin: 15px 0; }
        .bar { height: 100%; background: linear-gradient(90deg, #e94560, #ff6b6b); width: 0%; transition: width 0.3s; display: flex; align-items: center; justify-content: center; color: white; font-weight: bold; }
        #status { text-align: center; color: #aaa; }
        #library { display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 10px; }
        .manga-item { padding: 15px; background: rgba(0,0,0,0.3); border-radius: 10px; cursor: pointer; transition: all 0.3s; border: 2px solid transparent; }
        .manga-item:hover { background: rgba(233,69,96,0.2); }
        .manga-item.selected { border-color: #e94560; background: rgba(233,69,96,0.3); }
        .log { background: rgba(0,0,0,0.4); padding: 15px; height: 200px; overflow-y: auto; border-radius: 10px; font-family: 'Consolas', monospace; font-size: 12px; }
        .log div { padding: 3px 0; border-bottom: 1px solid rgba(255,255,255,0.1); }
        .log div:before { content: '> '; color: #666; }
        .buttons { display: flex; gap: 10px; margin-top: 10px; }
    </style>
</head>
<body>
    <h1>📚 Manga Pipeline</h1>
    
    <div class="box">
        <h2>➕ Add Manga</h2>
        <div class="row">
            <input type="text" id="url" placeholder="Paste MangaDex URL or Title here...">
            <button class="btn-add" onclick="addManga()">Add to Library</button>
        </div>
    </div>
    
    <div class="box">
        <h2>📚 Your Library</h2>
        <div id="library"></div>
    </div>
    
    <div class="box">
        <h2>⚡ Process</h2>
        <div class="buttons">
            <button class="btn-dl" onclick="download()">📥 Download</button>
            <button class="btn-up" onclick="upscale()">🔥 Upscale 4x</button>
        </div>
        <div class="progress"><div class="bar" id="bar"></div></div>
        <p id="status">Select a manga and click Download or Upscale</p>
    </div>
    
    <div class="box">
        <h2>📝 Log</h2>
        <div class="log" id="log"><div>Ready...</div></div>
    </div>
    
    <script>
        let current = null;
        
        function log(msg) {
            const d = document.getElementById('log');
            d.innerHTML = '<div>' + msg + '</div>' + d.innerHTML;
        }
        
        function addManga() {
            const url = document.getElementById('url').value.trim();
            if (!url) { log('Please enter a URL or title'); return; }
            log('Adding: ' + url);
            
            fetch('/add', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({url: url})
            }).then(r => r.text()).then(t => {
                console.log('Response:', t);
                log('Result: ' + t);
                loadLibrary();
            }).catch(e => {
                console.log('Error:', e);
                log('Error: ' + e);
            });
        }
        
        function loadLibrary() {
            fetch('/library').then(r => r.json()).then(d => {
                const g = document.getElementById('library');
                g.innerHTML = d.map(m => '<div class="manga-item" onclick="select(\''+m+'\')">'+m+'</div>').join('');
            });
        }
        
        function select(name) {
            current = name;
            document.querySelectorAll('.manga-item').forEach(el => el.classList.remove('selected'));
            event.target.classList.add('selected');
            document.getElementById('status').innerText = 'Selected: ' + name;
            log('Selected: ' + name);
        }
        
        function download() {
            if (!current) { log('Select a manga first!'); return; }
            log('Starting download: ' + current);
            fetch('/download', {method: 'POST', body: JSON.stringify({name: current})})
                .then(r => r.json()).then(d => log(d.message));
        }
        
        function upscale() {
            if (!current) { log('Select a manga first!'); return; }
            log('Starting upscale: ' + current + ' (4x, ~4s/page)');
            fetch('/upscale', {method: 'POST', body: JSON.stringify({name: current})})
                .then(r => r.json()).then(d => log(d.message));
        }
        
        loadLibrary();
        setInterval(() => {
            fetch('/status').then(r => r.json()).then(d => {
                if (d.progress > 0 && d.progress < 100) {
                    document.getElementById('bar').style.width = d.progress + '%';
                    document.getElementById('bar').innerText = d.message;
                    document.getElementById('status').innerText = d.message;
                } else if (d.progress >= 100) {
                    document.getElementById('bar').style.width = '100%';
                    document.getElementById('bar').innerText = 'Done!';
                    log(d.message);
                }
            });
        }, 500);
    </script>
</body>
</html>
"""

@app.route('/')
def index():
    return render_template_string(HTML)

@app.route('/library')
def library():
    folders = [f.name for f in Path(MANGA_DIR).iterdir() if f.is_dir()]
    print(f"Serving /library: {folders}")
    return jsonify(folders)

@app.route('/add', methods=['POST'])
def add_manga():
    import subprocess
    print("=== /add called ===")
    data = request.get_json()
    url = data.get('url', '').strip()
    
    if not url:
        return jsonify({"message": "Please enter a URL or title"})
    
    # Extract manga name - handle different URL formats
    if 'mangadex.org' in url:
        # Extract ID from URL
        parts = url.split('/')
        for i, p in enumerate(parts):
            if p in ['title', 'title_id'] and i+1 < len(parts):
                name = parts[i+1]
                break
        else:
            name = parts[-1] if parts[-1] else parts[-2]
    else:
        # Use title as name
        name = url.replace(' ', '-').lower()[:50]
    
    folder = os.path.join(MANGA_DIR, name)
    os.makedirs(folder, exist_ok=True)
    
    print(f"Created folder: {folder}")
    print(f"Exists: {os.path.exists(folder)}")
    processing['message'] = f"Added: {name}"
    return jsonify({"message": f"Added to library: {name}", "name": name})

@app.route('/download', methods=['POST'])
def download():
    def run():
        import subprocess
        data = request.get_json()
        name = data.get('name', '')
        
        folder = os.path.join(MANGA_DIR, name)
        processing['status'] = 'download'
        processing['message'] = 'Starting download...'
        processing['progress'] = 10
        
        try:
            # Try mangadex-dl
            result = subprocess.run([
                'mangadex-dl', 
                'https://mangadex.org/title/' + name,
                '-o', folder,
                '--format', 'png',
                '--language', 'es'
            ], capture_output=True, text=True, timeout=300)
            
            processing['message'] = f"Download complete: {name}"
            processing['progress'] = 100
            
        except Exception as e:
            processing['message'] = f"Download error: {str(e)[:50]}"
    
    threading.Thread(target=run, daemon=True).start()
    return jsonify({"message": "Download started..."})

@app.route('/upscale', methods=['POST'])
def upscale():
    def run():
        import torch
        from spandrel import ModelLoader
        from PIL import Image
        import numpy as np
        
        data = request.get_json()
        name = data.get('name', '')
        
        model_path = '/root/workspace/MangaJaNai/models/4x_IllustrationJaNai_V2standard_FDAT_M_52k.safetensors'
        
        processing['status'] = 'upscale'
        
        input_folder = os.path.join(MANGA_DIR, name)
        output_folder = os.path.join(UPSCALED_DIR, name)
        os.makedirs(output_folder, exist_ok=True)
        
        try:
            model_desc = ModelLoader().load_from_file(model_path)
            model = model_desc.model.cuda().eval()
            
            images = sorted(Path(input_folder).glob('*.png')) + sorted(Path(input_folder).glob('*.jpg'))
            total = len(images)
            
            for i, img_path in enumerate(images):
                processing['message'] = f"{i+1}/{total}"
                processing['progress'] = (i/total)*100
                
                img = Image.open(img_path).convert('RGB')
                img_np = np.array(img).astype(np.float32)/255.0
                img_tensor = torch.from_numpy(img_np).permute(2,0,1).unsqueeze(0).cuda()
                
                with torch.no_grad():
                    out = model(img_tensor)
                out_np = (out.squeeze(0).permute(1,2,0).cpu().numpy()*255).clip(0,255).astype(np.uint8)
                Image.fromarray(out_np).save(os.path.join(output_folder, img_path.name), quality=95)
            
            processing['status'] = 'done'
            processing['message'] = f"Done! {total} pages"
            processing['progress'] = 100
            
        except Exception as e:
            processing['message'] = f"Error: {e}"
    
    threading.Thread(target=run, daemon=True).start()
    return jsonify({"message": "Upscaling started..."})

@app.route('/status')
def status():
    return jsonify(processing)

if __name__ == '__main__':
    print("Starting UI at http://localhost:5000")
    app.run(port=5000, debug=False)