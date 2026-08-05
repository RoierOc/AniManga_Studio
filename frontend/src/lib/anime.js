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

// Temporada actual derivada de la fecha (misma partición que el backend:
// anime.py — WINTER ene-mar, SPRING abr-jun, SUMMER jul-sep, FALL oct-dic).
// Se recalcula en cada llamada → rueda sola al cambiar de trimestre, cero mantenimiento.
export function currentSeason(now = new Date()) {
  const m = now.getMonth() + 1
  const season = m <= 3 ? 'WINTER' : m <= 6 ? 'SPRING' : m <= 9 ? 'SUMMER' : 'FALL'
  return { season, year: now.getFullYear() }
}

// Temporada ± n trimestres. El año se lleva solo con la división entera: cuatro temporadas por
// año, así que el índice absoluto (año*4 + posición) es un número de trimestre y desplazarse es
// sumar. Hacerlo con ifs sobre el nombre exige tratar a mano los saltos de año en los dos sentidos.
export const SEASON_ORDER = ['WINTER', 'SPRING', 'SUMMER', 'FALL']
export function shiftSeason(cs, delta) {
  const q = cs.year * 4 + SEASON_ORDER.indexOf(cs.season) + delta
  return { season: SEASON_ORDER[((q % 4) + 4) % 4], year: Math.floor(q / 4) }
}

// ¿Este anime pertenece a la temporada en emisión ahora mismo?
export function isCurrentSeason(anime, cs = currentSeason()) {
  return !!anime && (anime.season || '').toUpperCase() === cs.season
    && Number(anime.season_year) === cs.year
}

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
  const playable = (e) => isEpisodePlayable(e, batch)
  // "Punto más avanzado alcanzado": si el usuario saltó al ep 9 sin ver 7/8, el
  // episodio a continuar es el 10 (tras el máximo visto), no el primer hueco. Si
  // no hay nada tras ese punto, se cae al primer no visto (rellena huecos).
  const maxWatched = eps.reduce((m, e) => (e.watched ? Math.max(m, e.num) : m), 0)
  if (maxWatched > 0) {
    const after = eps.find(e => e.num > maxWatched && !e.watched && playable(e))
    if (after) return after
  }
  return eps.find(e => !e.watched && playable(e)) || null
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

// Tiempo transcurrido DESDE una emisión ya ocurrida (para "Emitido hace 4h").
// Simétrico a fmtCountdown; null si aún no ha pasado.
export function fmtAgo(airedAt, nowSec) {
  const diff = nowSec - airedAt
  if (diff < 0) return null
  const d = Math.floor(diff / 86400)
  const h = Math.floor((diff % 86400) / 3600)
  const m = Math.floor((diff % 3600) / 60)
  return { d, h, m, diff }
}

/* ── Nombre de release → algo legible ────────────────────────────────────────
 * La lista de Descargas pintaba el nombre CRUDO del torrent
 * («[ToonsHub] Chuhai Lips Canned Flavor of Married Women S01E03 1080p UNCENSORED OV WEB-DL
 * AAC2.0 H.264 (Hitozuma no Kuchibiru…)»), que es la única parte de la app que parecía un
 * cliente de torrents en vez de esta app. Esto saca serie / temporada / episodio / calidad;
 * el crudo sigue accesible en el tooltip, que es donde importa cuando algo se importó mal.
 */
const _REL_NOISE = /\b(1080p|720p|480p|2160p|4k|x264|x265|hevc|avc|10bits?|8bits?|aac\d?(\.\d)?|flac|opus|ddp?\d?(\.\d)?|web-?dl|web-?rip|bd-?rip|bd|bluray|hdtv|dual-?audio|multi-?audio|eng-?subs?|uncensored|censored|repack|batch|complete|remux|hi10p?)\b/gi

export function parseRelease(name = '') {
  const raw = String(name).replace(/\.(mkv|mp4|avi)$/i, '')
  let s = raw.replace(/^\[[^\]]*\]\s*/, '')            // grupo de release al principio

  // Episodio: SxxExx, o « - 05 » (SubsPlease y compañía). El rango « 01-12 » es una tanda.
  const se = s.match(/\bS(\d{1,2})E(\d{1,4})\b/i)
  const dash = !se && s.match(/\s-\s(\d{1,4})(?:v\d)?(?=\s|$|\[|\()/)
  const range = !se && !dash && s.match(/\s-?\s?\(?(\d{1,4})\s?[-~]\s?(\d{1,4})\)?(?=\s|$|\[|\()/)

  const sm = s.match(/\((?:season|temporada)\s*(\d{1,2})\)/i) || s.match(/\b(?:season\s*(\d{1,2})|S(\d{2}))\b/i)
  let season = se ? Number(se[1]) : sm ? Number(sm[1] ?? sm[2]) : null
  const episode = se ? Number(se[2]) : dash ? Number(dash[1]) : null
  // Una temporada nombrada SIN episodio es una tanda: «(Season 1) [BD 1080p]» son los 11 a la vez.
  const batch = !episode && (!!range || season != null || /\bbatch\b|\bcomplete\b/i.test(s))

  // El título es lo que hay ANTES del marcador de episodio/temporada.
  const cut = se?.index ?? dash?.index ?? range?.index ?? sm?.index ?? -1
  let title = cut >= 0 ? s.slice(0, cut) : s.replace(/\((?:season|temporada)\s*\d{1,2}\).*$/i, '')
  title = title
    .replace(/\[[^\]]*\]/g, '')
    .replace(/\([^)]*\)/g, '')      // «(2024)», «(Terror in Resonance)», «(Uncensored)»
    .replace(_REL_NOISE, '')
    .replace(/[._]+/g, ' ')
    .replace(/\s{2,}/g, ' ')
    .replace(/\s*[|/].*$/, '')
    .replace(/[\s\-–—:]+$/, '')
    .trim()

  const q = raw.match(/\b(2160p|1080p|720p|480p)\b/i)
  return { title: title || raw, season, episode, batch, quality: q ? q[1].toLowerCase() : '', raw }
}

const _norm = (s) => String(s || '').toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim()

/* Empareja un release con una serie de TU biblioteca, para poder pintar su póster.
 * Coincidencia por inclusión normalizada — sobra para nombres de carpeta reales y no
 * inventa nada: si no casa, la fila sale sin póster (que es la verdad). */
export function matchLibrary(relTitle, library = []) {
  const n = _norm(relTitle)
  if (n.length < 3) return null
  let best = null
  for (const a of library) {
    for (const t of [a.title, a.title_romaji]) {
      const c = _norm(t)
      if (!c || c.length < 3) continue
      if (n.includes(c) || c.includes(n)) {
        if (!best || c.length > best.len) best = { anime: a, len: c.length }
      }
    }
  }
  return best?.anime || null
}
