import { defineStore } from 'pinia'
import { api } from '@/lib/api'
import { onSSE } from '@/lib/sse'
import { useUiStore } from './ui'
import { nextUnwatchedEp, isSpanishOrMulti, isEnglishSub } from '@/lib/anime'
import { vtGo, marcarTarjeta } from '@/lib/vt'
import { isNative, send as nativeSend, onMessage as onNativeMessage } from '@/lib/nativeBridge'

// Estilo de subtítulos: lo aplica el PLAYER en vivo con `sub-ass-style-overrides` de mpv, NO se
// hornea en el archivo. Por defecto, el estilo que traían las pistas árabes (Adobe Arabic 26,
// negrita, borde 1, sin sombra), que es el que gustó. '' en un campo = no tocar ese campo.
// Bold/Outline/Shadow van en la escala de ASS: Bold -1 = sí, 0 = no.
export const SUB_STYLE_DEFAULT = { font: 'Adobe Arabic', size: '26', bold: '-1', outline: '1', shadow: '0' }

function _loadSubStyle() {
  try {
    return { ...SUB_STYLE_DEFAULT, ...JSON.parse(localStorage.getItem('anime-sub-style') || '{}') }
  } catch (_) {
    return { ...SUB_STYLE_DEFAULT }
  }
}

let autoplayTimer = null
let nowTimer = null
let sseBound = false
let dlPoll = null       // interval para refrescar el progreso de descarga de qBittorrent en vivo
const subPollers = {}   // subKey -> interval handle

