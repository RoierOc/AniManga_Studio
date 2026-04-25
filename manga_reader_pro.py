#!/usr/bin/env python3
"""
Manga Reader Pro - Full Featured
"""

import os
import sys
import time
import threading
import queue
import requests
import numpy as np
from pathlib import Path
from flask import Flask, render_template_string, request, jsonify, send_from_directory
from urllib.parse import quote

app = Flask(__name__)

# Paths
MANGA_DIR = os.path.expanduser("~/MangaLibrary")
UPSCALED_DIR = os.path.expanduser("~/MangaLibrary_Upscaled")
os.makedirs(MANGA_DIR, exist_ok=True)
os.makedirs(UPSCALED_DIR, exist_ok=True)

# Global state
download_queue = queue.Queue()
current_downloads = {}
upscale_status = {}
download_status = {}

def set_upscale_status(manga, status):
    upscale_status[manga] = status

def get_upscale_status():
    return upscale_status.copy()

def set_download_status(manga, status):
    download_status[manga] = status

def get_download_status(manga):
    return download_status.get(manga, {})

HTML = """
<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Manga Reader Pro</title>
    <style>
        :root {
            --bg-dark: #0f0f0f;
            --bg-card: #1a1a1a;
            --bg-hover: #252525;
            --accent: #e94560;
            --accent-hover: #ff6b6b;
            --text: #ffffff;
            --text-dim: #888;
            --border: #333;
            --success: #2ecc71;
            --warning: #f39c12;
        }
        
        * { margin: 0; padding: 0; box-sizing: border-box; }
        
        body {
            font-family: 'Segoe UI', system-ui, sans-serif;
            background: var(--bg-dark);
            color: var(--text);
            min-height: 100vh;
        }
        
        header {
            background: var(--bg-card);
            padding: 12px 20px;
            display: flex;
            align-items: center;
            gap: 20px;
            border-bottom: 1px solid var(--border);
            position: sticky;
            top: 0;
            z-index: 100;
        }
        
        .logo {
            font-size: 22px;
            font-weight: bold;
            color: var(--accent);
            text-decoration: none;
            display: flex;
            align-items: center;
            gap: 8px;
        }
        
        .search-box {
            flex: 1;
            max-width: 400px;
            position: relative;
        }
        
        .search-box input {
            width: 100%;
            padding: 10px 15px;
            padding-left: 40px;
            background: var(--bg-dark);
            border: 1px solid var(--border);
            border-radius: 8px;
            color: var(--text);
            font-size: 14px;
        }
        
        .search-box input:focus {
            outline: none;
            border-color: var(--accent);
        }
        
        .nav-links {
            display: flex;
            gap: 5px;
        }
        
        .nav-btn {
            background: transparent;
            border: none;
            color: var(--text-dim);
            padding: 10px 16px;
            border-radius: 6px;
            cursor: pointer;
            font-size: 14px;
            transition: all 0.2s;
        }
        
        .nav-btn:hover, .nav-btn.active {
            color: var(--text);
            background: var(--bg-hover);
        }
        
        .nav-btn.active {
            color: var(--accent);
        }
        
        main {
            padding: 20px;
            max-width: 1400px;
            margin: 0 auto;
        }
        
        /* Grid */
        .manga-grid {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(160px, 1fr));
            gap: 16px;
        }
        
        .manga-card {
            background: var(--bg-card);
            border-radius: 10px;
            overflow: hidden;
            transition: transform 0.2s, box-shadow 0.2s;
            cursor: pointer;
        }
        
        .manga-card:hover {
            transform: translateY(-4px);
            box-shadow: 0 8px 25px rgba(0,0,0,0.3);
        }
        
        .manga-cover {
            width: 100%;
            aspect-ratio: 2/3;
            background: linear-gradient(135deg, var(--bg-hover) 0%, var(--bg-card) 100%);
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 36px;
            position: relative;
        }
        
        .manga-cover img {
            width: 100%;
            height: 100%;
            object-fit: cover;
        }
        
        .manga-badge {
            position: absolute;
            top: 8px;
            right: 8px;
            background: var(--accent);
            color: white;
            font-size: 11px;
            padding: 3px 8px;
            border-radius: 4px;
            font-weight: 600;
        }
        
        .manga-info {
            padding: 10px;
        }
        
        .manga-title {
            font-size: 13px;
            font-weight: 600;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }
        
        .manga-meta {
            font-size: 11px;
            color: var(--text-dim);
            margin-top: 4px;
            display: flex;
            justify-content: space-between;
        }
        
        /* Search */
        .search-section {
            margin-bottom: 30px;
            padding: 20px;
            background: var(--bg-card);
            border-radius: 12px;
        }
        
        .search-section h3 {
            margin-bottom: 12px;
            font-size: 16px;
        }
        
        /* Chapters */
        .chapters-container {
            margin-top: 20px;
        }
        
        .chapter-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 15px;
        }
        
        .chapter-header h3 {
            font-size: 18px;
        }
        
        .action-btn {
            background: var(--accent);
            color: white;
            border: none;
            padding: 8px 16px;
            border-radius: 6px;
            cursor: pointer;
            font-weight: 600;
            font-size: 13px;
            transition: background 0.2s;
        }
        
        .action-btn:hover {
            background: var(--accent-hover);
        }
        
        .action-btn:disabled {
            background: var(--text-dim);
            cursor: not-allowed;
        }
        
        .action-btn.secondary {
            background: var(--bg-hover);
        }
        
        .chapter-list {
            background: var(--bg-card);
            border-radius: 12px;
            overflow: hidden;
            max-height: 60vh;
            overflow-y: auto;
        }
        
        .chapter-item {
            display: flex;
            align-items: center;
            justify-content: space-between;
            padding: 12px 16px;
            border-bottom: 1px solid var(--border);
            transition: background 0.2s;
        }
        
        .chapter-item:last-child {
            border-bottom: none;
        }
        
        .chapter-item:hover {
            background: var(--bg-hover);
        }
        
        .chapter-num {
            font-weight: 600;
            font-size: 14px;
            min-width: 80px;
        }
        
        .chapter-status {
            font-size: 12px;
            padding: 4px 10px;
            border-radius: 4px;
            margin-left: 10px;
        }
        
        .chapter-status.downloaded {
            background: rgba(46, 204, 113, 0.2);
            color: var(--success);
        }
        
        .chapter-status.pending {
            background: rgba(243, 156, 18, 0.2);
            color: var(--warning);
        }
        
        .chapter-status.downloading {
            background: rgba(233, 69, 96, 0.2);
            color: var(--accent);
        }
        
        .chapter-actions {
            display: flex;
            gap: 8px;
        }
        
        .chapter-btn {
            background: var(--bg-dark);
            border: 1px solid var(--border);
            color: var(--text);
            padding: 6px 12px;
            border-radius: 6px;
            cursor: pointer;
            font-size: 12px;
            transition: all 0.2s;
        }
        
        .chapter-btn:hover {
            border-color: var(--accent);
            color: var(--accent);
        }
        
        .chapter-btn:disabled {
            opacity: 0.5;
            cursor: not-allowed;
        }
        
        /* Reader */
        .reader-overlay {
            position: fixed;
            top: 0;
            left: 0;
            right: 0;
            bottom: 0;
            background: #000;
            z-index: 200;
            display: none;
            flex-direction: column;
        }
        
        .reader-overlay.active {
            display: flex;
        }
        
        .reader-toolbar {
            background: rgba(0,0,0,0.8);
            padding: 10px 20px;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        
        .reader-title {
            font-size: 14px;
            color: #ccc;
        }
        
        .reader-close {
            background: none;
            border: none;
            color: white;
            font-size: 24px;
            cursor: pointer;
            padding: 5px;
        }
        
        .reader-content {
            flex: 1;
            display: flex;
            align-items: center;
            justify-content: center;
            overflow: auto;
        }
        
        .reader-img {
            max-width: 100%;
            max-height: 100%;
            object-fit: contain;
        }
        
        .reader-nav {
            position: fixed;
            top: 50%;
            transform: translateY(-50%);
            background: rgba(0,0,0,0.5);
            border: none;
            color: white;
            font-size: 40px;
            padding: 30px 20px;
            cursor: pointer;
            transition: background 0.2s;
        }
        
        .reader-nav:hover {
            background: var(--accent);
        }
        
        .reader-nav.prev { left: 0; }
        .reader-nav.next { right: 0; }
        
        /* Downloads Panel */
        .downloads-panel {
            background: var(--bg-card);
            border-radius: 12px;
            padding: 20px;
            margin-bottom: 20px;
        }
        
        .downloads-panel h3 {
            margin-bottom: 15px;
            font-size: 16px;
            display: flex;
            align-items: center;
            gap: 8px;
        }
        
        .download-item {
            display: flex;
            align-items: center;
            justify-content: space-between;
            padding: 10px;
            background: var(--bg-dark);
            border-radius: 8px;
            margin-bottom: 8px;
        }
        
        .download-info {
            flex: 1;
        }
        
        .download-name {
            font-size: 13px;
            font-weight: 600;
        }
        
        .download-progress {
            font-size: 12px;
            color: var(--text-dim);
        }
        
        .download-bar {
            height: 4px;
            background: var(--bg-hover);
            border-radius: 2px;
            margin-top: 8px;
            overflow: hidden;
        }
        
        .download-bar-fill {
            height: 100%;
            background: var(--accent);
            transition: width 0.3s;
        }
        
        /* Views */
        .view { display: none; }
        .view.active { display: block; }
        
        /* Progress Bar */
        .progress-container {
            background: var(--bg-dark);
            border-radius: 8px;
            height: 24px;
            overflow: hidden;
            position: relative;
        }
        
        .progress-bar {
            background: linear-gradient(90deg, var(--accent) 0%, var(--accent-hover) 100%);
            height: 100%;
            transition: width 0.3s ease;
            border-radius: 8px;
        }
        
        .progress-text {
            position: absolute;
            top: 50%;
            left: 50%;
            transform: translate(-50%, -50%);
            font-size: 12px;
            font-weight: 600;
            color: white;
            text-shadow: 0 1px 2px rgba(0,0,0,0.8);
            z-index: 1;
        }
        
        /* Status Panel */
        .status-panel {
            background: var(--bg-card);
            border-radius: 12px;
            padding: 15px;
            margin-bottom: 15px;
        }
        
        .status-panel h4 {
            font-size: 14px;
            margin-bottom: 10px;
            display: flex;
            align-items: center;
            gap: 8px;
        }
        
        .status-indicator {
            display: inline-block;
            width: 10px;
            height: 10px;
            border-radius: 50%;
        }
        
        .status-indicator.downloading { background: var(--warning); animation: pulse 1s infinite; }
        .status-indicator.ready { background: var(--success); }
        .status-indicator.error { background: var(--accent); }
        
        @keyframes pulse {
            0%, 100% { opacity: 1; }
            50% { opacity: 0.5; }
        }
        
        /* Toast */
        .toast {
            position: fixed;
            bottom: 20px;
            right: 20px;
            background: var(--bg-card);
            border: 1px solid var(--border);
            padding: 12px 20px;
            border-radius: 8px;
            animation: slideIn 0.3s ease;
            z-index: 300;
        }
        
        @keyframes slideIn {
            from { transform: translateX(100%); opacity: 0; }
            to { transform: translateX(0); opacity: 1; }
        }
        
        /* Modal */
        .modal-overlay {
            position: fixed;
            top: 0;
            left: 0;
            right: 0;
            bottom: 0;
            background: rgba(0,0,0,0.8);
            z-index: 150;
            display: none;
            align-items: center;
            justify-content: center;
        }
        
        .modal-overlay.active { display: flex; }
        
        .modal {
            background: var(--bg-card);
            border-radius: 12px;
            width: 90%;
            max-width: 600px;
            max-height: 80vh;
            overflow: hidden;
            display: flex;
            flex-direction: column;
        }
        
        .modal-header {
            padding: 16px 20px;
            border-bottom: 1px solid var(--border);
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        
        .modal-header h2 {
            font-size: 18px;
        }
        
        .modal-close {
            background: none;
            border: none;
            color: var(--text-dim);
            font-size: 24px;
            cursor: pointer;
        }
        
        .modal-body {
            padding: 20px;
            overflow-y: auto;
            flex: 1;
        }
    </style>
</head>
<body>
    <header>
        <a href="#" class="logo" onclick="showView('library')">📚 MangaPro</a>
        
        <div class="search-box">
            <span style="position:absolute;left:12px;top:50%;transform:translateY(-50%);color:var(--text-dim)">🔍</span>
            <input type="text" id="searchInput" placeholder="Buscar manga..." onkeyup="searchManga(this.value)">
        </div>
        
        <nav class="nav-links">
            <button class="nav-btn active" onclick="showView('library')">📚 Biblioteca</button>
            <button class="nav-btn" onclick="showView('downloads')">⬇ Descargas</button>
        </nav>
    </header>
    
    <main>
        <!-- Library View -->
        <div id="library" class="view active">
            <div id="searchResults"></div>
            
            <div style="margin-top: 30px;">
                <h3 style="margin-bottom:15px;font-size:16px;">Tu Biblioteca</h3>
                <div class="manga-grid" id="libraryGrid"></div>
            </div>
        </div>
        
        <!-- Downloads View -->
        <div id="downloads" class="view">
            <div class="search-section" style="background: linear-gradient(135deg, #1a3a5c 0%, #1a1a2e 100%);">
                <h3>🔍 Agregar Manga (Descarga directa)</h3>
                <p style="color:var(--text-dim);font-size:13px;margin-bottom:10px;">
                    Escribe el nombre del manga para descargar desde MangaDex
                </p>
                <div style="display:flex;gap:10px;margin-bottom:15px;">
                    <input type="text" id="cliDownloadTitle" placeholder="Ej: Solo Leveling" style="flex:1;padding:12px;background:var(--bg-dark);border:1px solid var(--border);border-radius:8px;color:var(--text);font-size:14px;">
                    <input type="number" id="cliDownloadChapters" value="5" min="1" max="50" style="width:60px;padding:12px;background:var(--bg-dark);border:1px solid var(--border);border-radius:8px;color:var(--text);font-size:14px;">
                    <button class="action-btn" onclick="runCliDownload()" style="padding:12px 20px;">⬇ Descargar</button>
                </div>
                <div id="cliDownloadStatus" style="color:var(--text-dim);font-size:13px;"></div>
            </div>
            
            <div class="search-section" style="margin-top:20px;">
                <h3>🔍 Buscar en MangaDex</h3>
                <p style="color:var(--text-dim);font-size:13px;margin-bottom:10px;">
                    Busca para ver capítulos (no descarga directamente)
                </p>
                <div class="search-box" style="max-width:100%;">
                    <input type="text" id="downloadSearch" placeholder="Buscar manga..." onkeyup="searchForDownload(this.value)">
                </div>
                <div class="manga-grid" id="downloadResults" style="margin-top:15px;"></div>
            </div>
        </div>
    </main>
    
    <!-- Manga Detail Modal -->
    <div class="modal-overlay" id="mangaModal">
        <div class="modal">
            <div class="modal-header">
                <h2 id="modalTitle">Manga</h2>
                <button class="modal-close" onclick="closeModal()">✕</button>
            </div>
            <div class="modal-body">
                <div class="chapter-header">
                    <h3>Capítulos</h3>
                    <div>
                        <button class="action-btn" onclick="downloadAllChapters()">⬇ Todo</button>
                        <button class="action-btn secondary" onclick="upscaleAll()">🔥 Upscale Todo</button>
                    </div>
                </div>
                <div class="chapter-list" id="chaptersList"></div>
            </div>
        </div>
    </div>
    
    <!-- Reader -->
    <div class="reader-overlay" id="reader">
        <div class="reader-toolbar">
            <span class="reader-title" id="readerTitle">Capítulo</span>
            <button class="reader-close" onclick="closeReader()">✕ Cerrar</button>
        </div>
        <div class="reader-content">
            <button class="reader-nav prev" onclick="prevPage()">❮</button>
            <img class="reader-img" id="readerImg" src="">
            <button class="reader-nav next" onclick="nextPage()">❯</button>
        </div>
    </div>
    
    <div class="toast" id="toast" style="display:none;"></div>
    
    <script>
        let currentManga = null;
        let currentChapters = [];
        let chapterStatus = { downloaded: {}, upscaled: {} };
        let currentPages = [];
        let currentPage = 0;
        
        function showView(view) {
            document.querySelectorAll('.view').forEach(v => v.classList.remove('active'));
            document.querySelectorAll('.nav-btn').forEach(b => b.classList.remove('active'));
            document.getElementById(view).classList.add('active');
            event.target.classList.add('active');
            if(view === 'library') loadLibrary();
        }
        
        function showToast(msg) {
            const t = document.getElementById('toast');
            t.textContent = msg;
            t.style.display = 'block';
            setTimeout(() => t.style.display = 'none', 3000);
        }
        
        function searchManga(q) {
            if (q.length < 2) {
                document.getElementById('searchResults').innerHTML = '';
                return;
            }
            
            fetch('/api/search?q=' + encodeURIComponent(q))
                .then(r => r.json())
                .then(data => {
                    const grid = document.getElementById('searchResults');
                    if (data.length === 0) {
                        grid.innerHTML = '<p style="color:var(--text-dim)">Sin resultados</p>';
                        return;
                    }
                    
                    grid.innerHTML = `
                        <div class="manga-grid">
                            ${data.map(m => `
                                <div class="manga-card" onclick="openManga('${m.id}', '${m.title.replace(/'/g, "\\'")}')">
                                    <div class="manga-cover">📖</div>
                                    <div class="manga-info">
                                        <div class="manga-title">${m.title}</div>
                                    </div>
                                </div>
                            `).join('')}
                        </div>
                    `;
                });
        }
        
        function searchForDownload(q) {
            if (q.length < 2) {
                document.getElementById('downloadResults').innerHTML = '';
                return;
            }
            
            fetch('/api/search?q=' + encodeURIComponent(q))
                .then(r => r.json())
                .then(data => {
                    const grid = document.getElementById('downloadResults');
                    grid.innerHTML = data.map(m => `
                        <div class="manga-card" onclick="openManga('${m.id}', '${m.title.replace(/'/g, "\\'")}')">
                            <div class="manga-cover">📖</div>
                            <div class="manga-info">
                                <div class="manga-title">${m.title}</div>
                            </div>
                        </div>
                    `).join('');
                });
        }
        
        let downloadInterval = null;

        function runCliDownload() {
            const title = document.getElementById('cliDownloadTitle').value.trim();
            const chapters = parseInt(document.getElementById('cliDownloadChapters').value) || 5;
            const statusDiv = document.getElementById('cliDownloadStatus');
            
            if (!title) {
                statusDiv.innerHTML = '⚠️ Escribe el nombre del manga';
                return;
            }
            
            const downloadId = title.replace(/ /g, '_');
            
            // Show download modal
            const downloadHtml = `
                <div id="downloadProgressModal" style="position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,0.9);z-index:1000;display:flex;align-items:center;justify-content:center;flex-direction:column;">
                    <div style="background:var(--bg-card);padding:30px;border-radius:16px;width:350px;text-align:center;">
                        <h3 style="margin-bottom:15px;">📥 Descargando...</h3>
                        <div style="font-size:14px;color:var(--accent);margin-bottom:20px;">${title}</div>
                        <div style="background:var(--bg-dark);height:20px;border-radius:10px;overflow:hidden;margin-bottom:15px;">
                            <div id="downloadProgressBar" style="background:var(--warning);height:100%;width:0%;transition:width 0.3s;"></div>
                        </div>
                        <div id="downloadProgressText" style="color:var(--text-dim);font-size:14px;">Conectando a MangaDex...</div>
                        <div id="downloadStatusMsg" style="color:var(--text-dim);font-size:12px;margin-top:10px;">Buscando capítulos...</div>
                        <button onclick="closeDownloadModal()" style="margin-top:20px;padding:10px 20px;background:var(--bg-dark);border:1px solid var(--border);color:var(--text);border-radius:8px;cursor:pointer;">Cerrar</button>
                    </div>
                </div>
            `;
            document.body.insertAdjacentHTML('beforeend', downloadHtml);
            
            fetch('/api/download_cli', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ title, chapters })
            }).then(r => r.json()).then(d => {
                if (d.status === 'started') {
                    // Poll for download status
                    downloadInterval = setInterval(() => {
                        fetch('/api/download_progress/' + encodeURIComponent(downloadId))
                            .then(r => r.json())
                            .then(p => {
                                if (p.status === 'starting') {
                                    document.getElementById('downloadProgressText').textContent = 'Iniciando descarga...';
                                } else if (p.status === 'no_chapters') {
                                    clearInterval(downloadInterval);
                                    closeDownloadModal();
                                    statusDiv.innerHTML = '❌ MangaDex no tiene capítulos disponibles.<br><span style="font-size:12px;color:var(--text-dim)">Los servidores de MangaDex no tienen las páginas en caché.</span>';
                                } else if (p.status === 'error') {
                                    clearInterval(downloadInterval);
                                    closeDownloadModal();
                                    showToast('❌ Error: ' + p.message);
                                }
                            });
                    }, 2000);
                } else if (d.status === 'no_chapters') {
                    closeDownloadModal();
                    statusDiv.innerHTML = '❌ No hay capítulos disponibles.<br><span style="font-size:12px;color:var(--text-dim)">MangaDex no tiene este manga en inglés.</span>';
                } else {
                    closeDownloadModal();
                    statusDiv.textContent = '❌ Error: ' + (d.message || 'Error desconocido');
                }
            }).catch(err => {
                closeDownloadModal();
                statusDiv.textContent = '❌ Error: ' + err;
            });
        }

        function closeDownloadModal() {
            if (downloadInterval) clearInterval(downloadInterval);
            const modal = document.getElementById('downloadProgressModal');
            if (modal) modal.remove();
        }
        
        function openManga(id, title) {
            currentManga = { id, title };
            document.getElementById('modalTitle').textContent = title;
            document.getElementById('mangaModal').classList.add('active');
            loadChapters(id, title);
        }
        
        function closeModal() {
            document.getElementById('mangaModal').classList.remove('active');
        }
        
        function loadChapters(mangaId, title) {
            fetch('/api/chapters/' + mangaId)
                .then(r => r.json())
                .then(chapters => {
                    // If no chapters from MangaDex, get local chapters
                    if (chapters.length === 0) {
                        fetch('/api/chapter_status/' + encodeURIComponent(title))
                            .then(r => r.json())
                            .then(status => {
                                // Generate chapter list from local files
                                const localChapters = [];
                                if (status.downloaded) {
                                    for (const ch in status.downloaded) {
                                        localChapters.push({ chapter: ch, id: 'local' + ch });
                                    }
                                }
                                currentChapters = localChapters.sort((a, b) => parseFloat(b.chapter) - parseFloat(a.chapter));
                                chapterStatus = status;
                                renderChapters(title);
                            });
                    } else {
                        currentChapters = chapters;
                        fetch('/api/chapter_status/' + encodeURIComponent(title))
                            .then(r => r.json())
                            .then(status => {
                                chapterStatus = status;
                                renderChapters(title);
                            });
                    }
                });
        }
        
        function renderChapters(title) {
            const list = document.getElementById('chaptersList');
            
            if (!currentChapters || currentChapters.length === 0) {
                list.innerHTML = `
                    <div style="padding:30px;text-align:center;color:var(--text-dim);">
                        <p style="font-size:18px;margin-bottom:15px;">📭 No hay capítulos disponibles</p>
                        <p style="font-size:13px;margin-bottom:15px;">MangaDex no tiene las páginas en caché para este manga.</p>
                        <p style="font-size:12px;">Para descargar, los capítulos deben estar disponibles en MangaDex.</p>
                    </div>
                `;
                return;
            }
            
            list.innerHTML = currentChapters.map(ch => {
                const isDownloaded = chapterStatus.downloaded && chapterStatus.downloaded[ch.chapter] && chapterStatus.downloaded[ch.chapter].length > 0;
                const isUpscaled = chapterStatus.upscaled && chapterStatus.upscaled[ch.chapter];
                
                let statusClass = 'pending';
                let statusText = 'Pendiente';
                if (isUpscaled) {
                    statusClass = 'downloaded';
                    statusText = '✓ Upscaled';
                } else if (isDownloaded) {
                    statusClass = 'downloaded';
                    statusText = '✓ Descargado';
                }
                
                return `
                    <div class="chapter-item">
                        <div style="display:flex;align-items:center;">
                            <span class="chapter-num">Cap. ${ch.chapter}</span>
                            <span style="margin-left:8px;font-size:10px;padding:2px 6px;background:${ch.language === 'en' ? '#27ae60' : '#e67e22'};border-radius:4px;color:white;">${ch.language.toUpperCase()}</span>
                            <span class="chapter-status ${statusClass}" style="margin-left:8px;">
                                ${statusText}
                            </span>
                        </div>
                        <div class="chapter-actions">
                            ${!isDownloaded ? `<button class="chapter-btn" onclick="downloadChapter('${ch.id}', '${currentManga.title.replace(/'/g, "\\'")}', '${ch.chapter}')">⬇ Descargar (${ch.language})</button>` : ''}
                            ${isDownloaded && !isUpscaled ? `<button class="chapter-btn" onclick="upscaleChapter('${currentManga.title.replace(/'/g, "\\'")}', '${ch.chapter}')">🔥 Upscale</button>` : ''}
                            ${isDownloaded ? `<button class="chapter-btn" onclick="readChapter('${currentManga.title.replace(/'/g, "\\'")}', '${ch.chapter}')">📖 Leer</button>` : ''}
                        </div>
                    </div>
                `;
            }).join('');
        }
        
        function downloadChapter(chapterId, title, chapter) {
            // Show download modal for this specific chapter
            const downloadId = title.replace(/ /g, '_') + '_ch' + chapter;
            
            const downloadHtml = `
                <div id="chapterDownloadModal" style="position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,0.9);z-index:1000;display:flex;align-items:center;justify-content:center;flex-direction:column;">
                    <div style="background:var(--bg-card);padding:30px;border-radius:16px;width:320px;text-align:center;">
                        <h3 style="margin-bottom:10px;">📥 Descargando Capítulo ${chapter}</h3>
                        <div style="font-size:13px;color:var(--text-dim);margin-bottom:15px;">${title}</div>
                        <div style="background:var(--bg-dark);height:20px;border-radius:10px;overflow:hidden;">
                            <div id="chapterProgressBar" style="background:var(--warning);height:100%;width:0%;transition:width 0.5s;"></div>
                        </div>
                        <div id="chapterProgressText" style="color:var(--text-dim);font-size:13px;margin-top:10px;">Iniciando...</div>
                        <button onclick="closeChapterDownload()" style="margin-top:20px;padding:10px 20px;background:var(--bg-dark);border:1px solid var(--border);color:var(--text);border-radius:8px;cursor:pointer;">Cancelar</button>
                    </div>
                </div>
            `;
            document.body.insertAdjacentHTML('beforeend', downloadHtml);
            
            // Use direct download API for this chapter
            fetch('/api/download_chapter', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ chapterId, title, chapter })
            }).then(r => r.json()).then(d => {
                if (d.pages && d.pages > 0) {
                    document.getElementById('chapterProgressBar').style.width = '100%';
                    document.getElementById('chapterProgressText').textContent = '✅ Descargado: ' + d.pages + ' páginas';
                    setTimeout(() => {
                        closeChapterDownload();
                        showToast('✅ Capítulo ' + chapter + ' descargado (' + d.pages + ' páginas)');
                        // Refresh
                        loadChapters(currentManga.id, currentManga.title);
                    }, 1000);
                } else if (d.error) {
                    document.getElementById('chapterProgressBar').style.background = 'var(--accent)';
                    document.getElementById('chapterProgressText').textContent = '❌ ' + d.error;
                } else {
                    document.getElementById('chapterProgressBar').style.background = 'var(--accent)';
                    document.getElementById('chapterProgressText').textContent = '⚠️ MangaDex no tiene este capítulo disponible';
                }
            }).catch(err => {
                document.getElementById('chapterProgressBar').style.background = 'var(--accent)';
                document.getElementById('chapterProgressText').textContent = '❌ Error: ' + err;
            });
            
            // Simulate progress animation for visual feedback
            let progress = 0;
            downloadInterval = setInterval(() => {
                progress += 10;
                if (progress < 90) {
                    document.getElementById('chapterProgressBar').style.width = progress + '%';
                    document.getElementById('chapterProgressText').textContent = 'Descargando... ' + progress + '%';
                }
            }, 300);
        }

        function closeChapterDownload() {
            if (downloadInterval) clearInterval(downloadInterval);
            const modal = document.getElementById('chapterDownloadModal');
            if (modal) modal.remove();
        }

        function runCliDownloadFromButton(title) {
            runCliDownload();
        }
        
        function downloadAllChapters() {
            showToast('Descargando todos los capítulos...');
            fetch('/api/download_manga', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ mangaId: currentManga.id, title: currentManga.title, maxChapters: 10 })
            }).then(r => r.json()).then(d => {
                showToast('Descarga iniciada: ' + d.folder);
                // Refresh chapters after short delay
                setTimeout(() => {
                    loadChapters(currentManga.id, currentManga.title);
                }, 3000);
            });
        }
        
let upscaleInterval = null;

function upscaleChapter(title, chapter) {
            const upscaleId = title + '_ch' + chapter;
            showToast('🔥 Upscaling capítulo ' + chapter + '...');
            
            // Show progress modal
            const progressHtml = `
                <div id="upscaleProgressModal" style="position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,0.9);z-index:1000;display:flex;align-items:center;justify-content:center;flex-direction:column;">
                    <div style="background:var(--bg-card);padding:30px;border-radius:16px;width:300px;text-align:center;">
                        <h3 style="margin-bottom:20px;">🔥 Upscaling...</h3>
                        <div style="background:var(--bg-dark);height:20px;border-radius:10px;overflow:hidden;margin-bottom:15px;">
                            <div id="progressBar" style="background:linear-gradient(90deg,var(--accent),var(--accent-hover));height:100%;width:0%;transition:width 0.3s;"></div>
                        </div>
                        <div id="progressText" style="color:var(--text-dim);font-size:14px;">0%</div>
                        <div id="progressStatus" style="color:var(--text-dim);font-size:12px;margin-top:10px;">Cargando modelo...</div>
                        <button onclick="closeProgressModal()" style="margin-top:20px;padding:10px 20px;background:var(--bg-dark);border:1px solid var(--border);color:var(--text);border-radius:8px;cursor:pointer;">Cancelar</button>
                    </div>
                </div>
            `;
            document.body.insertAdjacentHTML('beforeend', progressHtml);
            
            fetch('/api/upscale_chapter', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ title, chapter })
            }).then(r => r.json()).then(d => {
                if (d.status === 'started') {
                    // Poll for progress
                    upscaleInterval = setInterval(() => {
                        fetch('/api/upscale_progress/' + encodeURIComponent(upscaleId))
                            .then(r => r.json())
                            .then(p => {
                                if (p.status === 'upscaleing') {
                                    const pct = p.total > 0 ? Math.round((p.current / p.total) * 100) : 0;
                                    document.getElementById('progressBar').style.width = pct + '%';
                                    document.getElementById('progressText').textContent = p.current + '/' + p.total + ' (' + pct + '%)';
                                    document.getElementById('progressStatus').textContent = 'Procesando imagen...';
                                } else if (p.status === 'complete') {
                                    clearInterval(upscaleInterval);
                                    closeProgressModal();
                                    showToast('✅ Capítulo ' + chapter + ' upscaleado!');
                                    // Refresh status
                                    fetch('/api/chapter_status/' + encodeURIComponent(title))
                                        .then(r => r.json())
                                        .then(status => {
                                            chapterStatus = status;
                                            renderChapters(title);
                                        });
                                } else if (p.status === 'error') {
                                    clearInterval(upscaleInterval);
                                    closeProgressModal();
                                    showToast('❌ Error: ' + p.error);
                                }
                            });
                    }, 1000);
                } else {
                    closeProgressModal();
                    showToast('❌ Error: ' + (d.message || 'Error'));
                }
            });
        }

        function closeProgressModal() {
            if (upscaleInterval) clearInterval(upscaleInterval);
            const modal = document.getElementById('upscaleProgressModal');
            if (modal) modal.remove();
        }

        function upscaleAll() {
            showToast('🔥 Upscaling todo el manga... (puede tomar minutos)');
            fetch('/api/upscale_manga', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ title: currentManga.title })
            }).then(r => r.json()).then(d => {
                if (d.status === 'started') {
                    showToast('⏳ Upscaling en progreso... (actualiza en 30 seg)');
                    setTimeout(() => {
                        loadLibrary();
                        showToast('✅ Upscale completo!');
                    }, 30000);
                }
            });
        }
        
        function readChapter(title, chapter) {
            fetch('/api/read_chapter', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ title, chapter })
            }).then(r => r.json()).then(data => {
                console.log('Read response:', data);
                if (data.pages && data.pages.length > 0) {
                    currentPages = data.pages;
                    currentPage = 0;
                    document.getElementById('readerTitle').textContent = 'Cap. ' + chapter + ' - ' + data.pages.length + ' páginas';
                    // Use the correct path format
                    const imgPath = data.pages[0].replace(/ /g, '%20');
                    document.getElementById('readerImg').src = '/uploads/' + imgPath;
                    document.getElementById('reader').classList.add('active');
                    closeModal();
                } else {
                    showToast('No se encontraron imágenes para este capítulo');
                }
            }).catch(err => {
                showToast('Error al leer: ' + err);
            });
        }
        
        function prevPage() {
            if (currentPage > 0) {
                currentPage--;
                document.getElementById('readerImg').src = '/uploads/' + currentPages[currentPage];
            }
        }
        
        function nextPage() {
            if (currentPage < currentPages.length - 1) {
                currentPage++;
                document.getElementById('readerImg').src = '/uploads/' + currentPages[currentPage];
            }
        }
        
        function closeReader() {
            document.getElementById('reader').classList.remove('active');
        }
        
        function loadLibrary() {
            fetch('/api/library')
                .then(r => r.json())
                .then(mangas => {
                    const grid = document.getElementById('libraryGrid');
                    if (mangas.length === 0) {
                        grid.innerHTML = '<p style="color:var(--text-dim);grid-column:1/-1;text-align:center;padding:40px;">Tu biblioteca está vacía. Busca y descarga manga!</p>';
                        return;
                    }
                    
                    grid.innerHTML = mangas.map(m => `
                        <div class="manga-card" onclick="openManga('${m.id}', '${m.name.replace(/'/g, "\\'")}')">
                            <div class="manga-cover">
                                📖
                                <span class="manga-badge">${m.count}</span>
                            </div>
                            <div class="manga-info">
                                <div class="manga-title">${m.name}</div>
                                <div class="manga-meta">
                                    <span>${m.count} caps</span>
                                    ${m.upscaled > 0 ? `<span style="color:var(--success)">✓ Upscaled</span>` : ''}
                                </div>
                            </div>
                        </div>
                    `).join('');
                });
        }
        
        document.addEventListener('keydown', e => {
            if (document.getElementById('reader').classList.contains('active')) {
                if (e.key === 'ArrowLeft') prevPage();
                if (e.key === 'ArrowRight') nextPage();
                if (e.key === 'Escape') closeReader();
            }
        });
        
        loadLibrary();
    </script>
</body>
</html>
"""

