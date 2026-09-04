<script setup>
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import { api } from '@/lib/api'
import { useUiStore } from '@/stores/ui'
import { useMangaStore } from '@/stores/manga'
import { useNovelsStore } from '@/stores/novels'
import { MANGA_STATUS, MANGA_STATUS_ORDER } from '@/lib/manga'
import MangaCard from '@/components/manga/MangaCard.vue'
import HistoryPanel from '@/components/manga/HistoryPanel.vue'
import DuplicateManager from '@/components/manga/DuplicateManager.vue'
import { imgProxy, imgThumb } from '@/lib/img'
import Spinner from '@/components/ui/Spinner.vue'
import Icon from '@/components/ui/Icon.vue'
import EmptyState from '@/components/ui/EmptyState.vue'
import ErrorState from '@/components/ui/ErrorState.vue'
import MangaRecRail from '@/components/manga/MangaRecRail.vue'
import ContextMenu from '@/components/ui/ContextMenu.vue'
import Select from '@/components/ui/Select.vue'
import ContentToolbar from '@/components/ui/ContentToolbar.vue'
import DensityToggle from '@/components/ui/DensityToggle.vue'
import ContinueRail from '@/components/media/ContinueRail.vue'
import { useTagsStore } from '@/stores/tags'
import { opcionesGenero, generoActivo, conGenero } from '@/lib/generos'
import Skeleton from '@/components/ui/Skeleton.vue'
import { useGridKeyboard } from '@/lib/useGridKeyboard'
import { onSSE } from '@/lib/sse'

const ui = useUiStore()
const manga = useMangaStore()
const tags = useTagsStore()
tags.load()

// Menú contextual (clic derecho) sobre las tarjetas de manga.
const cm = ref({ open: false, x: 0, y: 0, items: [] })
function openMenu(e, m) {
  cm.value = {
    open: true, x: e.clientX, y: e.clientY,
    items: [
      { label: 'Abrir', icon: 'library', action: () => manga.open(m) },
      { label: 'Continuar leyendo', icon: 'play', action: () => manga.resumeManga(m) },
      { label: 'Etiquetas…', icon: 'spark', action: () => tags.openPicker('manga', m.id, m.name) },
      { sep: true },
      { label: 'Quitar de biblioteca', icon: 'trash', danger: true, action: async () => { await manga.open(m); manga.deleteManga() } },
    ],
  }
}
/* Herramientas de la biblioteca que NO son de uso diario: viven detrás de `⋯`, no en la barra.
 * `toolsBusy` mantiene visible que algo corre aunque el menú esté cerrado — una tarea en marcha
 * escondida en un desplegable es una tarea que el usuario cree que no lanzó. */
const toolsBusy = computed(() => findingCovers.value || !!manga.offlineCovers?.running)
function openTools(e) {
  const r = e.currentTarget.getBoundingClientRect()
  cm.value = {
    open: true, x: r.left, y: r.bottom + 6,
    items: [
      { label: 'Historial de lectura', icon: 'clock', action: () => { showHistory.value = true } },
      { label: 'Revisar duplicados', icon: 'alert', action: () => { duplicatesOpen.value = true } },
      { label: 'Explorar novelas (SkyNovels)', icon: 'book', action: () => novels.openBrowse() },
      { sep: true },
      { label: findingCovers.value ? 'Buscando portadas…' : 'Buscar portadas faltantes',
        icon: 'spark', disabled: findingCovers.value, action: findCovers },
      { label: manga.offlineCovers?.running
          ? `Descargando portadas ${manga.offlineCovers.done}/${manga.offlineCovers.total}`
          : 'Descargar portadas para offline',
        icon: 'download', disabled: !!manga.offlineCovers?.running,
        action: () => manga.downloadCoversOffline() },
    ],
  }
}

