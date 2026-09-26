/* Routes external cover/banner art (TMDB, AniList, MangaDex) through the
 * backend's disk cache (/api/img) instead of hitting the CDN — and the
 * browser's own disk cache — every time. Local/relative URLs, and any host
 * not in this allowlist (e.g. arbitrary Suwayomi extension covers), pass
 * through untouched — the backend only ever fetches/caches these same
 * hosts, so keeping the check here too avoids ever sending an unrelated
 * URL to our own /api/img endpoint. */
import { ref } from 'vue'

const ALLOWED_HOSTS = new Set(['image.tmdb.org', 's4.anilist.co', 'uploads.mangadex.org', 'images.mangabaka.dev', 'cdn.mangabaka.dev', 'artworks.thetvdb.com', 'cdn.myanimelist.net'])
const revisions = ref(new Map())
const animeRevisions = ref(new Map())

// Per-entry versions change the browser URL for all existing consumers of that image.
export function setImageRevisions(animes) {
  const next = new Map()
  const byAnime = new Map()
  for (const anime of animes || []) {
    const revision = anime?.cover_rev
    if (!revision) continue
    if (anime.id != null) byAnime.set(String(anime.id), revision)
    for (const url of [anime.cover, anime.cover_xl]) {
      if (url) next.set(url, revision)
    }
  }
  revisions.value = next
  animeRevisions.value = byAnime
}

function versionParam(url, revision) {
  const value = revision ?? revisions.value.get(url)
  return value === undefined || value === null || value === '' ? '' : `&v=${encodeURIComponent(value)}`
}

/* `w` = ancho CSS al que se va a PINTAR la imagen (no el del archivo). El backend sirve una
 * rendición a ese tamaño desde su caché en disco; sin `w` manda el original entero.
 *
 * MEDIDO sobre portadas reales: 91 KB (local) y 229 KB (AniList) para pintarse en una caja de
 * ~250 px. Con 84 tarjetas en pantalla eso son ~19 MB decodificándose en el hilo principal del
 * WebView — que es justo lo que hace que una rejilla se sienta pesada al hacer scroll.
 *
 * El backend sube al peldaño siguiente de su escalera (28/48/96/320/480/640/900) y nunca amplía,
 * así que pasarse de ancho no rompe nada; quedarse corto, sí se ve. */
export function imgProxy(url, w = 0, revision = null) {
  if (!url) return url
  const q = w ? `${dpx(w)}` : 0
  // Portadas servidas por nuestro propio backend: también aceptan `?w=`.
  if (typeof url === 'string' && url.startsWith('/api/library/thumb/')) {
    const params = [q ? `w=${q}` : '', versionParam(url, revision).replace(/^&/, '')].filter(Boolean)
    return params.length ? `${url}${url.includes('?') ? '&' : '?'}${params.join('&')}` : url
  }
  if (!/^https?:\/\//.test(url)) return url
  try {
    if (!ALLOWED_HOSTS.has(new URL(url).hostname.toLowerCase())) return url
  } catch {
    return url
  }
  return `/api/img?u=${encodeURIComponent(url)}${q ? `&w=${q}` : ''}${versionParam(url, revision)}`
}

/* La MISMA escalera que `_LADDER` de `imgproxy.py`. Tiene que estar aquí también: el backend
 * sube al peldaño siguiente de todos modos, así que `w=160` y `w=300` devolvían el MISMO fichero
 * (medido: 50 862 bytes los dos, peldaño 320) bajo DOS urls distintas → dos descargas y dos
 * decodificaciones del mismo píxel. Redondeando en el cliente, una. */
const LADDER = [28, 48, 96, 320, 480, 512, 640, 900]

/* Ancho en píxeles FÍSICOS: en una pantalla HiDPI una caja de 250 px CSS necesita 500 px reales
 * o la portada se ve blanda. Se topa en 2 para no pedir 3× en pantallas exóticas. */
function dpx(cssWidth) {
  const dpr = Math.min(2, (typeof window !== 'undefined' && window.devicePixelRatio) || 1)
  const real = Math.round(cssWidth * dpr)
  return LADDER.find(w => real <= w) || LADDER[LADDER.length - 1]
}

/* Ancho al que el PC extrae el fotograma de un episodio. Espejo de `_THUMB_W` en `api/anime.py`:
 * tiene que estar aquí porque **viaja en la URL**, y ésa es justo la razón de que exista este
 * helper. El endpoint responde con `max-age` de 7 días, así que al subir el ancho de 480 a 960 el
 * fichero del disco cambió pero la URL no: cada navegador siguió pintando el de 480 que ya tenía
 * guardado, y desde fuera parecía que el arreglo no había servido de nada.
 *
 * Con el ancho dentro, subirlo invalida a todo el mundo solo. Y va en UN sitio porque la URL se
 * armaba a mano en OCHO componentes: mientras estuviera repetida, este parámetro se habría quedado
 * en unos y no en otros, que es peor que no ponerlo. */
export const THUMB_W = 960

export function animeThumb(animeId, num, epType, episodeKey = '') {
  const params = new URLSearchParams({ v: THUMB_W })
  if (epType === 'special') params.set('special', '1')
  if (episodeKey) params.set('episode_key', episodeKey)
  const revision = animeRevisions.value.get(String(animeId))
  if (revision) params.set('r', revision)
  return `/api/anime/thumb/${animeId}/${num}?${params}`
}

/* Blur-up placeholder: ~28px thumb (~1 KB) served from the proxy's disk cache,
 * painted blurred under the real cover while it loads. Only exists for
 * proxyable hosts — returns null otherwise (card falls back to plain fade). */
export function imgThumb(url, w = 28, revision = null) {
  if (!url) return null
  // El micro-thumb se pinta DESENFOCADO: va a tamaño fijo, sin multiplicar por el dpr.
  if (typeof url === 'string' && url.startsWith('/api/library/thumb/')) {
    const version = versionParam(url, revision).replace(/^&/, '')
    return `${url}${url.includes('?') ? '&' : '?'}w=${w}${version ? `&${version}` : ''}`
  }
  if (!/^https?:\/\//.test(url)) return null
  try {
    if (!ALLOWED_HOSTS.has(new URL(url).hostname.toLowerCase())) return null
  } catch { return null }
  return `/api/img?u=${encodeURIComponent(url)}&w=${w}${versionParam(url, revision)}`
}