@app.route('/')
def index():
    return render_template_string(HTML)

@app.route('/api/search')
def api_search():
    query = request.args.get('q', '')
    if len(query) < 2:
        return jsonify([])
    
    r = requests.get('https://api.mangadex.org/manga', params={'title': query, 'limit': 15})
    data = r.json()
    
    results = []
    for m in data.get('data', []):
        attrs = m['attributes']
        title = attrs['title'].get('en') or list(attrs['title'].values())[0]
        results.append({'id': m['id'], 'title': title})
    
    return jsonify(results)

@app.route('/api/chapters/<manga_id>')
def api_chapters(manga_id):
    # Check if it's a UUID (MangaDex ID) or a folder name
    import re
    uuid_pattern = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', re.I)
    
    if uuid_pattern.match(manga_id):
        # It's a MangaDex ID - fetch from MangaDex
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
                
                # Add this chapter+language combo if not already present
                lang_exists = any(l['language'] == lang for l in chapters_by_num[num])
                if not lang_exists:
                    chapters_by_num[num].append({'id': item['id'], 'chapter': num, 'language': lang})
            
            offset += 100
            if len(items) < 100:
                break
        
        # Flatten and include language in display
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
    else:
        # It's a folder name - get local chapters from disk
        chapter_data = []
        folder = Path(MANGA_DIR) / manga_id
        
        if folder.exists():
            # Check if files are directly in folder or in subfolders
            has_subdirs = any(f.is_dir() for f in folder.iterdir())
            has_files = any(f.is_file() for f in folder.glob('*'))
            
            if has_files and not has_subdirs:
                # Files directly in folder - group by chapter prefix
                files = list(folder.glob('*.jpg')) + list(folder.glob('*.png')) + list(folder.glob('*.webp'))
                chapters_found = {}
                for f in files:
                    parts = f.stem.split('_')
                    if len(parts) > 0 and parts[0].startswith('ch'):
                        ch = parts[0][2:]
                        if ch not in chapters_found:
                            chapters_found[ch] = []
                        chapters_found[ch].append(f)
                
                for ch, pages in chapters_found.items():
                    chapter_data.append({
                        'id': f"{manga_id}|{ch}",
                        'chapter': ch,
                        'language': 'local',
                        'page_count': len(pages)
                    })
            else:
                # Files in subfolders
                for ch_folder in sorted(folder.iterdir(), reverse=True):
                    if not ch_folder.is_dir():
                        continue
                    ch_name = ch_folder.name
                    if ch_name.startswith('ch'):
                        ch_num = ch_name[2:]
                    else:
                        ch_num = ch_name
                    
                    pages = list(ch_folder.glob('*.jpg')) + list(ch_folder.glob('*.png')) + list(ch_folder.glob('*.webp'))
                    
                    if pages:
                        chapter_data.append({
                            'id': str(ch_folder),
                            'chapter': ch_num,
                            'language': 'local',
                            'page_count': len(pages)
                        })
        
        chapter_data.sort(key=lambda x: float(x['chapter'].replace('.','')) if x['chapter'].replace('.','').replace('-','').isdigit() else 0, reverse=True)
        return jsonify(chapter_data[:50])

