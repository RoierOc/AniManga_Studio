import { defineStore } from 'pinia'
import { api } from '@/lib/api'
import { onStatus } from '@/lib/sse'
import { useUiStore } from './ui'
import { taskId, canonicalTitle } from '@/lib/manga'

let statusBound = false
let previewTimer = null

// Preferred MangaDex chapter language (Spanish-first, then English), or the first
// available — so a freshly opened manga shows one clean language, not all at once.
const LANG_PRIO = ['es-la', 'es', 'en', 'pt-br', 'ja']
function preferredLang(langs) {
  for (const p of LANG_PRIO) if (langs.includes(p)) return p
  return langs[0] || ''
}

// Build a plain { chapterKey: {status, progress, total, pct} } map from a
// {chapterKey: taskId} table and the live task-status dict. Returns plain data
// so Vue tracks it through a normal computed (the previous getter-returning-a-
// function pattern did not re-render the progress rings).
function buildProgressMap(taskTable, statusDict) {
  const out = {}
  for (const [ch, tid] of Object.entries(taskTable)) {
    const d = statusDict[tid] || {}
    const total = d.total || 0
    const progress = d.progress || 0
    out[ch] = {
      status: d.status || 'starting',
      progress,
      total,
      pct: total ? Math.min(100, Math.round((progress / total) * 100)) : 0,
    }
  }
  return out
}

