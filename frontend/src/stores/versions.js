import { defineStore } from 'pinia'
import { api } from '@/lib/api'
import { useUiStore } from './ui'

// "Fuentes por capítulo": dominio NUEVO, separado de `stores/manga.js` (namespace `ver`,
// que sigue intacto y solo hace el ranking de calidad de solo-lectura). Este store gestiona
// la cobertura de capítulos por fuente, el mapa de asignación por capítulo (chapter_sources
// en el backend) y las acciones de asignar/completar/revisar actualizaciones. No duplica
// `current`/capítulos locales de manga.js — los consulta cuando hace falta.

const POLL_MS = 1200

function emptyState() {
  return {
    title: '',
    currentLang: '',   // igual que Versiones para el mismo título — mismo key de caché "versions"
    loading: false,
    phase: '',
    progress: {},
    totalKnownChapters: 0,
    sources: [],       // [{sourceKind,sourceId,mangaId,sourceName,sourceLang,chapters[],count,completeness,quality,updateFrequency}]
    assigned: {},       // {chapterNorm: {sourceKind,sourceId,mangaId,sourceName,sourceLang,assignedAt,assignedBy}} — ÚNICA fuente de verdad de "qué capítulos tiene este manga y de dónde"
    _poll: null,
    freshness: { loading: false, suggestions: [] },
  }
}

