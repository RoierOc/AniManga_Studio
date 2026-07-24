<script setup>
import { computed, ref, watch, onMounted, onBeforeUnmount, onUnmounted } from 'vue'
import { useMangaStore } from '@/stores/manga'
import { formatChapter, MANGA_STATUS, pageUrl } from '@/lib/manga'
import { formatBytes } from '@/lib/format'
import { imgProxy } from '@/lib/img'
import { coverRGB, vivid } from '@/lib/coverColor'
import { api } from '@/lib/api'
import { useUiStore } from '@/stores/ui'
import Icon from '@/components/ui/Icon.vue'
import Spinner from '@/components/ui/Spinner.vue'
import Skeleton from '@/components/ui/Skeleton.vue'
import { useVersionsStore } from '@/stores/versions'
import { useDiscoveryStore } from '@/stores/discovery'
import VersionsCoverageGrid from '@/components/manga/VersionsCoverageGrid.vue'
import MangaRecRail from '@/components/manga/MangaRecRail.vue'
import Select from '@/components/ui/Select.vue'
import { useModal } from '@/lib/useModal'

const store = useMangaStore()
const ui = useUiStore()
const vg = useVersionsStore()
const disco = useDiscoveryStore()

// Añadir a biblioteca DESDE esta ficha (abierta desde Descubrir con "Ver versiones y leer"): una
// Obra abierta así no está descargada (trackedOnly/mdOnly) y puede no estar aún en la biblioteca.
// Reusa el store de Descubrir (que ya resuelve portada en el backend por al_id al añadir).
const libWork = computed(() => {
  const c = store.current
  return c ? { id: c.id, title: c.name, cover: c.cover, ids: { anilist: c.al_id || null } } : null
})
// Solo tiene sentido ofrecerlo cuando NO está descargada localmente (esas ya están en la biblioteca).
const canAddToLib = computed(() => !!store.current && (store.current.trackedOnly || store.current.mdOnly))
const inLib = computed(() => !!libWork.value && disco.inLibrary(libWork.value))
const addingLib = computed(() => !!libWork.value && disco.isAdding(libWork.value))
function addToLib() { if (libWork.value) disco.addToLibrary(libWork.value) }
// The X / overlay just closes the modal and stays on the current view — it must NOT
// navigate browser history (that was jumping to the previous page, sometimes Mi Anime).
// replaceNav updates the current history entry to "closed"; the browser back/forward
// buttons still reopen/close via the snapshot model.
function closeModal() { store.close(); ui.replaceNav() }
const m = computed(() => store.current)
// ¿Hay algún modelo a color (APISR)? Habilita los botones de escalado a color.
const hasColorModel = computed(() => Object.values(store.modelsColor).includes(true))
const colorModelKeys = computed(() => Object.keys(store.modelsColor).filter(k => store.modelsColor[k]))
const colorModelLabel = computed(() => {
  const k = (store.colorModel && colorModelKeys.value.includes(store.colorModel)) ? store.colorModel : colorModelKeys.value[0]
  return store.models[k] || 'el modelo a color'
})
const modelEntries = computed(() =>
  Object.entries(store.models).map(([key, label]) => ({ key, label, color: !!store.modelsColor[key] })))

const colorPickerSelCount = computed(() => {
  const cp = store.colorPicker
  if (cp.mode === 'all') return cp.chapters.reduce((n, c) => n + c.pages.filter(p => p.sel).length, 0)
  return cp.pages.filter(p => p.sel).length
})
const colorPickerTotal = computed(() => {
  const cp = store.colorPicker
  if (cp.mode === 'all') return cp.chapters.reduce((n, c) => n + c.pages.length, 0)
  return cp.pages.length
})
const colorPickerEmpty = computed(() => colorPickerTotal.value === 0)
const upState = (ch) => store.upscaled[ch]          // true | 'partial' | undefined

// Ambiente por portada (solo FONDO, nunca sobre texto/controles): color dominante para
// el aura + resplandor del póster, y la propia portada esmerilada de backdrop en la
// cabecera. Se limpia al cerrar/cambiar de serie. Cross-origin/ilegible → sin ambiente.
const coverArt = ref(null)   // { css, art } | null
watch(() => store.current?.cover, async (cover) => {
  coverArt.value = null
  if (!cover) return
  const url = imgProxy(cover)
  const rgb = await coverRGB(url)
  if (!rgb) return
  const v = vivid(rgb)
  coverArt.value = { css: `${v.r}, ${v.g}, ${v.b}`, art: `url("${url}")` }
}, { immediate: true })
const coverStyle = computed(() => coverArt.value
  ? { '--cv': coverArt.value.css, '--cvart': coverArt.value.art }
  : {})

// Tamaño en disco de esta serie (original vs 4K) — se muestra en el panel Gestionar.
const seriesSize = ref(null)
watch(() => store.current?.id, async (id) => {
  seriesSize.value = null
  if (!id) return
  // Si la ficha se abre para una Obra no descargada (desde Descubrir) y aún no sabemos qué hay en
  // la biblioteca, cárgalo → el botón "Añadir a biblioteca" refleja el estado real desde el inicio.
  if (canAddToLib.value && !disco.libraryTitles.length) disco.loadLibrary()
  // Recomendados por esta serie (AniList, resuelto por título en el backend).
  store.loadRecs(store.current?.name || id)
  try { seriesSize.value = await api.get(`/api/storage/series?title=${encodeURIComponent(id)}`) } catch (_) {}
}, { immediate: true })

// Liberar espacio: borra del disco el 4K (regenerable) o los originales descargados,
// como en el anime. No quita el manga de la biblioteca (se puede re-descargar/re-escalar).
const freeing = ref('')
async function freeSpace(scope) {
  const id = store.current?.id
  if (!id || freeing.value) return
  const label = scope === 'upscaled' ? 'la copia 4K' : scope === 'original' ? 'los archivos originales descargados' : 'TODOS los archivos (4K + originales)'
  if (!await ui.confirm({ title: 'Liberar espacio', danger: true, confirmLabel: 'Borrar',
      body: `¿Borrar ${label} de "${store.current?.name || id}" del disco?\nEl manga sigue en tu biblioteca; podrás volver a descargar/escalar.` })) return
  freeing.value = scope
  try {
    const r = await api.post('/api/storage/series/delete', { title: id, scope })
    try { seriesSize.value = await api.get(`/api/storage/series?title=${encodeURIComponent(id)}`) } catch (_) {}
    if (scope !== 'original') store._refreshUpscaled()   // 4K borrado → refresca badges
    ui.toast(`Liberados ${formatBytes(r.freed || 0)}`, 'ok')
  } catch (_) {
    ui.toast('No se pudo liberar el espacio', 'error')
  } finally {
    freeing.value = ''
  }
}

// Cobertura de escalado: cuántos capítulos están en 4K / parciales / sin escalar.
const coverage = computed(() => {
  const chs = store.chapters || []
  if (!chs.length) return null
  let full = 0, partial = 0
  for (const c of chs) {
    const st = store.upscaled[c.chapter]
    if (st === true) full++
    else if (st === 'partial') partial++
  }
  return { total: chs.length, full, partial, plain: chs.length - full - partial }
})

// Capítulo por el que vas ahora mismo (filo azul + "pág. N" en su fila).
function isCurrent(ch) {
  const i = store.continueInfo()
  return !!i && String(i.chapter) === String(ch)
}

// Progreso de lectura de la serie, para la barra de la cabecera.
const chapterTotal = computed(() => store.collectionChapters.length)
const readTotal = computed(() => store.collectionChapters.filter(c => store.isChapterRead(c.chapter)).length)
const readPct = computed(() => (chapterTotal.value ? (readTotal.value / chapterTotal.value) * 100 : 0))
// Primer capítulo descargado, para "Empezar a leer" cuando no hay progreso.
const firstChapter = computed(() => {
  const c = [...store.chapters].sort((a, b) => (parseFloat(a.chapter) || 0) - (parseFloat(b.chapter) || 0))[0]
  return c ? c.chapter : null
})

const LANG_FLAG = { en: '🇬🇧', es: '🇪🇸', 'es-la': '🇲🇽', ja: '🇯🇵', 'pt-br': '🇧🇷', fr: '🇫🇷', ko: '🇰🇷', zh: '🇨🇳', 'zh-hk': '🇭🇰', it: '🇮🇹', de: '🇩🇪', ru: '🇷🇺' }
const flag = (l) => LANG_FLAG[l] || l

// ── Selección por lote (casillas en la lista de Capítulos) ──────────────────────
// batchSel = Set de claves de capítulo marcadas. Una barra flotante actúa sobre ellas:
// Descargar (capítulos remotos) o Escalar 4K (capítulos locales). Se limpia al cambiar de
// pestaña/manga.
const batchSel = ref(new Set())
function clearBatch() { batchSel.value = new Set(); lastTouched.value = null }

// ── Selección múltiple: shift+clic (rango) y clic-arrastre (pincel) ───────────────────────────
// Las dos, porque cubren cosas distintas: arrastrar va bien para rachas cortas y adyacentes, pero
// para marcar 50 capítulos tendrías que arrastrar por una lista que scrollea. Shift+clic lo hace
// en dos clics y es el estándar que todo el mundo ya conoce (Explorador, Gmail).
const lastTouched = ref(null)      // ancla del rango: último capítulo marcado con un clic normal
const painting = ref(null)         // null | true (pintando selección) | false (pintando borrado)

function _order() { return store.collectionChapters.map(c => String(c.chapter)) }

function _applyRange(from, to, on) {
  const order = _order()
  const i = order.indexOf(String(from)), j = order.indexOf(String(to))
  if (i < 0 || j < 0) return
  const s = new Set(batchSel.value)
  for (const k of order.slice(Math.min(i, j), Math.max(i, j) + 1)) on ? s.add(k) : s.delete(k)
  batchSel.value = s
}

function _setSel(ch, on) {
  const s = new Set(batchSel.value)
  on ? s.add(String(ch)) : s.delete(String(ch))
  batchSel.value = s
}

// mousedown (no click): hay que empezar a pintar ANTES de soltar el botón.
function batchDown(ch, ev) {
  if (ev.shiftKey && lastTouched.value != null) {
    // Rango: extiende con el mismo estado que tenga el ancla, como en un explorador.
    _applyRange(lastTouched.value, ch, batchSel.value.has(String(lastTouched.value)))
    ev.preventDefault()          // evita que shift+clic seleccione texto de la lista
    return
  }
  const on = !batchSel.value.has(String(ch))
  _setSel(ch, on)
  lastTouched.value = ch
  painting.value = on            // arrastrar sigue haciendo LO MISMO que el primer clic
}

// Al entrar en otra fila con el botón pulsado, se pinta igual que el primero: si empezaste
// marcando, marcas; si empezaste desmarcando, desmarcas. Nunca alterna (eso haría que pasar por
// encima dos veces deshiciera el trabajo).
function batchOver(ch) {
  if (painting.value === null) return
  _setSel(ch, painting.value)
  lastTouched.value = ch
}

// El mouseup se escucha en window, no en la lista: si sueltas fuera (muy fácil al arrastrar hasta
// el borde para scrollear) el pincel se quedaría pegado y seguirías seleccionando sin pulsar.
function endPaint() { painting.value = null }
onMounted(() => window.addEventListener('mouseup', endPaint))
onBeforeUnmount(() => window.removeEventListener('mouseup', endPaint))
// De lo marcado: cuántos son locales (escalables) vs remotos (descargables).
const batchStats = computed(() => {
  let local = 0, remote = 0
  for (const c of store.collectionChapters) {
    if (!batchSel.value.has(String(c.chapter))) continue
    if (!c._sourceId && !c._mdChapterId && !c._covMulti) local++
    else remote++
  }
  return { local, remote }
})
// Descargar es sólo el primer paso: la cadena (escalar / traducir+escalar) se arma ANTES de
// lanzar las descargas y avanza sola con el SSE, aunque cierres el modal.
async function batchDownload() {
  const sel = [...batchSel.value]
  clearBatch()
  // La cadena se arma con lo que downloadChapters ARRANCÓ de verdad, no con lo marcado: los ya
  // locales o ya en curso no generan tarea, y esperarlos dejaría la cadena colgada.
  const started = await store.downloadChapters(sel)
  store.armChain(started || [])
}
// Traducir necesita fuentes elegidas (pestaña Traducir). Sin ellas el chip se deshabilita en vez
// de dejarte armar una cadena que fallaría al llegar a ese paso.
const tpReady = computed(() => !!(store.tp.artSel && store.tp.esSel))
// El botón dice lo que VA A PASAR, no "Descargar" a secas: es lo único que ve el usuario del
// encadenado, así que si el texto no lo cuenta, el encadenado es invisible.
const chainLabel = computed(() => {
  const c = store.chain
  const tr = c.translate && tpReady.value
  if (tr && c.upscale) return 'Descargar, traducir y escalar'
  if (tr) return 'Descargar y traducir'
  if (c.upscale) return 'Descargar y escalar'
  return 'Descargar'
})
function batchUpscale() {
  const excludePages = store.current?.source_meta?.imported ? store.excludedPages : []
  store.upscaleChapters([...batchSel.value], { excludePages }); clearBatch()
}
// El escalado 4K usa el modelo B&N y SALTA las páginas a color (se copian tal cual), así que
// éstas necesitan su propia pasada con el modelo de color. Antes sólo se podía lanzar sobre el
// manga ENTERO desde "Gestionar"; aquí va sobre lo que hayas marcado.
function batchColor() {
  const chapters = [...batchSel.value]
  store.openColorPickerAll(chapters); clearBatch()
}
function toggleAllBatch() {
  const all = store.collectionChapters.map(c => String(c.chapter))
  batchSel.value = batchSel.value.size === all.length ? new Set() : new Set(all)
}
// color pages
function detectColors() { store.loadColorPages([...sel.value]) }

// modal tabs + tomo export form
const tab = ref('chapters')
const sel = ref(new Set())
const volName = ref('')
const fmt = ref('cbz')
const quality = ref(92)
const codec = ref('jpeg')
const downscale = ref(false)
// WebP holds up at lower quality than JPEG (no ringing on text), so its slider floor is lower.
const qMin = computed(() => codec.value === 'webp' ? 70 : 85)
// Keep quality within the codec's valid range when switching codecs.
watch(codec, () => { if (quality.value < qMin.value) quality.value = qMin.value })

// management panel
const showManage = ref(false)
const renameVal = ref('')
const tomoCover = ref('')      // b64 cover for the exported tomo

// Traducir (trasplante)
const tp = computed(() => store.tp)
const tpSel = ref(new Set())
const toggleTp = (ch) => { const s = new Set(tpSel.value); s.has(ch) ? s.delete(ch) : s.add(ch); tpSel.value = s }
const onArt = (v) => store.tpSelectArt(tp.value.artCands.find(c => store._candKey(c) === v))
const onEs = (v) => store.tpSelectEs(tp.value.esCands.find(c => store._candKey(c) === v))
// Candidatos de fuente → opciones del desplegable (misma etiqueta que tenía el <option>).
const candOpts = (list, withScore) => list.map(c => ({
  value: store._candKey(c),
  label: `${c.sourceName} (${c.sourceLang})`,
  hint: c.quality ? (withScore ? `${c.quality.height}px · ${c.quality.score}` : `${c.quality.height}px`) : 'sin medir',
}))
// Si el capítulo tiene una fuente asignada (grid de cobertura) que NO es español, el backend
// usa ese arte para ESE capítulo en vez del arte global del título (ver transplant.py
// _run_chapters) — mostramos por qué para que no sea una sorpresa.
const _ES_LANGS = new Set(['es', 'es-419', 'es-es', 'es-la', 'es-mx'])
function artOverrideFor(chn) {
  const a = vg.assigned[String(chn)]
  return a && !_ES_LANGS.has((a.sourceLang || '').toLowerCase()) ? a : null
}
// Fuente ES anclada por capítulo (chapter_sources en español): el backend la usa como fuente
// ES de ESE capítulo en vez del ES global — se lo mostramos al usuario para que no sorprenda.
function esOverrideFor(chn) {
  const a = vg.assigned[String(chn)]
  return a && _ES_LANGS.has((a.sourceLang || '').toLowerCase()) ? a : null
}
const tpPhaseLabel = computed(() => {
  const p = tp.value.discoverProgress || {}
  switch (tp.value.phase) {
    case 'start': case 'variants': return 'Buscando variantes del título…'
    case 'searching': return p.searchTotal ? `Rastreando fuentes… ${p.searched || 0}/${p.searchTotal}` : 'Rastreando fuentes…'
    case 'ranking': return p.rankTotal ? `Midiendo calidad… ${p.ranked || 0}/${p.rankTotal}` : 'Midiendo calidad…'
    default: return 'Trabajando…'
  }
})
const tpRunPct = computed(() => {
  const s = tp.value.runStatus
  if (!s?.chapterTotal) return 0
  return Math.round(((s.chapterDone || 0) / s.chapterTotal) * 100)
})

// Versiones (ranking de calidad)
const ver = computed(() => store.ver)
const verLangs = computed(() => Object.keys(store.ver.byLang || {}))
const verList = computed(() => {
  const f = store.ver.langFilter
  return f ? (store.ver.byLang[f] || []) : store.ver.versions
})
const verBest = computed(() => verList.value[0] || null)   // mejor de la selección actual (para "recomendada ↑")
const verSampleKey = (c) => `${c.sourceId}_${c.mangaId}`
// Calidad "irregular": el peor capítulo muestreado es notablemente peor que la mediana
// (consistency = peor/mediana). El ranking ya lo penaliza; aquí solo lo señalamos.
const isIrregular = (q) => !!(q && q.consistency != null && q.consistency < 0.85
  && q.heightMax && q.heightMin && q.heightMax - q.heightMin > 150)
function isCurrentVersion(c) {
  const sm = store.current?.source_meta
  return !!(sm && c.sourceId === sm.sourceId && String(c.mangaId) === String(sm.mangaId))
}
// La versión local como "candidato" sintético para poder compararla en A|B
const localCand = computed(() => store.ver.local
  ? { local: true, sourceId: '__local__', mangaId: '__local__', sourceName: 'Tu versión local', sourceLang: store.current?.source_meta?.sourceLang || '', quality: store.ver.local }
  : null)
// Referencia de completitud: cuántos capítulos hay ACTUALMENTE del manga (máximo visto entre
// fuentes, o total oficial de AniList si terminó). Permite señalar qué versión está al día.
const refChapters = computed(() => store.ver.referenceChapters || null)
const refSourceLabel = computed(() => store.ver.referenceSource === 'anilist' ? 'AniList' : 'fuentes')
// Una versión está "al día" si su último capítulo alcanza la referencia (margen de 0.5 por
// numeraciones con decimales/partes). `faltan` = cuántos capítulos le faltan hasta el último.
function versionReach(c) { return c?.quality?.latestChapter ?? null }
function isUpToDate(c) {
  const last = versionReach(c); const ref = refChapters.value
  return last != null && ref != null && last >= ref - 0.5
}
function chaptersBehind(c) {
  const last = versionReach(c); const ref = refChapters.value
  if (last == null || ref == null) return null
  const n = Math.round(ref - last)
  return n > 0 ? n : 0
}
const recommended = computed(() => store.current?.source_meta?.recommended_source || null)
function isPrimary(c) {
  const r = recommended.value
  return !!(r && r.sourceId === c.sourceId && String(r.mangaId) === String(c.mangaId))
}
function isInCompare(c) {
  return store.ver.cmpSel.some(s => s.sourceId === c.sourceId && String(s.mangaId) === String(c.mangaId))
}
const verPhaseLabel = computed(() => {
  const p = ver.value.discoverProgress || {}
  switch (ver.value.phase) {
    case 'start': case 'variants': return 'Buscando variantes del título…'
    case 'searching': return p.searchTotal ? `Rastreando fuentes… ${p.searched || 0}/${p.searchTotal}` : 'Rastreando fuentes…'
    case 'ranking': return p.rankTotal ? `Midiendo calidad… ${p.ranked || 0}/${p.rankTotal}` : 'Midiendo calidad…'
    default: return 'Trabajando…'
  }
})

