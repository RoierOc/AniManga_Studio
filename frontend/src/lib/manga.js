/* Manga helpers — mirror the backend's id/chapter logic for matching task status. */

export function sanitizeTitleId(title) {
  let c = (title || '').trim().replace(/[^A-Za-z0-9._-]+/g, '_')
  c = c.replace(/_+/g, '_').replace(/^_+|_+$/g, '')
  return c || 'manga'
}

// task_id == `${sanitizeTitleId(title)}_${type}_ch${chapterNorm}`
export const taskId = (title, chapter, type) =>
  `${sanitizeTitleId(title)}_${type}_ch${chapter}`

// Canonical title for matching (lowercase, alphanumeric only)
export const canonicalTitle = (t) => (t || '').toLowerCase().replace(/[^a-z0-9]/g, '')

// Display label for a chapter number ("one_shot" -> "One Shot")
export const formatChapter = (ch) => ch === 'one_shot' ? 'One Shot' : 'Cap. ' + ch

export function chapterSortKey(ch) {
  const n = parseFloat(ch)
  return isNaN(n) ? Infinity : n
}

// Reading-status enum, mirrors lib/anime.js's ANIME_STATUS.
export const MANGA_STATUS = {
  reading:      { label: 'Leyendo',    color: 'var(--jade)' },
  completed:    { label: 'Completado', color: 'var(--azure-bright)' },
  plan_to_read: { label: 'Por leer',   color: 'var(--violet)' },
  on_hold:      { label: 'En pausa',   color: 'var(--gold)' },
  dropped:      { label: 'Abandonado', color: 'var(--coral)' },
}
export const MANGA_STATUS_ORDER = ['reading', 'plan_to_read', 'on_hold', 'completed', 'dropped']

// Page URL served by Flask (/uploads prefers upscaled, falls back to original).
// Absolute paths (CBZ pages, remote URLs) pass through unchanged.
// `w` (opcional): pide la página reescalada a ese ancho (el backend cachea) — el
// lector la usa para no decodificar 4K completo cuando se muestra a ~1000px (lag
// del WebView2). Sin w o en rutas remotas/absolutas → archivo íntegro.
// Codifica la RUTA de una página conservando el sufijo de cache-bust `?v=<mtime>` que añade
// el backend. Es IMPRESCINDIBLE: los nombres de carpeta/archivo pueden contener `?`, `#`, `&`
// o espacios (p.ej. "So, Do You Want To Go Out, Or? (How Do We Relationship)") — sin codificar,
// el `?` de la carpeta trunca la URL y la imagen da 404 (páginas "que no aparecen").
const _encPagePath = (p) => {
  const m = p.match(/^(.*)\?(v=\d+)$/)      // solo el `?v=<dígitos>` final es query real
  const path = m ? m[1] : p
  const q = m ? `?${m[2]}` : ''
  return path.split('/').map(encodeURIComponent).join('/') + q
}
export const pageUrl = (p, w = 0) => {
  if (/^(https?:)?\/\//.test(p)) return p
  const base = p.startsWith('/') ? p : `/uploads/${_encPagePath(p)}`
  return w > 0 ? `${base}${base.includes('?') ? '&' : '?'}w=${w}` : base
}
export const pageUrlOriginal = (p) =>
  (/^(https?:)?\/\//.test(p) || p.startsWith('/')) ? p : `/uploads/original/${_encPagePath(p)}`
export const pageUrlUpscaled = (p) =>
  (/^(https?:)?\/\//.test(p) || p.startsWith('/')) ? p : `/uploads/upscaled/${_encPagePath(p)}`
