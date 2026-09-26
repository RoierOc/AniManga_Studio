"""Disk cache/proxy for external cover & banner images (TMDB, AniList, MangaDex).

The frontend used to point <img> tags straight at the external CDN, so every
reload re-fetched (or relied on the browser's RAM cache for) the same covers —
slow on cold loads and a steady RAM cost. This re-serves them from a local
file under ~/.cache/manga-upscaler/imgproxy/ after the first fetch, with a
long max-age so the browser's *disk* cache (not RAM) takes over after that.
"""
import hashlib
import mimetypes
import os
import threading
from pathlib import Path as _Path
from urllib.parse import urlparse

from api.resilient_http import http as requests  # retry + backoff + per-host rate limiting
from flask import Blueprint, request, send_file, redirect

imgproxy_bp = Blueprint('imgproxy', __name__)

_CACHE_DIR = _Path.home() / '.cache' / 'manga-upscaler' / 'imgproxy'
_TIMEOUT = 10
_MAX_AGE = 2592000  # 30 days — these assets don't change once published

# Only ever fetch/cache from the CDNs this app actually uses art from — never
# an open proxy for arbitrary URLs. The frontend's imgProxy() already checks
# this same allowlist before ever pointing an <img> at this route, but the
# backend must not trust that — without this check a crafted ?u= could turn
# this endpoint into an open redirect to any attacker-chosen URL.
_ALLOWED_HOSTS = {
    'image.tmdb.org',
    's4.anilist.co',
    'uploads.mangadex.org',
    'images.mangabaka.dev',   # Manga Hub: portadas raw del agregador
    'cdn.mangabaka.dev',      # Manga Hub: variantes dimensionadas (x350@2 ≈ 700px) — las que se usan
    'artworks.thetvdb.com',   # Series/pelis: es lo que devuelve Sonarr en `remoteUrl`
    'cdn.myanimelist.net',    # Jikan/MAL: portadas alternativas del selector de anime
}


# TMDB codifica el tamaño EN la ruta (`/t/p/w780/abc.jpg`). Las entradas de biblioteca guardan
# la URL ya formada, así que las que se cachearon cuando pedíamos `w1280` se quedarían en
# w1280 para siempre: `_ensure_meta` sólo rellena `banner` si falta. Esto las sube al vuelo.
_TMDB_HD = {'w300': 'w780', 'w500': 'w780', 'w780': 'w1280', 'w1280': 'original'}


def hd_url(url: str, *, hero: bool = False) -> str:
    """Sube una URL de arte al tamaño mayor razonable. Vale para URLs ya guardadas.

    `hero=True` para arte que se pinta a SANGRE (fondo a pantalla completa) → `original`.
    Sin `hero`, sube un escalón: suficiente para tarjetas y pósters, sin bajar 4 MB por miniatura.

    AniList SÍ lleva el tamaño en la ruta, y además desplazado respecto al nombre del campo:
    `extraLarge` sirve `/cover/large/`, `large` sirve `/cover/medium/`. Cuando a una serie le
    falta `extraLarge` (pasa: medido con Frieren en «Para ti»), `_cover` cae a `large` y acabas
    sirviendo una portada de 230 px en una tarjeta de 480. Subir la RUTA lo arregla sin pedir
    nada de nuevo a la API.
    """
    if not url:
        return url
    if 'image.tmdb.org' in url:
        for size, bigger in _TMDB_HD.items():
            seg = f'/t/p/{size}/'
            if seg in url:
                return url.replace(seg, '/t/p/original/' if hero else f'/t/p/{bigger}/')
        return url
    if 's4.anilist.co' in url:
        for chico in ('/cover/small/', '/cover/medium/'):
            if chico in url:
                return url.replace(chico, '/cover/large/')
    return url


def _hd_undo(url: str) -> str:
    """Deshace lo que hizo `hd_url` para AniList: el tamaño que la API dio de verdad."""
    return url.replace('/cover/large/', '/cover/medium/') if 's4.anilist.co' in url else url


def _ext_for(content_type, url):
    ext = mimetypes.guess_extension((content_type or '').split(';')[0].strip()) if content_type else None
    if not ext:
        ext = _Path(urlparse(url).path).suffix or '.jpg'
    if ext == '.jpe':
        ext = '.jpg'
    # Acotado a `_EXTS` a propósito: `_lookup` busca por extensión, así que escribir una que no
    # esté en la lista haría que el fichero existiera y no se encontrara nunca — se re-descargaría
    # en cada petición, en silencio. Lo que no reconocemos se guarda como .jpg (Pillow decide por
    # el contenido, no por el nombre).
    return ext if ext in _EXTS else '.jpg'


