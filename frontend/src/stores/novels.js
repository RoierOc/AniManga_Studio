/* Store de NOVELAS — "Buscar para leer" + biblioteca.
 *
 * El backend (`/api/novels`, que proxea el sidecar de plugins LNReader) hace todo el trabajo
 * sucio; aquí solo se guarda el estado de la búsqueda de versiones y la novela abierta.
 * Deliberadamente NO hay traducción ni escalado: no aplican a texto. */
import { defineStore } from 'pinia'
import { api } from '@/lib/api'
import { useUiStore } from '@/stores/ui'
import { mergeNovelProgress, readLocalNovelProgress, sameNovelProgress, saveLocalNovelProgress } from '@/lib/novelProgress'

export const useNovelsStore = defineStore('novels', {
  state: () => ({
    finding: false,
    findQuery: '',
    versions: [],      // [{ pluginId, name, path, cover }]
    findSources: [],   // [{ id, status, error }] — para explicar por qué faltan resultados
    tried: [],         // títulos con los que se buscó (original + variantes)
    counts: {},        // 'pluginId:path' -> nº de capítulos (llega después de la búsqueda)
    library: [],       // entradas kind:'novel' de local_library
    libraryError: '',
    detail: null,      // ficha abierta: { novelId, title, pluginId, path, cover } | null
    novel: null,       // novela cargada: { title, pluginId, path, chapters:[…] }
    novelLoading: false,
    novelError: '',
    _adding: {},

    // ── Catálogo (navegar una fuente nativa: SkyNovels) ──
    browseOpen: false,
    browseItems: [],
    browseSort: 'views',   // views | rating | chapters | title
    browseSearch: '',      // filtra el catálogo por título (usa /search)
    browseTotal: 0,
    browsePage: 1,
    browseLoading: false,
    browseMoreLoading: false,

    // ── Lector de texto ──
    reader: null,          // { novelId, title, pluginId, chapterName } | null
    chapterHtml: '',
    chapterWords: 0,
    chapterMinutes: 0,
    chapterLoading: false,
    chapterError: '',
    chapterErrorIndex: null,
    // Ajustes de lectura de TEXTO (nada que ver con los de imagen: aquí manda la tipografía).
    fontSize: parseFloat(localStorage.getItem('novel-fontsize') || '1.15') || 1.15,
    lineHeight: parseFloat(localStorage.getItem('novel-lineheight') || '1.8') || 1.8,
    measure: parseInt(localStorage.getItem('novel-measure') || '38', 10) || 38, // ancho en rem
    serif: localStorage.getItem('novel-serif') !== '0',
    theme: localStorage.getItem('novel-theme') || 'night',   // night | sepia | light
    progress: readLocalNovelProgress(),
    progressLoaded: false,
    progressLoading: false,
    progressError: '',
    progressSyncTimer: null,
    progressSyncInFlight: false,
    progressSyncAgain: false,
  }),

  getters: {
    // Fuentes consultadas que no respondieron: se muestran como aviso, no como error duro
    // (regla del proyecto: "falló ≠ no había").
    failedSources: (s) => s.findSources.filter(x => x.status !== 'ok').map(x => x.id),
    inLibrary: (s) => (v) => s.library.some(n => n.novel?.pluginId === v.pluginId && n.novel?.path === v.path),
    isAdding: (s) => (v) => !!s._adding[`${v?.pluginId}:${v?.path}`],
    // Por dónde iba el usuario en una novela concreta (para "Continuar cap. N").
    progressOf: (s) => (novelId) => s.progress[novelId] || null,
  },

  actions: {
    async loadLibrary() {
      if (!this.progressLoaded && !this.progressLoading) this.hydrateProgress()
      this.libraryError = ''
      try { this.library = await api.get('/api/novels/library') || [] }
      catch (e) { this.libraryError = e?.message || 'No se pudo cargar la biblioteca de novelas' }
    },

    /** Busca el título en las fuentes curadas (EN+ES) y ofrece las versiones encontradas. */
    async findVersions(title) {
      if (!title) return
      this.finding = true; this.findQuery = title; this.versions = []; this.findSources = []
      try {
        const r = await api.post('/api/novels/find', { title })
        this.versions = r.results || []
        this.findSources = r.sources || []
        this.tried = r.tried || []
        this.loadCounts()   // en segundo plano: no retrasa la lista
      } catch (_) {
        useUiStore().toast('No se pudo buscar la novela', 'error')
      } finally { this.finding = false }
    },

    /** Rellena el nº de capítulos de cada versión (abre la ficha de cada una: lento). */
    async loadCounts() {
      const items = this.versions.map(v => ({ pluginId: v.pluginId, path: v.path }))
      if (!items.length) return
      try {
        const rows = await api.post('/api/novels/counts', { items })
        const next = { ...this.counts }
        for (const r of rows || []) next[`${r.pluginId}:${r.path}`] = r.count
        this.counts = next
      } catch (_) { /* sin conteos se sigue pudiendo elegir */ }
    },

    chapterCount(v) { return this.counts[`${v.pluginId}:${v.path}`] },

    clearFind() { this.versions = []; this.findSources = []; this.tried = []; this.counts = {}; this.findQuery = '' },

    async addToLibrary(v, title) {
      const key = `${v.pluginId}:${v.path}`
      if (this.inLibrary(v) || this._adding[key]) return
      const ui = useUiStore()
      this._adding = { ...this._adding, [key]: true }
      try {
        await api.post('/api/novels/library/add', {
          title: title || v.name, pluginId: v.pluginId, path: v.path, cover: v.cover || null,
          lang: v.lang || '', site: v.sourceName || '',
        })
        await this.loadLibrary()
        ui.toast('Novela añadida a tu biblioteca', 'ok')
      } catch (_) {
        ui.toast('No se pudo añadir la novela', 'error')
      } finally {
        const a = { ...this._adding }; delete a[key]; this._adding = a
      }
    },

    // ── Catálogo SkyNovels ──────────────────────────────────────────────────
    // Navegar la fuente ES fiable (API oficial, 476 novelas) con orden por vistas/rating/etc.
    // Búsqueda y navegación comparten la misma rejilla; buscar usa /search (todo el catálogo),
    // navegar usa /browse (paginado + ordenado).
    openBrowse() { this.browseOpen = true; if (!this.browseItems.length) this.loadBrowse() },
    closeBrowse() { this.browseOpen = false },

    async loadBrowse() {
      this.browseLoading = true; this.browsePage = 1; this.browseItems = []
      try {
        if (this.browseSearch.trim()) {
          const r = await api.get(`/api/novels/search?pluginId=skynovels&q=${encodeURIComponent(this.browseSearch.trim())}`)
          this.browseItems = (Array.isArray(r) ? r : []).map(x => ({ ...x, pluginId: 'skynovels' }))
          this.browseTotal = this.browseItems.length
        } else {
          const d = await api.get(`/api/novels/browse?pluginId=skynovels&sort=${this.browseSort}&page=1`)
          this.browseItems = (d.results || []).map(x => ({ ...x, pluginId: 'skynovels' }))
          this.browseTotal = d.total || this.browseItems.length
        }
      } catch (_) { useUiStore().toast('No se pudo cargar el catálogo', 'error'); this.browseItems = [] }
      finally { this.browseLoading = false }
    },

    async loadMoreBrowse() {
      // La búsqueda ya trae todo lo que casa (sin páginas); solo navegar pagina.
      if (this.browseMoreLoading || this.browseSearch.trim()) return
      if (this.browseItems.length >= this.browseTotal) return
      this.browseMoreLoading = true
      const next = this.browsePage + 1
      try {
        const d = await api.get(`/api/novels/browse?pluginId=skynovels&sort=${this.browseSort}&page=${next}`)
        const seen = new Set(this.browseItems.map(x => x.path))
        this.browseItems = [...this.browseItems, ...(d.results || []).filter(x => !seen.has(x.path)).map(x => ({ ...x, pluginId: 'skynovels' }))]
        this.browsePage = d.page || next
      } catch (_) { /* un fallo de página no borra lo cargado */ }
      finally { this.browseMoreLoading = false }
    },

    setBrowseSort(s) { if (s === this.browseSort) return; this.browseSort = s; this.loadBrowse() },
    runBrowseSearch() { this.loadBrowse() },

    /** Ficha + lista de capítulos de una novela (para el lector, F4). */
    async openNovel(pluginId, path, title = '') {
      this.novelLoading = true; this.novelError = ''; this.novel = null
      try {
        const n = await api.post('/api/novels/novel', { pluginId, path })
        this.novel = { ...n, title: n.name || title, pluginId, path }
      } catch (e) {
        this.novelError = e?.message || 'No se pudo abrir la novela'
        useUiStore().toast('No se pudo abrir la novela', 'error')
      } finally { this.novelLoading = false }
    },

    /** Texto de un capítulo: { html, text, words, minutes }. */
    async chapter(pluginId, path) {
      return await api.post('/api/novels/chapter', { pluginId, path })
    },

    // ── Ficha de la novela ────────────────────────────────────────────────────
    /** Abre la ficha (sinopsis + capítulos + continuar), NO el lector. */
    async openDetail(entry) {
      const { pluginId, path } = entry.novel || entry
      const src = entry.novel || entry
      this.detail = {
        novelId: entry.id || `novel:${pluginId}:${path}`,
        title: entry.title, pluginId, path, cover: entry.cover || src.cover || null,
        lang: src.lang || '', sourceName: src.sourceName || src.site || '',  // la biblioteca lo guarda como 'site'
      }
      await this.openNovel(pluginId, path, entry.title)
    },

    closeDetail() { this.detail = null; if (!this.reader) this.novel = null },

    // ── Lector ────────────────────────────────────────────────────────────────
    /** Abre la novela en el lector, retomando por donde iba si hay progreso. */
    async openReader(entry, chapterIndex = null) {
      const { pluginId, path } = entry.novel || entry
      const novelId = entry.id || `novel:${pluginId}:${path}`
      if (!this.progressLoaded) await this.hydrateProgress()
      // Si la ficha ya cargó esta novela, no se vuelve a pedir: entrar a leer es instantáneo.
      const already = this.novel && this.novel.pluginId === pluginId && this.novel.path === path
      if (!already) await this.openNovel(pluginId, path, entry.title)
      if (!this.novel) return
      const saved = this.progress[novelId]
      const index = chapterIndex != null ? chapterIndex : (saved?.chapterIndex || 0)
      const scroll = chapterIndex != null ? 0 : (saved?.scroll || 0)
      this.reader = { novelId, title: this.novel.title, pluginId, chapterIndex: -1, chapterName: '' }
      await this.goChapter(index, scroll)
    },

    closeReader() { this.reader = null; this.chapterHtml = ''; if (!this.detail) this.novel = null },

    async goChapter(index, scroll = 0) {
      const chapters = this.novel?.chapters || []
      const ch = chapters[index]
      if (!ch || !this.reader) return
      this.chapterLoading = true; this.chapterError = ''; this.chapterErrorIndex = index; this.chapterHtml = ''
      try {
        const r = await this.chapter(this.reader.pluginId, ch.path)
        this.chapterHtml = r.html || ''
        this.chapterWords = r.words || 0
        this.chapterMinutes = r.minutes || 0
        this.reader = { ...this.reader, chapterIndex: index, chapterName: ch.name || `Capítulo ${index + 1}` }
        this.chapterErrorIndex = null
        this.saveProgress(scroll)
      } catch (e) {
        this.chapterError = e?.message || 'No se pudo cargar el capítulo'
        useUiStore().toast('No se pudo cargar el capítulo', 'error')
      } finally { this.chapterLoading = false }
      return scroll
    },

    /** Progreso = capítulo + posición dentro de él (el texto no tiene "páginas"). */
    saveProgress(scroll = 0) {
      if (!this.reader) return
      this.progress = {
        ...this.progress,
        [this.reader.novelId]: {
          title: this.reader.title, pluginId: this.reader.pluginId,
          path: this.novel?.path, chapterIndex: this.reader.chapterIndex,
          chapterName: this.reader.chapterName, scroll,
          total: this.novel?.chapters?.length || 0, at: Date.now(),
        },
      }
      saveLocalNovelProgress(this.progress)
      this._scheduleProgressSync()
    },

    async hydrateProgress() {
      if (this.progressLoading) return
      if (this.progressLoaded) return this.progress
      this.progressLoading = true
      this.progressError = ''
      try {
        const remote = await api.get('/api/novels/progress')
        const merged = mergeNovelProgress(this.progress, remote)
        this.progress = merged
        saveLocalNovelProgress(merged)
        this.progressLoaded = true
        if (!sameNovelProgress(merged, remote)) {
          try {
            const saved = await api.post('/api/novels/progress', merged)
            this.progress = mergeNovelProgress(this.progress, saved)
            saveLocalNovelProgress(this.progress)
          } catch (e) {
            this.progressError = e?.message || 'No se pudo guardar el progreso de novelas'
          }
        }
        return this.progress
      } catch (e) {
        this.progressError = e?.message || 'No se pudo cargar el progreso de novelas'
        return this.progress
      } finally {
        this.progressLoading = false
      }
    },

    retryProgress() {
      this.progressLoaded = false
      return this.hydrateProgress()
    },

    _scheduleProgressSync() {
      clearTimeout(this.progressSyncTimer)
      this.progressSyncTimer = setTimeout(() => {
        this.progressSyncTimer = null
        this._pushProgress()
      }, 900)
    },

    async _pushProgress() {
      if (this.progressSyncInFlight) {
        this.progressSyncAgain = true
        return
      }
      this.progressSyncInFlight = true
      try {
        const saved = await api.post('/api/novels/progress', this.progress)
        this.progress = mergeNovelProgress(this.progress, saved)
        saveLocalNovelProgress(this.progress)
        this.progressError = ''
      } catch (e) {
        this.progressError = e?.message || 'No se pudo guardar el progreso de novelas'
      } finally {
        this.progressSyncInFlight = false
        if (this.progressSyncAgain) {
          this.progressSyncAgain = false
          this._scheduleProgressSync()
        }
      }
    },

    setSetting(key, value) {
      this[key] = value
      const store = { fontSize: 'novel-fontsize', lineHeight: 'novel-lineheight', measure: 'novel-measure',
                      serif: 'novel-serif', theme: 'novel-theme' }[key]
      if (store) localStorage.setItem(store, typeof value === 'boolean' ? (value ? '1' : '0') : String(value))
    },
  },
})
