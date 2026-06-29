/* Anime domain constants + small pure helpers. */

export const ANIME_STATUS = {
  watching:      { label: 'Viendo',     color: 'var(--jade)' },
  completed:     { label: 'Completado', color: 'var(--azure-bright)' },
  plan_to_watch: { label: 'Por ver',    color: 'var(--violet)' },
  on_hold:       { label: 'En pausa',   color: 'var(--gold)' },
  dropped:       { label: 'Abandonado', color: 'var(--coral)' },
}
export const STATUS_ORDER = ['watching', 'plan_to_watch', 'on_hold', 'completed', 'dropped']

export const SEASON_ES = { WINTER: 'Invierno', SPRING: 'Primavera', SUMMER: 'Verano', FALL: 'Otoño' }

const FORMAT_LABEL = {
  TV: 'TV', TV_SHORT: 'TV Corto', MOVIE: 'Película', OVA: 'OVA',
  ONA: 'ONA', SPECIAL: 'Especial', MUSIC: 'Música',
}
export const animeFormatLabel = (f) => FORMAT_LABEL[f] || f || 'TV'

export function animeEpLabel(anime, ep) {
  const fmt = anime?.format
  if (fmt === 'MOVIE') return 'Película'
  if (fmt === 'MUSIC') return 'Video Musical'
  if (ep.ep_type === 'special') return ep.title || `Especial ${ep.num}`
  return ep.title && ep.in_local ? cleanEpTitle(ep.title, ep.num) : `Episodio ${ep.num}`
}

// Strip release-group noise from a filename to a readable episode title.
function cleanEpTitle(raw, num) {
  let t = (raw || '').replace(/\.[a-z0-9]{2,4}$/i, '')
  t = t.replace(/\[[^\]]*\]/g, '').replace(/\([^)]*\)/g, '')
  t = t.replace(/\b(1080p|720p|480p|2160p|x264|x265|hevc|aac|web-?dl|bluray|bd)\b/gi, '')
  t = t.replace(/[._]+/g, ' ').replace(/\s+/g, ' ').trim()
  return t || `Episodio ${num}`
}

/* Derive batch state from an episode list (ep num 0 = season batch torrent). */
export function batchInfo(episodes = []) {
  const batchEp = episodes.find(e => e.num === 0)
  const hasBatch = !!(batchEp && batchEp.in_qbt)
  const batchDone = !!(batchEp && batchEp.progress >= 100)
  return { batchEp, hasBatch, batchDone }
}

export function isEpisodePlayable(ep, batch) {
  return (ep.in_qbt && ep.progress >= 100) || ep.in_local || (ep.num > 0 && batch.batchDone)
}

export function nextUnwatchedEp(anime) {
  const eps = (anime?.episodes || [])
    .filter(e => e.num > 0 && e.ep_type !== 'special')
    .sort((a, b) => a.num - b.num)
  const batch = batchInfo(anime?.episodes || [])
  return eps.find(e => !e.watched && isEpisodePlayable(e, batch)) || null
}

/* Torrent title language detection.
   Catches common multi-sub markers used by groups like erai-raws ([Multiple Subtitle]) and
   SubsPlease ([Multi]), plus explicit Spanish tags (ESP, Español, Dual, Latino). */
export const isSpanishOrMulti = (title) =>
  /\b(esp|espa[nñ]ol|castellano|multi|lat|latino|multi.?sub|multiple.?sub(?:title)?|sub.?esp|dual)\b/i.test(title || '')

export const isEnglishSub = (title) => {
  if (isSpanishOrMulti(title)) return false
  return /\b(eng(?:lish)?[\s._-]?(?:sub(?:bed)?|dub(?:bed)?)?|english[\s._-]?(?:sub(?:bed)?|dubbed)?|\[en\]|\[eng\])\b/i.test(title || '')
}

export function fmtCountdown(airingAt, nowSec) {
  const diff = airingAt - nowSec
  if (diff <= 0) return null
  const d = Math.floor(diff / 86400)
  const h = Math.floor((diff % 86400) / 3600)
  const m = Math.floor((diff % 3600) / 60)
  return { d, h, m, diff }
}
