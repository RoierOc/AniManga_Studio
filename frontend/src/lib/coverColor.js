/* Color dominante (promedio) de una portada, para usarlo SOLO en zonas decorativas
 * de FONDO (auras, resplandores, backdrops esmerilados) — nunca sobre texto ni
 * controles, para no pelear con el azul del design system. Misma técnica de
 * muestreo que HeroBanner.vue (canvas 10×10 + media). Cachea por url; si la imagen
 * es cross-origin sin CORS (canvas tainted) o falla, devuelve null.
 *
 * Pasa la url por imgProxy() antes de llamar para que TMDB/AniList/MangaDex se
 * sirvan same-origin (/api/img) y el canvas sea legible.
 */

const _cache = new Map()   // url → {r,g,b} | null

export function coverRGB(url) {
  if (!url) return Promise.resolve(null)
  if (_cache.has(url)) return Promise.resolve(_cache.get(url))
  return new Promise((resolve) => {
    const img = new Image()
    img.crossOrigin = 'anonymous'
    const done = (v) => { _cache.set(url, v); resolve(v) }
    img.onload = () => {
      try {
        const n = 12
        const cv = document.createElement('canvas'); cv.width = cv.height = n
        const ctx = cv.getContext('2d', { willReadFrequently: true })
        ctx.drawImage(img, 0, 0, n, n)
        const d = ctx.getImageData(0, 0, n, n).data
        let r = 0, g = 0, b = 0, m = 0
        for (let i = 0; i < d.length; i += 4) { r += d[i]; g += d[i + 1]; b += d[i + 2]; m++ }
        done({ r: (r / m) | 0, g: (g / m) | 0, b: (b / m) | 0 })
      } catch (_) { done(null) }
    }
    img.onerror = () => done(null)
    img.src = url
  })
}

/* Realza un poco la saturación del color promedio (que tiende al gris) para que los
 * resplandores/auras se lean como COLOR sin volverse chillones. `boost` ~1.35. */
export function vivid({ r, g, b }, boost = 1.35) {
  const avg = (r + g + b) / 3
  const f = (c) => Math.max(0, Math.min(255, Math.round(avg + (c - avg) * boost)))
  return { r: f(r), g: f(g), b: f(b) }
}

export function rgbaCss({ r, g, b }, a = 1) { return `rgba(${r}, ${g}, ${b}, ${a})` }
