<script setup>
import { ref, computed, onMounted, watch } from 'vue'
import { api } from '@/lib/api'
import { useUiStore } from '@/stores/ui'
import { useMangaStore } from '@/stores/manga'
import { useNovelsStore } from '@/stores/novels'
import { MANGA_STATUS, MANGA_STATUS_ORDER } from '@/lib/manga'
import MangaCard from '@/components/manga/MangaCard.vue'
import HistoryPanel from '@/components/manga/HistoryPanel.vue'
import { imgProxy, imgThumb } from '@/lib/img'
import Spinner from '@/components/ui/Spinner.vue'
import Icon from '@/components/ui/Icon.vue'
import EmptyState from '@/components/ui/EmptyState.vue'
import MangaRecRail from '@/components/manga/MangaRecRail.vue'
import ContextMenu from '@/components/ui/ContextMenu.vue'
import Select from '@/components/ui/Select.vue'
import ContentToolbar from '@/components/ui/ContentToolbar.vue'
import ContinueRail from '@/components/media/ContinueRail.vue'
import Skeleton from '@/components/ui/Skeleton.vue'
import MediaHero from '@/components/media/MediaHero.vue'

const ui = useUiStore()
const manga = useMangaStore()

// Menú contextual (clic derecho) sobre las tarjetas de manga.
const cm = ref({ open: false, x: 0, y: 0, items: [] })
function openMenu(e, m) {
  cm.value = {
    open: true, x: e.clientX, y: e.clientY,
    items: [
      { label: 'Abrir', icon: 'library', action: () => manga.open(m) },
      { label: 'Continuar leyendo', icon: 'play', action: () => manga.resumeManga(m) },
      { sep: true },
      { label: 'Quitar de biblioteca', icon: 'trash', danger: true, action: async () => { await manga.open(m); manga.deleteManga() } },
    ],
  }
}
const items = ref([])
const loading = ref(true)
const error = ref(false)
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

// "Continuar leyendo": series con progreso reciente, cruzadas con la biblioteca (cover/nombre).
const continueItems = computed(() => {
  const byId = new Map(items.value.map(m => [m.id, m]))
  return manga.recentlyRead(10)
    .map(r => { const m = byId.get(r.title); return m ? { ...m, _resume: r } : null })
    .filter(Boolean)
    .slice(0, 8)
})

// Forma genérica del riel compartido. (El scroll con la rueda ya lo trae `ContinueRail`.)
const continueRail = computed(() => continueItems.value.map(m => ({
  id: m.id,
  raw: m,
  thumb: m.cover ? imgProxy(m.cover, 160) : '',
  title: m.name,
  subtitle: `Cap. ${m._resume.lastChapter}${m._resume.pct ? ` · ${m._resume.pct}%` : ''}`,
  badge: `CAP ${m._resume.lastChapter}`,
  progress: m._resume.pct || 0,
})))

// ── Destacados del hero ───────────────────────────────────────────────────
// La cabecera era texto sobre fondo plano mientras Anime y Series tenían arte. Reutiliza
// `MediaHero` (no una cabecera nueva) alimentándolo con lo que ya está en memoria.
// El arte ancho es el `bannerImage` de AniList (MEDIDO: 67 % de la biblioteca lo tiene; TMDB no
// serviría, no indexa manga). Sin banner, `MediaHero` cae a `artFallback` y difumina la portada
// para llenar el marco — exactamente lo que hace una película sin fanart.
const heroTint = ref('rgb(77, 141, 255)')
const banners = ref({})
async function loadBanners(titles) {
  if (!titles.length) return
  try { banners.value = { ...banners.value, ...(await api.post('/api/anilist/manga/banners', { titles })) } }
  catch (_) { /* sin banner se ve la portada difuminada: no hay nada que avisar */ }
}
const heroItems = computed(() => {
  const upd = (m) => manga.updatesByTitle[m.name]?.new_count || 0
  const pool = [
    ...continueItems.value,                                                   // lo que estás leyendo
    ...items.value.filter(m => upd(m) > 0),                                   // con capítulos nuevos
    ...[...items.value].sort((a, b) => (b.chapter_count || 0) - (a.chapter_count || 0)),
  ]
  const seen = new Set()
  const out = []
  for (const m of pool) {
    if (!m.cover || seen.has(m.id)) continue
    seen.add(m.id)
    const n = upd(m)
    const read = manga.readCountOf(m.id)
    out.push({
      id: m.id,
      art: banners.value[m.name] || null,
      artFallback: m.cover,
      overline: m._resume ? 'SIGUES LEYENDO' : n ? 'CAPÍTULOS NUEVOS' : 'EN TU BIBLIOTECA',
      title: m.name,
      meta: [
        `${m.chapter_count || 0} capítulos`,
        ...(read ? [`${read} leídos`] : []),
        ...(m.upscaled ? [`${m.upscaled} en 4K`] : []),
      ],
      tags: n ? [`${n} sin descargar`] : [],
      progress: m._resume?.pct || (m.chapter_count ? Math.round(read / m.chapter_count * 100) : 0),
      actions: [
        { label: m._resume ? `Continuar · Cap. ${m._resume.lastChapter}` : 'Leer', icon: 'play',
          primary: true, run: () => (m.kind === 'novel' ? openItem(m) : manga.resumeManga(m)) },
        { label: 'Ver ficha', icon: 'library', run: () => openItem(m) },
      ],
    })
    if (out.length === 5) break
  }
  return out
})

