import { defineStore } from 'pinia'
import { api } from '@/lib/api'
import { onStatus } from '@/lib/sse'
import { useUiStore } from './ui'
import { useVersionsStore } from './versions'
import { useRootsStore } from './roots'
import { taskId, pageUrl, pageUrlOriginal, pageUrlUpscaled } from '@/lib/manga'
import { vtGo, marcarTarjeta } from '@/lib/vt'
import { effectiveReaderPrefs, readReaderPrefs, saveReaderPrefs } from '@/lib/readerPrefs'

let statusBound = false
let _progressTimer = 0   // agrupa las subidas del progreso de lectura
let previewTimer = null
let upDoneSeen = new Set()   // upscale ids already refreshed-for (bounded, pruned each SSE tick)
// Activity center bookkeeping (module-level so it survives store re-creation in HMR):
let firstStatusSeen = false  // primed on the first SSE snapshot so we never replay OLD terminal toasts
let tpTermSeen = new Set()   // transplant task ids whose terminal side-effects (toasts/refresh) already fired
let exportTermSeen = new Set() // export ids whose completion auto-download already fired (bounded, pruned each tick)
const chainWriteQueue = new Map() // serializa POST/DELETE por obra para no resucitar una cadena

// Preferred MangaDex chapter language (Spanish-first, then English), or the first
// available — so a freshly opened manga shows one clean language, not all at once.
const LANG_PRIO = ['es-la', 'es', 'en', 'pt-br', 'ja']
// Idiomas de portada que se muestran. MangaDex publica una por tomo y por idioma (80 en Witch Hat
// Atelier): fuera de estos tres, son la misma portada con el logo traducido.
const COVER_LANGS = new Set(['en', 'ja', 'es'])
const coverLangOk = (c) => COVER_LANGS.has(String(c?.locale || '').toLowerCase().split('-')[0])
/* Idiomas con los que se pide el mapa de tomos. `aggregate` sin filtro declara lo publicado en
 * CUALQUIER lengua: por eso el Tomo 1 de Witch Hat decía «5/6» — el que faltaba era un extra en
 * catalán. Con este filtro da los 5 exactos que hay en disco. */
const MD_LANGS = ['en', 'es', 'es-la', 'ja']
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
// Los estados de RESULTADO FINAL deben mapearse a un terminal explícito; si no,
// caen al `return 'running'` de abajo y la tarea "carga eternamente" (nunca sale
// de Activas). download/upscale terminan con outcomes no-obvios (ok, no_chapters,
// nothing_to_repair, already_running, not_found) además de complete/done.
// Estados en los que una descarga SIGUE viva (download.py). La cadena considera terminal todo lo
// demás — al revés (listar los terminales) un estado no previsto como 'no_chapters' la dejaría
// esperando para siempre a un capítulo que ya acabó. Fallo seguro: ante lo desconocido, seguir.
const _DL_ACTIVE = new Set(['starting', 'started', 'downloading', 'cancel_requested'])
const _DONE_LIKE = new Set(['done', 'complete', 'ok', 'nothing_to_repair', 'already_running', 'downloaded', 'no_changes'])
const _ERROR_LIKE = new Set(['error', 'not_found', 'no_chapters', 'failed'])
const _CANCEL_LIKE = new Set(['cancelled', 'canceled', 'interrupted', 'cancelling', 'cancel_requested'])
function _mapStatus(raw) {
  if (_ERROR_LIKE.has(raw)) return 'error'
  if (_CANCEL_LIKE.has(raw)) return 'cancelled'
  if (_DONE_LIKE.has(raw)) return 'done'
  if (raw === 'starting' || raw === 'queued') return 'queued'
  return 'running'   // downloading/upscaling/discovering/running/… = genuinamente en curso
}
// One backend task entry → unified shape, or null to hide it.
function normalizeTask(kind, id, v, actual = null) {
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
      pct: status === 'done' ? 100 : _pct(v.progress, v.total), label: v.volume_name || 'Tomo',
      file: status === 'done', profile: v.profile || '' }
  if (kind === 'translate') {
    // `chapterDone` is the 1-based chapter being WORKED ON (not the count completed), so a
    // single-chapter job would otherwise pin the bar at 100%. Make it page-aware: chapters
    // fully done + the current chapter's page fraction → a smooth bar that mirrors the modal's
    // "pág d/t" detail. `pageDone/pageTotal` only flow during the compose phase.
    const total = v.chapterTotal || 0
    const completed = Math.max(0, (v.chapterDone || 0) - 1)
    // Cada fase ocupa una BANDA dentro del capítulo. Antes sólo `compose` movía la barra, con dos
    // consecuencias feas: durante `download` (que puede tardar minutos) parecía colgada, y al
    // entrar en `rescue` —que va DESPUÉS de componer— la fracción volvía a 0 y la barra
    // RETROCEDÍA. Las bandas son monótonas, así que ya no puede ir hacia atrás.
    const paginas = v.pageTotal ? Math.min(1, (v.pageDone || 0) / v.pageTotal) : 0
    let frac = 0
    if (v.phase === 'download') frac = 0.10
    else if (v.phase === 'compose') frac = 0.15 + 0.70 * paginas
    else if (v.phase === 'rescue') frac = 0.90
    else if (v.phase === 'skip') frac = 1
    let pct = total ? Math.min(100, Math.round((completed + frac) / total * 100)) : 0
    if (status === 'done') pct = 100
    let label
    if (v.phase === 'compose' && v.pageTotal) label = `Cap. ${v.chapter} · pág ${v.pageDone || 0}/${v.pageTotal}`
    else if (v.phase === 'download') label = `Cap. ${v.chapter} · descargando`
    else if (v.phase === 'rescue') label = `Cap. ${v.chapter} · rescatando páginas`
    else if (v.phase === 'start' || v.phase === 'list' || !total) label = 'Preparando…'
    else label = `Cap. ${Math.min(v.chapterDone || 0, total)}/${total}`
    // El trasplante cuenta lo que hace en `note` («12 pág. en inglés → probando Mangas.in»),
    // no en `message`: sin esto la segunda línea del Centro de Actividad salía vacía justo para
    // la tarea más larga de la app.
    return { ...base, msg: v.note || base.msg, mangaId: v.title || '', title: v.title || '',
      chapter: v.chapter, pct, label }
  }
  if (kind === 'versiondl')   // descarga de versión: visualmente una descarga
    return { ...base, kind: 'download', mangaId: v.title || '', title: v.title || '',
      pct: _pct(v.chapterDone, v.chapterTotal), label: v.chapterTotal ? `Versión · ${v.chapterDone || 0}/${v.chapterTotal} cap.` : 'Descarga de versión' }
  if (kind === 'subtitle') {
    // Traducción de subtítulos de anime. El backend ya reporta progress 0-100. Se
    // agrupa aparte de los mangas (clave `anime:<título>` + isAnime) para que el
    // clic NO abra el modal de manga y el icono/portada sean los del anime.
    // Si la tarea pertenece a un LOTE, se suprime: el agregado (`subtitle_batch`) ya la cuenta,
    // y mostrar ambos duplicaría el episodio en curso en el Centro de Actividad.
    if (v.batch_id) return null
    const title = v.title || 'Anime'
    return { ...base, mangaId: `anime:${title}`, title, isAnime: true,
      episode: v.episode, pct: status === 'done' ? 100 : Math.min(100, Math.round(v.progress || 0)),
      label: v.episode != null ? `Ep. ${v.episode} · subtítulos` : 'Subtítulos' }
  }
  if (kind === 'anime_upscale') {
    // Horneado Anime4K de un episodio. Se agrupa bajo `anime:<serie>` igual que los subtítulos,
    // para que use el icono del anime y el clic no abra el modal de manga.
    //
    // El nombre del fichero va en la segunda línea y no en la etiqueta: son cosas como
    // `[SubsPlease] Serie - 07 (1080p) [A1B2C3D4].mkv`, que a lo ancho de una fila se come el resto.
    const title = v.title || 'Anime'
    return { ...base, mangaId: `anime:${title}`, title, isAnime: true,
      msg: v.file || base.msg,
      pct: status === 'done' ? 100 : Math.min(100, Math.round(v.progress || 0)),
      eta: v.eta,
      label: v.quality ? `Escalando a 4K · ${v.quality}` : 'Escalando a 4K' }
  }
  if (kind === 'subtitle_batch') {
    // Lote de subtítulos: UNA tarjeta con el progreso global (5/12), agrupada bajo el mismo
    // `anime:<título>` para que use el icono/portada del anime y no abra el modal de manga.
    //
    // `actual` es la tarea del episodio EN CURSO. Sin ella la barra sólo se movía 8 veces en 40
    // minutos —una por episodio— y entre salto y salto se quedaba clavada sin mensaje ni
    // estimación: justo el rato en el que uno mira. El episodio en curso aporta su fracción y su
    // relato («qwen2.5:14b: 238/558 líneas…»), que ya viajaban en el snapshot pero se tiraban
    // porque la tarea suelta se suprime para no duplicarla.
    const title = v.title || 'Subtítulos'
    const total = v.total || 0
    const done = v.done || 0
    const frac = actual && actual.status !== 'done' ? Math.min(100, actual.progress || 0) / 100 : 0
    return { ...base, mangaId: `anime:${title}`, title, isAnime: true, batchId: id,
      msg: (actual && actual.message) || base.msg,
      pct: total ? Math.min(100, Math.round((done + frac) / total * 100)) : 0,
      label: v.current_episode != null
        ? `Subtítulos en español · ${done}/${total} · ep. ${v.current_episode}`
        : `Subtítulos en español · ${done}/${total}` }
  }
  return null
}
// Agrupa una lista de tareas normalizadas por manga, con portada (si se conoce) y % medio.
function groupByManga(tasks, coverCache) {
  const map = new Map()
  for (const t of tasks) {
    const key = t.mangaId || t.title || t.id
    // `coverCache` sólo se llena al ABRIR un manga, así que una descarga lanzada desde la rejilla
    // salía con la inicial y no con la portada. `/api/library/thumb/<carpeta>` no necesita la
    // biblioteca cargada: para un manga el mangaId ES el nombre de carpeta. Da 404 si no existe,
    // y la plantilla cae a la inicial con @error.
    if (!map.has(key)) map.set(key, { mangaId: key, title: t.title || key,
      cover: coverCache[key] || (t.isAnime ? '' : `/api/library/thumb/${encodeURIComponent(key)}`),
      tasks: [], pct: 0, anyError: false, anyActive: false, isAnime: !!t.isAnime })
    map.get(key).tasks.push(t)
  }
  for (const g of map.values()) {
    g.pct = Math.round(g.tasks.reduce((a, t) => a + (t.pct || 0), 0) / g.tasks.length)
    g.anyError = g.tasks.some(t => t.status === 'error')
    g.anyActive = g.tasks.some(t => t.status === 'running' || t.status === 'queued')
  }
  return [...map.values()]
}

const CHAIN_DEFAULT = { upscale: true, translate: false }

function _loadChain() {
  try { return { ...CHAIN_DEFAULT, ...JSON.parse(localStorage.getItem('manga-chain') || '{}') } }
  catch (_) { return { ...CHAIN_DEFAULT } }
}

function _serializeChainJob(job) {
  const strings = (value) => [...new Set((value instanceof Set ? [...value] : (Array.isArray(value) ? value : []))
    .flatMap(v => { if (v == null) return []; const text = String(v).trim(); return text ? [text] : [] }))]
  const chapters = strings(job?.chapters)
  const values = (value, fallback) => strings(value ?? fallback)
  return {
    title: String(job?.title || ''),
    chapters,
    translatable: values(job?.translatable, chapters).filter(ch => chapters.includes(ch)),
    waiting: values(job?.waiting, chapters).filter(ch => chapters.includes(ch)),
    upscale: !!job?.upscale,
    translate: !!job?.translate,
    stage: job?.stage || 'downloading',
  }
}

function _hydrateChainJob(raw, key = '') {
  const data = _serializeChainJob({ ...raw, title: raw?.title || key })
  if (!data.title || !data.chapters.length) return null
  return { ...data, waiting: new Set(data.waiting) }
}

function _queueChainWrite(title, operation) {
  const previous = chainWriteQueue.get(title) || Promise.resolve()
  const next = previous.catch(() => {}).then(operation)
  chainWriteQueue.set(title, next)
  next.finally(() => {
    if (chainWriteQueue.get(title) === next) chainWriteQueue.delete(title)
  }).catch(() => {})
}

