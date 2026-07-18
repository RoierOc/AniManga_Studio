/* Routes external cover/banner art (TMDB, AniList, MangaDex) through the
 * backend's disk cache (/api/img) instead of hitting the CDN — and the
 * browser's own disk cache — every time. Local/relative URLs, and any host
 * not in this allowlist (e.g. arbitrary Suwayomi extension covers), pass
 * through untouched — the backend only ever fetches/caches these same
 * hosts, so keeping the check here too avoids ever sending an unrelated
 * URL to our own /api/img endpoint. */
const ALLOWED_HOSTS = new Set(['image.tmdb.org', 's4.anilist.co', 'uploads.mangadex.org', 'images.mangabaka.dev', 'cdn.mangabaka.dev'])

export function imgProxy(url) {
  if (!url || !/^https?:\/\//.test(url)) return url
  try {
    if (!ALLOWED_HOSTS.has(new URL(url).hostname.toLowerCase())) return url
  } catch {
    return url
  }
  return `/api/img?u=${encodeURIComponent(url)}`
}

/* Blur-up placeholder: ~28px thumb (~1 KB) served from the proxy's disk cache,
 * painted blurred under the real cover while it loads. Only exists for
 * proxyable hosts — returns null otherwise (card falls back to plain fade). */
export function imgThumb(url, w = 28) {
  const proxied = imgProxy(url)
  if (proxied && proxied.startsWith('/api/img?')) return `${proxied}&w=${w}`
  // Portadas de biblioteca servidas por nuestro backend (/api/library/thumb/<folder>)
  // también exponen micro-thumb con w= para el blur-up (paridad con /api/img). Respeta un
  // posible `?v=<mtime>` ya presente (cache-bust) usando el separador correcto.
  if (typeof url === 'string' && url.startsWith('/api/library/thumb/')) {
    return `${url}${url.includes('?') ? '&' : '?'}w=${w}`
  }
  return null
}