const items = ref([])
const loading = ref(true)
const error = ref('')   // '' = sin fallo; si falla, guarda el MENSAJE (no un booleano)
const search = ref('')
const statusFilter = ref('all')
// "Todo" muestra solo lo ACTIVO (igual que la biblioteca de anime): completadas y abandonadas
// solo aparecen en su propia pestaña de estado, no ensucian la lista principal.
const INACTIVE = ['completed', 'dropped']
const showHistory = ref(false)
const sort = ref(localStorage.getItem('lib-sort') || 'title')
const SORTS = [
  { id: 'title', label: 'Título' },
  { id: 'recent', label: 'Leído reciente' },
  { id: 'pending', label: 'Sin leer' },
  { id: 'chapters', label: 'Capítulos' },
  { id: 'updates', label: 'Novedades' },
  { id: 'size', label: 'Tamaño' },
]
watch(sort, (s) => { localStorage.setItem('lib-sort', s); if (s === 'size') ensureSizes() })

// Tamaño real por serie: se pide una sola vez, de forma perezosa, al ordenar por tamaño.
const sizeMap = ref(null)
async function ensureSizes() {
  if (sizeMap.value) return
  try {
    const d = await api.get('/api/storage/summary')
    const m = {}
    for (const s of (d.series || [])) m[s.name] = (s.original_bytes || 0) + (s.upscaled_bytes || 0)
    sizeMap.value = m
  } catch (_) { sizeMap.value = {} }
}
// Ranking de "leído recientemente" (título → posición); los no leídos van al final.
const readRank = computed(() => {
  const rank = {}
  manga.recentlyRead(999).forEach((r, i) => { rank[r.title] = i })
  return rank
})

const statusCounts = computed(() => {
  // El contador de "Todo" refleja lo que muestra: solo activos (como en anime).
  const c = { all: items.value.filter(m => !INACTIVE.includes(m.status)).length }
  for (const k of MANGA_STATUS_ORDER) c[k] = items.value.filter(m => m.status === k).length
  return c
})

// Filtros para la barra compartida: "Todo" + un pill por estado de lectura.
const libFilters = computed(() => [
  { id: 'all', label: 'Todo', n: statusCounts.value.all },
  ...MANGA_STATUS_ORDER.map(k => ({ id: k, label: MANGA_STATUS[k].label, n: statusCounts.value[k], color: MANGA_STATUS[k].color })),
])

/* Las etiquetas van en UN desplegable, no en pills.
 *
 * Primero fueron pills junto a los estados y era un error de escala: los estados son cinco y
 * fijos, las etiquetas las inventa el usuario y no tienen tope — la barra crecía sin control y se
 * volvía fea justo cuando más etiquetas tienes, que es cuando más falta hacen. Un desplegable
 * ocupa lo mismo con 2 que con 40, y es además el gesto correcto: los estados se ven de un vistazo
 * porque siempre son los mismos; las etiquetas hay que ir a buscarlas.
 *
 * Comparte `statusFilter` con los pills a propósito: filtrar es UNA cosa, así que elegir etiqueta
 * suelta el estado y viceversa, sin código extra. */
const tagOptions = computed(() => [
  { value: '', label: 'Todas las etiquetas' },
  ...tags.universe('manga').map(t => ({
    value: t, label: t,
    hint: String(items.value.filter(m => tags.forWork('manga', m.id).includes(t)).length),
  })),
])
const tagFilter = computed({
  get: () => (statusFilter.value.startsWith('tag:') ? statusFilter.value.slice(4) : ''),
  set: (v) => { statusFilter.value = v ? `tag:${v}` : 'all' },
})

/* Géneros: mismo desplegable, misma ranura de filtro (ver `lib/generos.js`). Los géneros de manga
   llegan de AniList en segundo plano (`loadGenres`), así que la lista se rellena sola cuando
   aterrizan; hasta entonces está vacía y el control ni se pinta. */
const generos = computed(() => opcionesGenero(items.value))

// "Continuar leyendo": series con progreso reciente, cruzadas con la biblioteca (cover/nombre).
const continueItems = computed(() => {
  const byId = new Map(items.value.map(m => [m.id, m]))
  return manga.recentlyRead(10)
    .map(r => { const m = byId.get(r.title); return m ? { ...m, _resume: r } : null })
    .filter(Boolean)
    .slice(0, 8)
})