// Cobertura de capítulos por fuente ("Fuentes por capítulo"): grid + asignación por rango
// + revisar actualizaciones. Store dedicado `versions.js`. La asignación (chapter_sources) es
// la ÚNICA fuente de verdad de la colección: nunca se completan huecos automáticamente.
const rangePanel = ref(null)   // { source } cuando el panel Desde/Hasta está abierto
const rangeFromCh = ref('')
const rangeToCh = ref('')
function loadCoverage(refresh = false) { vg.fetchCoverage(store.current?.id, store.current?.al_id, refresh, store.current?.source_meta?.sourceLang || '') }
// Misma clave que usa store.readOnline() internamente para marcar "en curso" — necesaria
// porque una fila con asignación por capítulo ya no se identifica por _sourceId/_mdChapterId.
function onlineBusyKey(c) { return c._assignedSource ? `assigned:${c.chapter}` : (c._sourceId || c._mdChapterId) }
function openRangePanel(source) { rangePanel.value = { source }; rangeFromCh.value = ''; rangeToCh.value = '' }
function closeRangePanel() { rangePanel.value = null }
async function confirmRangePanel() {
  if (!rangePanel.value) return
  const src = rangePanel.value.source
  await vg.assignSource({
    range: { from: rangeFromCh.value || null, to: rangeToCh.value || null },
    source: { sourceKind: src.sourceKind, sourceId: src.sourceId, mangaId: src.mangaId, sourceName: src.sourceName, sourceLang: src.sourceLang },
  })
  closeRangePanel()
}
function onGridAssignRange({ source, chapters }) {
  vg.assignSource({
    chapters,
    source: { sourceKind: source.sourceKind, sourceId: source.sourceId, mangaId: source.mangaId, sourceName: source.sourceName, sourceLang: source.sourceLang },
  })
}
// Vuelve al modo NAVEGAR: borra todas las asignaciones del manga (source:null, range:'all').
// El backend elimina las filas de chapter_sources; el getter collectionChapters vuelve a la
// fusión legada (local + fuente de origen + MangaDex) en cuanto `hasAssignments` pasa a false.
function clearAllAssignments() {
  vg.assignSource({ range: 'all', source: null })
}
const showFreshness = ref(false)
function toggleFreshness() {
  showFreshness.value = !showFreshness.value
  if (showFreshness.value && !vg.freshness.suggestions.length) vg.checkFreshness(store.current?.al_id)
}

// Reasignación puntual de UN capítulo ya descargado, desde la pestaña Capítulos.
const reassignOpen = ref(null)   // chapterNorm del capítulo con el menú abierto
function toggleReassign(chn) { reassignOpen.value = reassignOpen.value === chn ? null : chn }
function pickReassign(chn, src) {
  vg.assignSource({ chapters: [chn], source: { sourceKind: src.sourceKind, sourceId: src.sourceId, mangaId: src.mangaId, sourceName: src.sourceName, sourceLang: src.sourceLang } })
  reassignOpen.value = null
}

// Reparto manual de un tomo (cuando el reparto automático por nº de páginas ES falla)
const manualVol = ref(null)
const manualCounts = ref([])
function openManualSplit(vol) {
  manualVol.value = vol
  manualCounts.value = (vol.esChapters?.length ? vol.esChapters : [{ chapter: vol.chapterStartHint || '1' }])
    .map(c => ({ chapter: c.chapter, pages: c.pageCount || 0 }))
}
function closeManualSplit() { manualVol.value = null; manualCounts.value = [] }
const manualTotal = computed(() => manualCounts.value.reduce((sum, c) => sum + (Number(c.pages) || 0), 0))
function addManualRow() {
  const last = manualCounts.value[manualCounts.value.length - 1]
  const next = last ? (Number(last.chapter) || 0) + 1 : 1
  manualCounts.value.push({ chapter: String(next), pages: 0 })
}
function removeManualRow(i) { manualCounts.value.splice(i, 1) }
function submitManualSplit() {
  if (!manualVol.value) return
  const pageCounts = manualCounts.value.map(c => ({ chapter: c.chapter, pages: Number(c.pages) || 0 }))
  store.tpResolveVolumeManual(manualVol.value.prefix, pageCounts)
  closeManualSplit()
}

watch(m, (v) => {
  // Pestaña inicial: normalmente Capítulos, pero una apertura desde Descubrir pide Versiones
  // (proponer la «mejor versión» al instante). `pendingTab` es de un solo uso.
  tab.value = store.pendingTab || 'chapters'; store.pendingTab = null
  sel.value = new Set(); volName.value = v?.name || ''
  showManage.value = false; renameVal.value = v?.name || ''
  tomoCover.value = ''; clearBatch(); tpSel.value = new Set()
  // NO tocar `vg` aquí: su ciclo de vida (resetForManga + loadAssignedMap) lo gestiona
  // store.open() en la apertura del manga. Llamar `vg.reset()` aquí borraba en carrera el mapa
  // de asignación recién cargado y dejaba la selección persistida invisible. Solo reseteamos
  // la UI local del modal y el ranking de Versiones (namespace `ver`, independiente).
  store.verReset(); rangePanel.value = null; showFreshness.value = false; reassignOpen.value = null
  if (v) { store.resetMdex(); store.loadHealth(); store.colorPages = []; store.excludedPages = []; store.exportPreview = { pages: 0, est_mb: 0, upscaled_pages: 0, original_pages: 0 } }
  if (v && !Object.keys(store.models).length) store.loadModels()
  if (v) { store.loadDestinations(); store.loadColorStatus() }
}, { immediate: true })

// Live tomo estimate: recompute whenever the selection, quality or exclusions change
// while the export tab is open.
watch([sel, quality, codec, tab, () => store.excludedPages.length], () => {
  if (tab.value === 'tomo') store.loadExportPreview([...sel.value], quality.value, codec.value)
})

function applyVol(vol) {
  const { selected, label } = store.applyMdexVolume(vol)
  if (selected.length) { sel.value = new Set(selected); volName.value = label }
}

// Auto-organizar todos los tomos según MangaDex (un CBZ por volumen)
const autoVolCover = ref(true)
const exportingAll = ref(false)
const volPlan = computed(() => store.mdex.volumes.length ? store.volumePlan() : [])
const volPlanChapters = computed(() => volPlan.value.reduce((n, t) => n + t.chapters.length, 0))
async function exportAllTomos() {
  if (exportingAll.value) return
  exportingAll.value = true
  try {
    await store.exportAllVolumes({
      format: fmt.value, quality: quality.value, codec: codec.value,
      downscaleHalf: downscale.value, coverPerVolume: autoVolCover.value,
    })
  } finally { exportingAll.value = false }
}

function onTomoCover(e) {
  const f = e.target.files?.[0]; if (!f) return
  const r = new FileReader(); r.onload = () => { tomoCover.value = r.result }; r.readAsDataURL(f)
}

async function saveMeta() {
  const ok = await store.editMeta({ newTitle: renameVal.value.trim() })
  if (ok) showManage.value = false
}
function onCoverFile(e) {
  const f = e.target.files?.[0]; if (!f) return
  store.applyCoverFile(f)   // aplica + refresca la portada mostrada (cache-bust)
  e.target.value = ''
}

const toggleSel = (ch) => { const s = new Set(sel.value); s.has(ch) ? s.delete(ch) : s.add(ch); sel.value = s }
const allSelected = computed(() => store.chapters.length > 0 && sel.value.size === store.chapters.length)
const selectAll = () => { sel.value = allSelected.value ? new Set() : new Set(store.chapters.map(c => c.chapter)) }

async function doExport(toDrive = false) {
  if (!sel.value.size) return
  const chapters = [...sel.value].sort((a, b) => parseFloat(a) - parseFloat(b))
  await store.exportTomo({ chapters, volumeName: volName.value, format: fmt.value, quality: quality.value, codec: codec.value, downscaleHalf: downscale.value, coverB64: tomoCover.value || store.mdex.coverB64, toDrive })
}

// Escape cierra, el foco no se escapa por detrás y el fondo no scrollea.
const modalEl = ref(null)
useModal(() => !!m.value, closeModal, modalEl)
</script>