def get_downloaded_chapters_for_title(title):
    """Check which chapters are downloaded for a given title"""
    actual_folder = find_manga_folder(title)
    folder = Path(MANGA_DIR) / actual_folder
    if not folder.exists():
        return {}
    
    downloaded = {}
    for ext in ['*.jpg', '*.png', '*.webp']:
        for f in folder.glob(ext):
            parts = f.stem.split('_')
            if len(parts) > 0 and parts[0].startswith('ch'):
                ch = parts[0][2:]
                if ch not in downloaded:
                    downloaded[ch] = []
                downloaded[ch].append(f.stem)
    
    return downloaded

def find_manga_folder(query):
    """Find manga folder by trying exact match, case-insensitive, or partial match"""
    # Normalize: replace - with space for comparison
    query_norm = query.lower().replace('-', ' ').replace('_', ' ')
    
    candidates = []
    
    for f in Path(MANGA_DIR).iterdir():
        if not f.is_dir():
            continue
        
        folder_norm = f.name.lower().replace('-', ' ').replace('_', ' ')
        has_images = len(list(f.glob('*.jpg'))) + len(list(f.glob('*.png'))) + len(list(f.glob('*.webp')))
        
        # Exact match (case sensitive) - highest priority regardless of images
        if f.name == query:
            candidates.append((10, f.name))
            continue
        
        # Exact match case-insensitive - high priority regardless of images
        if f.name.lower() == query.lower() or folder_norm == query_norm:
            candidates.append((9, f.name))
            continue
        
        # Partial match (with images)
        if query_norm in folder_norm or folder_norm in query_norm:
            if has_images:
                candidates.append((5, f.name))
            else:
                candidates.append((1, f.name))
    
    if candidates:
        candidates.sort(key=lambda x: -x[0])  # Sort by priority
        return candidates[0][1]
    
    return query

