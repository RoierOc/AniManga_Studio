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

# ── Capped, rotating log ──────────────────────────────────────────────────────
# Route all output (print() + tracebacks + flask/waitress logging) through a size-
# rotated file so the log can never grow unbounded again (a runaway error loop once
# flooded it to ~500k lines). 4 MB × 3 files ≈ 12 MB max.
try:
    import logging as _logging
    from logging.handlers import RotatingFileHandler as _RFH
    _LOG_PATH = os.environ.get('SERVER_LOG', '/tmp/flask.log')
    _lh = _RFH(_LOG_PATH, maxBytes=4_000_000, backupCount=2, encoding='utf-8', delay=True)
    _lh.setFormatter(_logging.Formatter('%(asctime)s %(levelname).1s %(message)s', '%H:%M:%S'))
    _logging.basicConfig(level=_logging.INFO, handlers=[_lh])

    class _StreamToLog:
        """Make print()/stderr writes go through the rotating logger."""
        def __init__(self, level): self._level = level; self._buf = ''
        def write(self, msg):
            self._buf += msg
            while '\n' in self._buf:
                line, self._buf = self._buf.split('\n', 1)
                if line.strip():
                    _logging.log(self._level, line)
        def flush(self): pass
        def isatty(self): return False
    sys.stdout = _StreamToLog(_logging.INFO)
    sys.stderr = _StreamToLog(_logging.ERROR)
except Exception:
    pass

from api.runtime import MANGA_DIR, UPSCALED_DIR, HIDDEN_MANGA_DIR, HIDDEN_UPSCALED_DIR, manga_dir, upscaled_dir

os.makedirs(str(MANGA_DIR), exist_ok=True)
os.makedirs(str(UPSCALED_DIR), exist_ok=True)
# Raíces de la biblioteca oculta: se crean vacías al arrancar para que la primera
# activación del modo oculto no falle. Vacías no delatan nada (mismo aspecto que
# cualquier carpeta de datos sin usar).
os.makedirs(str(HIDDEN_MANGA_DIR), exist_ok=True)
os.makedirs(str(HIDDEN_UPSCALED_DIR), exist_ok=True)

from flask import Flask, render_template, jsonify, request, send_from_directory, send_file
from flask_compress import Compress

app = Flask(__name__,
    template_folder=str(BASE_DIR.parent / 'templates'),
    static_folder=str(BASE_DIR.parent / 'static'),
    static_url_path='/static')
import secrets as _secrets
# Prefer SECRET_KEY from the environment (start_server.sh sources .env); fall back
# to a random per-process key rather than a known, committed default string.
app.secret_key = os.environ.get('SECRET_KEY') or _secrets.token_hex(32)
app.jinja_env.auto_reload = False
app.jinja_env.cache = None