<template>
  <Teleport to="body">
    <Transition name="modal">
      <div v-if="m" class="ov" @click.self="closeModal">
        <!-- Ancho POR PESTAÑA: «Capítulos» está cómoda estrecha, pero «Versiones» mete nueve
             datos en una fila y «Exportar Tomo» es un grid de dos columnas — ahogados en 45rem.
             La transición hace que el ensanchado se lea como intencionado, no como un salto. -->
        <div ref="modalEl" class="modal" :class="[{ 'modal--art': !!coverArt }, `modal--${tab}`]" :style="coverStyle">
          <button class="modal__x" @click="closeModal"><Icon name="close" :size="18" /></button>

          <header class="modal__head">
            <div class="modal__ambient" aria-hidden="true" />
            <img v-if="m.cover" :src="imgProxy(m.cover, 180)" class="modal__cover" :alt="m.name" />
            <div v-else class="modal__cover modal__cover--ph"><Icon name="library" :size="30" /></div>
            <div class="modal__info">
              <h2 class="modal__title">{{ m.name }}</h2>

              <!-- Una SOLA línea de metadatos: antes esto eran cinco bloques apilados (subtítulo,
                   enlace, estado, leyenda de colores y barra de cobertura) con el mismo peso
                   visual, y el ojo no sabía dónde aterrizar. La leyenda desapareció: los badges
                   de la lista ya dicen "4K"/"PARCIAL" con palabras. -->
              <p class="modal__meta">
                <span>{{ chapters?.length || store.chapters.length }} capítulos</span>
                <span v-if="readTotal" class="modal__dot" />
                <span v-if="readTotal" class="modal__read">{{ readTotal }} leídos</span>
                <span v-if="coverage?.full" class="modal__dot" />
                <span v-if="coverage?.full" class="modal__4k">{{ coverage.full }} en 4K<template v-if="coverage.partial"> · {{ coverage.partial }} parcial</template></span>
                <span v-if="m.source_meta?.sourceName" class="modal__dot" />
                <span v-if="m.source_meta?.sourceName">{{ m.source_meta.sourceName }}</span>
                <a v-if="store.mdId" :href="'https://mangadex.org/title/' + store.mdId" target="_blank" rel="noopener" class="mdlink" title="Ver en MangaDex">
                  <Icon name="globe" :size="12" /> MangaDex
                </a>
              </p>

              <!-- Progreso de lectura de la SERIE: con 250 capítulos, un punto por fila no deja
                   ver el patrón; esta barra sí. -->
              <div v-if="readTotal && chapterTotal" class="modal__prog" :title="`${readTotal} de ${chapterTotal} capítulos leídos`">
                <span class="modal__prog-fill" :style="{ width: readPct + '%' }" />
              </div>

              <!-- Acción PRIMARIA, sola y grande. Antes «Continuar» vivía dentro del cuerpo
                   scrolleable, debajo de las pestañas: con la lista desplazada, lo que haces el
                   90% de las veces no estaba ni en pantalla. -->
              <div class="modal__hacts">
                <button v-if="store.continueInfo()" class="hgo" @click="store.resumeCurrent()">
                  <Icon name="play" :size="15" />
                  <span>Continuar · Cap. {{ formatChapter(store.continueInfo().chapter) }}</span>
                  <em v-if="store.continueInfo().total">pág. {{ store.continueInfo().page + 1 }}/{{ store.continueInfo().total }}</em>
                </button>
                <button v-else-if="firstChapter != null" class="hgo" @click="store.read(firstChapter)">
                  <Icon name="play" :size="15" /> <span>Empezar a leer</span>
                </button>
                <button v-if="canAddToLib" class="hbtn hbtn--accent" :class="{ 'is-added': inLib }"
                        :disabled="inLib || addingLib" @click="addToLib">
                  <Icon :name="inLib ? 'check' : 'plus'" :size="14" />
                  {{ inLib ? 'En biblioteca' : (addingLib ? 'Añadiendo…' : 'Añadir a biblioteca') }}
                </button>
                <Select :model-value="m.status || ''" aria-label="Estado de lectura"
                        :options="[{ value: '', label: 'Sin estado' }, ...Object.entries(MANGA_STATUS).map(([k, v]) => ({ value: k, label: v.label, color: v.color }))]"
                        @change="store.setStatus(m, $event)" />
                <button class="hbtn" @click="showManage = !showManage" :class="{ 'is-on': showManage }">Gestionar</button>
                <button v-if="!canAddToLib" class="hbtn" @click="store.scanCorrupt()">Verificar</button>
              </div>
            </div>
          </header>

          <!-- Management panel -->
          <Transition name="info">
            <div v-if="showManage" class="manage">
              <section class="manage__sect">
                <span class="manage__label">Información del manga</span>
                <div v-if="seriesSize" class="manage__size">
                  <Icon name="folder" :size="13" />
                  <span><b>{{ formatBytes(seriesSize.original_bytes) }}</b> original</span>
                  <span v-if="seriesSize.upscaled_bytes" class="manage__size-4k">· <b>{{ formatBytes(seriesSize.upscaled_bytes) }}</b> 4K</span>
                  <span class="manage__size-tot">· {{ formatBytes(seriesSize.original_bytes + seriesSize.upscaled_bytes) }} en total · {{ seriesSize.original_chapters }} cap.</span>
                </div>
                <!-- Liberar espacio en disco (como el anime): borra 4K u originales -->
                <div v-if="seriesSize && (seriesSize.upscaled_bytes || seriesSize.original_bytes)" class="manage__free">
                  <span class="manage__free-lbl"><Icon name="trash" :size="12" /> Liberar espacio</span>
                  <button v-if="seriesSize.upscaled_bytes" class="freebtn" :disabled="!!freeing" @click="freeSpace('upscaled')">
                    {{ freeing === 'upscaled' ? 'Borrando…' : `Borrar 4K (${formatBytes(seriesSize.upscaled_bytes)})` }}
                  </button>
                  <button v-if="seriesSize.original_bytes" class="freebtn" :disabled="!!freeing" @click="freeSpace('original')">
                    {{ freeing === 'original' ? 'Borrando…' : `Borrar descargados (${formatBytes(seriesSize.original_bytes)})` }}
                  </button>
                </div>
                <div class="manage__row">
                  <label class="mf"><span>Renombrar</span><input v-model="renameVal" type="text" /></label>
                </div>

                <!-- Selector visual de portada (AniList + MangaDex) -->
                <button class="cvp__toggle" @click="store.coverPicker.open ? store.closeCoverPicker() : store.openCoverPicker()">
                  <Icon name="library" :size="13" /> {{ store.coverPicker.open ? 'Ocultar portadas' : 'Elegir portada (AniList / MangaDex)' }}
                </button>
                <div v-if="store.coverPicker.open" class="cvp">
                  <div v-if="store.coverPicker.loading" class="cvp__load"><Spinner :size="11" /> Buscando portadas online…</div>
                  <template v-else>
                    <div class="cvp__grid">
                      <div v-if="store.coverPicker.current" class="cvp__item is-current" title="Portada actual">
                        <img :src="imgProxy(store.coverPicker.current)" referrerpolicy="no-referrer" alt="" />
                        <span class="cvp__tag">Actual</span>
                      </div>
                      <button v-for="c in store.coverPicker.anilist" :key="'al' + c.url" class="cvp__item"
                              :disabled="!!store.coverPicker.applying" @click="store.applyCover(c.url)" title="Usar esta portada">
                        <img :src="imgProxy(c.thumb)" referrerpolicy="no-referrer" loading="lazy" alt="" />
                        <span class="cvp__tag cvp__tag--al">AniList</span>
                        <span v-if="store.coverPicker.applying === c.url" class="cvp__busy"><Spinner :size="11" /></span>
                      </button>
                      <button v-for="(c, i) in store.coverPicker.mangadex" :key="'md' + i" class="cvp__item"
                              :disabled="!!store.coverPicker.applying" @click="store.applyCover(c.url)" title="Usar esta portada">
                        <img :src="imgProxy(c.thumb)" referrerpolicy="no-referrer" loading="lazy" alt="" />
                        <span v-if="c.volume && c.volume !== '?'" class="cvp__tag">Vol {{ c.volume }}</span>
                        <span v-if="store.coverPicker.applying === c.url" class="cvp__busy"><Spinner :size="11" /></span>
                      </button>
                    </div>
                    <p v-if="!store.coverPicker.anilist.length && !store.coverPicker.mangadex.length" class="cvp__empty">
                      No se encontraron portadas online para esta serie. Usa "Subir portada" (en Gestionar) para poner una tuya.
                    </p>
                  </template>
                </div>

                <div class="manage__actions">
                  <button class="delbtn" @click="store.deleteManga()" title="Eliminar este manga de la biblioteca"><Icon name="close" :size="13" /> Eliminar manga</button>
                  <span class="manage__spacer" />
                  <label class="upbtn"><Icon name="library" :size="13" /> Subir portada<input type="file" accept="image/*" @change="onCoverFile" hidden /></label>
                  <button class="savebtn" @click="saveMeta">Guardar cambios</button>
                </div>
              </section>
              <section class="manage__sect manage__sect--up">
                <span class="manage__label">Escalado 4K <em>· se aplica al instante</em></span>
                <div class="manage__row">
                  <div class="mf">
                    <span>Modelo</span>
                    <!-- Mismo desplegable que el resto de la app; la etiqueta Color/B&N viaja como
                         `hint`, igual que en Ajustes. Antes esto era un menú propio de ~100 líneas
                         que reimplementaba teletransporte, colocación, clic fuera y Escape. -->
                    <Select block :model-value="store.activeModel" aria-label="Modelo de escalado"
                            :options="modelEntries.map(e => ({ value: e.key, label: e.label, hint: e.color ? 'Color' : 'B&N' }))"
                            @change="store.setModel($event)" />
                  </div>
                  <label class="mf mf--chk"><span>Modo eco <em>(deja correr MPV al escalar)</em></span>
                    <input type="checkbox" :checked="store.eco" @change="store.setEco($event.target.checked)" />
                  </label>
                </div>
                <div class="manage__row manage__row--color" v-if="hasColorModel">
                  <button class="colorbtn" @click="store.openColorPickerAll()" title="Muestra las páginas a color de todo el manga para que elijas cuáles escalar con el modelo a color">
                    <Icon name="palette" :size="13" /> Escalar páginas a color
                  </button>
                  <span class="manage__hint">Detecta las páginas a color de todo el manga y te deja elegir cuáles escalar con el modelo a color. El progreso sale en Actividad.</span>
                </div>
              </section>
            </div>
          </Transition>

          <div class="modal__tabs">
            <button class="mtab mtab--sep" :class="{ 'is-active': tab === 'chapters' }" @click="tab = 'chapters'">
              <Icon name="book" :size="13" /> Capítulos
            </button>
            <button class="mtab" :class="{ 'is-active': tab === 'versions' }" @click="tab = 'versions'"><Icon name="spark" :size="13" /> Versiones</button>
            <button class="mtab" :class="{ 'is-active': tab === 'translate' }" @click="tab = 'translate'"><Icon name="globe" :size="13" /> Traducir
              <span v-if="store.current?.transplant_meta?.translated?.length" class="mtab__badge">ES</span>
            </button>
            <button class="mtab" :class="{ 'is-active': tab === 'tomo' }" @click="tab = 'tomo'"><Icon name="library" :size="13" /> Exportar Tomo</button>
            <button class="mtab" :class="{ 'is-active': tab === 'recs' }" @click="tab = 'recs'"><Icon name="spark" :size="13" /> Recomendados</button>
          </div>

          <div class="modal__body">
            <!-- Carga: esqueleto con FORMA de lista de capítulos (no un spinner pelado) para que
                 la apertura se sienta como contenido llegando. La cabecera (portada/título) ya se
                 pintó al instante desde store.current; esto solo cubre el cuerpo. -->
            <div v-if="store.modalLoading" class="modal__skel">
              <Skeleton variant="line" width="40%" height="1rem" />
              <div class="modal__skel-rows">
                <div v-for="i in 8" :key="i" class="modal__skel-row">
                  <Skeleton variant="line" :width="`${58 + (i * 37) % 34}%`" height="0.9rem" />
                  <Skeleton variant="block" width="1.5rem" height="1.5rem" radius="var(--r-sm)" />
                </div>
              </div>
            </div>
            <div v-else-if="!store.chapters.length && !store.hasSourceMeta && !vg.hasAssignments && tab !== 'recs'" class="empty">Sin capítulos descargados.</div>

            <!-- RECOMENDADOS -->
            <div v-else-if="tab === 'recs'" class="recs">
              <MangaRecRail v-if="store.recsLoading || store.recs.length"
                            :items="store.recs" :loading="store.recsLoading"
                            layout="grid" title="Similares a este"
                            subtitle="Otras series que gustan a lectores de esta"
                            @select="store.discoverRec" />
              <div v-else class="empty">No hay recomendaciones para esta serie.</div>
            </div>

            <!-- TOMO EXPORT -->
            <div v-else-if="tab === 'tomo'" class="tomo">
              <div class="tomo__form">
                <label class="fld"><span>Nombre del tomo</span><input v-model="volName" type="text" placeholder="Volumen 1" /></label>
                <div class="fld-row">
                  <label class="fld"><span>Formato</span>
                    <Select block v-model="fmt" aria-label="Formato"
                            :options="[{ value: 'cbz', label: 'CBZ' }, { value: 'cbr', label: 'CBR' }]" />
                  </label>
                  <label class="fld"><span>Compresión</span>
                    <Select block v-model="codec" aria-label="Compresión" :options="[
                      { value: 'jpeg', label: 'JPEG' },
                      { value: 'webp', label: 'WebP', hint: 'menor tamaño' }]" />
                  </label>
                  <label class="fld"><span>Calidad: {{ quality }}</span><input v-model.number="quality" type="range" :min="qMin" max="100" /></label>
                </div>
                <p v-if="codec === 'webp'" class="codec-hint">WebP pesa menos a calidad equivalente y conserva mejor el texto al comprimir. Requiere que tu lector lo soporte (la mayoría de los modernos sí).</p>
                <label class="chk"><input v-model="downscale" type="checkbox" /> Reducir a la mitad (menor tamaño)</label>
                <label class="upbtn"><Icon name="library" :size="13" /> {{ tomoCover ? 'Portada elegida ✓' : 'Portada del tomo (opcional)' }}<input type="file" accept="image/*" @change="onTomoCover" hidden /></label>
                <div v-if="sel.size && store.exportPreview.pages" class="tprev">
                  <span class="tprev__main">{{ store.exportPreview.pages }} págs · ~{{ store.exportPreview.est_mb }} MB</span>
                  <span v-if="store.exportPreview.upscaled_pages" class="tprev__tag tprev__tag--4k">{{ store.exportPreview.upscaled_pages }} en 4K</span>
                  <span v-if="store.exportPreview.original_pages" class="tprev__tag">{{ store.exportPreview.original_pages }} original</span>
                </div>
                <button class="exportbtn" :disabled="!sel.size" @click="doExport(false)">
                  <Icon name="download" :size="15" /> Exportar {{ sel.size }} {{ sel.size === 1 ? 'capítulo' : 'capítulos' }}
                </button>
                <button v-if="store.drive.connected" class="exportbtn exportbtn--drive" :disabled="!sel.size" @click="doExport(true)">
                  <Icon name="globe" :size="15" /> Exportar a Google Drive
                </button>
                <div class="dests">
                  <button v-if="!store.drive.connected" class="dlink" @click="store.connectDrive()">Conectar Google Drive</button>
                  <template v-else><span class="dok">Drive: {{ store.drive.email }}</span><button class="dlink" @click="store.disconnectDrive()">Desconectar</button></template>
                  <a v-if="store.webdav.localUrl" :href="store.webdav.localUrl" target="_blank" class="dlink">Biblioteca móvil (PC) ↗</a>
                </div>

                <!-- color pages exclusion -->
                <div class="colors">
                  <button class="btn-xs" :disabled="store.colorLoading || !sel.size" @click="detectColors">
                    <Spinner v-if="store.colorLoading" :size="11" />Detectar páginas a color
                  </button>
                  <div v-if="store.colorPages.length" class="colors__grid">
                    <button v-for="cp in store.colorPages" :key="cp.filename" class="colorpg" :class="{ 'is-excl': store.excludedPages.includes(cp.filename) }"
                            :title="cp.label + (store.excludedPages.includes(cp.filename) ? ' (excluida)' : '')" @click="store.toggleExclude(cp.filename)">
                      <img :src="cp.url" loading="lazy" alt="" />
                      <span v-if="store.excludedPages.includes(cp.filename)" class="colorpg__x"><Icon name="close" :size="12" /></span>
                    </button>
                  </div>
                  <p v-else-if="!store.colorLoading && store.colorPages.length === 0 && sel.size" class="colors__hint">Pulsa para detectar y excluir páginas a color del tomo.</p>
                </div>
              </div>
              <!-- MangaDex volumes + covers -->
              <div class="mdex">
                <div class="mdex__head">
                  <span>Tomos y portadas de MangaDex</span>
                  <button class="btn-xs" :disabled="store.mdex.volumesLoading" @click="store.loadMdexVolumes(); store.loadMdexCovers()">
                    <Spinner v-if="store.mdex.volumesLoading" :size="11" />{{ store.mdex.volumes.length ? 'Recargar' : 'Cargar' }}
                  </button>
                </div>
                <!-- Coincidencia activa + corrección manual (la auto-resolución puede acertar
                     aproximado; el buscador permite fijar la obra correcta y sus portadas). -->
                <p v-if="store.mdex.mdManga" class="mdex__match" :class="{ 'is-approx': store.mdex.approx }">
                  {{ store.mdex.approx ? '≈' : '✓' }} {{ store.mdex.mdManga.title }}
                  <a :href="'https://mangadex.org/title/' + store.mdex.id" target="_blank" rel="noopener">↗</a>
                </p>
                <div class="mdex__search">
                  <input v-model="store.mdex.search" placeholder="¿Manga incorrecto? Búscalo en MangaDex…"
                         @keyup.enter="store.searchMdexForTomo()" />
                  <button class="btn-xs" :disabled="store.mdex.searching" @click="store.searchMdexForTomo()">
                    <Spinner v-if="store.mdex.searching" :size="11" />Buscar
                  </button>
                </div>
                <div v-if="store.mdex.results.length" class="mdex__results">
                  <button v-for="r in store.mdex.results" :key="r.id" class="mdres" @click="store.selectMdexEntry(r)">
                    <img v-if="r.cover" :src="imgProxy(r.cover)" loading="lazy" alt="" />
                    <span class="mdres__t">{{ r.title }}<small v-if="r.year"> · {{ r.year }}</small></span>
                  </button>
                </div>
                <div v-if="store.mdex.volumes.length" class="mdex__vols">
                  <button v-for="v in store.mdex.volumes" :key="v.volume" class="volchip" @click="applyVol(v)">{{ v.label || ('Tomo ' + v.volume) }}</button>
                </div>
                <!-- Auto-organizar: un CBZ por cada tomo, con los caps descargados que le tocan -->
                <div v-if="volPlan.length" class="mdex__auto">
                  <div class="mdex__auto-info">
                    <strong>{{ volPlan.length }}</strong> tomo(s) · <strong>{{ volPlanChapters }}</strong> caps descargados se organizarán automáticamente
                  </div>
                  <label class="mdex__auto-cov"><input type="checkbox" v-model="autoVolCover" /> Portada por tomo</label>
                  <button class="exportbtn exportbtn--auto" :disabled="exportingAll" @click="exportAllTomos">
                    <Spinner v-if="exportingAll" :size="11" /><Icon v-else name="library" :size="13" /> Exportar todos los tomos
                  </button>
                </div>
                <div v-if="store.mdex.covers.length" class="mdex__covers">
                  <button v-for="c in store.mdex.covers" :key="c.id || c.url" class="covsel" :class="{ 'is-sel': store.mdex.selectedCover?.url === c.url }" @click="store.selectMdexCover(c)">
                    <img :src="c.url256 || c.url" loading="lazy" alt="" />
                    <span v-if="c.volume && c.volume !== 'none'" class="covsel__v">{{ c.volume }}</span>
                    <span v-if="store.mdex.coverLoadingId === c.id" class="covsel__load"><Spinner :size="11" /></span>
                  </button>
                </div>
              </div>

              <div class="tomo__pick">
                <button class="selall" @click="selectAll">{{ allSelected ? 'Ninguno' : 'Todos' }}</button>
                <ul class="picklist">
                  <li v-for="c in store.sortedChapters" :key="c.chapter" class="pick" :class="{ 'is-sel': sel.has(c.chapter) }" @click="toggleSel(c.chapter)">
                    <span class="pick__box"><Icon v-if="sel.has(c.chapter)" name="check" :size="12" /></span>
                    <span class="pick__num">Cap. {{ c.chapter }}</span>
                    <span v-if="upState(c.chapter) === true" class="pick__4k">4K</span>
                  </li>
                </ul>
              </div>
            </div>

            <!-- VERSIONES (ranking de calidad de imagen) -->
            <div v-if="tab === 'versions' && !store.modalLoading" class="vr">
              <p class="vr__lead">Busca todas las versiones de este manga en las fuentes y compáralas por calidad de imagen (resolución nativa × nitidez). Tu versión local aparece como referencia.</p>

              <!-- Referencia de completitud: cuántos capítulos hay AHORA del manga -->
              <div v-if="refChapters" class="vr__ref">
                <Icon name="library" :size="14" />
                <span>Este manga tiene <strong>{{ refChapters }} capítulos</strong> actualmente</span>
                <span class="vr__refsrc">según {{ refSourceLabel }}</span>
              </div>

              <!-- descubrimiento en curso -->
              <div v-if="ver.loading" class="vr__disc">
                <Spinner :size="18" />
                <span class="muted">{{ verPhaseLabel }}</span>
              </div>

              <!-- aún sin buscar -->
              <div v-else-if="ver.phase !== 'ready'" class="vr__cta">
                <button class="hbtn hbtn--accent" @click="store.verDiscover()"><Icon name="spark" :size="14" /> Buscar versiones</button>
                <span v-if="ver.phase === 'error'" class="tl__err">No se encontraron fuentes. ¿Suwayomi en línea?</span>
                <span v-else class="vr__hint">Requiere Suwayomi en línea.</span>
              </div>

              <!-- resultados -->
              <template v-else>
                <div class="vr__head">
                  <div class="vr__filter">
                    <span class="muted">Idioma</span>
                    <Select :model-value="ver.langFilter" aria-label="Idioma" :options="[
                      { value: '', label: 'Todos', hint: String(ver.versions.length) },
                      ...verLangs.map(l => ({ value: l, label: `${flag(l)} ${l}`, hint: String(ver.byLang[l].length) }))]"
                      @change="store.verSetLangFilter($event)" />
                  </div>
                  <button class="btn-xs" @click="store.verDiscover(true)" title="Volver a buscar (ignora la caché)">↻ Buscar de nuevo</button>
                </div>

                <!-- versión principal fijada -->
                <div v-if="recommended" class="vr__primary">
                  <Icon name="check" :size="13" /> Principal: <strong>{{ recommended.sourceName }}</strong>
                  <span v-if="recommended.quality?.height" class="muted">· {{ recommended.quality.height }}px</span>
                </div>

                <!-- bandeja de comparación A|B -->
                <div v-if="ver.cmpSel.length" class="vr__tray">
                  <span class="muted">Comparar:</span>
                  <span v-for="(c, i) in ver.cmpSel" :key="i" class="vr__chip">
                    {{ c.sourceName }}
                    <button class="vr__chipx" @click="store.verToggleCompare(c)"><Icon name="close" :size="10" /></button>
                  </span>
                  <span v-if="ver.cmpSel.length < 2" class="vr__trayhint">elige {{ 2 - ver.cmpSel.length }} más</span>
                  <button class="hbtn hbtn--accent vr__traygo" :disabled="ver.cmpSel.length !== 2" @click="store.verRunCompare()">
                    <Icon name="globe" :size="13" /> Comparar A|B
                  </button>
                </div>

                <!-- descarga de versión en curso -->
                <div v-if="ver.dl" class="vr__dl">
                  <Spinner :size="14" />
                  <span>Descargando capítulos que faltan… {{ ver.dl.done }}/{{ ver.dl.total || '?' }}</span>
                </div>

                <!-- línea base: tu versión local -->
                <div v-if="ver.local" class="vr__row vr__row--local" :class="{ 'vr__row--cmp': isInCompare(localCand) }">
                  <div class="vr__main">
                    <span class="vr__src"><Icon name="library" :size="13" /> Tu versión local <span class="vr__badge vr__badge--actual">ACTUAL</span></span>
                    <span class="vr__q">{{ ver.local.height }}px · score {{ ver.local.score }}<template v-if="ver.local.totalChapters"> · <strong class="vr__caps">{{ ver.local.totalChapters }} cap.</strong></template></span>
                    <span v-if="isUpToDate(localCand)" class="vr__complete">✓ al día</span>
                    <span v-else-if="chaptersBehind(localCand)" class="vr__behind">faltan {{ chaptersBehind(localCand) }}</span>
                  </div>
                  <div class="vr__acts">
                    <button class="vr__eye" :class="{ 'is-on': isInCompare(localCand) }" @click="store.verToggleCompare(localCand)" title="Añadir a comparación A|B">
                      <Icon name="globe" :size="13" /> A|B
                    </button>
                  </div>
                </div>

                <!-- ranking de candidatos -->
                <ul v-if="verList.length" class="vr__list">
                  <li v-for="c in verList" :key="verSampleKey(c)" class="vr__item">
                    <div class="vr__row" :class="{ 'vr__row--best': c === verBest && !isCurrentVersion(c) && !isPrimary(c), 'vr__row--primary': isPrimary(c), 'vr__row--cmp': isInCompare(c) }">
                      <div class="vr__main">
                        <span class="vr__src">
                          {{ c.sourceName }} <em class="vr__lang">{{ flag(c.sourceLang) }} {{ c.sourceLang }}</em>
                          <span v-if="isPrimary(c)" class="vr__badge vr__badge--primary">★ PRINCIPAL</span>
                          <span v-else-if="isCurrentVersion(c)" class="vr__badge vr__badge--actual">ACTUAL</span>
                          <span v-else-if="c === verBest" class="vr__badge vr__badge--best">★ mejor calidad</span>
                        </span>
                        <span class="vr__q">{{ c.quality?.height }}px · score {{ c.quality?.score }}<template v-if="c.quality?.totalChapters"> · <strong class="vr__caps">{{ c.quality.totalChapters }} cap.</strong></template></span>
                        <span v-if="isUpToDate(c)" class="vr__complete" :title="`Llega al capítulo ${versionReach(c)} — al día con el manga (${refChapters})`">✓ al día</span>
                        <span v-else-if="chaptersBehind(c)" class="vr__behind" :title="`Su último capítulo es el ${versionReach(c)}; el manga va por el ${refChapters}`">faltan {{ chaptersBehind(c) }}</span>
                        <span v-if="c.match != null" class="vg__match" :class="{ 'vg__match--low': c.match < 0.95 }" :title="'Parecido de título con &quot;' + m?.name + '&quot; — mismo match que la Cobertura por capítulo'">{{ Math.round(c.match * 100) }}% título</span>
                        <span v-if="isIrregular(c.quality)" class="vr__irr" :title="`Calidad irregular entre capítulos (${c.quality.heightMin}–${c.quality.heightMax}px). Algún capítulo es notablemente peor — penalizado en el ranking.`">⚠ irregular</span>
                      </div>
                      <div class="vr__acts">
                        <button class="vr__eye" :class="{ 'is-on': ver.sample.key === verSampleKey(c) && ver.sample.open }" @click="store.verReadSample(c)" title="Leer páginas de muestra">
                          <Icon name="search" :size="13" />
                        </button>
                        <button class="vr__eye" :class="{ 'is-on': isInCompare(c) }" @click="store.verToggleCompare(c)" title="Añadir a comparación A|B">
                          <Icon name="globe" :size="13" /> A|B
                        </button>
                        <button class="vr__eye" :disabled="!!ver.dl" @click="store.verDownloadVersion(c)" title="Descargar de esta versión los capítulos que falten">
                          <Icon name="download" :size="13" />
                        </button>
                        <button class="vr__fix" :class="{ 'is-on': isPrimary(c) }" @click="store.verSetPrimary(c)" :title="isPrimary(c) ? 'Quitar como principal' : 'Fijar como versión principal'">
                          {{ isPrimary(c) ? 'Fijada ✓' : 'Fijar' }}
                        </button>
                      </div>
                    </div>
                    <!-- tira de páginas de muestra -->
                    <div v-if="ver.sample.key === verSampleKey(c) && ver.sample.open" class="vr__prev">
                      <div v-if="ver.sample.loading" class="vr__prevload"><Spinner :size="16" /></div>
                      <div v-else-if="ver.sample.pages.length" class="tl__strip">
                        <a v-for="(u, i) in ver.sample.pages" :key="i" :href="u" target="_blank" rel="noopener" class="tl__thumb" :title="`Página ${i + 1}`">
                          <img :src="u" loading="lazy" referrerpolicy="no-referrer" />
                        </a>
                      </div>
                      <div v-else class="muted vr__prevempty">Sin páginas de muestra.</div>
                    </div>
                  </li>
                </ul>
                <div v-else class="empty">No hay versiones en este idioma.</div>
              </template>

              <!-- ── Fuentes por capítulo: cobertura + asignación por rango + huecos ── -->
              <div class="vg">
                <div class="vg__head">
                  <h4 class="vg__title">Cobertura por capítulo</h4>
                  <div class="vg__actions">
                    <button v-if="vg.phase !== 'ready'" class="hbtn" :disabled="vg.loading" @click="loadCoverage(false)">
                      <Spinner v-if="vg.loading" :size="13" /><Icon v-else name="grid" :size="14" /> Ver cobertura
                    </button>
                    <button v-if="vg.phase === 'ready'" class="hbtn" :disabled="vg.loading" @click="loadCoverage(true)" title="Recalcular saltando la caché (vuelve a medir todas las fuentes)">
                      <Spinner v-if="vg.loading" :size="13" /><Icon v-else name="refresh" :size="14" /> Recalcular
                    </button>
                    <button v-if="vg.phase === 'ready'" class="hbtn" @click="toggleFreshness">
                      <Icon name="spark" :size="14" /> Revisar actualizaciones
                    </button>
                    <button v-if="vg.hasAssignments" class="hbtn hbtn--danger" @click="clearAllAssignments"
                      title="Elimina todas las asignaciones de este manga y vuelve al modo normal (fuente de origen + MangaDex)">
                      <Icon name="trash" :size="14" /> Vaciar selección
                    </button>
                  </div>
                </div>
                <p class="vg__lead">Cada fuente marcada en <span class="vg__swcyan">cian</span> es la fuente ASIGNADA a ese capítulo; en <span class="vg__swblue">azul</span>, alternativas disponibles. Arrastra sobre la fila de una fuente para asignar un rango de capítulos. Solo se descargan/leen los capítulos que asignes — la app nunca completa huecos por su cuenta.</p>

                <div v-if="vg.loading" class="vr__disc"><Spinner :size="16" /><span class="muted">{{ vg.phase === 'coverage' ? `Calculando cobertura… ${vg.progress.covered || 0}/${vg.progress.coverTotal || 0}` : 'Buscando fuentes…' }}</span></div>

                <div v-if="showFreshness" class="vg__fresh">
                  <div v-if="vg.freshness.loading" class="vr__disc"><Spinner :size="16" /><span class="muted">Revisando fuentes…</span></div>
                  <ul v-else-if="vg.freshness.suggestions.length" class="vg__suglist">
                    <li v-for="sug in vg.freshness.suggestions" :key="sug.chapter + sug.reason" class="vg__sug">
                      <span class="vg__sugtxt">
                        Cap. {{ formatChapter(sug.chapter) }} —
                        <template v-if="sug.reason === 'new'">nuevo en {{ sug.betterSource.sourceName }}</template>
                        <template v-else>mejor calidad en {{ sug.betterSource.sourceName }} (+{{ sug.delta }})</template>
                      </span>
                      <button class="hbtn hbtn--accent" @click="vg.applySuggestion(sug)">Aplicar</button>
                    </li>
                  </ul>
                  <p v-else class="muted">Nada nuevo por ahora.</p>
                </div>

                <template v-if="vg.phase === 'ready'">
                  <VersionsCoverageGrid :sources="vg.sources" :assigned="vg.assigned" :total-known-chapters="vg.totalKnownChapters"
                    @assign-range="onGridAssignRange" @select-source="openRangePanel" />

                  <ul class="vg__srclist">
                    <li v-for="s in vg.sources" :key="s.sourceId + '_' + s.mangaId" class="vg__srcrow">
                      <span class="vg__srcname">{{ s.sourceName }} <em class="vr__lang">{{ flag(s.sourceLang) }}</em></span>
                      <!-- El backend ya descarta <85% de match (_COVERAGE_MATCH_MIN); lo cercano al piso
                           (<95%) se resalta como "aún así, vale la pena mirarlo dos veces". -->
                      <span v-if="s.match != null" class="vg__match" :class="{ 'vg__match--low': s.match < 0.95 }" :title="'Parecido de título con &quot;' + m?.name + '&quot; — valores cercanos al 85% pueden ser una obra distinta, revisa antes de confiar'">{{ Math.round(s.match * 100) }}% título</span>
                      <span class="vg__srcmeta">{{ s.count }} cap.<template v-if="s.completeness != null"> · {{ Math.round(s.completeness * 100) }}% completo</template><template v-if="s.updateFrequency?.medianDaysBetweenChapters"> · ~{{ s.updateFrequency.medianDaysBetweenChapters }}d/cap</template></span>
                      <button class="vr__fix" @click="openRangePanel(s)">Asignar rango…</button>
                    </li>
                  </ul>

                  <div v-if="rangePanel" class="vg__rangepanel">
                    <span>Asignar a <strong>{{ rangePanel.source.sourceName }}</strong>:</span>
                    <input class="vg__rangein" type="text" placeholder="Desde (ej. 1)" v-model="rangeFromCh" />
                    <span>–</span>
                    <input class="vg__rangein" type="text" placeholder="Hasta (vacío = fin)" v-model="rangeToCh" />
                    <button class="hbtn hbtn--accent" @click="confirmRangePanel">Aplicar</button>
                    <button class="hbtn" @click="closeRangePanel">Cancelar</button>
                  </div>
                </template>
              </div>
            </div>

            <!-- TRADUCIR (trasplante) -->
            <div v-if="tab === 'translate' && !store.modalLoading" class="tl">
              <p class="tl__lead">Busca la mejor fuente de arte (cualquier idioma) y una en español, y trasplanta el texto ES sobre el arte HD. El resultado reemplaza los capítulos del manga.</p>

              <!-- Arte local: usar las páginas YA descargadas+escaladas (4K) como base del trasplante
                   en vez de buscar una fuente de arte externa. Solo para descargados con capítulos
                   locales; en importados el arte SIEMPRE es local (no hay opción). Siempre visible en
                   la pestaña — al cambiarlo se vuelve a descubrir (solo se busca la fuente ES). -->
              <label v-if="!m?.source_meta?.imported && store.chapters.length" class="tl__localart">
                <input type="checkbox" :checked="tp.artLocal" @change="store.tpSetArtLocal($event.target.checked)" :disabled="tp.loading || tp.running" />
                <span>
                  Usar mis páginas descargadas y escaladas (4K) como arte
                  <em class="tl__localhint">Solo se busca la fuente en español; el texto se trasplanta sobre tu copia local.</em>
                </span>
              </label>

              <!-- tomos importados sin repartir en capítulos reales (aún no se intentó) -->
              <div v-if="tp.pendingVolumes.length" class="tl__vol">
                <div class="tl__volmsg">
                  <Icon name="folder" :size="14" />
                  <span>{{ tp.pendingVolumes.length }} tomo(s) importado(s) abarcan varios capítulos — hace falta repartirlos para que el match de traducción funcione por capítulo.</span>
                </div>
                <button class="hbtn hbtn--accent" :disabled="tp.volResolving || !tp.esSel" @click="store.tpResolveVolumes()">
                  <Spinner v-if="tp.volResolving" :size="13" /><Icon v-else name="globe" :size="14" /> Repartir capítulos
                </button>
                <span v-if="!tp.esSel" class="tl__err">Elige primero una fuente en español.</span>
              </div>

              <!-- tomos que no se pudieron repartir automáticamente (page count no cuadra) -->
              <div v-if="tp.unresolvedVolumes.length" class="tl__vol tl__vol--warn">
                <div v-for="vol in tp.unresolvedVolumes" :key="vol.prefix" class="tl__volitem">
                  <div class="tl__volmsg">
                    <Icon name="spark" :size="14" />
                    <span>Tomo de {{ vol.pages }} pág. (sugerido desde cap. {{ vol.chapterStartHint || vol.esChapters?.[0]?.chapter || '?' }}): no encontré una racha de capítulos ES que cuadre exacto — ajusta a mano.</span>
                  </div>
                  <button v-if="manualVol?.prefix !== vol.prefix" class="tl__re2" @click="openManualSplit(vol)">Repartir a mano</button>
                  <div v-else class="tl__manual">
                    <div class="tl__manualrow" v-for="(c, i) in manualCounts" :key="i">
                      <span class="muted">Cap.</span>
                      <input type="text" class="tl__manchin" v-model="c.chapter">
                      <input type="number" min="0" class="tl__maninput" v-model="c.pages">
                      <span class="muted">pág.</span>
                      <button class="tl__rmrow" @click="removeManualRow(i)"><Icon name="close" :size="11" /></button>
                    </div>
                    <button class="tl__re2" @click="addManualRow">+ Añadir capítulo</button>
                    <div class="tl__manualtotal" :class="{ 'is-bad': manualTotal !== vol.pages }">
                      Total: {{ manualTotal }} / {{ vol.pages }} pág.
                    </div>
                    <div class="tl__manualacts">
                      <button class="hbtn hbtn--accent" :disabled="manualTotal !== vol.pages" @click="submitManualSplit">Guardar reparto</button>
                      <button class="tl__re2" @click="closeManualSplit">Cancelar</button>
                    </div>
                  </div>
                </div>
              </div>

              <!-- descubrimiento en curso -->
              <div v-if="tp.loading" class="tl__disc">
                <Spinner :size="18" />
                <span class="muted">{{ tpPhaseLabel }}</span>
              </div>

              <!-- sin fuentes aún -->
              <div v-else-if="tp.phase !== 'ready'" class="tl__cta">
                <button class="hbtn hbtn--accent" @click="store.tpDiscover()">
                  <Icon name="globe" :size="14" />
                  {{ (m?.source_meta?.imported || tp.artLocal) ? 'Buscar fuente en español' : 'Buscar mejor fuente' }}
                </button>
                <span v-if="tp.phase === 'error'" class="tl__err">No se encontraron fuentes. ¿Suwayomi en línea?</span>
              </div>

              <!-- fuentes elegidas + capítulos -->
              <template v-else>
                <div class="tl__picks">
                  <div class="tl__pick">
                    <div class="tl__pickh">Arte <button class="tl__re" @click="store.tpDiscover()" title="Volver a buscar">↻</button></div>
                    <div v-if="tp.artSel?.local" class="tl__cand">
                      <span class="tl__src">{{ m?.source_meta?.imported ? 'Arte local (importado)' : 'Arte local (descargado + escalado 4K)' }}</span>
                    </div>
                    <div v-else-if="tp.artSel" class="tl__cand">
                      <span class="tl__src">{{ tp.artSel.sourceName }} · {{ tp.artSel.sourceLang }}</span>
                      <span v-if="tp.artSel.quality" class="tl__q">{{ tp.artSel.quality.height }}px · score {{ tp.artSel.quality.score }}</span>
                    </div>
                    <Select v-if="tp.artCands.length" block aria-label="Fuente del arte"
                            :model-value="store._candKey(tp.artSel)" :options="candOpts(tp.artCands, true)" @change="onArt" />
                  </div>
                  <div class="tl__pick">
                    <div class="tl__pickh">Español</div>
                    <div v-if="tp.esSel" class="tl__cand">
                      <span class="tl__src">{{ tp.esSel.sourceName }} · {{ tp.esSel.sourceLang }}</span>
                      <span v-if="tp.esSel.quality" class="tl__q">{{ tp.esSel.quality.height }}px</span>
                    </div>
                    <Select v-if="tp.esCands.length" block aria-label="Fuente en español"
                            :model-value="store._candKey(tp.esSel)" :options="candOpts(tp.esCands, false)" @change="onEs" />
                  </div>
                </div>

                <!-- progreso de ejecución -->
                <div v-if="tp.running" class="tl__run">
                  <div class="tl__bar"><div class="tl__fill" :style="{ width: tpRunPct + '%' }" /></div>
                  <div class="tl__runinfo">
                    <span class="muted">Cap. {{ tp.runStatus?.chapterDone || 0 }}/{{ tp.runStatus?.chapterTotal || 0 }}
                      <template v-if="tp.runStatus?.chapter"> · #{{ tp.runStatus.chapter }}</template>
                      <template v-if="tp.runStatus?.phase === 'compose' && tp.runStatus?.pageTotal"> · pág {{ tp.runStatus.pageDone }}/{{ tp.runStatus.pageTotal }}</template>
                      <template v-else-if="tp.runStatus?.phase === 'download'"> · descargando</template>
                      <template v-else-if="tp.runStatus?.phase === 'cancelling'"> · deteniendo…</template>
                    </span>
                    <button class="tl__stop" @click="store.tpCancel()"><Icon name="close" :size="13" /> Parar</button>
                  </div>
                </div>

                <!-- lista de capítulos -->
                <div class="tl__chhead">
                  <span>Capítulos a traducir <span v-if="tp.chapters.length" class="muted">({{ tp.chapters.length }})</span></span>
                  <div class="tl__acts">
                    <button class="btn-xs" :disabled="tp.running || !tpSel.size" @click="store.tpRun([...tpSel])">Traducir {{ tpSel.size || '' }} sel.</button>
                    <button class="btn-xs btn-xs--accent" :disabled="tp.running || !tp.chapters.length" @click="store.tpRun('all')">Traducir todos</button>
                  </div>
                </div>
                <div v-if="tp.chaptersLoading" class="center"><Spinner :size="20" /></div>
                <ul v-else-if="tp.chapters.length" class="tl__chaps">
                  <li v-for="c in tp.chapters" :key="c.chapter" class="tl__chapwrap">
                    <div class="tl__chap" :class="{ 'is-sel': tpSel.has(c.chapter) }">
                      <span class="tl__box" @click="toggleTp(c.chapter)"><Icon v-if="tpSel.has(c.chapter)" name="check" :size="11" /></span>
                      <span class="tl__cnum" @click="toggleTp(c.chapter)">Cap. {{ c.chapter }}</span>
                      <span class="tl__chip" :class="'tl__chip--' + (tp.runStatus?.chapter === c.chapter && tp.running ? 'doing' : c.status)">
                        {{ tp.runStatus?.chapter === c.chapter && tp.running ? 'traduciendo' : c.status === 'done' ? 'hecho' : c.status === 'failed' ? 'falló' : 'pendiente' }}
                      </span>
                      <span v-if="artOverrideFor(c.chapter)" class="chap__tag chap__tag--assigned" :title="`Este capítulo usará el arte de ${artOverrideFor(c.chapter).sourceName} (asignado en Cobertura) en vez del arte global`">
                        arte: {{ artOverrideFor(c.chapter).sourceName }}
                      </span>
                      <span v-if="esOverrideFor(c.chapter)" class="chap__tag chap__tag--assigned" :title="`Este capítulo tomará el español de ${esOverrideFor(c.chapter).sourceName} (anclado en Cobertura) en vez de la fuente ES global`">
                        ES: {{ esOverrideFor(c.chapter).sourceName }}
                      </span>
                      <button class="tl__eye" :class="{ 'is-on': tp.preview[c.chapter]?.open }" @click.stop="store.tpTogglePreview(c.chapter)" title="Vista previa del arte">
                        <Icon name="search" :size="13" />
                      </button>
                    </div>
                    <div v-if="tp.preview[c.chapter]?.open" class="tl__prev">
                      <div v-if="tp.preview[c.chapter].loading" class="tl__prevload"><Spinner :size="16" /></div>
                      <div v-else-if="tp.preview[c.chapter].pages.length" class="tl__strip">
                        <a v-for="(u, i) in tp.preview[c.chapter].pages" :key="i" :href="u" target="_blank" rel="noopener" class="tl__thumb" :title="`Página ${i + 1}`">
                          <img :src="u" loading="lazy" referrerpolicy="no-referrer" />
                        </a>
                      </div>
                      <div v-else class="muted tl__prevempty">Sin páginas.</div>
                    </div>
                  </li>
                </ul>
                <div v-else class="empty">Sin capítulos comunes a ambas fuentes.</div>
              </template>
            </div>

            <template v-if="tab === 'chapters' && !store.modalLoading && (store.chapters.length || store.hasSourceMeta || vg.hasAssignments)">
            <!-- Taller: exclusión manual de páginas a color antes de escalar -->
            <div v-if="m.source_meta?.imported" class="colors colors--ws">
              <div class="colors__head">
                <span class="colors__title">Páginas a color (excluir del escalado)</span>
                <button class="btn-xs" :disabled="store.colorLoading || !store.chapters.length" @click="store.loadColorPages(store.chapters.map(c => c.chapter))">
                  <Spinner v-if="store.colorLoading" :size="11" />Detectar
                </button>
              </div>
              <div v-if="store.colorPages.length" class="colors__grid">
                <button v-for="cp in store.colorPages" :key="cp.filename" class="colorpg" :class="{ 'is-excl': store.excludedPages.includes(cp.filename) }"
                        :title="cp.label + (store.excludedPages.includes(cp.filename) ? ' (excluida)' : '')" @click="store.toggleExclude(cp.filename)">
                  <img :src="cp.url" loading="lazy" alt="" />
                  <span v-if="store.excludedPages.includes(cp.filename)" class="colorpg__x"><Icon name="close" :size="12" /></span>
                </button>
              </div>
              <p v-else-if="!store.colorLoading" class="colors__hint">Detecta y excluye páginas a color antes de escalar los capítulos marcados.</p>
            </div>
            <!-- Novedades MangaDex: anuncio de "nuevo capítulo listo" con descarga 1-clic. Solo
                 aparece cuando hay capítulos por delante de lo que ya tienes (fuente de verdad). -->
            <div v-if="store.mdUpdates.newCount" class="mdupd">
              <div class="mdupd__head">
                <span class="mdupd__badge">{{ store.mdUpdates.newCount }}</span>
                <span class="mdupd__txt">
                  MangaDex va por delante:
                  {{ store.mdUpdates.newCount === 1 ? '1 capítulo' : store.mdUpdates.newCount + ' capítulos' }}
                  <template v-if="store.mdUpdates.behindFrom">
                    · del {{ store.mdUpdates.behindFrom }} al {{ store.mdUpdates.behindTo }}
                  </template>
                </span>
                <button class="mdupd__all" @click="store.downloadAllMdUpdates()"><Icon name="download" :size="13" /> Descargar todo</button>
              </div>
              <ul class="mdupd__list">
                <li v-for="c in store.mdUpdates.list.filter(x => x.isNew)" :key="c.chapterId" class="mdupd__row">
                  <span class="mdupd__n">Cap. {{ c.number }}</span>
                  <span class="mdupd__lang">{{ flag(c.lang) }} {{ c.lang }}</span>
                  <span class="mdupd__date">{{ (c.publishedAt || '').slice(0, 10) }}</span>
                  <button class="mdupd__dl" @click="store.downloadMdUpdate(c)"><Icon name="download" :size="13" /> Descargar</button>
                </li>
              </ul>
            </div>
            <div class="modal__chhead">
              <span>Capítulos</span>
              <span v-if="store.effectiveSource?.pinned" class="chsrc" :title="`Fuente fijada: ${store.effectiveSource.sourceName}`">
                <Icon name="spark" :size="11" /> {{ store.effectiveSource.sourceName || 'versión fijada' }}
              </span>
              <Select v-if="store.mdLangs.length > 1" v-model="store.mdLang" aria-label="Idioma"
                      :options="[{ value: '', label: 'Todos' }, ...store.mdLangs.map(l => ({ value: l, label: `${flag(l)} ${l}` }))]" />
            </div>

            <!-- «Continuar» ya no va aquí: subió a la cabecera, que no scrollea. -->

            <!-- Barra de selección por lote: marca capítulos y actúa sobre ellos (descargar/escalar) -->
            <div v-if="store.collectionChapters.length" class="batchhead">
              <button class="batchhead__all" @click="toggleAllBatch" :title="batchSel.size === store.collectionChapters.length ? 'Deseleccionar todo' : 'Seleccionar todo'">
                <span class="batchbox" :class="{ 'is-on': batchSel.size && batchSel.size === store.collectionChapters.length, 'is-part': batchSel.size && batchSel.size < store.collectionChapters.length }">
                  <Icon v-if="batchSel.size" name="check" :size="11" />
                </span>
                Seleccionar
              </button>
              <span v-if="batchSel.size" class="batchhead__n">{{ batchSel.size }} marcado(s)</span>
            </div>

            <ul class="chaps">
              <template v-for="c in store.collectionChapters" :key="c.chapter">
              <li class="chap" :class="{ 'chap--4k': upState(c.chapter) === true, 'chap--part': upState(c.chapter) === 'partial', 'chap--src': c._sourceId || c._mdChapterId || c._covMulti, 'chap--md': !!c._mdChapterId, 'chap--read': store.isChapterRead(c.chapter), 'chap--sel': batchSel.has(String(c.chapter)), 'chap--now': isCurrent(c.chapter) }" @mouseenter="batchOver(c.chapter)">
                <!-- Fases pendientes de la cadena. Sin esto lanzabas "descargar, traducir y
                     escalar" y sólo veías la descarga: no había forma de saber si lo demás
                     seguía en pie o se había quedado por el camino. -->
                <div v-if="store.chainStepsFor(c.chapter)" class="steps" :title="`Pendiente: ${store.chainStepsFor(c.chapter).steps.map(s => s.label).join(' → ')}`">
                  <template v-for="(s, i) in store.chainStepsFor(c.chapter).steps" :key="s.k">
                    <span v-if="i" class="steps__sep">›</span>
                    <span class="steps__s"
                          :class="{ 'is-at': s.k === store.chainStepsFor(c.chapter).at,
                                    'is-done': store.chainStepsFor(c.chapter).steps.findIndex(x => x.k === store.chainStepsFor(c.chapter).at) > i }">
                      {{ s.k === 'dl' ? '↓' : s.k === 'es' ? 'ES' : '4K' }}
                    </span>
                  </template>
                </div>
                <!-- Casilla de selección por lote -->
                <button class="chap__check" @mousedown.stop.left="batchDown(c.chapter, $event)" @click.stop
                        :title="batchSel.has(String(c.chapter)) ? 'Quitar de la selección' : 'Añadir · shift+clic marca hasta aquí · arrastra para marcar varios'">
                  <span class="batchbox" :class="{ 'is-on': batchSel.has(String(c.chapter)) }"><Icon v-if="batchSel.has(String(c.chapter))" name="check" :size="11" /></span>
                </button>
                <!-- Downloaded chapter: clic = leer · clic derecho = marcar/desmarcar leído -->
                <template v-if="!c._sourceId && !c._mdChapterId && !c._covMulti">
                  <button class="chap__read" @click="store.read(c.chapter)"
                          @contextmenu.prevent="store.toggleChapterRead(c.chapter)" :title="store.isChapterRead(c.chapter) ? 'Leído · clic derecho para desmarcar' : 'Clic derecho: marcar leído'">
                    <span v-if="store.isChapterRead(c.chapter)" class="chap__read-dot" title="Leído" />
                    <span class="chap__num">{{ formatChapter(c.chapter) }}</span>
                    <span class="chap__pages">{{ c.page_count }} pág.</span>
                    <span v-if="upState(c.chapter) === true" class="chap__tag chap__tag--4k">4K</span>
                    <span v-else-if="upState(c.chapter) === 'partial'" class="chap__tag chap__tag--part" :title="store.health[c.chapter] ? `Faltan ${store.health[c.chapter].missing_upscaled} págs.` : ''">PARCIAL<template v-if="store.health[c.chapter]?.missing_upscaled"> ·{{ store.health[c.chapter].missing_upscaled }}</template></span>
                    <!-- Dónde te quedaste DENTRO del capítulo en curso -->
                    <span v-if="isCurrent(c.chapter)" class="chap__now">
                      <Icon name="play" :size="10" /> pág. {{ store.continueInfo().page + 1 }}<template v-if="store.continueInfo().total">/{{ store.continueInfo().total }}</template>
                    </span>
                  </button>
                  <!-- Fuente asignada a este capítulo (chapter_sources) + reasignación puntual -->
                  <div v-if="vg.sources.length" class="chap__srcpin">
                    <button class="chap__srcpinbtn" @click="toggleReassign(String(c.chapter))" :title="c._assignedSourceName ? `Asignado: ${c._assignedSourceName}` : 'Asignar fuente para completar/actualizar este capítulo'">
                      <Icon name="grid" :size="11" /> {{ c._assignedSourceName || 'Fuente' }}
                    </button>
                    <ul v-if="reassignOpen === String(c.chapter)" class="chap__srcmenu">
                      <li v-for="s in vg.sourcesWithChapter(c.chapter)" :key="s.sourceId + '_' + s.mangaId">
                        <button @click="pickReassign(String(c.chapter), s)">{{ s.sourceName }} <em class="vr__lang">{{ flag(s.sourceLang) }}</em></button>
                      </li>
                      <li v-if="!vg.sourcesWithChapter(c.chapter).length" class="muted chap__srcmenuempty">Ninguna fuente conocida tiene este capítulo.</li>
                    </ul>
                  </div>
                </template>
                <!-- Source/MD/multi-fuente chapter: not clickable, show download info.
                     `_assignedSource` (chapter_sources) SIEMPRE manda sobre la fuente legada
                     del manga completo — nunca se muestran las dos a la vez. -->
                <div v-else class="chap__read"
                     @contextmenu.prevent="store.toggleChapterRead(c.chapter)"
                     :title="store.isChapterRead(c.chapter) ? 'Leído · clic derecho para desmarcar' : 'Clic derecho: marcar leído'">
                  <span v-if="store.isChapterRead(c.chapter)" class="chap__read-dot" title="Leído" />
                  <span v-if="c._mdLang" class="chap__flag" :title="c._mdLang">{{ flag(c._mdLang) }}</span>
                  <span class="chap__num">{{ formatChapter(c.chapter) }}</span>
                  <span class="chap__pages" v-if="c._assignedSourceName">vía {{ c._assignedSourceName }}</span>
                  <span class="chap__pages" v-else-if="c._sourceId">vía {{ store.current.source_meta?.sourceName }}</span>
                  <span class="chap__pages" v-else-if="c._mdLang">{{ c._mdGroup || 'MangaDex' }}<template v-if="c.page_count"> · {{ c.page_count }} pág.</template></span>
                  <span class="chap__pages" v-else>MangaDex</span>
                </div>

                <div class="chap__actions">
                  <!-- multi-fuente: capítulo conocido en cobertura pero aún no descargado.
                       Mismas dos acciones que un capítulo externo normal (Leer/Descargar) —
                       ambas resuelven la fuente en el momento vía chapter_urls. -->
                  <template v-if="c._covMulti">
                    <template v-if="store.downloadByChapter[c.chapter]">
                      <div class="chap__dlprog">
                        <svg class="dl-ring" viewBox="0 0 24 24">
                          <circle class="dl-ring__track" cx="12" cy="12" r="9" />
                          <circle class="dl-ring__fill" cx="12" cy="12" r="9"
                            :style="{ strokeDashoffset: 56.5 - (56.5 * (store.downloadByChapter[c.chapter].pct / 100)) }" />
                        </svg>
                        <span class="chap__dlprog-n" v-if="store.downloadByChapter[c.chapter].total">{{ store.downloadByChapter[c.chapter].pct }}%</span>
                      </div>
                    </template>
                    <template v-else>
                      <button class="chap__dlbtn chap__dlbtn--ghost"
                        :disabled="onlineBusyKey(c) === store.onlineLoadingId" @click="store.readOnline(c)">
                        <Spinner v-if="onlineBusyKey(c) === store.onlineLoadingId" :size="13" /><Icon v-else name="library" :size="14" /> Leer
                      </button>
                      <button class="chap__dlbtn" :disabled="!c._assignedSource" @click="vg.downloadChapterFrom(c.chapter, c._assignedSource)">
                        <Icon name="download" :size="14" /> Descargar
                      </button>
                    </template>
                  </template>
                  <!-- Source / MD chapter: download button with spinner -->
                  <template v-else-if="c._sourceId || c._mdChapterId">
                    <span v-if="c._mdGroup || c._mdTitle" class="chap__srcmeta">{{ c._mdGroup || c._scanlator }}<template v-if="c._mdTitle"> · {{ c._mdTitle }}</template></span>
                    <template v-if="store.downloadByChapter[c.chapter]">
                      <div class="chap__dlprog">
                        <svg class="dl-ring" viewBox="0 0 24 24">
                          <circle class="dl-ring__track" cx="12" cy="12" r="9" />
                          <circle class="dl-ring__fill" cx="12" cy="12" r="9"
                            :style="{ strokeDashoffset: 56.5 - (56.5 * (store.downloadByChapter[c.chapter].pct / 100)) }" />
                        </svg>
                        <span class="chap__dlprog-n" v-if="store.downloadByChapter[c.chapter].total">{{ store.downloadByChapter[c.chapter].pct }}%</span>
                      </div>
                    </template>
                    <template v-else>
                      <button class="chap__dlbtn chap__dlbtn--ghost"
                        :disabled="onlineBusyKey(c) === store.onlineLoadingId" @click="store.readOnline(c)">
                        <Spinner v-if="onlineBusyKey(c) === store.onlineLoadingId" :size="13" /><Icon v-else name="library" :size="14" /> Leer
                      </button>
                      <button class="chap__dlbtn"
                        @click="c._assignedSource ? vg.downloadChapterFrom(c.chapter, c._assignedSource) : (c._sourceId ? store.downloadSourceChapter(c) : store.downloadMdChapter(c))">
                        <Icon name="download" :size="14" /> Descargar
                      </button>
                    </template>
                  </template>
                  <!-- running upscale -->
                  <template v-else>
                  <div v-if="store.upscaleByChapter[c.chapter]" class="chap__dlprog">
                    <svg class="dl-ring" viewBox="0 0 24 24">
                      <circle class="dl-ring__track" cx="12" cy="12" r="9" />
                      <circle class="dl-ring__fill" cx="12" cy="12" r="9" :style="{ strokeDashoffset: 56.5 - (56.5 * (store.upscaleByChapter[c.chapter].pct / 100)) }" />
                    </svg>
                    <span class="chap__dlprog-n" v-if="store.upscaleByChapter[c.chapter].total">{{ store.upscaleByChapter[c.chapter].pct }}%</span>
                    <button class="ib ib--danger" title="Cancelar" @click="store.cancelUpscale(c.chapter)"><Icon name="close" :size="13" /></button>
                  </div>
                  <template v-else>
                    <button class="ib" :class="{ 'ib--read': store.isChapterRead(c.chapter) }" :title="store.isChapterRead(c.chapter) ? 'Marcar no leído' : 'Marcar leído'" @click="store.toggleChapterRead(c.chapter)"><Icon name="check" :size="14" /></button>
                    <button class="ib" title="Leer original" @click="store.read(c.chapter, 'original')"><Icon name="library" :size="14" /></button>
                    <button v-if="upState(c.chapter) === 'partial'" class="ib ib--warn" title="Reparar upscale" @click="store.repairChapter(c.chapter)"><Icon name="spark" :size="14" /></button>
                    <button v-else-if="upState(c.chapter) !== true" class="ib ib--accent" title="Escalar a 4K" @click="store.upscaleChapter(c.chapter, m.source_meta?.imported ? { excludePages: store.excludedPages } : {})"><Icon name="spark" :size="14" /></button>
                    <template v-if="hasColorModel">
                      <div v-if="store.colorByChapter[c.chapter] && store.colorByChapter[c.chapter].status === 'upscaling'" class="chap__dlprog" :title="`Color: ${store.colorByChapter[c.chapter].pct}%`">
                        <svg class="dl-ring" viewBox="0 0 24 24">
                          <circle class="dl-ring__track" cx="12" cy="12" r="9" />
                          <circle class="dl-ring__fill" cx="12" cy="12" r="9" :style="{ strokeDashoffset: 56.5 - (56.5 * (store.colorByChapter[c.chapter].pct / 100)) }" />
                        </svg>
                      </div>
                      <button v-else-if="store.colorDone[String(c.chapter)]" class="ib ib--colordone" title="Páginas a color ya escaladas" disabled><Icon name="palette" :size="14" /></button>
                      <button v-else class="ib ib--color" title="Escalar páginas a color de este capítulo (elige cuáles)" @click="store.openColorPicker(c.chapter)"><Icon name="palette" :size="14" /></button>
                    </template>
                    <button class="ib ib--danger" title="Borrar capítulo" @click="store.deleteChapter(c.chapter)"><Icon name="close" :size="14" /></button>
                  </template>
                  </template>
                </div>
              </li>
              </template>
            </ul>

            <!-- Barra flotante de acciones por lote -->
            <Transition name="info">
              <div v-if="batchSel.size" class="batchbar">
                <span class="batchbar__n">{{ batchSel.size }} seleccionado(s)</span>
                <div class="batchbar__acts">
                  <!-- La cadena: qué pasa cuando la descarga acabe. Chips y no un menú porque el
                       estado tiene que verse SIN abrir nada: es lo que decide qué hará el botón. -->
                  <div v-if="batchStats.remote" class="chain">
                    <span class="chain__lbl">luego</span>
                    <button class="chain__chip" :class="{ 'is-on': store.chain.upscale }"
                            @click="store.setChain({ upscale: !store.chain.upscale })"
                            title="Escalar a 4K los capítulos al terminar de descargarlos">
                      <Icon v-if="store.chain.upscale" name="check" :size="11" /> 4K
                    </button>
                    <button class="chain__chip" :class="{ 'is-on': store.chain.translate && tpReady }"
                            :disabled="!tpReady"
                            @click="store.setChain({ translate: !store.chain.translate })"
                            :title="tpReady ? 'Traducir antes de escalar (traducir invalida el 4K, así que el orden importa)' : 'Elige fuente de arte y de español en la pestaña Traducir'">
                      <Icon v-if="store.chain.translate && tpReady" name="check" :size="11" /> ES
                    </button>
                  </div>
                  <button v-if="batchStats.remote" class="hbtn hbtn--accent" @click="batchDownload">
                    <Icon name="download" :size="14" /> {{ chainLabel }} {{ batchStats.remote }}
                  </button>
                  <button v-if="batchStats.local" class="hbtn" @click="batchUpscale">
                    <Icon name="spark" :size="14" /> Escalar 4K {{ batchStats.local }}
                  </button>
                  <button v-if="batchStats.local" class="hbtn" @click="batchColor"
                          title="El escalado 4K salta las páginas a color: éstas van con el modelo de color, y eliges cuáles">
                    <Icon name="palette" :size="14" /> Páginas a color
                  </button>
                  <button class="hbtn batchbar__clear" @click="clearBatch"><Icon name="close" :size="14" /></button>
                </div>
              </div>
            </Transition>
            </template>
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>

  <!-- Selector de páginas a color: elige qué escalar con APISR (color pre-marcado) -->
  <Teleport to="body">
    <Transition name="fade">
      <div v-if="store.colorPicker.open" class="cpk-ov" @click.self="store.closeColorPicker()">
        <div class="cpk">
          <header class="cpk__head">
            <div>
              <h3 v-if="store.colorPicker.mode === 'all'">Escalar a color · Todo el manga</h3>
              <h3 v-else>Escalar a color · Cap. {{ formatChapter(store.colorPicker.chapter) }}</h3>
              <p>Solo se muestran las páginas a color. Marca las que quieras escalar con {{ colorModelLabel }}.</p>
            </div>
            <label class="cpk__model" v-if="colorModelKeys.length > 1">Modelo
              <Select :model-value="store.colorModel || colorModelKeys[0]" aria-label="Modelo a color"
                      :options="colorModelKeys.map(k => ({ value: k, label: store.models[k] }))"
                      @change="store.setColorModel($event)" />
            </label>
            <button class="cpk__x" @click="store.closeColorPicker()"><Icon name="close" :size="16" /></button>
          </header>
          <div v-if="store.colorPicker.loading" class="cpk__center"><Spinner :size="28" /></div>
          <div v-else-if="colorPickerEmpty" class="cpk__center"><p>No se detectaron páginas a color.</p></div>
          <!-- Un capítulo: rejilla plana -->
          <div v-else-if="store.colorPicker.mode === 'single'" class="cpk__grid">
            <button v-for="p in store.colorPicker.pages" :key="p.name" class="cpk__pg" :class="{ 'is-sel': p.sel }" @click="store.toggleColorPage(p.name)">
              <img :src="pageUrl(p.url, 180)" loading="lazy" alt="" />
              <span class="cpk__check" :class="{ 'is-on': p.sel }"><Icon v-if="p.sel" name="check" :size="12" /></span>
            </button>
          </div>
          <!-- Todo el manga: agrupado por capítulo -->
          <div v-else class="cpk__scroll">
            <section v-for="c in store.colorPicker.chapters" :key="c.chapter" class="cpk__chap">
              <h4>Cap. {{ formatChapter(c.chapter) }} <em v-if="c.done">· ya escalado</em></h4>
              <div class="cpk__grid">
                <button v-for="p in c.pages" :key="p.name" class="cpk__pg" :class="{ 'is-sel': p.sel }" @click="store.toggleColorPage(p.name, c.chapter)">
                  <img :src="pageUrl(p.url, 180)" loading="lazy" alt="" />
                  <span class="cpk__check" :class="{ 'is-on': p.sel }"><Icon v-if="p.sel" name="check" :size="12" /></span>
                </button>
              </div>
            </section>
          </div>
          <footer class="cpk__foot">
            <span class="cpk__n">{{ colorPickerSelCount }} de {{ colorPickerTotal }} seleccionada(s)</span>
            <button class="cpk__cancel" @click="store.closeColorPicker()">Cancelar</button>
            <button class="cpk__run" :disabled="!colorPickerSelCount" @click="store.runColorPicker()"><Icon name="palette" :size="14" /> Escalar a color</button>
          </footer>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.ov { position: fixed; inset: 0; z-index: var(--z-modal); display: grid; place-items: center; padding: var(--s-5);
  background: rgba(7,10,18,.72); backdrop-filter: blur(8px); }