export const useMangaStore = defineStore('manga', {
  state: () => ({
    // detail modal
    current: null,            // { id, name, source_meta } of the open manga
    pendingTab: null,         // pestaña inicial de un solo uso para el próximo open() (p.ej. Descubrir → 'versions')
    chapters: [],             // [{ chapter, page_count }] — local downloaded chapters
    upscaled: {},             // { chapterNorm: true | 'partial' }
    modalLoading: false,
    sourceChapters: [],       // [{ id, chapterNumber, name, scanlator, pageCount }] — from Suwayomi source
    sourceLoading: false,
    mdId: null,               // MangaDex UUID for library manga with MD pairing
    mdChapters: [],           // MangaDex chapters for library manga
    mdChaptersLoading: false,
    mdLang: '',               // language filter for MD chapters ('' = all)
    mdUpdates: { list: [], newCount: 0, loading: false, uuid: null, behindFrom: null, behindTo: null },  // "vas N por detrás" vs MangaDex
    _mdUpdTimer: null,        // debounce de scheduleMdUpdates (no reactivo)
    dlTasks: {},              // { chapterKey: taskId } — maps chapter to SSE download task ID
    upTasks: {},              // { chapterKey: taskId } — maps chapter to SSE upscale task ID
    cancelledIds: [],         // task ids cancelled locally — hidden everywhere until the backend catches up
    dismissedExports: [],     // export task ids dismissed from the queue
    libraryDirty: 0,          // bumped after a manga is deleted so LibraryView reloads
    pendingDelete: [],        // manga ids hidden during the undo window before deletion

    // Recomendados (AniList): por el manga abierto + "Para ti" (de la biblioteca).
    recs: [], recsLoading: false, recsFor: '',
    /* Ficha de la obra (sinopsis, autor, géneros…). `/api/library` sólo devuelve CONTADORES,
       así que un manga descargado no contaba nada de sí mismo mientras uno de Descubrir sí.
       `null` = aún no pedida · `{error}` = falló (que no es lo mismo que «no hay ficha»). */
    workInfo: null, workInfoFor: null,
    forYou: [], forYouLoading: false, forYouLoaded: false,

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
      referenceChapters: null, // cuántos capítulos hay ACTUALMENTE del manga (referencia de completitud)
      referenceSource: '',     // 'fuentes' (máx visto) | 'anilist' (total oficial si terminada)
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
    subtitles: {},            // { taskId: {status, progress, title, episode, ...} } — traducción de subs de anime (modelo)
    subtitleBatches: {},      // { batchId: {status, done, total, title, ...} } — lote de subtítulos (agregado)
    animeUpscale: {},         // { id: {status, progress, title, file, quality, eta} } — horneado Anime4K

    // Centro de Actividad: historial DERIVADO del mismo snapshot (rebanada terminal). No se
    // graba en el cliente — activas e historial son la misma fuente de verdad y sobreviven F5.
    coverCache: {},           // { mangaId: coverUrl } poblado al abrir/listar mangas, para las tarjetas del centro
    hiddenHistoryIds: (() => { try { return JSON.parse(localStorage.getItem('act-hidden') || '[]') } catch { return [] } })(),

    // reader
    reader: null,             // { title, chapter, source, kind } | null  (kind: manga | cbz)
    pages: [],
    page: 0,
    readerLoading: false,
    // Prefetch del capítulo VECINO (solo local): lista de páginas ya resuelta para que el salto
    // "siguiente capítulo" sea instantáneo (sin round-trip). Lo llena `prefetchNextChapter()` al
    // acercarse al final; `read()` lo consume si casa título+capítulo+fuente. Ver Reader.vue.
    _nextPrefetch: { title: null, chapter: null, source: null, pages: null },
    onlineLoadingId: null,    // chapter id currently resolving pages for "Leer" (online), for button spinners
    mode: localStorage.getItem('reader-mode') || 'paged',   // paged | webtoon
    // Desfase de la doble página. Casi todos los tomos empiezan con una portada SUELTA, así que
    // emparejar 0-1, 2-3… deja el resto del capítulo con las dobles páginas partidas por la mitad.
    // MangaDex lo llama "offset double page"; es un clic, no un ajuste que haya que entender.
    spreadOffset: Number(localStorage.getItem('reader-spread-offset') || 0) ? 1 : 0,
    // Ancho de la tira en modo webtoon, en % del ancho de la ventana. Estaba clavado en 900 px:
    // en un monitor grande la tira se quedaba corta y en uno pequeño no cabía.
    webtoonWidth: Number(localStorage.getItem('reader-webtoon-width') || 52),
    // Hueco entre páginas de la tira. Iban PEGADAS: con un capítulo partido en 30 trozos no se
    // distingue dónde acaba uno y empieza el otro.
    webtoonGap: Number(localStorage.getItem('reader-webtoon-gap') ?? 8),
    // Auto-scroll de la tira, en px/s. La velocidad se recuerda; el estado encendido/apagado NO
    // (nadie quiere que un capítulo empiece a moverse solo al abrirlo).
    autoScrollSpeed: Number(localStorage.getItem('reader-autoscroll') || 60),
    // Override manual de modo por serie (título → 'paged'|'webtoon'). Si existe, gana
    // sobre la auto-detección webtoon vs manga; si no, se detecta por aspect-ratio.
    readerModeOverride: JSON.parse(localStorage.getItem('reader-mode-series') || '{}'),
    fit: localStorage.getItem('reader-fit') || 'width',     // width | height | original
    dir: localStorage.getItem('reader-dir') || 'rtl',       // rtl | ltr
    spread: localStorage.getItem('reader-spread') === '1',  // two-page spread (paged manga only)
    zoom: 1.0,
    // Brillo de lectura (modo noche): atenúa el papel blanco del B/N sin tocar el sistema.
    // 1 = original; <1 = más tenue. Invertir = negativo (blanco↔negro) para leer en oscuridad.
    readerBrightness: parseFloat(localStorage.getItem('reader-brightness') || '1') || 1,
    readerInvert: localStorage.getItem('reader-invert') === '1',
    panX: 0,
    panY: 0,
    barsHidden: false,
    compareMode: false,
    compareX: 50,
    // comparador A|B (Versiones): segunda capa de páginas + etiquetas
    scanCompareMode: false,
    comparePages2: [],
    compareLabels: null,        // { left, right } — etiquetas A|B en el comparador (versiones)
    progress: (() => { try { return JSON.parse(localStorage.getItem('manga-progress-v1') || '{}') } catch { return {} } })(),

    // chapter updates (followed manga with new chapters on MangaDex)
    updates: [],                // [{manga_id, title, cover, new_count, new_chapters}]
    updatesLoaded: false,

    // reading history (server-persisted, recorded when a chapter is marked read)
    history: [],
    historyLoaded: false,
    historyError: '',          // una caída del servidor no es un historial vacío

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
      covers: [], coversLoading: false, coversHidden: 0,
      selectedCover: null, coverLoadingId: null, coverB64: '', coverUrl: '',
    },

    // Selector de portada (cambiar la del manga si está corrupta/baja calidad)
    coverPicker: { open: false, loading: false, current: null, anilist: [], mangadex: [], applying: null },
    // Cadena tras descargar. Por defecto escalar (el flujo normal del usuario); traducir es la
    // excepción y se marca a mano. Ver setChain/_reconcileChain.
    chain: _loadChain(),
    // UNA cadena POR MANGA, no una sola global. Era `chainJob: null` y `armChain` la
    // SOBRESCRIBÍA: al lanzar una segunda tanda (o al cambiar de manga) la primera perdía su
    // cadena en silencio y sus capítulos se quedaban descargados y sin escalar para siempre.
    // MEDIDO en Witch Hat Atelier: 20 capítulos descargados, sólo 10 escalados — justo los 10 de
    // la última tanda armada. Ver `armChain` (que ahora FUSIONA) y `chain.spec.js`.
    chainJobs: {},            // { [título]: job }

    // upscale model + mode
    models: {},                 // { key: label }
    modelsColor: {},            // { key: bool } — qué modelos son a color (APISR)
    activeModel: 'eula',
    colorDone: {},              // { chapterNorm: true } — capítulos ya escalados a color (marcador en disco)
    // Selector de páginas a color. mode 'single' = un capítulo (pages[]); mode 'all' = todo el
    // manga agrupado por capítulo (chapters[]). Ambos muestran SOLO páginas a color.
    colorPicker: { open: false, mode: 'single', chapter: null, folder: '', pages: [], chapters: [], loading: false },
    colorModel: localStorage.getItem('upscale-color-model') || '',  // modelo a color elegido (vacío = primero disponible)
    eco: localStorage.getItem('upscale-eco') !== '0',   // eco on by default (lets MPV run)

    // Modo QA de traducción (SOLO testing): conserva artefactos de debug por página al traducir
    // y habilita el botón ⚑ en el lector para marcar páginas mal traducidas. Off por defecto.
    qaMode: localStorage.getItem('tp-qa') === '1',
  }),

  getters: {
    /* ¿Hay CON QUÉ traducir? Traducir exige las dos fuentes elegidas (arte y español).
     * `willTranslate` es la ÚNICA verdad de «esta tanda va a traducir»: el chip ES, el texto del
     * botón y `armChain` la comparten. Antes cada uno la calculaba por su cuenta y `armChain` se
     * quedaba con `chain.translate` a secas, ignorando si había fuentes. Como `chain` se persiste
     * en localStorage, bastaba haber activado ES alguna vez para que, en un manga sin fuentes
     * elegidas, el chip saliera apagado y deshabilitado y el botón dijera «Descargar y escalar»
     * — y aun así saltara «Ninguno de esos capítulos está en las DOS fuentes de traducción».
     * Pedías dos cosas y te contestaba por la tercera. */
    tpReady: (s) => !!(s.tp.artSel && s.tp.esSel),
    willTranslate() { return !!(this.chain.translate && this.tpReady) },

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
    // Progreso del escalado a COLOR por capítulo — se lee de las tareas con flag `color`
    // en el snapshot de upscale (task_id propio `_color`), keyed por número de capítulo.
    colorByChapter: (s) => {
      const out = {}
      for (const v of Object.values(s.upscale)) {
        if (!v || !v.color || v.chapter == null) continue
        const total = v.total || 0, progress = v.progress || 0
        out[String(v.chapter)] = {
          status: v.status || 'upscaling', progress, total,
          pct: total ? Math.min(100, Math.round((progress / total) * 100)) : (v.status === 'complete' ? 100 : 0),
        }
      }
      return out
    },
    sortedChapters: (s) => [...s.chapters].sort((a, b) => parseFloat(b.chapter) - parseFloat(a.chapter)),
    mdLangs: (s) => [...new Set(s.mdChapters.map(c => c.language).filter(Boolean))].sort(),
    // Filas LOCALES (archivos ya descargados), ocultando las que tienen una descarga EN VUELO
    // (para que se vea el anillo de progreso en vez de las páginas a medias). Base compartida
    // por `mergedChapters` (modo navegar) y `collectionChapters` (modo curado) — una sola
    // definición de "qué tengo en disco ahora mismo".
    _localRows: (s) => {
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
      return s.chapters.filter(c => !activeDownloading.has(String(c.chapter)))
    },
    // Merge local + source + MD chapters, showing all available for download (modo NAVEGAR)
    mergedChapters() {
      const s = this
      const localChapters = this._localRows
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
      // Etiqueta ADITIVA de fuente asignada (chapter_sources), solo lectura: no cambia el
      // orden ni qué filas aparecen — permite que la pestaña Capítulos muestre de dónde
      // viene cada capítulo sin tocar la lógica de fusión existente. Copia cada fila (spread)
      // en vez de mutar los objetos compartidos con `s.chapters`/`s.sourceChapters` — mutar
      // estado reactivo ajeno dentro de un getter es lo que impedía que la vista se refrescara
      // de forma fiable al reasignar una fuente.
      let tagged = result
      if (s.current?.id) {
        const vs = useVersionsStore()
        if (vs.title === s.current.id) {
          tagged = result.map((row) => {
            const a = vs.assigned[String(row.chapter)]
            return a ? { ...row, _assignedSource: a, _assignedSourceKind: a.sourceKind, _assignedSourceName: a.sourceName } : row
          })
        }
      }
      return tagged.sort((a, b) => parseFloat(b.chapter) - parseFloat(a.chapter))
    },
    // Lista ÚNICA de la pestaña Capítulos — el "plan de colección" del manga. Dos modos:
    //  · CURADO (el manga tiene ≥1 asignación en chapter_sources): la colección es
    //    EXACTAMENTE `local ∪ asignado`, NADA MÁS. Se descartan por completo los catálogos
    //    sueltos de la fuente activa / MangaDex — un capítulo de otra fuente (p.ej. un 2.2)
    //    NO aparece solo por existir en algún sitio; solo aparece lo que el usuario eligió.
    //  · NAVEGAR (sin asignaciones): comportamiento legado (local + fuente de origen +
    //    MangaDex) intacto — los mangas normales no cambian.
    // Nunca se infiere "la mejor fuente disponible" para huecos ni se comparan números entre
    // fuentes: numeraciones como 1.5/3.2 varían por fuente, no hay tal cosa como un "hueco".
    collectionChapters() {
      const vs = useVersionsStore()
      const curated = !!this.current?.id && vs.title === this.current.id && vs.hasAssignments
      if (!curated) return this.mergedChapters
      // Modo curado: parte de lo local (etiquetado con su fuente si está asignado) y añade una
      // fila por cada capítulo asignado que aún no esté descargado.
      const local = this._localRows
      const seen = new Set(local.map(c => String(c.chapter)))
      const rows = local.map((row) => {
        const a = vs.assigned[String(row.chapter)]
        return a ? { ...row, _assignedSource: a, _assignedSourceKind: a.sourceKind, _assignedSourceName: a.sourceName } : row
      })
      for (const [chn, a] of Object.entries(vs.assigned)) {
        if (seen.has(chn)) continue
        seen.add(chn)
        rows.push({
          chapter: chn, page_count: null, _covMulti: true,
          _assignedSource: a, _assignedSourceKind: a.sourceKind, _assignedSourceName: a.sourceName,
        })
      }
      return rows.sort((a, b) => parseFloat(b.chapter) - parseFloat(a.chapter))
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
    // Fuente efectiva de UN capítulo concreto: si `chapter_sources` (store `versions`) tiene
    // una asignación explícita para ese capítulo, manda (permite mezclar fuentes por rango);
    // si no, cae exactamente al comportamiento legado de `effectiveSource` (fuente única del
    // manga completo) — así ningún manga sin asignaciones por capítulo cambia de comportamiento.
    effectiveSourceForChapter() {
      return (chapterNorm) => {
        if (this.current?.id) {
          const vs = useVersionsStore()
          if (vs.title === this.current.id) {
            const a = vs.sourceForChapter(chapterNorm)
            if (a) return { sourceId: a.sourceId, mangaId: a.mangaId, sourceName: a.sourceName || '', sourceLang: a.sourceLang || '', kind: a.sourceKind, pinned: true }
          }
        }
        return this.effectiveSource
      }
    },
    // Whether we have any external chapters to show (source, MD, or local)
    hasAnyChapters: (s) => s.mergedChapters.length > 0,

    updatesByTitle: (s) => { const m = {}; for (const u of s.updates) m[u.title] = u; return m },

    // chapter navigation within the reader (ascending order).
    // Base = collectionChapters (local ∪ fuente ∪ MangaDex ∪ asignados), no solo lo
    // descargado: así la navegación entre capítulos y la tarjeta de fin ("Siguiente")
    // funcionan IGUAL leyendo online que leyendo descargado. Para un manga puramente
    // local (sin fuentes cargadas) collectionChapters == local → comportamiento intacto.
    chapterListAsc() { return [...this.collectionChapters].sort((a, b) => (parseFloat(a.chapter) || 0) - (parseFloat(b.chapter) || 0)) },
    chapterIndex() { return this.chapterListAsc.findIndex(c => String(c.chapter) === String(this.reader?.chapter)) },
    canPrevChapter() { return this.chapterIndex > 0 },
    canNextChapter() { return this.chapterIndex >= 0 && this.chapterIndex < this.chapterListAsc.length - 1 },

    // Filtro CSS de las páginas (modo noche): brillo + inversión opcional. 'none' cuando no hay
    // ajuste, para no pagar un repintado con filtro en el caso normal. No se aplica al comparador
    // (ahí se inspecciona calidad, el filtro falsearía la comparación).
    pageFilter: (s) => {
      const parts = []
      if (s.readerBrightness !== 1) parts.push(`brightness(${s.readerBrightness})`)
      if (s.readerInvert) parts.push('invert(1) hue-rotate(180deg)')
      return parts.length ? parts.join(' ') : 'none'
    },

    // two-page spread is only meaningful for paged reading without compare/zoom overlap
    spreadActive: (s) => s.spread && s.mode === 'paged' && !s.compareMode,
    // the page(s) currently shown — one index, or a [left,right] pair in spread mode.
    // The pair is rendered right-to-left when dir === 'rtl' (manga order).
    spreadPair() {
      if (!this.spreadActive) return [this.page]
      // Con desfase, la página 0 va SOLA (como la portada de un tomo) y el emparejamiento
      // arranca en la 1. Sin él, el par es siempre par-impar.
      if (this.spreadOffset && this.page === 0) return [0]
      const hasNext = this.page + 1 < this.pages.length
      return hasNext ? [this.page, this.page + 1] : [this.page]
    },

    // compare (original vs upscaled) — only paged manga, chapter has an upscale
    canCompare: (s) => s.reader?.kind === 'manga' && s.mode === 'paged' &&
      (s.reader?.source === 'upscaled' || s.upscaled[s.reader?.chapter] === true || s.upscaled[s.reader?.chapter] === 'partial'),
    // `http(s)` pass-through: las muestras de versiones (pestaña Versiones) son URLs
    // absolutas de Suwayomi, no rutas locales /uploads — no anteponer prefijo.
    // pageUrlOriginal/Upscaled codifican la ruta (carpetas con `?`/espacios rompían la URL) y
    // dejan pasar http(s)/rutas absolutas de las muestras de Versiones (Suwayomi) sin prefijo.
    pageOrigUrl: (s) => {
      const p = s.pages[s.page]; if (!p) return ''
      return pageUrlOriginal(p)
    },
    pageUpUrl: (s) => {
      if (s.scanCompareMode && s.comparePages2.length) {
        const p2 = s.comparePages2[Math.min(s.page, s.comparePages2.length - 1)]
        return p2 ? pageUrlOriginal(p2) : ''
      }
      const p = s.pages[s.page]; if (!p) return ''
      return pageUrlUpscaled(p)
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
      for (const [id, v] of Object.entries(s.subtitles)) {
        const t = normalizeTask('subtitle', id, v); if (t) out.push(t)
      }
      for (const [id, v] of Object.entries(s.animeUpscale)) {
        if (s.cancelledIds.includes(id)) continue
        const t = normalizeTask('anime_upscale', id, v); if (t) out.push(t)
      }
      // El episodio en curso de cada lote, para que la tarjeta del lote pueda contar su avance.
      const enCurso = {}
      for (const v of Object.values(s.subtitles)) {
        if (v && v.batch_id && v.status !== 'done') enCurso[v.batch_id] = v
      }
      for (const [id, v] of Object.entries(s.subtitleBatches)) {
        const t = normalizeTask('subtitle_batch', id, v, enCurso[id]); if (t) out.push(t)
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
      // Reconcilia el progreso de lectura con su copia durable, y no dejes la última página
      // leída sólo en memoria: al ocultar la pestaña o cerrar, se vacía lo pendiente.
      this._hydrateProgress()
      document.addEventListener('visibilitychange', () => { if (document.hidden) this._flushProgress() })
      window.addEventListener('beforeunload', () => this._flushProgress())
      // La variante con traducción necesita conservar su intención aunque se recargue la UI.
      // El backend sólo guarda el workflow; las fuentes siguen siendo las confirmadas por obra.
      this._hydrateChainJobs()
      // Sync the eco toggle (default on) to the backend so MPV-friendly GPU
      // throttling applies from first load, not only after the user toggles it.
      api.post('/api/upscale/mode', { eco: this.eco }).catch(() => {})
      onStatus((data) => {
        this.downloads = data.downloads || {}
        this.upscale = data.upscale || {}
        this.exports = data.exports || {}
        this.transplant = data.transplant || {}
        this.subtitles = data.subtitles || {}
        this.subtitleBatches = data.subtitle_batches || {}
        this.animeUpscale = data.anime_upscale || {}
        // Escalado a color completado → marca el capítulo como "color hecho" (oculta el botón,
        // como el 4K). El marcador en disco (loadColorStatus) es la verdad persistente.
        for (const v of Object.values(this.upscale)) {
          if (v && v.color && v.chapter != null && (v.status === 'complete' || v.status === 'done') && !this.colorDone[String(v.chapter)])
            this.colorDone = { ...this.colorDone, [String(v.chapter)]: true }
        }
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
        // Avanza la cadena descargar→(traducir)→escalar de los capítulos marcados.
        this._reconcileChain()
      })
    },

    // Rebuild dlTasks entries for active (non-terminal) chapter downloads of the current manga
    // from the live `downloads` snapshot. Needed on page-refresh (dlTasks starts empty) and
    // modal re-open after navigating away mid-download (partial pages on disk hide the source
    // chapter in mergedChapters unless dlTasks is current).
    async _hydrateChainJobs() {
      try {
        const saved = await api.get('/api/download/chains')
        const jobs = {}
        for (const [title, raw] of Object.entries(saved || {})) {
          const job = _hydrateChainJob(raw, title)
          if (job) jobs[job.title] = job
        }
        this.chainJobs = { ...jobs, ...this.chainJobs }
        this._reconcileChain()
      } catch (_) { /* el flujo normal no depende de este endpoint */ }
    },
    _persistChainJob(job) {
      const payload = _serializeChainJob(job)
      if (!payload.title || !payload.chapters.length) return
      _queueChainWrite(payload.title, () => api.post('/api/download/chains', payload))
    },
    _removePersistedChain(title) {
      if (!title) return
      _queueChainWrite(title, () => api.del('/api/download/chains', { body: { title } }))
    },
    // ── Cadena: qué pasa DESPUÉS de descargar ────────────────────────────────────────────
    // El flujo real del usuario es descargar+escalar; traducir es la excepción. Se arma al
    // pulsar el botón y avanza sola con el snapshot SSE. La intención se guarda en el backend
    // para que una recarga no convierta una descarga terminada en un capítulo olvidado.
    //
    // El ORDEN no es negociable: traducir reescribe el arte e invalida el 4K, así que si hay
    // traducción se escala DESPUÉS. Hacerlo al revés tira las horas de GPU (ver upscale_cost).
    setChain(patch) {
      this.chain = { ...this.chain, ...patch }
      try { localStorage.setItem('manga-chain', JSON.stringify(this.chain)) } catch (_) {}
    },

    // Traducir un capítulo exige que exista en LAS DOS fuentes elegidas (arte y español). Tener
    // fuentes no basta: medido en "Kono Koi wo Hoshi ni wa Negawanai", el arte (MangaFire/fr)
    // tenía 4 capítulos y el ES 8 distintos → sólo el 1 en común. Armar la traducción del 19
    // hacía que el backend la ejecutase, no encontrase el capítulo en el arte y devolviese
    // `status: done` con `note: "falta capítulo en arte"` — o sea, un éxito de mentira que se
    // parecía a "la cadena se saltó la traducción".
    async translatableAmong(chapters) {
      try {
        const d = await api.get(`/api/transplant/chapters/${encodeURIComponent(this.current.id)}`)
        const ok = new Set((d.chapters || []).map(c => String(c.chapter)))
        return chapters.filter(c => ok.has(String(c)))
      } catch (_) {
        return null            // no se pudo saber ≠ no hay ninguno: que decida quien llama
      }
    },

    async armChain(chapters) {
      const ui = useUiStore()
      const title = this.current?.id
      const { upscale } = this.chain
      let translate = this.willTranslate   // NO `chain.translate`: ver el getter
      const list = chapters.map(String)
      // Sin capítulos nuevos NO se toca la cadena viva: antes ponía `chainJob = null` y bastaba
      // un "Descargar" sobre una selección ya descargada para cargarse la cadena en marcha.
      if (!title || !list.length) return

      let translatable = list
      if (translate) {
        const ok = await this.translatableAmong(list)
        if (ok === null) {
          translate = false
          ui.toast('No se pudo comprobar qué capítulos son traducibles: se descargará y escalará sin traducir', 'error', 7000)
        } else if (!ok.length) {
          translate = false
          ui.toast('Ninguno de esos capítulos está en las DOS fuentes de traducción (arte y español), '
                   + 'así que no se puede traducir. Se descargará y escalará.', 'error', 9000)
        } else {
          translatable = ok
          if (ok.length < list.length) {
            ui.toast(`Sólo ${ok.length} de ${list.length} se pueden traducir; el resto sólo se escalará.`, 'info', 7000)
          }
        }
      }
      if (!upscale && !translate) {
        delete this.chainJobs[title]
        this._removePersistedChain(title)
        return
      }

      // FUSIÓN, no reemplazo. Una segunda tanda sobre el mismo manga amplía la cadena viva en vez
      // de tirarla. Sólo se fusiona si sigue en fase de descarga: si ya está traduciendo, sus
      // capítulos van camino de `tpRun` y meter otros a mitad los dejaría fuera de esa llamada.
      const prev = this.chainJobs[title]
      if (prev && prev.stage === 'downloading') {
        for (const ch of list) { if (!prev.chapters.includes(ch)) prev.chapters.push(ch); prev.waiting.add(ch) }
        prev.translatable = [...new Set([...(prev.translatable || []), ...translatable])]
        prev.upscale = prev.upscale || upscale
        prev.translate = prev.translate || translate
        this._persistChainJob(prev)
        return
      }
      const job = {
        title,
        chapters: list,
        translatable,
        waiting: new Set(list),
        upscale, translate, stage: 'downloading',
      }
      this.chainJobs[title] = job
      this._persistChainJob(job)
    },

    // Fases de un capítulo dentro de la cadena, para pintarlas. Devuelve null si no está en
    // ninguna cadena. `at` = la fase en curso; los pasos que ya pasaron van 'done'.
    // Existe porque el usuario no podía ver QUÉ le faltaba a un capítulo: lanzabas
    // descargar+traducir+escalar y sólo veías la descarga, sin saber si lo demás vendría o no.
    chainStepsFor(chapter) {
      const j = this.chainJobs[this.current?.id]
      const ch = String(chapter)
      if (!j || !j.chapters.includes(ch)) return null
      const willTranslate = j.translate && (j.translatable || []).includes(ch)
      const steps = [{ k: 'dl', label: 'Descargar' }]
      if (willTranslate) steps.push({ k: 'es', label: 'Traducir' })
      if (j.upscale) steps.push({ k: '4k', label: 'Escalar 4K' })
      let at = 'dl'
      if (j.stage === 'translating') at = willTranslate ? 'es' : '4k'
      else if (!j.waiting.has(ch)) at = willTranslate ? 'es' : '4k'
      return { steps, at }
    },

    // Recorre TODAS las cadenas vivas: dos mangas pueden estar encadenando a la vez.
    _reconcileChain() {
      for (const j of Object.values(this.chainJobs)) this._reconcileChainJob(j)
    },

    _startChainTranslation(j, chapters) {
      j.stage = 'translating'
      this._persistChainJob(j)
      Promise.resolve(this.tpRun(chapters)).then((ok) => {
        if (ok === false && this.chainJobs[j.title] === j && j.stage === 'translating') {
          j.stage = 'ready_to_translate'
          this._persistChainJob(j)
        }
      }).catch(() => {
        if (this.chainJobs[j.title] === j && j.stage === 'translating') {
          j.stage = 'ready_to_translate'
          this._persistChainJob(j)
        }
      })
    },

    _reconcileChainJob(j) {
      if (!j) return
      // Si todos los capítulos acabaron mientras la página estaba cerrada, no podemos ejecutar
      // `tpRun` a ciegas: necesita el manga abierto y sus dos fuentes confirmadas. Se queda en
      // este estado durable y se retoma al abrir la obra.
      if (j.stage === 'ready_to_translate') {
        if (this.current?.id !== j.title || !this.tp.artSel || !this.tp.esSel) return
        this._startChainTranslation(j, (j.translatable || []).filter(c => j.chapters.includes(c)))
        return
      }
      if (j.stage !== 'downloading') return
      let changed = false
      for (const v of Object.values(this.downloads)) {
        if (v.title !== j.title) continue
        const ch = String(v.chapter || '')
        if (!ch || !j.waiting.has(ch)) continue
        if (_DL_ACTIVE.has(v.status)) continue      // sigue en marcha: esperar
        // OJO: las descargas terminan en 'complete', NO en 'done' (download.py). Comparar con
        // 'done' a pelo hace que la cadena no salte JAMÁS y, peor, que cada descarga buena
        // caiga en la rama de fallo. `_DONE_LIKE` es el criterio del repo para "acabó bien".
        j.waiting.delete(ch)
        changed = true
        if (_DONE_LIKE.has(v.status)) {
          // Sin traducción de por medio se escala YA, capítulo a capítulo: la GPU trabaja
          // mientras el resto sigue bajando. El worker GPU es una cola única, así que
          // encolarlos según llegan no compite con nada.
          if (!j.translate && j.upscale) {
            const opts = { silent: true }
            if (this.current?.id !== j.title) opts.title = j.title
            this.upscaleChapter(ch, opts)
          }
        } else {
          j.chapters = j.chapters.filter(x => x !== ch)   // lo que no bajó no se encadena
        }
      }
      if (changed) this._persistChainJob(j)
      if (j.waiting.size) return
      if (!j.chapters.length) { delete this.chainJobs[j.title]; this._removePersistedChain(j.title); return }
      // Sólo se manda a traducir lo que EXISTE en ambas fuentes y además se descargó.
      const tr = (j.translatable || []).filter(c => j.chapters.includes(c))
      if (j.translate && tr.length) {
        if (this.current?.id !== j.title || !this.tp.artSel || !this.tp.esSel) {
          j.stage = 'ready_to_translate'
          this._persistChainJob(j)
          return
        }
        this._startChainTranslation(j, tr)
      } else if (j.translate && j.upscale) {
        this.upscaleChapters(j.chapters)
        delete this.chainJobs[j.title]
        this._removePersistedChain(j.title)
      } else {
        delete this.chainJobs[j.title]   // los escalados ya se lanzaron uno a uno
        this._removePersistedChain(j.title)
      }
    },

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
      // Cierre de la cadena: el 4K va DESPUÉS de traducir, nunca antes.
      const j = this.chainJobs[st.title]
      if (j && j.stage === 'translating') {
        delete this.chainJobs[st.title]
        this._removePersistedChain(st.title)
        if (j.upscale && st.status === 'done') this.upscaleChapters(j.chapters)
      }
      const n = (st.chapters || []).length
      if (st.status === 'cancelled') ui.toast('Traducción detenida', 'info')
      else if (st.status === 'error') ui.toast(`Falló la traducción${st.note ? `: ${st.note}` : ''}`, 'error')
      // El backend puede terminar en 'done' habiendo compuesto CERO capítulos y con el motivo en
      // `note` (p.ej. "falta capítulo en arte"). Sin enseñarlo, un fallo real se ve igual que un
      // éxito silencioso — que es justo lo que hizo pensar que la cadena se saltaba la traducción.
      else ui.toast(n ? `Listo: ${n} capítulo(s) en español`
                      : `No se tradujo nada${st.note ? `: ${st.note}` : ''}`, n ? 'ok' : 'error', n ? 4000 : 8000)
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
      this.saveExportFile(id, v)
    },
    // El tomo se GUARDA en la carpeta de tomos desde el backend, no se «descarga»: un <a download>
    // abre el panel de descargas de WebView2 (el de un navegador, con su lista y su «Abrir
    // archivo»), que es justo lo que no queremos ver en una app de escritorio. El archivo ya está
    // en disco; el backend sólo lo mueve. Si eso fallara, cae a la descarga clásica: perder un
    // tomo recién generado por un fallo al mover sería mucho peor que una ventanita fea.
    async saveExportFile(id, v = {}) {
      const ui = useUiStore()
      const nombre = v.volume_name || v.title || 'Tomo'
      try {
        const d = await api.post(`/api/export/save/${encodeURIComponent(id)}`, {})
        if (d?.error) throw new Error(d.error)
        ui.toast(`✓ ${nombre} guardado en ${d.display || d.dir}`, 'ok', 6000,
          { label: 'Abrir carpeta', fn: () => api.post('/api/export/reveal', {}).catch(() => {}) })
      } catch (_) {
        this.downloadExportFile(id)
        ui.toast(`✓ Tomo listo: ${nombre}`, 'ok')
      }
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
      /* El bloque SÍNCRONO va dentro de `vtGo`: el póster de la tarjeta vuela hasta la portada
         del modal, igual que al abrir un anime. Sólo lo síncrono — meter aquí los `await` de red
         congelaría el fotograma durante segundos, porque `startViewTransition` pausa el render
         hasta que su callback termina. */
      marcarTarjeta(manga?.id || manga?.name)   // para señalarla al volver a la rejilla
      await vtGo(() => this._openSync(manga, opts))
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
        // Novedades MangaDex en 2º plano: no bloquea la apertura del modal. Se programa (debounce)
        // para calcular el ALCANCE `have` una vez que la sección de capítulos (fuente/MangaDex,
        // async) esté cargada — si no, `have` saldría incompleto y marcaría de más.
        if (!manga.trackedOnly) this.scheduleMdUpdates()
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
            .finally(() => { this.mdChaptersLoading = false; if (!manga.trackedOnly) this.scheduleMdUpdates() })
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
        // Una cadena con traducción que terminó de descargar sin la pestaña abierta queda lista
        // aquí, después de reconstruir las fuentes confirmadas del manga.
        this._reconcileChain()
      }
    },

    _openSync(manga, opts = {}) {
      this.current = {
        id: manga.id || manga.name, name: manga.name, cover: manga.cover, source_meta: manga.source_meta,
        mdOnly: !!manga.mdOnly, trackedOnly: !!manga.trackedOnly,
        // al_id (AniList) alimenta el descubrimiento de fuentes de Cobertura/Versiones. Antes se
        // perdía al abrir → coverage iba solo por título; para Obras de Descubrir traemos el id
        // real (vía MangaBaka), así el ranking de «mejor versión» arranca con mejor información.
        al_id: manga.al_id || null,
        trackedId: manga.trackedId || manga.mdId || null, status: manga.status || '',
      }
      if (this.current.cover) this.coverCache[this.current.id] = this.current.cover
      this.chapters = []
      this.sourceChapters = []
      this.mdChapters = []
      this.mdId = manga.mdId || null
      this.mdLang = ''
      this.mdUpdates = { list: [], newCount: 0, loading: false, uuid: null, behindFrom: null, behindTo: null }
      this.upscaled = {}
      this.modalLoading = true
      // Ciclo de vida del store `versions` (plan de colección): lo gestiona SOLO aquí, en la
      // apertura del manga — NO el watch del modal (que antes hacía `vg.reset()` y borraba en
      // carrera el mapa recién cargado, dejando la selección invisible). `resetForManga` limpia
      // lo transitorio de Cobertura y fija `title`; `loadCoverageCached` repuebla en UNA sola
      // consulta tanto la asignación persistida (chapter_sources) como el último resultado de
      // cobertura guardado en disco → el grid aparece al instante sin recalcular (solo
      // "Recalcular" re-mide). Si no hay cobertura cacheada, igual trae el mapa de asignación.
      const _vs = useVersionsStore()
      _vs.resetForManga(this.current.id)
      _vs.loadCoverageCached(this.current.id, this.current.source_meta?.sourceLang || '')
      // Push a history entry so browser back closes the modal and forward reopens it.
      // Skip when we're re-opening *because of* a back/forward (fromHistory).
      if (!opts.fromHistory) useUiStore().pushNav()
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
          .finally(() => { this.mdChaptersLoading = false; this.scheduleMdUpdates() })
        return
      }
      this.sourceLoading = true
      const srcMangaId = src.mangaId
      const mangaKey = this.current?.id
      const applyChapters = (chs) => {
        this.sourceChapters = (chs || []).map(ch => ({
          ...ch,
          chapterNorm: String(ch.chapterNumber ?? '').replace(/\.0$/, ''),
          name: ch.name || `Cap. ${ch.chapterNumber ?? '?'}`,
        }))
        // La sección ya conoce el alcance real de la fuente → recalcula "por detrás" con ese `have`.
        this.scheduleMdUpdates()
      }
      // SWR: el backend sirve la DB local de Suwayomi al instante (como MangaDex) y refresca
      // la web de la fuente en 2º plano. `?meta=1` → {chapters, stale}. Si vino "stale" (había
      // un refresco pendiente), re-consultamos UNA vez a los pocos segundos para recoger los
      // capítulos nuevos sin bloquear la apertura, solo si el modal sigue en este mismo manga.
      // Pasamos fuente+título para que el backend pueda RE-RESOLVER el mangaId si quedó
      // obsoleto (la DB de Suwayomi reasigna ids al reconstruirse → los caps no cargaban).
      const q = new URLSearchParams({ meta: '1' })
      if (src.sourceId) q.set('sourceId', String(src.sourceId))
      const srcTitle = this.current?.source_meta?.title || this.current?.name || ''
      if (srcTitle) q.set('title', srcTitle)
      // url estable (slug de la fuente): ancla el match exacto si el mangaId numérico derivó.
      const sm2 = this.current?.source_meta
      const srcUrl = (src.pinned ? sm2?.recommended_source?.url : sm2?.mangaUrl) || ''
      if (srcUrl) q.set('url', srcUrl)
      api.get(`/api/sources/manga/${srcMangaId}/chapters?${q.toString()}`)
        .then(res => {
          applyChapters(res?.chapters)
          // Si el backend re-resolvió a un id nuevo, actualiza source_meta para que lecturas/
          // descargas (effectiveSource deriva de source_meta.mangaId) usen ya el id correcto.
          if (res?.resolvedId && this.current?.id === mangaKey && this.current?.source_meta) {
            this.current.source_meta = { ...this.current.source_meta, mangaId: res.resolvedId }
          }
          if (res?.stale) {
            setTimeout(() => {
              if (this.current?.id !== mangaKey || this.effectiveSource?.mangaId !== srcMangaId) return
              api.get(`/api/sources/manga/${srcMangaId}/chapters?meta=1`)
                .then(r2 => { if (this.current?.id === mangaKey) applyChapters(r2?.chapters) })
                .catch(() => {})
            }, 6000)
          }
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
        // Arte = mis páginas ya descargadas+escaladas (en vez de buscar fuente externa). Se
        // fuerza en importados (CBZ: no hay fuente remota); opcional para descargados con
        // capítulos locales. Recuerda la elección del último discover si la hubo.
        artLocal: meta?.art?.local ?? !!this.current?.source_meta?.imported,
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

    // Cambia el origen del arte (local escalado ↔ fuente externa). Si ya se habían descubierto
    // fuentes, re-descubre para aplicar el cambio; si aún no, solo fija el flag (el botón
    // "Buscar" lo usará). No hace nada durante un discover/run en curso.
    tpSetArtLocal(val) {
      if (this.tp.loading || this.tp.running) return
      this.tp.artLocal = !!val
      if (this.tp.phase === 'ready') this.tpDiscover()
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
          // Importados: siempre arte local. Descargados: según el toggle del usuario.
          artLocal: !!this.current?.source_meta?.imported || !!this.tp.artLocal,
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

    // Traducir reescribe el arte, así que el backend invalida (borra) el capítulo ya escalado a
    // 4K y hay que re-escalarlo desde cero. Pasaba en silencio: se podían tirar horas de GPU sin
    // enterarse. Se avisa ANTES, con la cuenta real de páginas que se van a perder.
    async _tpConfirmUpscaleLoss(chapters) {
      let cost
      try {
        cost = await api.post('/api/transplant/upscale_cost', { title: this.current.id, chapters: chapters || 'all' })
      } catch (_) {
        return true       // si no se puede contar, no bloqueamos la traducción por el aviso
      }
      if (!cost?.pages) return true
      const chs = Object.keys(cost.byChapter || {}).sort()
      const lista = chs.length > 6 ? `${chs.slice(0, 6).join(', ')}… (+${chs.length - 6})` : chs.join(', ')
      return useUiStore().confirm({
        title: 'Se pierde trabajo de 4K', danger: true, confirmLabel: 'Traducir igualmente',
        body: `Esto va a invalidar ${cost.pages} página(s) ya escaladas a 4K en ${cost.chapters} capítulo(s).\n` +
              `${lista}\n` +
              'Al traducir cambia el arte, así que el 4K deja de servir y habrá que volver a escalar ' +
              '(horas de GPU). Lo recomendable es traducir primero y escalar después.',
      })
    },

    async tpRun(chapters) {
      const ui = useUiStore(); const t = this.tp
      if (!t.artSel || !t.esSel) { ui.toast('Elige fuente de arte y de español', 'error'); return false }
      if (!await this._tpConfirmUpscaleLoss(chapters)) return false
      await this._tpConfirm()
      try {
        const res = await api.post('/api/transplant/run', { title: this.current.id, chapters: chapters || 'all', qa: this.qaMode })
        t.running = true
        t.taskId = res.task_id
        t.runStatus = { phase: 'start', chapterTotal: (res.chapters || []).length }
        ui.toast(`Traduciendo ${(res.chapters || []).length} capítulo(s)…`, 'info')
        // Progreso + terminación llegan por el snapshot SSE (_reconcileTransplant); sin polling.
        return true
      } catch (e) {
        ui.toast(e?.body?.includes('no chapters') ? 'No hay capítulos comunes a ambas fuentes' : 'No se pudo iniciar', 'error')
        return false
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
            v.referenceChapters = st.referenceChapters ?? null
            v.referenceSource = st.referenceSource || ''
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
      v.referenceChapters = null; v.referenceSource = ''
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
        sourceLang: cand.sourceLang, url: cand.url, quality: cand.quality,
      } : null)
      try {
        // Pasa la portada (y al_id) que ya conoce la ficha: fijar crea la carpeta del manga aunque
        // no esté descargado; sin esto la tarjeta salía SIN portada en la biblioteca.
        const d = await api.post('/api/transplant/set_primary', {
          title: this.current.id, source,
          cover: this.current?.cover || null, al_id: this.current?.al_id || null,
        })
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

    async downloadSourceChapter(ch, opts = {}) {
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
          ...(opts.chainUpscale ? {
            chain_upscale: true,
            chain_eco: opts.chainEco ?? this.eco,
            chain_fast: opts.chainFast ?? false,
          } : {}),
          ...(useRootsStore().target ? { root: useRootsStore().target } : {}),
        })
        // Use real task_id if different from estimated
        if (res.task_id) this.dlTasks[chKey] = res.task_id
      } catch (_) { ui.toast('No se pudo descargar', 'error'); delete this.dlTasks[chKey] }
    },

    async downloadMdChapter(ch, opts = {}) {
      const ui = useUiStore()
      const cid = ch._mdChapterId
      if (!cid) return
      const chKey = String(ch.chapter)
      this.dlTasks[chKey] = taskId(this.current.id, chKey, 'download')
      try {
        const res = await api.post('/api/download/download_chapter', {
          title: this.current.id, chapter: ch.chapter, chapterId: cid, mangaId: this.mdId,
          ...(opts.chainUpscale ? {
            chain_upscale: true,
            chain_eco: opts.chainEco ?? this.eco,
            chain_fast: opts.chainFast ?? false,
          } : {}),
          ...(useRootsStore().target ? { root: useRootsStore().target } : {}),
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
      const title = opts.title || this.current?.id
      if (!title) return false
      const chKey = String(chapter)
      if (this.current?.id === title) this.upTasks[chKey] = taskId(title, chKey, 'upscale')
      try {
        const res = await api.post('/api/upscale/upscale_chapter', {
          title, chapter, eco: opts.eco ?? this.eco, fast: opts.fast ?? false,
          ...(opts.excludePages?.length ? { exclude_pages: opts.excludePages } : {}),
          // ⚠️ AQUÍ NO va la carpeta elegida. El selector dice dónde caen las DESCARGAS; el
          // escalado no descarga nada: lee páginas que ya existen y escribe el 4K al lado de
          // cada original (nunca cruza de disco). Mandarlo rompía el escalado con un 404
          // «Folder not found» en cuanto el disco elegido no era el de la obra.
        })
        // Use the backend's real task id so the progress ring matches SSE keys
        // even if the optimistic id normalization differs.
        if (res?.task_id && this.current?.id === title) this.upTasks[chKey] = res.task_id
        if (!opts.silent) useUiStore().toast(`Escalando 4K · cap. ${chapter}`, 'info')
        return true
      } catch (_) {
        if (!opts.silent) useUiStore().toast('No se pudo iniciar el escalado', 'error')
        if (this.current?.id === title) delete this.upTasks[chKey]
        return false
      }
    },
    async loadUpdates() {
      try { this.updates = await api.get('/api/mangadex/updates') || [] } catch (_) { this.updates = [] }
      finally { this.updatesLoaded = true }
    },
    // ── Recomendados (AniList) ─────────────────────────────────────────────────
    // Por el manga que estás viendo. Se resuelve el al_id por título en el backend
    // (cacheado 24h). Idempotente por título para no re-pedir al reabrir el mismo.
    // Ficha AniList del manga abierto. Cacheada 24 h en el backend; aquí sólo se evita repetir
    // la petición del mismo id. Sin al_id no hay a quién preguntar: se queda muda, sin error.
    async loadWorkInfo(alId, mdId = null) {
      if (!alId) { this.workInfo = null; this.workInfoFor = null; return }
      const key = `${alId}|${mdId || ''}`
      if (this.workInfoFor === key) return
      this.workInfoFor = key
      this.workInfo = null
      try { this.workInfo = await api.get(`/api/anilist/manga/${alId}${mdId ? `?md=${mdId}` : ''}`) }
      catch (e) { this.workInfo = { error: e?.body?.error || e?.message || 'No se pudo cargar la ficha' } }
    },

    async loadRecs(title) {
      const t = (title || '').trim()
      if (!t) { this.recs = []; return }
      if (this.recsFor === t && (this.recs.length || this.recsLoading)) return
      this.recsFor = t; this.recs = []; this.recsLoading = true
      try { this.recs = await api.get(`/api/anilist/manga/recommendations?title=${encodeURIComponent(t)}`) || [] }
      catch (_) { this.recs = [] }
      finally { this.recsLoading = false }
    },
    // "Para ti": agrega recomendaciones de tu biblioteca (backend cacheado 24h).
    async loadForYou(force = false) {
      if (this.forYouLoaded && !force && this.forYou.length) return
      this.forYouLoading = true
      try { this.forYou = await api.get('/api/anilist/manga/for_you') || [] }
      catch (_) { this.forYou = [] }
      finally { this.forYouLoading = false; this.forYouLoaded = true }
    },
    // Clic en una recomendación → buscarla en Explorar (MangaDex) para descubrir/añadir.
    // Abre una obra (de Descubrir, o una recomendación/"similar") en el MangaModal como punto
    // de convergencia: sembrada por título + al_id + portada, aterriza en Versiones y calcula la
    // «mejor versión». Reutiliza TODA la maquinaria del modal (cobertura, capítulos, leer,
    // descargar) sin duplicarla. Fase 2 de [[project_manga_hub_discovery]].
    async openFromWork(work, opts = {}) {
      if (!work) return
      const title = work.title || work.name || ''
      if (!title) return
      const anilist = (work.ids && work.ids.anilist) || work.al_id || null
      this.pendingTab = 'versions'
      await this.open({
        id: title, name: title, cover: work.cover || null,
        al_id: anilist, trackedOnly: true,          // no está descargado → salta la carga local
      }, opts)
      // Propone la mejor fuente por defecto: calcula cobertura si no hay ya un ranking cargado.
      try {
        const vs = useVersionsStore()
        if (!vs.sources.length) vs.fetchCoverage(title, anilist, false, '')
      } catch (_) { /* si Suwayomi está offline, la ficha sigue mostrando metadatos */ }
    },

    // Clic en una recomendación / "similar" → a la ficha centralizada (no al buscador de fuentes).
    async discoverRec(rec) {
      const title = rec?.title || rec?.title_romaji || rec?.name || ''
      if (!title) return
      await this.openFromWork({
        title, cover: rec.cover || rec.coverImage || null,
        ids: { anilist: rec.al_id || rec.anilist_id || rec.id || null },
      })
    },
    async loadModels() {
      try { const d = await api.get('/api/upscale/models'); this.models = d.models || {}; this.modelsColor = d.color || {}; this.activeModel = d.active || 'eula' } catch (_) {}
    },
    async setModel(key) {
      try {
        const d = await api.post('/api/upscale/set_model', { model: key })
        if (d.status === 'ok') { this.activeModel = key; useUiStore().toast(`Modelo: ${d.label}`, 'ok') }
        else useUiStore().toast(d.message || 'Error al cambiar modelo', 'error')
      } catch (_) { useUiStore().toast('Error al cambiar modelo', 'error') }
    },
    setEco(v) { this.eco = v; localStorage.setItem('upscale-eco', v ? '1' : '0'); api.post('/api/upscale/mode', { eco: v }).catch(() => {}) },
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
      this._forgetRemote({ chapters: { [mangaId]: [String(chapter)] } })
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
      if (this.progress[title]) {
        delete this.progress[title]; this._persistProgress()
        this._forgetRemote({ titles: [title] })
      }
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

    // Borra varias obras desde la rejilla sin abrir N modales ni hacer N recargas de la biblioteca.
    // Las novelas no llegan aquí: tienen su propio catálogo y no comparten el endpoint de manga.
    async deleteMangasBatch(items) {
      const targets = [...new Map((items || []).map((item) => {
        if (!item || item.kind === 'novel') return [null, null]
        const title = String(item.id || item.name || '').trim()
        if (!title) return [null, null]
        return [title, {
          title,
          name: String(item.name || title),
          trackedId: item.trackedId || item.mdId || null,
        }]
      }).filter(([title]) => title))].map(([, target]) => target)
      if (!targets.length) return false

      const ui = useUiStore()
      const n = targets.length
      if (!await ui.confirm({
        title: `¿Quitar ${n} manga${n === 1 ? '' : 's'}?`,
        body: `Se eliminarán sus archivos descargados y su seguimiento de la biblioteca.\n` +
              'Las obras se ocultarán ahora y tendrás 6 segundos para deshacerlo.',
        confirmLabel: `Quitar ${n === 1 ? 'manga' : 'mangas'}`,
        danger: true,
      })) return false

      const titles = targets.map(({ title }) => title)
      const withProgress = titles.filter(title => this.progress[title])
      for (const title of withProgress) delete this.progress[title]
      if (withProgress.length) {
        this._persistProgress()
        this._forgetRemote({ titles: withProgress })
      }
      this.pendingDelete = [...new Set([...this.pendingDelete, ...titles])]
      this.libraryDirty++

      let undone = false
      ui.toast(`${n} manga${n === 1 ? '' : 's'} eliminado${n === 1 ? '' : 's'}`, 'info', 6000, {
        label: 'Deshacer',
        fn: () => {
          undone = true
          this.pendingDelete = this.pendingDelete.filter(title => !titles.includes(title))
          this.libraryDirty++
        },
      })

      setTimeout(async () => {
        if (undone) return
        const failed = []
        // Secuencial a propósito: cada endpoint purga local_library.json y las escrituras deben
        // conservar el resultado de la anterior. Sólo se evita la recarga repetida de la UI.
        for (const target of targets) {
          try {
            await api.del('/api/download/delete_manga', {
              body: { title: target.title, trackedId: target.trackedId },
            })
          } catch (_) {
            failed.push(target.name)
          }
        }
        this.pendingDelete = this.pendingDelete.filter(title => !titles.includes(title))
        if (failed.length) {
          const shown = failed.slice(0, 2).join(', ')
          const extra = failed.length > 2 ? ` y ${failed.length - 2} más` : ''
          ui.toast(`No se pudo quitar: ${shown}${extra}`, 'error')
        }
        this.libraryDirty++
      }, 6000)
      return true
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
      if (t.kind === 'subtitle') { api.post(`/api/subtitle/cancel/${t.id}`).catch(() => {}); return }
      if (t.kind === 'anime_upscale') {
        // Cancelar el horneado mata ffmpeg y borra el fichero a medias; el `.parcial` de 1 GB no
        // se queda por ahí. Se marca ya para que la fila desaparezca sin esperar al siguiente
        // retrato, que puede tardar un par de segundos.
        this._markCancelled(t.id)
        api.post('/api/anime/upscale/cancel', { id: t.id }).catch(() => {})
        return
      }
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
    async retryExport(id) {
      try {
        const d = await api.post(`/api/export/retry/${encodeURIComponent(id)}`, {})
        if (d?.task_id) { useUiStore().toast('Exportación reencolada', 'info'); return d.task_id }
      } catch (e) { useUiStore().toast(e?.message || 'No se pudo reintentar la exportación', 'error') }
      return null
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
    /* Metadatos para el ComicInfo.xml del CBZ. Salen de la ficha de AniList que el modal ya tiene
     * cargada — no se pide nada extra. Sin ficha, se manda sólo lo que sí se sabe (serie y tomo):
     * un campo vacío en ComicInfo es peor que ausente, el lector deja de buscarlo por su cuenta. */
    _comicMeta(volumeName) {
      const i = this.workInfo && !this.workInfo.error ? this.workInfo : null
      const m = { series: this.current?.name || this.current?.id }
      const n = String(volumeName || '').match(/(\d+)\s*$/)
      if (n) m.volume = n[1]
      if (this.mdex.volumes?.length) m.count = this.mdex.volumes.length
      if (i?.authors?.length) m.authors = i.authors
      if (i?.genres?.length) m.genres = i.genres
      if (i?.synopsis) m.summary = i.synopsis
      if (i?.years) m.year = String(i.years).slice(0, 4)
      if (i?.url) m.web = i.url
      return m
    },
    async exportTomo({ chapters, volumeName, format = 'cbz', quality = 92, codec = 'jpeg', downscaleHalf = false, coverB64 = '', profile = 'manual', toDrive = false }) {
      const body = {
        title: this.current.id, chapters, volume_name: volumeName || this.current.name,
        meta: this._comicMeta(volumeName || this.current.name),
        format, quality, codec, profile, downscale_half: downscaleHalf, ...(coverB64 ? { cover_data: coverB64 } : {}),
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
    // ── Acciones por lote (casillas de la lista de Capítulos) ──────────────────────
    // Escala a 4K una lista EXPLÍCITA de capítulos (los que el usuario marcó). Solo tienen
    // sentido los capítulos LOCALES que no estén ya en 4K; el resto se ignora en silencio.
    async upscaleChapters(chapters, opts = {}) {
      const ui = useUiStore()
      const local = new Set(this.chapters.map(c => String(c.chapter)))
      const targets = [...new Set(chapters.map(String))].filter(ch => local.has(ch) && this.upscaled[ch] !== true)
      if (!targets.length) { ui.toast('Nada que escalar en la selección (deben ser capítulos descargados y sin 4K)', 'info'); return }
      // Reutiliza EXACTAMENTE el flujo individual (/upscale_chapter): filtra por
      // ch_prefix y crea un task_id por capítulo → progreso real en la lista Y en
      // Actividad. El endpoint /upscale_manga ignoraba `chapters` (globeaba TODA la
      // carpeta bajo un task 'all') por eso el lote "no funcionaba". Silencioso por
      // capítulo (un solo toast resumen al final); el worker GPU es una cola única, así
      // que lanzarlos en ráfaga es equivalente a pulsar el botón individual N veces.
      let started = 0
      for (const ch of targets) {
        if (await this.upscaleChapter(ch, { ...opts, silent: true })) started++
      }
      if (started) ui.toast(`Escalando ${started} capítulo(s) a 4K`, 'info')
      else ui.toast('No se pudo iniciar el escalado', 'error')
    },
    // Descarga una lista EXPLÍCITA de capítulos resolviendo la fuente de cada uno igual que el
    // botón individual: fuente ASIGNADA (chapter_sources) primero, luego fuente Suwayomi, luego
    // MangaDex. Los capítulos ya locales se ignoran. Reutiliza los endpoints por capítulo.
    async downloadChapters(chapters, opts = {}) {
      const ui = useUiStore()
      const vg = useVersionsStore()
      const wanted = new Set(chapters.map(String))
      const rows = this.collectionChapters.filter(c => wanted.has(String(c.chapter)))
      const started = []
      const chainOpts = opts.chainUpscale ? {
        chainUpscale: true,
        chainEco: opts.chainEco ?? this.eco,
        chainFast: opts.chainFast ?? false,
      } : {}
      for (const c of rows) {
        // capítulo ya local (no es fila de fuente/MD/multi) → nada que descargar
        if (!c._sourceId && !c._mdChapterId && !c._covMulti) continue
        if (this.downloadByChapter[c.chapter] || this.dlTasks[String(c.chapter)]) continue
        if (c._assignedSource) vg.downloadChapterFrom(c.chapter, c._assignedSource, chainOpts)
        else if (c._sourceId) this.downloadSourceChapter(c, chainOpts)
        else if (c._mdChapterId) this.downloadMdChapter(c, chainOpts)
        else continue
        started.push(String(c.chapter))
      }
      if (!started.length) ui.toast('Nada que descargar en la selección (ya están descargados)', 'info')
      else ui.toast(`Descargando ${started.length} capítulo(s)`, 'info')
      // Devuelve los que REALMENTE arrancaron: la cadena sólo puede esperar por éstos. Armarla
      // con los "marcados" la dejaría esperando eternamente por capítulos sin tarea.
      return started
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
          api.get(`/api/mangadex/volumes/${id}?lang=${MD_LANGS.join(',')}`).catch(() => []),
          api.get(`/api/mangadex/covers/${id}`).catch(() => []),
        ])
        let volumes = Array.isArray(vol) ? vol : []
        // MISMO filtro de idioma que la rejilla de portadas, y por un motivo más gordo que
        // estético: abajo se inventa un tomo por cada portada que el mapa no declare, así que las
        // ediciones francesas y alemanas («1.1», «3.1», «12.1» — todas fr/de) creaban CHIPS DE
        // TOMO FANTASMA que no casaban con ningún capítulo.
        const covers = (Array.isArray(cov) ? cov : []).filter(coverLangOk)
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
    // Mapeo PURO volumen→capítulos locales (sin toasts ni efectos) — la lógica fiel de
    // applyMdexVolume, reutilizable tanto por el clic de un tomo como por el lote "todos".
    // Devuelve { selected: [chapterNorms], total } (total = nº de caps que el tomo declara).
    _mapVolumeToLocal(vol) {
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
      const total = vol.chapters ? vol.chapters.length : 0
      return { selected: selected || [], total }
    },
    /* El nombre del tomo ES el nombre del fichero exportado. `Tomo 1.cbz` suelto en un lector no
     * dice de qué obra es, y exportar dos series distintas se pisa el nombre. Va con la obra
     * delante — salvo que la etiqueta de MangaDex ya la traiga. */
    _tomoName(vol) {
      const obra = (this.current?.name || this.current?.id || '').trim()
      const t = vol.label || `Tomo ${vol.volume}`
      if (!obra || t.toLowerCase().includes(obra.toLowerCase())) return t
      return `${obra} - ${t}`
    },
    // Returns { selected: [chapterNorms], label } — clic de un tomo, con feedback.
    applyMdexVolume(vol) {
      const { selected, total } = this._mapVolumeToLocal(vol)
      const label = this._tomoName(vol)
      const ui = useUiStore()
      if (!selected.length) { ui.toast(`${label}: ningún capítulo en este rango`, 'warn'); return { selected: [], label } }
      const missing = total > 0 && total > selected.length ? total - selected.length : 0
      ui.toast(missing > 0 ? `${selected.length}/${total} caps — ${label} (faltan ${missing})` : `${selected.length} caps — ${label}`, missing > 0 ? 'warn' : 'ok')
      return { selected, label }
    },
    // Plan de "todos los tomos": para cada volumen con capítulos locales, su lista de caps y
    // etiqueta. Sin efectos — la UI lo usa para previsualizar y exportAllVolumes para ejecutar.
    volumePlan() {
      // Solo capítulos realmente DESCARGADOS: el mapeo de un tomo tiene un fallback que
      // devuelve los caps declarados aunque no sean locales (útil al clicar un tomo suelto),
      // pero un lote automático nunca debe exportar un cap que no existe en disco.
      const local = new Set(this.chapters.map(g => String(g.chapter)))
      const plan = []
      const seen = new Set()   // evita que dos tomos reclamen el mismo capítulo local
      for (const vol of this.mdex.volumes) {
        const label = this._tomoName(vol)
        const { selected } = this._mapVolumeToLocal(vol)
        const chapters = selected.filter(c => local.has(String(c)) && !seen.has(c))
        if (!chapters.length) continue
        chapters.forEach(c => seen.add(c))
        plan.push({ volume: vol.volume, label, chapters })
      }
      return plan
    },
    // Exporta AUTOMÁTICAMENTE un tomo por cada volumen de MangaDex (auto-organiza los caps
    // descargados según el mapa oficial). Reutiliza exportTomo (backend/Actividad ya cablean
    // el progreso). Portada por tomo si hay una en mdex.covers para ese volumen.
    async exportAllVolumes({ format = 'cbz', quality = 92, codec = 'jpeg', downscaleHalf = false, profile = 'manual', toDrive = false, coverPerVolume = true } = {}) {
      const ui = useUiStore()
      const plan = this.volumePlan()
      if (!plan.length) { ui.toast('No hay tomos con capítulos descargados para organizar', 'warn'); return { queued: 0 } }
      const coverByVol = {}
      for (const c of (this.mdex.covers || [])) if (c.volume && !(c.volume in coverByVol)) coverByVol[c.volume] = c
      ui.toast(`Organizando y exportando ${plan.length} tomo(s)…`, 'info')
      let queued = 0
      for (const t of plan) {
        let coverB64 = ''
        if (coverPerVolume && coverByVol[t.volume]) {
          try { const d = await api.post('/api/mangadex/cover_b64', { url: coverByVol[t.volume].url }); if (d?.b64) coverB64 = d.b64 } catch (_) {}
        }
        const taskId = await this.exportTomo({
          chapters: t.chapters, volumeName: t.label, format, quality, codec, downscaleHalf,
          coverB64, profile, toDrive,
        })
        if (taskId || toDrive) queued++
      }
      ui.toast(`${queued} tomo(s) en cola de exportación`, queued ? 'ok' : 'error')
      return { queued, planned: plan.length }
    },
    /* MangaDex publica una portada por tomo Y POR IDIOMA: 80 en Witch Hat Atelier (fr 28, de 18,
     * ja 15, en 11, es 5, pt-br 3). Buscar la del tomo 7 entre 80 miniaturas casi idénticas no es
     * elegir, es rebuscar. Se muestran los idiomas que esta biblioteca usa; el resto se cuenta
     * (`coversHidden`) en vez de desaparecer sin más — ocultar en silencio es lo que hace pensar
     * que MangaDex «no tiene» esa portada. */
    async loadMdexCovers() {
      const id = await this.resolveMdexId({ fuzzy: true }); if (!id) return
      this.mdex.coversLoading = true; this.mdex.covers = []; this.mdex.coversHidden = 0
      try {
        const all = await api.get(`/api/mangadex/covers/${id}`) || []
        const keep = all.filter(coverLangOk)
        // Si el filtro dejara la rejilla vacía, no filtra: mejor 80 portadas que ninguna.
        this.mdex.covers = keep.length ? keep : all
        this.mdex.coversHidden = keep.length ? all.length - keep.length : 0
      } catch (_) {}
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
    resetMdex() { this.mdex = { id: null, mdManga: null, approx: false, search: '', results: [], searching: false, volumes: [], volumesLoading: false, covers: [], coversLoading: false, coversHidden: 0, selectedCover: null, coverLoadingId: null, coverB64: '', coverUrl: '' } },

    /* ── Reader ─────────────────────────────────────────────────────────── */
    _resetView() { this.zoom = 1.0; this.panX = 0; this.panY = 0; this.compareMode = false; this.barsHidden = false; this.scanCompareMode = false; this.comparePages2 = []; this.compareLabels = null },

    // El valor global sigue siendo el fallback; una obra que ya se ha configurado recuerda sólo
    // sus tres preferencias de presentación y no contamina la siguiente lectura.
    _applyReaderPrefs(title) {
      const p = effectiveReaderPrefs(title, this.readerModeOverride?.[title])
      this.mode = p.mode
      this.fit = p.fit
      this.dir = p.dir
    },
    _persistReaderPref(field, value) {
      const title = this.reader?.title
      if (title) {
        saveReaderPrefs(title, { [field]: value })
        return
      }
      const globalKey = { mode: 'reader-mode', fit: 'reader-fit', dir: 'reader-dir' }[field]
      if (globalKey) {
        try { localStorage.setItem(globalKey, String(value)) } catch (_) {}
      }
    },

    // titleOverride/coverOverride let continueHistory() jump straight into the
    // reader for a local chapter without first opening the manga modal (which
    // would otherwise need to reload chapters/cover just to populate `current`).
    async read(chapter, source = 'auto', titleOverride = null, coverOverride = null) {
      const title = titleOverride || this.current.id
      this.readerLoading = true
      this._resetView()
      this.reader = { title, chapter, source, kind: 'manga', cover: coverOverride || this.current?.cover || '' }
      this._applyReaderPrefs(title)
      this.pages = []
      this.page = 0
      // Leer un capítulo es una PÁGINA, no un estado interno: sin esta entrada, "atrás" desde el
      // lector te sacaba del lector y del manga a la vez. `pushNav` se ignora sola cuando este
      // `read()` viene de un atrás/adelante, así que no duplica entradas.
      useUiStore().pushNav()
      // ¿Tenemos este capítulo ya prefetcheado (salto al vecino)? Entonces sin round-trip.
      const pf = this._nextPrefetch
      const usePf = pf && pf.pages && String(pf.title) === String(title)
        && String(pf.chapter) === String(chapter) && pf.source === source
      this._nextPrefetch = { title: null, chapter: null, source: null, pages: null }  // consumido/invalidado
      try {
        const d = usePf ? { pages: pf.pages, source: pf.resolvedSource || source }
                        : await api.post('/api/reader/read_chapter', { title, chapter, source })
        this.pages = d.pages || []
        if (this.reader) this.reader.source = d.source
        this._autoMode(title)   // webtoon vs manga (respeta override manual por serie)
        // restore last-read page for this chapter
        const pr = this.progress[title]
        if (pr && String(pr.lastChapter) === String(chapter) && pr.lastPage > 0 && pr.lastPage < this.pages.length)
          this.page = pr.lastPage
      } catch (_) { useUiStore().toast('No se pudo abrir el capítulo', 'error'); this.reader = null }
      finally { this.readerLoading = false }
    },

    // Precarga la LISTA de páginas del capítulo siguiente (solo local: barato, un listdir) para que
    // `goNextChapter` no espere al servidor. Idempotente y tolerante a carreras: si el usuario cambia
    // de capítulo mientras carga, el resultado se descarta. Devuelve las páginas (para que el lector
    // caliente las primeras imágenes) o null. Los capítulos online/multi-fuente NO se prefetchean:
    // su resolución es cara y depende de la fuente; el prefetch se limita al camino local común.
    async prefetchNextChapter() {
      if (!this.canNextChapter || this.reader?.kind !== 'manga') return null
      const row = this.chapterListAsc[this.chapterIndex + 1]
      if (!row || row._sourceId || row._mdChapterId || row._covMulti) return null   // solo local
      const title = this.reader?.title
      const chapter = row.chapter
      const source = this.upscaled[chapter] ? 'upscaled' : 'auto'
      const pf = this._nextPrefetch
      if (pf && pf.pages && String(pf.title) === String(title) && String(pf.chapter) === String(chapter) && pf.source === source)
        return pf.pages   // ya prefetcheado
      try {
        const d = await api.post('/api/reader/read_chapter', { title, chapter, source })
        // ¿Sigue siendo el vecino esperado? (el usuario pudo saltar mientras cargaba.)
        if (this.reader?.title !== title || this.chapterListAsc[this.chapterIndex + 1]?.chapter !== chapter) return null
        this._nextPrefetch = { title, chapter, source, pages: d.pages || [], resolvedSource: d.source }
        return this._nextPrefetch.pages
      } catch (_) { return null }
    },
    /* ── Escalado a color (APISR) — gestionado desde el MangaModal, fuera del lector ──
       Auto (todo el manga) o por capítulo con SELECTOR de páginas. El detector de
       is_color_page pre-marca las páginas a color; el usuario ajusta y confirma. */
    _colorResult(d, ui) {
      if (d.status === 'started') ui.toast(`Escalando ${d.total} página(s) a color con ${d.label}`, 'success')
      else if (d.status === 'already_running') ui.toast('Ya se está escalando a color', 'info')
      else if (d.status === 'none') ui.toast('No se detectaron páginas a color', 'info')
      else ui.toast(d.message || 'No se pudo iniciar el escalado a color', 'error')
    },
    // Modelo a color efectivo: el elegido por el usuario si sigue siendo válido, si no el primero.
    _effectiveColorModel() {
      const keys = Object.keys(this.modelsColor || {}).filter(k => this.modelsColor[k])
      if (this.colorModel && keys.includes(this.colorModel)) return this.colorModel
      return keys[0] || undefined
    },
    setColorModel(key) {
      this.colorModel = key
      localStorage.setItem('upscale-color-model', key || '')
    },
    // Auto: todo el manga (botón de nivel manga en Gestionar). Legado — se mantiene por si se
    // quiere el flujo 100% automático, pero la UI ahora abre el selector masivo (openColorPickerAll).
    async upscaleColorAuto(chapter = null) {
      const ui = useUiStore(); const title = this.current?.id
      if (!title) return
      try { this._colorResult(await api.post('/api/upscale/upscale_color', { title, chapter, model: this._effectiveColorModel() }), ui) }
      catch (_) { ui.toast('No se pudo iniciar el escalado a color', 'error') }
    },
    // Capítulos con marcador de "color hecho" (para ocultar el botón, como el 4K).
    async loadColorStatus() {
      const title = this.current?.id
      if (!title) { this.colorDone = {}; return }
      try {
        const d = await api.get(`/api/upscale/color_status?title=${encodeURIComponent(title)}`)
        this.colorDone = Object.fromEntries((d.done || []).map(ch => [String(ch), true]))
      } catch (_) {}
    },
    // Abre el selector de páginas a color de UN capítulo (thumbnails; solo páginas a color,
    // todas pre-seleccionadas).
    async openColorPicker(chapter) {
      const title = this.current?.id
      if (!title) return
      this.colorPicker = { open: true, mode: 'single', chapter, folder: '', pages: [], chapters: [], loading: true }
      try {
        const d = await api.get(`/api/upscale/color_pages?title=${encodeURIComponent(title)}&chapter=${encodeURIComponent(chapter)}`)
        this.colorPicker = {
          open: true, mode: 'single', chapter, folder: d.folder || '', chapters: [], loading: false,
          pages: (d.pages || []).map(p => ({ ...p, sel: true })),   // solo llegan páginas a color → todas marcadas
        }
      } catch (_) {
        this.closeColorPicker()
        useUiStore().toast('No se pudieron cargar las páginas', 'error')
      }
    },
    // Abre el selector MASIVO: páginas a color agrupadas por capítulo, con la misma selección
    // manual (todas pre-marcadas). Sin `chapters` mira el manga entero; con ellos, sólo los
    // capítulos MARCADOS en la lista (es lo que usa la barra del lote).
    async openColorPickerAll(chapters = null) {
      const title = this.current?.id
      if (!title) return
      this.colorPicker = { open: true, mode: 'all', chapter: null, folder: '', pages: [], chapters: [], loading: true }
      try {
        const q = chapters?.length ? `&chapters=${encodeURIComponent(chapters.join(','))}` : ''
        const d = await api.get(`/api/upscale/color_pages_all?title=${encodeURIComponent(title)}${q}`)
        this.colorPicker = {
          open: true, mode: 'all', chapter: null, folder: d.folder || '', pages: [], loading: false,
          chapters: (d.chapters || []).map(c => ({
            chapter: c.chapter, done: !!c.done,
            pages: (c.pages || []).map(p => ({ ...p, sel: true })),
          })),
        }
      } catch (_) {
        this.closeColorPicker()
        useUiStore().toast('No se pudieron cargar las páginas', 'error')
      }
    },
    closeColorPicker() { this.colorPicker = { open: false, mode: 'single', chapter: null, folder: '', pages: [], chapters: [], loading: false } },
    toggleColorPage(name, chapter = null) {
      const list = chapter == null
        ? this.colorPicker.pages
        : (this.colorPicker.chapters.find(c => c.chapter === chapter)?.pages || [])
      const p = list.find(x => x.name === name)
      if (p) p.sel = !p.sel
    },
    async runColorPicker() {
      const ui = useUiStore(); const title = this.current?.id
      const { mode, chapter, pages, chapters } = this.colorPicker
      const model = this._effectiveColorModel()
      try {
        let d
        if (mode === 'all') {
          const selections = chapters
            .map(c => ({ chapter: c.chapter, pages: c.pages.filter(p => p.sel).map(p => p.name) }))
            .filter(s => s.pages.length)
          if (!title || !selections.length) { ui.toast('Marca al menos una página', 'info'); return }
          d = await api.post('/api/upscale/upscale_pages_multi', { title, model, selections })
        } else {
          const sel = pages.filter(p => p.sel).map(p => p.name)
          if (!title || chapter == null || !sel.length) { ui.toast('Marca al menos una página', 'info'); return }
          d = await api.post('/api/upscale/upscale_pages', { title, chapter, pages: sel, model })
        }
        this._colorResult(d, ui)
        if (d.status === 'started' || d.status === 'already_running') this.closeColorPicker()
      } catch (_) { ui.toast('No se pudo iniciar el escalado a color', 'error') }
    },
    openReaderRaw(title, pages, label = '', volumeFile = '', startPage = 0) {
      this._resetView()
      this.reader = { title, chapter: label, source: '', kind: 'cbz', volumeFile }
      this._applyReaderPrefs(title)
      this.pages = pages
      this.page = Math.max(0, Math.min(Number(startPage) || 0, pages.length - 1))
      useUiStore().pushNav()
    },
    // Open a chapter read straight from remote page URLs (no disk round-trip).
    // kind:'manga' (not 'cbz') so spread/webtoon mode, progress tracking and
    // markRead keep working exactly like a locally-read chapter. `meta` records
    // {kind, chapterRef} so a finished online chapter can be re-resolved later
    // from the reading-history panel (the at-home page URLs themselves are single-use).
    openOnlineReader(title, chapter, pages, label = '', meta = null, cover = '') {
      this._resetView()
      this.reader = { title, chapter, source: 'online', kind: 'manga', sourceLabel: label, onlineMeta: meta, cover: cover || this.current?.cover || '' }
      this._applyReaderPrefs(title)
      this.pages = pages
      this.page = 0
      useUiStore().pushNav()
      this._autoMode(title)   // webtoon vs manga (respeta override manual por serie)
      const pr = this.progress[title]
      if (pr && String(pr.lastChapter) === String(chapter) && pr.lastPage > 0 && pr.lastPage < this.pages.length)
        this.page = pr.lastPage
      // Graba la fuente online YA (sin esperar a que cambie de página) para que
      // "Continuar" pueda reanudar un capítulo no descargado re-resolviendo sus páginas.
      this._saveProgress()
    },
    // Read a not-yet-downloaded chapter (MangaModal) directly from its source, online.
    // ── Novedades MangaDex ("nuevo capítulo listo") ─────────────────────────────
    // Usa MangaDex como fuente de verdad: compara su catálogo (con fecha de publicación) contra
    // lo descargado y contra la última visita, y marca los capítulos NUEVOS listos para bajar.
    // Coalesce varios disparos (apertura + cargas async de fuente/MangaDex) en UN solo fetch, para
    // que `have` (el alcance de la sección de capítulos) esté ya completo y no marque de más.
    scheduleMdUpdates() {
      clearTimeout(this._mdUpdTimer)
      this._mdUpdTimer = setTimeout(() => this.fetchMdUpdates(), 350)
    },
    async fetchMdUpdates() {
      const title = this.current?.id
      if (!title) return
      this.mdUpdates.loading = true
      try {
        // Alcance = capítulo MÁS ALTO de la SECCIÓN de capítulos (local ∪ fuente activa ∪ MangaDex),
        // no solo lo descargado: si tienes 1-250 vía tu fuente, MangaDex ≤250 no es "nuevo".
        const _num = (x) => { const n = parseFloat(x); return Number.isFinite(n) ? n : -1 }
        // ⚠️ `mdChapters` NO entra cuando es el catálogo REMOTO de MangaDex: es contra lo que se
        // compara, no algo a lo que ya tengas acceso. Incluirlo se anulaba a sí mismo — en cuanto
        // la lista de MangaDex cargaba con el 50.2, `have` pasaba a 50.2 y la condición del
        // backend (`chn > reach`) daba falso, así que el aviso «capítulo nuevo» APARECÍA y se
        // BORRABA solo unos milisegundos después (visto en Amayo no Tsuki 50.2).
        // Sí cuenta cuando hay una versión de MangaDex FIJADA: ahí `_loadSourceChapters` mete en
        // `mdChapters` la lista de TU fuente activa, y esos capítulos sí están a tu alcance.
        const mdEsMiFuente = this.effectiveSource?.kind === 'mangadex'
        const have = Math.max(-1,
          ...this.chapters.map(c => _num(c.chapter)),
          ...this.sourceChapters.map(c => _num(c.chapterNorm ?? c.chapterNumber)),
          ...(mdEsMiFuente ? this.mdChapters.map(c => _num(c.chapter)) : []))
        const qs = `title=${encodeURIComponent(title)}&uuid=${encodeURIComponent(this.mdId || '')}`
          + `&al=${encodeURIComponent(this.current?.al_id || '')}&have=${have}`
        const d = await api.get(`/api/md_updates/chapters?${qs}`)
        this.mdUpdates = {
          list: d.chapters || [], newCount: d.newCount || 0,
          uuid: d.uuid || null, loading: false,
          behindFrom: d.behindFrom || null, behindTo: d.behindTo || null,
        }
      } catch (_) {
        this.mdUpdates = { list: [], newCount: 0, uuid: null, loading: false, behindFrom: null, behindTo: null }
      }
    },
    // Descarga 1-clic de un capítulo que te falta desde MangaDex (reusa la descarga agnóstica de origen).
    async downloadMdUpdate(ch) {
      const ui = useUiStore()
      const title = this.current?.id
      const uuid = this.mdUpdates.uuid
      if (!title || !uuid || !ch) return
      const source = { sourceId: '__mangadex__', mangaId: `${uuid}@@${ch.lang}`, sourceName: 'MangaDex', sourceLang: ch.lang }
      try {
        const d = await api.post('/api/transplant/chapter_urls', { title, chapter: ch.chapter, source })
        if (!d.urls?.length) { ui.toast('No se encontraron páginas para ese capítulo', 'error'); return }
        await api.post('/api/download/download_source_chapter', {
          title, chapter: ch.chapter, pageUrls: d.urls,
          sourceId: source.sourceId, mangaId: source.mangaId, sourceName: source.sourceName, sourceLang: source.sourceLang,
        })
        ui.toast(`Descargando cap. ${ch.number}`, 'info')
      } catch (_) { ui.toast('No se pudo descargar el capítulo', 'error') }
    },
    async downloadAllMdUpdates() {
      for (const ch of this.mdUpdates.list.filter(c => c.isNew)) await this.downloadMdUpdate(ch)
      // El aviso "vas N por detrás" se limpia solo cuando los ficheros aterrizan: refresca el conteo.
      this.fetchMdUpdates()
    },

    async readOnline(ch) {
      const ui = useUiStore()
      // La asignación explícita por capítulo (chapter_sources) manda SIEMPRE sobre los
      // `_sourceId`/`_mdChapterId` legados de la fila (que vienen de la fuente única vieja
      // y quedan obsoletos en cuanto el capítulo se reasigna) — si no, "Leer" seguía
      // abriendo la fuente ORIGINAL aunque el capítulo ya estuviera reasignado a otra.
      const vs = useVersionsStore()
      const assigned = this.current?.id ? (vs.sourceForChapter(ch.chapter) || ch._assignedSource || null) : (ch._assignedSource || null)
      const busyKey = assigned ? `assigned:${ch.chapter}` : (ch._sourceId || ch._mdChapterId)
      this.onlineLoadingId = busyKey
      try {
        // RESILIENCIA: una sola fuente puede fallar (tras Cloudflare, caída o sin sincronizar
        // en Suwayomi → 0 páginas). En vez de rendirse con "sin páginas", probamos en orden la
        // fuente asignada/preferida PRIMERO y luego el resto de fuentes que declaran cubrir este
        // capítulo (incluida MangaDex) hasta que una entregue páginas. Así la lectura online es
        // responsiva aunque la mejor versión esté temporalmente inaccesible.
        const seen = new Set()
        const srcKey = (x) => `${x.sourceId}|${x.mangaId}`
        const candidates = []
        const pushSrc = (x) => {
          if (!x || x.mangaId == null || x.sourceId == null) return
          const k = srcKey(x)
          if (!seen.has(k)) { seen.add(k); candidates.push({ type: 'src', src: x }) }
        }
        if (assigned) pushSrc(assigned)
        for (const s of vs.sourcesWithChapter(ch.chapter)) pushSrc(s)
        if (ch._sourceId) candidates.push({ type: 'legacySource', id: ch._sourceId })
        if (ch._mdChapterId) candidates.push({ type: 'mangadex', id: ch._mdChapterId })

        let pages = []
        let meta = null
        let tried = 0
        for (const c of candidates) {
          try {
            if (c.type === 'src') {
              const s = c.src
              const d = await api.post('/api/transplant/chapter_urls', {
                title: this.current.id, chapter: ch.chapter,
                source: { sourceKind: s.sourceKind, sourceId: s.sourceId, mangaId: s.mangaId, sourceLang: s.sourceLang },
              })
              pages = d.urls || []
              // chapterRef aquí es una clave COMPUESTA (sourceId_mangaId), NO un id de capítulo:
              // las páginas se re-resuelven vía /transplant/chapter_urls con la fuente + el nº de
              // capítulo. Guardamos la `source` para poder REANUDAR (continueHistory la reusa).
              meta = { kind: 'source', chapterRef: `${s.sourceId}_${s.mangaId}`,
                       source: { sourceKind: s.sourceKind, sourceId: s.sourceId, mangaId: s.mangaId, sourceLang: s.sourceLang } }
            } else if (c.type === 'legacySource') {
              const pg = await api.get(`/api/sources/chapter/${c.id}/pages`)
              pages = pg.pages || []
              meta = { kind: 'source', chapterRef: c.id }
            } else if (c.type === 'mangadex') {
              const pg = await api.get(`/api/mangadex/chapter/${c.id}/pages`)
              pages = pg.pages || []
              meta = { kind: 'mangadex', chapterRef: c.id }
            }
          } catch (_) { pages = [] }
          tried++
          if (pages.length) break
        }
        if (!pages.length) {
          ui.toast(tried > 1
            ? 'Ninguna fuente pudo entregar este capítulo ahora (prueba de nuevo o descárgalo)'
            : 'Capítulo sin páginas', 'error')
          return
        }
        this.openOnlineReader(this.current.id, ch.chapter, pages, '', meta, this.current?.cover || '')
      } catch (_) { ui.toast('No se pudo abrir el capítulo', 'error') }
      finally { this.onlineLoadingId = null }
    },
    closeReader() {
      // Salir de pantalla completa al abandonar el capítulo: si el usuario estaba en
      // fullscreen, el estado quedaba atascado (ni F11 lo recuperaba) hasta reabrir otro.
      try { const ui = useUiStore(); if (ui.fullscreen) ui.setFullscreen(false) } catch {}
      this.reader = null; this.pages = []; this._resetView()
    },
    /* Salir del lector DESDE LA UI (botón, Esc). Retrocede en el historial en lugar de mutar, así
       "adelante" reabre el capítulo. `closeReader` se queda como cierre puro: lo llama el propio
       atrás/adelante, y si se llamase a sí mismo por aquí sería un bucle. */
    exitReader() { useUiStore().back(() => this.closeReader()) },

    setPage(i) {
      if (i < 0 || i >= this.pages.length) return
      this.page = i; this.panX = 0; this.panY = 0
      this._saveProgress()
      if (i >= this.pages.length - 1) this.markRead(this.reader?.chapter)
    },
    // El paso lo dicta el par REAL: con desfase, la primera página va sola y avanzar dos se
    // saltaría la siguiente sin que nada lo dijera.
    nextPage() { const step = this.spreadActive ? this.spreadPair.length : 1; this.setPage(Math.min(this.page + step, this.pages.length - 1)) },
    prevPage() { const step = this.spreadActive ? 2 : 1; this.setPage(Math.max(this.page - step, 0)) },

    setMode(m) {
      if (!['paged', 'webtoon'].includes(m)) return
      this.mode = m
      // Cambiar de modo manualmente fija la preferencia para ESTA serie, para que
      // la auto-detección no la vuelva a sobreescribir en próximas aperturas.
      const t = this.reader?.title
      if (t) {
        this._persistReaderPref('mode', m)
        this.readerModeOverride[t] = m
        try { localStorage.setItem('reader-mode-series', JSON.stringify(this.readerModeOverride)) } catch (_) {}
      } else {
        this._persistReaderPref('mode', m)
      }
      this._resetView()
    },

    // Modo noche del lector: brillo (0.4–1) e inversión. Persisten globalmente (preferencia de
    // lectura, no por serie), como fit/dir/spread.
    setBrightness(v) {
      this.readerBrightness = Math.max(0.4, Math.min(1, Math.round(v * 100) / 100))
      localStorage.setItem('reader-brightness', String(this.readerBrightness))
    },
    toggleInvert() {
      this.readerInvert = !this.readerInvert
      localStorage.setItem('reader-invert', this.readerInvert ? '1' : '0')
    },

    // Auto-detección webtoon/manhwa (tira vertical) vs manga (paginado) por relación de
    // aspecto de las páginas. Se salta si la serie tiene override manual. No persiste.
    _applyAutoMode(m) { this.mode = m },
    // URL para MEDIR el aspecto: online (`http(s)://`) es la misma URL que muestra el lector
    // → reutiliza la caché del navegador (0 fetches extra). Local pide una miniatura de 64px:
    // el ratio alto/ancho es invariante a la escala, así la decodificación es barata (no
    // descarga el 4K entero solo para medir). pageUrl codifica la ruta (carpetas con `?`/espacios).
    _pageAspectUrl(p) { return pageUrl(p, 64) },
    _imgAspect(url) {
      return new Promise((resolve) => {
        const im = new Image()
        im.onload = () => resolve(im.naturalWidth ? im.naturalHeight / im.naturalWidth : 0)
        im.onerror = () => resolve(0)
        im.src = url
      })
    },
    async _autoMode(title) {
      // Override manual → respétalo y no detectes.
      const ov = readReaderPrefs(title).mode || this.readerModeOverride[title]
      if (ov === 'paged' || ov === 'webtoon') { this.mode = ov; return }
      const pages = this.pages
      if (!pages || !pages.length) return
      const token = title + '|' + pages.length
      this._autoModeToken = token
      // Muestrea las PRIMERAS páginas (máx 3) — que el lector precarga de todos modos para
      // mostrarlas, así NO añade peticiones de red. Muestrear medio/final SÍ las añadiría
      // (fetches on-demand caros en fuentes online que dejaban la página visible en blanco).
      // Señal: manhwa/manhua son TIRAS verticales → alguna página con h/w ≥ 2 (el manga
      // ronda 1.4-1.5 y casi nunca llega a 2). Basta UNA tira alta para clasificar webtoon;
      // esto acierta aunque la portada del webtoon sea un banner casi cuadrado. Se decide
      // PROVISIONALMENTE tras la portada (modo correcto desde el primer instante) y se
      // refina a webtoon si una página siguiente resulta ser una tira.
      const n = Math.min(3, pages.length)
      let decided = false
      for (let i = 0; i < n; i++) {
        const r = await this._imgAspect(this._pageAspectUrl(pages[i]))
        if (this._autoModeToken !== token) return   // cambió de capítulo mientras medía
        if (r >= 2.0) { this._applyAutoMode('webtoon'); return }   // tira alta → webtoon
        if (!decided && r > 0) { this._applyAutoMode('paged'); decided = true }   // provisional: manga
      }
      if (!decided) this._applyAutoMode('paged')   // ninguna cargó → asume manga (paginado)
    },
    cycleFit() { const M = ['width', 'height', 'original']; this.setFit(M[(M.indexOf(this.fit) + 1) % 3]) },
    setFit(f) { if (['width', 'height', 'original'].includes(f)) { this.fit = f; this._persistReaderPref('fit', f) } },
    toggleDir() { this.dir = this.dir === 'rtl' ? 'ltr' : 'rtl'; this._persistReaderPref('dir', this.dir) },
    // Spread on: snap to an aligned page so the pairs no se descuadran (0-1, 2-3, … o, con
    // desfase, 0 sola y luego 1-2, 3-4, …).
    toggleSpread() {
      this.spread = !this.spread
      localStorage.setItem('reader-spread', this.spread ? '1' : '0')
      if (this.spread) this._alignSpread()
      this.resetZoom()
    },
    // Mueve la página actual al inicio de su par. Sin esto, al activar el desfase la página
    // visible se queda a mitad de par y el resto del capítulo va desplazado una página.
    _alignSpread() {
      if (this.page === 0) return
      const aligned = (this.page - this.spreadOffset) % 2 === 0
      if (!aligned) this.page = Math.max(0, this.page - 1)
    },
    toggleSpreadOffset() {
      this.spreadOffset = this.spreadOffset ? 0 : 1
      localStorage.setItem('reader-spread-offset', String(this.spreadOffset))
      this._alignSpread()
    },
    setWebtoonGap(px) {
      this.webtoonGap = Math.max(0, Math.min(48, Math.round(Number(px) || 0)))
      localStorage.setItem('reader-webtoon-gap', String(this.webtoonGap))
    },
    setAutoScrollSpeed(px) {
      this.autoScrollSpeed = Math.max(10, Math.min(400, Math.round(Number(px) || 60)))
      localStorage.setItem('reader-autoscroll', String(this.autoScrollSpeed))
    },
    setWebtoonWidth(pct) {
      this.webtoonWidth = Math.max(25, Math.min(100, Math.round(Number(pct) || 52)))
      localStorage.setItem('reader-webtoon-width', String(this.webtoonWidth))
    },
    // Saltar a CUALQUIER capítulo sin salir del lector (en MangaDex es un desplegable; aquí
    // antes había que cerrar, buscar la fila y volver a abrir). El índice es sobre chapterListAsc.
    async goChapterAt(i) {
      const row = this.chapterListAsc[i]
      if (!row || String(row.chapter) === String(this.reader?.chapter)) return
      await this._readRow(row)
    },

    zoomBy(delta) {
      this.zoom = Math.max(0.5, Math.min(3.0, this.zoom + delta))
      if (this.zoom <= 1.0) { this.panX = 0; this.panY = 0 }
    },
    resetZoom() { this.zoom = 1.0; this.panX = 0; this.panY = 0 },

    toggleCompare() { if (!this.canCompare) { this.compareMode = false; return } this.compareMode = !this.compareMode; if (this.compareMode) this.compareX = 50 },

    async goNextChapter() {
      if (!this.canNextChapter) return
      await this._readRow(this.chapterListAsc[this.chapterIndex + 1])
    },
    async goPrevChapter() {
      if (!this.canPrevChapter) return
      await this._readRow(this.chapterListAsc[this.chapterIndex - 1])
    },
    // Abre una fila de `collectionChapters` por el camino correcto según su origen: una fila
    // ONLINE (fuente Suwayomi, MangaDex, o capítulo asignado sin descargar) resuelve sus
    // páginas en el momento vía readOnline(); una fila descargada usa el lector local. Así
    // la navegación entre capítulos es idéntica sin que el usuario piense en el origen.
    async _readRow(row) {
      if (!row) return
      if (row._sourceId || row._mdChapterId || row._covMulti) await this.readOnline(row)
      else await this.read(row.chapter, this.upscaled[row.chapter] ? 'upscaled' : 'auto')
    },

    /* Progreso de lectura (qué capítulos has leído y por dónde vas en cada obra).
     *
     * El localStorage es la copia LOCAL: instantánea y funciona sin backend. Pero no es un
     * sitio donde guardar datos — limpiar el almacenamiento del WebView, reinstalar o cambiar
     * de máquina se llevaba por delante el mapa de leídos de TODA la biblioteca, y ni la copia
     * semanal ni "Recuperar biblioteca" lo traían de vuelta (el historial son los últimos 500
     * capítulos terminados: no reconstruye ni los leídos por obra ni la página).
     * Ahora hay una copia DURABLE en el backend (`/api/reader/progress`), y las dos se
     * reconcilian FUNDIENDO: unión de leídos, la posición más reciente gana. */
    _persistProgress() {
      localStorage.setItem('manga-progress-v1', JSON.stringify(this.progress))
      this._pushProgress()
    },
    _pushProgress() {
      clearTimeout(_progressTimer)
      // Se agrupa: pasar página escribe progreso constantemente y no hace falta una petición
      // por página. Se vacía también al ocultar la pestaña o cerrar (ver init).
      _progressTimer = setTimeout(() => {
        api.post('/api/reader/progress', this.progress).catch(() => {})
      }, 2000)
    },
    _flushProgress() {
      clearTimeout(_progressTimer)
      api.post('/api/reader/progress', this.progress).catch(() => {})
    },
    /** Reconcilia con la copia durable al arrancar. Una sola petición: el backend funde lo que
     *  le mandas con lo que tiene y devuelve el resultado, así que no hay dos algoritmos de
     *  fusión que puedan divergir. Si el backend no responde, se sigue con lo local. */
    async _hydrateProgress() {
      try {
        const merged = await api.post('/api/reader/progress', this.progress)
        if (merged && typeof merged === 'object') {
          this.progress = merged
          localStorage.setItem('manga-progress-v1', JSON.stringify(merged))
        }
      } catch (_) { /* sin backend: la copia local sigue mandando */ }
    },
    /** Un borrado es una INTENCIÓN, no una ausencia: hay que declararlo o la fusión (que une
     *  los leídos) lo resucitaría en la siguiente sincronización. */
    _forgetRemote(body) { api.post('/api/reader/progress/forget', body).catch(() => {}) },
    // Devuelve la entrada de progreso SIEMPRE por el proxy reactivo de Pinia. Ojo:
    // `x = this.progress[k] || (this.progress[k] = {...})` devolvía el objeto CRUDO
    // recién asignado (no el proxy) la 1ª vez que se lee un manga nuevo → mutar
    // `e.lastPage=…` NO disparaba reactividad y la UI (continuar/leído) no se
    // actualizaba hasta F5. Leer de vuelta `this.progress[k]` da el proxy → reactivo.
    _progressEntry(k) {
      if (!this.progress[k]) this.progress[k] = { read: {} }
      return this.progress[k]
    },
    _saveProgress() {
      if (this.reader?.kind !== 'manga') return
      const k = this.reader.title
      const e = this._progressEntry(k)
      e.lastChapter = String(this.reader.chapter); e.lastPage = this.page
      e.lastTotal = this.pages.length || e.lastTotal || 0
      e.ts = Date.now()   // recencia → "Continuar leyendo"
      // Fuente del último capítulo, para poder REANUDAR uno NO descargado (leído
      // online): las URLs at-home son de un solo uso → guardamos kind+ref y se
      // re-resuelven al reanudar. Leído del disco (onlineMeta null) → limpiamos.
      const meta = this.reader.onlineMeta
      if (meta?.kind && meta.kind !== 'local' && meta.chapterRef != null) {
        e.lastKind = meta.kind; e.lastRef = meta.chapterRef
        // Fuente completa (vía moderna `src`): reanudar re-resuelve con /transplant/chapter_urls,
        // porque su chapterRef es una clave compuesta, no un id de capítulo consultable.
        if (meta.source) e.lastSource = meta.source; else delete e.lastSource
      } else {
        delete e.lastKind; delete e.lastRef; delete e.lastSource
      }
      this._persistProgress()
    },
    markRead(chapter) {
      if (this.reader?.kind !== 'manga' || chapter == null) return
      const k = this.reader.title
      const e = this._progressEntry(k)
      ;(e.read ||= {})[String(chapter)] = true
      this._persistProgress()
      this._recordHistory(chapter)
    },
    isChapterRead(chapter) { return !!this.progress[this.current?.id]?.read?.[String(chapter)] },
    /** Capítulos leídos de una serie CUALQUIERA (no sólo la abierta) — lo usa la tarjeta de la
     *  rejilla para enseñar lo que falta en vez del total, que es un dato muerto. */
    readCountOf(mangaId) { return Object.keys(this.progress[mangaId]?.read || {}).length },
    toggleChapterRead(chapter) {
      const k = this.current?.id; if (!k) return
      const e = this._progressEntry(k)
      e.read ||= {}
      const desmarcado = !!e.read[String(chapter)]
      if (desmarcado) delete e.read[String(chapter)]; else e.read[String(chapter)] = true
      e.ts = Date.now()
      this._persistProgress()
      // Desmarcar es una intención explícita: sin declararlo, la unión lo volvería a marcar.
      if (desmarcado) this._forgetRemote({ chapters: { [k]: [String(chapter)] } })
    },
    // Marca como leídos TODOS los capítulos hasta `chapter` (inclusive) — atajo típico.
    markReadUpTo(chapter) {
      const k = this.current?.id; if (!k) return
      const hi = parseFloat(chapter)
      const e = this._progressEntry(k)
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
      if (!info) return
      const pr = this.progress[this.current?.id]
      const downloaded = this.chapters?.some(c => String(c.chapter) === String(info.chapter))
      // Capítulo NO descargado leído online → reanuda re-resolviendo sus páginas
      // frescas (mismo camino que el historial), en vez de leer del disco (vacío).
      if (!downloaded && pr?.lastKind && pr?.lastRef) {
        this.continueHistory({
          kind: pr.lastKind, chapter: info.chapter, chapter_ref: pr.lastRef,
          source: pr.lastSource || null,
          title: this.current.id, cover: this.current?.cover || '',
        })
        return
      }
      this.read(info.chapter, this.upscaled[info.chapter] ? 'upscaled' : 'auto')
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
          source_obj: meta?.source ?? null,   // objeto de fuente: re-resolver al reanudar
        })
        this.historyLoaded = false
      } catch (_) {}
    },
    async loadHistory() {
      this.historyError = ''
      try { this.history = await api.get('/api/reader/history') || [] }
      catch (e) { this.historyError = e?.body || e?.message || 'No se pudo cargar el historial.' }
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
        // `entry.source` es un OBJETO cuando viene de resumeCurrent (`pr.lastSource`); desde el
        // panel de historial es la CADENA 'online' de display y el objeto real viaja en
        // `source_obj`. Tomamos el que sea objeto: tratar la cadena como objeto mandaba
        // `{sourceId: undefined}` y devolvía 0 páginas ("fuente sin páginas ahora").
        const srcObj = (entry.source && typeof entry.source === 'object') ? entry.source : entry.source_obj
        if (entry.kind === 'local') {
          await this.read(entry.chapter, 'auto', entry.title, entry.cover)
        } else if (srcObj) {
          // Vía moderna: la fuente se re-resuelve a páginas frescas (el chapterRef es compuesto,
          // no un id de capítulo). Mismo camino que readOnline → reanudar online funciona.
          const s = srcObj
          const d = await api.post('/api/transplant/chapter_urls', {
            title: entry.title, chapter: entry.chapter,
            source: { sourceKind: s.sourceKind, sourceId: s.sourceId, mangaId: s.mangaId, sourceLang: s.sourceLang },
          })
          const pages = d.urls || []
          if (!pages.length) { ui.toast('No se pudo continuar (fuente sin páginas ahora)', 'error'); return }
          this.openOnlineReader(entry.title, entry.chapter, pages, '',
            { kind: 'source', chapterRef: `${s.sourceId}_${s.mangaId}`, source: s }, entry.cover)
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