Compress(app)

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
from api.anime_batch import anime_batch_bp
from api.subtitle import subtitle_bp
from api.subtitle_batch import subbatch_bp, load_batches
from api.imgproxy import imgproxy_bp
from api.backup import backup_bp
from api.tags import tags_bp
from api.transplant import transplant_bp
from api.import_cbz import import_bp
from api.discovery import discovery_bp
from api.md_updates import md_updates_bp
from api.roots import roots_bp
from api.anime_upscale import anime_upscale_bp

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
app.register_blueprint(anime_batch_bp, url_prefix='/api/anime/batch')
app.register_blueprint(anime_upscale_bp, url_prefix='/api/anime/upscale')
app.register_blueprint(subtitle_bp, url_prefix='/api/subtitle')
app.register_blueprint(subbatch_bp, url_prefix='/api/subtitle/batch')
app.register_blueprint(imgproxy_bp, url_prefix='/api/img')
app.register_blueprint(backup_bp, url_prefix='/api/backup')
app.register_blueprint(tags_bp, url_prefix='/api/tags')
app.register_blueprint(transplant_bp, url_prefix='/api/transplant')
app.register_blueprint(import_bp, url_prefix='/api/import')
app.register_blueprint(discovery_bp, url_prefix='/api/discovery')
app.register_blueprint(md_updates_bp, url_prefix='/api/md_updates')
app.register_blueprint(roots_bp, url_prefix='/api/roots')
# Acceso remoto. Sin prefijo: `guardia` tiene que ver TODAS las rutas, no un subárbol, y
# /api/hello es la tarjeta de presentación que el móvil pide antes de tener token.
from api.auth import auth_remote_bp, guardia
app.register_blueprint(auth_remote_bp)
app.before_request(guardia)
# DESPUÉS de la guardia: decide qué RAÍZ se sirve, no si se atiende. Ver `modo_por_peticion`.
from api.config_store import modo_por_peticion
app.before_request(modo_por_peticion)
from api.library_health import health_bp
app.register_blueprint(health_bp, url_prefix='/api/health')
from api.bridge import bridge_bp
app.register_blueprint(bridge_bp, url_prefix='/api/bridge')
from api.novels import novels_bp
app.register_blueprint(novels_bp, url_prefix='/api/novels')
from api.stream import stream_bp
app.register_blueprint(stream_bp, url_prefix='/api/stream')
from api.storage import storage_bp
app.register_blueprint(storage_bp, url_prefix='/api/storage')
from api.config_store import config_bp
app.register_blueprint(config_bp, url_prefix='/api/config')
from api.sync import sync_bp, start_auto_sync
app.register_blueprint(sync_bp, url_prefix='/api/sync')
start_auto_sync()   # copia semanal del perfil al repo privado (no hace nada sin repo+token)
load_batches()      # lotes de subtítulos de antes del reinicio (los vivos se declaran rotos)
from api.media import media_bp
app.register_blueprint(media_bp, url_prefix='/api/media')
# Agenda, cola e historial de Cine. Módulo aparte (media.py ya pasa de 1100 líneas) y mismo
# prefijo: para quien consume la API son parte de Series y Películas, no otra sección.
from api.media_agenda import media_agenda_bp
app.register_blueprint(media_agenda_bp, url_prefix='/api/media')
# Arte de alta resolución (TMDB) para Cine: Sonarr sirve TVDB a 680x1000 y sin logo.
from api.media_art import media_art_bp
app.register_blueprint(media_art_bp, url_prefix='/api/media')
# «Para ti»: recomendaciones agregadas sobre tu biblioteca. Módulo propio (anime.py ya pasa de
# 4400 líneas) y perezoso en su import de anime para no cerrar un ciclo entre los dos.
from api.for_you import for_you_bp
app.register_blueprint(for_you_bp, url_prefix='/api/for_you')
# Retrospectiva («tu mes» / «tu año»): agrega el historial archivado por `history_store`.
from api.retrospective import retro_bp
app.register_blueprint(retro_bp, url_prefix='/api/retrospective')
# Apagar el PC desde el móvil. Módulo propio: es lo ÚNICO de la API que apaga la máquina.
from api.power import power_bp
app.register_blueprint(power_bp, url_prefix='/api/power')


# Suwayomi es on-demand (ver sources.py: ensure_suwayomi + reaper de inactividad).
# SUWAYOMI_EAGER=1 restaura el comportamiento anterior: JVM siempre encendida.
if os.environ.get('SUWAYOMI_EAGER') == '1':
    from api.sources import ensure_suwayomi as _ensure_suwayomi
    import threading as _threading
    _threading.Thread(target=_ensure_suwayomi, daemon=True).start()
    print('[startup] Suwayomi eager start requested', flush=True)


# Precalienta la biblioteca de anime en segundo plano: el primer /api/anime/library en frío
# cuesta ~1 s (escaneo DrvFS + qBittorrent) y es la vista de aterrizaje — sin esto, cada
# arranque son un segundo de esqueletos. El test_client ejecuta la vista real, así que llena
# exactamente los mismos cachés por carpeta que la petición del frontend.
def _warm_anime_library():
    from api.observability import swallow
    with swallow('anime', 'warm_library'):
        app.test_client().get('/api/anime/library')

import threading as _t
_t.Thread(target=_warm_anime_library, daemon=True).start()


# ── Ciclo de vida (app de escritorio / sidecar) ───────────────────────────────
# /health: readiness probe para el shell Tauri (splash → poll → cargar UI).
# /shutdown: cierre limpio pedido por el shell al cerrar la ventana — apaga
# Suwayomi y termina el proceso. Solo acepta peticiones desde localhost.

@app.route('/health')
def health_check():
    from flask import jsonify
    return jsonify({'ok': True, 'app': 'manga-upscaler', 'pid': os.getpid()})


