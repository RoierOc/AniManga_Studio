const STORAGE_KEY = 'novel-progress-v1'
const FIELDS = ['title', 'pluginId', 'path', 'chapterIndex', 'chapterName', 'scroll', 'total', 'at']

function storage() {
  return typeof globalThis.localStorage === 'undefined' ? null : globalThis.localStorage
}

function cleanEntry(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null
  return Object.fromEntries(FIELDS.filter(field => field in value).map(field => [field, value[field]]))
}

export function normalizeNovelProgress(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return {}
  return Object.fromEntries(Object.entries(value)
    .map(([id, entry]) => [String(id), cleanEntry(entry)])
    .filter(([, entry]) => entry))
}

export function readLocalNovelProgress() {
  const ls = storage()
  if (!ls) return {}
  try { return normalizeNovelProgress(JSON.parse(ls.getItem(STORAGE_KEY) || '{}')) }
  catch (_) { return {} }
}

export function saveLocalNovelProgress(value) {
  const ls = storage()
  if (!ls) return
  try { ls.setItem(STORAGE_KEY, JSON.stringify(normalizeNovelProgress(value))) } catch (_) {}
}

function at(value) {
  const n = Number(value)
  return Number.isFinite(n) ? n : 0
}

/** El último punto de lectura gana; una respuesta remota antigua nunca borra la local. */
export function mergeNovelProgress(base, incoming) {
  const out = normalizeNovelProgress(base)
  for (const [id, entry] of Object.entries(normalizeNovelProgress(incoming))) {
    if (!out[id] || at(entry.at) >= at(out[id].at)) out[id] = entry
  }
  return out
}

export function sameNovelProgress(left, right) {
  const a = normalizeNovelProgress(left)
  const b = normalizeNovelProgress(right)
  const pack = (value) => Object.keys(value).sort().map(id => [id, value[id]])
  return JSON.stringify(pack(a)) === JSON.stringify(pack(b))
}