.modal { position: relative; width: min(var(--modal-w, 52rem), 100%); max-height: 88vh; display: flex; flex-direction: column;
  background: var(--glass-strong); border: 1px solid var(--line-2); border-radius: var(--r-lg); box-shadow: var(--shadow-xl); overflow: hidden;
  transition: width var(--t-base) var(--ease-silk); }
/* Las pestañas densas piden aire; las de lectura no. */
.modal--versions, .modal--tomo { --modal-w: 72rem; }
.modal--recs { --modal-w: 60rem; }
.modal__x { position: absolute; top: var(--s-3); right: var(--s-3); z-index: 2; width: 2.125rem; height: 2.125rem; display: grid; place-items: center; border-radius: var(--r-sm); color: var(--ink-soft); background: var(--surface); border: 1px solid var(--line); transition: all var(--t-fast); }
.modal__x:hover { color: var(--ink); border-color: var(--line-strong); }

.modal__head { position: relative; overflow: hidden; display: flex; gap: var(--s-4); padding: var(--s-5); border-bottom: 1px solid var(--line); flex-shrink: 0; }
/* Ambiente por portada (solo cabecera): backdrop de la propia portada esmerilada +
   aura del color dominante. Puramente decorativo, detrás del texto/póster (que suben
   con z-index:1). Sin ambiente disponible → oculto. */