// Forma genérica del riel compartido.
const continueRail = computed(() => continueItems.value.map(m => ({
  id: m.id,
  raw: m,
  thumb: m.cover ? imgProxy(m.cover, 160) : '',
  title: m.name,
  subtitle: `Cap. ${m._resume.lastChapter}${m._resume.pct ? ` · ${m._resume.pct}%` : ''}`,
  badge: `CAP ${m._resume.lastChapter}`,
  progress: m._resume.pct || 0,
})))

/* Aquí vivía un `MediaHero` con el arte de tu propia colección. Retirado por decisión del
   usuario: el 33 % de la biblioteca no tiene `bannerImage` en AniList, así que caía a la PORTADA
   difuminada — y una portada tope 1000 px estirada a un marco de 1341 px de ancho se ve mal por
   definición, no por un bug que se pueda arreglar. El endpoint `/api/anilist/manga/banners` que
   lo alimentaba se ha borrado con él. */

const filtered = computed(() => {
  let list = items.value
  if (manga.pendingDelete.length) list = list.filter(m => !manga.pendingDelete.includes(m.id))
  // Estado como sección primaria (como anime): "Todo" = solo activos; cada estado, su pestaña.
  if (generoActivo(statusFilter.value)) {
    // Como con las etiquetas: filtrar por género NO esconde lo terminado. Buscas algo que leer,
    // y una obra acabada es justo eso.
    list = conGenero(list, generoActivo(statusFilter.value))
  }
  else if (statusFilter.value.startsWith('tag:')) {
    // Filtrar por etiqueta NO esconde lo terminado: si etiquetaste algo, quieres verlo salga
    // como salga. Por eso no se aplica aquí el descarte de INACTIVE.
    const t = statusFilter.value.slice(4)
    list = list.filter(m => tags.forWork('manga', m.id).includes(t))
  }
  else if (statusFilter.value !== 'all') list = list.filter(m => m.status === statusFilter.value)
  else list = list.filter(m => !INACTIVE.includes(m.status))
  const q = search.value.trim().toLowerCase()
  if (q) list = list.filter(m => (m.name || '').toLowerCase().includes(q))

  const upd = (m) => manga.updatesByTitle[m.name]?.new_count || 0
  const cmp = {
    title: (a, b) => (a.name || '').localeCompare(b.name || ''),
    chapters: (a, b) => (b.chapter_count || 0) - (a.chapter_count || 0),
    // Lo que te falta por leer — el orden que la tarjeta ahora hace visible.
    pending: (a, b) => ((b.chapter_count || 0) - manga.readCountOf(b.id)) - ((a.chapter_count || 0) - manga.readCountOf(a.id)),
    updates: (a, b) => upd(b) - upd(a),
    recent: (a, b) => (readRank.value[a.id] ?? 1e9) - (readRank.value[b.id] ?? 1e9),
    size: (a, b) => ((sizeMap.value?.[b.id] || 0) - (sizeMap.value?.[a.id] || 0)),
  }[sort.value]
  return cmp ? [...list].sort(cmp) : list
})

const { selected: gridSelected, count: gridSelectionCount, clear: clearGridSelection, onKey: onGridKey, onSelect: onGridSelect } =
  useGridKeyboard(() => filtered.value.map(m => m.id))
const selectedMangas = computed(() => filtered.value.filter(m =>
  gridSelected.has(String(m.id)) && m.kind !== 'novel'))
const selectedNovels = computed(() => filtered.value.filter(m =>
  gridSelected.has(String(m.id)) && m.kind === 'novel'))

const totals = computed(() => ({
  series: items.value.length,
  chapters: items.value.reduce((a, m) => a + (m.chapter_count || 0), 0),
  upscaled: items.value.filter(m => (m.upscaled || 0) > 0).length,
}))

const novels = useNovelsStore()
const duplicatesOpen = ref(false)

// Una novela abre su FICHA (sinopsis + capítulos + continuar), igual que un manga abre la suya.
// Entrar directo a leer perdía el contexto: no se veía por dónde ibas ni se podía saltar de capítulo.
function openItem(m) {
  if (m.kind === 'novel' && m.novel) {
    return novels.openDetail({ id: m.trackedId, title: m.name, cover: m.cover, novel: m.novel })
  }
  manga.open(m)
}

