import { defineStore } from 'pinia'
import { api } from '@/lib/api'
import { onSSE } from '@/lib/sse'
import { useUiStore } from './ui'
import { nextUnwatchedEp, isSpanishOrMulti, isEnglishSub } from '@/lib/anime'

let autoplayTimer = null
let nowTimer = null
let sseBound = false
const subPollers = {}   // subKey -> interval handle

export const useAnimeStore = defineStore('anime', {
  state: () => ({
    library: [],
    loading: false,
    // Guard against a stale localStorage value landing on the placeholder fallback.
    sub: ['library', 'search', 'seasonal', 'downloads', 'history'].includes(localStorage.getItem('anime-sub'))
      ? localStorage.getItem('anime-sub') : 'library',   // library | search | seasonal | downloads | history
    detailId: null,            // open anime detail id
    libSort: localStorage.getItem('anime-libsort') || 'last_watched',  // recently-watched first by default
    libFilter: 'all',
    libSearch: '',

    skipTimes: {},             // `${animeId}_${ep}` -> {op_start, op_end, ed_start, ed_end}
    epInfo: {},                // `${malId}_${ep}` -> {title, synopsis, ...} | null(loading)
    epInfoOpen: null,          // currently expanded ep-info key
    nextAiring: {},            // al_id -> {episode, airing_at}
    airing: {},                // al_id -> {status, next_episode, next_airing_at, last_episode, last_aired_at} — fresh airing schedule

    // discovery in detail (keyed by al_id)
    recs: {}, recsState: {},
    stacks: {}, stacksState: {},
    tags: {}, malUrls: {},
    // browse overlays
    stackBrowse: null, stackBrowseMeta: null, stackBrowseAnime: [], stackBrowseState: 'idle',
    tagBrowse: null, tagBrowseAnime: [], tagBrowseState: 'idle',
    // hover preview
    preview: null,             // anime object
    previewPos: { x: 0, y: 0 },

    // local scan paths
    scan: { show: false, paths: [], folders: [], loading: false, newPath: '',
            browseOpen: false, browsePath: '', browseWin: '', browseParent: null, browseItems: [] },

    // anime download location (qBittorrent save path)
    dlSettings: { download_path: '', qbt_default: '' },
    dlBrowse: { open: false, path: '', win: '', parent: null, items: [], loading: false },

    // management
    linkTorrent: { show: false, list: [], loading: false, subpath: '' },
    epOverrideMenu: null,      // { anime, ep, x, y }
    rename: null,              // { id, items } | null
    renameBusy: false,

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
    qbtLoading: false,

    // history
    history: [],
    historyLoaded: false,

    // seasonal
    seasonal: [],
    seasonalLoading: false,
    season: '',
    year: 0,
    seasonSort: 'score',         // score | popularity | trending
    seasonGenre: '',

    // search + torrents
    searchQuery: '',
    searchResults: [],
    searchLoading: false,
    torrentAnime: null,          // anime whose torrents are open (null = show results)
    torrents: [],
    torrentsLoading: false,
    torrentQuery: '',
    torrentCategory: '1_2',
    flt: { lang: 'all', hideDead: true, quality: '', group: '', ep: 'all' },
    targetEp: null,             // target episode when navigating from detail
    addingHashes: [],            // keys currently being added to qbt
    addedHashes: [],             // keys just added (transient ✓)
  }),

  getters: {
    detail: (s) => s.library.find(a => a.id === s.detailId) || null,

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
          const inProg = eps.find(e => e.num > 0 && e.ep_type !== 'special' && (e.resume_pos > 0) && !e.watched && (e.in_local || (e.in_qbt && e.progress >= 100)))
          const ep = inProg || nextUnwatchedEp(a)
          return ep ? { anime: a, ep } : null
        })
        .filter(Boolean)
        .sort((x, y) => (y.anime.last_watched_at || 0) - (x.anime.last_watched_at || 0))
        .slice(0, 12)
    },

    // Hero banner content, in priority order:
    //   1) library anime currently airing whose newest episode JUST aired (carousel,
    //      most recently aired first) — "Capítulo nuevo N de anime X"
    //   2) library anime with a freshly downloaded unwatched episode
    //   3) "continue watching" (most recently watched)
    //   4) seasonal popular when the library is empty
    heroItems() {
      const now = Date.now() / 1000
      const RECENT = 12 * 86400   // an episode aired within ~12 days counts as "new"

      // 1 — newly aired episodes of library (seasonal/airing) anime
      const aired = []
      for (const a of this.library) {
        const inf = this.airing[a.al_id]
        if (!inf || !inf.last_episode || !inf.last_aired_at) continue
        if (now - inf.last_aired_at > RECENT) continue
        // play it directly if that episode is already downloaded & unwatched
        const dl = (a.episodes || []).find(e => e.num === inf.last_episode
          && (e.in_local || (e.in_qbt && e.progress >= 100)) && !e.watched)
        aired.push({
          anime: a, ep: dl || { num: inf.last_episode }, kind: 'new',
          ts: inf.last_aired_at, aired_at: inf.last_aired_at, hasFile: !!dl,
        })
      }
      if (aired.length) return aired.sort((x, y) => y.ts - x.ts).slice(0, 8)

      // 2 — fresh, unwatched, downloaded episodes (previous behaviour)
      const fresh = []
      for (const a of this.library) {
        const eps = (a.episodes || []).filter(e =>
          e.num > 0 && e.ep_type !== 'special' &&
          (e.in_local || (e.in_qbt && e.progress >= 100)) && !e.watched)
        if (!eps.length) continue
        const ep = eps.reduce((b, e) => (e.added_on || 0) > (b.added_on || 0) ? e : b)
        fresh.push({ anime: a, ep, kind: 'downloaded', ts: ep.added_on || 0, hasFile: true })
      }
      if (fresh.length) return fresh.sort((x, y) => y.ts - x.ts).slice(0, 8)

      // 3 — continue watching
      const cw = this.continueWatching
      if (cw.length) return cw.slice(0, 8).map(c => ({ anime: c.anime, ep: c.ep, kind: 'continue', ts: c.anime.last_watched_at || 0, hasFile: true }))

      // 4 — seasonal popular (not already in library)
      if (this.seasonal.length) {
        return this.seasonal
          .filter(a => !this.library.find(lib => lib.al_id === a.al_id || lib.mal_id === a.mal_id))
          .sort((x, y) => (y.popularity || 0) - (x.popularity || 0))
          .slice(0, 8)
          .map(a => ({
            anime: { ...a, id: a.al_id || a.id, episodes: [], last_watched_at: 0, status: a.status || 'RELEASING' },
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
      for (const k in map) {
        map[k].sort((a, b) => {
          if (a.isSpanish !== b.isSpanish) return a.isSpanish ? -1 : 1
          if (a.isEnglish !== b.isEnglish) return a.isEnglish ? -1 : 1
          return b.seeders - a.seeders
        })
      }
      return Object.entries(map)
        .map(([k, torrents]) => ({ episode: Number(k), torrents }))
        .sort((a, b) => {
          if (a.episode === 0) return -1
          if (b.episode === 0) return 1
          if (a.episode === -1) return 1
          if (b.episode === -1) return -1
          return a.episode - b.episode
        })
    },
  },

  actions: {
    init() {
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
    async openDlBrowse(path = '') {
      this.dlBrowse.open = true; this.dlBrowse.loading = true
      try {
        const d = await api.get(`/api/anime/browse?path=${encodeURIComponent(path)}`)
        this.dlBrowse = { open: true, path: d.path, win: d.win_path, parent: d.parent, items: d.items || [], loading: false }
      } catch (_) { this.dlBrowse.loading = false; useUiStore().toast('No se pudo explorar', 'error') }
    },
    closeDlBrowse() { this.dlBrowse.open = false },

    async loadLibrary(silent = false) {
      if (!silent) this.loading = true
      try {
        const data = await api.get('/api/anime/library')
        this.library = Array.isArray(data) ? data : []
      } catch (_) {
        if (!silent) useUiStore().toast('No se pudo cargar tu anime', 'error')
      } finally {
        this.loading = false
      }
    },

    persist() { localStorage.setItem('anime-sub', this.sub) },

    showPreview(anime, ev) {
      clearTimeout(this._previewTimer)
      this._previewTimer = setTimeout(() => {
        const W = 256, H = 232, pad = 12
        let x = ev.clientX + 20, y = ev.clientY - H / 2
        if (x + W > window.innerWidth - pad) x = ev.clientX - W - 20
        y = Math.max(pad, Math.min(y, window.innerHeight - H - pad))
        this.preview = anime
        this.previewPos = { x, y }
      }, 600)
    },
    hidePreview() { clearTimeout(this._previewTimer); this.preview = null },

    openDetail(anime) {
      this.hidePreview()
      this.detailId = anime.id
      this.epInfoOpen = null
      this.linkTorrent = { show: false, list: [], loading: false, subpath: '' }
      if (anime.al_id) { this.loadTags(anime); this.loadRecs(anime) }
      // Browser back closes the detail and returns to the current sub-tab.
      // NOTE: the view must be the top-level 'anime' id, never the sub-tab —
      // passing the sub here corrupts history.state.view and strands the back
      // button on a non-existent view (the old "EN MIGRACIÓN" placeholder bug).
      const ui = useUiStore()
      ui.pushNav('anime', this.sub, anime.id)
      // Guard: only act if the detail is still open, so a later back press after
      // a tab switch doesn't fire a stale restore.
      ui._setNavRestore(() => { if (this.detailId) this.detailId = null })
    },
    // Pure reset for programmatic callers (tab switches, openRec). The "Volver"
    // button instead uses history.back() to consume the pushed history entry.
    closeDetail() {
      this.detailId = null
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

    async browseByTag(tagName, alId = null) {
      this.tagBrowse = tagName; this.tagBrowseAnime = []; this.tagBrowseState = 'loading'
      try {
        const qs = new URLSearchParams({ tag: tagName }); if (alId) qs.set('al_id', alId)
        this.tagBrowseAnime = await api.get(`/api/anime/browse_tag?${qs}`) || []
        this.tagBrowseState = 'done'
      } catch (_) { this.tagBrowseState = 'error' }
    },
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
      if (!confirm(`¿Borrar los episodios de "${anime.title}" para liberar espacio?\n\nSe eliminan los archivos (su torrent en qBittorrent, o los vídeos de la carpeta vinculada), pero la serie —portada, estado, progreso visto y miniaturas— se conserva. Podrás volver a descargarla desde Torrents cuando quieras.`)) return
      try {
        // delete the files (free space) AND remove the torrent so qBittorrent no
        // longer flags it as "missing files" and re-downloads it. The library entry
        // itself is kept by the backend.
        await api.post(`/api/anime/library/${anime.id}/clear_episodes`, { remove_from_qbt: true, delete_files: true })
        useUiStore().toast(`Episodios de "${anime.title}" liberados`, 'ok')
        await this.loadLibrary(true)
      } catch (_) { useUiStore().toast('No se pudo borrar', 'error') }
    },
    async removeFromLibrary(animeId) {
      if (!confirm('¿Eliminar esta serie de la biblioteca?')) return
      try { await api.del(`/api/anime/library/${animeId}`, { body: { delete_files: false } }); this.detailId = null; await this.loadLibrary(true) }
      catch (_) { useUiStore().toast('No se pudo eliminar', 'error') }
    },
    openEpOverrideMenu(ev, anime, ep) { this.epOverrideMenu = { anime, ep, x: ev.clientX, y: ev.clientY } },
    async setEpOverride(type) {
      const m = this.epOverrideMenu; this.epOverrideMenu = null; if (!m) return
      try { await api.post(`/api/anime/library/${m.anime.id}/ep_override`, { filename: m.ep.filename, type }); await this.loadLibrary(true) }
      catch (_) { useUiStore().toast('No se pudo cambiar el tipo', 'error') }
    },
    async openRename(anime) {
      try { const d = await api.get(`/api/anime/rename_preview/${anime.id}`); this.rename = { id: anime.id, items: d.renames || [] } }
      catch (_) { useUiStore().toast('No se pudo previsualizar', 'error') }
    },
    async applyRename() {
      if (!this.rename) return
      this.renameBusy = true
      try { await api.post(`/api/anime/rename_apply/${this.rename.id}`, { renames: this.rename.items }); this.rename = null; await this.loadLibrary(true); useUiStore().toast('Episodios renombrados', 'ok') }
      catch (_) { useUiStore().toast('Error al renombrar', 'error') }
      finally { this.renameBusy = false }
    },

    async play(anime, ep, subFile = '', startPos = 0) {
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
      const malId = anime?.mal_id
      if (!malId) return
      const key = `${malId}_${ep.num}`
      this.epInfoOpen = this.epInfoOpen === key ? null : key
      if (this.epInfo[key] !== undefined) return
      this.epInfo[key] = null
      try { this.epInfo[key] = await api.get(`/api/anime/episode_info/${malId}/${ep.num}`) || {} }
      catch (_) { this.epInfo[key] = {} }
    },

    /* ── Auto-play ──────────────────────────────────────────────────────── */
    showAutoplay(anime, ep) {
      this.dismissAutoplay()
      this.autoplay = { anime, ep }
      this.autoplaySeconds = 10
      autoplayTimer = setInterval(() => {
        this.autoplaySeconds--
        if (this.autoplaySeconds <= 0) {
          const a = this.autoplay
          this.dismissAutoplay()
          if (a) this.play(a.anime, a.ep)
        }
      }, 1000)
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
      // MPV finished → autoplay next
      if (ev.from_mpv && ev.watched) {
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
    },

    /* ── qBittorrent ────────────────────────────────────────────────────── */
    hasActiveQbt() {
      return this.library.some(a => (a.episodes || []).some(e => e.in_qbt && e.progress < 100))
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
      try { this.qbtTorrents = await api.get('/api/anime/qbt/list') || [] }
      catch (_) { this.qbtTorrents = [] }
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
      } catch (e) { useUiStore().toast('Error: ' + (e.message || 'qBittorrent'), 'error') }
    },

    /* ── Seasonal ───────────────────────────────────────────────────────── */
    async loadSeasonal() {
      this.seasonalLoading = true
      try {
        const p = new URLSearchParams({ sort: this.seasonSort })
        if (this.season) p.set('season', this.season)
        if (this.year) p.set('year', String(this.year))
        const d = await api.get(`/api/anime/seasonal?${p}`)
        this.seasonal = d.results || []
        if (!this.season) this.season = d.season || ''
        if (!this.year) this.year = d.year || 0
      } catch (_) { this.seasonal = [] }
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
          total_episodes: anime.episodes || null,
          format: anime.format || '', track_only: true,
        })
        if (d.ok) { useUiStore().toast(`"${anime.title}" añadido a Mi Anime`, 'ok'); await this.loadLibrary(true) }
      } catch (_) { useUiStore().toast('Error al añadir a biblioteca', 'error') }
    },

    /* ── Search + torrents ──────────────────────────────────────────────── */
    async searchAnime() {
      const q = this.searchQuery.trim()
      if (q.length < 2) return
      this.searchLoading = true
      this.searchResults = []
      try {
        const d = await api.get(`/api/anime/search?q=${encodeURIComponent(q)}`)
        if (Array.isArray(d)) this.searchResults = d
      } catch (_) { useUiStore().toast('Error buscando anime', 'error') }
      finally { this.searchLoading = false }
    },
    closeTorrents() { this.torrentAnime = null },

    async _nyaaMultiFetch(queries) {
      const cat = this.torrentCategory
      const lists = await Promise.all(queries.map(q =>
        api.get(`/api/anime/torrents?${new URLSearchParams({ q, category: cat })}`).catch(() => [])))
      const seen = new Set(); const merged = []
      for (const list of lists) for (const t of (list || [])) {
        const key = t.info_hash || t.title
        if (!seen.has(key)) { seen.add(key); merged.push(t) }
      }
      return merged
    },
    async openTorrents(anime, targetEpisode = null) {
      this.hidePreview()
      // Remember where we came from so the back button returns there, then drill
      // into the torrent panel. Clear the detail directly (no extra history entry).
      const prevSub = this.sub
      this.detailId = null
      this.sub = 'search'
      const ui = useUiStore()
      ui.pushNav('anime', prevSub, null)
      // Guard: only act if the torrent panel is still open (idempotent — a stale
      // back press after a tab switch becomes a harmless no-op).
      ui._setNavRestore(() => { if (this.torrentAnime) { this.torrentAnime = null; this.sub = prevSub } })
      this.torrentAnime = anime
      this.torrents = []
      this.flt = { lang: 'all', hideDead: true, quality: '', group: '', ep: targetEpisode ? 'episodes' : 'all' }
      const fmt = anime.format || ''
      this.targetEp = (fmt === 'MOVIE' || fmt === 'MUSIC') ? null : (targetEpisode && targetEpisode > 0 ? targetEpisode : null)
      const variants = [...new Set([anime.title_romaji, anime.title_english, anime.title].map(t => (t || '').trim()).filter(Boolean))]
      this.torrentQuery = variants[0] || ''
      this.torrentsLoading = true
      try { this.torrents = await this._nyaaMultiFetch(variants) }
      catch (_) { useUiStore().toast('Error buscando en Nyaa', 'error') }
      finally { this.torrentsLoading = false }
    },
    async searchTorrents() {
      const q = this.torrentQuery.trim()
      if (!q) return
      this.torrentsLoading = true; this.torrents = []
      try { this.torrents = await api.get(`/api/anime/torrents?${new URLSearchParams({ q, category: this.torrentCategory })}`) || [] }
      catch (_) { useUiStore().toast('Error buscando en Nyaa', 'error') }
      finally { this.torrentsLoading = false }
    },
    async searchTosho() {
      const q = this.torrentQuery.trim()
      if (!q) return
      this.torrentsLoading = true; this.torrents = []
      try { this.torrents = await api.get(`/api/anime/torrents_tosho?q=${encodeURIComponent(q)}`) || [] }
      catch (_) { useUiStore().toast('Error buscando en Animetosho', 'error') }
      finally { this.torrentsLoading = false }
    },

    isAdding(key) { return this.addingHashes.includes(key) },
    isAdded(key) {
      if (this.addedHashes.includes(key)) return true
      if (!key || key.startsWith('http') || !this.torrentAnime) return false
      const cur = this.torrentAnime
      const lib = this.library.find(a => (cur.al_id && a.al_id === cur.al_id) || (cur.mal_id && a.mal_id === cur.mal_id))
      return !!(lib?.episodes || []).find(e => e.info_hash === key)
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
              cover: a.cover || '', total_episodes: a.episodes || null, format: a.format || '',
              episode: torrent.episode === 0 ? 0 : torrent.episode,
              torrent_title: torrent.title, info_hash: torrent.info_hash || '',
            }).then(() => this.loadLibrary(true)).catch(() => {})
          }
        } else ui.toast('qBittorrent: ' + (d.msg || d.error || 'Error'), 'error')
      } catch (_) { ui.toast('Error conectando qBittorrent', 'error') }
      finally { this.addingHashes = this.addingHashes.filter(k => k !== key) }
    },

    /* ── Subtitles ──────────────────────────────────────────────────────── */
    subKey(anime, ep) { return `${anime.id}_${ep.ep_type === 'special' ? 'sp' : 'ep'}${ep.num}` },

    async translateSubs(anime, ep) {
      const ui = useUiStore()
      const key = this.subKey(anime, ep)
      this.subFetching = key
      const p = new URLSearchParams({
        info_hash: ep.info_hash || '', episode: ep.num, anime_id: anime.id,
        ep_type: ep.ep_type || 'episode', ...(ep.local_path ? { local_path: ep.local_path } : {}),
      })
      try {
        const d = await api.get(`/api/subtitle/tracks?${p}`)
        const tracks = d.tracks || [], ext = d.external_tracks || [], spa = d.spanish_tracks || []
        if (spa.length || tracks.length || ext.length) {
          this.subTrackModal = { anime, ep, tracks, externalTracks: ext, spanishTracks: spa, missingKeys: d.sources_missing_key || [] }
          return
        }
        const hint = (d.sources_missing_key || []).length ? ` (sin API key: ${d.sources_missing_key.join(', ')})` : ''
        ui.toast(`No se encontraron subtítulos${hint}`, 'warn', 7000)
      } catch (_) { ui.toast('Error obteniendo pistas de subtítulos', 'error') }
      finally { this.subFetching = null }
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

    async startTranslate(anime, ep, subIndex = 0, externalSub = null) {
      const ui = useUiStore()
      const key = this.subKey(anime, ep)
      this.subTrackModal = null
      this.subTasks[key] = { status: 'starting', progress: 0, message: 'Iniciando…' }
      try {
        const d = await api.post('/api/subtitle/translate', {
          info_hash: ep.info_hash || '', episode: ep.num, ep_type: ep.ep_type || 'episode',
          anime_id: anime.id, sub_index: subIndex, force: true,
          ...(ep.local_path ? { local_path: ep.local_path } : {}),
          ...(externalSub ? { external_sub: externalSub } : {}),
        })
        if (d.error) { this.subTasks[key] = { status: 'error', progress: 0, message: d.error }; ui.toast(d.error, 'error'); return }
        this.subTasks[key] = { ...this.subTasks[key], task_id: d.task_id }
        this._subPoll(key, d.task_id)
      } catch (e) { this.subTasks[key] = { status: 'error', progress: 0, message: e.body || 'Error' }; ui.toast(e.body || 'Error al traducir', 'error') }
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
        this.scan.folders = await api.get('/api/anime/scan/folders') || []
        // lazily fetch a suggestion for unmatched folders (sequential to avoid rate-limit)
        for (const f of this.scan.folders) {
          if (!f.mapped_id && f.suggestion === null) {
            f.suggestion = await api.get(`/api/anime/scan/suggest?name=${encodeURIComponent(f.name)}`).catch(() => null)
          }
        }
      } catch (_) {}
      finally { this.scan.loading = false }
    },
    async addScanPath(path) {
      const p = (path || this.scan.newPath || '').trim(); if (!p) return
      try { await api.post('/api/anime/scanpaths', { path: p }); this.scan.newPath = ''; await this.loadScanFolders() }
      catch (_) { useUiStore().toast('No se pudo añadir la ruta', 'error') }
    },
    async removeScanPath(path) {
      try { await api.del('/api/anime/scanpaths', { body: { path } }); await this.loadScanFolders() } catch (_) {}
    },
    async matchFolder(folder, sug) {
      if (!sug) return
      try {
        await api.post('/api/anime/scan/match', { folder: folder.folder, anilist_id: sug.id, title: sug.title, cover: sug.cover })
        useUiStore().toast(`"${sug.title}" enlazado`, 'ok')
        await this.loadLibrary(true); await this.loadScanFolders()
      } catch (_) { useUiStore().toast('No se pudo enlazar', 'error') }
    },
    async unmatchFolder(folder) {
      try { await api.post('/api/anime/scan/unmatch', { folder: folder.folder }); await this.loadScanFolders(); await this.loadLibrary(true) } catch (_) {}
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