# El nombre en caché es `<sha256>.<ext>`, y la extensión sólo se conoce tras descargar. Antes eso
# se resolvía con `_CACHE_DIR.glob(f'{key}.*')`, o sea **listar el directorio entero en cada
# petición**: con 7796 ficheros son 3,4 ms de Python con el GIL cogido, y una rejilla pide 18
# portadas a la vez. MEDIDO: 1 hilo 8 ms, 2 hilos 13 ms, 4 hilos 145 ms, 8 hilos 1881 ms — el
# colapso es superlineal porque los hilos se pelean por el GIL haciendo `scandir`. Un CSS estático
# de 199 KB por el mismo servidor: 17 ms con 8 hilos.
#
# Se prueban las extensiones posibles con `exists()` (0,007 ms), que es lo que se debió hacer
# siempre, y se memoriza el acierto para no repetir ni eso. El índice sólo cachea aciertos: un
# fallo vuelve a mirar el disco, así que un fichero que aparece después (lo escribe `warm()` desde
# otro hilo) se ve enseguida.
_EXTS = ('.jpg', '.png', '.webp', '.jpeg', '.gif', '.avif')
_INDEX = {}
_INDEX_LOCK = threading.Lock()


def _lookup(key):
    """Ruta cacheada para `key`, o None si aún no está en disco."""
    hit = _INDEX.get(key)
    if hit is not None:
        try:
            if hit.stat().st_size > 0:
                return hit
        except OSError:
            pass                      # lo borraron por debajo: se re-resuelve
        with _INDEX_LOCK:
            _INDEX.pop(key, None)
    for ext in _EXTS:
        p = _CACHE_DIR / f'{key}{ext}'
        try:
            if p.stat().st_size > 0:
                with _INDEX_LOCK:
                    _INDEX[key] = p
                return p
        except OSError:
            continue
    return None


def _remember(path):
    with _INDEX_LOCK:
        _INDEX[path.stem] = path


def _fetch_and_cache(url):
    """Download `url` into the disk cache if it isn't there yet. Returns the
    cached file path, or None on failure. Shared by the /api/img route (cold
    request from the browser) and warm() (proactive fill from the metadata
    backfill thread, so the detail page's first paint never has to wait on a
    live TMDB-art -> CDN round trip)."""
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256(url.encode()).hexdigest()

    hit = _lookup(key)
    if hit is not None:
        return hit

    try:
        r = requests.get(url, timeout=_TIMEOUT, stream=True)
        # Subir la ruta de AniList a `/cover/large/` supone que ese tamaño EXISTE, y en las fichas
        # viejas no: el único fichero vive en `/cover/medium/` y la URL mejorada da 404, así que la
        # portada desaparecía (visto en «Kaguya-hime: Taketori Monogatari», id 14471). Se vuelve a
        # la que dio AniList. Cuesta una petición sólo en el caso que ya estaba roto.
        if r.status_code == 404:
            original = _hd_undo(url)
            if original != url:
                r = requests.get(original, timeout=_TIMEOUT, stream=True)
        r.raise_for_status()
    except Exception:
        return None

    dest = _CACHE_DIR / f'{key}{_ext_for(r.headers.get("Content-Type"), url)}'
    # Unique tmp per writer: the blur-up thumb and the full cover are requested
    # in parallel for the same uncached URL, and both land here concurrently.
    tmp = dest.with_suffix(dest.suffix + f'.{os.getpid()}-{threading.get_ident()}.part')
    with open(tmp, 'wb') as f:
        for chunk in r.iter_content(65536):
            f.write(chunk)
    tmp.replace(dest)
    _remember(dest)
    return dest


def warm(url):
    """Best-effort proactive cache fill, called from anime.py's metadata
    backfill once a banner/logo/cover URL is resolved. Silently no-ops for
    falsy URLs or hosts outside the allowlist — never warms an arbitrary URL."""
    if not url:
        return
    try:
        if urlparse(url).netloc.lower() not in _ALLOWED_HOSTS:
            return
        _fetch_and_cache(url)
    except Exception:
        pass


# ── Escalera de tamaños ───────────────────────────────────────────────────────────────────────
# Anchos DISCRETOS: cada uno es un fichero en caché, así que aceptar cualquier `w` significaría
# un fichero por píxel pedido (y un vector para llenar el disco desde fuera). El cliente pide el
# ancho que necesita y aquí se sube al peldaño siguiente.
#   28/48/96 → blur-up (se pintan desenfocados: la calidad da igual)
#   320-900  → la imagen REAL de una tarjeta. Una portada 2:3 a 14rem mide ~225-295 px CSS, que
#              en pantalla HiDPI (dpr 2) son ~590 px → 640 es el peldaño normal de una tarjeta.
_LADDER = (28, 48, 96, 320, 480, 512, 640, 900)
_BLUR_MAX = 96          # por encima de esto la imagen SE VE: sube la calidad

# Sólo se re-codifica si el destino es MUCHO más pequeño que el original. MEDIDO: las portadas de
# AniList llegan a 780 px y 223 KB, ya bien comprimidas; rehacerlas a 640 da 206 KB — un 8% menos
# a cambio de una segunda pasada de JPEG (pérdida de generación). No compensa: por debajo de este
# umbral se sirve el original, que pesa casi igual y se ve mejor. Donde SÍ compensa es lo que de
# verdad viene enorme (pósters de TMDB de 1024-1200 px y >1 MB) y las pantallas sin HiDPI.
_WORTH_IT = 0.75


