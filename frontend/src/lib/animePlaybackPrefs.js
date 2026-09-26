const STORAGE_KEY = 'anime-playback-prefs-v1'
const LANG_ALIASES = {
  eng: 'en', english: 'en', spa: 'es', esp: 'es', spanish: 'es',
  jpn: 'ja', jap: 'ja', japanese: 'ja',
}

function clean(value) {
  return String(value || '').trim().toLowerCase().replace(/\s+/g, ' ')
}

function language(value) {
  const raw = clean(value)
  const base = raw.split(/[-_]/)[0]
  return LANG_ALIASES[raw] || LANG_ALIASES[base] || base
}

export function trackIdentity(track) {
  if (!track) return ''
  const lang = language(track.lang)
  const external = !!track.external
  const title = external ? '' : clean(track.title)
  const fallback = clean(track.codec)
  if (!lang && !title && !fallback) return ''
  // Sidecar filenames vary by episode; their language is the stable identity.
  return external
    ? `external:${lang || fallback}`
    : `embedded:${lang}|${title || (!lang ? fallback : '')}`
}

export function resolveTrackSelection(audioTracks = [], subTracks = [], prefs = {}, preferredSub = 0) {
  const find = (tracks, identity) => identity
    ? tracks.findIndex(track => trackIdentity(track) === identity)
    : -1
  const audio = find(audioTracks, prefs.audioTrack)
  let sub = prefs.subTrack === 'off' ? -1 : find(subTracks, prefs.subTrack)
  if (sub < 0 && prefs.subTrack !== 'off') {
    const fallback = Number(preferredSub) - 1
    sub = Number.isInteger(fallback) && fallback >= 0 && fallback < subTracks.length ? fallback : -1
  }
  return { audioIndex: audio >= 0 ? audio : 0, subIndex: sub }
}

export function readAnimePlaybackPrefs(animeId, storage = globalThis.localStorage) {
  if (animeId == null || !storage?.getItem) return {}
  try {
    const all = JSON.parse(storage.getItem(STORAGE_KEY) || '{}')
    const prefs = all?.[String(animeId)]
    return prefs && typeof prefs === 'object' && !Array.isArray(prefs) ? prefs : {}
  } catch (_) { return {} }
}

export function patchAnimePlaybackPrefs(animeId, patch, storage = globalThis.localStorage) {
  if (animeId == null || !storage?.getItem || !storage?.setItem) return
  try {
    const all = JSON.parse(storage.getItem(STORAGE_KEY) || '{}')
    const prefs = all && typeof all === 'object' && !Array.isArray(all) ? all : {}
    prefs[String(animeId)] = { ...readAnimePlaybackPrefs(animeId, storage), ...patch }
    storage.setItem(STORAGE_KEY, JSON.stringify(prefs))
  } catch (_) { /* la preferencia no debe impedir la reproducción */ }
}
