/* Routes external cover/banner art (TMDB, AniList, MangaDex) through the
 * backend's disk cache (/api/img) instead of hitting the CDN — and the
 * browser's own disk cache — every time. Local/relative URLs, and any host
 * not in this allowlist (e.g. arbitrary Suwayomi extension covers), pass
 * through untouched — the backend only ever fetches/caches these same
 * hosts, so keeping the check here too avoids ever sending an unrelated
 * URL to our own /api/img endpoint. */
const ALLOWED_HOSTS = new Set(['image.tmdb.org', 's4.anilist.co', 'uploads.mangadex.org', 'images.mangabaka.dev', 'cdn.mangabaka.dev', 'artworks.thetvdb.com'])

/* `w` = ancho CSS al que se va a PINTAR la imagen (no el del archivo). El backend sirve una
 * rendición a ese tamaño desde su caché en disco; sin `w` manda el original entero.
 *
 * MEDIDO sobre portadas reales: 91 KB (local) y 229 KB (AniList) para pintarse en una caja de
 * ~250 px. Con 84 tarjetas en pantalla eso son ~19 MB decodificándose en el hilo principal del
 * WebView — que es justo lo que hace que una rejilla se sienta pesada al hacer scroll.
 *
 * El backend sube al peldaño siguiente de su escalera (28/48/96/320/480/640/900) y nunca amplía,
 * así que pasarse de ancho no rompe nada; quedarse corto, sí se ve. */
export function imgProxy(url, w = 0) {
  if (!url) return url
  const q = w ? `${dpx(w)}` : 0
  // Portadas servidas por nuestro propio backend: también aceptan `?w=`.
  if (typeof url === 'string' && url.startsWith('/api/library/thumb/')) {
    return q ? `${url}${url.includes('?') ? '&' : '?'}w=${q}` : url
  }
  if (!/^https?:\/\//.test(url)) return url
  try {
    if (!ALLOWED_HOSTS.has(new URL(url).hostname.toLowerCase())) return url
  } catch {
    return url
  }
  return `/api/img?u=${encodeURIComponent(url)}${q ? `&w=${q}` : ''}`
}

/* Ancho en píxeles FÍSICOS: en una pantalla HiDPI una caja de 250 px CSS necesita 500 px reales
 * o la portada se ve blanda. Se topa en 2 para no pedir 3× en pantallas exóticas. */
function dpx(cssWidth) {
  const dpr = Math.min(2, (typeof window !== 'undefined' && window.devicePixelRatio) || 1)
  return Math.round(cssWidth * dpr)
}

/* Blur-up placeholder: ~28px thumb (~1 KB) served from the proxy's disk cache,
 * painted blurred under the real cover while it loads. Only exists for
 * proxyable hosts — returns null otherwise (card falls back to plain fade). */
export function imgThumb(url, w = 28) {
  if (!url) return null
  // El micro-thumb se pinta DESENFOCADO: va a tamaño fijo, sin multiplicar por el dpr.
  if (typeof url === 'string' && url.startsWith('/api/library/thumb/')) {
    return `${url}${url.includes('?') ? '&' : '?'}w=${w}`
  }
  if (!/^https?:\/\//.test(url)) return null
  try {
    if (!ALLOWED_HOSTS.has(new URL(url).hostname.toLowerCase())) return null
  } catch { return null }
  return `/api/img?u=${encodeURIComponent(url)}&w=${w}`
}