@app.route('/api/chapter_status/<path:title>')
def api_chapter_status(title):
    """Get download status for each chapter"""
    # Find actual folder name
    actual_title = find_manga_folder(title)
    
    downloaded = get_downloaded_chapters_for_title(actual_title)
    
    upscaled_folder = Path(UPSCALED_DIR) / actual_title
    upscaled = {}
    if upscaled_folder.exists():
        for f in upscaled_folder.glob('*.jpg'):
            parts = f.stem.split('_')
            if parts[0].startswith('ch'):
                ch = parts[0][2:]
                upscaled[ch] = True
    
    return jsonify({
        'downloaded': downloaded,
        'upscaled': upscaled,
        'folder': actual_title
    })

@app.route('/api/library')
def api_library():
    folders = []
    for f in Path(MANGA_DIR).iterdir():
        if f.is_dir():
            images = list(f.glob('*.png')) + list(f.glob('*.jpg')) + list(f.glob('*.webp'))
            upscaled = list(Path(UPSCALED_DIR).joinpath(f.name).glob('*.jpg'))
            folders.append({
                'id': f.name,
                'name': f.name,
                'count': len(images),
                'upscaled': len(upscaled)
            })
    
    folders.sort(key=lambda x: x['name'].lower())
    return jsonify(folders)