function openDuplicate(record) {
  const local = record.kind === 'folder'
  manga.open({
    id: local ? record.name : (record.tracked_id || record.name),
    name: record.name,
    cover: record.cover,
    mdId: record.md_id || null,
    al_id: record.al_id || null,
    trackedId: record.tracked_id || record.md_id || null,
    trackedOnly: !local,
    status: record.status || '',
    source_meta: record.source_meta || undefined,
  })
}

const findingCovers = ref(false)
let stopLibraryEvents = null
let libraryReloadTimer = 0

async function load() {
  loading.value = true; error.value = ''
  try {
    // El cruce disco+seguimiento vive en el backend (`library_overview.py`), junto al resto
    // de la lógica de identidad: aquí sólo se pinta lo que llega.
    items.value = (await api.get('/api/library/overview')) || []
    loadGenres()          // en segundo plano: la rejilla no espera a AniList para pintarse
  } catch (e) {
    // El mensaje real viaja hasta la vista: `ErrorState` lo pinta en pequeño y convierte
    // un "no va" en un informe de fallo útil. El toast se desvanece; esto se queda.
    error.value = e?.body || e?.message || 'Error desconocido'
    ui.toast('No se pudo cargar la biblioteca', 'error')
  } finally {
    loading.value = false
  }
}

async function deleteSelectedMangas() {
  if (!selectedMangas.value.length) return
  const accepted = await manga.deleteMangasBatch(selectedMangas.value)
  if (accepted) clearGridSelection()
}

/* Géneros de la tarjeta. Un solo POST para toda la biblioteca (el backend cachea 30 días por
   obra), y si AniList no responde las tarjetas se quedan como estaban: un género que falta no
   puede tumbar la biblioteca.
   Sólo 27 de 218 obras tienen `al_id`, así que las demás van por TÍTULO — el backend las resuelve
   a ritmo de tanda, y por eso esto se vuelve a llamar en cada carga: completa lo que faltó. */
async function loadGenres() {
  const al_ids = [...new Set(items.value.map(m => m.al_id).filter(Boolean))]
  const titles = [...new Set(items.value.filter(m => !m.al_id && !m.novel).map(m => m.name).filter(Boolean))]
  if (!al_ids.length && !titles.length) return
  try {
    const g = await api.post('/api/anilist/genres_by_id', { al_ids, titles })
    if (!g) return
    items.value = items.value.map(m => {
      const gen = g[m.al_id] || g[m.name]
      return gen?.length ? { ...m, genres: gen } : m
    })
  } catch (_) { /* sin géneros, la tarjeta sigue siendo la de siempre */ }
}

async function findCovers() {
  findingCovers.value = true
  ui.toast('Buscando portadas faltantes en MangaDex…', 'info')
  try {
    const found = await api.get('/api/library/search-covers')
    const n = Object.keys(found || {}).length
    if (n) {
      items.value = items.value.map(m => found[m.id] && !m.cover ? { ...m, cover: found[m.id] } : m)
      ui.toast(`${n} portadas encontradas`, 'ok')
    } else ui.toast('No se encontraron portadas nuevas', 'warn')
  } catch (_) { ui.toast('Error buscando portadas', 'error') }
  finally { findingCovers.value = false }
}
function scheduleLibraryReload() {
  if (libraryReloadTimer) return
  libraryReloadTimer = window.setTimeout(() => {
    libraryReloadTimer = 0
    load()
  }, 180)
}

onMounted(() => {
  stopLibraryEvents = onSSE('library_changed', scheduleLibraryReload)
  load(); if (!manga.updatesLoaded) manga.loadUpdates(); if (sort.value === 'size') ensureSizes(); manga.loadForYou()
})
onUnmounted(() => {
  stopLibraryEvents?.()
  stopLibraryEvents = null
  if (libraryReloadTimer) window.clearTimeout(libraryReloadTimer)
  libraryReloadTimer = 0
})
// Reload the grid after a manga is deleted from the modal.
watch(() => manga.libraryDirty, () => load())
</script>

