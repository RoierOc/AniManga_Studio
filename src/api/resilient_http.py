#!/usr/bin/env python3
"""
Shared resilient HTTP client.

One pooled `requests.Session` per process wrapped with:
  - retry + exponential backoff (with jitter) on network errors and on retryable
    status codes (429, 500, 502, 503, 504),
  - honoring of the `Retry-After` header (integer seconds),
  - per-host rate limiting (a minimum interval between requests to the same host),
  - per-host default headers, retry count and timeout overrides.

Why: every external integration (MangaDex API, MangaDex@Home image CDN, AniList,
Suwayomi, Jikan, TMDB, Nyaa) was doing its own bare `requests.get/post` with, at
best, an ad-hoc 429 loop copy-pasted in two places. A transient 429 or a dropped
connection silently lost a page (in the version comparator, the Taller anchor,
the cover fetch…). This centralizes that fragility.

Semantics are a safe SUPERSET of plain `requests`: `get()/post()/delete()` return
a `requests.Response` (the last one seen, even after retries are exhausted). If
*every* attempt raised a network exception, the last exception is re-raised —
matching the previous uncaught behavior at the call sites. So existing code that
does `client.get(...).json()` or checks `r.status_code` keeps working unchanged.

Per-host quirks preserved on purpose:
  - MangaDex (`*.mangadex.org`) must NOT send a browser User-Agent — its WAF 400s
    any Chrome/Firefox UA. We never override the UA there, so the underlying
    session's default `python-requests/x.y` is used (allowed through).
  - Nyaa needs a browser UA, so its host config sets one.
"""

import random
import threading
import time
from urllib.parse import urlparse

import requests
from requests.adapters import HTTPAdapter

# Statuses worth retrying: rate limit + transient upstream/proxy errors.
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class _HostConfig:
    __slots__ = ("retries", "backoff", "max_backoff", "min_interval", "headers", "timeout")

    def __init__(self, retries=3, backoff=1.0, max_backoff=30.0,
                 min_interval=0.0, headers=None, timeout=None):
        self.retries = retries            # max attempts
        self.backoff = backoff            # base seconds for exponential backoff
        self.max_backoff = max_backoff    # cap per-sleep
        self.min_interval = min_interval  # min seconds between requests to this host
        self.headers = headers or {}      # default headers (caller's win on conflict)
        self.timeout = timeout            # default timeout if caller omits one


_DEFAULT = _HostConfig(retries=3, backoff=1.0, min_interval=0.0)

# Registered per-host tuning. Keys are matched against the request netloc by exact
# match first, then by suffix (so `api.mangadex.org` also picks up `*.mangadex.org`).
_HOSTS = {
    # MangaDex JSON API — gentle spacing avoids the per-IP 429 during batch lookups.
    "api.mangadex.org":   _HostConfig(retries=4, min_interval=0.30, timeout=15),
    # The rest of MangaDex (auth, at-home assignment) — same domain, no UA override.
    "mangadex.org":       _HostConfig(retries=3, min_interval=0.20),
    # AniList GraphQL — degraded limit ~30/min; honor Retry-After, space requests out.
    "graphql.anilist.co": _HostConfig(retries=4, min_interval=0.50, timeout=10),
    # Jikan (MAL mirror) — ~3 req/s / 60 per minute.
    "api.jikan.moe":      _HostConfig(retries=3, min_interval=0.60),
    # MangaBaka (Manga Hub · agregador de metadatos) — 30/min búsqueda: espaciar ~2.1s.
    "api.mangabaka.org":  _HostConfig(retries=3, min_interval=2.1, timeout=15),
    # MangaUpdates API (Manga Hub · refuerzo de taxonomía) — devuelve 429 al pasarse.
    "api.mangaupdates.com": _HostConfig(retries=3, min_interval=0.40, timeout=15),
    # TMDB images/metadata.
    "api.themoviedb.org": _HostConfig(retries=3, min_interval=0.10),
    # Suwayomi (local) — cheap to retry, no spacing needed.
    "127.0.0.1:4567":     _HostConfig(retries=2, min_interval=0.0),
    "localhost:4567":     _HostConfig(retries=2, min_interval=0.0),
    # Nyaa needs a browser UA (default python UA gets blocked there).
    "nyaa.si":            _HostConfig(retries=3, min_interval=0.0,
                                      headers={"User-Agent": "Mozilla/5.0"}),
}