// Los banners se piden por los títulos que el hero ha ELEGIDO, no por la biblioteca entera: 5
// en vez de 28. La clave es la lista de títulos, que no cambia al llegar los banners → sin bucle.
watch(() => heroItems.value.map(h => h.title).join('|'), (k) => {
  if (k) loadBanners(heroItems.value.map(h => h.title))
}, { immediate: true })

const filtered = computed(() => {
  let list = items.value
  if (manga.pendingDelete.length) list = list.filter(m => !manga.pendingDelete.includes(m.id))
  // Estado como sección primaria (como anime): "Todo" = solo activos; cada estado, su pestaña.
  if (statusFilter.value !== 'all') list = list.filter(m => m.status === statusFilter.value)
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

const totals = computed(() => ({
  series: items.value.length,
  chapters: items.value.reduce((a, m) => a + (m.chapter_count || 0), 0),
  upscaled: items.value.filter(m => (m.upscaled || 0) > 0).length,
}))

const novels = useNovelsStore()

// Una novela abre su FICHA (sinopsis + capítulos + continuar), igual que un manga abre la suya.
// Entrar directo a leer perdía el contexto: no se veía por dónde ibas ni se podía saltar de capítulo.
function openItem(m) {
  if (m.kind === 'novel' && m.novel) {
    return novels.openDetail({ id: m.trackedId, title: m.name, cover: m.cover, novel: m.novel })
  }
  manga.open(m)
}

const findingCovers = ref(false)

async function load() {
  loading.value = true; error.value = false
  try {
    // El cruce disco+seguimiento vive en el backend (`library_overview.py`), junto al resto
    // de la lógica de identidad: aquí sólo se pinta lo que llega.
    items.value = (await api.get('/api/library/overview')) || []
  } catch (e) {
    error.value = true
    ui.toast('No se pudo cargar la biblioteca', 'error')
  } finally {
    loading.value = false
  }
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
onMounted(() => { load(); if (!manga.updatesLoaded) manga.loadUpdates(); if (sort.value === 'size') ensureSizes(); manga.loadForYou() })
// Reload the grid after a manga is deleted from the modal.
watch(() => manga.libraryDirty, () => load())
</script>

<template>
  <div class="view">
    <div class="view__aura" :style="{ '--tint-c': heroTint }" />

    <!-- Arte de tu propia colección, no una cabecera de texto sobre fondo plano. -->
    <MediaHero v-if="heroItems.length" :items="heroItems" @tint="c => heroTint = c" />

    <!-- Hero header -->
    <header class="lhead stagger">
      <div class="lhead__head" style="--i:0">
        <p class="lhead__eyebrow"><span class="lhead__tick" /> TU COLECCIÓN LOCAL</p>
        <h1 v-if="!heroItems.length" class="lhead__title">Biblioteca</h1>
      </div>

      <div class="lhead__stats" style="--i:1">
        <div class="stat">
          <span class="stat__num">{{ totals.series }}</span>
          <span class="stat__label">series</span>
        </div>
        <div class="stat">
          <span class="stat__num">{{ totals.chapters }}</span>
          <span class="stat__label">capítulos</span>
        </div>
        <div class="stat stat--accent">
          <span class="stat__num">{{ totals.upscaled }}</span>
          <span class="stat__label">en 4K</span>
        </div>
      </div>
    </header>

    <!-- Controls -->
    <ContentToolbar :filters="libFilters" :filter="statusFilter" @update:filter="statusFilter = $event"
                    :search="search" @update:search="search = $event"
                    search-placeholder="Filtrar series…">
      <template #extra>
        <button class="covbtn" @click="novels.openBrowse()" title="Explorar el catálogo de novelas en español (SkyNovels)">
          <Icon name="book" :size="14" /> Novelas
        </button>
        <button class="covbtn" @click="showHistory = true" title="Historial de lectura">
          <Icon name="clock" :size="14" /> Historial
        </button>
        <button class="covbtn" :disabled="findingCovers" @click="findCovers" title="Buscar portadas faltantes en MangaDex">
          <Spinner v-if="findingCovers" :size="14" /><Icon v-else name="spark" :size="14" /> Portadas
        </button>
        <button class="covbtn" :disabled="manga.offlineCovers?.running" @click="manga.downloadCoversOffline()" title="Descargar todas las portadas para uso offline">
          <Spinner v-if="manga.offlineCovers?.running" :size="14" /><Icon v-else name="download" :size="14" />
          <span v-if="manga.offlineCovers?.running">{{ manga.offlineCovers.done }}/{{ manga.offlineCovers.total }}</span><span v-else>Offline</span>
        </button>
        <Select v-model="sort" aria-label="Ordenar la biblioteca"
                :options="SORTS.map(s => ({ value: s.id, label: s.label }))" />
      </template>
    </ContentToolbar>

    <!-- Continuar leyendo — el MISMO riel que anime y series, en modo póster. -->
    <ContinueRail v-if="!loading" :items="continueRail" poster title="Continuar leyendo"
                  @play="({ raw }) => manga.resumeManga(raw)" />

    <!-- Grid -->
    <div v-if="loading" class="grid">
      <Skeleton v-for="n in 12" :key="n" variant="poster" />
    </div>

    <EmptyState v-else-if="error" icon="globe" title="El backend no responde."
                hint="Comprueba que el servidor esté en marcha e inténtalo de nuevo.">
      <template #action>
        <button class="btn" @click="load"><Icon name="spark" :size="15" /> Reintentar</button>
      </template>
    </EmptyState>

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

    <TransitionGroup v-else name="grid" tag="div" class="grid">
      <MangaCard v-for="m in filtered" :key="m.id" :manga="m" :updates="manga.updatesByTitle[m.name]?.new_count || 0"
                 @open="openItem(m)" @play="m.kind === 'novel' ? openItem(m) : manga.resumeManga(m)"
                 @contextmenu.prevent="openMenu($event, m)" />
    </TransitionGroup>

    <!-- Para ti: recomendaciones basadas en tu biblioteca (AniList) — al final del todo -->
    <MangaRecRail v-if="!loading && (manga.forYouLoading || manga.forYou.length)"
                  :items="manga.forYou" :loading="manga.forYouLoading"
                  title="Para ti" subtitle="Descubre mangas afines a tu biblioteca"
                  @select="manga.discoverRec" />

    <HistoryPanel :open="showHistory" @close="showHistory = false" />
    <ContextMenu v-model:open="cm.open" :x="cm.x" :y="cm.y" :items="cm.items" />
  </div>
</template>

<style scoped>
.view { position: relative; padding: var(--s-4) var(--s-6) var(--s-8); max-width: var(--content-max); margin: 0 auto; }
/* Aura del color dominante del hero, como en Anime y Series. Decorativa: nunca bajo texto. */
.view__aura {
  position: absolute; inset: 0 0 auto 0; height: 60vh; pointer-events: none; z-index: 0;
  background: radial-gradient(80% 60% at 20% 0%, color-mix(in srgb, var(--tint-c) 20%, transparent) 0%, transparent 70%);
  transition: background 1.2s var(--ease-silk);
}
.view > :not(.view__aura) { position: relative; z-index: 1; }

/* Caja de portada de tamaño FIJO (8.5×12.75rem = 2:3) con las imágenes en position
   absolute: así la imagen NUNCA dicta el tamaño de la tarjeta. Antes algunas salían
   apaisadas y otras normales porque la regla global `.blurup + img {position:relative}`
   dejaba la imagen principal en flujo y su aspecto influía en la caja. Escala con rem. */

/* ── Hero ─────────────────────────────────────────────────────────────── */
.lhead {
  display: flex; align-items: flex-end; justify-content: space-between;
  flex-wrap: wrap; gap: var(--s-5);
  padding: var(--s-5) 0 var(--s-6);
}
.lhead__eyebrow {
  display: flex; align-items: center; gap: var(--s-2);
  font-family: var(--font-mono); font-size: var(--fs-2xs);
  letter-spacing: var(--tracking-caps); color: var(--azure);
  margin-bottom: var(--s-2);
}
.lhead__tick { width: 0.875rem; height: 1px; background: var(--azure); box-shadow: 0 0 8px var(--azure-glow); }
.lhead__title { font-size: var(--fs-3xl); }

.lhead__stats { display: flex; gap: var(--s-6); }
.stat { display: flex; flex-direction: column; }
.stat__num { font-family: var(--font-display); font-size: var(--fs-2xl); font-weight: 600; line-height: 1; }
.stat__label { font-size: var(--fs-xs); color: var(--ink-faint); margin-top: 4px; text-transform: lowercase; }
.stat--accent .stat__num { color: var(--cyan); }

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

/* ── Grid ─────────────────────────────────────────────────────────────── */
.grid { position: relative;
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(14.0625rem, 1fr));
  gap: var(--s-6) var(--s-5);
}
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
  .grid { grid-template-columns: repeat(auto-fill, minmax(10rem, 1fr)); gap: var(--s-5) var(--s-3); }
  .lhead__stats { gap: var(--s-5); }
}
</style>