<template>
  <div class="view">
    <!-- Sin titular: la barra superior ya dice «Biblioteca» justo encima, y las pestañas
         Descargados/Locales están entre medias — la palabra salía dos veces en 150 px. Mi Anime,
         la vista de referencia, tampoco tiene <h1>: el contenido empieza arriba del todo, y aquí
         eso significa que «Continuar leyendo» es lo primero que ves. Las cifras de la colección
         siguen al pie, en `.ltotals`. -->
    <!-- Continuar leyendo — el MISMO riel que anime y series, en modo póster.
         Va ANTES de los filtros, como en la biblioteca de anime: al abrir la sección lo que quieres
         casi siempre es seguir donde lo dejaste; la barra de filtros sólo la usas cuando buscas algo
         concreto. Estaba en cuarta posición, detrás de una barra que no ibas a tocar. -->
    <ContinueRail v-if="!loading" :items="continueRail" poster title="Continuar leyendo"
                  @play="({ raw }) => manga.resumeManga(raw)" />

    <!-- Controls -->
    <ContentToolbar :filters="libFilters" :filter="statusFilter" @update:filter="statusFilter = $event"
                    :genres="generos" :search="search" @update:search="search = $event"
                    search-placeholder="Filtrar series…">
      <template #extra>
        <DensityToggle />
        <!-- Sin etiquetas puestas no aparece: quien no las usa no ve un control de más. -->
        <Select v-if="tagOptions.length > 1" v-model="tagFilter" icon="spark"
                aria-label="Filtrar por etiqueta" :options="tagOptions" />
        <Select v-model="sort" aria-label="Ordenar la biblioteca"
                :options="SORTS.map(s => ({ value: s.id, label: s.label }))" />
        <!-- Lo que NO se usa a diario vive detrás de `⋯`. Antes cuatro botones compartían peso
             visual con el filtro: "buscar portadas faltantes" se hace una vez cada meses y pesaba
             igual que lo que tocas cada día. Si algo está corriendo, el botón lo señala (una tarea
             en marcha no puede quedarse escondida en un menú). -->
        <button class="covbtn covbtn--more" :class="{ 'is-busy': toolsBusy }" @click="openTools"
                data-tip="Más herramientas" aria-label="Más herramientas de la biblioteca"
                aria-haspopup="menu" :aria-expanded="cm.open">
          <Spinner v-if="toolsBusy" :size="14" /><Icon v-else name="menu" :size="14" />
        </button>
      </template>
    </ContentToolbar>

    <!-- Grid -->
    <div v-if="loading" class="grid">
      <Skeleton v-for="n in 12" :key="n" variant="poster" />
    </div>

    <!-- El fallo va ANTES del vacío y con SU componente: usaba `EmptyState`, el mismo que dice
         "tu biblioteca está vacía". El texto los distinguía, el componente no — y la biblioteca de
         anime ya usaba `ErrorState` para lo mismo. -->
    <ErrorState v-else-if="error" title="No se pudo cargar tu biblioteca." :detail="error"
                @retry="load" />

    <!-- Un estado vacío debe enseñar la SALIDA, no sólo constatar el vacío. -->
    <EmptyState v-else-if="!filtered.length" icon="library"
                :title="items.length ? 'Sin resultados para ese filtro.' : 'Tu biblioteca está vacía.'"
                :hint="items.length ? '' : 'Descarga capítulos desde MangaDex o tus fuentes para empezar.'">
      <template #action>
        <button v-if="items.length" class="is-primary" @click="search = ''; statusFilter = 'all'">
          <Icon name="close" :size="15" /> Quitar filtros
        </button>
        <button v-else class="is-primary" @click="ui.goto('explore')">
          <Icon name="spark" :size="15" /> Explorar fuentes
        </button>
      </template>
    </EmptyState>

    <p v-if="gridSelectionCount && !loading && !error" class="grid__selection" aria-live="polite">
      <span>{{ gridSelectionCount }} marcada(s) · {{ selectedMangas.length }} manga(s)</span>
      <span v-if="selectedNovels.length" class="grid__selection-note">
        {{ selectedNovels.length }} novela(s) se gestionan desde SkyNovels
      </span>
      <button v-if="selectedMangas.length" type="button" class="grid__selection-action"
              @click="deleteSelectedMangas">Quitar seleccionados</button>
      <button type="button" data-tip="Quitar marcas" @click="clearGridSelection">Limpiar</button>
    </p>

    <TransitionGroup v-if="!loading && !error && filtered.length" name="grid" tag="div" class="grid" role="grid"
                     aria-label="Biblioteca de manga" aria-multiselectable="true"
                     @keydown="onGridKey">
      <MangaCard v-for="m in filtered" :key="m.id" :manga="m" :updates="manga.updatesByTitle[m.name]?.new_count || 0"
                 selectable
                 data-grid-item :data-grid-key="m.id" role="gridcell"
                 :aria-selected="gridSelected.has(String(m.id))"
                 :class="{ 'is-key-selected': gridSelected.has(String(m.id)) }"
                 @open="openItem(m)" @play="m.kind === 'novel' ? openItem(m) : manga.resumeManga(m)"
                 @select="onGridSelect(m.id, $event)"
                 @contextmenu.prevent="openMenu($event, m)" />
    </TransitionGroup>

    <!-- Las cifras de la colección, al PIE. Estaban justo bajo el hero, en el sitio de máxima
         prominencia, y no responden a ninguna pregunta que te hagas al abrir la sección: no son
         accionables y no cambian. Aquí siguen estando (dan gusto verlas) sin competir con lo que
         sí vas a tocar. Se ocultan mientras carga y si no hay nada. -->
    <p v-if="!loading && !error && items.length" class="ltotals">
      <span><b>{{ totals.series }}</b> series</span>
      <span class="ltotals__dot" />
      <span><b>{{ totals.chapters }}</b> capítulos</span>
      <span class="ltotals__dot" />
      <span class="ltotals__4k"><b>{{ totals.upscaled }}</b> en 4K</span>
    </p>

    <!-- Para ti: recomendaciones basadas en tu biblioteca (AniList) — al final del todo -->
    <MangaRecRail v-if="!loading && (manga.forYouLoading || manga.forYou.length)"
                  :items="manga.forYou" :loading="manga.forYouLoading"
                  title="Para ti" subtitle="Descubre mangas afines a tu biblioteca"
                  @select="manga.discoverRec" />

    <HistoryPanel :open="showHistory" @close="showHistory = false" />
    <DuplicateManager v-model:open="duplicatesOpen" @open="openDuplicate" />
    <ContextMenu v-model:open="cm.open" :x="cm.x" :y="cm.y" :items="cm.items" />
  </div>
