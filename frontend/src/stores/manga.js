import { defineStore } from 'pinia'
import { api } from '@/lib/api'
import { onStatus } from '@/lib/sse'
import { useUiStore } from './ui'
import { taskId } from '@/lib/manga'

let statusBound = false
let previewTimer = null
let upDoneSeen = new Set()   // upscale ids already refreshed-for (bounded, pruned each SSE tick)
// Activity center bookkeeping (module-level so it survives store re-creation in HMR):
let firstStatusSeen = false  // primed on the first SSE snapshot so we never replay OLD terminal toasts
let tpTermSeen = new Set()   // transplant task ids whose terminal side-effects (toasts/refresh) already fired
let exportTermSeen = new Set() // export ids whose completion auto-download already fired (bounded, pruned each tick)

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

/* ── Modelo unificado de tareas para el Centro de Actividad ─────────────────────
   Aplana CADA entrada de estado del backend (download/upscale/export/transplant) a una
   forma común que el indicador de la TopBar, el drawer, la vista Actividad y el progreso
   contextual del modal consumen por igual — una sola fuente de verdad. */
const _TASK_TERMINAL = ['done', 'complete', 'cancelled', 'error', 'interrupted']

function _pct(progress, total, fallback = 0) {
  if (total) return Math.min(100, Math.round((progress || 0) / total * 100))
  return fallback
}
// Backend status string → estado unificado del centro.
function _mapStatus(raw) {
  if (raw === 'error') return 'error'
  if (raw === 'cancelled' || raw === 'interrupted') return 'cancelled'
  if (raw === 'done' || raw === 'complete') return 'done'
  if (raw === 'starting') return 'queued'
  return 'running'
}
// One backend task entry → unified shape, or null to hide it.
function normalizeTask(kind, id, v) {
  if (!v || typeof v !== 'object') return null
  const status = _mapStatus(v.status)
  const _ts = v.ended_at || v._ts   // epoch seconds; ended_at is stamped once on terminal
  const base = { id, kind, status, ts: _ts ? _ts * 1000 : Date.now(), error: v.error || '', msg: v.message || '' }
  if (kind === 'download')
    return { ...base, mangaId: v.title || '', title: v.title || '', chapter: v.chapter,
      pct: _pct(v.progress, v.total), label: v.chapter != null ? `Cap. ${v.chapter}` : 'Descarga' }
  if (kind === 'upscale')
    return { ...base, mangaId: v.title || '', title: v.title || '', chapter: v.chapter,
      pct: _pct(v.progress ?? v.current, v.total), label: v.chapter != null ? `Cap. ${v.chapter}` : 'Escalar todo' }
  if (kind === 'export')
    return { ...base, mangaId: v.title || v.volume_name || '', title: v.title || v.volume_name || '',
      pct: status === 'done' ? 100 : _pct(v.progress, v.total), label: v.volume_name || 'Tomo', file: status === 'done' }
  if (kind === 'translate') {
    // `chapterDone` is the 1-based chapter being WORKED ON (not the count completed), so a
    // single-chapter job would otherwise pin the bar at 100%. Make it page-aware: chapters
    // fully done + the current chapter's page fraction → a smooth bar that mirrors the modal's
    // "pág d/t" detail. `pageDone/pageTotal` only flow during the compose phase.
    const total = v.chapterTotal || 0
    const completed = Math.max(0, (v.chapterDone || 0) - 1)
    const frac = (v.phase === 'compose' && v.pageTotal) ? (v.pageDone || 0) / v.pageTotal : 0
    let pct = total ? Math.min(100, Math.round((completed + frac) / total * 100)) : 0
    if (status === 'done') pct = 100
    let label
    if (v.phase === 'compose' && v.pageTotal) label = `Cap. ${v.chapter} · pág ${v.pageDone || 0}/${v.pageTotal}`
    else if (v.phase === 'download') label = `Cap. ${v.chapter} · descargando`
    else if (v.phase === 'start' || !total) label = 'Preparando…'
    else label = `Cap. ${Math.min(v.chapterDone || 0, total)}/${total}`
    return { ...base, mangaId: v.title || '', title: v.title || '', chapter: v.chapter, pct, label }
  }
  if (kind === 'versiondl')   // descarga de versión: visualmente una descarga
    return { ...base, kind: 'download', mangaId: v.title || '', title: v.title || '',
      pct: _pct(v.chapterDone, v.chapterTotal), label: v.chapterTotal ? `Versión · ${v.chapterDone || 0}/${v.chapterTotal} cap.` : 'Descarga de versión' }
  return null
}
// Agrupa una lista de tareas normalizadas por manga, con portada (si se conoce) y % medio.
function groupByManga(tasks, coverCache) {
  const map = new Map()
  for (const t of tasks) {
    const key = t.mangaId || t.title || t.id
    if (!map.has(key)) map.set(key, { mangaId: key, title: t.title || key, cover: coverCache[key] || '', tasks: [], pct: 0, anyError: false, anyActive: false })
    map.get(key).tasks.push(t)
  }
  for (const g of map.values()) {
    g.pct = Math.round(g.tasks.reduce((a, t) => a + (t.pct || 0), 0) / g.tasks.length)
    g.anyError = g.tasks.some(t => t.status === 'error')
    g.anyActive = g.tasks.some(t => t.status === 'running' || t.status === 'queued')
  }
  return [...map.values()]
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
    dismissedExports: [],     // export task ids dismissed from the queue
    libraryDirty: 0,          // bumped after a manga is deleted so LibraryView reloads
    pendingDelete: [],        // manga ids hidden during the undo window before deletion

    // Traducción por trasplante (pestaña "Traducir" del modal), scoped al manga actual
    tp: {
      loading: false, phase: '', variants: [], discoverProgress: {},
      artCands: [], esCands: [], artSel: null, esSel: null,
      chapters: [], chaptersLoading: false,
      preview: {},   // chapterNorm -> { open, loading, pages: [] }
      running: false, runStatus: null, taskId: null,
      _dpoll: null, _rpoll: null,
      pendingVolumes: [], unresolvedVolumes: [],   // tomos importados sin repartir en capítulos reales
      volResolving: false,
    },

    // Versiones (pestaña "Versiones" del modal): ranking de calidad de imagen de TODAS
    // las versiones del título en las fuentes, agrupadas por idioma. Solo lectura — no
    // toca archivos ni metadata. Reusa el mismo motor discover/rank que `tp`.
    ver: {
      loading: false, phase: '', discoverProgress: {},
      versions: [],            // [{ sourceId, mangaId, sourceName, sourceLang, match, quality }]
      byLang: {},              // { lang: [version,...] } cada grupo desc por score
      local: null,             // { height, sharpness, score, ... } score de la versión local (baseline)
      currentLang: '',         // idioma de la versión local del usuario
      langFilter: '',          // '' = todos los idiomas
      sample: { open: false, key: null, loading: false, pages: [], name: '' },
      cmpSel: [],              // hasta 2 candidatos elegidos para comparar A|B
      dl: null,                // { running, done, total, name } descarga de versión en curso
      _poll: null, _dlpoll: null,
    },

    // live task status (from SSE aggregated payload)
    downloads: {},
    upscale: {},
    exports: {},
    transplant: {},           // { taskId: {status, phase, chapterDone, chapterTotal, ...} } — traducción + descarga de versión

    // Centro de Actividad: historial DERIVADO del mismo snapshot (rebanada terminal). No se
    // graba en el cliente — activas e historial son la misma fuente de verdad y sobreviven F5.
    coverCache: {},           // { mangaId: coverUrl } poblado al abrir/listar mangas, para las tarjetas del centro
    hiddenHistoryIds: (() => { try { return JSON.parse(localStorage.getItem('act-hidden') || '[]') } catch { return [] } })(),

    // reader
    reader: null,             // { title, chapter, source, kind } | null  (kind: manga | cbz)
    pages: [],
    page: 0,
    readerLoading: false,
    onlineLoadingId: null,    // chapter id currently resolving pages for "Leer" (online), for button spinners
    mode: localStorage.getItem('reader-mode') || 'paged',   // paged | webtoon
    fit: localStorage.getItem('reader-fit') || 'width',     // width | height | original
    dir: localStorage.getItem('reader-dir') || 'rtl',       // rtl | ltr
    spread: localStorage.getItem('reader-spread') === '1',  // two-page spread (paged manga only)
    zoom: 1.0,
    panX: 0,
    panY: 0,
    barsHidden: false,
    compareMode: false,
    compareX: 50,
    // scanlation comparison (different downloaded groups of the same chapter)
    scanCompareMode: false,
    comparePages2: [],
    compareLabels: null,        // { left, right } — etiquetas A|B en el comparador (versiones)
    scanCmp: { open: false, chapter: null, variants: [], loading: false },
    progress: (() => { try { return JSON.parse(localStorage.getItem('manga-progress-v1') || '{}') } catch { return {} } })(),

    // chapter updates (followed manga with new chapters on MangaDex)
    updates: [],                // [{manga_id, title, cover, new_count, new_chapters}]
    updatesLoaded: false,

    // reading history (server-persisted, recorded when a chapter is marked read)
    history: [],
    historyLoaded: false,

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
      id: null, mdManga: null, approx: false,
      search: '', results: [], searching: false,
      volumes: [], volumesLoading: false,
      covers: [], coversLoading: false,
      selectedCover: null, coverLoadingId: null, coverB64: '', coverUrl: '',
    },

    // Selector de portada (cambiar la del manga si está corrupta/baja calidad)
    coverPicker: { open: false, loading: false, current: null, anilist: [], mangadex: [], applying: null },

    // upscale model + mode
    models: {},                 // { key: label }
    activeModel: 'eula',
    eco: localStorage.getItem('upscale-eco') !== '0',   // eco on by default (lets MPV run)

    // Modo QA de traducción (SOLO testing): conserva artefactos de debug por página al traducir
    // y habilita el botón ⚑ en el lector para marcar páginas mal traducidas. Off por defecto.
    qaMode: localStorage.getItem('tp-qa') === '1',
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
      // Only truly in-flight downloads suppress the local chapter (positive check avoids
      // stale/failed statuses like 'no_chapters' or 'starting' from a crashed session from
      // incorrectly hiding local chapters and causing source+MD duplicates).
      const _IN_FLIGHT = ['downloading', 'started', 'starting']
      const curId = s.current?.id
      const activeDownloading = new Set(
        Object.entries(s.dlTasks).filter(([, tid]) => {
          const st = s.downloads[tid]
          return st && st.title === curId && _IN_FLIGHT.includes(st.status)
        }).map(([ch]) => ch)
      )
      const localChapters = s.chapters.filter(c => !activeDownloading.has(String(c.chapter)))
      // seenNorms tracks what's already in result; prevents a chapter from appearing via both
      // sourceChapters (Suwayomi) AND mdChapters (MangaDex) when dlNorms misses it.
      const seenNorms = new Set(localChapters.map(c => String(c.chapter)))
      const result = [...localChapters]
      for (const sc of s.sourceChapters) {
        if (!seenNorms.has(sc.chapterNorm)) {
          result.push({ chapter: sc.chapterNorm, page_count: sc.pageCount || 0, _sourceId: sc.id, _sourceName: sc.name, _scanlator: sc.scanlator })
          seenNorms.add(sc.chapterNorm)
        }
      }
      for (const mc of s.mdChapters) {
        if (s.mdLang && mc.language !== s.mdLang) continue
        const cNorm = String(mc.chapter)
        if (!seenNorms.has(cNorm)) {
          result.push({ chapter: cNorm, page_count: mc.pages || 0, _mdChapterId: mc.id, _mdGroup: (mc.groups || []).join(', '), _mdTitle: mc.title, _mdLang: mc.language })
          seenNorms.add(cNorm)
        }
      }
      return result.sort((a, b) => parseFloat(b.chapter) - parseFloat(a.chapter))
    },
    hasSourceMeta: (s) => !!(s.current?.source_meta?.sourceId && s.current?.source_meta?.mangaId) || !!s.mdId,
    // Fuente activa de capítulos: si hay una versión FIJADA como principal (recommended_source)
    // se descargan los capítulos de ESA fuente (la mejor según el criterio del usuario); si no,
    // la fuente de origen del manga. Alimenta la lista y la descarga de la pestaña Capítulos.
    effectiveSource: (s) => {
      const sm = s.current?.source_meta
      const rec = sm?.recommended_source
      // La versión FIJADA manda, sea de la fuente que sea: Suwayomi se lista/descarga vía
      // /api/sources, MangaDex vía /api/mangadex (mangaId = `uuid@@lang`). Una versión LOCAL
      // fijada es la que ya tienes = origen, así que no cambia la fuente de Capítulos.
      const kindOf = (id) => id === '__mangadex__' ? 'mangadex' : id === '__local__' ? 'local' : 'suwayomi'
      if (rec?.sourceId && rec?.mangaId) {
        const kind = kindOf(rec.sourceId)
        if (kind !== 'local')
          return { sourceId: rec.sourceId, mangaId: rec.mangaId, sourceName: rec.sourceName || '', sourceLang: rec.sourceLang || '', kind, pinned: true }
      }
      if (sm?.sourceId && sm?.mangaId) return { sourceId: sm.sourceId, mangaId: sm.mangaId, sourceName: sm.sourceName || '', sourceLang: sm.sourceLang || '', kind: 'suwayomi', pinned: false }
      return null
    },
    // Whether we have any external chapters to show (source, MD, or local)
    hasAnyChapters: (s) => s.mergedChapters.length > 0,

    updatesByTitle: (s) => { const m = {}; for (const u of s.updates) m[u.title] = u; return m },

    // chapter navigation within the reader (ascending order)
    chapterListAsc: (s) => [...s.chapters].sort((a, b) => (parseFloat(a.chapter) || 0) - (parseFloat(b.chapter) || 0)),
    chapterIndex() { return this.chapterListAsc.findIndex(c => String(c.chapter) === String(this.reader?.chapter)) },
    canPrevChapter() { return this.chapterIndex > 0 },
    canNextChapter() { return this.chapterIndex >= 0 && this.chapterIndex < this.chapterListAsc.length - 1 },

    // two-page spread is only meaningful for paged reading without compare/zoom overlap
    spreadActive: (s) => s.spread && s.mode === 'paged' && !s.compareMode,
    // the page(s) currently shown — one index, or a [left,right] pair in spread mode.
    // The pair is rendered right-to-left when dir === 'rtl' (manga order).
    spreadPair() {
      if (!this.spreadActive) return [this.page]
      const hasNext = this.page + 1 < this.pages.length
      return hasNext ? [this.page, this.page + 1] : [this.page]
    },

    // compare (original vs upscaled) — only paged manga, chapter has an upscale
    canCompare: (s) => s.reader?.kind === 'manga' && s.mode === 'paged' &&
      (s.reader?.source === 'upscaled' || s.upscaled[s.reader?.chapter] === true || s.upscaled[s.reader?.chapter] === 'partial'),
    // `http(s)` pass-through: las muestras de versiones (pestaña Versiones) son URLs
    // absolutas de Suwayomi, no rutas locales /uploads — no anteponer prefijo.
    pageOrigUrl: (s) => {
      const p = s.pages[s.page]; if (!p) return ''
      return (p.startsWith('/') || p.startsWith('http')) ? p : '/uploads/original/' + p
    },
    pageUpUrl: (s) => {
      if (s.scanCompareMode && s.comparePages2.length) {
        const p2 = s.comparePages2[Math.min(s.page, s.comparePages2.length - 1)]
        return p2 ? ((p2.startsWith('/') || p2.startsWith('http')) ? p2 : '/uploads/original/' + p2) : ''
      }
      const p = s.pages[s.page]; if (!p) return ''
      return (p.startsWith('/') || p.startsWith('http')) ? p : '/uploads/upscaled/' + p
    },

    /* ── Centro de Actividad: modelo unificado ──────────────────────────────
       normalizedTasks aplana las CUATRO fuentes de estado a una forma común. Todo lo
       demás (indicador TopBar, drawer, vista, historial) deriva de aquí → una sola fuente
       de verdad, sincronizada por reactividad de Pinia. */
    normalizedTasks: (s) => {
      const out = []
      for (const [id, v] of Object.entries(s.downloads)) {
        if (s.cancelledIds.includes(id)) continue
        const t = normalizeTask('download', id, v); if (t) out.push(t)
      }
      for (const [id, v] of Object.entries(s.upscale)) {
        if (s.cancelledIds.includes(id)) continue
        const t = normalizeTask('upscale', id, v); if (t) out.push(t)
      }
      for (const [id, v] of Object.entries(s.exports)) {
        if (s.dismissedExports.includes(id)) continue
        const t = normalizeTask('export', id, v); if (t) out.push(t)
      }
      for (const [id, v] of Object.entries(s.transplant)) {
        const kind = id.endsWith('_transplant_chdlversion') ? 'versiondl' : 'translate'
        const t = normalizeTask(kind, id, v); if (t) out.push(t)
      }
      return out
    },
    // Solo lo que está vivo ahora (cola + corriendo) — alimenta indicador, drawer y "Activas".
    liveTasks() { return this.normalizedTasks.filter(t => t.status === 'running' || t.status === 'queued') },
    activeCount() { return this.liveTasks.length },
    aggregatePct() {
      const t = this.liveTasks
      return t.length ? Math.round(t.reduce((a, x) => a + (x.pct || 0), 0) / t.length) : 0
    },
    // Historial = la rebanada TERMINAL del mismo snapshot, por recencia (sello ended_at),
    // acotada y sin las ocultadas por el usuario. Misma fuente que las activas → siempre
    // sincronizado y sobrevive a F5; sin grabación frágil en el cliente.
    historyTasks() {
      const hidden = new Set(this.hiddenHistoryIds)
      return this.normalizedTasks
        .filter(t => ['done', 'error', 'cancelled'].includes(t.status) && !hidden.has(t.id))
        .sort((a, b) => b.ts - a.ts)
        .slice(0, 80)
    },
    // Tarjetas agrupadas por manga (activas) y del historial.
    processingGroups() { return groupByManga(this.liveTasks, this.coverCache) },
    historyGroups() { return groupByManga(this.historyTasks, this.coverCache) },
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
        this.transplant = data.transplant || {}
        // Drop cancelled ids once the backend has actually stopped them (terminal
        // status or gone), so the set can't grow unbounded.
        if (this.cancelledIds.length) {
          this.cancelledIds = this.cancelledIds.filter(id => {
            const v = this.downloads[id] || this.upscale[id]
            return v && ['starting', 'started', 'upscaling', 'downloading'].includes(v.status)
          })
        }
        // Drop dismissed export ids once the backend no longer reports them.
        if (this.dismissedExports.length) {
          this.dismissedExports = this.dismissedExports.filter(id => id in this.exports)
        }
        // Clean up finished chapter tasks straight from the SSE snapshot — this replaces
        // the old 1-second polling loop (one extra request per active task per second),
        // since the stream already carries every download/upscale status every 500 ms.
        const TERMINAL = ['complete', 'done', 'cancelled', 'error']
        let refreshCh = false, refreshUp = false
        for (const [ch, tid] of Object.entries(this.dlTasks)) {
          const v = this.downloads[tid]
          if (v && TERMINAL.includes(v.status)) { delete this.dlTasks[ch]; refreshCh = true }
        }
        for (const [ch, tid] of Object.entries(this.upTasks)) {
          const v = this.upscale[tid]
          if (v && TERMINAL.includes(v.status)) { delete this.upTasks[ch]; refreshUp = true }
        }
        // Catch upscales for the open manga not tracked per-chapter (e.g. "escalar todo"),
        // refreshing only ONCE per newly-completed task (not every 500ms tick).
        if (this.current) {
          const prefix = taskId(this.current.id, '', 'upscale').slice(0, -3)
          for (const [k, v] of Object.entries(this.upscale)) {
            if (k.startsWith(prefix) && (v.status === 'done' || v.status === 'complete') && !upDoneSeen.has(k)) {
              upDoneSeen.add(k); refreshUp = true
            }
          }
          if (upDoneSeen.size) upDoneSeen = new Set([...upDoneSeen].filter(k => {
            const v = this.upscale[k]; return v && (v.status === 'done' || v.status === 'complete')
          }))
        }
        if (refreshCh) this._refreshChapters()
        if (refreshUp) this._refreshUpscaled()

        // On the FIRST snapshot this page-load, mark everything already terminal as "seen" so we
        // don't replay old TOASTS from a previous session (the history itself is derived, not grabbed).
        if (!firstStatusSeen) {
          for (const t of this.normalizedTasks)
            if (['done', 'error', 'cancelled'].includes(t.status)) tpTermSeen.add(t.id)
          // Exports already complete on first load (e.g. a finished export from before an F5)
          // must NOT auto-download — only newly-completed ones should. Seed them as seen.
          for (const id of Object.keys(this.exports)) exportTermSeen.add(id)
          firstStatusSeen = true
        }
        // Translation / version-download flow through the SSE snapshot (no polling): bind the open
        // manga's modal to its live task and fire terminal side-effects (toasts/refresh) once.
        this._reconcileTransplant()
        // Auto-download finished tomos the moment they complete — globally, so the file is saved
        // even if the user already left the export view.
        this._reconcileExports()
        // Rebuild dlTasks for any active chapter downloads of the open manga that aren't
        // tracked yet — handles page-refresh (dlTasks starts empty) and modal re-open.
        this._reconcileDownloads()
      })
    },

    // Rebuild dlTasks entries for active (non-terminal) chapter downloads of the current manga
    // from the live `downloads` snapshot. Needed on page-refresh (dlTasks starts empty) and
    // modal re-open after navigating away mid-download (partial pages on disk hide the source
    // chapter in mergedChapters unless dlTasks is current).
    _reconcileDownloads() {
      const curId = this.current?.id
      if (!curId) return
      for (const [tid, v] of Object.entries(this.downloads)) {
        if (v.title !== curId || _TASK_TERMINAL.includes(v.status)) continue
        const ch = String(v.chapter || '')
        if (ch && !(ch in this.dlTasks)) this.dlTasks[ch] = tid
      }
    },

    // Drive the open manga's contextual progress (tp.runStatus / ver.dl) straight from the
    // aggregated `transplant` snapshot, and run each task's terminal handler exactly once.
    _reconcileTransplant() {
      const TERMINAL = ['done', 'cancelled', 'error']
      for (const [id, st] of Object.entries(this.transplant)) {
        const isDl = id.endsWith('_transplant_chdlversion')
        const terminal = TERMINAL.includes(st.status)
        // Task ids are deterministic per manga+kind, so a RE-run reuses the same id. Once it
        // goes active again, forget its previous terminal so the next completion fires anew.
        if (!terminal) tpTermSeen.delete(id)
        if (this.current && st.title === this.current.id) {
          if (isDl) {
            if (!terminal) this.ver.dl = { running: true, done: st.chapterDone || 0, total: st.chapterTotal || 0, name: this.ver.dl?.name || st.title }
          } else {
            this.tp.taskId = id; this.tp.runStatus = st; this.tp.running = !terminal
          }
        }
        if (terminal && !tpTermSeen.has(id)) {
          tpTermSeen.add(id)
          if (isDl) this._onVersionDlDone(st)
          else this._onTranslateDone(st)
        }
      }
      if (tpTermSeen.size > 200) tpTermSeen = new Set([...tpTermSeen].filter(id => id in this.transplant))
    },
    // Terminal handler for a translation run (replaces the old _pollTpRun terminal branch).
    _onTranslateDone(st) {
      const ui = useUiStore()
      const n = (st.chapters || []).length
      if (st.status === 'cancelled') ui.toast('Traducción detenida', 'info')
      else if (st.status === 'error') ui.toast('Falló la traducción', 'error')
      else ui.toast(n ? `Listo: ${n} capítulo(s) en español` : 'Sin capítulos compuestos', n ? 'ok' : 'error')
      this.libraryDirty++
      if (this.current?.id && this.current.id === st.title) {
        this.tp.running = false
        this.tpLoadChapters()
        api.get(`/api/library/${encodeURIComponent(this.current.id)}`)
          .then(d => { this.chapters = d.chapters || []; this.upscaled = d.upscaled || {}; this.current.transplant_meta = d.transplant_meta || null })
          .catch(() => {})
      }
    },
    // Terminal handler for a version download (replaces the old _pollVerDl terminal branch).
    _onVersionDlDone(st) {
      const ui = useUiStore()
      if (st.status === 'error') ui.toast('Falló la descarga de la versión', 'error')
      else if (st.status === 'cancelled') ui.toast('Descarga de versión detenida', 'info')
      else { const n = st.downloaded || 0; ui.toast(n ? `Descargados ${n} capítulo(s) de esta versión` : 'No faltaban capítulos de esta versión', n ? 'ok' : 'info') }
      this.libraryDirty++
      if (this.current?.id === st.title) {
        this.ver.dl = null
        api.get(`/api/library/${encodeURIComponent(this.current.id)}`)
          .then(d => { this.chapters = d.chapters || []; this.upscaled = d.upscaled || {} }).catch(() => {})
      }
    },
    // Detecta exportaciones recién completadas en el snapshot SSE y dispara su descarga UNA vez.
    // Vive en el store (no en la vista de exportar) → la descarga ocurre aunque el usuario ya haya
    // salido de esa pantalla. El backend mantiene el .cbz en /tmp hasta que se sirve (luego pasa a
    // 'downloaded' y desaparece del snapshot), así que aquí basta con pedir el archivo.
    _reconcileExports() {
      for (const [id, v] of Object.entries(this.exports)) {
        if (exportTermSeen.has(id)) continue
        if (_mapStatus(v.status) === 'done') {
          exportTermSeen.add(id)
          this._onExportDone(id, v)
        } else if (v.status === 'error') {
          exportTermSeen.add(id)
          useUiStore().toast(`Falló la exportación${v.title ? ' de «' + v.title + '»' : ''}`, 'error')
        }
      }
      if (exportTermSeen.size > 200) exportTermSeen = new Set([...exportTermSeen].filter(id => id in this.exports))
    },
    // Dispara la descarga del navegador para un tomo terminado. Un <a download> apuntando al
    // endpoint (Content-Disposition: attachment) guarda el archivo sin abrir pestaña; el backend
    // marca la tarea como 'downloaded' al servirla y la limpia. Si el navegador la bloqueara, la
    // vista Actividad ofrece un botón "Descargar" manual como respaldo.
    _onExportDone(id, v) {
      this.downloadExportFile(id)
      useUiStore().toast(`✓ Tomo listo: ${v.volume_name || v.title || 'descarga'}`, 'ok')
    },
    downloadExportFile(id) {
      try {
        const a = document.createElement('a')
        a.href = this.exportFileUrl(id); a.download = ''
        document.body.appendChild(a); a.click(); a.remove()
      } catch (_) { window.location.assign(this.exportFileUrl(id)) }
    },
    // El historial se DERIVA del snapshot (getter historyTasks). Estas acciones solo afectan
    // a la VISTA (no se puede borrar lo que el backend reporta): ocultan ids localmente.
    _persistHidden() { try { localStorage.setItem('act-hidden', JSON.stringify(this.hiddenHistoryIds.slice(-300))) } catch {} },
    hideFromHistory(id) { if (!this.hiddenHistoryIds.includes(id)) { this.hiddenHistoryIds = [...this.hiddenHistoryIds, id]; this._persistHidden() } },
    clearActivityHistory() {
      this.hiddenHistoryIds = [...new Set([...this.hiddenHistoryIds, ...this.historyTasks.map(t => t.id)])]
      this._persistHidden()
    },

    async open(manga, opts = {}) {
      this.current = {
        id: manga.id || manga.name, name: manga.name, cover: manga.cover, source_meta: manga.source_meta,
        mdOnly: !!manga.mdOnly, trackedOnly: !!manga.trackedOnly,
        trackedId: manga.trackedId || manga.mdId || null, status: manga.status || '',
      }
      if (this.current.cover) this.coverCache[this.current.id] = this.current.cover
      this.chapters = []
      this.sourceChapters = []
      this.mdChapters = []
      this.mdId = manga.mdId || null
      this.mdLang = ''
      this.upscaled = {}
      this.modalLoading = true
      // Push a history entry so browser back closes the modal and forward reopens it.
      // Skip when we're re-opening *because of* a back/forward (fromHistory).
      if (!opts.fromHistory) useUiStore().pushNav()
      try {
        // Load local chapters if downloaded (skip for tracked-but-not-downloaded entries)
        if (!manga.mdOnly && !manga.trackedOnly) {
          const d = await api.get(`/api/library/${encodeURIComponent(this.current.id)}`)
          this.chapters = d.chapters || []
          this.upscaled = d.upscaled || {}
          if (d.source_meta) this.current.source_meta = d.source_meta
          this.current.transplant_meta = d.transplant_meta || null
        }
        this._resetTp()
        // If source_meta exists (downloaded, or tracked from Fuentes pre-download), load source
        // chapters from the EFFECTIVE source (the pinned version if any, else the origin source).
        this._loadSourceChapters()
        // If mdId exists, load MangaDex chapters — UNLESS a MangaDex version is pinned, in which
        // case _loadSourceChapters already loaded THAT version's chapters into mdChapters.
        if (this.mdId && this.effectiveSource?.kind !== 'mangadex') {
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
      finally {
        this.modalLoading = false
        // If a translation/version-download is already running for this manga, rebind the
        // modal's contextual progress immediately (don't wait for the next 500ms SSE tick).
        this._reconcileTransplant()
        // Rebuild chapter download tracking so mid-download re-opens show the progress ring
        // instead of the partial pages.
        this._reconcileDownloads()
      }
    },

    // Carga la lista de capítulos de la fuente ACTIVA (effectiveSource). Se reusa al abrir el
    // modal y al fijar/quitar una versión principal, para que Capítulos refleje al instante la
    // fuente desde la que se descargará.
    _loadSourceChapters() {
      const src = this.effectiveSource
      this.sourceChapters = []
      if (!src) return
      // Versión MangaDex fijada: cargar SUS capítulos en mdChapters (el merge los enruta por la
      // vía MangaDex vía `_mdChapterId`). mangaId viene como `uuid@@lang`.
      if (src.kind === 'mangadex') {
        const [uuid, lang] = String(src.mangaId).split('@@')
        this.mdChaptersLoading = true
        api.get(`/api/mangadex/chapters/${uuid}`)
          .then(chs => {
            this.mdChapters = chs || []
            const langs = [...new Set(this.mdChapters.map(c => c.language).filter(Boolean))]
            this.mdLang = (lang && langs.includes(lang)) ? lang : preferredLang(langs)
          })
          .catch(() => {})
          .finally(() => { this.mdChaptersLoading = false })
        return
      }
      this.sourceLoading = true
      api.get(`/api/sources/manga/${src.mangaId}/chapters`)
        .then(chs => {
          this.sourceChapters = (chs || []).map(ch => ({
            ...ch,
            chapterNorm: String(ch.chapterNumber ?? '').replace(/\.0$/, ''),
            name: ch.name || `Cap. ${ch.chapterNumber ?? '?'}`,
          }))
        })
        .catch(() => {})
        .finally(() => { this.sourceLoading = false })
    },
    close() { this._stopTpPolls(); this.current = null },

    // ── Traducción por trasplante (pestaña "Traducir") ─────────────────────────
    _candKey(c) { return c ? `${c.sourceId}_${c.id}` : null },
    _stopTpPolls() {
      const t = this.tp
      if (t._dpoll) { clearInterval(t._dpoll); t._dpoll = null }
      if (t._rpoll) { clearInterval(t._rpoll); t._rpoll = null }
    },
    _resetTp() {
      this._stopTpPolls()
      const meta = this.current?.transplant_meta || null
      this.tp = {
        loading: false, phase: meta?.art && meta?.es ? 'ready' : '', variants: meta?.variants || [],
        discoverProgress: {},
        artCands: [], esCands: [],
        artSel: meta?.art ? { ...meta.art, id: meta.art.mangaId } : null,
        esSel: meta?.es ? { ...meta.es, id: meta.es.mangaId } : null,
        chapters: [], chaptersLoading: false,
        preview: {},
        running: false, runStatus: null, taskId: null,
        _dpoll: null, _rpoll: null,
        pendingVolumes: [], unresolvedVolumes: [],
        volResolving: false,
      }
      // si ya hay fuentes elegidas (discover previo cacheado), cargar la lista de capítulos
      if (meta?.art && meta?.es) this.tpLoadChapters()
    },

    async tpLoadChapters() {
      const title = this.current?.id
      if (!title) return
      this.tp.chaptersLoading = true
      try {
        const d = await api.get(`/api/transplant/chapters/${encodeURIComponent(title)}`)
        this.tp.chapters = d.chapters || []
        this.tp.pendingVolumes = d.pendingVolumes || []
      } catch (_) { this.tp.chapters = [] }
      finally { this.tp.chaptersLoading = false }
    },

    // Reintenta el reparto automático de tomos pendientes (manga importado).
    async tpResolveVolumes() {
      const title = this.current?.id
      if (!title) return
      this.tp.volResolving = true
      try {
        const res = await api.post('/api/transplant/resolve_volumes', { title })
        this.tp.unresolvedVolumes = res.unresolved || []
        if (res.resolved?.length) { this.tpLoadChapters(); useUiStore().toast(`${res.resolved.length} tomo(s) repartido(s) en capítulos`, 'ok') }
      } catch (e) {
        useUiStore().toast('No se pudo repartir el tomo', 'error')
      } finally {
        this.tp.volResolving = false
      }
    },
    // Reparto manual: pageCounts = [{chapter, pages}, ...] en orden.
    async tpResolveVolumeManual(prefix, pageCounts) {
      const title = this.current?.id
      if (!title) return
      try {
        await api.post('/api/transplant/resolve_volume_manual', { title, prefix, pageCounts })
        this.tp.unresolvedVolumes = this.tp.unresolvedVolumes.filter(v => v.prefix !== prefix)
        this.tpLoadChapters()
        useUiStore().toast('Tomo repartido en capítulos', 'ok')
      } catch (e) {
        let msg = 'No se pudo repartir: revisa que las páginas sumen el total del tomo'
        try { msg = JSON.parse(e.body)?.error || msg } catch {}
        useUiStore().toast(msg, 'error')
      }
    },

    async tpDiscover() {
      const ui = useUiStore()
      const title = this.current?.id
      if (!title) return
      const t = this.tp
      t.loading = true; t.phase = 'start'; t.discoverProgress = {}
      try {
        const res = await api.post('/api/transplant/discover', {
          title, anilistId: this.current?.al_id || null,
          artLocal: !!this.current?.source_meta?.imported,
        })
        this._pollTpDiscover(res.task_id)
      } catch (e) {
        t.loading = false; t.phase = 'error'
        ui.toast(e?.status === 503 ? 'Suwayomi offline' : 'Falló el descubrimiento', 'error')
      }
    },
    _pollTpDiscover(taskId) {
      const ui = useUiStore(); const t = this.tp
      if (t._dpoll) clearInterval(t._dpoll)
      t._dpoll = setInterval(async () => {
        try {
          const st = await api.get(`/api/transplant/status?task_id=${encodeURIComponent(taskId)}`)
          t.phase = st.phase || t.phase
          if (st.variants) t.variants = st.variants
          t.discoverProgress = { searched: st.searched, searchTotal: st.searchTotal, ranked: st.ranked, rankTotal: st.rankTotal }
          if (st.status === 'done') {
            clearInterval(t._dpoll); t._dpoll = null; t.loading = false
            t.artCands = st.art?.candidates || []
            t.esCands = st.es?.candidates || []
            t.artSel = st.art?.best || t.artCands[0] || null
            t.esSel = st.es?.best || t.esCands[0] || null
            t.unresolvedVolumes = st.unresolvedVolumes || []
            t.phase = 'ready'
            if (!t.artSel || !t.esSel) ui.toast('No se hallaron ambas fuentes (arte y ES)', 'error')
            else {
              await this._tpConfirm(); this.tpLoadChapters()
              if (st.resolvedVolumes?.length) ui.toast(`${st.resolvedVolumes.length} tomo(s) repartido(s) en capítulos`, 'ok')
              if (t.unresolvedVolumes.length) ui.toast('Algún tomo no se pudo repartir automáticamente — ajusta manualmente en Traducir', 'warn')
            }
          } else if (st.status === 'error') {
            clearInterval(t._dpoll); t._dpoll = null; t.loading = false; t.phase = 'error'
            ui.toast('Falló el descubrimiento', 'error')
          }
        } catch (_) {}
      }, 1200)
    },
    // Al cambiar de fuente (override): confirmar y RECARGAR la lista de capítulos
    // disponibles — la 2ª mejor calidad puede tener distintos capítulos que la mejor.
    async tpSelectArt(c) { this.tp.artSel = c; this.tp.preview = {}; await this._tpConfirm(); this.tpLoadChapters() },
    async tpSelectEs(c) { this.tp.esSel = c; await this._tpConfirm(); this.tpLoadChapters() },
    async _tpConfirm() {
      const t = this.tp
      if (!t.artSel || !t.esSel) return
      const toSrc = (c) => ({ sourceId: c.sourceId, mangaId: c.id ?? c.mangaId, sourceName: c.sourceName, sourceLang: c.sourceLang })
      try { await api.post('/api/transplant/confirm', { title: this.current.id, art: toSrc(t.artSel), es: toSrc(t.esSel) }) }
      catch (_) {}
    },

    async tpRun(chapters) {
      const ui = useUiStore(); const t = this.tp
      if (!t.artSel || !t.esSel) { ui.toast('Elige fuente de arte y de español', 'error'); return }
      await this._tpConfirm()
      try {
        const res = await api.post('/api/transplant/run', { title: this.current.id, chapters: chapters || 'all', qa: this.qaMode })
        t.running = true
        t.taskId = res.task_id
        t.runStatus = { phase: 'start', chapterTotal: (res.chapters || []).length }
        ui.toast(`Traduciendo ${(res.chapters || []).length} capítulo(s)…`, 'info')
        // Progreso + terminación llegan por el snapshot SSE (_reconcileTransplant); sin polling.
      } catch (e) {
        ui.toast(e?.body?.includes('no chapters') ? 'No hay capítulos comunes a ambas fuentes' : 'No se pudo iniciar', 'error')
      }
    },
    async tpCancel() {
      if (this.tp.taskId) this.cancelTransplant(this.tp.taskId)
    },
    // Cancel any transplant task (translation run) by id — used from the modal AND the
    // Activity drawer/view, where the task may not belong to the currently-open manga.
    async cancelTransplant(taskId) {
      if (!taskId) return
      try { await api.post('/api/transplant/cancel', { task_id: taskId }) } catch (_) {}
      useUiStore().toast('Deteniendo…', 'info')
    },

    /* ── Modo QA de traducción (SOLO testing) ───────────────────────────────── */
    toggleQa() {
      this.qaMode = !this.qaMode
      localStorage.setItem('tp-qa', this.qaMode ? '1' : '0')
    },
    // Marca la página ACTUAL del lector como mal traducida, con un motivo y nota opcional.
    // El backend conserva salida + arte EN + ES + overlay + stats en data/_translation_qa.
    async qaFlagPage(reason, note = '') {
      const ui = useUiStore()
      if (!this.reader) return
      const url = this.pages[this.page] || ''
      const page = decodeURIComponent(url.split('?')[0].split('/').pop() || '')
      if (!page) { ui.toast('No se pudo identificar la página', 'error'); return }
      try {
        await api.post('/api/transplant/qa/flag', {
          title: this.reader.title, chapter: this.reader.chapter, page, reason, note,
        })
        ui.toast(`Página marcada · ${reason}`, 'ok')
      } catch (_) { ui.toast('No se pudo marcar la página', 'error') }
    },
    async qaSize() {
      try { return await api.get('/api/transplant/qa/size') } catch (_) { return { bytes: 0, flags: 0 } }
    },
    async qaClear() {
      const ui = useUiStore()
      try { const d = await api.post('/api/transplant/qa/clear', {}); ui.toast('Datos QA borrados', 'ok'); return d }
      catch (_) { ui.toast('No se pudo borrar', 'error'); return null }
    },

    // Vista previa de páginas de un capítulo (para revisar calidad de la fuente de arte)
    async tpTogglePreview(chapter) {
      const t = this.tp
      const cur = t.preview[chapter]
      if (cur?.open) { t.preview = { ...t.preview, [chapter]: { ...cur, open: false } }; return }
      if (cur?.pages?.length) { t.preview = { ...t.preview, [chapter]: { ...cur, open: true } }; return }
      t.preview = { ...t.preview, [chapter]: { open: true, loading: true, pages: [] } }
      try {
        const d = await api.get(`/api/transplant/preview/${encodeURIComponent(this.current.id)}/${encodeURIComponent(chapter)}`)
        t.preview = { ...t.preview, [chapter]: { open: true, loading: false, pages: d.pages || [] } }
      } catch (_) {
        t.preview = { ...t.preview, [chapter]: { open: true, loading: false, pages: [] } }
      }
    },

    /* ── Versiones (ranking de calidad, solo lectura) ────────────────────── */
    async verDiscover(refresh = false) {
      const ui = useUiStore()
      const title = this.current?.id
      if (!title) return
      const v = this.ver
      v.loading = true; v.phase = 'start'; v.discoverProgress = {}
      v.versions = []; v.byLang = {}; v.local = null
      v.sample = { open: false, key: null, loading: false, pages: [], name: '' }
      try {
        const res = await api.post('/api/transplant/versions', {
          title, anilistId: this.current?.al_id || null,
          currentLang: this.current?.source_meta?.sourceLang || '',
          refresh,   // "Buscar de nuevo" fuerza re-barrido (salta la caché de 24h)
        })
        this._pollVer(res.task_id)
      } catch (e) {
        v.loading = false; v.phase = 'error'
        ui.toast(e?.status === 503 ? 'Suwayomi offline' : 'Falló la búsqueda de versiones', 'error')
      }
    },
    _pollVer(taskId) {
      const ui = useUiStore(); const v = this.ver
      if (v._poll) clearInterval(v._poll)
      v._poll = setInterval(async () => {
        try {
          const st = await api.get(`/api/transplant/status?task_id=${encodeURIComponent(taskId)}`)
          v.phase = st.phase || v.phase
          v.discoverProgress = { searched: st.searched, searchTotal: st.searchTotal, ranked: st.ranked, rankTotal: st.rankTotal }
          if (st.status === 'done') {
            clearInterval(v._poll); v._poll = null; v.loading = false
            v.versions = st.versions || []
            v.byLang = st.byLang || {}
            v.local = st.local || null
            v.currentLang = st.currentLang || ''
            // Por defecto mostramos TODAS las fuentes/idiomas (panorama completo); el usuario
            // filtra por idioma con el desplegable (que lleva el conteo por idioma).
            v.langFilter = ''
            v.phase = 'ready'
            if (!v.versions.length) ui.toast('No se encontraron otras versiones', 'info')
          } else if (st.status === 'error') {
            clearInterval(v._poll); v._poll = null; v.loading = false; v.phase = 'error'
            ui.toast('Falló la búsqueda de versiones', 'error')
          }
        } catch (_) {}
      }, 1200)
    },
    verSetLangFilter(l) { this.ver.langFilter = l },
    verReset() {
      const v = this.ver
      if (v._poll) { clearInterval(v._poll); v._poll = null }
      if (v._dlpoll) { clearInterval(v._dlpoll); v._dlpoll = null }
      v.loading = false; v.phase = ''; v.discoverProgress = {}
      v.versions = []; v.byLang = {}; v.local = null; v.currentLang = ''; v.langFilter = ''
      v.sample = { open: false, key: null, loading: false, pages: [], name: '' }
      v.cmpSel = []; v.dl = null
    },
    // Comparar A|B: selección de hasta 2 candidatos (toggle; el 3º desplaza al más viejo)
    verToggleCompare(cand) {
      const v = this.ver
      const k = (c) => `${c.sourceId}_${c.mangaId}`
      const idx = v.cmpSel.findIndex(c => k(c) === k(cand))
      if (idx >= 0) { v.cmpSel = v.cmpSel.filter((_, i) => i !== idx); return }
      v.cmpSel = [...v.cmpSel, cand].slice(-2)
    },
    async verRunCompare() {
      const ui = useUiStore(); const v = this.ver
      if (v.cmpSel.length !== 2) return
      const [a, b] = v.cmpSel
      this._resetView()
      this.mode = 'paged'   // el comparador A|B solo se renderiza en modo paginado
      this.reader = { title: this.current?.id || '', chapter: '', source: 'compare', kind: 'manga', cover: this.current?.cover || '' }
      this.readerLoading = true; this.pages = []; this.page = 0; this.comparePages2 = []
      const slim = (c) => ({ sourceId: c.sourceId, mangaId: c.mangaId, sourceLang: c.sourceLang })
      const label = (c) => c.local ? `Tu versión local · ${c.quality?.height || '?'}px` : `${c.sourceName} · ${c.quality?.height || '?'}px`
      this.compareLabels = { left: label(a), right: label(b) }
      try {
        // El backend empareja por hash perceptual: ambos lados muestran LA MISMA página.
        const d = await api.post('/api/transplant/compare_pages', { title: this.current.id, a: slim(a), b: slim(b) })
        const pairs = d.pairs || []
        if (!pairs.length) {
          ui.toast('No se hallaron páginas equivalentes para comparar estas versiones', 'error'); this.reader = null; return
        }
        this.pages = pairs.map(p => p.left)
        this.comparePages2 = pairs.map(p => p.right)
        const avg = Math.round(100 * (d.sim_avg ?? (pairs.reduce((s, p) => s + p.sim, 0) / pairs.length)))
        // Muestra distribuida: la comparación abarca varios capítulos repartidos por la serie.
        const chs = (d.chapters || []).filter(Boolean)
        const chLbl = chs.length > 1 ? ` · caps ${chs.join(', ')}` : (chs[0] ? ` · cap ${chs[0]}` : '')
        this.compareLabels = { left: `${label(a)} · ${avg}% coincidencia${chLbl}`, right: label(b) }
        this.compareMode = true
        this.scanCompareMode = true
      } catch (e) {
        const detail = e?.status === 404 ? ' (reinicia el servidor para cargar la nueva ruta)' : (e?.status ? ` (${e.status})` : '')
        ui.toast(`No se pudo cargar la comparación${detail}`, 'error'); this.reader = null
      } finally { this.readerLoading = false }
    },
    // Descargar en la biblioteca los capítulos que faltan de una versión (no destructivo).
    async verDownloadVersion(cand) {
      const ui = useUiStore(); const v = this.ver
      v.dl = { running: true, done: 0, total: 0, name: cand.sourceName }
      try {
        const res = await api.post('/api/transplant/download_version', {
          title: this.current.id,
          source: { mangaId: cand.mangaId, sourceId: cand.sourceId, sourceName: cand.sourceName, sourceLang: cand.sourceLang },
        })
        // Progreso + terminación llegan por el snapshot SSE (_reconcileTransplant); sin polling.
        void res
      } catch (e) {
        v.dl = null
        ui.toast(e?.status === 503 ? 'Suwayomi offline' : 'No se pudo iniciar la descarga', 'error')
      }
    },
    // Fijar como principal (solo etiqueta, no destructivo)
    async verSetPrimary(cand) {
      const ui = useUiStore()
      const sm = this.current?.source_meta
      const isSet = sm?.recommended_source && cand
        && sm.recommended_source.sourceId === cand.sourceId
        && String(sm.recommended_source.mangaId) === String(cand.mangaId)
      const source = isSet ? null : (cand ? {
        sourceId: cand.sourceId, mangaId: cand.mangaId, sourceName: cand.sourceName,
        sourceLang: cand.sourceLang, quality: cand.quality,
      } : null)
      try {
        const d = await api.post('/api/transplant/set_primary', { title: this.current.id, source })
        if (this.current) this.current.source_meta = { ...(this.current.source_meta || {}), recommended_source: d.recommended_source || null }
        this.libraryDirty++
        // La fuente activa de capítulos cambió: recargar la lista para que la pestaña Capítulos
        // descargue ya desde la versión fijada (o vuelva a la de origen al quitarla).
        this._loadSourceChapters()
        ui.toast(source ? `Fijada "${cand.sourceName}" — los capítulos se descargarán de esta fuente` : 'Versión principal quitada', 'ok')
      } catch (_) { ui.toast('No se pudo fijar la versión', 'error') }
    },
    async verReadSample(cand) {
      const v = this.ver
      const key = `${cand.sourceId}_${cand.mangaId}`
      if (v.sample.key === key && v.sample.open) { v.sample = { ...v.sample, open: false }; return }
      v.sample = { open: true, key, loading: true, pages: [], name: cand.sourceName }
      try {
        const qs = `mangaId=${encodeURIComponent(cand.mangaId)}&sourceId=${encodeURIComponent(cand.sourceId || '')}&lang=${encodeURIComponent(cand.sourceLang || '')}&title=${encodeURIComponent(this.current?.id || '')}`
        const d = await api.get(`/api/transplant/preview_candidate?${qs}`)
        v.sample = { open: true, key, loading: false, pages: d.pages || [], name: cand.sourceName }
      } catch (_) {
        v.sample = { open: true, key, loading: false, pages: [], name: cand.sourceName }
        useUiStore().toast('No se pudo cargar la muestra', 'error')
      }
    },

    async downloadSourceChapter(ch) {
      const ui = useUiStore()
      const src = this.effectiveSource
      const sid = ch._sourceId || ch.id
      if (!src || !sid) return
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
          sourceId: src.sourceId,
          mangaId: src.mangaId,
          sourceName: src.sourceName,
          sourceLang: src.sourceLang,
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

    // manga.trackedId is the local_library.json key (MangaDex uuid / src_* composite /
    // sanitized title), set when the open()'d entry already came from a tracked match.
    // A purely-local folder with no tracked entry yet gets one created on first use.
    async setStatus(manga, status) {
      const prev = manga.status
      manga.status = status
      try {
        let key = manga.trackedId
        if (!key) {
          key = manga.id
          await api.post('/api/mangadex/local_library/add', { manga: { id: key, title: manga.name || manga.title, cover: manga.cover, kind: 'local' } })
          manga.trackedId = key
        }
        await api.post('/api/mangadex/local_library/status', { manga_id: key, status })
        this.libraryDirty++
      } catch (_) { manga.status = prev; useUiStore().toast('No se pudo cambiar el estado', 'error') }
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
        const res = await api.post('/api/upscale/upscale_chapter', {
          title: this.current.id, chapter, eco: opts.eco ?? this.eco, fast: opts.fast ?? false,
          ...(opts.excludePages?.length ? { exclude_pages: opts.excludePages } : {}),
        })
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
    async upscaleAll(opts = {}) {
      const chapters = this.chapters.map(c => c.chapter).filter(ch => this.upscaled[ch] !== true)
      if (!chapters.length) { useUiStore().toast('Todos los capítulos ya están en 4K', 'info'); return }
      for (const ch of chapters) this.upTasks[String(ch)] = taskId(this.current.id, String(ch), 'upscale')
      try {
        await api.post('/api/upscale/upscale_manga', {
          title: this.current.id, chapters, eco: opts.eco ?? this.eco,
          ...(opts.excludePages?.length ? { exclude_pages: opts.excludePages } : {}),
        })
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
        this._forgetChapterProgress(this.current.id, chapter)
      } catch (_) { useUiStore().toast('No se pudo borrar el capítulo', 'error') }
    },
    // Al borrar un capítulo: quitar su marca de leído y, si era el "último leído" del
    // rail "Continuar leyendo", soltar la entrada (sin ts no aparece) para no ofrecer
    // reanudar un capítulo que ya no existe en disco.
    _forgetChapterProgress(mangaId, chapter) {
      const e = this.progress[mangaId]; if (!e) return
      if (e.read) delete e.read[String(chapter)]
      if (String(e.lastChapter) === String(chapter)) {
        delete e.lastChapter; delete e.lastPage; delete e.lastTotal; delete e.ts
      }
      this._persistProgress()
    },

    // Delete a whole manga from the library: downloaded + upscaled files, and the
    // MangaDex local-library entry if it's tracked there (mirrors the legacy flow).
    deleteManga() {
      if (!this.current) return
      const title = this.current.id
      const name = this.current.name || title
      const mdId = this.mdId
      const trackedId = this.current.trackedId || mdId   // id en local_library.json (src_… o uuid)
      const ui = useUiStore()
      // Optimistic: close the modal and hide it from the grid, with a 6s undo window
      // before the (irreversible) file deletion actually runs — no confirm dialog needed.
      this.close(); ui.replaceNav()
      // Olvida el progreso de lectura (localStorage) para que NO siga en "Continuar leyendo"
      // ni deje reanudar un manga eliminado. Se hace ya (no en el timeout) porque el borrado
      // es lo que el usuario pidió; si deshace, no recupera la posición exacta (aceptable).
      if (this.progress[title]) { delete this.progress[title]; this._persistProgress() }
      if (!this.pendingDelete.includes(title)) this.pendingDelete = [...this.pendingDelete, title]
      this.libraryDirty++
      let undone = false
      ui.toast(`"${name}" eliminado`, 'info', 6000, {
        label: 'Deshacer',
        fn: () => { undone = true; this.pendingDelete = this.pendingDelete.filter(t => t !== title); this.libraryDirty++ },
      })
      setTimeout(async () => {
        if (undone) return
        let ok = false
        // delete_manga ahora borra carpeta Y purga local_library.json (por trackedId o título)
        try { await api.del('/api/download/delete_manga', { body: { title, trackedId } }); ok = true } catch (_) {}
        if (trackedId) { try { await api.del(`/api/mangadex/local_library/remove/${encodeURIComponent(trackedId)}`); ok = true } catch (_) {} }
        this.pendingDelete = this.pendingDelete.filter(t => t !== title)
        if (!ok) ui.toast('No se pudo eliminar el manga', 'error')
        this.libraryDirty++
      }, 6000)
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
    // Unified cancel/dismiss dispatcher used by the Activity drawer + view. `t` is a
    // normalized task ({ id, kind, status, ... }). Routes to the right per-kind action.
    cancelAnyTask(t) {
      if (!t) return
      if (t.kind === 'translate') return this.cancelTransplant(t.id)
      if (t.kind === 'export') {
        const terminal = ['done', 'complete', 'error', 'cancelled']
        if (terminal.includes(t.status)) return this.dismissExport(t.id)
        return this.cancelExport(t.id)
      }
      return this.cancelTask({ id: t.id, kind: t.kind })   // download (incl. version dl) / upscale
    },
    async cancelExport(id) {
      this._markCancelled(id)
      try { await api.post(`/api/export/cancel/${encodeURIComponent(id)}`, {}) } catch (_) {}
    },
    exportFileUrl(id) { return `/api/export/file/${id}` },
    // Remove a finished export from the queue (and delete its temp file on the backend).
    async dismissExport(id) {
      if (!this.dismissedExports.includes(id)) this.dismissedExports = [...this.dismissedExports, id]
      try { await api.del(`/api/export/task/${encodeURIComponent(id)}`) } catch (_) {}
    },

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
    async exportTomo({ chapters, volumeName, format = 'cbz', quality = 92, codec = 'jpeg', downscaleHalf = false, coverB64 = '', toDrive = false }) {
      const body = {
        title: this.current.id, chapters, volume_name: volumeName || this.current.name,
        format, quality, codec, downscale_half: downscaleHalf, ...(coverB64 ? { cover_data: coverB64 } : {}),
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

    // ── Selector de portada (AniList + MangaDex) ──────────────────────────
    async openCoverPicker() {
      const cp = this.coverPicker
      cp.open = true; cp.loading = true; cp.anilist = []; cp.mangadex = []; cp.applying = null
      try {
        const qs = `title=${encodeURIComponent(this.current.id)}&mdId=${encodeURIComponent(this.mdId || '')}`
        const d = await api.get(`/api/library/cover_options?${qs}`)
        cp.current = d.current; cp.anilist = d.anilist || []; cp.mangadex = d.mangadex || []
      } catch (_) { useUiStore().toast('No se pudieron cargar portadas', 'error') }
      finally { cp.loading = false }
    },
    closeCoverPicker() { this.coverPicker.open = false },
    _bustCover() {
      // apunta la portada mostrada al archivo local recién escrito, con cache-bust
      const u = `/api/library/cover/${encodeURIComponent(this.current.id)}?t=${Date.now()}`
      if (this.current) this.current.cover = u
      this.libraryDirty++
    },
    async applyCover(url) {
      const cp = this.coverPicker
      cp.applying = url
      const ok = await this.editMeta({ coverUrl: url })
      cp.applying = null
      if (ok) { this._bustCover(); cp.open = false }
    },
    applyCoverFile(file) {
      if (!file) return
      const r = new FileReader()
      r.onload = async () => {
        const ok = await this.editMeta({ coverB64: r.result })
        if (ok) { this._bustCover(); this.coverPicker.open = false }
      }
      r.readAsDataURL(file)
    },

    async loadHealth() {
      try {
        const d = await api.get(`/api/library/chapter_health/${encodeURIComponent(this.current.id)}`)
        const m = {}; for (const h of (Array.isArray(d) ? d : [])) m[h.chapter] = h; this.health = m
      } catch (_) {}
    },
    async upscaleRange(from, to, fast = false, excludePages = []) {
      const lo = Math.min(parseFloat(from), parseFloat(to)), hi = Math.max(parseFloat(from), parseFloat(to))
      if (isNaN(lo) || isNaN(hi)) return
      const chapters = this.chapters.map(c => c.chapter).filter(ch => { const n = parseFloat(ch); return !isNaN(n) && n >= lo && n <= hi && this.upscaled[ch] !== true })
      if (!chapters.length) { useUiStore().toast('Nada que escalar en ese rango', 'info'); return }
      try {
        await api.post('/api/upscale/upscale_manga', {
          title: this.current.id, chapters, eco: this.eco, fast,
          ...(excludePages?.length ? { exclude_pages: excludePages } : {}),
        })
        useUiStore().toast(`Escalando ${chapters.length} capítulos`, 'info')
      } catch (_) { useUiStore().toast('No se pudo iniciar', 'error') }
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
    loadExportPreview(chapters, quality = 92, codec = 'jpeg') {
      clearTimeout(previewTimer)
      if (!chapters?.length || !this.current) {
        this.exportPreview = { pages: 0, est_mb: 0, upscaled_pages: 0, original_pages: 0 }
        return
      }
      previewTimer = setTimeout(async () => {
        try {
          const d = await api.post('/api/export/preview', { title: this.current.id, chapters, quality, codec, exclude_pages: this.excludedPages })
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
    async resolveMdexId({ fuzzy = false } = {}) {
      if (this.mdex.id) return this.mdex.id
      // Manga ya emparejado con MangaDex → su UUID es autoritativo, sin búsqueda.
      if (this.mdId) { this.mdex.id = this.mdId; return this.mdId }
      const title = this.current?.name
      if (!title) return null
      try {
        // Resuelve por VARIANTES de nombre (sinónimos AniList: romaji/inglés/nativo), no sólo
        // por el nombre principal — así la carpeta local encuentra su entrada aunque MangaDex
        // la indexe bajo otro título/idioma. El backend compara contra altTitles. Con `fuzzy`
        // (portadas) cae al mejor candidato si no hay match exacto (marcado `approx`).
        const qs = new URLSearchParams({ title })
        if (this.current?.al_id) qs.set('al_id', this.current.al_id)
        if (fuzzy) qs.set('fuzzy', '1')
        const m = await api.get('/api/mangadex/resolve?' + qs.toString())
        if (m && m.id) { this.mdex.id = m.id; this.mdex.mdManga = m; this.mdex.approx = !!m.approx; return m.id }
        return null
      } catch (_) { return null }
    },
    async searchMdexForTomo() {
      const q = this.mdex.search.trim(); if (!q) return
      this.mdex.searching = true; this.mdex.results = []
      try { const r = await api.get('/api/mangadex/search?q=' + encodeURIComponent(q)); this.mdex.results = Array.isArray(r) ? r.slice(0, 8) : [] }
      catch (_) {} finally { this.mdex.searching = false }
    },
    async selectMdexEntry(entry) {
      this.mdex.id = entry.id; this.mdex.mdManga = entry; this.mdex.approx = false
      this.mdex.results = []; this.mdex.search = ''
      this.mdex.volumes = []; this.mdex.covers = []
      await this.loadMdexVolumes(); this.loadMdexCovers()
    },
    async loadMdexVolumes() {
      // fuzzy: si no hay match exacto, usa el mejor candidato para TRAER las portadas igual
      // (como en /legacy). Si es aproximado, se avisa y el buscador manual permite corregir.
      const id = await this.resolveMdexId({ fuzzy: true })
      if (!id) { useUiStore().toast('No se encontró en MangaDex — búscalo manualmente abajo', 'warn'); return }
      if (this.mdex.approx) useUiStore().toast(`Coincidencia aproximada: "${this.mdex.mdManga?.title || ''}" — verifica o busca manualmente`, 'info')
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
      const id = await this.resolveMdexId({ fuzzy: true }); if (!id) return
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
    resetMdex() { this.mdex = { id: null, mdManga: null, approx: false, search: '', results: [], searching: false, volumes: [], volumesLoading: false, covers: [], coversLoading: false, selectedCover: null, coverLoadingId: null, coverB64: '', coverUrl: '' } },

    /* ── Reader ─────────────────────────────────────────────────────────── */
    _resetView() { this.zoom = 1.0; this.panX = 0; this.panY = 0; this.compareMode = false; this.barsHidden = false; this.scanCompareMode = false; this.comparePages2 = []; this.compareLabels = null },

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

    // titleOverride/coverOverride let continueHistory() jump straight into the
    // reader for a local chapter without first opening the manga modal (which
    // would otherwise need to reload chapters/cover just to populate `current`).
    async read(chapter, source = 'auto', titleOverride = null, coverOverride = null) {
      const title = titleOverride || this.current.id
      this.readerLoading = true
      this._resetView()
      this.reader = { title, chapter, source, kind: 'manga', cover: coverOverride || this.current?.cover || '' }
      this.pages = []
      this.page = 0
      try {
        const d = await api.post('/api/reader/read_chapter', { title, chapter, source })
        this.pages = d.pages || []
        if (this.reader) this.reader.source = d.source
        // restore last-read page for this chapter
        const pr = this.progress[title]
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
    // Open a chapter read straight from remote page URLs (no disk round-trip).
    // kind:'manga' (not 'cbz') so spread/webtoon mode, progress tracking and
    // markRead keep working exactly like a locally-read chapter. `meta` records
    // {kind, chapterRef} so a finished online chapter can be re-resolved later
    // from the reading-history panel (the at-home page URLs themselves are single-use).
    openOnlineReader(title, chapter, pages, label = '', meta = null, cover = '') {
      this._resetView()
      this.reader = { title, chapter, source: 'online', kind: 'manga', sourceLabel: label, onlineMeta: meta, cover: cover || this.current?.cover || '' }
      this.pages = pages
      this.page = 0
      const pr = this.progress[title]
      if (pr && String(pr.lastChapter) === String(chapter) && pr.lastPage > 0 && pr.lastPage < this.pages.length)
        this.page = pr.lastPage
    },
    // Read a not-yet-downloaded chapter (MangaModal) directly from its source, online.
    async readOnline(ch) {
      const ui = useUiStore()
      this.onlineLoadingId = ch._sourceId || ch._mdChapterId
      try {
        let pages = []
        if (ch._sourceId) {
          const pg = await api.get(`/api/sources/chapter/${ch._sourceId}/pages`)
          pages = pg.pages || []
        } else if (ch._mdChapterId) {
          const pg = await api.get(`/api/mangadex/chapter/${ch._mdChapterId}/pages`)
          pages = pg.pages || []
        }
        if (!pages.length) { ui.toast('Capítulo sin páginas', 'error'); return }
        const meta = ch._sourceId ? { kind: 'source', chapterRef: ch._sourceId } : { kind: 'mangadex', chapterRef: ch._mdChapterId }
        this.openOnlineReader(this.current.id, ch.chapter, pages, '', meta, this.current?.cover || '')
      } catch (_) { ui.toast('No se pudo abrir el capítulo', 'error') }
      finally { this.onlineLoadingId = null }
    },
    closeReader() { this.reader = null; this.pages = []; this._resetView() },

    setPage(i) {
      if (i < 0 || i >= this.pages.length) return
      this.page = i; this.panX = 0; this.panY = 0
      this._saveProgress()
      if (i >= this.pages.length - 1) this.markRead(this.reader?.chapter)
    },
    nextPage() { const step = this.spreadActive ? 2 : 1; this.setPage(Math.min(this.page + step, this.pages.length - 1)) },
    prevPage() { const step = this.spreadActive ? 2 : 1; this.setPage(Math.max(this.page - step, 0)) },

    setMode(m) { this.mode = m; localStorage.setItem('reader-mode', m); this._resetView() },
    cycleFit() { const M = ['width', 'height', 'original']; this.fit = M[(M.indexOf(this.fit) + 1) % 3]; localStorage.setItem('reader-fit', this.fit) },
    toggleDir() { this.dir = this.dir === 'rtl' ? 'ltr' : 'rtl'; localStorage.setItem('reader-dir', this.dir) },
    // Spread on: snap to an even page so pairs stay aligned (0-1, 2-3, …).
    toggleSpread() {
      this.spread = !this.spread
      localStorage.setItem('reader-spread', this.spread ? '1' : '0')
      if (this.spread && this.page % 2 === 1) this.page = this.page - 1
      this.resetZoom()
    },

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
      e.lastTotal = this.pages.length || e.lastTotal || 0
      e.ts = Date.now()   // recencia → "Continuar leyendo"
      this._persistProgress()
    },
    markRead(chapter) {
      if (this.reader?.kind !== 'manga' || chapter == null) return
      const k = this.reader.title
      const e = this.progress[k] || (this.progress[k] = { read: {} })
      ;(e.read ||= {})[String(chapter)] = true
      this._persistProgress()
      this._recordHistory(chapter)
    },
    isChapterRead(chapter) { return !!this.progress[this.current?.id]?.read?.[String(chapter)] },
    toggleChapterRead(chapter) {
      const k = this.current?.id; if (!k) return
      const e = this.progress[k] || (this.progress[k] = { read: {} })
      e.read ||= {}
      if (e.read[String(chapter)]) delete e.read[String(chapter)]; else e.read[String(chapter)] = true
      e.ts = Date.now()
      this._persistProgress()
    },
    // Marca como leídos TODOS los capítulos hasta `chapter` (inclusive) — atajo típico.
    markReadUpTo(chapter) {
      const k = this.current?.id; if (!k) return
      const hi = parseFloat(chapter)
      const e = this.progress[k] || (this.progress[k] = { read: {} })
      e.read ||= {}
      for (const c of this.chapters) {
        const n = parseFloat(c.chapter)
        if (!isNaN(n) && n <= hi) e.read[String(c.chapter)] = true
      }
      e.ts = Date.now(); this._persistProgress()
    },
    // Info para "Continuar" en el manga abierto: dónde se quedó (si hay y no terminó el todo).
    continueInfo() {
      const pr = this.progress[this.current?.id]
      if (!pr?.lastChapter) return null
      return { chapter: pr.lastChapter, page: pr.lastPage || 0, total: pr.lastTotal || 0 }
    },
    resumeCurrent() {
      const info = this.continueInfo()
      if (info) this.read(info.chapter, this.upscaled[info.chapter] ? 'upscaled' : 'auto')
    },
    // Desde el rail "Continuar leyendo": abre el manga y reanuda donde se quedó.
    async resumeManga(item) {
      await this.open(item)
      this.resumeCurrent()
    },
    // Lista para el rail "Continuar leyendo": entradas con progreso, por recencia.
    recentlyRead(limit = 12) {
      return Object.entries(this.progress)
        .filter(([, e]) => e && e.lastChapter != null && e.ts)
        .map(([title, e]) => ({
          title, lastChapter: e.lastChapter, lastPage: e.lastPage || 0,
          total: e.lastTotal || 0, ts: e.ts,
          pct: e.lastTotal ? Math.min(100, Math.round(((e.lastPage + 1) / e.lastTotal) * 100)) : 0,
        }))
        .sort((a, b) => b.ts - a.ts)
        .slice(0, limit)
    },

    /* ── Reading history ──────────────────────────────────────────────── */
    async _recordHistory(chapter) {
      const r = this.reader
      if (!r) return
      const meta = r.onlineMeta
      try {
        await api.post('/api/reader/history/record', {
          title: r.title, chapter,
          cover: r.cover || this.current?.cover || '',
          source: r.source || 'local',
          kind: meta?.kind || 'local',
          chapter_ref: meta?.chapterRef ?? null,
        })
        this.historyLoaded = false
      } catch (_) {}
    },
    async loadHistory() {
      try { this.history = await api.get('/api/reader/history') || [] }
      catch (_) { this.history = [] }
      finally { this.historyLoaded = true }
    },
    async clearHistory() {
      try { await api.post('/api/reader/history/clear', {}); this.history = [] }
      catch (_) { useUiStore().toast('No se pudo limpiar el historial', 'error') }
    },
    // Continue reading from a history entry: 'local' reopens via the normal disk
    // path, 'mangadex'/'source' re-resolves fresh page URLs (the originals are
    // single-use) and opens the reader directly without needing the manga modal.
    async continueHistory(entry) {
      const ui = useUiStore()
      try {
        if (entry.kind === 'local') {
          await this.read(entry.chapter, 'auto', entry.title, entry.cover)
        } else {
          const route = entry.kind === 'source'
            ? `/api/sources/chapter/${entry.chapter_ref}/pages`
            : `/api/mangadex/chapter/${entry.chapter_ref}/pages`
          const pg = await api.get(route)
          const pages = pg.pages || []
          if (!pages.length) { ui.toast('Capítulo sin páginas', 'error'); return }
          this.openOnlineReader(entry.title, entry.chapter, pages, '', { kind: entry.kind, chapterRef: entry.chapter_ref }, entry.cover)
        }
      } catch (_) { ui.toast('No se pudo continuar la lectura', 'error') }
    },
  },
})