def _snap(width: int) -> int:
    return next((w for w in _LADDER if width <= w), _LADDER[-1])


def _render_variant(src, key: str, width: int):
    """Rendición JPEG de `src` a `width` px de ancho, cacheada en disco. Devuelve la ruta, o
    None si no se puede decodificar (el llamante sirve entonces el original).

    NUNCA amplía, y tampoco reduce por poco (ver `_WORTH_IT`): en ambos casos devuelve None y el
    llamante sirve el original, que se ve mejor y pesa lo mismo.
    """
    # Dos extensiones posibles porque hay dos formatos de salida (ver abajo): el acierto de caché
    # tiene que mirar las dos, o una imagen con transparencia se re-generaría en cada visita.
    jpg = _CACHE_DIR / f'{key}_w{width}.thumb.jpg'
    webp = _CACHE_DIR / f'{key}_w{width}.thumb.webp'
    for p in (jpg, webp):
        if p.exists() and p.stat().st_size > 0:
            return p
    try:
        from PIL import Image
        with Image.open(src) as im:
            # El blur-up SIEMPRE se genera (28 px desde 780 es una reducción brutal); para los
            # tamaños visibles, sólo si el recorte merece la pena.
            if width > _BLUR_MAX and width > im.width * _WORTH_IT:
                return None
            if im.width <= width:
                return None
            h = max(1, round(im.height * (width / float(im.width))))
            q = 70 if width <= _BLUR_MAX else 86
            # ¿Lleva transparencia? Los LOGOS de TMDB (el rótulo del título del hero) son PNG con
            # alfa, y convertirlos a RGB les pone un fondo NEGRO sólido encima del arte. Por eso
            # hasta ahora se servían sin pasar por aquí: a tamaño original, desde el CDN y sin
            # caché — 1,2 MB para pintarlos en una caja de 543 px. Con alfa se sale en WebP, que
            # la conserva y pesa una fracción.
            con_alfa = 'A' in im.getbands() or (im.mode == 'P' and 'transparency' in im.info)
            if con_alfa:
                out = webp
                im = im.convert('RGBA').resize((width, h), Image.LANCZOS)
            else:
                out = jpg
                # LANCZOS: las portadas de manga llevan TEXTO, y con bicúbico el título se
                # emborrona al reducir. `subsampling=0` (4:4:4) evita el sangrado en los rótulos.
                im = im.convert('RGB').resize((width, h), Image.LANCZOS)
            tmp = out.with_suffix(f'.{os.getpid()}-{threading.get_ident()}.part')
            if con_alfa:
                # `method=4`: el 6 cuesta 3,4× para ahorrar un 0,9 % (medido al exportar tomos).
                im.save(tmp, 'WEBP', quality=q, method=4)
            else:
                im.save(tmp, 'JPEG', quality=q, subsampling=0, optimize=True, progressive=True)
        tmp.replace(out)
        return out
    except Exception:
        return None


def render_cached(src, cache_key: str, width: int):
    """Igual que `_render_variant` pero para ficheros de FUERA de este caché (p.ej. el cover.jpg
    de una carpeta de la biblioteca). `cache_key` debe incluir algo que cambie con el contenido
    —el mtime— o una portada nueva se serviría con la rendición vieja."""
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return _render_variant(src, hashlib.sha256(cache_key.encode()).hexdigest(), _snap(width))


def invalidate(url: str) -> int:
    """Remove one allowlisted source image and all local size variants from the proxy cache."""
    parsed = urlparse(url or '')
    if parsed.scheme not in ('http', 'https') or parsed.netloc.lower() not in _ALLOWED_HOSTS:
        return 0

    key = hashlib.sha256(url.encode()).hexdigest()
    with _INDEX_LOCK:
        _INDEX.pop(key, None)

    paths = [_CACHE_DIR / f'{key}{ext}' for ext in _EXTS]
    paths.extend(
        _CACHE_DIR / f'{key}_w{width}.thumb.{ext}'
        for width in _LADDER for ext in ('jpg', 'webp')
    )
    removed = 0
    for path in paths:
        try:
            path.unlink()
            removed += 1
        except FileNotFoundError:
            pass
    return removed


@imgproxy_bp.route('')
def proxy():
    url = request.args.get('u', '')
    parsed = urlparse(url)
    if parsed.scheme not in ('http', 'https'):
        return ('', 400)
    if parsed.netloc.lower() not in _ALLOWED_HOSTS:
        return ('', 404)

    dest = _fetch_and_cache(url)
    if dest is None:
        # Don't break the <img> over a fetch failure — fall back to the original CDN.
        return redirect(url, code=302)

    try:
        width = int(request.args.get('w', 0))
    except ValueError:
        width = 0
    if width > 0:
        thumb = _render_variant(dest, hashlib.sha256(url.encode()).hexdigest(), _snap(width))
        if thumb is not None:
            return send_file(str(thumb), max_age=_MAX_AGE, conditional=True)
        # original indescifrable, o ya más pequeño que lo pedido → se sirve tal cual

    return send_file(str(dest), max_age=_MAX_AGE, conditional=True)
