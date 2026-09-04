import { defineStore } from 'pinia'
import { api } from '@/lib/api'
import { useUiStore } from './ui'

// Taller: importar CBZ/CBR propios a la biblioteca (ver import_cbz.py).
// Flujo: analyze (sube + detecta capítulos, NO escribe) → el usuario confirma/ajusta
// los cortes → commit (escribe el split). Una vez importado es un manga normal:
// Traducir/Upscalear se hacen desde el MangaModal de siempre.
const METHOD_LABEL = {
  filenames: 'por nombres de archivo',
  comicinfo: 'ComicInfo.xml',
  anchored: 'anclado por contenido (dHash)',
  flat: 'archivo plano (sin estructura)',
}

export const useWorkshopStore = defineStore('workshop', {
  state: () => ({
    items: [],
    loading: false,
    loadError: '',
    analyzing: false,
    committing: false,
    // staged = { token, filename, title, method, count, cover, chapters:[{chapter,start,count}], busy }
    staged: null,
  }),
  getters: {
    methodLabel: (s) => METHOD_LABEL[s.staged?.method] || s.staged?.method || '',
    // primera página de "cuerpo" (la portada, si es la página 0, no entra en capítulos)
    bodyStart: (s) => (s.staged && s.staged.cover === 0 ? 1 : 0),
    totalChapterPages: (s) => (s.staged?.chapters || []).reduce((a, c) => a + (c.count || 0), 0),
  },
  actions: {
    async loadList() {
      this.loading = true
      this.loadError = ''
      try {
        this.items = await api.get('/api/import/list')
      } catch (e) {
        this.loadError = e?.body || e?.message || 'No se pudo cargar el Taller.'
        useUiStore().toast('No se pudo cargar el Taller', 'error')
      } finally {
        this.loading = false
      }
    },

    thumbUrl(idx) {
      return this.staged ? `/api/import/thumb?token=${this.staged.token}&idx=${idx}` : ''
    },

    // Sube UN archivo y obtiene la división detectada (sin escribir nada todavía).
    async analyze(file) {
      this.analyzing = true
      try {
        const fd = new FormData()
        fd.append('file', file)
        const d = await api.upload('/api/import/analyze', fd)
        this.staged = {
          token: d.token, filename: d.filename, title: d.suggestedTitle || '',
          method: d.method, count: d.count, cover: d.cover,
          chapters: (d.chapters || []).map(c => ({ ...c })), busy: false,
        }
      } catch (e) {
        let m = 'No se pudo analizar el archivo'
        try { m = JSON.parse(e.body)?.error || m } catch {}
        useUiStore().toast(m, 'error')
      } finally {
        this.analyzing = false
      }
    },

    cancelStaged() { this.staged = null },

    // Recalcula los counts (cada capítulo llena hasta el inicio del siguiente / fin).
    _rebuild(starts) {
      const n = this.staged.count
      const arr = [...starts].sort((a, b) => a.start - b.start)
      arr.forEach((c, i) => { c.count = (i + 1 < arr.length ? arr[i + 1].start : n) - c.start })
      this.staged.chapters = arr
    },

    setChapterNum(i, val) {
      if (this.staged?.chapters[i]) this.staged.chapters[i].chapter = String(val).trim()
    },

    // Renumera los capítulos secuencialmente desde `base` (útil tras anclar o cortar a mano).
    renumberFrom(base) {
      const b = parseFloat(base)
      if (isNaN(b)) return
      this.staged.chapters.forEach((c, i) => { c.chapter = String(Number.isInteger(b + i) ? b + i : (b + i)) })
    },

    // Marca/desmarca una página como INICIO de capítulo (corte manual = nivel N4).
    toggleCut(pageIdx) {
      if (!this.staged) return
      const chs = this.staged.chapters
      const existing = chs.find(c => c.start === pageIdx)
      if (existing) {
        if (chs.length <= 1 || pageIdx === Math.min(...chs.map(c => c.start))) return  // no quitar el 1er corte
        this._rebuild(chs.filter(c => c.start !== pageIdx))
      } else {
        if (pageIdx < this.bodyStart) return
        this._rebuild([...chs, { start: pageIdx, chapter: '?' }])
      }
      const base = parseFloat(chs[0]?.chapter)
      if (!isNaN(base)) this.renumberFrom(base)
    },

    setCover(idx) {
      if (!this.staged) return
      this.staged.cover = (this.staged.cover === idx ? null : idx)
    },

    // N3 — anclado por contenido (para archivos planos): ubica la 1ª página de cada
    // capítulo (de una fuente) dentro del archivo por dHash.
    async anchor(startChapter, anilistId) {
      if (!this.staged) return
      this.staged.busy = true
      try {
        const d = await api.post('/api/import/anchor', {
          token: this.staged.token, title: this.staged.title,
          startChapter, anilistId: anilistId || null,
        })
        if (d.chapters?.length) {
          this.staged.method = d.method; this.staged.cover = d.cover
          this.staged.chapters = d.chapters.map(c => ({ ...c }))
          useUiStore().toast(`Detectados ${d.chapters.length} capítulo(s) por contenido`, 'success')
        } else if (d.error) {
          useUiStore().toast(d.error, 'info')
        }
      } catch (e) {
        useUiStore().toast('No se pudo auto-detectar por contenido', 'error')
      } finally {
        this.staged.busy = false
      }
    },

    async commit() {
      if (!this.staged) return
      if (!this.staged.title.trim()) { useUiStore().toast('Ponle un título a la serie', 'error'); return }
      this.committing = true
      try {
        const d = await api.post('/api/import/commit', {
          token: this.staged.token, title: this.staged.title.trim(),
          cover: this.staged.cover, chapters: this.staged.chapters,
        })
        useUiStore().toast(`"${d.title}" importado (${d.chapter_count} cap.)`, 'success')
        this.staged = null
        await this.loadList()
      } catch (e) {
        let m = 'Error al importar'
        try { m = JSON.parse(e.body)?.error || m } catch {}
        useUiStore().toast(m, 'error')
      } finally {
        this.committing = false
      }
    },
  },
})
