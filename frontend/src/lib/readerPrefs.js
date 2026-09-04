const KEY_PREFIX = 'reader-prefs:'

const VALID = Object.freeze({
  mode: new Set(['paged', 'webtoon']),
  fit: new Set(['width', 'height', 'original']),
  dir: new Set(['rtl', 'ltr']),
})

const GLOBAL_KEYS = Object.freeze({
  mode: 'reader-mode',
  fit: 'reader-fit',
  dir: 'reader-dir',
})

const FALLBACKS = Object.freeze({ mode: 'paged', fit: 'width', dir: 'rtl' })

function storage() {
  return typeof globalThis.localStorage === 'undefined' ? null : globalThis.localStorage
}

function valid(field, value) {
  return typeof value === 'string' && VALID[field]?.has(value) ? value : null
}

function parse(workId) {
  const ls = storage()
  if (workId == null || workId === '' || !ls) return {}
  try {
    const raw = JSON.parse(ls.getItem(readerPrefsKey(workId)) || '{}')
    if (!raw || typeof raw !== 'object') return {}
    return Object.fromEntries(Object.keys(VALID).flatMap(field => {
      const value = valid(field, raw[field])
      return value ? [[field, value]] : []
    }))
  } catch (_) {
    return {}
  }
}

export function readerPrefsKey(workId) {
  return `${KEY_PREFIX}${String(workId)}`
}

/** Valores globales antiguos: siguen siendo el valor inicial de una obra sin preferencias. */
export function readerDefaults() {
  const ls = storage()
  return Object.fromEntries(Object.keys(GLOBAL_KEYS).map(field => {
    let value = null
    try { value = valid(field, ls?.getItem(GLOBAL_KEYS[field])) } catch (_) {}
    return [field, value || FALLBACKS[field]]
  }))
}

/** Sólo la preferencia explícita de la obra; no mezcla los valores globales. */
export function readReaderPrefs(workId) {
  return parse(workId)
}

export function effectiveReaderPrefs(workId, legacyMode = '') {
  const stored = parse(workId)
  if (!stored.mode && valid('mode', legacyMode)) stored.mode = legacyMode
  return { ...readerDefaults(), ...stored }
}

export function saveReaderPrefs(workId, patch = {}) {
  const ls = storage()
  if (workId == null || workId === '' || !ls || !patch || typeof patch !== 'object') return
  const next = { ...parse(workId) }
  for (const field of Object.keys(VALID)) {
    const value = valid(field, patch[field])
    if (value) next[field] = value
  }
  try { ls.setItem(readerPrefsKey(workId), JSON.stringify(next)) } catch (_) {}
  return next
}