.modal__ambient { display: none; }
.modal--art .modal__ambient { display: block; position: absolute; inset: 0; z-index: 0; pointer-events: none; }
.modal--art .modal__ambient::before {   /* portada esmerilada */
  content: ''; position: absolute; inset: 0;
  background-image: var(--cvart); background-size: cover; background-position: center 22%;
  filter: blur(28px) saturate(1.25); transform: scale(1.25); opacity: .22;
}
.modal--art .modal__ambient::after {    /* aura de color + desvanecido a la superficie */
  content: ''; position: absolute; inset: 0;
  background:
    radial-gradient(120% 90% at 18% 0%, rgba(var(--cv), .30), transparent 62%),
    linear-gradient(180deg, transparent 30%, var(--glass-strong) 96%);
}
.modal__head > :not(.modal__ambient) { position: relative; z-index: 1; }
/* align-self:flex-start stops the flex row from stretching the cover to the (taller)
   info column's height. Keep the natural aspect ratio (width fixed, height auto) so the
   cover is shown whole — no cropping the sides, no distortion. */
.modal__cover { width: 8.25rem; height: auto; align-self: flex-start; border-radius: var(--r-md); box-shadow: var(--shadow-md); flex-shrink: 0; }
/* Resplandor del póster en su propio color dominante (#2). */
.modal--art .modal__cover { box-shadow: var(--shadow-md), 0 6px 30px rgba(var(--cv), .45); }
.modal__cover--ph { display: grid; place-items: center; background: var(--surface-2); color: var(--ink-ghost); width: 8.25rem; aspect-ratio: 2/3; }
.modal__info { min-width: 0; flex: 1; padding-right: var(--s-7); display: flex; flex-direction: column; }
.modal__title { font-size: var(--fs-2xl); line-height: var(--lh-tight); }

/* Metadatos en UNA línea, todos del mismo peso bajo (son referencia, no acción). */
.modal__meta { display: flex; align-items: center; flex-wrap: wrap; gap: var(--s-2);
  margin-top: var(--s-2); color: var(--ink-faint); font-size: var(--fs-sm); }
