import { defineStore } from 'pinia'
import { api } from '@/lib/api'
import { onSSE } from '@/lib/sse'
import { useUiStore } from './ui'
import { nextUnwatchedEp } from '@/lib/anime'

let autoplayTimer = null
let nowTimer = null
let sseBound = false

export const useAnimeStore = defineStore('anime', {
  state: () => ({
    library: [],
    loading: false,
    sub: 'library',            // library | search | seasonal | downloads | history
    detailId: null,            // open anime detail id
    libSort: 'last_added',
    libFilter: 'all',
    libSearch: '',

    skipTimes: {},             // `${animeId}_${ep}` -> {op_start, op_end, ed_start, ed_end}
    epInfo: {},                // `${malId}_${ep}` -> {title, synopsis, ...} | null(loading)
    epInfoOpen: null,          // currently expanded ep-info key
    nextAiring: {},            // al_id -> {episode, airing_at}

    autoplay: null,            // { anime, ep } | null
    autoplaySeconds: 0,
    nowSec: Math.floor(Date.now() / 1000),

    // qBittorrent
    qbt: { connected: false, version: '', url: 'http://localhost:8080', username: '', password: '' },
    qbtTorrents: [],
    qbtLoading: false,

    // history
    history: [],
    historyLoaded: false,
  }),

  getters: {
    detail: (s) => s.library.find(a => a.id === s.detailId) || null,
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

    openDetail(anime) {
      this.detailId = anime.id
      this.epInfoOpen = null
      if (anime.al_id) this.loadTags(anime)
    },
    closeDetail() { this.detailId = null },

    async loadTags(anime) {
      if (!anime.al_id || this.nextAiring[anime.al_id]) return
      try {
        const d = await api.get(`/api/anime/tags/${anime.al_id}`)
        if (d?.next_airing) this.nextAiring[anime.al_id] = d.next_airing
      } catch (_) {}
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
      ep.watched = !was                         // optimistic
      try {
        await api.post(`/api/anime/library/${anime.id}/watched`, { episode: ep.num })
      } catch (_) {
        ep.watched = was
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
      if (ep) ep.watched = !!ev.watched
      anime.last_watched_at = ev.last_watched_at
      // MPV finished an episode → offer the next one.
      if (ev.from_mpv && ev.watched) {
        const next = nextUnwatchedEp(anime)
        if (next) this.showAutoplay(anime, next)
      }
    },
    _onPosition(ev) {
      const anime = this.library.find(a => a.id === ev.anime_id)
      if (!anime) return
      const ep = (anime.episodes || []).find(e => String(e.num) === String(ev.ep_str))
      if (ep) ep.resume_pos = ev.position
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
