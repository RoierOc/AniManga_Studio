/* Store de NOVELAS — "Buscar para leer" + biblioteca.
 *
 * El backend (`/api/novels`, que proxea el sidecar de plugins LNReader) hace todo el trabajo
 * sucio; aquí solo se guarda el estado de la búsqueda de versiones y la novela abierta.
 * Deliberadamente NO hay traducción ni escalado: no aplican a texto. */
import { defineStore } from 'pinia'
import { api } from '@/lib/api'
import { useUiStore } from '@/stores/ui'

export const useNovelsStore = defineStore('novels', {
  state: () => ({
    finding: false,
    findQuery: '',
    versions: [],      // [{ pluginId, name, path, cover }]
    findSources: [],   // [{ id, status, error }] — para explicar por qué faltan resultados
    library: [],       // entradas kind:'novel' de local_library
    novel: null,       // novela abierta: { title, pluginId, path, chapters:[…] }
    novelLoading: false,
    _adding: {},

    // ── Lector de texto ──
    reader: null,          // { novelId, title, pluginId, chapterIndex, chapterName } | null
    chapterHtml: '',
    chapterWords: 0,
    chapterMinutes: 0,
    chapterLoading: false,
    // Ajustes de lectura de TEXTO (nada que ver con los de imagen: aquí manda la tipografía).
    fontSize: parseFloat(localStorage.getItem('novel-fontsize') || '1.15') || 1.15,
    lineHeight: parseFloat(localStorage.getItem('novel-lineheight') || '1.8') || 1.8,
    measure: parseInt(localStorage.getItem('novel-measure') || '38', 10) || 38, // ancho en rem
    serif: localStorage.getItem('novel-serif') !== '0',
    theme: localStorage.getItem('novel-theme') || 'night',   // night | sepia | light
    progress: JSON.parse(localStorage.getItem('novel-progress-v1') || '{}'),
  }),

  getters: {
    // Fuentes consultadas que no respondieron: se muestran como aviso, no como error duro
    // (regla del proyecto: "falló ≠ no había").
    failedSources: (s) => s.findSources.filter(x => x.status !== 'ok').map(x => x.id),
    inLibrary: (s) => (v) => s.library.some(n => n.novel?.pluginId === v.pluginId && n.novel?.path === v.path),
    isAdding: (s) => (v) => !!s._adding[`${v?.pluginId}:${v?.path}`],
  },

  actions: {
    async loadLibrary() {
      try { this.library = await api.get('/api/novels/library') || [] } catch (_) { /* no bloquea */ }
    },

    /** Busca el título en las fuentes curadas (EN+ES) y ofrece las versiones encontradas. */
    async findVersions(title) {
      if (!title) return
      this.finding = true; this.findQuery = title; this.versions = []; this.findSources = []
      try {
        const r = await api.post('/api/novels/find', { title })
        this.versions = r.results || []
        this.findSources = r.sources || []
      } catch (_) {
        useUiStore().toast('No se pudo buscar la novela', 'error')
      } finally { this.finding = false }
    },

    clearFind() { this.versions = []; this.findSources = []; this.findQuery = '' },

    async addToLibrary(v, title) {
      const key = `${v.pluginId}:${v.path}`
      if (this.inLibrary(v) || this._adding[key]) return
      const ui = useUiStore()
      this._adding = { ...this._adding, [key]: true }
      try {
        await api.post('/api/novels/library/add', {
          title: title || v.name, pluginId: v.pluginId, path: v.path, cover: v.cover || null,
        })
        await this.loadLibrary()
        ui.toast('Novela añadida a tu biblioteca', 'ok')
      } catch (_) {
        ui.toast('No se pudo añadir la novela', 'error')
      } finally {
        const a = { ...this._adding }; delete a[key]; this._adding = a
      }
    },

    /** Ficha + lista de capítulos de una novela (para el lector, F4). */
    async openNovel(pluginId, path, title = '') {
      this.novelLoading = true; this.novel = null
      try {
        const n = await api.post('/api/novels/novel', { pluginId, path })
        this.novel = { ...n, title: n.name || title, pluginId, path }
      } catch (_) {
        useUiStore().toast('No se pudo abrir la novela', 'error')
      } finally { this.novelLoading = false }
    },

    /** Texto de un capítulo: { html, text, words, minutes }. */
    async chapter(pluginId, path) {
      return await api.post('/api/novels/chapter', { pluginId, path })
    },

    // ── Lector ────────────────────────────────────────────────────────────────
    /** Abre la novela en el lector, retomando por donde iba si hay progreso. */
    async openReader(entry) {
      const { pluginId, path } = entry.novel || entry
      const novelId = entry.id || `novel:${pluginId}:${path}`
      await this.openNovel(pluginId, path, entry.title)
      if (!this.novel) return
      const saved = this.progress[novelId]
      this.reader = { novelId, title: this.novel.title, pluginId, chapterIndex: -1, chapterName: '' }
      await this.goChapter(saved?.chapterIndex || 0, saved?.scroll || 0)
    },

    closeReader() { this.reader = null; this.chapterHtml = ''; this.novel = null },

    async goChapter(index, scroll = 0) {
      const chapters = this.novel?.chapters || []
      const ch = chapters[index]
      if (!ch || !this.reader) return
      this.chapterLoading = true; this.chapterHtml = ''
      try {
        const r = await this.chapter(this.reader.pluginId, ch.path)
        this.chapterHtml = r.html || ''
        this.chapterWords = r.words || 0
        this.chapterMinutes = r.minutes || 0
        this.reader = { ...this.reader, chapterIndex: index, chapterName: ch.name || `Capítulo ${index + 1}` }
        this.saveProgress(scroll)
      } catch (_) {
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
      localStorage.setItem('novel-progress-v1', JSON.stringify(this.progress))
    },

    setSetting(key, value) {
      this[key] = value
      const store = { fontSize: 'novel-fontsize', lineHeight: 'novel-lineheight', measure: 'novel-measure',
                      serif: 'novel-serif', theme: 'novel-theme' }[key]
      if (store) localStorage.setItem(store, typeof value === 'boolean' ? (value ? '1' : '0') : String(value))
    },
  },
})