def _parse_retry_after(value):
    """Retry-After is usually an integer number of seconds. Ignore HTTP-date form
    (rare for these APIs) and fall through to normal backoff if unparseable."""
    if not value:
        return None
    try:
        secs = int(str(value).strip())
        return max(0, min(secs, 60))
    except (TypeError, ValueError):
        return None


class ResilientHTTP:
    """A thin, thread-safe wrapper over a single pooled requests.Session."""

    def __init__(self):
        self._session = requests.Session()
        adapter = HTTPAdapter(pool_connections=16, pool_maxsize=32)
        self._session.mount("http://", adapter)
        self._session.mount("https://", adapter)
        # Per-host pacing state: netloc -> (lock, last_request_monotonic).
        self._pace_lock = threading.Lock()
        self._pace = {}
        # netloc -> monotonic del último request (escritura atómica por el GIL)
        self.last_use = {}

    @property
    def session(self):
        return self._session

    def _config_for(self, netloc):
        cfg = _HOSTS.get(netloc)
        if cfg is not None:
            return cfg
        for host, c in _HOSTS.items():
            if netloc == host or netloc.endswith("." + host):
                return c
        return _DEFAULT

    def _throttle(self, netloc, min_interval):
        """Block until at least `min_interval` has elapsed since the last request to
        this host. Per-host lock so different hosts never wait on each other."""
        if min_interval <= 0:
            return
        with self._pace_lock:
            entry = self._pace.get(netloc)
            if entry is None:
                entry = [threading.Lock(), 0.0]
                self._pace[netloc] = entry
        lock, _ = entry
        with lock:
            now = time.monotonic()
            wait = entry[1] + min_interval - now
            if wait > 0:
                time.sleep(wait)
            entry[1] = time.monotonic()

    def request(self, method, url, *, retries=None, **kwargs):
        netloc = urlparse(url).netloc
        # Actividad por host — sources.py lo lee para el auto-stop de Suwayomi:
        # cualquier petición (GraphQL, páginas, thumbnails) cuenta como "en uso".
        self.last_use[netloc] = time.monotonic()
        cfg = self._config_for(netloc)
        attempts = retries if retries is not None else cfg.retries

        if cfg.headers:
            merged = dict(cfg.headers)
            merged.update(kwargs.get("headers") or {})
            kwargs["headers"] = merged
        if cfg.timeout is not None and "timeout" not in kwargs:
            kwargs["timeout"] = cfg.timeout

        last_resp = None
        last_exc = None
        for attempt in range(attempts):
            self._throttle(netloc, cfg.min_interval)
            try:
                resp = self._session.request(method, url, **kwargs)
            except requests.RequestException as exc:
                last_exc = exc
                last_resp = None
            else:
                if resp.status_code in _RETRYABLE_STATUS and attempt < attempts - 1:
                    delay = _parse_retry_after(resp.headers.get("Retry-After"))
                    if delay is None:
                        delay = min(cfg.backoff * (2 ** attempt), cfg.max_backoff)
                        delay += random.uniform(0, cfg.backoff)
                    resp.close()  # free the pooled connection before sleeping
                    time.sleep(delay)
                    last_resp = None
                    continue
                return resp
            # network error path: back off then retry (unless this was the last try)
            if attempt < attempts - 1:
                delay = min(cfg.backoff * (2 ** attempt), cfg.max_backoff)
                delay += random.uniform(0, cfg.backoff)
                time.sleep(delay)

        if last_resp is not None:
            return last_resp
        if last_exc is not None:
            raise last_exc
        # Should be unreachable (attempts >= 1), but be explicit.
        raise requests.RequestException(f"no attempts made for {url}")

    # Session-compatible convenience methods (drop-in for requests.Session).
    def get(self, url, **kwargs):
        return self.request("GET", url, **kwargs)

    def post(self, url, **kwargs):
        return self.request("POST", url, **kwargs)

    def put(self, url, **kwargs):
        return self.request("PUT", url, **kwargs)

    def delete(self, url, **kwargs):
        return self.request("DELETE", url, **kwargs)

    def head(self, url, **kwargs):
        return self.request("HEAD", url, **kwargs)


# Process-wide shared client. Import this everywhere instead of `requests`.
http = ResilientHTTP()