@app.route('/api/upscale_status')
def api_upscale_status():
    return jsonify(get_upscale_status())

@app.route('/api/upscale_progress/<upscale_id>')
def api_upscale_progress(upscale_id):
    status = get_upscale_status()
    return jsonify(status.get(upscale_id, {'status': 'not_found'}))

@app.route('/api/download_progress/<download_id>')
def api_download_progress(download_id):
    return jsonify(get_download_status(download_id))

@app.route('/api/download_chapter', methods=['POST'])
def download_chapter():
    data = request.get_json()
    chapterId = data.get('chapterId')
    title = data.get('title')
    chapter = data.get('chapter')
    
    # Find existing folder - prioritize folders with content
    actual_folder = find_manga_folder(title)
    folder = Path(MANGA_DIR) / actual_folder
    
    # If folder doesn't exist create it
    if not folder.exists():
        for f in Path(MANGA_DIR).iterdir():
            if f.is_dir() and (f.name.lower() == title.lower() or f.name.lower().replace('-', ' ') == title.lower()):
                folder = f
                break
        if not folder.exists():
            folder.mkdir(parents=True, exist_ok=True)
    
    # Get pages
    try:
        r = requests.get(f'https://api.mangadex.org/at-home/server/{chapterId}', timeout=30)
        resp_data = r.json()
        
        if resp_data.get('result') != 'ok':
            return jsonify({'error': 'Capítulo no disponible en MangaDex', 'pages': 0})
        
        pages = resp_data.get('chapter', {}).get('data', [])
        if not pages:
            return jsonify({'error': 'No hay páginas disponibles (MangaDex no tiene caché)', 'pages': 0})
        
        base = resp_data['baseUrl']
        hash_val = resp_data['chapter']['hash']
        
        downloaded_count = 0
        for i, page in enumerate(pages, 1):
            img_url = f"{base}/data/{hash_val}/{page}"
            time.sleep(0.2)
            
            r = requests.get(img_url)
            if r.status_code == 200:
                ext = page.split('.')[-1]
                filename = f"ch{int(float(chapter)):04d}_{i:03d}.{ext}"
                with open(folder / filename, 'wb') as f:
                    f.write(r.content)
                downloaded_count += 1
        
        return jsonify({'status': 'ok', 'chapter': chapter, 'folder': actual_folder, 'pages': downloaded_count})
    
    except Exception as e:
        return jsonify({'error': str(e), 'pages': 0})