def _stop_suwayomi():
    import subprocess
    script = BASE_DIR.parent / 'suwayomi' / 'stop.sh'
    if script.exists():
        try:
            subprocess.run(['bash', str(script)], timeout=15,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as e:
            print(f'[shutdown] Suwayomi stop failed: {e}', flush=True)


def _stop_tracked_mpv():
    """Cierra los mpv.exe (Windows) que la app lanzó para el anime — así el cierre por
    /shutdown también los libera aunque no pase por el shell nativo. Best-effort."""
    import subprocess, shutil
    pid_file = '/tmp/manga_mpv_pids'
    if not os.path.exists(pid_file) or not shutil.which('taskkill.exe'):
        return
    try:
        with open(pid_file) as f:
            pids = [ln.strip() for ln in f if ln.strip()]
        for pid in pids:
            try:
                subprocess.run(['taskkill.exe', '/PID', pid, '/F'], timeout=5,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except Exception:
                pass
        os.remove(pid_file)
    except Exception as e:
        print(f'[shutdown] mpv stop failed: {e}', flush=True)


@app.route('/shutdown', methods=['POST'])
def shutdown_server():
    from flask import request, jsonify, abort
    if request.remote_addr not in ('127.0.0.1', '::1'):
        abort(403)

    def _die():
        _stop_suwayomi()
        _stop_tracked_mpv()
        print('[shutdown] bye', flush=True)
        os._exit(0)

    import threading
    threading.Timer(0.3, _die).start()  # deja salir la respuesta HTTP antes de morir
    return jsonify({'ok': True, 'stopping': True})


@app.route('/library')
def mobile_library():
    from flask import Response
    html_path = BASE_DIR.parent / 'templates' / 'library.html'
    with open(html_path, 'r', encoding='utf-8') as f:
        content = f.read()
    return Response(content, mimetype='text/html', headers={'Cache-Control': 'no-cache'})

# ── Frontend v2 (Vite SPA) ────────────────────────────────────────────────────
# Built assets live in frontend/dist. Flask serves them at / in production.
# The legacy single-file app stays available at /legacy as a fallback.
FRONTEND_DIST = BASE_DIR.parent / 'frontend' / 'dist'

def _serve_spa():
    from flask import Response
    index_file = FRONTEND_DIST / 'index.html'
    if not index_file.exists():
        # dist not built yet — fall back to the legacy UI
        return _serve_legacy()
    return Response(index_file.read_text(encoding='utf-8'),
                    mimetype='text/html', headers={'Cache-Control': 'no-cache'})

def _serve_legacy():
    from flask import Response
    html_path = BASE_DIR.parent / 'templates' / 'index.html'
    with open(html_path, 'r', encoding='utf-8') as f:
        content = f.read()
    return Response(content, mimetype='text/html', headers={'Cache-Control': 'no-cache'})

@app.route('/')
def index():
    return _serve_spa()

@app.route('/legacy')
def legacy_index():
    return _serve_legacy()

@app.route('/assets/<path:filename>')
def serve_spa_assets(filename):
    return send_from_directory(str(FRONTEND_DIST / 'assets'), filename)

@app.route('/fonts/<path:filename>')
def serve_spa_fonts(filename):
    # Fuentes autoalojadas (frontend/public/fonts → dist/fonts). Inmutables por contenido no,
    # pero cambian casi nunca: caché de un día para no re-pedirlas en cada arranque.
    r = send_from_directory(str(FRONTEND_DIST / 'fonts'), filename)
    r.headers['Cache-Control'] = 'public, max-age=86400'
    return r

@app.route('/favicon.svg')
def serve_favicon():
    return send_from_directory(str(FRONTEND_DIST), 'favicon.svg',
                               mimetype='image/svg+xml')

@app.route('/sw.js')
def serve_sw():
    return send_from_directory(app.static_folder, 'sw.js',
                               mimetype='application/javascript')

@app.route('/static/<path:filename>')
def serve_static(filename):
    return send_from_directory(app.static_folder, filename)

_PAGE_MAX_AGE = 7 * 24 * 3600  # chapter pages are immutable once downloaded/upscaled

import hashlib as _hashlib
_READER_CACHE = Path.home() / '.cache' / 'manga-upscaler' / 'reader'

def _serve_page_file(directory, name):
    """Sirve una página del lector. Con ?w=N y si la imagen es MÁS ANCHA que N,
    devuelve una versión reescalada (JPEG) cacheada en disco por (ruta, mtime, N).
    Motivo (lag del lector 4K en la shell nativa): el lector muestra la página a
    ~1000px pero el archivo escalado mide ~5760px → el WebView2 decodifica/pinta
    30× más píxeles de los que se ven. Servir al tamaño del viewport lo elimina;
    a resolución completa (zoom/original) el lector pide sin ?w. Cualquier fallo
    cae al archivo original: nunca romper la lectura."""
    try:
        w = int(request.args.get('w', 0))
    except (TypeError, ValueError):
        w = 0
    if w <= 0:
        return send_from_directory(str(directory), name, max_age=_PAGE_MAX_AGE)
    try:
        from PIL import Image
        src = Path(directory) / name
        st = src.stat()
        key = _hashlib.sha256(f'{src}|{int(st.st_mtime)}|{w}'.encode()).hexdigest()
        cached = _READER_CACHE / f'{key}.jpg'
        if not (cached.exists() and cached.stat().st_size > 0):
            with Image.open(src) as im:
                if im.width <= w:
                    return send_from_directory(str(directory), name, max_age=_PAGE_MAX_AGE)
                h = round(im.height * w / im.width)
                if im.mode not in ('RGB', 'L'):
                    im = im.convert('RGB')
                im = im.resize((w, h), Image.LANCZOS)
                _READER_CACHE.mkdir(parents=True, exist_ok=True)
                tmp = cached.with_suffix('.tmp')
                im.save(tmp, 'JPEG', quality=88, optimize=True)
                tmp.replace(cached)
        return send_file(str(cached), mimetype='image/jpeg', max_age=_PAGE_MAX_AGE)
    except Exception:
        return send_from_directory(str(directory), name, max_age=_PAGE_MAX_AGE)

# La URL de una página es `<obra>/<archivo>` y NO lleva disco a propósito: si lo llevara,
# mover una obra de disco invalidaría todo enlace guardado (progreso, marcadores, caché del
# navegador). Resolver contra TODAS las raíces es lo que hace que una obra repartida entre
# C: y D: se lea como una sola. Ver api/roots.py.
def _find_page(filename: str, prefer_upscaled: bool):
    """🔴 «Escalada si existe» sólo es cierto si se prueban TODAS las extensiones primero.

    El escalador escribe siempre `.jpg`, pero el original puede ser `.webp` (todo lo bajado de
    MangaDex lo es) y el listado de páginas nombra los ORIGINALES. Buscando la extensión pedida en
    escalados y originales antes de probar otras —que es lo que hacía `find_file`+bucle—, el
    `.webp` original SIEMPRE ganaba y la versión escalada no se servía nunca: medido, una página
    que en disco mide 4500x6400 llegaba al cliente como 1125x1600. Horas de GPU invisibles.

    Ahora el orden es por CARPETA y no por extensión: primero todos los formatos en escalados,
    después todos en originales."""
    orden = ('upscaled', 'manga') if prefer_upscaled else ('manga', 'upscaled')
    for clave in orden:
        p = _find_page_in(filename, clave)
        if p:
            return p
    return None


def _find_page_in(filename: str, key: str):
    """Igual, pero forzando originales o escalados (modo comparar)."""
    from api.roots import roots as _roots
    from api.runtime import safe_child
    base = Path(filename)
    for ext in ('',) + ('.jpg', '.png', '.webp', '.jpeg'):
        cand = base if not ext else base.with_suffix(ext)
        for r in _roots():
            p = safe_child(r[key], cand)
            if p and p.is_file():
                return p
    return None


@app.route('/uploads/original/<path:filename>')
def serve_upload_original(filename):
    """Siempre el original sin escalar (modo comparar), venga del disco que venga."""
    p = _find_page_in(filename, 'manga')
    return _serve_page_file(p.parent, p.name) if p else ('Not found', 404)

@app.route('/uploads/upscaled/<path:filename>')
def serve_upload_upscaled(filename):
    """Siempre la versión escalada (modo comparar), venga del disco que venga."""
    p = _find_page_in(filename, 'upscaled')
    return _serve_page_file(p.parent, p.name) if p else ('Not found', 404)

@app.route('/uploads/<path:filename>')
def serve_upload(filename):
    # Escalada si existe; si no, el original.
    p = _find_page(filename, prefer_upscaled=True)
    return _serve_page_file(p.parent, p.name) if p else ('Not found', 404)

if __name__ == '__main__':
    print("🚀 Manga Upscaler Pro - http://localhost:5101")
    try:
        from waitress import serve
        print("   WSGI server: waitress")
        # Dos puertos, no uno. El 5103 es la BOCA REMOTA: todo lo que entra por ahí se trata como
        # remoto y necesita token, pase la IP que pase.
        #
        # Hace falta porque el único camino que tiene esta máquina para dejar entrar a un móvil es
        # un `netsh portproxy` de Windows hacia el bucle local (el modo `mirrored` de WSL no
        # entrega tráfico externo). Y ese puente reescribe el origen a 127.0.0.1: sin esta
        # separación, `remote_addr` diría "local" para TODA la red y el guardián no serviría de
        # nada — comprobado en un dispositivo real antes de escribir esto.
        #
        # El puerto de escucha no lo elige el cliente, así que es una señal en la que sí se puede
        # confiar; la IP de origen y la cabecera `Host` no lo son.
        from api.auth import PUERTO_REMOTO
        serve(app, listen=f'0.0.0.0:5101 127.0.0.1:{PUERTO_REMOTO}', threads=8)
    except ImportError:
        print("   WSGI server: Flask dev (install waitress for production)")
        app.run(port=5101, debug=False, host='0.0.0.0')