export const useMangaStore = defineStore('manga', {
  state: () => ({
    // detail modal
    current: null,            // { id, name, source_meta } of the open manga
    chapters: [],             // [{ chapter, page_count }] — local downloaded chapters
    upscaled: {},             // { chapterNorm: true | 'partial' }
    modalLoading: false,
    sourceChapters: [],       // [{ id, chapterNumber, name, scanlator, pageCount }] — from Suwayomi source
    sourceLoading: false,
    mdId: null,               // MangaDex UUID for library manga with MD pairing
    mdChapters: [],           // MangaDex chapters for library manga
    mdChaptersLoading: false,
    mdLang: '',               // language filter for MD chapters ('' = all)
    dlTasks: {},              // { chapterKey: taskId } — maps chapter to SSE download task ID
    upTasks: {},              // { chapterKey: taskId } — maps chapter to SSE upscale task ID
    cancelledIds: [],         // task ids cancelled locally — hidden everywhere until the backend catches up
    libraryDirty: 0,          // bumped after a manga is deleted so LibraryView reloads

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
    // scanlation comparison (different downloaded groups of the same chapter)
    scanCompareMode: false,
    comparePages2: [],
    scanCmp: { open: false, chapter: null, variants: [], loading: false },
    progress: (() => { try { return JSON.parse(localStorage.getItem('manga-progress-v1') || '{}') } catch { return {} } })(),

    // chapter updates (followed manga with new chapters on MangaDex)
    updates: [],                // [{manga_id, title, cover, new_count, new_chapters}]
    updatesLoaded: false,

    // chapter health + color pages + offline covers
    health: {},                 // chapterNorm -> { status, missing_upscaled, missing_pages }
    colorPages: [],             // [{chapter, filename, url, label}]
    colorLoading: false,
    excludedPages: [],          // filenames excluded from export
    exportPreview: { pages: 0, est_mb: 0, upscaled_pages: 0, original_pages: 0 },  // live tomo size/page estimate
    offlineCovers: null,        // { running, done, total } | null

    // export destinations
    drive: { configured: false, connected: false, email: '' },
    webdav: { phoneUrl: '', localUrl: '' },

    // MangaDex Tomo Builder (volumes + covers)
    mdex: {
      id: null, mdManga: null,
      search: '', results: [], searching: false,
      volumes: [], volumesLoading: false,
      covers: [], coversLoading: false,
      selectedCover: null, coverLoadingId: null, coverB64: '', coverUrl: '',
    },

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
    // Download/upscale progress as PLAIN reactive maps keyed by chapter.
    // A computed map (clear deps on dlTasks + downloads) re-renders reliably,
    // unlike the old getter-returning-a-function which the template read several
    // times per binding and Vue failed to track — the rings stayed at 0%.
    downloadByChapter: (s) => buildProgressMap(s.dlTasks, s.downloads),
    upscaleByChapter: (s) => buildProgressMap(s.upTasks, s.upscale),
    sortedChapters: (s) => [...s.chapters].sort((a, b) => parseFloat(b.chapter) - parseFloat(a.chapter)),
    mdLangs: (s) => [...new Set(s.mdChapters.map(c => c.language).filter(Boolean))].sort(),
    // Merge local + source + MD chapters, showing all available for download
    mergedChapters: (s) => {
      const dlNorms = new Set(s.chapters.map(c => String(c.chapter)))
      const result = [...s.chapters]
      for (const sc of s.sourceChapters) {
        if (!dlNorms.has(sc.chapterNorm)) {
          result.push({ chapter: sc.chapterNorm, page_count: sc.pageCount || 0, _sourceId: sc.id, _sourceName: sc.name, _scanlator: sc.scanlator })
        }
      }
      for (const mc of s.mdChapters) {
        if (s.mdLang && mc.language !== s.mdLang) continue
        const cNorm = String(mc.chapter)
        if (!dlNorms.has(cNorm)) {
          result.push({ chapter: cNorm, page_count: mc.pages || 0, _mdChapterId: mc.id, _mdGroup: (mc.groups || []).join(', '), _mdTitle: mc.title, _mdLang: mc.language })
        }
      }
      return result.sort((a, b) => parseFloat(b.chapter) - parseFloat(a.chapter))
    },
    hasSourceMeta: (s) => !!(s.current?.source_meta?.sourceId && s.current?.source_meta?.mangaId) || !!s.mdId,
    // Whether we have any external chapters to show (source, MD, or local)
    hasAnyChapters: (s) => s.mergedChapters.length > 0,

    updatesByTitle: (s) => { const m = {}; for (const u of s.updates) m[u.title] = u; return m },

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
      if (s.scanCompareMode && s.comparePages2.length) {
        const p2 = s.comparePages2[Math.min(s.page, s.comparePages2.length - 1)]
        return p2 ? (p2.startsWith('/') ? p2 : '/uploads/original/' + p2) : ''
      }
      const p = s.pages[s.page]; if (!p) return ''
      return p.startsWith('/') ? p : '/uploads/upscaled/' + p
    },

    // Unified active task list for the global queue widget.
    activeTasks: (s) => {
      const out = []
      const pct = (p, t) => t ? Math.min(100, Math.round((p || 0) / t * 100)) : (p || 0)
      for (const [id, v] of Object.entries(s.downloads)) {
        if (s.cancelledIds.includes(id)) continue
        if (['done', 'complete', 'error', 'cancelled', 'interrupted'].includes(v.status)) continue
        out.push({ id, kind: 'download', label: `${v.title || ''} · cap. ${v.chapter ?? ''}`, status: v.status, pct: pct(v.progress, v.total), msg: v.message })
      }
      for (const [id, v] of Object.entries(s.upscale)) {
        if (s.cancelledIds.includes(id)) continue
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
      // Sync the eco toggle (default on) to the backend so MPV-friendly GPU
      // throttling applies from first load, not only after the user toggles it.
      api.post('/api/upscale/mode', { eco: this.eco }).catch(() => {})
      onStatus((data) => {
        this.downloads = data.downloads || {}
        this.upscale = data.upscale || {}
        this.exports = data.exports || {}
        // Drop cancelled ids once the backend has actually stopped them (terminal
        // status or gone), so the set can't grow unbounded.
        if (this.cancelledIds.length) {
          this.cancelledIds = this.cancelledIds.filter(id => {
            const v = this.downloads[id] || this.upscale[id]
            return v && ['starting', 'started', 'upscaling', 'downloading'].includes(v.status)
          })
        }
        if (this.current) {
          const upDone = Object.entries(this.upscale).some(([k, v]) =>
            k.startsWith(taskId(this.current.id, '', 'upscale').slice(0, -3)) && v.status === 'done')
          if (upDone) this._refreshUpscaled()
        }
      })
      // Direct poll for download & upscale progress — bypass SSE getter issue
      setInterval(async () => {
        for (const [ch, tid] of Object.entries(this.dlTasks)) {
          try {
            const s = await api.get(`/api/status/download/${encodeURIComponent(tid)}`)
            if (!s) continue
            if (['complete', 'done', 'cancelled', 'error'].includes(s.status)) {
              delete this.dlTasks[ch]; this._refreshChapters()
            }
            this.downloads = { ...this.downloads, [tid]: s }
          } catch (_) {}
        }
        for (const [ch, tid] of Object.entries(this.upTasks)) {
          try {
            const s = await api.get(`/api/status/upscale/${encodeURIComponent(tid)}`)
            if (!s) continue
            if (['done', 'complete', 'cancelled', 'error'].includes(s.status)) {
              delete this.upTasks[ch]; this._refreshUpscaled()
            }
            this.upscale = { ...this.upscale, [tid]: s }
          } catch (_) {}
        }
      }, 1000)
    },

    async open(manga) {
      this.current = { id: manga.id || manga.name, name: manga.name, cover: manga.cover, source_meta: manga.source_meta }
      this.chapters = []
      this.sourceChapters = []
      this.mdChapters = []
      this.mdId = manga.mdId || null
      this.mdLang = ''
      this.upscaled = {}
      this.modalLoading = true
      try {
        // Load local chapters if downloaded (skip for MD-only entries)
        if (!manga.mdOnly) {
          const d = await api.get(`/api/library/${encodeURIComponent(this.current.id)}`)
          this.chapters = d.chapters || []
          this.upscaled = d.upscaled || {}
          if (d.source_meta) this.current.source_meta = d.source_meta
          // If source_meta exists, load source chapters
          const sm = this.current.source_meta
          if (sm?.sourceId && sm?.mangaId) {
            this.sourceLoading = true
            api.get(`/api/sources/manga/${sm.mangaId}/chapters`)
              .then(chs => {
                this.sourceChapters = (chs || []).map(ch => ({
                  ...ch,
                  chapterNorm: String(ch.chapterNumber ?? '').replace(/\.0$/, ''),
                  name: ch.name || `Cap. ${ch.chapterNumber ?? '?'}`,
                }))
              })
              .catch(() => {})
              .finally(() => { this.sourceLoading = false })
          }
        }
        // If mdId exists, load MangaDex chapters
        if (this.mdId) {
          this.mdChaptersLoading = true
          api.get(`/api/mangadex/chapters/${this.mdId}`)
            .then(chs => {
              this.mdChapters = chs || []
              // Default to one preferred language so the list isn't a confusing mix of
              // every language at once (the user can switch via the dropdown).
              const langs = [...new Set(this.mdChapters.map(c => c.language).filter(Boolean))]
              this.mdLang = preferredLang(langs)
            })
            .catch(() => {})
            .finally(() => { this.mdChaptersLoading = false })
        }
      } catch (_) { useUiStore().toast('No se pudieron cargar los capítulos', 'error') }
      finally { this.modalLoading = false }
    },
    close() { this.current = null },

    async downloadSourceChapter(ch) {
      const ui = useUiStore()
      const sm = this.current?.source_meta
      const sid = ch._sourceId || ch.id
      if (!sm?.sourceId || !sid) return
      const chKey = String(ch.chapter)
      this.dlTasks[chKey] = taskId(this.current.id, chKey, 'download')
      try {
        const pg = await api.get(`/api/sources/chapter/${sid}/pages`)
        const pageUrls = pg.pages || []
        if (!pageUrls.length) { ui.toast('Capítulo sin páginas', 'error'); delete this.dlTasks[chKey]; return }
        const res = await api.post('/api/download/download_source_chapter', {
          title: this.current.id,
          chapter: ch.chapterNorm || ch.chapterNumber || ch.chapter,
          pageUrls,
          sourceId: sm.sourceId,
          mangaId: sm.mangaId,
          sourceName: sm.sourceName || '',
          sourceLang: sm.sourceLang || '',
        })
        // Use real task_id if different from estimated
        if (res.task_id) this.dlTasks[chKey] = res.task_id
      } catch (_) { ui.toast('No se pudo descargar', 'error'); delete this.dlTasks[chKey] }
    },

    async downloadMdChapter(ch) {
      const ui = useUiStore()
      const cid = ch._mdChapterId
      if (!cid) return
      const chKey = String(ch.chapter)
      this.dlTasks[chKey] = taskId(this.current.id, chKey, 'download')
      try {
        const res = await api.post('/api/download/download_chapter', {
          title: this.current.id, chapter: ch.chapter, chapterId: cid, mangaId: this.mdId,
        })
        if (res.task_id) this.dlTasks[chKey] = res.task_id
      } catch (_) { ui.toast('No se pudo iniciar la descarga', 'error'); delete this.dlTasks[chKey] }
    },

    async _refreshUpscaled() {
      try {
        const d = await api.get(`/api/library/${encodeURIComponent(this.current.id)}`)
        this.upscaled = d.upscaled || {}
      } catch (_) {}
    },
    async _refreshChapters() {
      try {
        const d = await api.get(`/api/library/${encodeURIComponent(this.current.id)}`)
        this.chapters = d.chapters || []
        this.upscaled = d.upscaled || {}
        if (d.source_meta) this.current.source_meta = d.source_meta
      } catch (_) {}
    },

    async upscaleChapter(chapter, opts = {}) {
      const chKey = String(chapter)
      this.upTasks[chKey] = taskId(this.current.id, chKey, 'upscale')
      try {
        const res = await api.post('/api/upscale/upscale_chapter', { title: this.current.id, chapter, eco: opts.eco ?? this.eco, fast: opts.fast ?? false })
        // Use the backend's real task id so the progress ring matches SSE keys
        // even if the optimistic id normalization differs.
        if (res?.task_id) this.upTasks[chKey] = res.task_id
        useUiStore().toast(`Escalando 4K · cap. ${chapter}`, 'info')
      } catch (_) { useUiStore().toast('No se pudo iniciar el escalado', 'error'); delete this.upTasks[chKey] }
    },
    async loadUpdates() {
      try { this.updates = await api.get('/api/mangadex/updates') || [] } catch (_) { this.updates = [] }
      finally { this.updatesLoaded = true }
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
      for (const ch of chapters) this.upTasks[String(ch)] = taskId(this.current.id, String(ch), 'upscale')
      try {
        await api.post('/api/upscale/upscale_manga', { title: this.current.id, chapters, eco: this.eco })
        useUiStore().toast(`Escalando ${chapters.length} capítulos a 4K`, 'info')
      } catch (_) { useUiStore().toast('No se pudo iniciar', 'error'); this.upTasks = {} }
    },
    async repairChapter(chapter) {
      const chKey = String(chapter)
      this.upTasks[chKey] = taskId(this.current.id, chKey, 'upscale')
      try {
        await api.post('/api/upscale/repair_chapter', { title: this.current.id, chapter })
        useUiStore().toast(`Reparando cap. ${chapter}`, 'info')
      } catch (_) { useUiStore().toast('No se pudo reparar', 'error'); delete this.upTasks[chKey] }
    },
    async cancelUpscale(chapter) {
      const chKey = String(chapter)
      // Use the real task id stored when the upscale started (falls back to the
      // optimistic one), then drop it so the progress ring disappears at once.
      const tid = this.upTasks[chKey] || taskId(this.current.id, chapter, 'upscale')
      delete this.upTasks[chKey]
      this._markCancelled(tid)
      try { await api.post(`/api/upscale/cancel/${encodeURIComponent(tid)}`, {}) } catch (_) {}
      // Cancellation is cooperative; refresh shortly after so the chapter reflects
      // its final 4K/partial state.
      setTimeout(() => this._refreshUpscaled(), 1500)
    },
    // Hide a task from the queue + modal ring immediately, surviving SSE overwrites
    // until the backend reports it as stopped.
    _markCancelled(id) {
      if (!this.cancelledIds.includes(id)) this.cancelledIds = [...this.cancelledIds, id]
      for (const [ch, t] of Object.entries(this.upTasks)) if (t === id) delete this.upTasks[ch]
      for (const [ch, t] of Object.entries(this.dlTasks)) if (t === id) delete this.dlTasks[ch]
    },
    async deleteChapter(chapter) {
      try {
        await api.post('/api/download/delete_chapter', { title: this.current.id, chapter })
        this.chapters = this.chapters.filter(c => c.chapter !== chapter)
        delete this.upscaled[chapter]
      } catch (_) { useUiStore().toast('No se pudo borrar el capítulo', 'error') }
    },

    // Delete a whole manga from the library: downloaded + upscaled files, and the
    // MangaDex local-library entry if it's tracked there (mirrors the legacy flow).
    async deleteManga() {
      if (!this.current) return
      const title = this.current.id
      const name = this.current.name || title
      if (!confirm(`¿Borrar "${name}" de tu biblioteca?\nSe eliminarán los archivos descargados y escalados.`)) return
      const ui = useUiStore()
      let ok = false
      try { await api.del('/api/download/delete_manga', { body: { title } }); ok = true } catch (_) {}
      if (this.mdId) { try { await api.del(`/api/mangadex/local_library/remove/${encodeURIComponent(this.mdId)}`); ok = true } catch (_) {} }
      if (ok) {
        ui.toast(`"${name}" eliminado de la biblioteca`, 'ok')
        this.libraryDirty++
        this.close()
      } else {
        ui.toast('No se pudo eliminar el manga', 'error')
      }
    },

    /* ── Task queue actions ─────────────────────────────────────────────── */
    async cancelTask(task) {
      // Hide it from the queue (and the modal ring if visible) right away.
      this._markCancelled(task.id)
      try {
        if (task.kind === 'download') await api.post(`/api/download/cancel/${encodeURIComponent(task.id)}`, {})
        else if (task.kind === 'upscale') await api.post(`/api/upscale/cancel/${encodeURIComponent(task.id)}`, {})
      } catch (_) {}
      if (task.kind === 'upscale') setTimeout(() => this._refreshUpscaled(), 1500)
      else if (task.kind === 'download') setTimeout(() => this._refreshChapters(), 1500)
    },
    exportFileUrl(id) { return `/api/export/file/${id}` },

    /* ── Tomo export + destinations ─────────────────────────────────────── */
    async loadDestinations() {
      try { const d = await api.get('/api/drive/status'); this.drive = { configured: !!d.configured, connected: !!d.connected, email: d.email || '' } } catch (_) {}
      try { const w = await api.get('/api/webdav/status'); this.webdav = { phoneUrl: w.library_url_phone || '', localUrl: w.library_url_local || '' } } catch (_) {}
    },
    async connectDrive() {
      try { const d = await api.get('/api/drive/auth'); if (d.auth_url) window.open(d.auth_url, '_blank') } catch (_) { useUiStore().toast('Drive no configurado', 'error') }
    },
    async disconnectDrive() {
      try { await api.post('/api/drive/disconnect', {}); this.drive = { configured: this.drive.configured, connected: false, email: '' }; useUiStore().toast('Drive desconectado', 'info') } catch (_) {}
    },
    async exportTomo({ chapters, volumeName, format = 'cbz', quality = 92, downscaleHalf = false, coverB64 = '', toDrive = false }) {
      const body = {
        title: this.current.id, chapters, volume_name: volumeName || this.current.name,
        format, quality, downscale_half: downscaleHalf, ...(coverB64 ? { cover_data: coverB64 } : {}),
        ...(this.excludedPages.length ? { exclude_pages: this.excludedPages } : {}),
      }
      try {
        if (toDrive) {
          useUiStore().toast('Subiendo tomo a Google Drive…', 'info')
          const d = await api.post('/api/drive/upload_tomo', body)
          if (d.error) { useUiStore().toast(d.error, 'error'); return null }
          useUiStore().toast('✓ Tomo subido a Drive', 'ok'); return null
        }
        const d = await api.post('/api/export/start', body)
        useUiStore().toast(`Exportando "${volumeName || this.current.name}"…`, 'info')
        return d.task_id
      } catch (_) { useUiStore().toast('No se pudo exportar', 'error'); return null }
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
    async loadHealth() {
      try {
        const d = await api.get(`/api/library/chapter_health/${encodeURIComponent(this.current.id)}`)
        const m = {}; for (const h of (Array.isArray(d) ? d : [])) m[h.chapter] = h; this.health = m
      } catch (_) {}
    },
    async upscaleRange(from, to, fast = false) {
      const lo = Math.min(parseFloat(from), parseFloat(to)), hi = Math.max(parseFloat(from), parseFloat(to))
      if (isNaN(lo) || isNaN(hi)) return
      const chapters = this.chapters.map(c => c.chapter).filter(ch => { const n = parseFloat(ch); return !isNaN(n) && n >= lo && n <= hi && this.upscaled[ch] !== true })
      if (!chapters.length) { useUiStore().toast('Nada que escalar en ese rango', 'info'); return }
      try { await api.post('/api/upscale/upscale_manga', { title: this.current.id, chapters, eco: this.eco, fast }); useUiStore().toast(`Escalando ${chapters.length} capítulos`, 'info') }
      catch (_) { useUiStore().toast('No se pudo iniciar', 'error') }
    },
    async loadColorPages(chapters) {
      this.colorLoading = true; this.excludedPages = []
      try { this.colorPages = await api.post('/api/export/color_pages', { title: this.current.id, chapters }) || [] }
      catch (_) { this.colorPages = [] }
      finally { this.colorLoading = false }
    },
    toggleExclude(filename) {
      this.excludedPages = this.excludedPages.includes(filename) ? this.excludedPages.filter(f => f !== filename) : [...this.excludedPages, filename]
    },
    // Debounced live estimate of the tomo (pages + size) for the selected chapters.
    loadExportPreview(chapters, quality = 92) {
      clearTimeout(previewTimer)
      if (!chapters?.length || !this.current) {
        this.exportPreview = { pages: 0, est_mb: 0, upscaled_pages: 0, original_pages: 0 }
        return
      }
      previewTimer = setTimeout(async () => {
        try {
          const d = await api.post('/api/export/preview', { title: this.current.id, chapters, quality, exclude_pages: this.excludedPages })
          this.exportPreview = {
            pages: d.pages || 0, est_mb: d.est_mb || 0,
            upscaled_pages: d.upscaled_pages || 0, original_pages: d.original_pages || 0,
          }
        } catch (_) {}
      }, 400)
    },
    async downloadCoversOffline() {
      try {
        await api.post('/api/library/download_covers_offline', {})
        this.offlineCovers = { running: true, done: 0, total: 0 }
        const poll = setInterval(async () => {
          try {
            const s = await api.get('/api/library/offline_covers_status')
            this.offlineCovers = s
            if (!s.running) { clearInterval(poll); useUiStore().toast('Portadas descargadas ✓', 'ok') }
          } catch (_) { clearInterval(poll) }
        }, 1500)
      } catch (_) { useUiStore().toast('No se pudo iniciar la descarga de portadas', 'error') }
    },
    async scanCorrupt() {
      const ui = useUiStore()
      try {
        const d = await api.get(`/api/library/scan_corrupt/${encodeURIComponent(this.current.id)}`)
        ui.toast(d.corrupt?.length ? `${d.corrupt.length} páginas corruptas de ${d.checked}` : `${d.checked} páginas OK ✓`, d.corrupt?.length ? 'warn' : 'ok')
        return d
      } catch (_) { ui.toast('Error escaneando', 'error') }
    },

    /* ── MangaDex Tomo Builder ──────────────────────────────────────────── */
    async resolveMdexId() {
      if (this.mdex.id) return this.mdex.id
      const title = this.current?.name
      if (!title) return null
      try {
        const results = await api.get('/api/mangadex/search?q=' + encodeURIComponent(title))
        if (!Array.isArray(results) || !results.length) return null
        const exact = results.find(r => canonicalTitle(r.title) === canonicalTitle(title))
        if (!exact) return null
        this.mdex.id = exact.id; this.mdex.mdManga = exact
        return exact.id
      } catch (_) { return null }
    },
    async searchMdexForTomo() {
      const q = this.mdex.search.trim(); if (!q) return
      this.mdex.searching = true; this.mdex.results = []
      try { const r = await api.get('/api/mangadex/search?q=' + encodeURIComponent(q)); this.mdex.results = Array.isArray(r) ? r.slice(0, 8) : [] }
      catch (_) {} finally { this.mdex.searching = false }
    },
    async selectMdexEntry(entry) {
      this.mdex.id = entry.id; this.mdex.mdManga = entry; this.mdex.results = []; this.mdex.search = ''
      this.mdex.volumes = []; this.mdex.covers = []
      await this.loadMdexVolumes(); this.loadMdexCovers()
    },
    async loadMdexVolumes() {
      const id = await this.resolveMdexId()
      if (!id) { useUiStore().toast('No se encontró este manga en MangaDex', 'warn'); return }
      this.mdex.volumesLoading = true; this.mdex.volumes = []
      try {
        const [vol, cov] = await Promise.all([
          api.get(`/api/mangadex/volumes/${id}`).catch(() => []),
          api.get(`/api/mangadex/covers/${id}`).catch(() => []),
        ])
        let volumes = Array.isArray(vol) ? vol : []
        const covers = Array.isArray(cov) ? cov : []
        const apiVolCount = volumes.filter(v => v.chapters && v.chapters.length).length
        const feedFallback = volumes.some(v => v.feedFallback)
        if (apiVolCount === 0 || feedFallback) {
          try { const s = await api.get(`/api/mangadex/scrape_volumes/${id}`); if (Array.isArray(s) && s.length && !s.error) volumes = s } catch (_) {}
        }
        const known = new Set(volumes.map(v => v.volume))
        for (const v of [...new Set(covers.map(c => c.volume).filter(x => x && x !== 'none'))])
          if (!known.has(v)) volumes.push({ volume: v, label: `Tomo ${v}`, chapters: null, count: 0, noChapterData: true })
        volumes.sort((a, b) => { const na = parseFloat(a.volume), nb = parseFloat(b.volume); if (!isNaN(na) && !isNaN(nb)) return na - nb; return isNaN(na) ? 1 : -1 })
        this.mdex.volumes = volumes
      } catch (_) {}
      finally { this.mdex.volumesLoading = false }
    },
    // Returns { selected: [chapterNorms], label } — faithful to the original applyMdexVolume.
    applyMdexVolume(vol) {
      const localChaps = this.chapters
        .map(g => ({ norm: String(g.chapter), num: parseFloat(g.chapter) }))
        .filter(g => !isNaN(g.num))
      const vols = this.mdex.volumes
      let selected
      if (vol.noChapterData || !vol.chapters || !vol.chapters.length) {
        const volNum = parseFloat(vol.volume)
        const known = vols.filter(v => !v.noChapterData && v.chapters && v.chapters.length)
        if (known.length === 0) {
          const coverVols = vols.filter(v => !isNaN(parseFloat(v.volume))).sort((a, b) => parseFloat(a.volume) - parseFloat(b.volume))
          const idx = coverVols.findIndex(v => v.volume === vol.volume)
          const count = coverVols.length
          const sorted = [...localChaps].sort((a, b) => a.num - b.num)
          const mainChaps = sorted.filter(g => Number.isInteger(g.num))
          const bonusChaps = sorted.filter(g => !Number.isInteger(g.num))
          const base = Math.floor(mainChaps.length / count)
          const slices = []
          for (let i = 0; i < count; i++) { const start = i * base; const end = (i === count - 1) ? mainChaps.length : start + base; slices.push(mainChaps.slice(start, end).map(g => g.norm)) }
          for (const bonus of bonusChaps) { const parent = Math.floor(bonus.num); const target = slices.findIndex(s => s.some(n => parseFloat(n) === parent)); (target >= 0 ? slices[target] : slices[slices.length - 1]).push(bonus.norm) }
          selected = slices[idx] || []
        } else {
          const prev = [...known].sort((a, b) => parseFloat(b.volume) - parseFloat(a.volume)).find(v => parseFloat(v.volume) < volNum)
          const next = [...known].sort((a, b) => parseFloat(a.volume) - parseFloat(b.volume)).find(v => parseFloat(v.volume) > volNum)
          const prevMax = prev ? Math.max(...prev.chapters.map(Number).filter(n => !isNaN(n) && n >= 1)) : 0
          const nn = next ? next.chapters.map(Number).filter(n => !isNaN(n) && n >= 1) : []
          const nextMin = nn.length ? Math.min(...nn) : Infinity
          const prevDataNum = prev ? parseFloat(prev.volume) : -Infinity
          const nextDataNum = next ? parseFloat(next.volume) : Infinity
          const subGapVols = vols.filter(v => { const vn = parseFloat(v.volume); return !isNaN(vn) && vn > prevDataNum && vn < nextDataNum && (v.noChapterData || !v.chapters || !v.chapters.length) }).sort((a, b) => parseFloat(a.volume) - parseFloat(b.volume))
          const gapChaps = [...localChaps].filter(g => g.num > prevMax && g.num < nextMin).sort((a, b) => a.num - b.num)
          if (subGapVols.length <= 1) selected = gapChaps.map(g => g.norm)
          else { const idx = subGapVols.findIndex(v => v.volume === vol.volume); const chunk = Math.ceil(gapChaps.length / subGapVols.length); selected = gapChaps.slice(idx * chunk, idx * chunk + chunk).map(g => g.norm) }
        }
      } else {
        const nums = vol.chapters.map(Number).filter(n => !isNaN(n))
        const regular = nums.filter(n => n >= 1)
        const anchor = regular.length ? regular : nums
        const minCh = Math.min(...anchor), maxCh = Math.max(...anchor)
        const inRange = localChaps.filter(g => g.num >= minCh && g.num <= maxCh)
        if (inRange.length > 0) selected = inRange.map(g => g.norm)
        else if (!vol.fromScrape) selected = vol.chapters.map(c => String(c)).filter(c => c !== '')
      }
      const label = vol.label || `Tomo ${vol.volume}`
      const ui = useUiStore()
      if (!selected || !selected.length) { ui.toast(`${label}: ningún capítulo en este rango`, 'warn'); return { selected: [], label } }
      const total = vol.chapters ? vol.chapters.length : 0
      const missing = total > 0 && total > selected.length ? total - selected.length : 0
      ui.toast(missing > 0 ? `${selected.length}/${total} caps — ${label} (faltan ${missing})` : `${selected.length} caps — ${label}`, missing > 0 ? 'warn' : 'ok')
      return { selected, label }
    },
    async loadMdexCovers() {
      const id = await this.resolveMdexId(); if (!id) return
      this.mdex.coversLoading = true; this.mdex.covers = []
      try { this.mdex.covers = await api.get(`/api/mangadex/covers/${id}`) || [] } catch (_) {}
      finally { this.mdex.coversLoading = false }
    },
    async selectMdexCover(cover) {
      this.mdex.selectedCover = cover; this.mdex.coverLoadingId = cover.id; this.mdex.coverB64 = ''; this.mdex.coverUrl = cover.url
      try {
        const d = await api.post('/api/mangadex/cover_b64', { url: cover.url })
        if (d?.b64) { this.mdex.coverB64 = d.b64; this.mdex.coverUrl = d.data_url || cover.url }
      } catch (_) {}
      finally { this.mdex.coverLoadingId = null }
    },
    resetMdex() { this.mdex = { id: null, mdManga: null, search: '', results: [], searching: false, volumes: [], volumesLoading: false, covers: [], coversLoading: false, selectedCover: null, coverLoadingId: null, coverB64: '', coverUrl: '' } },

    /* ── Reader ─────────────────────────────────────────────────────────── */
    _resetView() { this.zoom = 1.0; this.panX = 0; this.panY = 0; this.compareMode = false; this.barsHidden = false; this.scanCompareMode = false; this.comparePages2 = [] },

    /* ── Compare scanlations (downloaded variants of the same chapter) ───── */
    async openComparePanel(chapter) {
      if (this.scanCmp.chapter === chapter && this.scanCmp.open) { this.scanCmp.open = false; return }
      this.scanCmp = { open: true, chapter, variants: [], loading: true }
      try { this.scanCmp.variants = await api.get(`/api/library/${encodeURIComponent(this.current.id)}/compare_variants/${chapter}`) || [] }
      catch (_) {} finally { this.scanCmp.loading = false }
    },
    async downloadCompareVariant(variant, chapter) {
      try {
        await api.post('/api/download/download_compare', {
          chapterId: variant.id, title: this.current.id, chapter,
          group: variant.groups?.[0] || 'unknown', lang: variant.language,
        })
        useUiStore().toast('Descargando variante para comparar…', 'info')
        setTimeout(() => this.openComparePanel(chapter), 8000)
      } catch (_) { useUiStore().toast('No se pudo descargar la variante', 'error') }
    },
    async readCompareSources(chapter, compareDir) {
      this._resetView()
      this.reader = { title: this.current.id, chapter, source: 'original', kind: 'manga' }
      this.readerLoading = true; this.pages = []; this.page = 0
      try {
        const [d1, d2] = await Promise.all([
          api.post('/api/reader/read_chapter', { title: this.current.id, chapter, source: 'original' }),
          api.post('/api/reader/read_compare', { title: this.current.id, chapter, compare_dir: compareDir }),
        ])
        this.pages = d1.pages || []
        this.comparePages2 = d2.pages || []
        this.compareMode = true
        this.scanCompareMode = true
      } catch (_) { useUiStore().toast('No se pudo cargar la comparación', 'error'); this.reader = null }
      finally { this.readerLoading = false }
    },

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