@app.route('/api/download_cli', methods=['POST'])
def download_cli():
    """Run CLI download command"""
    data = request.get_json()
    title = data.get('title')
    chapters = data.get('chapters', 5)
    
    if not title:
        return jsonify({'status': 'error', 'message': 'title required'})
    
    import subprocess
    import threading
    
    venv_python = '/root/workspace/manga-upscaler/.venv/bin/python'
    download_id = title.replace(' ', '_')
    
    def run_download():
        set_download_status(download_id, {'status': 'starting', 'title': title, 'chapters': chapters})
        try:
            result = subprocess.run(
                [venv_python, 'manga_cli.py', 'download', title, '--chapters', str(chapters)],
                cwd='/root/workspace/manga-upscaler',
                capture_output=True,
                text=True,
                timeout=300
            )
            
            output = result.stdout + result.stderr
            if "0 English chapters" in output:
                set_download_status(download_id, {'status': 'no_chapters', 'message': 'No chapters available on MangaDex'})
            elif result.returncode == 0:
                set_download_status(download_id, {'status': 'complete', 'message': 'Download complete', 'output': output[:500]})
            else:
                set_download_status(download_id, {'status': 'error', 'message': result.stderr[:200]})
        except Exception as e:
            set_download_status(download_id, {'status': 'error', 'message': str(e)})
    
    set_download_status(download_id, {'status': 'started', 'title': title, 'chapters': chapters})
    threading.Thread(target=run_download, daemon=True).start()
    
    return jsonify({'status': 'started', 'title': title, 'download_id': download_id})