</template>

<style scoped>
.view { position: relative; padding: var(--s-4) var(--s-6) var(--s-8); max-width: var(--content-max); margin: 0 auto; }
.grid__selection { display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: var(--s-3); margin: calc(var(--s-4) * -1) 0 var(--s-4); padding: var(--s-2) var(--s-3); border: 1px solid color-mix(in srgb, var(--azure) 34%, transparent); border-radius: var(--r-md); background: var(--azure-haze); color: var(--azure-bright); font-size: var(--fs-xs); }
.grid__selection-note { color: var(--ink-faint); }
.grid__selection-action { margin-left: auto; padding: var(--s-1) var(--s-2); border: 1px solid var(--coral); border-radius: var(--r-sm); color: var(--coral); font-size: var(--fs-xs); }
.grid__selection-action:hover { background: color-mix(in srgb, var(--coral) 14%, transparent); }
.grid__selection button { color: var(--ink-soft); font-size: var(--fs-xs); text-decoration: underline; text-underline-offset: 2px; }
.grid__selection .grid__selection-action { color: var(--coral); text-decoration: none; }
.grid__selection button:hover { color: var(--ink); }
.grid :deep(.mcard.is-key-selected .mcard__poster) { border-color: var(--azure); box-shadow: 0 0 0 2px var(--azure), 0 0 24px -8px var(--azure-glow); }

/* Caja de portada de tamaño FIJO (8.5×12.75rem = 2:3) con las imágenes en position
   absolute: así la imagen NUNCA dicta el tamaño de la tarjeta. Antes algunas salían
   apaisadas y otras normales porque la regla global `.blurup + img {position:relative}`
   dejaba la imagen principal en flujo y su aspecto influía en la caja. Escala con rem. */


/* Totales de la colección, al pie: una línea discreta, no tres cifras en tamaño display.
   Al perder el sitio prominente pierden también el peso tipográfico — si siguieran a --fs-2xl
   competirían con la rejilla desde abajo, que es el mismo problema movido de sitio. */