export const useVersionsStore = defineStore('versions', {
  state: () => emptyState(),

  getters: {
    // Fuente resuelta para UN capítulo concreto: asignación explícita (chapter_sources) si
    // existe; si no, null (el llamador cae al fallback legado de manga.js::effectiveSource).
    sourceForChapter: (s) => (chapterNorm) => s.assigned[String(chapterNorm)] || null,
    // Fuentes que tienen un capítulo dado (para el selector de reasignación puntual) — solo
    // disponible si se cargó la cobertura completa (`fetchCoverage`), no con el assigned-map liviano.
    sourcesWithChapter: (s) => (chapterNorm) =>
      s.sources.filter(src => src.chapters.includes(String(chapterNorm))),
    hasAssignments: (s) => Object.keys(s.assigned).length > 0,
  },

  actions: {
    reset() {
      if (this._poll) clearInterval(this._poll)
      Object.assign(this, emptyState())
    },

    // Reset de CICLO DE VIDA al abrir un manga (lo llama SOLO manga.js::open). Limpia lo
    // transitorio de la sesión de Cobertura (sources/catálogo, freshness, phase, poll) y fija
    // `title` de inmediato, pero PRESERVA el hueco de `assigned` para que el `loadAssignedMap`
    // que viene justo después no compita con un `reset()` que lo borraría (era la causa de que
    // la selección persistida no se viera: el watch del modal reseteaba y dejaba `title` vacío).
    resetForManga(title) {
      if (this._poll) { clearInterval(this._poll); this._poll = null }
      const fresh = emptyState()
      // conserva la asignación actual solo si es del MISMO manga; si cambia de manga, límpiala
      const keepAssigned = this.title === title ? this.assigned : {}
      Object.assign(this, fresh, { title: title || '', assigned: keepAssigned })
    },

    // Lectura LIVIANA (sin polling: una consulta SQLite instantánea) de la asignación
    // persistida — se llama SIEMPRE que se abre un manga (manga.js::open) para que la
    // selección manual del usuario se vea de inmediato sin tener que pulsar "Ver cobertura"
    // cada vez. NO trae `sources`/catálogo completo (eso sigue siendo cosa de fetchCoverage).
    async loadAssignedMap(title) {
      if (!title) return
      this.title = title
      try {
        const d = await api.get(`/api/transplant/assigned_map?title=${encodeURIComponent(title)}`)
        this.assigned = d.assigned || {}
      } catch (_) { /* silencioso: no bloquea la apertura del manga */ }
    },

    // Lectura INSTANTÁNEA del último resultado de cobertura persistido en disco (sin
    // recalcular): se llama al abrir el manga para que el grid aparezca de una vez sin volver
    // a barrer ~185 fuentes. Si hay caché, deja `phase='ready'` y `sources` poblado; si no,
    // no toca nada (queda el estado vacío → la UI muestra "Ver cobertura"). El usuario solo
    // recalcula con "Recalcular" (fetchCoverage refresh=true).
    async loadCoverageCached(title, currentLang = '') {
      if (!title) return
      this.title = title
      this.currentLang = currentLang
      try {
        const d = await api.get(`/api/transplant/coverage_cached?title=${encodeURIComponent(title)}&currentLang=${encodeURIComponent(currentLang)}`)
        if (d.assigned) this.assigned = d.assigned
        if (d.cached) {
          this.sources = d.sources || []
          this.totalKnownChapters = d.totalKnownChapters || 0
          this.phase = 'ready'
        }
      } catch (_) { /* silencioso: no bloquea la apertura del manga */ }
    },

    // `currentLang` DEBE coincidir con lo que usa Versiones para el mismo título (misma
    // caché "versions" en el backend) — así Cobertura ve EXACTAMENTE el mismo conjunto de
    // fuentes ya vetadas por Versiones, nunca un descubrimiento propio/distinto.
    async fetchCoverage(title, anilistId, refresh = false, currentLang = '') {
      const ui = useUiStore()
      if (!title) return
      this.title = title
      this.currentLang = currentLang
      this.loading = true
      this.phase = 'start'
      this.progress = {}
      try {
        const res = await api.post('/api/transplant/coverage', { title, anilistId: anilistId || null, currentLang, refresh })
        this._pollCoverage(res.task_id)
      } catch (e) {
        this.loading = false
        this.phase = 'error'
        ui.toast(e?.status === 503 ? 'Suwayomi offline' : 'Falló el cálculo de cobertura', 'error')
      }
    },

    _pollCoverage(taskId) {
      if (this._poll) clearInterval(this._poll)
      this._poll = setInterval(async () => {
        try {
          const st = await api.get(`/api/transplant/status?task_id=${encodeURIComponent(taskId)}`)
          this.phase = st.phase || this.phase
          this.progress = { covered: st.covered, coverTotal: st.coverTotal, searched: st.searched, searchTotal: st.searchTotal }
          // Ranking PROGRESIVO: el backend emite snapshots parciales ya ordenados durante la
          // fase de cobertura; los pintamos en vivo para que el grid se vaya poblando y ordenando
          // sin esperar al barrido completo (percepción de rendimiento). El 'done' final manda.
          if (st.status !== 'done' && Array.isArray(st.sources)) {
            this.sources = st.sources
            if (st.totalKnownChapters != null) this.totalKnownChapters = st.totalKnownChapters
          }
          if (st.status === 'done') {
            clearInterval(this._poll); this._poll = null; this.loading = false
            this.totalKnownChapters = st.totalKnownChapters || 0
            this.sources = st.sources || []
            this.assigned = st.assigned || {}
            this.phase = 'ready'
          } else if (st.status === 'error') {
            clearInterval(this._poll); this._poll = null; this.loading = false; this.phase = 'error'
            useUiStore().toast('Falló el cálculo de cobertura', 'error')
          }
        } catch (_) {}
      }, POLL_MS)
    },

    // Asigna una fuente a un rango/lista de capítulos (o la quita con source=null).
    // range: 'all' | {from?, to?} ; chapters: [chapterNorm,...] (alternativa explícita).
    async assignSource({ range, chapters, source } = {}) {
      const ui = useUiStore()
      if (!this.title) return null
      try {
        const d = await api.post('/api/transplant/assign_source', { title: this.title, range, chapters, source })
        this.assigned = d.assignedMap || this.assigned
        const n = (d.assigned || []).length
        ui.toast(source
          ? `${n} capítulo(s) asignado(s) a "${source.sourceName || 'fuente'}"`
          : `${n} capítulo(s) sin fuente asignada`, 'ok')
        return d
      } catch (_) {
        ui.toast('No se pudo asignar la fuente', 'error')
        return null
      }
    },

    async checkFreshness(anilistId) {
      const ui = useUiStore()
      if (!this.title) return
      this.freshness = { loading: true, suggestions: [] }
      try {
        const res = await api.post('/api/transplant/check_freshness', { title: this.title, anilistId: anilistId || null, currentLang: this.currentLang })
        this._pollFreshness(res.task_id)
      } catch (e) {
        this.freshness = { loading: false, suggestions: [] }
        ui.toast(e?.status === 503 ? 'Suwayomi offline' : 'No se pudo revisar actualizaciones', 'error')
      }
    },

    _pollFreshness(taskId) {
      const poll = setInterval(async () => {
        try {
          const st = await api.get(`/api/transplant/status?task_id=${encodeURIComponent(taskId)}`)
          if (st.status === 'done') {
            clearInterval(poll)
            this.freshness = { loading: false, suggestions: st.suggestions || [] }
            if (!st.suggestions?.length) useUiStore().toast('Todo al día — no hay fuentes mejores disponibles', 'info')
          } else if (st.status === 'error') {
            clearInterval(poll)
            this.freshness = { loading: false, suggestions: [] }
            useUiStore().toast('No se pudo revisar actualizaciones', 'error')
          }
        } catch (_) {}
      }, POLL_MS)
    },

    // Aplica una sugerencia de check_freshness: asigna la fuente sugerida al capítulo y lo
    // descarga — SIEMPRE una acción explícita de UN capítulo que el usuario revisó y
    // aprobó con el clic en "Aplicar" (nunca un rastreo automático de "huecos").
    async applySuggestion(sug) {
      await this.assignSource({ chapters: [sug.chapter], source: sug.betterSource })
      await this.downloadChapterFrom(sug.chapter, sug.betterSource)
      this.freshness.suggestions = this.freshness.suggestions.filter(s => s.chapter !== sug.chapter)
    },

    // Descarga UN capítulo concreto desde CUALQUIER fuente candidata (no solo la fuente
    // única legada) — resuelve las páginas con el endpoint nuevo `chapter_urls` y reutiliza
    // el endpoint de descarga YA EXISTENTE (agnóstico de origen, ya enganchado al progreso
    // SSE que pinta el anillo en la pestaña Capítulos vía `_reconcileDownloads`).
    async downloadChapterFrom(chapterNorm, source, opts = {}) {
      const ui = useUiStore()
      if (!this.title || !source) return
      try {
        const d = await api.post('/api/transplant/chapter_urls', {
          title: this.title, chapter: chapterNorm,
          source: { sourceKind: source.sourceKind, sourceId: source.sourceId, mangaId: source.mangaId, sourceLang: source.sourceLang },
        })
        if (!d.urls?.length) { ui.toast('No se encontraron páginas para ese capítulo en esa fuente', 'error'); return }
        await api.post('/api/download/download_source_chapter', {
          title: this.title, chapter: chapterNorm, pageUrls: d.urls,
          sourceId: source.sourceId, mangaId: source.mangaId, sourceName: source.sourceName, sourceLang: source.sourceLang,
          ...(opts.chainUpscale ? {
            chain_upscale: true,
            chain_eco: opts.chainEco ?? true,
            chain_fast: opts.chainFast ?? false,
          } : {}),
        })
      } catch (e) {
        ui.toast(e?.status === 503 ? 'Suwayomi offline' : 'No se pudo descargar el capítulo', 'error')
      }
    },
  },
})