@app.route('/api/read_chapter', methods=['POST'])
def read_chapter():
    data = request.get_json()
    title = data.get('title')
    chapter = data.get('chapter')
    
    # Find actual folder
    actual_folder = find_manga_folder(title)
    
    # Try upscaled first, then original
    folder = Path(UPSCALED_DIR) / actual_folder
    if not folder.exists():
        folder = Path(MANGA_DIR) / actual_folder
    
    if not folder.exists():
        return jsonify({'pages': []})
    
    # Check for all image formats (jpg, png, webp)
    pages = []
    for ext in ['jpg', 'png', 'webp']:
        found = sorted([f.name for f in folder.glob(f'ch{int(float(chapter)):04d}_*.{ext}')])
        pages.extend(found)
    pages = sorted(pages)
    
    # Include subfolder in path
    return jsonify({'pages': [f"{actual_folder}/{p}" for p in pages], 'folder': actual_folder})

@app.route('/api/upscale_chapter', methods=['POST'])
def upscale_chapter():
    data = request.get_json()
    title = data.get('title')
    chapter = data.get('chapter')
    
    import torch
    from spandrel import ModelLoader
    from PIL import Image
    
    model_path = "/root/workspace/MangaJaNai/models/2x_IllustrationJaNai_V2standard_FDAT_M_unshuffle_40k.safetensors"
    
    # Find actual folder
    actual_folder = find_manga_folder(title)
    input_folder = Path(MANGA_DIR) / actual_folder
    output_folder = Path(UPSCALED_DIR) / actual_folder
    output_folder.mkdir(parents=True, exist_ok=True)
    
    if not input_folder.exists():
        return jsonify({'status': 'error', 'message': 'Folder not found'})
    
    # Get images for this chapter
    ch_prefix = f"ch{int(float(chapter)):04d}_"
    images = sorted(input_folder.glob(ch_prefix + "*.*"))
    
    if not images:
        return jsonify({'status': 'error', 'message': 'No images found'})
    
    total = len(images)
    upscale_id = f"{actual_folder}_ch{chapter}"
    
    # Set initial status
    set_upscale_status(upscale_id, {'status': 'upscaleing', 'current': 0, 'total': total, 'title': actual_folder, 'chapter': chapter})
    
    # Run in background
    def run_upscale():
        try:
            set_upscale_status(upscale_id, {'status': 'loading_model', 'current': 0, 'total': total, 'title': actual_folder, 'chapter': chapter})
            
            # Load model once
            model_desc = ModelLoader().load_from_file(model_path)
            model = model_desc.model.cuda().eval()
            model = model.half()
            
            # Quick warmup
            dummy = torch.randn(1, 3, 256, 256).half().cuda()
            with torch.no_grad():
                _ = model(dummy)
            torch.cuda.synchronize()
            del dummy
            
            processed = 0
            for img_path in images:
                try:
                    set_upscale_status(upscale_id, {
                        'status': 'upscaleing', 
                        'current': processed + 1, 
                        'total': total, 
                        'title': actual_folder, 
                        'chapter': chapter,
                        'progress_pct': int((processed / total) * 100)
                    })
                    
                    img = Image.open(img_path).convert('RGB')
                    w, h = img.size
                    
                    # Use model native - no resize, let model handle it
                    img_t = torch.from_numpy(np.array(img)).permute(2, 0, 1).unsqueeze(0).half().cuda() / 255.0
                    
                    with torch.no_grad():
                        out = model(img_t)
                    
                    # The model already outputs 2x, no interpolation needed
                    out_img = (out.squeeze().permute(1, 2, 0).cpu().float() * 255).clamp(0, 255).byte()
                    out_pil = Image.fromarray(out_img.numpy(), 'RGB')
                    
                    out_path = output_folder / (img_path.stem + ".jpg")
                    out_pil.save(out_path, quality=95, optimize=True)
                    processed += 1
                except Exception as e:
                    print(f"Error: {img_path}: {e}")
            
            set_upscale_status(upscale_id, {'status': 'complete', 'current': processed, 'total': total, 'title': actual_folder, 'chapter': chapter})
        except Exception as e:
            set_upscale_status(upscale_id, {'status': 'error', 'error': str(e)})
            print(f"Upscale error: {e}")
    
    threading.Thread(target=run_upscale, daemon=True).start()
    
    return jsonify({
        'status': 'started', 
        'total': total,
        'chapter': chapter,
        'upscale_id': upscale_id
    })