.ltotals { display: flex; align-items: center; justify-content: center; gap: var(--s-3);
  margin: var(--s-6) auto var(--s-2); font-size: var(--fs-xs); color: var(--ink-faint); }
.ltotals b { font-family: var(--font-mono); font-weight: 600; color: var(--ink-soft); }
.ltotals__dot { width: 3px; height: 3px; border-radius: 50%; background: var(--line-strong); }
.ltotals__4k b { color: var(--cyan); }

/* ── Toolbar ──────────────────────────────────────────────────────────── */
.toolbar {
  display: flex; align-items: center; justify-content: space-between;
  flex-wrap: wrap; gap: var(--s-3);
  margin-bottom: var(--s-6);
}
.filters { display: flex; flex-wrap: wrap; gap: var(--s-2); align-items: center; }
.filters__sep { width: 1px; height: 1.2rem; background: var(--line); margin: 0 var(--s-1); }
.pill {
  padding: var(--s-2) var(--s-4);
  border-radius: var(--r-pill);
  font-size: var(--fs-sm); font-weight: 500; color: var(--ink-soft);
  border: 1px solid var(--line);
  transition: all var(--t-fast) var(--ease-silk);
}
.pill:hover { color: var(--ink); border-color: var(--line-strong); }
.pill.is-active { background: var(--azure-haze); border-color: var(--azure); color: var(--azure-bright); }
.pill__n { margin-left: 0.3125rem; font-family: var(--font-mono); font-size: var(--fs-2xs); color: var(--cyan); }

.searchbox {
  display: flex; align-items: center; gap: var(--s-2);
  padding: var(--s-2) var(--s-3); width: min(17.5rem, 50vw);
  background: var(--surface); border: 1px solid var(--line); border-radius: var(--r-md);
  color: var(--ink-faint);
  transition: border-color var(--t-fast), box-shadow var(--t-fast);
}
.searchbox:focus-within { border-color: var(--azure); box-shadow: 0 0 0 3px var(--azure-haze); }
.searchbox input { flex: 1; border: none; outline: none; background: none; color: var(--ink); font-size: var(--fs-sm); }
.sortbox__ic { transform: rotate(90deg); flex: none; }
.tb-right { display: flex; align-items: center; gap: var(--s-2); }
.covbtn { display: inline-flex; align-items: center; gap: 0.375rem; padding: var(--s-2) var(--s-3); border-radius: var(--r-md); font-size: var(--fs-sm); color: var(--ink-soft); border: 1px solid var(--line); transition: all var(--t-fast); }
.covbtn:hover:not(:disabled) { color: var(--azure-bright); border-color: var(--azure); }
.covbtn:disabled { opacity: .6; }
/* `⋯`: cuadrado, sin etiqueta — es un contenedor, no una acción, y no debe competir con el
   filtro ni con el orden. Cuando hay algo corriendo dentro se tiñe de acento para que la tarea
   no quede invisible por estar guardada. */
.covbtn--more { padding: var(--s-2); min-width: 2.125rem; justify-content: center; }
.covbtn--more.is-busy { color: var(--azure-bright); border-color: var(--azure); background: var(--azure-haze); }

/* Ancho base propio; el resto de la rejilla (densidad, hueco, móvil) vive en base.css */
.grid { --card-min: 14.0625rem; }
/* ── Empty / error ────────────────────────────────────────────────────── */
.empty {
  display: flex; flex-direction: column; align-items: center; gap: var(--s-3);
  padding: var(--s-9) 0; color: var(--ink-faint); text-align: center;
}
.btn {
  display: inline-flex; align-items: center; gap: var(--s-2);
  margin-top: var(--s-2); padding: var(--s-2) var(--s-4);
  border-radius: var(--r-sm); background: var(--azure-haze); color: var(--azure-bright);
  border: 1px solid var(--azure); font-size: var(--fs-sm); font-weight: 500;
  transition: background var(--t-fast);
}
.btn:hover { background: var(--azure); color: #fff; }

@media (max-width: 540px) {
  .view { padding: var(--s-3) var(--s-4) var(--s-8); }
}
</style>