export const useAnimeStore = defineStore('anime', {
  state: () => ({
    library: [],
    loadError: '',              // la carga de la biblioteca falló (≠ biblioteca vacía)
    loading: false,
    // Guard against a stale localStorage value landing on the placeholder fallback.
    sub: ['library', 'search', 'explore', 'seasonal', 'schedule', 'downloads', 'history'].includes(localStorage.getItem('anime-sub'))
      ? localStorage.getItem('anime-sub') : 'library',   // library | search | seasonal | downloads | history
    detailId: null,            // open anime detail id (library entry)
    previewAnime: null,        // non-library anime open in detail (recommendations)
    enrichedPreviews: {},      // al_id → {cover_xl, banner, logo} fetched from /enrich
    recSeed: Math.floor(Math.random() * 100),  // rotates which 2 recs show — random per session
    libSort: localStorage.getItem('anime-libsort') || 'last_watched',  // recently-watched first by default
    libFilter: 'all',
    forYou: [],              // recomendaciones agregadas sobre la biblioteca (B2)
    forYouReason: '',        // 'sin_historial' → hay biblioteca pero nada visto todavía
    _forYouLoading: false,
    libSearch: '',

    skipTimes: {},             // `${animeId}_${ep}` -> {op_start, op_end, ed_start, ed_end}
    epInfo: {},                // `${malId}_${ep}` -> {title, synopsis, ...} | null(loading)
    epMeta: {},                // anime.id -> { '<num>': {title, overview, still, aired} }  (TMDB, o MAL de reserva)
    epInfoOpen: null,          // currently expanded ep-info key
    nextAiring: {},            // al_id -> {episode, airing_at}
    airing: {},                // al_id -> {status, next_episode, next_airing_at, last_episode, last_aired_at} — fresh airing schedule

    // discovery in detail (keyed by al_id)
    recs: {}, recsState: {},
    franchise: {}, franchiseState: {},   // orden de estreno de la franquicia por al_id
    stacks: {}, stacksState: {},
    tags: {}, malUrls: {},
    fullSyn: {},               // al_id -> sinopsis completa (la de library viene recortada a 320)
    // browse overlays
    stackBrowse: null, stackBrowseMeta: null, stackBrowseAnime: [], stackBrowseState: 'idle',
    tagBrowse: null, tagBrowseAnime: [], tagBrowseState: 'idle',
    tagBrowseSource: null,     // al_id desde el que se abrió (para no listarse a sí mismo)
    tagBrowseRank: 0,          // % mínimo del tag; 0 = sin umbral
    // hover preview
    preview: null,             // anime object
    previewPos: { x: 0, y: 0 },

    // local scan paths
    scan: { show: false, paths: [], folders: [], loading: false, newPath: '',
            adding: false, suggesting: 0,
            browseOpen: false, browsePath: '', browseWin: '', browseParent: null, browseItems: [] },

    // anime download location (qBittorrent save path)
    dlSettings: { download_path: '', qbt_default: '' },
    // management
    linkTorrent: { show: false, list: [], loading: false, subpath: '' },
    epOverrideMenu: null,      // { anime, ep, x, y }
    coverPicker: null,         // { id, tab, tabs: { cover, banner_detail, banner } } | null — each tab is { options, current, loaded }
    coverPickerLoading: false,
    coverSaving: false,

    autoplay: null,            // { anime, ep } | null
    autoplaySeconds: 0,
    nowSec: Math.floor(Date.now() / 1000),

    // subtitles
    subTasks: {},              // subKey -> { status, progress, message, engine, task_id }
    subTrackModal: null,       // { anime, ep, tracks, externalTracks, spanishTracks, missingKeys }
    subFetching: null,         // subKey currently fetching tracks

    // qBittorrent
    qbt: { connected: false, version: '', url: 'http://localhost:8080', username: '', password: '' },
    qbtTorrents: [],
    qbtError: '',          // qBittorrent apagado o VPN caída ≠ «no hay descargas»
    qbtLoading: false,

    // history
    history: [],
    historyLoaded: false,

    // seasonal
    seasonal: [],
    seasonalLoading: false,
    seasonalError: '',     // '' = sin fallo. Un [] vacío NO puede significar «falló» (regla del repo)
    season: '',
    year: 0,
    seasonSort: 'score',         // score | popularity | trending
    seasonGenre: '',

    // explore (AniList browse: popularidad / año / género / formato)
    explore: [],
    exploreLoading: false,
    exploreError: '',
    exploreSort: 'score',        // score | popularity | trending
    exploreGenre: '',
    exploreYear: 0,              // 0 = cualquier año
    exploreFormat: '',           // '' = todos
    explorePage: 1,
    exploreHasNext: false,
    exploreGenreList: [],        // {name,type} desde /api/anilist/genres

    // search + torrents
    searchQuery: '',
    searchResults: [],
    searchError: '',           // la búsqueda falló (≠ sin resultados)
    _searchReqId: 0,           // anti-carrera al buscar mientras se escribe
    _searchTimer: 0,
    searchLoading: false,
    torrentAnime: null,          // anime whose torrents are open (null = show results)
    torrents: [],
    torrentsLoading: false,
    torrentQuery: '',
    torrentVariants: [],         // alias/sinónimos (AniList) que se buscan en Nyaa, visibles en el panel (cap 5)
    torrentAllVariants: [],      // full AniList synonym list (no cap) — used by _buildExtraQueries
    torrentCategory: '1_2',
    flt: { lang: 'all', hideDead: true, quality: '', group: '', ep: 'all', sort: 'relevance' },
    targetEp: null,             // target episode when navigating from detail
    epFetch: {},                 // per-episode deep-fetch state: epNum → 'loading' | 'done'
    addingHashes: [],            // keys currently being added to qbt
    addedHashes: [],             // keys just added (transient ✓)

    // ── Player web embebido (estilo Crunchyroll) ─────────────────────────
    playerMode: localStorage.getItem('anime-player-mode') || 'web', // 'web' | 'mpv' | 'native'
    player: null,                // {anime, ep, sess, loading, error} — overlay abierto si != null
    // Reproductor NATIVO embebido (libmpv en la shell Windows, vía nativeBridge).
    nativePlayer: null,          // {anime, ep, pos, duration, paused, tier} — abierto si != null
    // 'off' | 'high' | 'ultra' (los tiers 'fast'/'medium'/'artcnn'/'fsrcnnx' se retiraron → migran a 'high')
    native4kTier: ['off', 'high', 'ultra'].includes(localStorage.getItem('anime-native-4k'))
      ? localStorage.getItem('anime-native-4k') : 'high',
    // Tier para IMAGEN REAL (series/películas). Aparte del de anime a propósito: Anime4K está
    // entrenado en line art y sobre imagen real deja halos, así que cada dominio guarda el suyo
    // y ver anime no arrastra nunca el coste del otro (ni al revés).
    nativeLiveTier: ['off', 'live', 'live_lite'].includes(localStorage.getItem('anime-native-live'))
      ? localStorage.getItem('anime-native-live') : 'live',
    nativeVol: Number(localStorage.getItem('anime-native-vol') ?? 100), // 0..100
    nativeSubScale: Number(localStorage.getItem('anime-native-subscale') ?? 1), // 0.5..2
    nativeSubStyle: _loadSubStyle(),   // {font,size,bold,outline,shadow} — ver SUB_STYLE_DEFAULT
    subFonts: [],                      // familias instaladas, para el selector de Ajustes
    nativeBright: Number(localStorage.getItem('anime-native-bright') ?? 1), // 0.5..3 gamma (HDR)
    nativeSat: Number(localStorage.getItem('anime-native-sat') ?? 1), // 0.5..3 saturación (HDR)
  }),

  getters: {
    detail: (s) => s.previewAnime || s.library.find(a => a.id === s.detailId) || null,

    // "Continue watching": in-progress or next-unwatched episode per recently-watched anime.
    // Excludes fully-watched series (where every available episode is watched).
    continueWatching: (s) => {
      return s.library
        .filter(a => {
          if (!a.last_watched_at) return false
          const eps = (a.episodes || []).filter(e => e.num > 0 && e.ep_type !== 'special' && (e.in_local || (e.in_qbt && e.progress >= 100)))
          // Exclude anime where ALL available episodes are watched
          if (eps.length > 0 && eps.every(e => e.watched)) return false
          return true
        })
        .map(a => {
          const eps = (a.episodes || [])
          const hay = e => e.num > 0 && e.ep_type !== 'special' && (e.in_local || (e.in_qbt && e.progress >= 100))
          /* El ancla es el ÚLTIMO episodio que tocaste (`last_ep`), no el de número más alto con
             posición guardada. Buscar «el primero con `resume_pos`» hacía que asomarse un minuto
             al 7 dejase la serie clavada ahí aunque después vieras el 4 entero: la posición del 7
             seguía guardada y no había forma de saber cuál era más reciente.
             Si lo dejaste a medias, se reanuda ése; si lo terminaste, cae al siguiente sin ver. */
          const ultimo = a.last_ep ? eps.find(e => e.num === a.last_ep && hay(e)) : null
          const inProg = (ultimo && ultimo.resume_pos > 0 && !ultimo.watched)
            ? ultimo
            // Sin `last_ep` (series vistas antes de que existiera el campo) se mantiene el
            // comportamiento de siempre, que acierta mientras no haya posiciones sueltas.
            : (a.last_ep ? null : eps.find(e => hay(e) && e.resume_pos > 0 && !e.watched))
          const ep = inProg || nextUnwatchedEp(a)
          return ep ? { anime: a, ep } : null
        })
        .filter(Boolean)
        .sort((x, y) => (y.anime.last_watched_at || 0) - (x.anime.last_watched_at || 0))
        .slice(0, 12)
    },

    // Rotated pool of high-scored seasonal series NOT in the library, mapped to hero items.
    // recSeed (aleatorio por sesión) rota el orden para variar en cada apertura; alimenta el
    // relleno del hero (heroItems) para que el carrusel nunca se vea vacío ni con "sólo 2".
    recommendedItems() {
      const libIds = new Set(this.library.flatMap(a => [a.al_id, a.mal_id].filter(Boolean)))
      const pool = this.seasonal
        .filter(a => !libIds.has(a.al_id) && !libIds.has(a.mal_id) && (a.score || 0) >= 65)
        .sort((x, y) => (y.score || 0) - (x.score || 0))
        .slice(0, 16)
      const n = pool.length
      if (!n) return []
      const start = this.recSeed % n
      return [...pool.slice(start), ...pool.slice(0, start)].map(a => {
        const enriched = this.enrichedPreviews[a.al_id] || {}
        return {
          anime: { ...a, ...enriched, id: a.al_id || a.id, episodes: [], total_episodes: typeof a.episodes === 'number' ? a.episodes : (a.total_episodes || null), last_watched_at: 0, status: a.status || 'RELEASING' },
          ep: { num: this.nextAiring[a.al_id]?.episode || a.next_episode || a.episodes || '?' },
          kind: 'recommendation', ts: 0, hasFile: false,
        }
      })
    },
    // "Populares de la temporada" rail — series de temporada que no están en la biblioteca.
    seasonalPopular() {
      const libIds = new Set(this.library.flatMap(a => [a.al_id, a.mal_id].filter(Boolean)))
      return this.seasonal
        .filter(a => !libIds.has(a.al_id) && !libIds.has(a.mal_id))
        .sort((x, y) => (y.popularity || 0) - (x.popularity || 0))
        .slice(0, 20)
    },

    heroItems() {
      const now = Date.now() / 1000
      const RECENT = 12 * 86400
      const TARGET = 10   // el hero aspira a llenarse (estilo Crunchyroll) aunque la biblioteca sea chica

      // Rec pool compartido (mismo que el riel "Recomendados") para RELLENAR cualquier nivel
      // hasta TARGET, así el carrusel nunca se ve vacío ni con "sólo 2".
      const recs0 = this.recommendedItems
      const fill = (base) => {
        if (base.length >= TARGET) return base.slice(0, TARGET)
        const have = new Set(base.map(it => it.anime.al_id || it.anime.id))
        const extra = recs0.filter(r => !have.has(r.anime.al_id || r.anime.id))
        return [...base, ...extra].slice(0, TARGET)
      }

      // 1 — newly aired library episodes, interspersed with recommendations, filled to TARGET
      const aired = []
      for (const a of this.library) {
        const inf = this.airing[a.al_id]
        if (!inf || !inf.last_episode || !inf.last_aired_at) continue
        if (now - inf.last_aired_at > RECENT) continue
        const dl = (a.episodes || []).find(e => e.num === inf.last_episode
          && (e.in_local || (e.in_qbt && e.progress >= 100)) && !e.watched)
        aired.push({
          anime: a, ep: dl || { num: inf.last_episode }, kind: 'new',
          ts: inf.last_aired_at, aired_at: inf.last_aired_at, hasFile: !!dl,
        })
      }
      if (aired.length) {
        aired.sort((x, y) => y.ts - x.ts)
        const recs = recs0
        // Interleave: 1 recomendación cada 2 estrenos, luego se rellena el resto hasta TARGET.
        const result = []
        let ri = 0
        for (let i = 0; i < aired.length && result.length < TARGET; i++) {
          result.push(aired[i])
          if ((i + 1) % 2 === 0 && ri < recs.length && result.length < TARGET) result.push(recs[ri++])
        }
        return fill(result)
      }

      // 2 — fresh, unwatched, downloaded episodes (+ recomendaciones para dar vida)
      const fresh = []
      for (const a of this.library) {
        const eps = (a.episodes || []).filter(e =>
          e.num > 0 && e.ep_type !== 'special' &&
          (e.in_local || (e.in_qbt && e.progress >= 100)) && !e.watched)
        if (!eps.length) continue
        const ep = eps.reduce((b, e) => (e.added_on || 0) > (b.added_on || 0) ? e : b)
        fresh.push({ anime: a, ep, kind: 'downloaded', ts: ep.added_on || 0, hasFile: true })
      }
      if (fresh.length) return fill(fresh.sort((x, y) => y.ts - x.ts))

      // 3 — continue watching (+ recomendaciones)
      const cw = this.continueWatching
      if (cw.length) return fill(cw.map(c => ({ anime: c.anime, ep: c.ep, kind: 'continue', ts: c.anime.last_watched_at || 0, hasFile: true })))

      // 4 — seasonal popular (library empty or nothing aired)
      if (this.seasonal.length) {
        const libIds = new Set(this.library.flatMap(a => [a.al_id, a.mal_id].filter(Boolean)))
        return this.seasonal
          .filter(a => !libIds.has(a.al_id) && !libIds.has(a.mal_id))
          .sort((x, y) => (y.popularity || 0) - (x.popularity || 0))
          .slice(0, TARGET)
          .map(a => ({
            anime: { ...a, id: a.al_id || a.id, episodes: [], total_episodes: typeof a.episodes === 'number' ? a.episodes : (a.total_episodes || null), last_watched_at: 0, status: a.status || 'RELEASING' },
            ep: { num: this.nextAiring[a.al_id]?.episode || a.next_episode || a.episodes || '?' },
            kind: 'seasonal', ts: 0, hasFile: false,
          }))
      }
      return []
    },

    filteredTorrents: (s) => {
      let list = s.torrents
      if (s.flt.hideDead) list = list.filter(t => t.seeders > 0)
      if (s.flt.lang === 'esp') list = list.filter(t => isSpanishOrMulti(t.title))
      else if (s.flt.lang === 'eng') list = list.filter(t => isEnglishSub(t.title))
      else if (s.flt.lang === 'other') list = list.filter(t => !isSpanishOrMulti(t.title) && !isEnglishSub(t.title))
      if (s.flt.quality) list = list.filter(t => t.quality === s.flt.quality)
      if (s.flt.group) list = list.filter(t => t.group === s.flt.group)
      if (s.flt.ep === 'batch') list = list.filter(t => t.episode === 0)
      else if (s.flt.ep === 'episodes') list = list.filter(t => t.episode > 0)
      return list
    },
    langCounts: (s) => {
      const all = s.torrents.filter(t => s.flt.hideDead ? t.seeders > 0 : true)
      return {
        all: all.length,
        esp: all.filter(t => isSpanishOrMulti(t.title)).length,
        eng: all.filter(t => isEnglishSub(t.title)).length,
        other: all.filter(t => !isSpanishOrMulti(t.title) && !isEnglishSub(t.title)).length,
      }
    },
    torrentGroups: (s) => [...new Set(s.torrents.map(t => t.group).filter(Boolean))].sort(),
    torrentQualities: (s) => [...new Set(s.torrents.map(t => t.quality).filter(Boolean))].sort(),
    groupedEpisodes() {
      const list = this.filteredTorrents.map(t => ({ ...t, isSpanish: isSpanishOrMulti(t.title), isEnglish: isEnglishSub(t.title) }))
      const map = {}
      for (const t of list) { (map[t.episode] ??= []).push(t) }
      // Seed placeholder groups for early episodes the RSS window starved to zero, so they remain
      // expandable (expanding triggers fetchEpisodeTorrents). Capped to single-cour/season shows to
      // avoid seeding hundreds of empty groups for long-runners. When AniList knows the episode
      // count, that count is the ceiling; otherwise we seed up to the highest episode actually seen.
      const epNums = Object.keys(map).map(Number).filter(n => n > 0)
      // AniList episode count (0 = unknown). Raw search results carry it as a number in `episodes`;
      // library/seasonal objects get normalized so `episodes` is the episode LIST and the count
      // lives in `total_episodes`. Read both so the cap works regardless of entry path.
      const epField = this.torrentAnime?.episodes
      const known = (typeof epField === 'number' ? epField : Number(this.torrentAnime?.total_episodes)) || 0
      let maxEp = known > 0 ? known : Math.max(0, ...epNums)
      if (this.targetEp) maxEp = Math.max(maxEp, this.targetEp)
      if (maxEp > 0 && maxEp <= 50) {
        for (let n = 1; n <= maxEp; n++) if (!map[n]) map[n] = []
      }
      const sortKey = this.flt.sort || 'relevance'
      const cmp = {
        seeders: (a, b) => b.seeders - a.seeders,
        size_desc: (a, b) => (b.size_bytes || 0) - (a.size_bytes || 0),
        size_asc: (a, b) => (a.size_bytes || 0) - (b.size_bytes || 0),
        // relevance: idioma preferido primero (ES, luego EN), desempate por seeders
        relevance: (a, b) => {
          if (a.isSpanish !== b.isSpanish) return a.isSpanish ? -1 : 1
          if (a.isEnglish !== b.isEnglish) return a.isEnglish ? -1 : 1
          return b.seeders - a.seeders
        },
      }[sortKey]
      for (const k in map) map[k].sort(cmp)
      let groups = Object.entries(map)
        .map(([k, torrents]) => ({ episode: Number(k), torrents }))
        .sort((a, b) => {
          if (a.episode === 0) return -1
          if (b.episode === 0) return 1
          if (a.episode === -1) return 1
          if (b.episode === -1) return -1
          return a.episode - b.episode
        })
      // Cap to the real episode count AniList reports: drops phantom/mislabeled groups (absolute
      // numbering across seasons, resolutions misparsed as episodes, eps beyond the season). When
      // AniList doesn't know the total (ongoing shows → null), keep everything as-is. Batch (0) and
      // unclassified (-1) are always kept, plus the active targetEp as a safety net.
      if (known > 0) {
        groups = groups.filter(g => g.episode <= 0 || g.episode <= known || g.episode === this.targetEp)
      }
      // "Registrar como episodio N": show only that episode (+ batches), like the legacy.
      if (this.targetEp !== null) {
        groups = groups.filter(g => g.episode === 0 || g.episode === this.targetEp)
      }
      return groups
    },
  },

  actions: {
    init() {
      this.reportCaps()
      if (!nowTimer) nowTimer = setInterval(() => { this.nowSec = Math.floor(Date.now() / 1000) }, 30000)
      if (!sseBound) {
        sseBound = true
        onSSE('watched', (ev) => this._onWatched(ev))
        onSSE('position', (ev) => this._onPosition(ev))
        onSSE('download_complete', () => this.loadLibrary(true))
      }
    },

    async loadAiring() {
      try { this.airing = await api.get('/api/anime/airing') || {} } catch (_) {}
    },

    setLibSort(id) { this.libSort = id; localStorage.setItem('anime-libsort', id) },

    /* «Para ti»: recomendaciones agregadas sobre TU biblioteca (no de una serie suelta).
     * El backend cachea 24 h con una huella de la biblioteca, así que esto es barato salvo la
     * primera vez. `forYouReason` distingue «aún no has visto nada» (vacío legítimo, se explica)
     * de «AniList no respondió» (fallo, se calla y se reintenta luego) — no son lo mismo. */
    async loadForYou() {
      if (this.forYou.length || this._forYouLoading) return
      this._forYouLoading = true
      try {
        const r = await api.get('/api/for_you/anime')
        if (Array.isArray(r)) { this.forYou = r; this.forYouReason = '' }
        else { this.forYou = r?.items || []; this.forYouReason = r?.reason || '' }
      } catch {
        this.forYou = []
        this.forYouReason = ''      // un fallo NO es «no tienes historial»
      } finally {
        this._forYouLoading = false
      }
    },

    /* ── Download location ──────────────────────────────────────────────── */
    async loadDlSettings() {
      try { this.dlSettings = await api.get('/api/anime/settings') || this.dlSettings } catch (_) {}
    },
    async saveDlPath(path) {
      const p = (path || '').trim()
      try {
        await api.post('/api/anime/settings', { download_path: p })
        this.dlSettings.download_path = p
        useUiStore().toast(p ? 'Carpeta de descargas guardada' : 'Usando la carpeta por defecto de qBittorrent', 'ok')
      } catch (_) { useUiStore().toast('No se pudo guardar', 'error') }
    },
    async loadLibrary(silent = false) {
      if (!silent) this.loading = true
      this.loadError = ''
      try {
        const data = await api.get('/api/anime/library')
        this.library = Array.isArray(data) ? data : []
        this._ensureDlPolling()   // si hay descargas en curso, refresca el progreso en vivo
      } catch (e) {
        // El toast se desvanece; la rejilla se quedaba anunciando «Aún no has añadido anime»
        // para siempre. El fallo tiene que SOBREVIVIR en el estado para que la vista lo diga.
        this.loadError = e?.message || 'No se pudo contactar con el servidor.'
        if (!silent) useUiStore().toast('No se pudo cargar tu anime', 'error')
      } finally {
        this.loading = false
      }
    },

    persist() { localStorage.setItem('anime-sub', this.sub) },

    // Hover preview bubble removed (buggy / low value); kept as a no-op so existing
    // callers (openDetail, etc.) don't need to change.
    hidePreview() { this.preview = null },

    openDetail(anime) {
      // Recuerda de qué tarjeta saliste para señalarla al volver (rejillas de decenas de
      // pósters: el scroll ya volvía bien, pero no había ninguna pista de cuál mirabas).
      marcarTarjeta(anime.id)
      // vtGo = View Transition (el póster de la card "vuela" al hero del detalle)
      vtGo(() => {
        this.hidePreview()
        this.previewAnime = null
        this.detailId = anime.id
        this.epInfoOpen = null
        this.linkTorrent = { show: false, list: [], loading: false, subpath: '' }
        if (anime.al_id) { this.loadTags(anime); this.loadRecs(anime); this.loadSynopsis(anime); this.loadFranchise(anime) }
        this.loadEpMeta(anime)
        useUiStore().pushNav()
      })
    },
    // Eagerly enriches a non-library anime with TMDB/AniList HD data and caches the
    // result in enrichedPreviews so heroItems reacts and updates the hero slider too.
    async enrichPreview(al_id) {
      if (al_id === undefined || al_id === null) return
      if (this.enrichedPreviews[al_id] !== undefined) return  // already fetching or done
      this.enrichedPreviews[al_id] = null  // mark in-progress (avoids duplicate requests)
      try {
        const data = await api.get(`/api/anime/enrich/${al_id}`)
        if (data) this.enrichedPreviews[al_id] = data
      } catch (_) {}
    },
    // Opens a non-library anime (recommendation / seasonal) in the detail panel.
    // Uses cached enrichment if already available; triggers enrichment otherwise.
    openPreview(anime) {
      vtGo(() => this._openPreview(anime))
    },
    _openPreview(anime) {
      this.hidePreview()
      this.detailId = null
      const cached = this.enrichedPreviews[anime.al_id] || {}
      this.previewAnime = {
        ...anime,
        ...cached,
        id: anime.al_id || anime.id,
        episodes: [],
        downloaded_count: 0,
        total_episodes: typeof anime.episodes === 'number' ? anime.episodes : (anime.total_episodes || null),
      }
      this.epInfoOpen = null
      this.linkTorrent = { show: false, list: [], loading: false, subpath: '' }
      if (anime.al_id) {
        this.loadTags(anime)
        this.loadRecs(anime)
        this.loadSynopsis(anime)
        this.loadFranchise(anime)
        // enrichPreview caches result → also updates previewAnime via watcher below
        this.enrichPreview(anime.al_id).then(() => {
          const enriched = this.enrichedPreviews[anime.al_id]
          if (enriched && this.previewAnime?.al_id === anime.al_id) {
            this.previewAnime = { ...this.previewAnime, ...enriched }
          }
        })
      }
      useUiStore().pushNav()
    },
    // Pure reset for programmatic callers (tab switches, openRec). The "Volver"
    // button uses ui.back() so forward can reopen the detail.
    closeDetail() {
      vtGo(() => this._resetDetail())
    },
    _resetDetail() {
      this.detailId = null
      this.previewAnime = null
    },

    async loadSynopsis(anime) {
      const id = anime.al_id
      if (!id || this.fullSyn[id] !== undefined) return
      this.fullSyn[id] = ''    // marca en curso (evita peticiones duplicadas)
      try {
        const d = await api.get(`/api/anime/synopsis/${id}`)
        if (d?.synopsis) this.fullSyn[id] = d.synopsis
      } catch (_) {}
    },

    async loadTags(anime) {
      const id = anime.al_id
      if (!id || this.tags[id] !== undefined) return
      this.tags[id] = []
      try {
        const d = await api.get(`/api/anime/tags/${id}`)
        this.tags[id] = d?.tags || []
        this.malUrls[id] = d?.mal_url || ''
        if (d?.next_airing) this.nextAiring[id] = d.next_airing
        // stacks need a mal_id (from tags response or the anime entry)
        const malId = anime.mal_id || (d?.mal_url || '').match(/anime\/(\d+)/)?.[1]
        if (malId) this.loadStacks(id, malId)
      } catch (_) {}
    },

    async loadRecs(anime) {
      const id = anime.al_id
      if (!id || this.recsState[id]) return
      this.recsState[id] = 'loading'
      try {
        const d = await api.get(`/api/anime/recommendations/${id}`)
        this.recs[id] = d || []
        this.recsState[id] = (d && d.length) ? 'done' : 'none'
      } catch (_) { this.recsState[id] = 'error' }
    },
    openRec(rec) {
      const lib = this.library.find(a => (rec.al_id && a.al_id === rec.al_id) || (rec.mal_id && a.mal_id === rec.mal_id) || (rec.title && a.title === rec.title))
      if (lib) this.openDetail(lib)
      else this.openTorrents(rec)   // navigates to search + opens torrents
    },

    async loadFranchise(anime) {
      const id = anime.al_id
      if (!id || this.franchiseState[id]) return
      this.franchiseState[id] = 'loading'
      try {
        const d = await api.get(`/api/anime/franchise/${id}`)
        const items = (d && d.items) || []
        this.franchise[id] = items
        // solo tiene sentido mostrarla si hay más de una entrega
        this.franchiseState[id] = items.length > 1 ? 'done' : 'none'
      } catch (_) { this.franchiseState[id] = 'error' }
    },
    openFranchiseItem(item) {
      if (item.is_current) return
      const lib = this.library.find(a => a.al_id === item.al_id)
      if (lib) this.openDetail(lib)
      else this.openTorrents({ al_id: item.al_id, title: item.title, cover: item.cover })
    },

    async loadStacks(alId, malId) {
      if (this.stacksState[alId]) return
      this.stacksState[alId] = 'loading'
      try {
        const d = await api.get(`/api/anime/stacks?mal_id=${malId}`)
        this.stacks[alId] = d || []
        this.stacksState[alId] = (d && d.length) ? 'done' : 'none'
      } catch (_) { this.stacksState[alId] = 'error' }
    },
    async browseStack(stack) {
      this.stackBrowse = stack; this.stackBrowseMeta = null; this.stackBrowseAnime = []; this.stackBrowseState = 'loading'
      try {
        const d = await api.get(`/api/anime/stacks/browse/${stack.id}`)
        this.stackBrowseMeta = d.stack || null
        this.stackBrowseAnime = d.items || []
        this.stackBrowseState = 'done'
      } catch (_) { this.stackBrowseState = 'error' }
    },
    closeStackBrowse() { this.stackBrowse = null; this.stackBrowseState = 'idle' },

    /* Buscar por tag CON umbral de relevancia. Un tag como «Magic» lo llevan cientos de series
       con un 60 % — a ese nivel el filtro no filtra. AniList publica el % por obra
       (`minimumTagRank`), así que se puede pedir «lo que va DE magia», no «lo que la menciona».
       El umbral se recuerda entre aperturas: quien busca al 90 % suele querer seguir al 90 %. */
    async browseByTag(tagName, alId = null, minRank = null) {
      if (minRank !== null) this.tagBrowseRank = minRank
      this.tagBrowse = tagName; this.tagBrowseSource = alId
      this.tagBrowseAnime = []; this.tagBrowseState = 'loading'
      try {
        const qs = new URLSearchParams({ tag: tagName })
        if (alId) qs.set('al_id', alId)
        if (this.tagBrowseRank) qs.set('min_rank', this.tagBrowseRank)
        this.tagBrowseAnime = await api.get(`/api/anime/browse_tag?${qs}`) || []
        this.tagBrowseState = 'done'
      } catch (_) { this.tagBrowseState = 'error' }
    },
    setTagRank(r) { this.browseByTag(this.tagBrowse, this.tagBrowseSource, r) },
    closeTagBrowse() { this.tagBrowse = null; this.tagBrowseState = 'idle' },

    /* ── Management ─────────────────────────────────────────────────────── */
    async openLinkTorrent() {
      this.linkTorrent = { show: true, list: [], loading: true, subpath: '' }
      try { this.linkTorrent.list = await api.get('/api/anime/qbt/list') || [] } catch (_) {}
      finally { this.linkTorrent.loading = false }
    },
    async linkExistingTorrent(anime, t) {
      try {
        await api.post(`/api/anime/library/${anime.id}/link_torrent`, { info_hash: t.hash, episode: 0, torrent_title: t.name, subpath: this.linkTorrent.subpath })
        this.linkTorrent.show = false
        useUiStore().toast('Torrent enlazado ✓', 'ok')
        await this.loadLibrary(true)
      } catch (_) { useUiStore().toast('No se pudo enlazar', 'error') }
    },
    async clearEpisodes(anime) {
      if (!await useUiStore().confirm({
        title: 'Borrar episodios', danger: true, confirmLabel: 'Liberar espacio',
        body: `¿Borrar los episodios de "${anime.title}" para liberar espacio?\n` +
              'Se eliminan los archivos (su torrent en qBittorrent, o los vídeos de la carpeta vinculada), ' +
              'pero la serie —portada, estado, progreso visto y miniaturas— se conserva.\n' +
              'Podrás volver a descargarla desde Torrents cuando quieras.',
      })) return
      try {
        // delete the files (free space) AND remove the torrent so qBittorrent no
        // longer flags it as "missing files" and re-downloads it. The library entry
        // itself is kept by the backend.
        await api.post(`/api/anime/library/${anime.id}/clear_episodes`, { remove_from_qbt: true, delete_files: true })
        useUiStore().toast(`Episodios de "${anime.title}" liberados`, 'ok')
        await this.loadLibrary(true)
      } catch (_) { useUiStore().toast('No se pudo borrar', 'error') }
    },
    // Quitar de la biblioteca NO borra los archivos: es reversible, así que no interrumpe con un
    // diálogo — actúa y ofrece deshacer. El diálogo se reserva para lo que borra bytes.
    async removeFromLibrary(animeId) {
      const ui = useUiStore()
      const snapshot = this.library.find(a => a.id === animeId)
      try {
        await api.del(`/api/anime/library/${animeId}`, { body: { delete_files: false } })
        this.detailId = null
        await this.loadLibrary(true)
        ui.toast(`«${snapshot?.title || 'Serie'}» quitada de la biblioteca`, 'info', 7000,
          snapshot ? { label: 'Deshacer', fn: () => this.addToLibrary(snapshot) } : null)
      } catch (_) { ui.toast('No se pudo eliminar', 'error') }
    },
    openEpOverrideMenu(ev, anime, ep) { this.epOverrideMenu = { anime, ep, x: ev.clientX, y: ev.clientY } },
    async setEpOverride(type) {
      const m = this.epOverrideMenu; this.epOverrideMenu = null; if (!m) return
      try { await api.post(`/api/anime/library/${m.anime.id}/ep_override`, { filename: m.ep.filename, type }); await this.loadLibrary(true) }
      catch (_) { useUiStore().toast('No se pudo cambiar el tipo', 'error') }
    },
    async openCoverPicker(anime) {
      this.coverPicker = {
        id: anime.id,
        tab: 'cover',
        tabs: {
          cover:         { options: [], current: anime.cover || '', loaded: false },
          banner_detail: { options: [], current: anime.banner_detail || anime.banner || '', loaded: false },
          banner:        { options: [], current: anime.banner || '', loaded: false },
        },
      }
      await this.loadPickerTab('cover')
    },
    async switchPickerTab(tab) {
      if (!this.coverPicker) return
      this.coverPicker.tab = tab
      await this.loadPickerTab(tab)
    },
    async loadPickerTab(tab) {
      const p = this.coverPicker
      if (!p || p.tabs[tab].loaded) return
      this.coverPickerLoading = true
      try {
        const url = tab === 'cover'
          ? `/api/anime/library/${p.id}/cover_options`
          : `/api/anime/library/${p.id}/backdrop_options?target=${tab}`
        const d = await api.get(url)
        if (this.coverPicker?.id === p.id) {
          this.coverPicker.tabs[tab].options = d.options || []
          if (d.current) this.coverPicker.tabs[tab].current = d.current
          this.coverPicker.tabs[tab].loaded = true
        }
      } catch (_) { useUiStore().toast('No se pudieron cargar las imágenes', 'error') }
      finally { this.coverPickerLoading = false }
    },
    async pickCover(opt) {
      if (!this.coverPicker) return
      const { id, tab } = this.coverPicker
      this.coverSaving = true
      try {
        if (tab === 'cover') {
          await api.post(`/api/anime/library/${id}/cover`, { cover: opt.url, source: opt.source })
        } else {
          await api.post(`/api/anime/library/${id}/backdrop`, { backdrop: opt.url, source: opt.source, target: tab })
        }
        this.coverPicker = null
        await this.loadLibrary(true)
        useUiStore().toast(tab === 'cover' ? 'Portada actualizada' : 'Fondo actualizado', 'ok')
      } catch (_) { useUiStore().toast('No se pudo guardar el cambio', 'error') }
      finally { this.coverSaving = false }
    },

    async play(anime, ep, subFile = '', startPos = 0) {
      // En la shell nativa se prefiere el motor embebido (libmpv) — es el objetivo:
      // vídeo nativo bajo la UI. Se puede forzar MPV externo (mode 'mpv', p.ej. el
      // botón "abrir en MPV") o subs traducidos, que siguen siendo exclusivos de MPV.
      if (isNative() && this.playerMode !== 'mpv' && !subFile) {
        return this.playNative(anime, ep, startPos)
      }
      // Player web embebido por defecto; MPV externo si el modo lo pide o si se
      // pasa un subtítulo externo (subs traducidos — flujo aún exclusivo de MPV).
      if (this.playerMode === 'web' && !subFile) {
        return this.openPlayer(anime, ep, startPos)
      }
      const base = ep.in_local
        ? { anime_id: anime.id, episode: ep.num, local_path: ep.local_path, sub_file: subFile }
        : { anime_id: anime.id, episode: ep.num, info_hash: ep.info_hash, sub_file: subFile }
      const body = startPos > 0 ? { ...base, start_pos: startPos } : base
      try {
        await api.post('/api/anime/play', body)
        useUiStore().toast(`▶ Reproduciendo episodio ${ep.num}`, 'info', 2200)
      } catch (_) {
        useUiStore().toast('No se pudo iniciar MPV', 'error')
      }
    },

    /* Reporta una vez qué códecs decodifica este navegador (log del backend). */
    reportCaps() {
      if (this._capsSent || typeof MediaSource === 'undefined') return
      this._capsSent = true
      const t = (c) => MediaSource.isTypeSupported(c)
      api.post('/api/stream/caps', {
        hevc_main10: t('video/mp4; codecs="hvc1.2.4.L153.B0"'),
        hevc_main: t('video/mp4; codecs="hvc1.1.6.L120.B0"'),
        av1: t('video/mp4; codecs="av01.0.08M.08"'),
        h264: t('video/mp4; codecs="avc1.640028"'),
        webgpu: !!navigator.gpu,
        ua: navigator.userAgent.slice(0, 80),
      }).catch(() => {})
    },

    setPlayerMode(mode) {
      this.playerMode = mode
      localStorage.setItem('anime-player-mode', mode)
    },

    /* ── Reproductor nativo embebido (libmpv en la shell) ──────────────────── */
    // `opts.progressKey` abre el MISMO reproductor para contenido que no es anime (series y
    // películas vía Sonarr/Radarr): el vídeo se resuelve por `ep.local_path` y el progreso se
    // guarda en el almacén de ese dominio (ver `_reportNativeProgress`).
    async playNative(anime, ep, startPos = 0, opts = {}) {
      this.dismissAutoplay?.()
      // Mostrar el overlay YA (estado de carga) para no dejar al usuario en la vista
      // previa mientras se resuelve la ruta (ffprobe) — la espera se percibía como
      // "no abre". El spinner se quita al llegar el primer evento de tiempo.
      this.nativePlayer = {
        anime, ep, pos: startPos, duration: 0, paused: false, loading: true,
        // `isLive` = imagen real: lo marca quien abre desde series/películas (progressKey).
        isLive: !!opts.progressKey,
        // ⚠️ Un episodio ya HORNEADO con Anime4K (`ep.a4k`) trae los shaders metidos en los píxeles:
        // aplicárselos otra vez en vivo es pasar la red dos veces — se emborrona y encima cuesta
        // fotogramas gratis. Se abre con los shaders apagados; el menú sigue disponible a mano.
        tier: ep?.a4k ? 'off' : (opts.progressKey ? this.nativeLiveTier : this.native4kTier),
        yaHorneado: !!ep?.a4k,
        _lastReport: 0, fullscreen: false,
        audioTracks: [], subTracks: [], aid: 1, sid: 0,
        speed: 1, subScale: this.nativeSubScale, subSync: 0,
        bright: this.nativeBright, sat: this.nativeSat,
        progressKey: opts.progressKey || null,
        onProgress: opts.onProgress || null,
        // Lista para el panel de episodios del reproductor. En anime sale de `anime.episodes`;
        // los dominios externos (series) la pasan aquí ya normalizada, con su propio progressKey
        // por episodio para que cambiar desde el panel siga guardando en su almacén.
        playlist: opts.playlist || null,
      }
      this._ensureNativeSub()

      const base = ep.in_local
        ? { anime_id: anime.id, episode: ep.num, local_path: ep.local_path }
        : { anime_id: anime.id, episode: ep.num, info_hash: ep.info_hash }
      const body = startPos > 0 ? { ...base, start_pos: startPos } : base
      let res
      try {
        res = await api.post('/api/anime/native/resolve', body)
      } catch (e) {
        useUiStore().toast(e?.message || 'No se pudo resolver el vídeo', 'error')
        this.nativePlayer = null
        return
      }
      if (!this.nativePlayer) return // cerrado mientras cargaba
      if (!res?.win_path) {
        useUiStore().toast('No se encontró el archivo de vídeo', 'error')
        this.nativePlayer = null
        return
      }
      const resume = startPos > 0 ? startPos : (res.resume_pos || 0)
      const subTracks = res.sub_tracks || []
      // Pista por defecto: el backend ya calcula la MEJOR pista española (sub_lang, mira
      // código Y título y prioriza latino) en `preferred_sub` (sid 1-based, 0 = ninguna).
      // Si por lo que sea no viene, caemos a un criterio de cliente amplio (código o título
      // con señal de español) y, en último término, a la primera pista.
      let defSid = res.preferred_sub || 0
      if (!defSid && subTracks.length) {
        const esRe = /(^|[^a-z])(spa|es|esp|lat|latino|castellano|cas|spanish|españ?ol)([^a-z]|$)/i
        const idx = subTracks.findIndex((t) => esRe.test(`${t.lang || ''} ${t.title || ''}`))
        defSid = idx >= 0 ? idx + 1 : 1
      }
      Object.assign(this.nativePlayer, {
        pos: resume,
        duration: res.duration || this.nativePlayer.duration || 0,
        audioTracks: res.audio_tracks || [],
        subTracks,
        aid: 1,                          // 1-based (orden de aparición)
        sid: defSid,
        thumbs: res.thumbs || null,      // {url, interval} para el scrubbing
      })
      // Cargar con el inicio ya en la posición de reanudación (fiable: fijar 'start'
      // antes de loadfile, no un seek posterior que se ignoraría) + tier Anime4K.
      nativeSend('loadfile', { path: res.win_path, start: resume })
      // Fijar la pista elegida (mpv no la autoselecciona con sub-auto=no + sub-add auto).
      // SÓLO si es INCRUSTADA: `sid` sobrevive al cambio de archivo, pero un sidecar todavía NO
      // EXISTE en el archivo recién cargado. MEDIDO en el log de mpv (School-Live! E02):
      //   177.925 loadfile · 177.926 set sid=4 ← la 4 aún no existe · 178.071 sub-add · 178.076 set sid=4
      // En esa ventana mpv resuelve el sid por su cuenta y se queda en la pista inglesa, mientras
      // la interfaz ya muestra «Español» seleccionado. El usuario lo veía como «está en inglés
      // pero dice español», y se arreglaba solo al cambiar de pista a mano. Para los sidecar la
      // selección se hace en `_pendingSubs`, que corre cuando mpv ya los tiene.
      const elegida = subTracks[defSid - 1] || null
      if (defSid > 0 && !(elegida && elegida.external)) nativeSend('track', { sid: String(defSid) })
      // Los sidecar (subs externos), en cambio, se añaden CUANDO el archivo nuevo ya está
      // sonando, no aquí. `loadfile` es ASÍNCRONO: vuelve antes de que mpv haya cambiado de
      // archivo, así que un `sub-add` inmediato se pega al archivo SALIENTE y muere con él.
      // Abrir desde la ficha colaba de casualidad (crear el motor tarda ~1 s y el mensaje
      // esperaba en la cola de la ventana); abrir desde el panel de capítulos o con «siguiente»
      // reutiliza el motor y no colaba NUNCA. Por eso el síntoma era exclusivo de los subtítulos
      // inyectados de series/películas: en anime van dentro del MKV, y aquí no se puede
      // reescribir el contenedor (rompería el hash del torrent), así que van como sidecar.
      // El orden de `subadd` es el mismo en que el backend los listó tras las incrustadas → el
      // sid (índice+1) sigue coincidiendo con la lista del gestor.
      const externals = subTracks.filter(t => t.external && t.win_path)
      this._pendingSubs = externals.length
        ? () => {
          for (const t of externals) nativeSend('subadd', { path: t.win_path })
          // Re-fijar: la pista elegida puede ser una de las que acaban de existir.
          if (defSid > 0) nativeSend('track', { sid: String(defSid) })
        }
        : null
      nativeSend('shaders', { tier: this.nativePlayer.tier })
      // Permitir amplificar hasta 150% (mpv corta en volume-max, 100 por defecto).
      nativeSend('setprop', { name: 'volume-max', value: '150' })
      nativeSend('volume', { value: this.nativeVol })
      // Reponer estado que persiste en el motor reutilizado entre episodios.
      nativeSend('setprop', { name: 'speed', value: '1' })
      nativeSend('setprop', { name: 'sub-delay', value: '0' })
      /* Explícito, no por defecto: `sub-scale` sólo llega a los subtítulos ASS si
       * `sub-ass-override` está en `scale`. Es el valor por defecto de mpv moderno, pero en
       * versiones anteriores era otro — y entonces el mismo deslizador de "Tamaño" movía los
       * .srt y no movía los .ass. Fijarlo hace que el control signifique lo mismo siempre.
       * `scale` NO toca fuentes ni posiciones: los carteles siguen intactos. */
      nativeSend('setprop', { name: 'sub-ass-override', value: 'scale' })
      nativeSend('setprop', { name: 'sub-scale', value: String(this.nativeSubScale || 1) })
      // Estilo propio de subtítulos. Va tras loadfile (necesita el archivo cargado) y sin await:
      // tiene que leer los estilos del MKV, y no merece retrasar la imagen por ello.
      this._applyNativeSubStyle()
      // Ajustes de imagen HDR (ecualizadores nativos de mpv): gamma + saturación.
      if (this.nativeBright !== 1) nativeSend('bright', { value: this.nativeBright })
      if (this.nativeSat !== 1) nativeSend('sat', { value: this.nativeSat })
      this.nativePlayer.bright = this.nativeBright
      this.nativePlayer.sat = this.nativeSat
    },

    // Suscripción única a los eventos que envía Rust (tiempo/estado).
    _ensureNativeSub() {
      if (this._nativeSubbed) return
      this._nativeSubbed = true
      onNativeMessage((d) => {
        if (!d || d.event !== 'time' || !this.nativePlayer) return
        this.nativePlayer.loading = false   // ya hay vídeo → quitar spinner
        // Primer latido del archivo nuevo = mpv ya lo tiene cargado: AHORA sí se le pueden
        // colgar los sidecar (ver el porqué en `playNative`). Se ejecuta una sola vez.
        if (this._pendingSubs) { const addSubs = this._pendingSubs; this._pendingSubs = null; addSubs() }
        this.nativePlayer.pos = d.pos ?? this.nativePlayer.pos
        this.nativePlayer.duration = d.duration || this.nativePlayer.duration
        this.nativePlayer.paused = !!d.paused
        // Reportar progreso al backend ~cada 5 s (resume/visto).
        const now = Date.now()
        if (now - (this.nativePlayer._lastReport || 0) > 5000) {
          this.nativePlayer._lastReport = now
          this._reportNativeProgress(false)
        }
        // Fin de episodio REAL: marcar visto y encadenar auto-play. Exigimos duración
        // creíble (>60 s) para no disparar con una duración espuria momentánea al cargar
        // o cambiar de archivo (que marcaría visto tras un instante de reproducción).
        if (d.duration > 60 && d.pos >= d.duration - 1) {
          // Se lee TODO antes de cerrar: `closeNative()` pone `nativePlayer` a null.
          const { anime, ep, playlist, onProgress } = this.nativePlayer
          this._reportNativeProgress(true)
          this.closeNative()
          // Con `playlist` (series/películas) el siguiente sale de ahí: `nextUnwatchedEp` mira
          // `anime.episodes`, que en esos dominios no existe — sin esto no encadenaría nunca.
          const nxt = playlist
            ? playlist.find(e => e.num > ep.num && e.in_local) || null
            : nextUnwatchedEp(anime, ep)
          if (nxt) {
            this.showAutoplay(anime, nxt, playlist
              ? { progressKey: nxt.progressKey || null, playlist, onProgress } : null)
          }
        }
      })
    },

    _reportNativeProgress(ended) {
      const np = this.nativePlayer
      if (!np) return
      // `progressKey` lo pone quien abre el reproductor desde OTRO dominio (series/películas
      // vía Sonarr): mismo overlay y mismo motor, pero el progreso va a su propio almacén.
      // Sin esto, una serie occidental escribiría dentro de la biblioteca de anime.
      if (np.progressKey) {
        api.post('/api/media/progress', {
          key: np.progressKey, position: np.pos, duration: np.duration, ended,
          // Título, portada y número de episodio viajan con el progreso porque el backend, al
          // apuntar el visionado en el historial compartido, sólo tiene la clave — y salir a
          // preguntárselos a Sonarr en cada latido del reproductor sería absurdo.
          title: np.anime?.title || '', cover: np.anime?.cover || '', episode: np.ep?.num,
        }).catch(() => {})
        // Y se avisa al dueño del dominio EN EL ACTO, sin esperar al ida y vuelta: es lo que
        // hace que al salir del episodio el minuto aparezca ya en la ficha y en "Seguir viendo"
        // (antes se refrescaba con un setTimeout a ciegas). Callback y no import del store de
        // media para no crear un ciclo: `stores/media.js` ya importa este módulo.
        np.onProgress?.({ key: np.progressKey, pos: np.pos, duration: np.duration, ended })
        return
      }
      api.post('/api/anime/native/progress', {
        anime_id: np.anime.id,
        episode: np.ep.num,
        position: np.pos,
        duration: np.duration,
        ended,
      }).catch(() => {})
    },

    nativePause(v) {
      if (!this.nativePlayer) return
      this.nativePlayer.paused = v
      nativeSend('pause', { value: v })
    },
    nativeSeek(pos) {
      // ACOTAR ANTES DE NADA. mpv interpreta un absoluto NEGATIVO como «desde el final»: al
      // arrastrar la barra del todo a la izquierda salía `pos = -0.16` y mpv saltaba al segundo
      // 1471,3 → EOF → se cerraba el reproductor y aparecía «¿ver siguiente capítulo?».
      // MEDIDO en el log de mpv:
      //   Run command: seek, args=[target="-0.160215", flags="absolute"]
      //   queuing seek to 1471.327785  →  EOF reached.
      // Va aquí y no en `seekTo` porque por este método pasan TODOS los saltos: barra, teclado
      // (un −10 s en el segundo 2 daba negativo igual) y «Saltar OP».
      const dur = this.nativePlayer?.duration || 0
      pos = Math.max(0, dur ? Math.min(pos, dur - 0.5) : pos)

      // Calibración del "Saltar OP": un seek en los 10 s siguientes al salto (y a menos de
      // 60 s del punto de aterrizaje) se lee como corrección → se aprende para esta serie.
      // El propio seek del salto (pos === target) no cuenta.
      const cal = this._skipOpCal
      if (cal && Date.now() - cal.at < 10000) {
        const d = pos - cal.target
        if (Math.abs(d) > 1 && Math.abs(d) < 60) {
          const learned = Math.min(200, Math.max(20, Math.round(cal.amt + d)))
          try { localStorage.setItem(cal.key, String(learned)) } catch {}
          this._skipOpCal = null
        }
      }
      if (!this.nativePlayer) return
      this.nativePlayer.pos = pos
      nativeSend('seek', { pos })
    },
    setNativeVol(v) {
      this.nativeVol = v
      localStorage.setItem('anime-native-vol', String(v))
      nativeSend('volume', { value: v })
    },
    setNativeSpeed(v) {
      if (this.nativePlayer) this.nativePlayer.speed = v
      nativeSend('setprop', { name: 'speed', value: String(v) })
    },
    setNativeSubScale(v) {
      v = Math.min(2, Math.max(0.5, Math.round(v * 10) / 10))
      this.nativeSubScale = v
      localStorage.setItem('anime-native-subscale', String(v))
      if (this.nativePlayer) this.nativePlayer.subScale = v
      nativeSend('setprop', { name: 'sub-scale', value: String(v) })
    },

    // ── Estilo de subtítulos (fuente/cuerpo/borde), en vivo ───────────────────────────────
    // El backend devuelve el override ya construido porque necesita leer los ESTILOS del
    // archivo: mpv aplica por nombre de estilo (`Default.Fontname=…`), y sólo así se puede
    // dejar en paz a los carteles, que el grupo casó con el arte del vídeo.
    async loadSubFonts() {
      if (this.subFonts.length) return this.subFonts
      try {
        const d = await api.get('/api/subtitle/fonts')
        this.subFonts = d.fonts || []
      } catch (_) { this.subFonts = [] }
      return this.subFonts
    },

    async _applyNativeSubStyle() {
      const np = this.nativePlayer
      if (!np?.ep) return
      const s = this.nativeSubStyle
      const p = new URLSearchParams({
        anime_id: String(np.anime?.id ?? ''),
        episode: String(np.ep.num ?? 1),
        ...(np.ep.in_local ? { local_path: np.ep.local_path || '' } : { info_hash: np.ep.info_hash || '' }),
        font: s.font || '', size: s.size || '', bold: s.bold || '',
        outline: s.outline || '', shadow: s.shadow || '',
      })
      /* Los subtítulos de TEXTO PLANO (srt/vtt/mov_text) no tienen estilos ASS, así que el
       * override por estilo no los toca y salían con la tipografía por defecto de mpv: en una
       * biblioteca con series en ASS y series en .srt, "el mismo ajuste" se veía de dos maneras.
       * mpv los pinta con sus propias opciones `sub-*`, y su unidad es "píxeles a una ventana de
       * 720 de alto" — exactamente la misma convención que usa el backend para escalar el
       * Fontsize del ASS (_SUB_REF_RES_Y = 720). Así que el MISMO número da el MISMO tamaño en
       * los dos caminos. Se manda siempre: sobre una pista ASS estas opciones son inertes. */
      nativeSend('setprop', { name: 'sub-font', value: s.font || 'sans-serif' })
      nativeSend('setprop', { name: 'sub-font-size', value: String(s.size || 26) })
      nativeSend('setprop', { name: 'sub-bold', value: (s.bold === '-1' || s.bold === '1') ? 'yes' : 'no' })
      nativeSend('setprop', { name: 'sub-border-size', value: String(s.outline || 0) })
      nativeSend('setprop', { name: 'sub-shadow-offset', value: String(s.shadow || 0) })

      let d
      try { d = await api.get(`/api/subtitle/styles?${p}`) } catch (_) { return }
      if (this.nativePlayer !== np) return          // cambió de episodio mientras se pedía
      // '' es válido y significativo: limpia un override anterior (volver al estilo del archivo).
      nativeSend('setprop', { name: 'sub-ass-style-overrides', value: d.overrides || '' })
    },

    setNativeSubStyle(patch) {
      this.nativeSubStyle = { ...this.nativeSubStyle, ...patch }
      localStorage.setItem('anime-sub-style', JSON.stringify(this.nativeSubStyle))
      this._applyNativeSubStyle()
    },

    resetNativeSubStyle() {
      this.nativeSubStyle = { ...SUB_STYLE_DEFAULT }
      localStorage.removeItem('anime-sub-style')
      this._applyNativeSubStyle()
    },
    setNativeSubSync(v) {
      v = Math.round(v * 10) / 10
      if (this.nativePlayer) this.nativePlayer.subSync = v
      nativeSend('setprop', { name: 'sub-delay', value: String(v) })
    },
    // Gamma: ecualizador nativo de mpv para calzar el gamma un pelín superior de mpv en
    // HDR. 1.0 = neutro. Sin shader (sin rojo/lag).
    setNativeBright(v) {
      v = Math.min(3, Math.max(0.5, Math.round(v * 100) / 100))
      this.nativeBright = v
      localStorage.setItem('anime-native-bright', String(v))
      if (this.nativePlayer) this.nativePlayer.bright = v
      nativeSend('bright', { value: v })
    },
    // Saturación: ecualizador nativo de mpv; devuelve el "punch" que el gamma le quita.
    setNativeSat(v) {
      v = Math.min(3, Math.max(0.5, Math.round(v * 100) / 100))
      this.nativeSat = v
      localStorage.setItem('anime-native-sat', String(v))
      if (this.nativePlayer) this.nativePlayer.sat = v
      nativeSend('sat', { value: v })
    },
    toggleNativeFullscreen() {
      if (!this.nativePlayer) return
      this.nativePlayer.fullscreen = !this.nativePlayer.fullscreen
      nativeSend('fullscreen', { on: this.nativePlayer.fullscreen })
    },
    setNativeAudio(idx) {           // idx 0-based
      if (!this.nativePlayer) return
      this.nativePlayer.aid = idx + 1
      nativeSend('track', { aid: String(idx + 1) })
    },
    setNativeSub(idx) {             // idx 0-based; -1 = desactivar
      if (!this.nativePlayer) return
      this.nativePlayer.sid = idx + 1   // 0 = off
      nativeSend('track', { sid: idx < 0 ? 'no' : String(idx + 1) })
    },
    // Salto de opening que APRENDE por serie: el primer episodio saltas 82 s (el genérico) y,
    // si corriges el aterrizaje con un seek en los siguientes 10 s, esa corrección se suma al
    // salto guardado de ESA serie. Del episodio 2 en adelante "Saltar OP" cae exacto.
    nativeSkipOp() {
      const np = this.nativePlayer
      if (!np) return
      const key = `anime-skipop:${np.anime?.id || ''}`
      const amt = Math.min(200, Math.max(20, Number(localStorage.getItem(key)) || 82))
      const target = (np.pos || 0) + amt
      this._skipOpCal = { at: Date.now(), target, key, amt }
      this.nativeSeek(target)
    },
    // Escribe en la preferencia del dominio ABIERTO: el menú del reproductor ofrece los tiers
    // de anime o los de imagen real, y cada uno persiste por su lado.
    setNative4kTier(tier) {
      if (this.nativePlayer?.isLive) {
        this.nativeLiveTier = tier
        localStorage.setItem('anime-native-live', tier)
      } else {
        this.native4kTier = tier
        localStorage.setItem('anime-native-4k', tier)
      }
      if (this.nativePlayer) {
        this.nativePlayer.tier = tier
        nativeSend('shaders', { tier })
      }
    },
    closeNative() {
      if (!this.nativePlayer) return
      // Si se cierra antes del primer latido, los sidecar pendientes no deben colgarse del
      // archivo que se abra DESPUÉS (serían los subs de otro episodio).
      this._pendingSubs = null
      this._reportNativeProgress(false)
      // Parcheo optimista del progreso en la biblioteca local para que "Continuar
      // viendo"/el episodio reflejen la posición AL INSTANTE, sin esperar el ida y
      // vuelta del SSE (que también llegará y confirmará). Evita el "hasta F5".
      const np = this.nativePlayer
      try {
        // Localiza el objeto REAL de la biblioteca (por id o al_id — las tarjetas pueden pasar
        // una copia con distinto identificador) para que la mutación sea reactiva en TODA la app.
        const anime = this.library.find(a => a.id === np.anime.id || a.id === np.anime.al_id
                                          || a.al_id === np.anime.id || a.al_id === np.anime.al_id)
        const ep = anime && (anime.episodes || []).find(e => String(e.num) === String(np.ep.num))
        if (ep) {
          const pos = Math.floor(np.pos || 0)
          // Visto SOLO si realmente llegó al final: duración creíble (>2 min) y abandonó faltando
          // ≤2 min. Mismo criterio (y guarda) que el backend en anime.py::_is_watched. En cualquier
          // otro caso se CONSERVA el episodio y su minuto exacto (resume_pos), pase lo que pase.
          const watched = np.duration > 120 && pos > 0 && (np.duration - np.pos) <= 120
          ep.watched = watched
          // Espeja al backend: la posición solo se guarda a partir de 30 s (evita micro-resumes).
          ep.resume_pos = watched ? 0 : (pos > 30 ? pos : 0)
          // Recencia al instante: completado O con posición guardable → "Continuar viendo"
          // refleja/reordena sin esperar el SSE ni un F5.
          if (watched || pos > 30) anime.last_watched_at = Math.floor(Date.now() / 1000)
        }
      } catch {}
      nativeSend('stop')
      this.nativePlayer = null
    },

    /* ── Player web embebido ────────────────────────────────────────────── */
    async openPlayer(anime, ep, startPos = 0, audio = null, forceTranscode = false) {
      this.dismissAutoplay?.()
      // sin pista explícita → la última que el usuario eligió para ESTA serie
      if (audio == null) {
        try { audio = JSON.parse(localStorage.getItem('anime-audio-pref') || '{}')[anime.id] ?? 0 }
        catch (_) { audio = 0 }
      }
      this.player = { anime, ep, sess: null, loading: true, error: '', startPos, audio, forceTranscode }
      const base = ep.in_local
        ? { anime_id: anime.id, episode: ep.num, local_path: ep.local_path }
        : { anime_id: anime.id, episode: ep.num, info_hash: ep.info_hash }
      // ¿Puede este navegador decodificar HEVC (Main10)? Si sí, el backend COPIA
      // el stream sin recodificar (cero pérdida) en vez de transcodificar.
      const hevcOk = typeof MediaSource !== 'undefined'
        && (MediaSource.isTypeSupported('video/mp4; codecs="hvc1.2.4.L153.B0"')
         || MediaSource.isTypeSupported('video/mp4; codecs="hev1.2.4.L153.B0"'))
      try {
        const sess = await api.post('/api/stream/open',
          { ...base, audio, hevc_ok: hevcOk, force_transcode: forceTranscode,
            // posición explícita (cambio de audio/reintento/continuar) → el
            // backend trocea directamente desde ahí; 0 = usa el resume guardado
            ...(startPos > 0 ? { start_at: startPos } : {}) })
        if (!this.player) return           // cerrado mientras abría
        this.player.sess = sess
        this.player.loading = false
      } catch (e) {
        if (!this.player) return
        this.player.error = e?.message || 'No se pudo preparar el stream'
        this.player.loading = false
      }
    },

    closePlayer() {
      api.post('/api/stream/close').catch(() => {})
      this.player = null
    },

    /* Siguiente episodio reproducible tras el actual (para el botón/auto-next). */
    playerNext() {
      const p = this.player
      if (!p) return null
      const a = this.library.find(x => x.id === p.anime.id) || p.anime
      const eps = (a.episodes || []).filter(e => e.num > p.ep.num && e.ep_type !== 'special'
        && (e.in_local || (e.in_qbt && e.progress >= 100)))
      eps.sort((x, y) => x.num - y.num)
      return eps.length ? { anime: a, ep: eps[0] } : null
    },

    async toggleWatched(anime, ep) {
      const was = !!ep.watched
      // Optimistic update — both the passed ep and the one in library
      ep.watched = !was
      if (!was) ep.resume_pos = 0
      const libAnime = this.library.find(a => a.id === anime.id)
      const libEp = libAnime?.episodes?.find(e => e.num === ep.num)
      if (libEp) { libEp.watched = !was; if (!was) libEp.resume_pos = 0 }
      try {
        await api.post(`/api/anime/library/${anime.id}/watched`, { episode: ep.num })
      } catch (_) {
        ep.watched = was; if (libEp) libEp.watched = was
        useUiStore().toast('No se pudo actualizar', 'error')
      }
    },

    async deleteEpisode(anime, ep) {
      try {
        await api.del(`/api/anime/library/${anime.id}/episode/${ep.num}`, { body: { delete_files: true } })
        await this.loadLibrary(true)
      } catch (_) { useUiStore().toast('No se pudo borrar', 'error') }
    },

    /* Borra VARIOS episodios: N peticiones y UNA sola recarga (no una por episodio — la
     * biblioteca son ~480 KB y recargarla 6 veces seguidas es medio mega por episodio para nada).
     *
     * Cuenta los fallos aparte y los dice: borrar 6 y que se borren 4 no puede leerse como éxito
     * (regla del repo: "falló" y "no había" nunca son lo mismo). */
    async deleteEpisodes(anime, eps) {
      if (!eps?.length) return { deleted: 0, failed: 0 }
      const ui = useUiStore()
      let deleted = 0, failed = 0
      for (const ep of eps) {
        try {
          await api.del(`/api/anime/library/${anime.id}/episode/${ep.num}`, { body: { delete_files: true } })
          deleted++
        } catch (_) { failed++ }
      }
      await this.loadLibrary(true)
      if (failed) ui.toast(`${deleted} borrado(s) · ${failed} no se pudieron borrar`, 'warn')
      else ui.toast(`${deleted} episodio(s) borrados`, 'ok')
      return { deleted, failed }
    },

    async setStatus(anime, status) {
      const prev = anime.status
      anime.status = status
      try { await api.post(`/api/anime/library/${anime.id}/status`, { status }) }
      catch (_) { anime.status = prev; useUiStore().toast('No se pudo cambiar el estado', 'error') }
    },

    async loadSkip(anime, ep) {
      const malId = anime?.mal_id
      if (!malId) return
      const key = `${anime.id}_${ep.num}`
      if (this.skipTimes[key] !== undefined) return
      this.skipTimes[key] = {}
      try { this.skipTimes[key] = await api.get(`/api/anime/skip_times/${malId}/${ep.num}`) || {} }
      catch (_) {}
    },

    async loadEpInfo(anime, ep) {
      // Solo alterna el panel; la descripción sale de epMeta (TMDB). Se conserva epInfo como
      // reserva (filler/recap y sinopsis de MAL) para animes sin match TMDB.
      const key = `${anime.id}_${ep.num}`
      this.epInfoOpen = this.epInfoOpen === key ? null : key
      // Si TMDB ya nos dio descripción para este episodio, no pedimos nada a MAL.
      if (this.epMeta[anime.id]?.[ep.num]?.overview) return
      const malId = anime?.mal_id
      if (!malId) return
      const jkey = `${malId}_${ep.num}`
      if (this.epInfo[jkey] !== undefined) return
      this.epInfo[jkey] = null
      try { this.epInfo[jkey] = await api.get(`/api/anime/episode_info/${malId}/${ep.num}`) || {} }
      catch (_) { this.epInfo[jkey] = {} }
    },

    // Título + descripción de cada episodio para el detalle, en UNA carga. Prefiere TMDB
    // (mejores nombres/descripciones, temporada correcta vía season_by_year); si no hay match
    // TMDB, cae a los títulos de MAL. Cacheado en el store por anime; backend cachea 7 días.
    async loadEpMeta(anime) {
      if (!anime?.id || this.epMeta[anime.id] !== undefined) return
      this.epMeta[anime.id] = {}
      try {
        const d = await api.get(`/api/anime/episode_meta/${anime.id}`)
        if (d?.source === 'tmdb' && d.meta && Object.keys(d.meta).length) {
          this.epMeta[anime.id] = d.meta
          return
        }
      } catch (_) { /* cae a MAL */ }
      // Reserva: títulos de MAL (solo title).
      if (anime.mal_id) {
        try {
          const t = (await api.get(`/api/anime/episode_titles/${anime.mal_id}`))?.titles || {}
          const m = {}
          for (const [num, v] of Object.entries(t)) m[num] = { title: v.title || '', overview: '' }
          this.epMeta[anime.id] = m
        } catch (_) { /* deja {} */ }
      }
    },

    /* ── Auto-play ──────────────────────────────────────────────────────── */
    // `opts` (progressKey/playlist/onProgress) viaja con el autoplay: sin él, encadenar el
    // siguiente episodio de una serie occidental caería en `play()`, que es del dominio anime.
    showAutoplay(anime, ep, opts = null) {
      this.dismissAutoplay()
      this.autoplay = { anime, ep, opts }
      this.autoplaySeconds = 10
      autoplayTimer = setInterval(() => {
        this.autoplaySeconds--
        if (this.autoplaySeconds <= 0) {
          const a = this.autoplay
          this.dismissAutoplay()
          if (a) this.playAutoplay(a)
        }
      }, 1000)
    },
    // Punto único de arranque del autoplay (lo usan el temporizador y el botón "Ver ahora").
    playAutoplay(a) {
      if (!a) return
      if (a.opts) return this.playNative(a.anime, a.ep, a.ep.pos || 0, a.opts)
      return this.play(a.anime, a.ep)
    },
    dismissAutoplay() {
      if (autoplayTimer) { clearInterval(autoplayTimer); autoplayTimer = null }
      this.autoplay = null
    },

    /* ── SSE handlers ───────────────────────────────────────────────────── */
    _onWatched(ev) {
      const anime = this.library.find(a => a.id === ev.anime_id)
      if (!anime) return
      const ep = (anime.episodes || []).find(e => String(e.num) === String(ev.ep_str))
      if (ep) {
        ep.watched = ev.watched !== undefined ? !!ev.watched : true
        ep.resume_pos = 0
        if (ev.duration) ep.duration = ev.duration
      }
      if (ev.last_watched_at) anime.last_watched_at = ev.last_watched_at
      anime.last_ep = Number(ev.ep_str) || anime.last_ep
      // MPV finished → autoplay next.
      // OJO: sólo para el mpv EXTERNO (sin overlay in-app). Con el reproductor NATIVO
      // abierto no debemos lanzar aquí el modal: el backend marca `watched` ya en los
      // últimos ~2 min (no sólo al terminar), así que saltaría en MEDIO de la pantalla,
      // encima del player nativo y sin poder clickearlo. El fin REAL del nativo lo
      // encadena su propio handler de 'time' (closeNative → showAutoplay), ya clickeable.
      if (ev.from_mpv && ev.watched && !this.nativePlayer && !this.autoplay) {
        const next = nextUnwatchedEp(anime)
        if (next) this.showAutoplay(anime, next)
      }
    },
    _onPosition(ev) {
      const anime = this.library.find(a => a.id === ev.anime_id)
      if (!anime) return
      const ep = (anime.episodes || []).find(e => String(e.num) === String(ev.ep_str))
      if (ep) {
        ep.resume_pos = ev.position || 0
        if (ev.duration) ep.duration = ev.duration
      }
      // Progreso parcial también actualiza la recencia → "Continuar viendo" reordena
      // y muestra la serie al instante (el backend lo manda al guardar posición >30s).
      if (ev.last_watched_at) anime.last_watched_at = ev.last_watched_at
      // …y CUÁL era el episodio, que es lo que ancla "Seguir viendo". Sin esto el ancla no se
      // movería hasta la siguiente recarga de la biblioteca.
      anime.last_ep = Number(ev.ep_str) || anime.last_ep
    },

    /* ── qBittorrent ────────────────────────────────────────────────────── */
    hasActiveQbt() {
      return this.library.some(a => (a.episodes || []).some(e => e.in_qbt && e.progress < 100))
    },
    // Progreso de descarga EN VIVO: el backend NO emite SSE de progreso de qBittorrent, así que
    // sondeamos /qbt/list mientras haya torrents activos y parcheamos los episodios por info_hash.
    // Al completar un torrent recargamos la biblioteca (para reflejar in_local) y paramos el poll.
    _patchDlProgress(torrents) {
      const byHash = {}
      for (const t of (torrents || [])) byHash[(t.hash || '').toLowerCase()] = t
      let active = false, justDone = false
      for (const a of this.library) {
        for (const e of (a.episodes || [])) {
          const h = (e.info_hash || '').toLowerCase()
          const t = h && byHash[h]
          if (!t) continue
          if ((e.progress ?? 0) < 100 && t.progress >= 100) justDone = true
          e.progress = t.progress; e.in_qbt = true
          if (t.progress < 100) active = true
        }
      }
      return { active, justDone }
    },
    _ensureDlPolling() {
      if (dlPoll || !this.hasActiveQbt()) return
      dlPoll = setInterval(async () => {
        let torrents = []
        try { torrents = await api.get('/api/anime/qbt/list') || [] } catch (_) { return }
        this.qbtTorrents = torrents
        const { active, justDone } = this._patchDlProgress(torrents)
        if (justDone) await this.loadLibrary(true)   // recoge in_local + estado final
        if (!active && !this.hasActiveQbt()) { clearInterval(dlPoll); dlPoll = null }
      }, 3000)
    },
    async checkQbt() {
      try {
        const d = await api.get('/api/anime/qbt/status')
        this.qbt.connected = d.connected
        this.qbt.version = d.version || ''
        if (d.url) this.qbt.url = d.url
      } catch (_) {}
    },
    async configureQbt() {
      try {
        const d = await api.post('/api/anime/qbt/configure', {
          url: this.qbt.url, username: this.qbt.username, password: this.qbt.password,
        })
        this.qbt.connected = d.connected
        const ui = useUiStore()
        if (d.connected) { ui.toast('qBittorrent conectado ✓', 'ok'); this.loadQbt() }
        else ui.toast('No se pudo conectar a qBittorrent', 'error')
      } catch (_) { useUiStore().toast('Error configurando qBittorrent', 'error') }
    },
    async loadQbt() {
      this.qbtLoading = true
      this.qbtError = ''
      try { this.qbtTorrents = await api.get('/api/anime/qbt/list') || [] }
      catch (e) { this.qbtTorrents = []; this.qbtError = e?.body || e?.message || 'qBittorrent no responde' }
      finally { this.qbtLoading = false }
    },
    async qbtAction(action, hash, deleteFiles = false) {
      try {
        await api.post('/api/anime/qbt/action', { action, hash, delete_files: deleteFiles })
        if (action === 'recheck') {
          useUiStore().toast('Recalculando archivos… espera unos segundos', 'info')
          await new Promise(r => setTimeout(r, 3000))
        }
        await this.loadQbt()
        // Also refresh the library so episode states (e.g. "descargando") update in the
        // series detail when a torrent is paused/deleted, not just the Downloads list.
        await this.loadLibrary(true)
      } catch (e) { useUiStore().toast('Error: ' + (e.message || 'qBittorrent'), 'error') }
    },

    /* ── Seasonal ───────────────────────────────────────────────────────── */
    async loadSeasonal() {
      this.seasonalLoading = true
      this.seasonalError = ''
      try {
        const p = new URLSearchParams({ sort: this.seasonSort })
        if (this.season) p.set('season', this.season)
        if (this.year) p.set('year', String(this.year))
        const d = await api.get(`/api/anime/seasonal?${p}`)
        this.seasonal = d.results || []
        if (!this.season) this.season = d.season || ''
        if (!this.year) this.year = d.year || 0
      } catch (e) { this.seasonal = []; this.seasonalError = e?.body || e?.message || 'Error desconocido' }
      finally { this.seasonalLoading = false }
    },
    seasonNav(dir) {
      const S = ['WINTER', 'SPRING', 'SUMMER', 'FALL']
      const idx = S.indexOf(this.season)
      if (idx === -1) return this.loadSeasonal()
      let ni = idx + dir
      if (ni < 0) { ni = 3; this.year-- }
      else if (ni > 3) { ni = 0; this.year++ }
      this.season = S[ni]
      this.loadSeasonal()
    },
    isInLibrary(anime) {
      if (!anime) return false
      return this.library.some(a =>
        (anime.al_id && a.al_id === anime.al_id) ||
        (anime.mal_id && a.mal_id === anime.mal_id) ||
        (anime.title && a.title === anime.title))
    },
    async addToLibrary(anime) {
      try {
        const d = await api.post('/api/anime/library/add', {
          al_id: anime.al_id, mal_id: anime.mal_id,
          title: anime.title, title_romaji: anime.title_romaji || '',
          cover: anime.cover || '', banner: anime.banner || '',
          total_episodes: typeof anime.episodes === 'number' ? anime.episodes : (anime.total_episodes || null),
          format: anime.format || '', track_only: true,
        })
        if (d.ok) { useUiStore().toast(`"${anime.title}" añadido a Mi Anime`, 'ok'); await this.loadLibrary(true) }
      } catch (_) { useUiStore().toast('Error al añadir a biblioteca', 'error') }
    },

    /* ── Explore (AniList browse) ───────────────────────────────────────── */
    _exploreSortKey() {
      return { score: 'SCORE_DESC', popularity: 'POPULARITY_DESC', trending: 'TRENDING_DESC' }[this.exploreSort] || 'SCORE_DESC'
    },
    async loadExplore(append = false) {
      this.exploreLoading = true
      this.exploreError = ''
      try {
        if (!append) this.explorePage = 1
        const p = new URLSearchParams({ sort: this._exploreSortKey(), page: String(this.explorePage) })
        // género o tag: la lista combina ambos, así que resolvemos el tipo
        if (this.exploreGenre) {
          const g = this.exploreGenreList.find(x => x.name === this.exploreGenre)
          p.set(g?.type === 'tag' ? 'tag' : 'genre', this.exploreGenre)
        }
        if (this.exploreYear) p.set('year', String(this.exploreYear))
        if (this.exploreFormat) p.set('format', this.exploreFormat)
        const d = await api.get(`/api/anilist/anime_top?${p}`)
        const rows = d.results || []
        this.explore = append ? [...this.explore, ...rows] : rows
        this.exploreHasNext = !!d.hasNextPage
      } catch (e) {
        if (!append) this.explore = []
        this.exploreError = e?.body || e?.message || 'Error desconocido'
      } finally { this.exploreLoading = false }
    },
    async loadExploreMore() {
      if (this.exploreLoading || !this.exploreHasNext) return
      this.explorePage += 1
      await this.loadExplore(true)
    },
    async loadExploreGenres() {
      if (this.exploreGenreList.length) return
      try { this.exploreGenreList = (await api.get('/api/anilist/genres')) || [] } catch (_) { this.exploreGenreList = [] }
    },

    /* ── Search + torrents ──────────────────────────────────────────────── */
    async searchAnime() {
      const q = this.searchQuery.trim()
      // Al BORRAR hasta menos de 2 letras hay que limpiar, o se quedan colgados los resultados
      // de la consulta anterior bajo una caja que ya no dice eso.
      if (q.length < 2) { this._searchReqId++; this.searchResults = []; this.searchError = ''; this.searchLoading = false; return }
      // Anti-carrera: al buscar mientras se escribe, la respuesta de "nar" puede llegar DESPUÉS
      // que la de "naruto" y pisarla. Sólo escribe el resultado la petición más reciente.
      const rid = ++this._searchReqId
      this.searchLoading = true
      this.searchResults = []
      this.searchError = ''
      try {
        const d = await api.get(`/api/anime/search?q=${encodeURIComponent(q)}`)
        if (rid !== this._searchReqId) return
        if (Array.isArray(d)) this.searchResults = d
      } catch (e) {
        if (rid !== this._searchReqId) return
        // Una búsqueda que revienta NO es una búsqueda sin resultados: decir «nada coincide»
        // manda al usuario a probar otro título cuando el problema es que no hay conexión.
        this.searchError = e?.message || 'No se pudo contactar con el servidor.'
        useUiStore().toast('Error buscando anime', 'error')
      }
      // Sólo la petición vigente apaga el spinner: si no, una respuesta vieja lo apagaría
      // mientras la nueva sigue en vuelo y la rejilla parpadearía a "vacío".
      finally { if (rid === this._searchReqId) this.searchLoading = false }
    },

    /* Buscar MIENTRAS se escribe (mismo patrón que `discovery.js`). Enter sigue funcionando y
       dispara ya, sin esperar al debounce. */
    onSearchInput() {
      clearTimeout(this._searchTimer)
      this._searchTimer = setTimeout(() => this.searchAnime(), 350)
    },
    submitSearch() { clearTimeout(this._searchTimer); this.searchAnime() },

    closeTorrents() { this.torrentAnime = null },

    // extraQueries are fetched with category '1_0' (all anime) to also surface Spanish/Non-English
    // group releases that the user's selected category (1_2 = English-translated) would miss.
    async _nyaaMultiFetch(queries, extraQueries = []) {
      const cat = this.torrentCategory
      const allFetches = [
        ...queries.map(q => ({ q, cat })),
        ...extraQueries.map(q => ({ q, cat: '1_0' })),
      ]
      const lists = await Promise.all(allFetches.map(({ q, cat: c }) =>
        api.get(`/api/anime/torrents?${new URLSearchParams({ q, category: c })}`).catch(() => [])))
      const seen = new Set(); const merged = []
      for (const list of lists) for (const t of (list || [])) {
        const key = t.info_hash || t.title
        if (!seen.has(key)) { seen.add(key); merged.push(t) }
      }
      return merged
    },
    // Strip trailing season/ordinal suffix so AniList synonyms become usable Nyaa queries.
    // "X 4th Season" → "X", "X Season 4" → "X", "X Part 2" → "X"
    _stripSeasonSuffix(s) {
      return s
        .replace(/\s+(?:\d+(?:st|nd|rd|th)\s+(?:Season|Cour)|(?:Season|Cour|Part|Series)(?:\s+\d+)?)\s*$/i, '')
        .replace(/\s+\d+$/, '')
        .trim()
    },

    // Extracts the reusable title "shapes" used to build Nyaa queries:
    //   fullTitle   — primary variant, colons flattened ("Honzuki no Gekokujou Shisho ni Naru…")
    //   baseTitle   — segment before the first colon ("Honzuki no Gekokujou") or null
    //   sub         — segment after the colon in the anime title ("Ryoushu no Youjo") or null
    //   synonyms    — long AniList synonyms beyond the 5-cap, season suffix stripped. These often
    //                 carry the full official franchise title (e.g. "…Shudan wo Erandeiraremasen
    //                 4th Season") which, stripped, matches ALL erai-raws episodes regardless of
    //                 how the per-episode subtitle is romanized.
    _titleParts() {
      const primary = this.torrentVariants[0] || ''
      const fullTitle = primary.replace(/[:：]/g, ' ').replace(/\s+/g, ' ').trim()
      const colonIdx = primary.indexOf(':')
      const baseTitle = colonIdx > 5 ? primary.substring(0, colonIdx).trim() : null

      const animeTitle = this.torrentAnime
        ? (this.torrentAnime.title_romaji || this.torrentAnime.title || '')
        : primary
      const subMatch = animeTitle.match(/^[^:：]+[：:]\s*(.+)$/)
      const sub = subMatch && subMatch[1].trim().length >= 8 ? subMatch[1].trim() : null

      const synonyms = []
      for (const syn of (this.torrentAllVariants || []).slice(5)) {
        if (!/[a-zA-Z]/.test(syn)) continue  // skip CJK-only synonyms
        const cleaned = this._stripSeasonSuffix(syn).replace(/[:：]/g, ' ').replace(/\s+/g, ' ').trim()
        if (cleaned.length >= 15 && cleaned.toLowerCase() !== fullTitle.toLowerCase()) synonyms.push(cleaned)
      }
      return { fullTitle, baseTitle, sub, synonyms }
    },

    // Builds supplementary Nyaa queries (fetched with cat 1_0) for the INITIAL broad load:
    //   1. fullTitle/baseTitle/sub + batch — covers batch releases from every naming convention
    //   2. baseTitle + "- 01" (always-on)  — ep1 anchor even without a targetEp set
    //   3. long-synonym broad search       — surfaces the franchise's recent episodes
    // Per-episode coverage is handled lazily on demand by fetchEpisodeTorrents() — see that action
    // for why early episodes need a targeted query (Nyaa RSS caps at 75 newest, starving old eps).
    _buildExtraQueries() {
      if (!this.torrentVariants.length) return []
      const { fullTitle, baseTitle, sub, synonyms } = this._titleParts()

      const extra = new Set()
      extra.add(fullTitle + ' batch')
      if (baseTitle) extra.add(baseTitle + ' batch')
      if (sub) extra.add(sub + ' batch')
      if (baseTitle) extra.add(`${baseTitle} - 01`)
      for (const syn of synonyms) extra.add(syn)

      const varLower = new Set(this.torrentVariants.map(v => v.toLowerCase().replace(/\s+/g, ' ')))
      return [...extra].filter(q => !varLower.has(q.toLowerCase().replace(/\s+/g, ' ')))
    },

    // Targeted queries for ONE episode: "<title> - 0N" across every title shape. These are the only
    // way to retrieve a specific early episode's torrents — Nyaa RSS returns just the 75 newest
    // items (no sort support), so for an airing show the recent episodes saturate the window and
    // episodes 1-5 collapse to 1-2 results. A targeted "Honzuki no Gekokujou - 02" returns 36.
    _episodeQueries(epNum) {
      const pad = String(epNum).padStart(2, '0')
      const { fullTitle, baseTitle, sub, synonyms } = this._titleParts()
      const out = new Set()
      out.add(`${fullTitle} - ${pad}`)
      if (baseTitle) out.add(`${baseTitle} - ${pad}`)
      if (sub) out.add(`${sub} - ${pad}`)
      for (const syn of synonyms) out.add(`${syn} - ${pad}`)
      return [...out]
    },

    // Lazy deep-fetch for a single episode's torrents, merged (dedup by info_hash) into the list.
    // Triggered when the user expands an episode group: brings a sparse group (1-3 torrents that
    // happened to fall inside the RSS window) up to its true count. cat 1_0 to catch every language.
    async fetchEpisodeTorrents(epNum) {
      if (!epNum || epNum <= 0) return
      if (this.epFetch[epNum]) return            // already loading or done
      if (!this.torrentVariants.length) return
      this.epFetch = { ...this.epFetch, [epNum]: 'loading' }
      try {
        const queries = this._episodeQueries(epNum)
        const lists = await Promise.all(queries.map(q =>
          api.get(`/api/anime/torrents?${new URLSearchParams({ q, category: '1_0' })}`).catch(() => [])))
        const seen = new Set(this.torrents.map(t => t.info_hash || t.title))
        const additions = []
        for (const list of lists) for (const t of (list || [])) {
          const key = t.info_hash || t.title
          if (!seen.has(key)) { seen.add(key); additions.push(t) }
        }
        if (additions.length) this.torrents = [...this.torrents, ...additions]
      } catch (_) {
      } finally {
        this.epFetch = { ...this.epFetch, [epNum]: 'done' }
      }
    },
    async openTorrents(anime, targetEpisode = null) {
      this.hidePreview()
      // Drill into the torrent panel under the 'search' sub-tab. A history entry is
      // pushed so back returns to where we came from (the panel is transient — any
      // back/forward closes it, see ui._apply).
      this.detailId = null
      this.sub = 'search'
      this.torrentAnime = anime
      useUiStore().pushNav()
      this.torrents = []
      // ep:'all' even with a target — the targetEp group filter keeps the episode + batches.
      this.flt = { lang: 'all', hideDead: true, quality: '', group: '', ep: 'all', sort: 'relevance' }
      const fmt = anime.format || ''
      this.targetEp = (fmt === 'MOVIE' || fmt === 'MUSIC') ? null : (targetEpisode && targetEpisode > 0 ? targetEpisode : null)
      const allVariants = await this._resolveTorrentVariants(anime)
      this.torrentAllVariants = allVariants
      const variants = allVariants.slice(0, 5)  // first 5 for main search + alias display
      this.torrentVariants = variants
      this.torrentQuery = variants[0] || ''
      this.torrentsLoading = true
      this.epFetch = {}
      try { this.torrents = await this._nyaaMultiFetch(variants, this._buildExtraQueries()) }
      catch (_) { useUiStore().toast('Error buscando en Nyaa', 'error') }
      finally { this.torrentsLoading = false }
      // When opened on a specific episode, deep-fetch it immediately so its group is complete.
      if (this.targetEp) this.fetchEpisodeTorrents(this.targetEp)
    },
    // Lista de alias a buscar en Nyaa: los 3 títulos de la tarjeta + SINÓNIMOS/títulos
    // alternativos de AniList (romaji/inglés/nativo/akas) — no depende sólo del nombre principal,
    // así una release nombrada con otro alias también aparece. Cap a 5 para no saturar Nyaa.
    async _resolveTorrentVariants(anime) {
      const base = [...new Set([anime.title_romaji, anime.title_english, anime.title].map(t => (t || '').trim()).filter(Boolean))]
      let variants = base
      try {
        const qs = new URLSearchParams()
        if (anime.al_id) qs.set('al_id', anime.al_id)
        if (base[0]) qs.set('title', base[0])
        const syn = await api.get('/api/anime/title_variants?' + qs.toString())
        if (Array.isArray(syn) && syn.length) {
          const seen = new Set(); const merged = []
          for (const t of [...base, ...syn]) {
            const k = (t || '').toLowerCase().replace(/[^a-z0-9]/g, '')
            if (t && k && !seen.has(k)) { seen.add(k); merged.push(t) }
          }
          variants = merged
        }
      } catch (_) {}
      return variants  // full list; callers apply their own cap
    },
    // "Refinar en Nyaa": si el usuario NO tocó la query (sigue siendo el alias principal) se
    // re-buscan TODAS las variantes (sinónimos incluidos); si escribió algo propio, búsqueda
    // literal de eso (no se le añaden alias para no ensanchar su filtro).
    async searchTorrents() {
      const q = this.torrentQuery.trim()
      if (!q) return
      const custom = q !== (this.torrentVariants[0] || '').trim()
      this.torrentsLoading = true; this.torrents = []; this.epFetch = {}
      try {
        // Custom query: search literally without extras (user is explicitly filtering)
        // Default query: re-run all variants plus supplementary batch/episode queries
        this.torrents = custom
          ? (await api.get(`/api/anime/torrents?${new URLSearchParams({ q, category: this.torrentCategory })}`) || [])
          : await this._nyaaMultiFetch(this.torrentVariants.length ? this.torrentVariants : [q], this._buildExtraQueries())
      }
      catch (_) { useUiStore().toast('Error buscando en Nyaa', 'error') }
      finally { this.torrentsLoading = false }
      if (!custom && this.targetEp) this.fetchEpisodeTorrents(this.targetEp)
    },

    isAdding(key) { return this.addingHashes.includes(key) },
    isAdded(key) {
      if (this.addedHashes.includes(key)) return true
      if (!key || key.startsWith('http') || !this.torrentAnime) return false
      const cur = this.torrentAnime
      const lib = this.library.find(a => (cur.al_id && a.al_id === cur.al_id) || (cur.mal_id && a.mal_id === cur.mal_id))
      // info_hash is kept on the episode even after qBittorrent removes/deletes the
      // torrent — only count it as "added" while it's still live there (in_qbt).
      return !!(lib?.episodes || []).find(e => e.info_hash === key && e.in_qbt)
    },
    async addToQbt(torrent) {
      const ui = useUiStore()
      if (!this.qbt.connected) { ui.toast('qBittorrent no conectado — ve a Descargas para configurarlo', 'error'); return }
      const key = torrent.info_hash || torrent.torrent_url
      if (this.addingHashes.includes(key)) return
      this.addingHashes.push(key)
      try {
        const d = await api.post('/api/anime/qbt/add', {
          magnet: torrent.magnet || '', torrent_url: torrent.torrent_url,
          anime_title: this.torrentAnime?.title || '',
        })
        if (d.ok) {
          ui.toast('Torrent agregado ✓', 'ok')
          this.addedHashes.push(key)
          setTimeout(() => { this.addedHashes = this.addedHashes.filter(k => k !== key) }, 2500)
          const a = this.torrentAnime
          if (a) {
            api.post('/api/anime/library/add', {
              al_id: a.al_id, mal_id: a.mal_id, title: a.title, title_romaji: a.title_romaji || '',
              cover: a.cover || '', total_episodes: typeof a.episodes === 'number' ? a.episodes : (a.total_episodes || null), format: a.format || '',
              episode: torrent.episode === 0 ? 0 : torrent.episode,
              torrent_title: torrent.title, info_hash: torrent.info_hash || '',
            }).then(() => this.loadLibrary(true)).catch(() => {})
          }
          // El torrent tarda 1-2s en aparecer en qBittorrent con progreso → un refresco extra
          // arranca el polling en vivo aunque el primer loadLibrary aún no lo viera.
          setTimeout(() => this.loadLibrary(true), 2500)
        } else ui.toast('qBittorrent: ' + (d.msg || d.error || 'Error'), 'error')
      } catch (_) { ui.toast('Error conectando qBittorrent', 'error') }
      finally { this.addingHashes = this.addingHashes.filter(k => k !== key) }
    },

    /* ── Subtitles ──────────────────────────────────────────────────────── */
    subKey(anime, ep) { return `${anime.id}_${ep.ep_type === 'special' ? 'sp' : 'ep'}${ep.num}` },

    /* `opts.onPick(choice)` convierte el modal en un SELECTOR: en vez de lanzar la traducción al
     * instante, devuelve la fuente elegida a quien lo abrió (lo usa el lote para dejar que elijas
     * fuente episodio a episodio). Sin `onPick` se comporta como siempre. */
    async translateSubs(anime, ep, opts = {}) {
      const ui = useUiStore()
      const key = this.subKey(anime, ep)
      this.subFetching = key
      const loadId = ui.toast('Buscando subtítulos en español…', 'loading', 0)
      const p = new URLSearchParams({
        info_hash: ep.info_hash || '', episode: ep.num, anime_id: anime.id || '',
        ep_type: ep.ep_type || 'episode', ...(ep.local_path ? { local_path: ep.local_path } : {}),
      })
      // Series/películas occidentales no están en la biblioteca de anime: mandan su propia
      // identidad. `season` importa de verdad — sin ella OpenSubtitles asume S01 y devuelve
      // el subtítulo de otro episodio (ver comentario en subtitle.py).
      if (ep.season) p.set('season', ep.season)
      for (const t of ep.titles || []) p.append('titles', t)
      try {
        const d = await api.get(`/api/subtitle/tracks?${p}`)
        const tracks = d.tracks || [], ext = d.external_tracks || [], spa = d.spanish_tracks || []
        const embedded = d.embedded_spanish || []
        if (spa.length || tracks.length || ext.length || embedded.length) {
          this.subTrackModal = { anime, ep, tracks, externalTracks: ext, spanishTracks: spa,
                                 embeddedSpanish: embedded, missingKeys: d.sources_missing_key || [],
                                 sourcesReport: d.sources_report || [],
                                 onPick: opts.onPick || null }
          // El español que YA viene dentro del archivo manda sobre lo que se pueda descargar:
          // suele ser el oficial del grupo y el player lo elige solo.
          if (embedded.length) ui.toast(`Este episodio ya trae ${embedded.length} pista(s) en español`, 'ok', 3500)
          else if (spa.length) ui.toast(`Ya hay ${spa.length} subtítulo(s) en español`, 'ok', 3000)
          return
        }
        // "No hay subtítulos" y "las fuentes se cayeron" NO son lo mismo: con lo segundo el
        // subtítulo puede existir y reintentar más tarde tiene sentido. Se dice cuál falló.
        const rotas = (d.sources_report || []).filter(r => r.status === 'error').map(r => r.source)
        const hint = (d.sources_missing_key || []).length ? ` (sin API key: ${d.sources_missing_key.join(', ')})` : ''
        if (rotas.length) ui.toast(`No se encontraron subtítulos, pero fallaron ${rotas.join(', ')} — puede que sí existan`, 'warn', 9000)
        else ui.toast(`No se encontraron subtítulos${hint}`, 'warn', 7000)
      } catch (_) { ui.toast('Error obteniendo pistas de subtítulos', 'error') }
      finally { this.subFetching = null; ui.dismissToast(loadId) }
    },

    async directInject(anime, ep, subInfo) {
      const ui = useUiStore()
      const key = this.subKey(anime, ep)
      this.subTrackModal = null
      this.subTasks[key] = { status: 'injecting', progress: 50, message: 'Descargando subtítulo…' }
      try {
        const d = await api.post('/api/subtitle/inject_direct', {
          info_hash: ep.info_hash || '', episode: ep.num, anime_id: anime.id,
          ...(ep.local_path ? { local_path: ep.local_path } : {}), external_sub: subInfo,
        })
        if (d.error) { this.subTasks[key] = { status: 'error', progress: 0, message: d.error }; ui.toast(d.error, 'error'); return }
        this.subTasks[key] = { status: 'done', progress: 100, message: '¡Completado!' }
        ui.toast(`✓ Subtítulos ESP añadidos: ${d.file || ''}`, 'ok')
      } catch (_) { this.subTasks[key] = { status: 'error', progress: 0, message: 'Error' }; ui.toast('Error al inyectar', 'error') }
    },

    // `force` NO se manda a ciegas. El backend responde 409 si el episodio ya tiene español —
    // una guarda pensada justo para esto que este método anulaba mandando force:true SIEMPRE,
    // dejándola en código muerto. Ahora el 409 se convierte en una pregunta: sólo se fuerza si
    // el usuario dice que sí, sabiendo que ya tiene español.
    async startTranslate(anime, ep, subIndex = 0, externalSub = null, force = false) {
      const ui = useUiStore()
      const key = this.subKey(anime, ep)
      this.subTrackModal = null
      this.subTasks[key] = { status: 'starting', progress: 0, message: 'Iniciando…' }
      const body = {
        info_hash: ep.info_hash || '', episode: ep.num, ep_type: ep.ep_type || 'episode',
        anime_id: anime.id, sub_index: subIndex, force,
        ...(ep.local_path ? { local_path: ep.local_path } : {}),
        ...(externalSub ? { external_sub: externalSub } : {}),
      }
      try {
        const d = await api.post('/api/subtitle/translate', body)
        if (d.error) { this.subTasks[key] = { status: 'error', progress: 0, message: d.error }; ui.toast(d.error, 'error'); return }
        this.subTasks[key] = { ...this.subTasks[key], task_id: d.task_id }
        this._subPoll(key, d.task_id)
      } catch (e) {
        if (e?.status === 409 && !force) {
          delete this.subTasks[key]
          if (await ui.confirm({
            title: 'Ya hay subtítulos en español', confirmLabel: 'Traducir igualmente',
            body: 'Este episodio ya tiene subtítulos en español.\n' +
                  'Traducir con IA creará otra pista, probablemente peor que la que ya tienes (si es oficial del grupo).',
          })) {
            return this.startTranslate(anime, ep, subIndex, externalSub, true)
          }
          return
        }
        this.subTasks[key] = { status: 'error', progress: 0, message: e.body || 'Error' }
        ui.toast(e.body || 'Error al traducir', 'error')
      }
    },

    _subPoll(key, taskId) {
      if (subPollers[key]) clearInterval(subPollers[key])
      subPollers[key] = setInterval(async () => {
        try {
          const d = await api.get(`/api/subtitle/status/${taskId}`)
          this.subTasks[key] = { ...d, task_id: taskId }
          if (['done', 'error', 'cancelled'].includes(d.status)) {
            clearInterval(subPollers[key]); delete subPollers[key]
            if (d.status === 'done') useUiStore().toast(`✓ Subtítulos ESP: ${(d.output || '').split('/').pop() || ''}`, 'ok')
            else if (d.status === 'error') useUiStore().toast(`Error: ${d.message}`, 'error')
          }
        } catch (_) {
          clearInterval(subPollers[key]); delete subPollers[key]
          this.subTasks[key] = { status: 'cancelled', message: 'Tarea perdida', task_id: taskId }
        }
      }, 1500)
    },

    async reinjectSub(anime, ep) {
      const ui = useUiStore()
      const key = this.subKey(anime, ep)
      this.subTrackModal = null
      this.subTasks[key] = { status: 'injecting', progress: 50, message: 'Re-inyectando…' }
      try {
        await api.post('/api/subtitle/reinject', {
          info_hash: ep.info_hash || '', episode: ep.num, anime_id: anime.id,
          ...(ep.local_path ? { local_path: ep.local_path } : {}), force: true,
        })
        this.subTasks[key] = { status: 'done', progress: 100, message: 'Re-inyectado ✓' }
        ui.toast('Subtítulo español re-inyectado ✓', 'ok')
      } catch (e) { this.subTasks[key] = { status: 'error', progress: 0, message: e.body || 'Error' }; ui.toast('Error al re-inyectar', 'error') }
    },

    async cancelTranslate(anime, ep) {
      const key = this.subKey(anime, ep)
      const tid = this.subTasks[key]?.task_id
      if (subPollers[key]) { clearInterval(subPollers[key]); delete subPollers[key] }
      if (tid) { try { await api.post(`/api/subtitle/cancel/${tid}`, {}) } catch (_) {} }
      this.subTasks[key] = { ...this.subTasks[key], status: 'cancelled', message: 'Cancelado' }
    },

    /* ── Local scan paths ───────────────────────────────────────────────── */
    async openScan() {
      this.scan.show = true
      await this.loadScanFolders()
    },
    async loadScanFolders() {
      this.scan.loading = true
      try {
        this.scan.paths = await api.get('/api/anime/scanpaths') || []
        // `_q`/`_results`/`_searching` son de la búsqueda manual, uno por fila.
        this.scan.folders = (await api.get('/api/anime/scan/folders') || [])
          .map(f => ({ ...f, _q: '', _results: [], _searching: false, _sugLoading: false }))
      } catch (_) { this.scan.folders = [] }
      // Las carpetas se PINTAN ya. Las sugerencias son una llamada a AniList por carpeta y antes
      // iban en serie DENTRO del `loading`: con 56 sin enlazar, el modal se quedaba en blanco
      // medio minuto por un adorno. Ahora caen de fondo, cada fila con su propia rueda.
      finally { this.scan.loading = false }
    },
    /* Sugerencia de UNA fila, pedida cuando esa fila entra en pantalla.
     *
     * Pedirlas todas al abrir era el error: son 56 llamadas a AniList, que corta a ~30/min, así
     * que la propia cola se castigaba con reintentos y esperas — y todo eso para adornar filas
     * que el usuario no estaba mirando. De dos en dos y sólo lo visible: la primera pantalla está
     * lista en un momento y bajar no acumula deuda. Las ya cacheadas en el servidor son gratis. */
    async suggestFor(f) {
      if (!f || f.mapped_id || f.suggestion != null || f._sugLoading) return
      f._sugLoading = true
      this._sugQ = this._sugQ || []
      this._sugQ.push(f)
      if ((this._sugActive || 0) >= 2) return
      this._sugActive = (this._sugActive || 0) + 1
      try {
        for (let n = this._sugQ.shift(); n; n = this._sugQ.shift()) {
          this.scan.suggesting = this._sugQ.length + 1
          n.suggestion = await api.get(`/api/anime/scan/suggest?name=${encodeURIComponent(n.name)}`).catch(() => null)
          n._sugLoading = false
        }
      } finally {
        this._sugActive--
        this.scan.suggesting = this._sugQ.length
      }
    },
    async addScanPath(path) {
      const p = (path || this.scan.newPath || '').trim(); if (!p) return
      const ui = useUiStore()
      this.scan.adding = true
      try {
        await api.post('/api/anime/scanpaths', { path: p })
        this.scan.newPath = ''
        ui.toast('Ruta añadida, buscando carpetas…', 'ok')
        await this.loadScanFolders()
      }
      // El servidor dice POR QUÉ (la ruta no existe, casi siempre); tragárselo dejaba al usuario
      // mirando una lista vacía sin pista alguna.
      catch (e) { ui.toast(e?.message || 'No se pudo añadir la ruta', 'error') }
      finally { this.scan.adding = false }
    },
    async removeScanPath(path) {
      try { await api.del('/api/anime/scanpaths', { body: { path } }); await this.loadScanFolders() } catch (_) {}
    },
    // La fila se pone en verde AL INSTANTE y el resto se hace por detrás. Antes se esperaba a
    // recargar la biblioteca Y volver a escanear las carpetas: segundos mirando una fila que
    // seguía diciendo "Sin coincidencia" cuando ya estaba enlazada. Si el servidor falla, la fila
    // se deshace sola y se avisa — que es la única razón para no ser optimista.
    async matchFolder(folder, sug) {
      if (!sug) return
      const antes = { id: folder.mapped_id, title: folder.matched_title, cover: folder.matched_cover }
      folder.mapped_id = String(sug.id)
      folder.matched_title = sug.title
      folder.matched_cover = sug.cover
      folder._results = []; folder._q = ''; folder._justMatched = true
      setTimeout(() => { folder._justMatched = false }, 1200)
      try {
        await api.post('/api/anime/scan/match', { folder: folder.folder, anilist_id: sug.id, title: sug.title, cover: sug.cover })
        useUiStore().toast(`"${sug.title}" enlazado`, 'ok')
        this.loadLibrary(true)                   // por detrás: la fila ya está bien
      } catch (e) {
        folder.mapped_id = antes.id; folder.matched_title = antes.title; folder.matched_cover = antes.cover
        useUiStore().toast(e?.message || 'No se pudo enlazar', 'error')
      }
    },
    // Buscar la serie a mano cuando la sugerencia falla o no hay ninguna. Estaba en la app vieja
    // (`searchForScanFolder`) y se quedó sin migrar: sin esto, una carpeta con nombre raro
    // ("konejeje", releases con etiquetas) no había forma de enlazarla.
    async searchScanFolder(folder) {
      const q = (folder._q || '').trim(); if (q.length < 2) return
      folder._searching = true; folder._results = []
      try {
        const items = await api.get(`/api/anime/search?q=${encodeURIComponent(q)}`) || []
        // 12, no 6: AniList devuelve una entrada POR TEMPORADA y las ordena por parecido del
        // título, así que buscando "kaguya" la 2ª temporada caía en el puesto 7 y la 3ª en el 9 —
        // fuera de la lista. Viajan también formato/año/episodios: sin eso dos temporadas se ven
        // como dos líneas casi idénticas y no hay forma de saber cuál es cuál.
        folder._results = items.slice(0, 12).map(a => ({
          id: a.al_id || a.id, title: a.title, cover: a.cover,
          format: a.format || '', season: a.season_label || '', episodes: a.episodes || 0,
        }))
        if (!folder._results.length) useUiStore().toast(`Sin resultados para "${q}"`, 'info')
      } catch (e) { useUiStore().toast(e?.message || 'No se pudo buscar', 'error') }
      finally { folder._searching = false }
    },
    async unmatchFolder(folder) {
      const antes = { id: folder.mapped_id, title: folder.matched_title, cover: folder.matched_cover }
      folder.mapped_id = null; folder.matched_title = ''; folder.matched_cover = ''
      try {
        await api.post('/api/anime/scan/unmatch', { folder: folder.folder })
        this.loadLibrary(true)
      } catch (e) {
        folder.mapped_id = antes.id; folder.matched_title = antes.title; folder.matched_cover = antes.cover
        useUiStore().toast(e?.message || 'No se pudo desenlazar', 'error')
      }
    },
    async browse(path = '') {
      try {
        const d = await api.get(`/api/anime/browse?path=${encodeURIComponent(path)}`)
        this.scan.browseOpen = true
        this.scan.browsePath = d.path; this.scan.browseWin = d.win_path; this.scan.browseParent = d.parent; this.scan.browseItems = d.items || []
      } catch (_) { useUiStore().toast('No se pudo explorar', 'error') }
    },

    /* ── History ────────────────────────────────────────────────────────── */
    async loadHistory() {
      try { this.history = await api.get('/api/anime/history') || [] }
      catch (_) { this.history = [] }
      finally { this.historyLoaded = true }
    },
    async clearHistory() {
      try { await api.post('/api/anime/history/clear', {}); this.history = [] }
      catch (_) { useUiStore().toast('No se pudo limpiar el historial', 'error') }
    },
  },
})