@app.route('/api/upscale_manga', methods=['POST'])
def upscale_manga():
    data = request.get_json()
    title = data.get('title')
    
    def run():
        import torch
        from spandrel import ModelLoader
        from PIL import Image
        
        model_path = "/root/workspace/MangaJaNai/models/2x_IllustrationJaNai_V2standard_FDAT_M_unshuffle_40k.safetensors"
        input_folder = Path(MANGA_DIR) / title
        output_folder = Path(UPSCALED_DIR) / title
        output_folder.mkdir(parents=True, exist_ok=True)
        
        # Get all images
        images = sorted(input_folder.glob('*.jpg')) + sorted(input_folder.glob('*.png'))
        
        if not images:
            return
        
        # Load model
        model_desc = ModelLoader().load_from_file(model_path)
        model = model_desc.model.cuda().eval()
        model = model.half()
        
        # Warmup
        dummy = torch.randn(1, 3, 256, 256).half().cuda()
        with torch.no_grad():
            _ = model(dummy)
        torch.cuda.synchronize()
        del dummy
        
        for img_path in images:
            try:
                img = Image.open(img_path).convert('RGB')
                img_t = torch.from_numpy(np.array(img)).permute(2, 0, 1).unsqueeze(0).half().cuda() / 255.0
                
                with torch.no_grad():
                    out = model(img_t)
                
                out_img = (out.squeeze().permute(1, 2, 0).cpu().float() * 255).clamp(0, 255).byte()
                out_pil = Image.fromarray(out_img.numpy(), 'RGB')
                
                out_path = output_folder / (img_path.stem + ".jpg")
                out_pil.save(out_path, quality=85)
            except Exception as e:
                print(f"Error: {e}")
    
    threading.Thread(target=run, daemon=True).start()
    return jsonify({'status': 'started'})

@app.route('/uploads/<path:filename>')
def serve_file(filename):
    # filename includes subfolder, e.g. "Title/ch0001_001.jpg"
    for d in [MANGA_DIR, UPSCALED_DIR]:
        path = Path(d) / filename
        if path.exists():
            return send_from_directory(path.parent, path.name)
    
    # Try reverse: search in all subdirectories
    parts = filename.split('/')
    if len(parts) >= 2:
        subfolder = parts[0]
        filename_only = '/'.join(parts[1:])
        for d in [MANGA_DIR, UPSCALED_DIR]:
            search_path = Path(d) / subfolder
            if search_path.is_dir():
                full_path = search_path / filename_only
                if full_path.exists():
                    return send_from_directory(search_path, filename_only)
    
    return 'Not found', 404

@app.route('/images/<path:filename>')
def serve_image(filename):
    # Serve images from any subdirectory
    for d in [MANGA_DIR, UPSCALED_DIR]:
        if '/' in filename:
            subfolder, fname = filename.split('/', 1)
            path = Path(d) / subfolder / fname
            if path.exists():
                return send_from_directory(path.parent, fname)
        else:
            path = Path(d) / filename
            if path.exists():
                return send_from_directory(d, filename)
    return 'Not found', 404

if __name__ == '__main__':
    print("🚀 Manga Reader Pro - http://localhost:5000")
    app.run(port=5000, debug=False)