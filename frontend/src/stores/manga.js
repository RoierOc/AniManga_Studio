import { defineStore } from 'pinia'
import { api } from '@/lib/api'
import { onStatus } from '@/lib/sse'
import { useUiStore } from './ui'
import { taskId } from '@/lib/manga'

let statusBound = false

export const useMangaStore = defineStore('manga', {
  state: () => ({
    // detail modal
    current: null,            // { id, name, source_meta } of the open manga
    chapters: [],             // [{ chapter, page_count }]
    upscaled: {},             // { chapterNorm: true | 'partial' }
    modalLoading: false,

    // live task status (from SSE aggregated payload)
    downloads: {},
    upscale: {},

    // reader
    reader: null,             // { title, chapter, source } | null
    pages: [],
    page: 0,
    readerLoading: false,
    mode: localStorage.getItem('reader-mode') || 'paged',   // paged | webtoon
    fit: localStorage.getItem('reader-fit') || 'width',     // width | height
    dir: localStorage.getItem('reader-dir') || 'rtl',       // rtl | ltr
  }),

  getters: {
    // upscale status for a given chapter, or null
    chapterTask: (s) => (chapter) => {
      if (!s.current) return null
      const t = s.upscale[taskId(s.current.id, chapter, 'upscale')]
      return t && ['starting', 'started', 'upscaling'].includes(t.status) ? t : null
    },
    sortedChapters: (s) => [...s.chapters].sort((a, b) => parseFloat(b.chapter) - parseFloat(a.chapter)),
  },

  actions: {
    init() {
      if (statusBound) return
      statusBound = true
      onStatus((data) => {
        this.downloads = data.downloads || {}
        this.upscale = data.upscale || {}
        // when an upscale for the open manga finishes, refresh its chapter map
        if (this.current) {
          const finished = Object.entries(this.upscale).some(([k, v]) =>
            k.startsWith(taskId(this.current.id, '', 'upscale').slice(0, -3)) && v.status === 'done')
          if (finished) this._refreshUpscaled()
        }
      })
    },

    async open(manga) {
      this.current = { id: manga.id || manga.name, name: manga.name, cover: manga.cover, source_meta: manga.source_meta }
      this.chapters = []
      this.upscaled = {}
      this.modalLoading = true
      try {
        const d = await api.get(`/api/library/${encodeURIComponent(this.current.id)}`)
        this.chapters = d.chapters || []
        this.upscaled = d.upscaled || {}
        if (d.source_meta) this.current.source_meta = d.source_meta
      } catch (_) { useUiStore().toast('No se pudieron cargar los capítulos', 'error') }
      finally { this.modalLoading = false }
    },
    close() { this.current = null },

    async _refreshUpscaled() {
      try {
        const d = await api.get(`/api/library/${encodeURIComponent(this.current.id)}`)
        this.upscaled = d.upscaled || {}
      } catch (_) {}
    },

    async upscaleChapter(chapter, opts = {}) {
      try {
        await api.post('/api/upscale/upscale_chapter', { title: this.current.id, chapter, eco: opts.eco ?? true, fast: opts.fast ?? false })
        useUiStore().toast(`Escalando 4K · cap. ${chapter}`, 'info')
      } catch (_) { useUiStore().toast('No se pudo iniciar el escalado', 'error') }
    },
    async repairChapter(chapter) {
      try {
        await api.post('/api/upscale/repair_chapter', { title: this.current.id, chapter })
        useUiStore().toast(`Reparando cap. ${chapter}`, 'info')
      } catch (_) { useUiStore().toast('No se pudo reparar', 'error') }
    },
    async cancelUpscale(chapter) {
      try { await api.post(`/api/upscale/cancel/${taskId(this.current.id, chapter, 'upscale')}`, {}) } catch (_) {}
    },
    async deleteChapter(chapter) {
      try {
        await api.post('/api/download/delete_chapter', { title: this.current.id, chapter })
        this.chapters = this.chapters.filter(c => c.chapter !== chapter)
        delete this.upscaled[chapter]
      } catch (_) { useUiStore().toast('No se pudo borrar el capítulo', 'error') }
    },

    /* ── Reader ─────────────────────────────────────────────────────────── */
    async read(chapter, source = 'auto') {
      this.readerLoading = true
      this.reader = { title: this.current.id, chapter, source }
      this.pages = []
      this.page = 0
      try {
        const d = await api.post('/api/reader/read_chapter', { title: this.current.id, chapter, source })
        this.pages = d.pages || []
        if (this.reader) this.reader.source = d.source
      } catch (_) { useUiStore().toast('No se pudo abrir el capítulo', 'error'); this.reader = null }
      finally { this.readerLoading = false }
    },
    closeReader() { this.reader = null; this.pages = [] },
    nextPage() { if (this.page < this.pages.length - 1) this.page++ },
    prevPage() { if (this.page > 0) this.page-- },
    setMode(m) { this.mode = m; localStorage.setItem('reader-mode', m) },
    setFit(f) { this.fit = f; localStorage.setItem('reader-fit', f) },
    toggleDir() { this.dir = this.dir === 'rtl' ? 'ltr' : 'rtl'; localStorage.setItem('reader-dir', this.dir) },
  },
})