.modal__dot { width: 3px; height: 3px; border-radius: 50%; background: var(--ink-ghost); flex-shrink: 0; }
.modal__read { color: var(--ink-soft); }
.modal__4k { color: var(--cyan); }
.mdlink { display: inline-flex; align-items: center; gap: 0.3125rem; font-size: var(--fs-xs); font-weight: 500; color: var(--violet); text-decoration: none; padding: 2px 0.5rem; border-radius: var(--r-pill); border: 1px solid color-mix(in srgb, var(--violet) 25%, transparent); transition: all var(--t-fast); }
.mdlink:hover { background: color-mix(in srgb, var(--violet) 10%, transparent); border-color: var(--violet); }

/* Progreso de lectura de la serie */
.modal__prog { height: 3px; margin-top: var(--s-3); border-radius: var(--r-pill);
  background: var(--surface-3); overflow: hidden; max-width: 22rem; }
.modal__prog-fill { display: block; height: 100%; border-radius: inherit;
  background: linear-gradient(90deg, var(--azure), var(--cyan));
  box-shadow: 0 0 8px var(--azure-glow); transition: width var(--t-slow) var(--ease-silk); }

/* Acción primaria: la única cosa grande y llena de la cabecera. */
.hgo { display: inline-flex; align-items: center; gap: var(--s-2);
  padding: var(--s-2) var(--s-5); border-radius: var(--r-md);
  font-size: var(--fs-sm); font-weight: 600; color: #0b0f1a; background: #fff;
  box-shadow: var(--shadow-md); transition: box-shadow var(--t-fast), transform var(--t-fast); }
.hgo:hover { box-shadow: var(--glow-azure); transform: translateY(-1px); }
.hgo em { font-style: normal; font-family: var(--font-mono); font-size: var(--fs-2xs); opacity: .6; }

