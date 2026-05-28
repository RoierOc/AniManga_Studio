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
    exports: {},
    queueOpen: true,

    // reader
    reader: null,             // { title, chapter, source, kind } | null  (kind: manga | cbz)
    pages: [],
    page: 0,
    readerLoading: false,
    mode: localStorage.getItem('reader-mode') || 'paged',   // paged | webtoon
    fit: localStorage.getItem('reader-fit') || 'width',     // width | height | original
    dir: localStorage.getItem('reader-dir') || 'rtl',       // rtl | ltr
    zoom: 1.0,
    panX: 0,
    panY: 0,
    barsHidden: false,
    compareMode: false,
    compareX: 50,
    progress: (() => { try { return JSON.parse(localStorage.getItem('manga-progress-v1') || '{}') } catch { return {} } })(),

    // upscale model + mode
    models: {},                 // { key: label }
    activeModel: 'eula',
    eco: localStorage.getItem('upscale-eco') !== '0',   // eco on by default (lets MPV run)
  }),

  getters: {
    // upscale status for a given chapter, or null
    chapterTask: (s) => (chapter) => {
      if (!s.current) return null
      const t = s.upscale[taskId(s.current.id, chapter, 'upscale')]
      return t && ['starting', 'started', 'upscaling'].includes(t.status) ? t : null
    },
    sortedChapters: (s) => [...s.chapters].sort((a, b) => parseFloat(b.chapter) - parseFloat(a.chapter)),

    // chapter navigation within the reader (ascending order)
    chapterListAsc: (s) => [...s.chapters].sort((a, b) => (parseFloat(a.chapter) || 0) - (parseFloat(b.chapter) || 0)),
    chapterIndex() { return this.chapterListAsc.findIndex(c => String(c.chapter) === String(this.reader?.chapter)) },
    canPrevChapter() { return this.chapterIndex > 0 },
    canNextChapter() { return this.chapterIndex >= 0 && this.chapterIndex < this.chapterListAsc.length - 1 },

    // compare (original vs upscaled) — only paged manga, chapter has an upscale
    canCompare: (s) => s.reader?.kind === 'manga' && s.mode === 'paged' &&
      (s.reader?.source === 'upscaled' || s.upscaled[s.reader?.chapter] === true || s.upscaled[s.reader?.chapter] === 'partial'),
    pageOrigUrl: (s) => {
      const p = s.pages[s.page]; if (!p) return ''
      return p.startsWith('/') ? p : '/uploads/original/' + p
    },
    pageUpUrl: (s) => {
      const p = s.pages[s.page]; if (!p) return ''
      return p.startsWith('/') ? p : '/uploads/upscaled/' + p
    },

    // Unified active task list for the global queue widget.
    activeTasks: (s) => {
      const out = []
      const pct = (p, t) => t ? Math.min(100, Math.round((p || 0) / t * 100)) : (p || 0)
      for (const [id, v] of Object.entries(s.downloads)) {
        if (['done', 'complete', 'error', 'cancelled', 'interrupted'].includes(v.status)) continue
        out.push({ id, kind: 'download', label: `${v.title || ''} · cap. ${v.chapter ?? ''}`, status: v.status, pct: pct(v.progress, v.total), msg: v.message })
      }
      for (const [id, v] of Object.entries(s.upscale)) {
        if (!['starting', 'started', 'upscaling'].includes(v.status)) continue
        out.push({ id, kind: 'upscale', label: `4K · ${v.title || ''} cap. ${v.chapter ?? ''}`, status: v.status, pct: pct(v.progress ?? v.current, v.total) })
      }
      for (const [id, v] of Object.entries(s.exports)) {
        const done = v.status === 'complete'
        if (['error', 'cancelled'].includes(v.status)) continue
        out.push({ id, kind: 'export', label: v.volume_name || 'Tomo', status: v.status, pct: pct(v.progress, v.total), done, file: done })
      }
      return out
    },
  },

  actions: {
    init() {
      if (statusBound) return
      statusBound = true
      onStatus((data) => {
        this.downloads = data.downloads || {}
        this.upscale = data.upscale || {}
        this.exports = data.exports || {}
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
        await api.post('/api/upscale/upscale_chapter', { title: this.current.id, chapter, eco: opts.eco ?? this.eco, fast: opts.fast ?? false })
        useUiStore().toast(`Escalando 4K · cap. ${chapter}`, 'info')
      } catch (_) { useUiStore().toast('No se pudo iniciar el escalado', 'error') }
    },
    async loadModels() {
      try { const d = await api.get('/api/upscale/models'); this.models = d.models || {}; this.activeModel = d.active || 'eula' } catch (_) {}
    },
    async setModel(key) {
      try {
        const d = await api.post('/api/upscale/set_model', { model: key })
        if (d.status === 'ok') { this.activeModel = key; useUiStore().toast(`Modelo: ${d.label}`, 'ok') }
        else useUiStore().toast(d.message || 'Error al cambiar modelo', 'error')
      } catch (_) { useUiStore().toast('Error al cambiar modelo', 'error') }
    },
    setEco(v) { this.eco = v; localStorage.setItem('upscale-eco', v ? '1' : '0'); api.post('/api/upscale/mode', { eco: v }).catch(() => {}) },
    async upscaleAll() {
      const chapters = this.chapters.map(c => c.chapter).filter(ch => this.upscaled[ch] !== true)
      if (!chapters.length) { useUiStore().toast('Todos los capítulos ya están en 4K', 'info'); return }
      try {
        await api.post('/api/upscale/upscale_manga', { title: this.current.id, chapters, eco: this.eco })
        useUiStore().toast(`Escalando ${chapters.length} capítulos a 4K`, 'info')
      } catch (_) { useUiStore().toast('No se pudo iniciar', 'error') }
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

    /* ── Task queue actions ─────────────────────────────────────────────── */
    async cancelTask(task) {
      try {
        if (task.kind === 'download') await api.post(`/api/download/cancel/${task.id}`, {})
        else if (task.kind === 'upscale') await api.post(`/api/upscale/cancel/${task.id}`, {})
      } catch (_) {}
    },
    exportFileUrl(id) { return `/api/export/file/${id}` },

    /* ── Tomo export ────────────────────────────────────────────────────── */
    async exportTomo({ chapters, volumeName, format = 'cbz', quality = 92, downscaleHalf = false }) {
      try {
        const d = await api.post('/api/export/start', {
          title: this.current.id, chapters, volume_name: volumeName || this.current.name,
          format, quality, downscale_half: downscaleHalf,
        })
        useUiStore().toast(`Exportando "${volumeName || this.current.name}"…`, 'info')
        return d.task_id
      } catch (_) { useUiStore().toast('No se pudo iniciar la exportación', 'error'); return null }
    },

    /* ── Management (rename, cover, health, integrity) ──────────────────── */
    async editMeta({ newTitle, coverUrl, coverB64 }) {
      const ui = useUiStore()
      try {
        const body = {}
        if (newTitle && newTitle !== this.current.id) body.new_title = newTitle
        if (coverUrl) body.cover_url = coverUrl
        if (coverB64) body.cover_b64 = coverB64
        const res = await fetch(`/api/library/meta/${encodeURIComponent(this.current.id)}`, {
          method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
        }).then(r => r.json())
        if (res.error) { ui.toast(res.error, 'error'); return false }
        if (res.new_title) { this.current.id = res.new_title; this.current.name = res.new_title }
        if (coverUrl) this.current.cover = coverUrl
        ui.toast('Metadatos actualizados', 'ok')
        return true
      } catch (_) { ui.toast('No se pudo actualizar', 'error'); return false }
    },
    async scanCorrupt() {
      const ui = useUiStore()
      try {
        const d = await api.get(`/api/library/scan_corrupt/${encodeURIComponent(this.current.id)}`)
        ui.toast(d.corrupt?.length ? `${d.corrupt.length} páginas corruptas de ${d.checked}` : `${d.checked} páginas OK ✓`, d.corrupt?.length ? 'warn' : 'ok')
        return d
      } catch (_) { ui.toast('Error escaneando', 'error') }
    },

    /* ── Reader ─────────────────────────────────────────────────────────── */
    _resetView() { this.zoom = 1.0; this.panX = 0; this.panY = 0; this.compareMode = false; this.barsHidden = false },

    async read(chapter, source = 'auto') {
      this.readerLoading = true
      this._resetView()
      this.reader = { title: this.current.id, chapter, source, kind: 'manga' }
      this.pages = []
      this.page = 0
      try {
        const d = await api.post('/api/reader/read_chapter', { title: this.current.id, chapter, source })
        this.pages = d.pages || []
        if (this.reader) this.reader.source = d.source
        // restore last-read page for this chapter
        const pr = this.progress[this.current.id]
        if (pr && String(pr.lastChapter) === String(chapter) && pr.lastPage > 0 && pr.lastPage < this.pages.length)
          this.page = pr.lastPage
      } catch (_) { useUiStore().toast('No se pudo abrir el capítulo', 'error'); this.reader = null }
      finally { this.readerLoading = false }
    },
    openReaderRaw(title, pages, label = '') {
      this._resetView()
      this.reader = { title, chapter: label, source: '', kind: 'cbz' }
      this.pages = pages
      this.page = 0
    },
    closeReader() { this.reader = null; this.pages = []; this._resetView() },

    setPage(i) {
      if (i < 0 || i >= this.pages.length) return
      this.page = i; this.panX = 0; this.panY = 0
      this._saveProgress()
      if (i >= this.pages.length - 1) this.markRead(this.reader?.chapter)
    },
    nextPage() { this.setPage(this.page + 1) },
    prevPage() { this.setPage(this.page - 1) },

    setMode(m) { this.mode = m; localStorage.setItem('reader-mode', m); this._resetView() },
    cycleFit() { const M = ['width', 'height', 'original']; this.fit = M[(M.indexOf(this.fit) + 1) % 3]; localStorage.setItem('reader-fit', this.fit) },
    toggleDir() { this.dir = this.dir === 'rtl' ? 'ltr' : 'rtl'; localStorage.setItem('reader-dir', this.dir) },

    zoomBy(delta) {
      this.zoom = Math.max(0.5, Math.min(3.0, this.zoom + delta))
      if (this.zoom <= 1.0) { this.panX = 0; this.panY = 0 }
    },
    resetZoom() { this.zoom = 1.0; this.panX = 0; this.panY = 0 },

    toggleCompare() { if (!this.canCompare) { this.compareMode = false; return } this.compareMode = !this.compareMode; if (this.compareMode) this.compareX = 50 },

    async goNextChapter() {
      if (!this.canNextChapter) return
      const next = this.chapterListAsc[this.chapterIndex + 1]
      await this.read(next.chapter, this.upscaled[next.chapter] ? 'upscaled' : 'auto')
    },
    async goPrevChapter() {
      if (!this.canPrevChapter) return
      const prev = this.chapterListAsc[this.chapterIndex - 1]
      await this.read(prev.chapter, this.upscaled[prev.chapter] ? 'upscaled' : 'auto')
    },

    /* reading progress (localStorage, per manga id) */
    _persistProgress() { localStorage.setItem('manga-progress-v1', JSON.stringify(this.progress)) },
    _saveProgress() {
      if (this.reader?.kind !== 'manga') return
      const k = this.reader.title
      const e = this.progress[k] || (this.progress[k] = { read: {} })
      e.lastChapter = String(this.reader.chapter); e.lastPage = this.page
      this._persistProgress()
    },
    markRead(chapter) {
      if (this.reader?.kind !== 'manga' || chapter == null) return
      const k = this.reader.title
      const e = this.progress[k] || (this.progress[k] = { read: {} })
      ;(e.read ||= {})[String(chapter)] = true
      this._persistProgress()
    },
    isChapterRead(chapter) { return !!this.progress[this.current?.id]?.read?.[String(chapter)] },
    toggleChapterRead(chapter) {
      const k = this.current?.id; if (!k) return
      const e = this.progress[k] || (this.progress[k] = { read: {} })
      e.read ||= {}
      if (e.read[String(chapter)]) delete e.read[String(chapter)]; else e.read[String(chapter)] = true
      this._persistProgress()
    },
  },
})
