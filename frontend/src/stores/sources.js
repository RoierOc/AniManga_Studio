import { defineStore } from 'pinia'
import { api } from '@/lib/api'
import { useUiStore } from './ui'

export const useSourcesStore = defineStore('sources', {
  state: () => ({
    online: false,
    checked: false,
    sources: [],                // [{id, name, lang, iconUrl, isNsfw, supportsLatest}]
    activeSource: '',           // '' = all sources

    query: '',
    results: [],                // flat manga results [{id,title,thumbnailUrl,inLibrary,sourceId,sourceName,sourceLang}]
    searching: false,
    progress: { done: 0, total: 0 },
    _es: null,

    detail: null,               // {id, title, description, thumbnailUrl, sourceId, sourceName, sourceLang, ...}
    chapters: [],
    detailLoading: false,
    downloading: {},            // chapterId -> true
  }),

  actions: {
    async checkHealth() {
      try { this.online = !!(await api.get('/api/sources/health'))?.online }
      catch (_) { this.online = false }
      finally { this.checked = true }
      if (this.online && !this.sources.length) this.loadSources()
    },
    async loadSources() {
      try { this.sources = await api.get('/api/sources/list') || [] } catch (_) {}
    },

    /* Global search via SSE stream (progressive results across all sources). */
    search() {
      const q = this.query.trim()
      if (q.length < 2) return
      this._stopStream()
      this.results = []
      this.searching = true
      this.progress = { done: 0, total: 0 }

      if (this.activeSource) return this._searchOne(q)

      try {
        const es = new EventSource(`/api/sources/search_all_stream?q=${encodeURIComponent(q)}`)
        this._es = es
        es.onmessage = (e) => {
          let d; try { d = JSON.parse(e.data) } catch { return }
          if (d.type === 'start') this.progress.total = d.total
          else if (d.type === 'result' && d.manga) this.results.push(d.manga)
          else if (d.type === 'progress') this.progress = { done: d.done, total: d.total }
          else if (d.type === 'done') this._stopStream()
        }
        es.onerror = () => this._stopStream()
      } catch (_) { this.searching = false }
    },
    async _searchOne(q) {
      try {
        const d = await api.get(`/api/sources/search?source=${encodeURIComponent(this.activeSource)}&q=${encodeURIComponent(q)}&page=1`)
        const src = this.sources.find(s => s.id === this.activeSource)
        this.results = (d.results || []).map(m => ({ ...m, sourceId: this.activeSource, sourceName: src?.name, sourceLang: src?.lang }))
      } catch (_) { useUiStore().toast('Error buscando en la fuente', 'error') }
      finally { this.searching = false }
    },
    _stopStream() {
      if (this._es) { this._es.close(); this._es = null }
      this.searching = false
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
        if (meta) this.detail = { ...m, ...meta, sourceId: m.sourceId, sourceName: m.sourceName, sourceLang: m.sourceLang }
        this.chapters = chs || []
      } catch (_) {}
      finally { this.detailLoading = false }
    },
    closeDetail() { this.detail = null },

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
