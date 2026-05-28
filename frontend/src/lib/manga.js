/* Manga helpers — mirror the backend's id/chapter logic for matching task status. */

export function sanitizeTitleId(title) {
  let c = (title || '').trim().replace(/[^A-Za-z0-9._-]+/g, '_')
  c = c.replace(/_+/g, '_').replace(/^_+|_+$/g, '')
  return c || 'manga'
}

// task_id == `${sanitizeTitleId(title)}_${type}_ch${chapterNorm}`
export const taskId = (title, chapter, type) =>
  `${sanitizeTitleId(title)}_${type}_ch${chapter}`

export function chapterSortKey(ch) {
  const n = parseFloat(ch)
  return isNaN(n) ? Infinity : n
}

// Page URL served by Flask (/uploads prefers upscaled, falls back to original)
export const pageUrl = (p) => `/uploads/${p}`
export const pageUrlOriginal = (p) => `/uploads/original/${p}`
export const pageUrlUpscaled = (p) => `/uploads/upscaled/${p}`
