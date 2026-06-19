"""Disk cache/proxy for external cover & banner images (TMDB, AniList, MangaDex).

The frontend used to point <img> tags straight at the external CDN, so every
reload re-fetched (or relied on the browser's RAM cache for) the same covers —
slow on cold loads and a steady RAM cost. This re-serves them from a local
file under ~/.cache/manga-upscaler/imgproxy/ after the first fetch, with a
long max-age so the browser's *disk* cache (not RAM) takes over after that.
"""
import hashlib
import mimetypes
from pathlib import Path as _Path
from urllib.parse import urlparse

import requests
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
}


def _ext_for(content_type, url):
    ext = mimetypes.guess_extension((content_type or '').split(';')[0].strip()) if content_type else None
    if not ext:
        ext = _Path(urlparse(url).path).suffix or '.jpg'
    return '.jpg' if ext == '.jpe' else ext


def _fetch_and_cache(url):
    """Download `url` into the disk cache if it isn't there yet. Returns the
    cached file path, or None on failure. Shared by the /api/img route (cold
    request from the browser) and warm() (proactive fill from the metadata
    backfill thread, so the detail page's first paint never has to wait on a
    live TMDB-art -> CDN round trip)."""
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256(url.encode()).hexdigest()
    existing = next(_CACHE_DIR.glob(f'{key}.*'), None)
    if existing and existing.stat().st_size > 0:
        return existing

    try:
        r = requests.get(url, timeout=_TIMEOUT, stream=True)
        r.raise_for_status()
    except Exception:
        return None

    dest = _CACHE_DIR / f'{key}{_ext_for(r.headers.get("Content-Type"), url)}'
    tmp = dest.with_suffix(dest.suffix + '.part')
    with open(tmp, 'wb') as f:
        for chunk in r.iter_content(65536):
            f.write(chunk)
    tmp.replace(dest)
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
    return send_file(str(dest), max_age=_MAX_AGE, conditional=True)