.modal__hacts { display: flex; gap: var(--s-2); margin-top: var(--s-3); }
.hbtn { display: inline-flex; align-items: center; gap: 0.3125rem; padding: 0.3125rem 0.625rem; border-radius: var(--r-sm); font-size: var(--fs-xs); color: var(--ink-soft); border: 1px solid var(--line-2); transition: all var(--t-fast); }
.hbtn:hover { color: var(--ink); border-color: var(--line-strong); }
.hbtn.is-on { color: var(--azure-bright); border-color: var(--azure); background: var(--azure-haze); }
.hbtn--accent { color: var(--cyan); border-color: color-mix(in srgb, var(--cyan) 30%, transparent); }
.hbtn--accent:hover { background: var(--cyan-glow); color: #d6fffb; }
.hbtn--accent.is-added { color: var(--jade, #4ade80); border-color: color-mix(in srgb, var(--jade, #4ade80) 45%, transparent);
  background: color-mix(in srgb, var(--jade, #4ade80) 12%, transparent); cursor: default; }
/* Micro-confirmación: el check da un pequeño "pop" al confirmarse la acción. */
.hbtn--accent.is-added :deep(svg) { animation: confirm-pop var(--t-base) var(--ease-snap); }
@keyframes confirm-pop { 0% { transform: scale(0); } 60% { transform: scale(1.35); } 100% { transform: scale(1); } }
@media (prefers-reduced-motion: reduce) { .hbtn--accent.is-added :deep(svg) { animation: none; } }
.hbtn:disabled { cursor: default; opacity: .85; }
.hbtn--danger { color: var(--danger, #f0788c); border-color: color-mix(in srgb, var(--danger, #f0788c) 30%, transparent); }
.hbtn--danger:hover { background: color-mix(in srgb, var(--danger, #f0788c) 14%, transparent); color: #ffb3bf; border-color: var(--danger, #f0788c); }
.mf--chk { flex-direction: row; align-items: center; justify-content: space-between; }
.mf--chk input { width: auto; }
.manage__free { display: flex; align-items: center; flex-wrap: wrap; gap: var(--s-2); margin-top: var(--s-2); }
.manage__free-lbl { display: inline-flex; align-items: center; gap: 4px; font-size: var(--fs-2xs); color: var(--ink-faint); }
.freebtn { display: inline-flex; align-items: center; gap: 0.3125rem; padding: 0.3125rem 0.625rem; border-radius: var(--r-sm); font-size: var(--fs-2xs); font-weight: 600;
  color: var(--coral); border: 1px solid color-mix(in srgb, var(--coral) 30%, transparent); transition: all var(--t-fast); }
.freebtn:hover:not(:disabled) { background: color-mix(in srgb, var(--coral) 12%, transparent); border-color: color-mix(in srgb, var(--coral) 55%, transparent); }
.freebtn:disabled { opacity: .5; cursor: default; }
.manage { padding: var(--s-4) var(--s-5); border-bottom: 1px solid var(--line); background: var(--base); overflow: hidden; flex-shrink: 0; }
/* expand/collapse animation for the management panel */
.info-enter-active, .info-leave-active { transition: max-height var(--t-base) var(--ease-silk), opacity var(--t-base) var(--ease-silk); overflow: hidden; }
.info-enter-from, .info-leave-to { max-height: 0; opacity: 0; }
.info-enter-to, .info-leave-from { max-height: 21.25rem; opacity: 1; }
.manage__sect { display: flex; flex-direction: column; gap: var(--s-2); }
.manage__sect + .manage__sect { margin-top: var(--s-3); padding-top: var(--s-3); border-top: 1px solid var(--line); }
.manage__label { font-family: var(--font-mono); font-size: var(--fs-2xs); letter-spacing: var(--tracking-caps); text-transform: uppercase; color: var(--ink-faint); }
.manage__label em { font-style: normal; text-transform: none; letter-spacing: 0; color: var(--ink-ghost); }
.manage__size { display: flex; align-items: center; flex-wrap: wrap; gap: 0.375rem; font-size: var(--fs-xs); color: var(--ink-faint); }
.manage__size :deep(svg) { color: var(--ink-ghost); }
.manage__size b { color: var(--ink); font-weight: 600; }
.manage__size-4k b { color: var(--cyan); }
.manage__size-tot { color: var(--ink-ghost); }
.manage__sect--up .manage__label { color: var(--cyan); }
.mf em { font-style: normal; color: var(--ink-ghost); }
.manage__row { display: flex; gap: var(--s-3); }
.mf { flex: 1; display: flex; flex-direction: column; gap: 4px; font-size: var(--fs-xs); color: var(--ink-faint); }
.mf input { padding: var(--s-2) var(--s-3); border-radius: var(--r-sm); background: var(--surface); border: 1px solid var(--line-2); color: var(--ink); font-size: var(--fs-sm); }
.mf input:focus { outline: none; border-color: var(--azure); }
.mf select {
  padding: var(--s-2) 1.875rem var(--s-2) var(--s-3); border-radius: var(--r-sm);
  background-color: var(--surface); border: 1px solid var(--line-2); color: var(--ink); font-size: var(--fs-sm);
  cursor: pointer; appearance: none; -webkit-appearance: none;
  background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='14' height='14' viewBox='0 0 24 24' fill='none' stroke='%239aa7bd' stroke-width='2.5' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpolyline points='6 9 12 15 18 9'/%3E%3C/svg%3E");
  background-repeat: no-repeat; background-position: right 10px center;
  transition: border-color var(--t-fast), box-shadow var(--t-fast);
}
.mf select:hover { border-color: var(--line-strong); }
.mf select:focus { outline: none; border-color: var(--azure); box-shadow: 0 0 0 3px var(--azure-haze); }
.mf select option { background: var(--surface-2); color: var(--ink); }

/* ── Selector de modelo propio (distintivo Color/B&N) ─────────────────────── */
/* Etiqueta Color / B&N */
/* Menú — teletransportado al body con posición FIJA (el panel Gestionar tiene overflow:hidden y
   lo recortaría); JS calcula top/left/width y voltea con .is-up si no cabe abajo. */
/* Solo opacidad: el posicionamiento (incl. translateY(-100%) del volteo) lo lleva JS/.is-up,
   así la animación no pisa el transform de colocación. */

.manage__actions { display: flex; align-items: center; gap: var(--s-2); margin-top: var(--s-3); }
.manage__spacer { flex: 1; }
.delbtn { display: inline-flex; align-items: center; gap: 0.375rem; padding: var(--s-2) var(--s-3); border-radius: var(--r-sm); font-size: var(--fs-xs); font-weight: 500; color: var(--coral); border: 1px solid color-mix(in srgb, var(--coral) 35%, transparent); background: transparent; transition: all var(--t-fast); }
.delbtn:hover { background: color-mix(in srgb, var(--coral) 12%, transparent); border-color: var(--coral); }
.upbtn { display: inline-flex; align-items: center; gap: 0.375rem; padding: var(--s-2) var(--s-3); border-radius: var(--r-sm); font-size: var(--fs-xs); color: var(--ink-soft); border: 1px solid var(--line-2); cursor: pointer; }
.upbtn:hover { color: var(--ink); }
.savebtn { padding: var(--s-2) var(--s-5); border-radius: var(--r-sm); background: var(--azure); color: #fff; font-weight: 600; font-size: var(--fs-sm); }
.savebtn:hover { background: var(--azure-bright); }
.manage__row--color { align-items: center; margin-top: var(--s-2); }
.colorbtn { flex-shrink: 0; display: inline-flex; align-items: center; gap: 0.375rem; padding: var(--s-2) var(--s-4); border-radius: var(--r-sm); font-size: var(--fs-xs); font-weight: 600; color: var(--cyan); border: 1px solid color-mix(in srgb, var(--cyan) 40%, transparent); background: color-mix(in srgb, var(--cyan) 8%, transparent); transition: all var(--t-fast); }
.colorbtn:hover { background: color-mix(in srgb, var(--cyan) 16%, transparent); border-color: var(--cyan); }
.manage__hint { font-size: var(--fs-2xs); color: var(--ink-ghost); line-height: var(--lh-snug); }

/* ── Selector visual de portada ───────────────────────────────────────── */
.cvp__toggle { display: inline-flex; align-items: center; gap: 0.375rem; margin-top: var(--s-3); padding: var(--s-2) var(--s-3); border-radius: var(--r-sm); border: 1px solid var(--line); font-size: var(--fs-xs); color: var(--azure-bright); }
.cvp__toggle:hover { border-color: var(--azure); background: var(--azure-haze); }
.cvp { margin-top: var(--s-2); }
.cvp__load { display: flex; align-items: center; gap: var(--s-2); font-size: var(--fs-xs); color: var(--ink-faint); padding: var(--s-3); }
.cvp__grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(5rem, 1fr)); gap: var(--s-2); max-height: 18rem; overflow-y: auto; padding: 2px; }
.cvp__item { position: relative; aspect-ratio: 2/3; border-radius: var(--r-sm); overflow: hidden; border: 2px solid transparent; background: var(--surface-2); cursor: pointer; transition: border-color var(--t-fast); }
.cvp__item:hover:not(:disabled) { border-color: var(--azure); }
.cvp__item.is-current { border-color: var(--azure-bright); cursor: default; }
.cvp__item img { width: 100%; height: 100%; object-fit: cover; display: block; }
.cvp__item:disabled { opacity: .7; cursor: progress; }
.cvp__tag { position: absolute; bottom: 0; left: 0; right: 0; font-size: 0.5625rem; line-height: 1.4; text-align: center; background: rgba(0,0,0,.6); color: #fff; padding: 1px 2px; }
.cvp__tag--al { background: color-mix(in oklab, var(--azure) 75%, #000); }
.cvp__busy { position: absolute; inset: 0; display: grid; place-items: center; background: rgba(0,0,0,.45); }
.cvp__empty { font-size: var(--fs-2xs); color: var(--ink-faint); padding: var(--s-2); }

/* Pestañas con el MISMO vocabulario que el resto de la app (`.subnav__tab`, pills): antes eran
   pestañas subrayadas, un segundo dialecto para lo mismo. `Capítulos` va destacada y separada:
   es la diaria, las otras cuatro son ocasionales. */
.modal__tabs { display: flex; align-items: center; gap: var(--s-1); padding: var(--s-3) var(--s-4);
  border-bottom: 1px solid var(--line); flex-shrink: 0; overflow-x: auto; }
.mtab { display: inline-flex; align-items: center; gap: 0.375rem; padding: var(--s-2) var(--s-4);
  border-radius: var(--r-pill); font-size: var(--fs-sm); font-weight: 500; color: var(--ink-faint);
  white-space: nowrap; transition: all var(--t-fast) var(--ease-silk); }
.mtab:hover { color: var(--ink); background: var(--surface); }
.mtab.is-active { color: var(--azure-bright); background: var(--azure-haze); }
.mtab--sep { margin-right: var(--s-2); padding-right: var(--s-3); border-right: 1px solid var(--line); border-radius: var(--r-pill) 0 0 var(--r-pill); }
.mtab__badge { font-size: 0.5625rem; font-weight: 800; letter-spacing: .04em; padding: 1px 0.3125rem; border-radius: var(--r-pill); background: var(--jade); color: #04130c; }

/* Pestaña Traducir */
.tl { padding: var(--s-2) 0 var(--s-4); }
.tl__lead { font-size: var(--fs-xs); color: var(--ink-soft); line-height: var(--lh-body); margin-bottom: var(--s-3); }
.tl__disc { display: flex; align-items: center; gap: var(--s-3); padding: var(--s-4); }
.tl__cta { display: flex; align-items: center; flex-wrap: wrap; gap: var(--s-3); padding: var(--s-3) 0; }
.tl__localart { display: flex; align-items: flex-start; gap: var(--s-2); font-size: var(--fs-xs); color: var(--ink-soft); cursor: pointer; margin-bottom: var(--s-3); padding: var(--s-2) var(--s-3); background: var(--surface-2, rgba(255,255,255,.03)); border-radius: var(--r-2, 0.5rem); }
.tl__localart input { accent-color: var(--accent); cursor: pointer; margin-top: 2px; }
.tl__localhint { display: block; font-style: normal; color: var(--ink-faint, var(--ink-soft)); opacity: .8; margin-top: 2px; }
.tl__err { font-size: var(--fs-xs); color: var(--rose, #e8748b); }
.tl__picks { display: grid; grid-template-columns: 1fr 1fr; gap: var(--s-3); margin-bottom: var(--s-3); }
.tl__pick { background: var(--surface); border: 1px solid var(--line); border-radius: var(--r-md); padding: var(--s-3); }
.tl__pickh { display: flex; align-items: center; justify-content: space-between; font-weight: 600; font-size: var(--fs-xs); margin-bottom: 0.375rem; }
.tl__re { color: var(--ink-faint); font-size: var(--fs-sm); padding: 0 4px; }
.tl__re:hover { color: var(--azure-bright); }
.tl__cand { display: flex; flex-direction: column; }
.tl__src { font-size: var(--fs-xs); color: var(--azure-bright); font-weight: 600; }
.tl__q { font-size: var(--fs-2xs); color: var(--ink-faint); }
.tl__run { margin: var(--s-3) 0; }
.tl__bar { height: 0.375rem; border-radius: var(--r-pill); background: var(--surface-2); overflow: hidden; }
.tl__fill { height: 100%; background: var(--azure); transition: width var(--t-base); }
.tl__runinfo { display: flex; align-items: center; justify-content: space-between; margin-top: 0.375rem; }
.tl__stop { display: inline-flex; align-items: center; gap: 0.3125rem; padding: 4px 0.625rem; border-radius: var(--r-sm); font-size: var(--fs-2xs); font-weight: 600; color: var(--rose, #e8748b); border: 1px solid color-mix(in srgb, var(--rose, #e8748b) 40%, transparent); }
.tl__stop:hover { background: color-mix(in srgb, var(--rose, #e8748b) 12%, transparent); }
.tl__chhead { display: flex; align-items: center; justify-content: space-between; margin: var(--s-3) 0 var(--s-2); font-weight: 600; }
.tl__acts { display: flex; gap: var(--s-2); }
.btn-xs--accent { color: #fff; background: var(--azure); border-color: transparent; }
.btn-xs--accent:hover:not(:disabled) { filter: brightness(1.1); }
.btn-xs:disabled { opacity: .45; cursor: default; }
.tl__chaps { max-height: 17.5rem; overflow-y: auto; display: flex; flex-direction: column; gap: 2px; }
.tl__chap { display: flex; align-items: center; gap: var(--s-2); padding: 0.375rem var(--s-2); border-radius: var(--r-sm); cursor: pointer; transition: background var(--t-fast); }
.tl__chap:hover { background: var(--surface); }
.tl__chap.is-sel { background: var(--azure-haze); }
.tl__box { width: 1rem; height: 1rem; flex-shrink: 0; display: grid; place-items: center; border-radius: 4px; border: 1px solid var(--line-strong); color: var(--azure-bright); }
.tl__chap.is-sel .tl__box { border-color: var(--azure); }
.tl__cnum { flex: 1; font-size: var(--fs-sm); cursor: pointer; }
.tl__box { cursor: pointer; }
.tl__eye { display: grid; place-items: center; width: 1.625rem; height: 1.625rem; flex-shrink: 0; border-radius: var(--r-sm); color: var(--ink-faint); border: 1px solid var(--line); }
.tl__eye:hover { color: var(--azure-bright); border-color: var(--azure); }
.tl__eye.is-on { color: var(--azure-bright); background: var(--azure-haze); border-color: var(--azure); }
.tl__prev { padding: var(--s-2) var(--s-2) var(--s-3) 1.625rem; }
.tl__prevload, .tl__prevempty { padding: var(--s-2); font-size: var(--fs-2xs); }
.tl__strip { display: flex; gap: 0.375rem; overflow-x: auto; padding-bottom: 0.375rem; }
.tl__thumb { flex-shrink: 0; width: 4rem; aspect-ratio: 2/3; border-radius: var(--r-sm); overflow: hidden; border: 1px solid var(--line); background: var(--surface-2); }
.tl__thumb img { width: 100%; height: 100%; object-fit: cover; }
.tl__thumb:hover { border-color: var(--azure); }
.tl__chip { font-size: 0.625rem; font-weight: 700; padding: 1px 0.4375rem; border-radius: var(--r-pill); text-transform: uppercase; letter-spacing: .03em; }
.tl__chip--pending { color: var(--ink-faint); background: var(--surface-2); }
.tl__chip--done { color: var(--jade); background: color-mix(in srgb, var(--jade) 14%, transparent); }
.tl__chip--failed { color: var(--rose, #e8748b); background: color-mix(in srgb, var(--rose, #e8748b) 14%, transparent); }
.tl__chip--doing { color: var(--azure-bright); background: var(--azure-haze); }
.muted { color: var(--ink-faint); font-weight: 400; }

/* Pestaña Versiones (ranking de calidad de imagen) */
.vr { padding: var(--s-2) 0 var(--s-4); }
.recs { padding: var(--s-1) var(--s-1) var(--s-4); }
.recs :deep(.rec) { margin-top: 0; }
.vr__lead { font-size: var(--fs-xs); color: var(--ink-soft); line-height: var(--lh-body); margin-bottom: var(--s-3); }
.vr__disc { display: flex; align-items: center; gap: var(--s-3); padding: var(--s-4); }
.vr__cta { display: flex; flex-wrap: wrap; align-items: center; gap: var(--s-3); padding: var(--s-3) 0; }
.vr__hint { font-size: var(--fs-2xs); color: var(--ink-ghost); }
.vr__head { display: flex; align-items: center; justify-content: space-between; margin-bottom: var(--s-3); }
.vr__filter { display: flex; align-items: center; gap: var(--s-2); font-size: var(--fs-xs); }
.vr__list { display: flex; flex-direction: column; gap: var(--s-1); }
.vr__row { display: flex; align-items: center; gap: var(--s-2); padding: var(--s-2) var(--s-3); border-radius: var(--r-md); background: var(--surface); border: 1px solid var(--line); }
.vr__row--local { margin-bottom: var(--s-2); background: var(--surface-2); border-style: dashed; }
.vr__row--best { border-color: color-mix(in srgb, var(--cyan) 45%, transparent); background: var(--cyan-glow, color-mix(in srgb, var(--cyan) 8%, transparent)); }
.vr__main { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 2px; }
.vr__src { display: inline-flex; align-items: center; gap: 0.3125rem; font-size: var(--fs-sm); font-weight: 600; color: var(--ink); }
.vr__lang { font-style: normal; font-weight: 400; font-size: var(--fs-2xs); color: var(--ink-faint); }
.vr__q { font-size: var(--fs-2xs); color: var(--ink-faint); font-family: var(--font-mono); }
.vr__caps { color: var(--ink); font-weight: 600; }
.vr__ref { display: flex; align-items: center; gap: var(--s-2); margin-bottom: var(--s-3); padding: var(--s-2) var(--s-3);
  border: 1px solid var(--line); border-radius: var(--r-sm); background: var(--surface-2, var(--surface));
  font-size: var(--fs-xs); color: var(--ink-soft); }
.vr__ref strong { color: var(--ink); }
.vr__refsrc { color: var(--ink-faint); font-size: var(--fs-2xs); margin-left: auto; }
.vr__complete { font-size: var(--fs-2xs); font-weight: 700; color: var(--jade, #4ade80);
  background: color-mix(in srgb, var(--jade, #4ade80) 14%, transparent); padding: 1px 0.4375rem; border-radius: var(--r-pill); }
.vr__behind { font-size: var(--fs-2xs); font-weight: 600; color: var(--amber, #f5b544);
  background: color-mix(in srgb, var(--amber, #f5b544) 14%, transparent); padding: 1px 0.4375rem; border-radius: var(--r-pill); }
.vr__irr { margin-left: 0.5rem; font-size: var(--fs-2xs); color: var(--warn); white-space: nowrap; cursor: help; }
.vr__badge { font-size: 0.5625rem; font-weight: 800; letter-spacing: .04em; padding: 2px 0.4375rem; border-radius: var(--r-pill); flex-shrink: 0; }
.vr__badge--actual { background: var(--ink-ghost); color: var(--base); }
.vr__badge--best { background: var(--cyan); color: #04130c; }
.vr__eye { display: inline-flex; align-items: center; gap: 0.3125rem; flex-shrink: 0; padding: 0.3125rem 0.625rem; border-radius: var(--r-sm); font-size: var(--fs-2xs); color: var(--ink-faint); border: 1px solid var(--line); }
.vr__eye:hover:not(:disabled) { color: var(--azure-bright); border-color: var(--azure); }
.vr__eye.is-on { color: var(--azure-bright); background: var(--azure-haze); border-color: var(--azure); }
.vr__eye:disabled { opacity: .4; cursor: default; }
.vr__dl { display: flex; align-items: center; gap: var(--s-2); padding: var(--s-2) var(--s-3); margin-bottom: var(--s-3); border-radius: var(--r-md); background: var(--surface); border: 1px solid var(--line); font-size: var(--fs-xs); color: var(--ink-soft); }
.vr__prev { padding: var(--s-2) var(--s-1) var(--s-1); }
.vr__prevload, .vr__prevempty { padding: var(--s-2); font-size: var(--fs-2xs); }
.vr__acts { display: flex; align-items: center; gap: 0.375rem; flex-shrink: 0; }
.vr__fix { padding: 0.3125rem 0.75rem; border-radius: var(--r-sm); font-size: var(--fs-2xs); font-weight: 600; color: var(--ink-soft); border: 1px solid var(--line); transition: all var(--t-fast); }
.vr__fix:hover { color: var(--cyan); border-color: color-mix(in srgb, var(--cyan) 45%, transparent); }
.vr__fix.is-on { color: #04130c; background: var(--cyan); border-color: transparent; animation: confirm-pop var(--t-base) var(--ease-snap); }
.vr__row--primary { border-color: color-mix(in srgb, var(--cyan) 55%, transparent); }
.vr__row--cmp { box-shadow: 0 0 0 1px var(--azure) inset; }
.vr__badge--primary { background: var(--cyan); color: #04130c; }

/* Fuentes por capítulo: cobertura + asignación por rango + huecos + actualizaciones */
.vg { margin-top: var(--s-5); padding-top: var(--s-4); border-top: 1px solid var(--line); }
.vg__head { display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: var(--s-2); margin-bottom: var(--s-2); }
.vg__title { font-size: var(--fs-sm); font-weight: 700; color: var(--ink); margin: 0; }
.vg__actions { display: flex; align-items: center; gap: var(--s-2); flex-wrap: wrap; }
.vg__lead { font-size: var(--fs-2xs); color: var(--ink-faint); line-height: var(--lh-body); margin-bottom: var(--s-3); }
.vg__swcyan { color: var(--cyan); font-weight: 600; }
.vg__swblue { color: var(--azure-bright, var(--azure)); font-weight: 600; }
.vg__srclist { display: flex; flex-direction: column; gap: var(--s-1); margin-top: var(--s-3); }
.vg__srcrow { display: flex; align-items: center; gap: var(--s-2); padding: 0.3125rem var(--s-2); border-radius: var(--r-sm); background: var(--surface); border: 1px solid var(--line); font-size: var(--fs-2xs); }
.vg__srcname { flex: 1; min-width: 0; color: var(--ink); font-weight: 600; }
.vg__srcmeta { color: var(--ink-faint); font-family: var(--font-mono); }
.vg__match { font-family: var(--font-mono); font-size: var(--fs-2xs); padding: 1px 0.375rem; border-radius: var(--r-xs); color: var(--ink-faint); background: var(--surface-2); cursor: help; }
.vg__match--low { color: var(--warn, var(--gold)); background: color-mix(in srgb, var(--warn, var(--gold)) 16%, transparent); }
.vg__rangepanel { display: flex; align-items: center; gap: var(--s-2); margin-top: var(--s-3); padding: var(--s-2) var(--s-3); border-radius: var(--r-md); background: var(--surface-2); border: 1px dashed var(--line); font-size: var(--fs-xs); flex-wrap: wrap; }
.vg__rangein { width: 6.5rem; font-size: var(--fs-xs); padding: 4px 0.5rem; border-radius: var(--r-sm); background: var(--surface); border: 1px solid var(--line); color: var(--ink); }
.vg__fresh { margin-bottom: var(--s-3); }
.vg__suglist { display: flex; flex-direction: column; gap: var(--s-1); }
.vg__sug { display: flex; align-items: center; justify-content: space-between; gap: var(--s-2); padding: 0.3125rem var(--s-3); border-radius: var(--r-sm); background: var(--surface); border: 1px solid var(--line); font-size: var(--fs-2xs); }
.vg__sugtxt { color: var(--ink-soft); }
.vr__primary { display: flex; align-items: center; gap: 0.375rem; font-size: var(--fs-xs); color: var(--cyan); margin-bottom: var(--s-2); }
.vr__primary strong { color: var(--ink); font-weight: 600; }
.vr__tray { display: flex; align-items: center; flex-wrap: wrap; gap: var(--s-2); padding: var(--s-2) var(--s-3); margin-bottom: var(--s-3); border-radius: var(--r-md); background: var(--azure-haze); border: 1px solid color-mix(in srgb, var(--azure) 30%, transparent); }
.vr__chip { display: inline-flex; align-items: center; gap: 0.3125rem; padding: 3px 4px 3px 0.625rem; border-radius: var(--r-pill); font-size: var(--fs-2xs); font-weight: 600; background: var(--surface); border: 1px solid var(--line); }
.vr__chipx { display: grid; place-items: center; width: 1rem; height: 1rem; border-radius: 50%; color: var(--ink-faint); }
.vr__chipx:hover { color: var(--coral); }
.vr__trayhint { font-size: var(--fs-2xs); color: var(--ink-faint); }
.vr__traygo { margin-left: auto; }

/* tomos sin repartir en capítulos reales */
.tl__vol { display: flex; flex-direction: column; gap: var(--s-2); padding: var(--s-3); margin-bottom: var(--s-3); border-radius: var(--r-md); background: var(--azure-haze); border: 1px solid color-mix(in srgb, var(--azure) 30%, transparent); }
.tl__vol--warn { background: color-mix(in srgb, var(--rose, #e8748b) 10%, transparent); border-color: color-mix(in srgb, var(--rose, #e8748b) 30%, transparent); }
.tl__volitem { display: flex; flex-direction: column; gap: var(--s-2); padding-bottom: var(--s-2); }
.tl__volitem + .tl__volitem { padding-top: var(--s-2); border-top: 1px solid color-mix(in srgb, var(--rose, #e8748b) 20%, transparent); }
.tl__volmsg { display: flex; align-items: flex-start; gap: var(--s-2); font-size: var(--fs-xs); color: var(--ink-soft); line-height: var(--lh-body); }
.tl__re2 { align-self: flex-start; font-size: var(--fs-2xs); font-weight: 600; color: var(--azure-bright); padding: 4px 0.625rem; border-radius: var(--r-sm); border: 1px solid var(--azure); }
.tl__re2:hover { background: var(--azure-haze); }
.tl__manual { display: flex; flex-direction: column; gap: 0.375rem; padding: var(--s-2); background: var(--surface); border-radius: var(--r-sm); }
.tl__manualrow { display: flex; align-items: center; gap: var(--s-2); font-size: var(--fs-xs); }
.tl__manchin { width: 3.6rem; font-size: var(--fs-xs); padding: 3px 0.375rem; border-radius: var(--r-sm); background: var(--surface-2); border: 1px solid var(--line); color: var(--ink); }
.tl__maninput { width: 5rem; font-size: var(--fs-xs); padding: 3px 0.375rem; border-radius: var(--r-sm); background: var(--surface-2); border: 1px solid var(--line); color: var(--ink); }
.tl__rmrow { color: var(--ink-faint); transition: color var(--t-fast); margin-left: auto; }
.tl__rmrow:hover { color: var(--coral); }
.tl__manualtotal { font-size: var(--fs-2xs); color: var(--ink-faint); }
.tl__manualtotal.is-bad { color: var(--rose, #e8748b); font-weight: 600; }
.tl__manualacts { display: flex; gap: var(--s-2); }

/* flex-basis auto (not 0): the modal has max-height, not a fixed height, so basis:0
   would collapse this scroll region to 0 and hide the chapters. auto lets it size to
   content and only shrink+scroll once the modal hits its max-height. */
.modal__body { flex: 1 1 auto; min-height: 0; overflow-y: auto; padding: var(--s-3); }
.modal__skel { padding: var(--s-2) var(--s-2) var(--s-3); }
.modal__skel-rows { margin-top: var(--s-4); display: flex; flex-direction: column; gap: var(--s-3); }
.modal__skel-row { display: flex; align-items: center; justify-content: space-between; gap: var(--s-3); }

/* tomo export */
.tomo { display: grid; grid-template-columns: 1fr 1fr; gap: var(--s-4); padding: var(--s-2); align-items: start; }
.tomo__form { grid-column: 1; grid-row: 1; display: flex; flex-direction: column; gap: var(--s-3); }
.mdex { grid-column: 1; grid-row: 2; border-top: 1px solid var(--line); padding-top: var(--s-3); }
.tomo__pick { grid-column: 2; grid-row: 1 / span 2; }

.mdex__head { display: flex; align-items: center; justify-content: space-between; font-size: var(--fs-xs); color: var(--ink-faint); margin-bottom: var(--s-2); }
.btn-xs { display: inline-flex; align-items: center; gap: 0.3125rem; padding: 4px 0.625rem; border-radius: var(--r-sm); font-size: var(--fs-2xs); font-weight: 600; color: var(--azure-bright); border: 1px solid var(--azure); }
.btn-xs:hover { background: var(--azure-haze); }
.mdex__match { font-size: var(--fs-2xs); color: var(--jade); margin-bottom: var(--s-2); display: flex; align-items: center; gap: 0.3125rem; }
.mdex__match.is-approx { color: var(--amber, var(--ink-faint)); }
.mdex__match a { color: var(--azure-bright); text-decoration: none; }
.mdex__search { display: flex; gap: var(--s-2); margin-bottom: var(--s-2); }
.mdex__search input { flex: 1; min-width: 0; padding: 0.3125rem 0.5625rem; border-radius: var(--r-sm); background: var(--surface); border: 1px solid var(--line-2); color: var(--ink); font-size: var(--fs-2xs); }
.mdex__search input:focus { outline: none; border-color: var(--azure); }
.mdex__results { display: flex; flex-direction: column; gap: 4px; max-height: 11.25rem; overflow-y: auto; margin-bottom: var(--s-3); }
.mdres { display: flex; align-items: center; gap: var(--s-2); padding: 4px; border-radius: var(--r-sm); border: 1px solid var(--line); text-align: left; transition: all var(--t-fast); }
.mdres:hover { border-color: var(--azure); background: var(--azure-haze); }
.mdres img { width: 1.75rem; height: 2.5rem; object-fit: cover; border-radius: var(--r-xs); flex-shrink: 0; }
.mdres__t { font-size: var(--fs-2xs); color: var(--ink-soft); line-height: 1.25; }
.mdres__t small { color: var(--ink-faint); }
.mdex__vols { display: flex; flex-wrap: wrap; gap: 0.3125rem; margin-bottom: var(--s-3); }
.volchip { padding: 4px 0.625rem; border-radius: var(--r-pill); font-size: var(--fs-2xs); color: var(--violet); border: 1px solid color-mix(in srgb, var(--violet) 30%, transparent); transition: all var(--t-fast); }
.volchip:hover { background: color-mix(in srgb, var(--violet) 14%, transparent); }
.mdex__covers { display: grid; grid-template-columns: repeat(auto-fill, minmax(3rem, 1fr)); gap: 0.375rem; max-height: 11.25rem; overflow-y: auto; }
.covsel { position: relative; aspect-ratio: 2/3; border-radius: var(--r-xs); overflow: hidden; border: 2px solid transparent; }
.covsel img { width: 100%; height: 100%; object-fit: cover; }
.covsel.is-sel { border-color: var(--azure); }
.covsel__v { position: absolute; bottom: 0; left: 0; right: 0; font-family: var(--font-mono); font-size: 0.5rem; text-align: center; background: rgba(7,10,18,.75); color: var(--ice); }
.covsel__load { position: absolute; inset: 0; display: grid; place-items: center; background: rgba(7,10,18,.6); }

.tomo__form { display: flex; flex-direction: column; gap: var(--s-3); }
.fld { display: flex; flex-direction: column; gap: 0.3125rem; font-size: var(--fs-xs); color: var(--ink-faint); }
.fld input[type=text], .fld select { padding: var(--s-2) var(--s-3); border-radius: var(--r-sm); background: var(--surface); border: 1px solid var(--line-2); color: var(--ink); font-size: var(--fs-sm); }
.fld input:focus, .fld select:focus { outline: none; border-color: var(--azure); }
.fld-row { display: flex; gap: var(--s-3); }
.fld-row .fld { flex: 1; }
.codec-hint { margin: calc(-1 * var(--s-1)) 0 0; font-size: var(--fs-2xs); line-height: 1.4; color: var(--ink-faint); }
.chk { display: inline-flex; align-items: center; gap: 0.375rem; font-size: var(--fs-xs); color: var(--ink-soft); cursor: pointer; }
.exportbtn { display: inline-flex; align-items: center; justify-content: center; gap: 0.5rem; margin-top: var(--s-2); padding: var(--s-3); border-radius: var(--r-sm); background: var(--azure); color: #fff; font-weight: 600; font-size: var(--fs-sm); transition: background var(--t-fast); }
.exportbtn:hover:not(:disabled) { background: var(--azure-bright); }
.exportbtn:disabled { opacity: .5; cursor: not-allowed; }
.exportbtn--drive { background: transparent; color: var(--azure-bright); border: 1px solid var(--azure); margin-top: var(--s-1); }
.exportbtn--drive:hover:not(:disabled) { background: var(--azure-haze); color: var(--azure-bright); }
.mdex__auto { display: flex; flex-wrap: wrap; align-items: center; gap: var(--s-2); margin-top: var(--s-2); padding: var(--s-2) var(--s-3);
  border: 1px solid var(--azure); border-radius: var(--r-sm); background: var(--azure-haze); }
.mdex__auto-info { flex: 1 1 auto; font-size: var(--fs-xs); color: var(--ink-soft); }
.mdex__auto-info strong { color: var(--azure-bright); }
.mdex__auto-cov { display: inline-flex; align-items: center; gap: 0.3125rem; font-size: var(--fs-2xs); color: var(--ink-faint); cursor: pointer; }
.exportbtn--auto { margin-top: 0; padding: var(--s-2) var(--s-4); }
.rangebox { display: inline-flex; align-items: center; gap: 3px; }
.rangebox input { width: 2.625rem; padding: 4px 0.375rem; border-radius: var(--r-xs); background: var(--surface); border: 1px solid var(--line-2); color: var(--ink); font-size: var(--fs-2xs); text-align: center; }
.rangebox button { padding: 4px 0.5rem; border-radius: var(--r-xs); font-size: var(--fs-2xs); font-weight: 600; color: var(--cyan); border: 1px solid color-mix(in srgb, var(--cyan) 30%, transparent); }
.rangebox button:hover { background: var(--cyan-glow); color: #d6fffb; }
.chap__read-dot { width: 0.375rem; height: 0.375rem; border-radius: 50%; background: var(--jade); flex-shrink: 0; }
.chap__flag { font-size: 1.05rem; line-height: 1; flex-shrink: 0; }
.colors { margin-top: var(--s-2); }
.colors--ws { margin: 0 0 var(--s-4); padding: var(--s-3); border-radius: var(--r-md); background: var(--surface-2); border: 1px solid var(--line); }
.colors__head { display: flex; align-items: center; justify-content: space-between; gap: var(--s-2); }
.colors__title { font-size: var(--fs-xs); font-weight: 600; color: var(--ink-soft); }
.colors__hint { font-size: var(--fs-2xs); color: var(--ink-faint); margin-top: var(--s-1); }
.colors__grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(2.75rem, 1fr)); gap: 0.3125rem; margin-top: var(--s-2); max-height: 9.375rem; overflow-y: auto; }
.colorpg { position: relative; aspect-ratio: 2/3; border-radius: var(--r-xs); overflow: hidden; border: 2px solid transparent; }
.colorpg img { width: 100%; height: 100%; object-fit: cover; }
.colorpg.is-excl { border-color: var(--coral); }
.colorpg.is-excl img { opacity: .4; }
.colorpg__x { position: absolute; inset: 0; display: grid; place-items: center; color: var(--coral); background: rgba(7,10,18,.4); }
.tprev { display: flex; flex-wrap: wrap; align-items: center; gap: var(--s-2); margin-top: var(--s-1); font-size: var(--fs-xs); color: var(--ink-soft); }
.tprev__main { font-family: var(--font-mono); color: var(--ink); }
.tprev__tag { font-size: var(--fs-2xs); padding: 1px 0.4375rem; border-radius: var(--r-pill); color: var(--ink-faint); border: 1px solid var(--line-2); }
.tprev__tag--4k { color: var(--cyan); border-color: var(--cyan-glow); }
.dests { display: flex; flex-wrap: wrap; gap: var(--s-3); align-items: center; margin-top: var(--s-2); font-size: var(--fs-xs); }
.dlink { color: var(--azure-bright); }
.dok { color: var(--jade); }

.tomo__pick { display: flex; flex-direction: column; min-height: 0; }
.selall { align-self: flex-start; margin-bottom: var(--s-2); font-size: var(--fs-xs); color: var(--azure-bright); }
.picklist { overflow-y: auto; max-height: 20rem; display: flex; flex-direction: column; gap: 2px; }
.pick { display: flex; align-items: center; gap: var(--s-2); padding: 0.375rem var(--s-2); border-radius: var(--r-xs); cursor: pointer; transition: background var(--t-fast); }
.pick:hover { background: var(--surface); }
.pick.is-sel { background: var(--azure-haze); }
.pick__box { width: 1.125rem; height: 1.125rem; display: grid; place-items: center; border-radius: 4px; border: 1px solid var(--line-strong); color: #fff; flex-shrink: 0; }
.pick.is-sel .pick__box { background: var(--azure); border-color: var(--azure); }
.pick__num { flex: 1; font-size: var(--fs-sm); }
.pick__4k { font-family: var(--font-mono); font-size: var(--fs-2xs); color: var(--cyan); }

@media (max-width: 560px) { .tomo { grid-template-columns: 1fr; } }
.center { display: grid; place-items: center; padding: var(--s-7); }
.empty { text-align: center; color: var(--ink-faint); padding: var(--s-7); }

.chaps { display: flex; flex-direction: column; gap: 4px; }
.modal__chhead { display: flex; align-items: center; justify-content: space-between; padding: var(--s-3) var(--s-3); font-weight: 600; font-size: var(--fs-sm); border-bottom: 1px solid var(--line); }
/* Novedades MangaDex — anuncio de capítulos nuevos listos */
.mdupd { margin: var(--s-3); border: 1px solid var(--azure); border-radius: var(--r-md); background: var(--azure-haze); overflow: hidden; }
.mdupd__head { display: flex; align-items: center; gap: var(--s-2); padding: var(--s-2) var(--s-3); }
.mdupd__badge { display: inline-grid; place-items: center; min-width: 1.4rem; height: 1.4rem; padding: 0 0.35rem; border-radius: var(--r-pill); background: var(--azure); color: #fff; font-size: var(--fs-2xs); font-weight: 800; }
.mdupd__txt { font-weight: 700; font-size: var(--fs-sm); color: var(--azure-bright); }
.mdupd__all { margin-left: auto; display: inline-flex; align-items: center; gap: 4px; padding: 4px 0.625rem; border-radius: var(--r-pill); border: 1px solid var(--azure); background: var(--azure); color: #fff; font-size: var(--fs-xs); font-weight: 600; cursor: pointer; transition: filter var(--t-fast); }
.mdupd__all:hover { filter: brightness(1.12); }
.mdupd__seen { display: inline-grid; place-items: center; width: 1.6rem; height: 1.6rem; border-radius: var(--r-sm); border: none; background: transparent; color: var(--ink-faint); cursor: pointer; }
.mdupd__seen:hover { background: color-mix(in srgb, var(--azure) 18%, transparent); color: var(--ink); }
.mdupd__list { list-style: none; margin: 0; padding: 0 var(--s-3) var(--s-2); display: flex; flex-direction: column; gap: 4px; max-height: 12rem; overflow-y: auto; }
.mdupd__row { display: flex; align-items: center; gap: var(--s-2); padding: 4px 0; font-size: var(--fs-xs); color: var(--ink-soft); }
.mdupd__n { font-weight: 700; color: var(--ink); min-width: 5rem; }
.mdupd__lang { color: var(--ink-soft); }
.mdupd__date { color: var(--ink-faint); margin-left: auto; }
.mdupd__dl { display: inline-flex; align-items: center; gap: 4px; padding: 3px 0.5625rem; border-radius: var(--r-pill); border: 1px solid var(--azure); background: transparent; color: var(--azure-bright); font-size: var(--fs-2xs); font-weight: 600; cursor: pointer; transition: background var(--t-fast); }
.mdupd__dl:hover { background: color-mix(in srgb, var(--azure) 18%, transparent); }
/* Leído: la fila entera se apaga (antes sólo un punto jade de 6px, invisible entre 250 filas).
   Al hover recupera el color, para poder releer sin perderla de vista. */
.chap--read { opacity: .45; }
.chap--read:hover, .chap--read.chap--now { opacity: 1; }
.chap--read .chap__num { font-weight: 500; }
.chsrc { display: inline-flex; align-items: center; gap: 4px; margin-right: auto; margin-left: var(--s-3); padding: 2px 0.5rem; border-radius: var(--r-pill); font-size: var(--fs-2xs); font-weight: 600; color: var(--azure-bright); background: var(--azure-haze); border: 1px solid color-mix(in srgb, var(--azure) 30%, transparent); }
.chap { position: relative; display: flex; align-items: center; gap: var(--s-3); padding: var(--s-2) var(--s-3); border-radius: var(--r-sm); border: 1px solid transparent; transition: background var(--t-fast), border-color var(--t-fast), opacity var(--t-fast); }
.chap:hover { background: var(--surface); border-color: var(--line); }

/* Capítulo EN CURSO: filo azul a la izquierda. Sin esto nada decía "vas por aquí". */
.chap--now::before {
  content: ''; position: absolute; left: 0; top: 50%; transform: translateY(-50%);
  width: 3px; height: 60%; border-radius: var(--r-pill);
  background: var(--azure); box-shadow: 0 0 10px var(--azure-glow);
}
.chap--now { background: color-mix(in srgb, var(--azure) 7%, transparent); }
.chap__now { display: inline-flex; align-items: center; gap: 4px; margin-left: auto;
  font-family: var(--font-mono); font-size: var(--fs-2xs); font-weight: 700; color: var(--azure-bright); }

/* Acciones AL HOVER: en reposo la fila enseña sólo número, estado y progreso. Antes los 4-6
   iconos estaban siempre visibles y con 250 capítulos eso es una pared de ruido. Se reserva su
   ancho (visibility, no display) para que la fila no salte al pasar el ratón. */
.chap__actions { display: flex; align-items: center; gap: 4px; visibility: hidden; opacity: 0; transition: opacity var(--t-fast); }
.chap:hover .chap__actions,
.chap:focus-within .chap__actions { visibility: visible; opacity: 1; }
/* Con una tarea en marcha el progreso manda: se ve siempre, aunque no haya ratón encima. */
.chap__actions:has(.chap__dlprog) { visibility: visible; opacity: 1; }
@media (hover: none) { .chap__actions { visibility: visible; opacity: 1; } }
.chap--sel { background: var(--azure-haze); border-color: color-mix(in srgb, var(--azure) 45%, transparent); }
.chap--sel:hover { background: color-mix(in oklab, var(--azure) 20%, transparent); }

/* Selección por lote: casilla + cabecera "seleccionar" + barra flotante de acciones */
.batchbox { width: 1.0625rem; height: 1.0625rem; flex-shrink: 0; display: grid; place-items: center; border-radius: 0.3125rem;
  border: 1.5px solid var(--line-strong); color: var(--ink-inverse, #06101f); background: var(--surface); transition: all var(--t-fast); }
.batchbox.is-on { background: var(--azure); border-color: var(--azure); color: #06101f; }
.batchbox.is-part { background: color-mix(in srgb, var(--azure) 40%, transparent); border-color: var(--azure); color: #06101f; }
.chap__check { flex-shrink: 0; display: grid; place-items: center; padding: 2px; }
.chap__check:hover .batchbox { border-color: var(--azure); }
.batchhead { display: flex; align-items: center; gap: var(--s-3); padding: 2px var(--s-3) var(--s-2); }
.batchhead__all { display: inline-flex; align-items: center; gap: var(--s-2); font-size: var(--fs-xs); font-weight: 600; color: var(--ink-soft); }
.batchhead__all:hover { color: var(--ink); }
.batchhead__n { font-size: var(--fs-2xs); color: var(--azure-bright); font-weight: 600; }
.batchbar { position: sticky; bottom: 0; z-index: 3; display: flex; align-items: center; justify-content: space-between; gap: var(--s-3);
  margin-top: var(--s-3); padding: var(--s-3) var(--s-4); border-radius: var(--r-md);
  background: var(--glass-strong); border: 1px solid var(--azure); box-shadow: var(--shadow-lg); backdrop-filter: blur(8px); }
.batchbar__n { font-size: var(--fs-sm); font-weight: 600; color: var(--azure-bright); }
.batchbar__acts { display: flex; align-items: center; gap: var(--s-2); }
/* Fases de la cadena en la fila: minúsculo, mono, no compite con el nº de capítulo. */
.steps { display: inline-flex; align-items: center; gap: 3px; padding: 2px 0.375rem; border-radius: var(--r-pill);
         background: var(--surface-2); border: 1px solid var(--line); flex-shrink: 0; }
.steps__s { font-family: var(--font-mono); font-size: 0.5625rem; font-weight: 700; color: var(--ink-ghost); line-height: 1; }
.steps__s.is-done { color: var(--jade); }
.steps__s.is-at { color: var(--azure-bright); }
.steps__sep { font-size: 0.5625rem; color: var(--line-strong); line-height: 1; }

/* Cadena: se lee como una frase ("luego · 4K · ES") pegada al botón que la ejecuta. */
.chain { display: flex; align-items: center; gap: 4px; padding-right: var(--s-2); margin-right: 2px; border-right: 1px solid var(--line); }
.chain__lbl { font-size: var(--fs-2xs); color: var(--ink-ghost); text-transform: lowercase; margin-right: 2px; }
.chain__chip { display: inline-flex; align-items: center; gap: 3px; padding: 3px 0.5rem; border-radius: var(--r-pill);
               font-family: var(--font-mono); font-size: var(--fs-2xs); font-weight: 700; letter-spacing: .03em;
               color: var(--ink-faint); border: 1px solid var(--line-2); transition: all var(--t-fast); }
.chain__chip:hover:not(:disabled) { color: var(--ink); border-color: var(--line-strong); }
.chain__chip.is-on { color: var(--azure-bright); border-color: var(--azure); background: var(--azure-haze); }
.chain__chip:disabled { opacity: .4; cursor: not-allowed; }
.batchbar__clear { padding: 0.375rem 0.5rem; }
.chap--4k { border-left: 2px solid var(--cyan); }
.chap--part { border-left: 2px solid var(--gold); }
.chap--src { border-left: 2px solid var(--violet); opacity: .85; }
.chap--md { border-left: 2px solid var(--coral); opacity: .85; }
.chap--src .chap__read, .chap--md .chap__read { cursor: default; }
.chap__read { flex: 1; display: flex; align-items: center; gap: var(--s-3); text-align: left; min-width: 0; }
.chap__num { font-family: var(--font-display); font-weight: 600; font-size: var(--fs-md); min-width: 3rem; }
.chap__pages { font-size: var(--fs-xs); color: var(--ink-faint); }
.chap__tag { font-family: var(--font-mono); font-size: var(--fs-2xs); font-weight: 700; padding: 1px 0.375rem; border-radius: var(--r-xs); }
.chap__tag--4k { color: var(--cyan); background: var(--cyan-glow); }
.chap__tag--part { color: var(--gold); background: color-mix(in srgb, var(--gold) 16%, transparent); }
.chap__tag--assigned { color: var(--azure-bright, var(--azure)); background: var(--azure-haze, color-mix(in srgb, var(--azure) 16%, transparent)); margin-left: 4px; }
.chap__srcpin { position: relative; flex-shrink: 0; margin-right: var(--s-2); }
.chap__srcpinbtn { display: inline-flex; align-items: center; gap: 4px; font-size: var(--fs-2xs); color: var(--ink-faint); padding: 3px 0.5rem; border-radius: var(--r-pill); border: 1px solid var(--line); background: var(--surface-2); }
.chap__srcpinbtn:hover { color: var(--azure-bright); border-color: var(--azure); }
.chap__srcmenu { position: absolute; z-index: 20; top: calc(100% + 4px); right: 0; min-width: 10rem; max-height: 12rem; overflow-y: auto; background: var(--surface-2); border: 1px solid var(--line); border-radius: var(--r-md); padding: 4px; box-shadow: var(--shadow-lg, 0 8px 24px rgba(0,0,0,.35)); }
.chap__srcmenu button { display: block; width: 100%; text-align: left; padding: 0.3125rem 0.5rem; border-radius: var(--r-sm); font-size: var(--fs-2xs); color: var(--ink); }
.chap__srcmenu button:hover { background: var(--surface); color: var(--azure-bright); }
.chap__srcmenuempty { padding: 0.375rem 0.5rem; font-size: var(--fs-2xs); }

.ib { width: 2rem; height: 1.875rem; display: grid; place-items: center; border-radius: var(--r-xs); border: 1px solid var(--line); color: var(--ink-faint); transition: all var(--t-fast); }
.ib:hover { color: var(--ink); border-color: var(--line-strong); background: var(--surface-2); }
.ib--accent:hover { color: var(--cyan); border-color: var(--cyan-glow); }
.ib--warn:hover { color: var(--gold); border-color: color-mix(in srgb, var(--gold) 40%, transparent); }
.ib--danger:hover { color: var(--coral); border-color: color-mix(in srgb, var(--coral) 40%, transparent); }
.ib--color { color: color-mix(in srgb, var(--cyan) 65%, var(--ink-faint)); }
.ib--color:hover { color: var(--cyan); border-color: var(--cyan-glow); background: color-mix(in srgb, var(--cyan) 10%, transparent); }
.ib--colordone { color: var(--jade); border-color: color-mix(in srgb, var(--jade) 35%, transparent); background: color-mix(in srgb, var(--jade) 10%, transparent); cursor: default; }

/* Selector de páginas a color */
.cpk-ov { position: fixed; inset: 0; z-index: calc(var(--z-modal) + 5); display: grid; place-items: center; padding: var(--s-5); background: rgba(7,10,18,.78); backdrop-filter: blur(8px); }
.cpk { width: min(52rem, 100%); max-height: 86vh; display: flex; flex-direction: column; background: var(--glass-strong); border: 1px solid var(--line-2); border-radius: var(--r-lg); box-shadow: var(--shadow-xl); overflow: hidden; }
.cpk__head { display: flex; align-items: flex-start; justify-content: space-between; gap: var(--s-3); padding: var(--s-4) var(--s-5); border-bottom: 1px solid var(--line); }
.cpk__head h3 { font-size: var(--fs-md); font-weight: 700; color: var(--ink); }
.cpk__head p { font-size: var(--fs-xs); color: var(--ink-soft); margin-top: 2px; }
.cpk__x { width: 2rem; height: 2rem; display: grid; place-items: center; border-radius: var(--r-sm); color: var(--ink-soft); border: 1px solid var(--line); flex-shrink: 0; }
.cpk__x:hover { color: var(--ink); border-color: var(--line-strong); }
.cpk__center { padding: var(--s-7); display: grid; place-items: center; }
.cpk__grid { flex: 1; overflow-y: auto; padding: var(--s-4); display: grid; grid-template-columns: repeat(auto-fill, minmax(6rem, 1fr)); gap: var(--s-3); }
.cpk__scroll { flex: 1; overflow-y: auto; }
.cpk__scroll .cpk__grid { flex: none; overflow: visible; padding-top: 0; }
.cpk__chap { border-bottom: 1px solid var(--line); }
.cpk__chap h4 { padding: var(--s-3) var(--s-4) 0; font-size: var(--fs-sm); font-weight: 700; color: var(--ink); }
.cpk__chap h4 em { font-style: normal; font-weight: 500; font-size: var(--fs-xs); color: var(--ink-soft); }
.cpk__model { display: flex; flex-direction: column; gap: 2px; font-size: var(--fs-xs); color: var(--ink-soft); margin-left: auto; }
.cpk__model select { font-size: var(--fs-xs); padding: 2px var(--s-2); border-radius: var(--r-sm); background: var(--surface-2); border: 1px solid var(--line); color: var(--ink); }
.cpk__pg { position: relative; aspect-ratio: 2/3; border-radius: var(--r-sm); overflow: hidden; border: 2px solid var(--line); background: var(--surface-2); transition: all var(--t-fast); }
.cpk__pg:hover { border-color: var(--line-strong); }
.cpk__pg.is-sel { border-color: var(--cyan); box-shadow: 0 0 0 1px var(--cyan); }
.cpk__pg img { width: 100%; height: 100%; object-fit: cover; display: block; }
.cpk__tag { position: absolute; top: 4px; left: 4px; padding: 1px 0.375rem; border-radius: var(--r-pill); font-size: 0.5625rem; font-weight: 800; text-transform: uppercase; letter-spacing: .04em; color: #fff; background: color-mix(in srgb, var(--cyan) 85%, black); }
.cpk__check { position: absolute; bottom: 4px; right: 4px; width: 1.125rem; height: 1.125rem; display: grid; place-items: center; border-radius: 50%; background: rgba(10,14,24,.7); border: 1px solid rgba(255,255,255,.4); color: #fff; }
.cpk__check.is-on { background: var(--cyan); border-color: transparent; }
.cpk__foot { display: flex; align-items: center; gap: var(--s-3); padding: var(--s-3) var(--s-5); border-top: 1px solid var(--line); }
.cpk__n { flex: 1; font-size: var(--fs-xs); color: var(--ink-soft); }
.cpk__cancel { padding: var(--s-2) var(--s-4); border-radius: var(--r-sm); font-size: var(--fs-xs); color: var(--ink-soft); border: 1px solid var(--line); }
.cpk__cancel:hover { color: var(--ink); border-color: var(--line-strong); }
.cpk__run { display: inline-flex; align-items: center; gap: 0.375rem; padding: var(--s-2) var(--s-5); border-radius: var(--r-sm); font-size: var(--fs-sm); font-weight: 600; color: #fff; background: var(--cyan); }
.cpk__run:hover:not(:disabled) { filter: brightness(1.1); }
.cpk__run:disabled { opacity: .45; cursor: default; }
.ib--read { color: var(--jade); border-color: color-mix(in srgb, var(--jade) 35%, transparent); background: color-mix(in srgb, var(--jade) 10%, transparent); }
.chap__dlbtn { display: inline-flex; align-items: center; gap: 0.375rem; padding: 0.375rem 0.75rem; border-radius: var(--r-sm); font-size: var(--fs-xs); font-weight: 600; color: var(--azure-bright); border: 1px solid var(--azure); background: transparent; transition: all var(--t-fast); }
.chap__dlbtn:hover:not(:disabled) { background: var(--azure-haze); color: #fff; }
.chap__dlbtn:disabled { opacity: .5; cursor: not-allowed; }
.chap__dlbtn--ghost { color: var(--ink-soft); border-color: var(--line-2); }
.chap__dlbtn--ghost:hover:not(:disabled) { color: var(--azure-bright); border-color: var(--azure); background: var(--azure-haze); }
.chap__srcmeta { font-size: var(--fs-2xs); color: var(--ink-ghost); max-width: 8.75rem; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.chap__dlprog { display: inline-flex; align-items: center; gap: var(--s-2); padding: 4px 0.625rem; border-radius: var(--r-sm); background: var(--azure-haze); border: 1px solid var(--azure); }
.chap__dlprog-n { font-family: var(--font-mono); font-size: var(--fs-2xs); color: var(--azure-bright); min-width: 1.75rem; }
.dl-ring { width: 1rem; height: 1rem; flex-shrink: 0; }
.dl-ring__track { fill: none; stroke: var(--surface-3); stroke-width: 3; }
.dl-ring__fill { fill: none; stroke: var(--azure); stroke-width: 3; stroke-linecap: round; stroke-dasharray: 56.5; transform: rotate(-90deg); transform-origin: 12px 12px; transition: stroke-dashoffset .4s var(--ease-silk); }

.chap__prog { display: flex; align-items: center; gap: var(--s-2); }
.chap__prog-bar { width: 5rem; height: 4px; border-radius: var(--r-pill); background: var(--surface-3); overflow: hidden; }
.chap__prog-bar span { display: block; height: 100%; background: linear-gradient(90deg, var(--cyan), var(--azure)); transition: width var(--t-base); }
.chap__prog-n { font-family: var(--font-mono); font-size: var(--fs-2xs); color: var(--cyan); }

.modal-enter-active, .modal-leave-active { transition: opacity var(--t-base); }
.modal-enter-active .modal { transition: transform var(--t-base) var(--ease-snap); }
.modal-enter-from, .modal-leave-to { opacity: 0; }
.modal-enter-from .modal { transform: scale(.95) translateY(12px); }

@media (max-width: 540px) { .modal__head { flex-direction: column; } .modal__info { padding-right: 0; } }
</style>
