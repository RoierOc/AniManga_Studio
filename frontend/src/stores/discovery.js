/* Manga Hub · store de la vista "Descubrir" (Fase 3+) — explorar estilo AniList.
 *
 * Una sola rejilla de OBRAS gobernada por filtros (texto + tipo + géneros + orden), servida por
 * /api/discovery/browse (MangaBaka, deduplicado y con «mejor versión» a nivel obra). La ficha
 * completa se pide bajo demanda. Ver [[project_manga_hub_discovery]]. */
import { defineStore } from 'pinia'
import { api } from '@/lib/api'
import { useUiStore } from '@/stores/ui'

export const TYPE_FILTERS = [
  { id: '', label: 'Todo' },
  { id: 'manhwa', label: 'Manhwa' },
  { id: 'manga', label: 'Manga' },
  { id: 'manhua', label: 'Manhua' },
  { id: 'novel', label: 'Novelas' },
]

export const SORT_OPTIONS = [
  { id: 'popularity', label: 'Popularidad' },
  { id: 'rating', label: 'Mejor valorados' },
  { id: 'year', label: 'Más recientes' },
]

const PAGE_SIZE = 24

export const useDiscoveryStore = defineStore('discovery', {
  state: () => ({
    // Filtros activos de la rejilla de exploración
    query: '',
    type: '',
    genres: [],                // [value] seleccionados
    sort: 'popularity',
    // Resultados
    items: [],
    page: 1,
    hasMore: false,
    loading: false,            // primera página (reemplaza)
    loadingMore: false,        // páginas siguientes (añade)
    error: '',
    _reqId: 0,
    // Catálogo de géneros para el filtro
    genreCatalog: [],          // [{label, value}]
    // Ficha abierta (metadatos completos de UNA obra)
    work: null,
    workLoading: false,
    // Biblioteca
    libraryTitles: [],
    _adding: {},
  }),
  getters: {
    hasFilters: (s) => !!(s.query.trim() || s.type || s.genres.length),
    inLibrary: (s) => (work) => !!work && s.libraryTitles.includes((work.title || '').toLowerCase().trim()),
    isAdding: (s) => (work) => !!work && !!s._adding[work.id],
    genreLabels: (s) => {
      const m = Object.fromEntries(s.genreCatalog.map(g => [g.value, g.label]))
      return s.genres.map(v => ({ value: v, label: m[v] || v }))
    },
  },
  actions: {
    _qs(page) {
      const p = new URLSearchParams()
      if (this.query.trim()) p.set('q', this.query.trim())
      if (this.type) p.set('type', this.type)
      if (this.sort) p.set('sort', this.sort)
      p.set('page', String(page))
      p.set('limit', String(PAGE_SIZE))
      let s = p.toString()
      for (const g of this.genres) s += `&genre=${encodeURIComponent(g)}`
      return s
    },
    // Reejecuta desde la página 1 (al cambiar cualquier filtro). Anti-carrera con _reqId.
    async browse() {
      const rid = ++this._reqId
      this.loading = true
      this.error = ''
      try {
        const d = await api.get(`/api/discovery/browse?${this._qs(1)}`)
        if (rid !== this._reqId) return
        this.items = d.works || []
        this.page = 1
        this.hasMore = !!d.has_more
      } catch (_) {
        if (rid !== this._reqId) return
        this.items = []; this.hasMore = false
        this.error = 'No se pudo cargar ahora mismo. Reintenta en un momento.'
      } finally {
        if (rid === this._reqId) this.loading = false
      }
    },
    async loadMore() {
      if (this.loadingMore || !this.hasMore) return
      const rid = this._reqId
      this.loadingMore = true
      try {
        const d = await api.get(`/api/discovery/browse?${this._qs(this.page + 1)}`)
        if (rid !== this._reqId) return          // cambió el filtro mientras cargaba
        this.items = [...this.items, ...(d.works || [])]
        this.page += 1
        this.hasMore = !!d.has_more
      } catch (_) { /* silencioso: el botón sigue disponible para reintentar */ }
      finally { this.loadingMore = false }
    },

    setType(id) { this.type = id; this.browse() },
    setSort(id) { this.sort = id; this.browse() },
    toggleGenre(value) {
      this.genres = this.genres.includes(value)
        ? this.genres.filter(g => g !== value)
        : [...this.genres, value]
      this.browse()
    },
    clearGenres() { if (this.genres.length) { this.genres = []; this.browse() } },
    clearFilters() {
      this.query = ''; this.type = ''; this.genres = []; this.sort = 'popularity'
      this.browse()
    },
    // Búsqueda por texto con debounce (mientras escribes).
    onQueryInput() {
      clearTimeout(this._qTimer)
      this._qTimer = setTimeout(() => this.browse(), 350)
    },
    submitQuery() { clearTimeout(this._qTimer); this.browse() },

    async loadGenres() {
      if (this.genreCatalog.length) return
      try {
        const d = await api.get('/api/discovery/genres')
        this.genreCatalog = d.genres || []
      } catch (_) { /* el filtro de género quedará vacío pero el resto funciona */ }
    },

    // Arranque de la vista: catálogo de géneros + primera rejilla + estado de biblioteca.
    async init() {
      this.loadGenres()
      this.loadLibrary()
      if (!this.items.length) this.browse()
    },

    // ── Ficha ──
    async openWork(summary) {
      this.work = { ...summary, _partial: true }
      this.workLoading = true
      try {
        const d = await api.get(`/api/discovery/work/${encodeURIComponent(summary.id)}`)
        if (this.work && this.work.id === summary.id) this.work = d.work || this.work
      } catch (_) { /* nos quedamos con el summary */ }
      finally { this.workLoading = false }
    },
    closeWork() { this.work = null; this.workLoading = false },

    // ── Biblioteca ──
    async loadLibrary() {
      try {
        const lib = await api.get('/api/mangadex/local_library') || []
        this.libraryTitles = lib.map(m => (m.title || '').toLowerCase().trim()).filter(Boolean)
      } catch (_) { /* no bloquea el descubrimiento */ }
    },
    async addToLibrary(work) {
      if (!work || this.inLibrary(work) || this._adding[work.id]) return
      const ui = useUiStore()
      this._adding = { ...this._adding, [work.id]: true }
      try {
        await api.post('/api/mangadex/local_library/add', {
          manga: {
            id: work.title, title: work.title, cover: work.cover || null,
            al_id: (work.ids && work.ids.anilist) || null, kind: 'tracked',
          },
        })
        const key = (work.title || '').toLowerCase().trim()
        if (key && !this.libraryTitles.includes(key)) this.libraryTitles.push(key)
        ui.toast('Añadido a tu biblioteca', 'ok')
      } catch (_) {
        ui.toast('No se pudo añadir a la biblioteca', 'error')
      } finally {
        const a = { ...this._adding }; delete a[work.id]; this._adding = a
      }
    },
  },
})
