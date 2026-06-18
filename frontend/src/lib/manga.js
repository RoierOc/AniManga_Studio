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
export const pageUrl = (p) => (/^(https?:)?\/\//.test(p) || p.startsWith('/')) ? p : `/uploads/${p}`
export const pageUrlOriginal = (p) => `/uploads/original/${p}`
export const pageUrlUpscaled = (p) => `/uploads/upscaled/${p}`
