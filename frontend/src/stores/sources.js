import { defineStore } from 'pinia'
import { api } from '@/lib/api'
import { useUiStore } from './ui'
import { useMangaStore } from './manga'

export const useSourcesStore = defineStore('sources', {
  state: () => ({
    online: false,
    starting: false,   // JVM on-demand arrancando (splash "arrancando fuentes…")
    checked: false,
    sources: [],
    webuiUrl: '',      // WebUI propia de Suwayomi (:4567) para gestionar extensiones/fuentes
    openingWebUI: false,

    // Array of selected source IDs (never Set — Pinia can't serialize Set)
    activeSources: [],

    query: '',
    results: [],
    resultGroups: [],          // [{ source, results }] for search display
    searching: false,
    progress: { done: 0, total: 0 },
    _es: null,

    // Popular
    popular: [],
    popularLoading: false,

    detail: null,
    chapters: [],
    detailLoading: false,
    downloading: {},
    reading: {},

    // Track which manga (by sourceId + mangaId) are already in the library
    libraryIds: [],
  }),

  getters: {
    hasSelection: (s) => s.activeSources.length > 0,
    selectedCount: (s) => s.activeSources.length,
    allSourcesSelected: (s) => s.activeSources.length === s.sources.length && s.sources.length > 0,
    inLibrary: (s) => (sourceId, mangaId) => s.libraryIds.includes(`${sourceId}_${mangaId}`),
  },

  actions: {
    async checkHealth(retries = 9) {
      // wake=1: Suwayomi es on-demand — entrar a Fuentes arranca la JVM en el
      // backend y este polling recoge el online cuando esté lista (~10-20s).
      let starting = false
      try {
        const h = await api.get('/api/sources/health?wake=1')
        this.online = !!h?.online
        starting = !!h?.starting
        if (h?.url) this.webuiUrl = h.url.replace('localhost', '127.0.0.1')
      }
      catch (_) { this.online = false }
      finally { this.checked = true }
      this.starting = starting
      if (this.online) { this.starting = false; if (!this.sources.length) this.loadSources(); return }
      if (retries > 0) setTimeout(() => this.checkHealth(retries - 1), 5000)
      else this.starting = false
    },

    // Abre la WebUI de Suwayomi (:4567) en una pestaña nueva para instalar
    // extensiones y elegir fuentes. Como la JVM es on-demand, la despierta antes
    // de navegar. Abre la pestaña YA (dentro del click) para no chocar con el
    // bloqueador de popups; si no arranca, la cierra.
    async openWebUI() {
      const url = this.webuiUrl || 'http://127.0.0.1:4567'
      if (this.online) { window.open(url, '_blank', 'noopener'); return }
      const win = window.open('', '_blank')
      this.openingWebUI = true
      this.starting = true
      try {
        await api.get('/api/sources/health?wake=1')  // dispara el arranque en el backend
        for (let i = 0; i < 16; i++) {
          await new Promise((r) => setTimeout(r, 2000))
          const h = await api.get('/api/sources/health').catch(() => null)
          if (h?.online) {
            this.online = true; this.starting = false; this.openingWebUI = false
            if (!this.sources.length) this.loadSources()
            if (win) win.location = url; else window.open(url, '_blank', 'noopener')
            return
          }
        }
        this.starting = false; this.openingWebUI = false
        if (win) win.close()
        useUiStore().toast('El servidor de fuentes tardó demasiado en arrancar', 'error')
      } catch (_) {
        this.starting = false; this.openingWebUI = false
        if (win) win.close()
      }
    },
    async loadSources() {
      try { this.sources = await api.get('/api/sources/list') || [] } catch (_) {}
    },

    toggleSource(id) {
      const idx = this.activeSources.indexOf(id)
      if (idx >= 0) this.activeSources.splice(idx, 1)
      else this.activeSources.push(id)
      this.popular = []
      this.resultGroups = []
    },

    selectAll() {
      this.activeSources = this.sources.map(s => s.id)
      this.popular = []
      this.resultGroups = []
    },

    clearAll() {
      this.activeSources = []
      this.popular = []
      this.resultGroups = []
    },

    _stopStream() {
      if (this._es) { this._es.close(); this._es = null }
      this.searching = false
      this.popularLoading = false
    },

    /* Search via SSE — results grouped by source. */
    search() {
      const q = this.query.trim()
      if (q.length < 2) return
      this._stopStream()
      this.resultGroups = []
      this.popular = []
      this.searching = true
      this.progress = { done: 0, total: this.sources.length }

      const srcs = this.activeSources.length > 0 ? this.activeSources.join(',') : ''
      let url = `/api/sources/search_all_stream?q=${encodeURIComponent(q)}`
      if (srcs) url += `&sources=${encodeURIComponent(srcs)}`

      try {
        const es = new EventSource(url)
        this._es = es
        es.onmessage = (e) => {
          let d; try { d = JSON.parse(e.data) } catch { return }
          if (d.type === 'start') this.progress.total = d.total
          else if (d.type === 'result') {
            if (d.results?.length) {
              const existing = this.resultGroups.find(g => g.source.id === d.source.id)
              if (existing) existing.results.push(...d.results)
              else this.resultGroups.push({ source: d.source, results: [...d.results] })
            }
          }
          else if (d.type === 'progress') this.progress = { done: d.done, total: d.total }
          else if (d.type === 'done') { this._stopStream(); this.progress.done = this.progress.total }
        }
        es.onerror = () => this._stopStream()
      } catch (_) { this.searching = false }
    },

    /* Popular via SSE. */
    loadPopular() {
      this._stopStream()
      this.popular = []
      this.popularLoading = true
      this.progress = { done: 0, total: this.sources.length }

      const srcs = this.activeSources.length > 0 ? this.activeSources.join(',') : ''
      let url = `/api/sources/search_all_stream?kind=POPULAR`
      if (srcs) url += `&sources=${encodeURIComponent(srcs)}`

      try {
        const es = new EventSource(url)
        this._es = es
        es.onmessage = (e) => {
          let d; try { d = JSON.parse(e.data) } catch { return }
          if (d.type === 'start') this.progress.total = d.total
          else if (d.type === 'result' && d.results) d.results.forEach(m => this.popular.push(m))
          else if (d.type === 'progress') this.progress = { done: d.done, total: d.total }
          else if (d.type === 'done') { this._stopStream(); this.progress.done = this.progress.total }
        }
        es.onerror = () => this._stopStream()
      } catch (_) { this.popularLoading = false }
    },

    async openDetail(m) {
      this.detail = { ...m }
      this.chapters = []
      this.detailLoading = true
      try {
        const [meta, chs] = await Promise.all([
          api.get(`/api/sources/manga/${m.id}`).catch(() => null),
          api.get(`/api/sources/manga/${m.id}/chapters`).catch(() => []),
        ])
        if (meta) {
          this.detail = {
            ...m,
            ...meta,
            sourceId: m.sourceId || meta.source?.id,
            sourceName: m.sourceName || meta.source?.name,
            sourceLang: m.sourceLang || meta.source?.lang,
          }
        }
        // Format chapters
        this.chapters = (chs || []).map(ch => ({
          ...ch,
          chapterNumber: ch.chapterNumber ?? 0,
          name: ch.name || `Cap. ${ch.chapterNumber ?? '?'}`,
        }))
        // Check if already in library
        this._checkLibrary()
        // Save source meta to library if manga already has a folder
        if (this.detail.sourceId && this.detail.id) {
          api.post('/api/sources/save_to_library', {
            title: this.detail.title,
            sourceId: this.detail.sourceId,
            mangaId: this.detail.id,
            thumbnailUrl: this.detail.thumbnailUrl || null,
            sourceName: this.detail.sourceName || null,
            sourceLang: this.detail.sourceLang || null,
            onlyIfExists: true,
          }).catch(() => {})
        }
      } catch (e) { console.error('[sources] openDetail error:', e) }
      finally { this.detailLoading = false }
    },
    async _checkLibrary() {
      try {
        const items = await api.get('/api/library') || []
        const sd = this.detail
        const found = items.find(m => {
          const name = (m.name || m.id || '').toLowerCase()
          const title = (sd?.title || '').toLowerCase()
          const sm = m.source_meta
          return sm && String(sm.sourceId) === String(sd?.sourceId) && String(sm.mangaId) === String(sd?.id)
              || name === title
        })
        const key = `${sd?.sourceId}_${sd?.id}`
        if (found && !this.libraryIds.includes(key)) this.libraryIds.push(key)
      } catch (_) {}
    },

    async addToLibrary() {
      const ui = useUiStore()
      const d = this.detail
      if (!d) return
      try {
        const res = await api.post('/api/sources/save_to_library', {
          title: d.title,
          sourceId: d.sourceId,
          mangaId: d.id,
          thumbnailUrl: d.thumbnailUrl || null,
          sourceName: d.sourceName || null,
          sourceLang: d.sourceLang || null,
        })
        const key = `${d.sourceId}_${d.id}`
        if (!this.libraryIds.includes(key)) this.libraryIds.push(key)
        // Also register a status-bearing tracked entry, so it shows up with a
        // reading status in Biblioteca even without anything downloaded yet.
        api.post('/api/mangadex/local_library/add', {
          manga: { id: `src_${key}`, kind: 'source', title: d.title, cover: d.thumbnailUrl || null, source_id: d.sourceId, manga_id: d.id },
        }).catch(() => {})
        ui.toast('Añadido a biblioteca', 'ok')
      } catch (e) {
        ui.toast('No se pudo añadir', 'error')
      }
    },

    closeDetail() { this.detail = null },

    async readChapter(ch) {
      const ui = useUiStore()
      this.reading[ch.id] = true
      try {
        const pg = await api.get(`/api/sources/chapter/${ch.id}/pages`)
        const pageUrls = pg.pages || []
        if (!pageUrls.length) { ui.toast('Capítulo sin páginas', 'error'); return }
        useMangaStore().openOnlineReader(this.detail.title, ch.chapterNumber || ch.name, pageUrls, this.detail.sourceName, { kind: 'source', chapterRef: ch.id }, this.detail.thumbnailUrl || '')
      } catch (_) { ui.toast('No se pudo abrir el capítulo', 'error') }
      finally { delete this.reading[ch.id] }
    },

    async downloadChapter(ch) {
      const ui = useUiStore()
      this.downloading[ch.id] = true
      try {
        const pg = await api.get(`/api/sources/chapter/${ch.id}/pages`)
        const pageUrls = pg.pages || []
        if (!pageUrls.length) { ui.toast('Capítulo sin páginas', 'error'); return }
        await api.post('/api/download/download_source_chapter', {
          title: this.detail.title,
          chapter: ch.chapterNumber || ch.name,
          pageUrls,
          sourceId: this.detail.sourceId,
          mangaId: this.detail.id,
          sourceName: this.detail.sourceName,
          sourceLang: this.detail.sourceLang,
        })
        ui.toast(`Descargando: ${ch.name}`, 'info')
      } catch (_) { ui.toast('No se pudo descargar', 'error') }
      finally { delete this.downloading[ch.id] }
    },
  },
})
