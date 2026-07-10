<script setup>
import { ref, computed, onMounted, watch } from 'vue'
import { api } from '@/lib/api'
import { useUiStore } from '@/stores/ui'
import { useMangaStore } from '@/stores/manga'
import { MANGA_STATUS, MANGA_STATUS_ORDER } from '@/lib/manga'
import MangaCard from '@/components/manga/MangaCard.vue'
import HistoryPanel from '@/components/manga/HistoryPanel.vue'
import { imgProxy, imgThumb } from '@/lib/img'
import Spinner from '@/components/ui/Spinner.vue'
import Icon from '@/components/ui/Icon.vue'
import EmptyState from '@/components/ui/EmptyState.vue'
import MangaRecRail from '@/components/manga/MangaRecRail.vue'

const ui = useUiStore()
const manga = useMangaStore()
const items = ref([])
const loading = ref(true)
const error = ref(false)
const search = ref('')
const filter = ref('all')
const statusFilter = ref('all')
const showHistory = ref(false)
const sort = ref(localStorage.getItem('lib-sort') || 'title')
const SORTS = [
  { id: 'title', label: 'Título' },
  { id: 'recent', label: 'Leído reciente' },
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

const FILTERS = computed(() => [
  { id: 'all', label: 'Todo' },
  { id: 'upscaled', label: 'Escalado 4K' },
  { id: 'downloaded', label: 'Solo descargado' },
  { id: 'updates', label: 'Novedades', count: manga.updates.length },
])

const statusCounts = computed(() => {
  const c = { all: items.value.length }
  for (const k of MANGA_STATUS_ORDER) c[k] = items.value.filter(m => m.status === k).length
  return c
})

// "Continuar leyendo": series con progreso reciente, cruzadas con la biblioteca (cover/nombre).
const continueItems = computed(() => {
  const byId = new Map(items.value.map(m => [m.id, m]))
  return manga.recentlyRead(10)
    .map(r => { const m = byId.get(r.title); return m ? { ...m, _resume: r } : null })
    .filter(Boolean)
    .slice(0, 8)
})

// Rueda del ratón → scroll HORIZONTAL del rail. En la shell nativa la rueda solo
// mueve la página en vertical, así que sin esto no había forma de recorrer el rail
// "Continuar leyendo" de lado. Solo actúa si hay desbordamiento y el gesto es vertical
// (deja pasar el scroll horizontal nativo de trackpad).
function railWheel(e) {
  const el = e.currentTarget
  if (el.scrollWidth <= el.clientWidth) return
  if (Math.abs(e.deltaY) <= Math.abs(e.deltaX)) return
  el.scrollLeft += e.deltaY
  e.preventDefault()
}

const filtered = computed(() => {
  let list = items.value
  if (manga.pendingDelete.length) list = list.filter(m => !manga.pendingDelete.includes(m.id))
  if (filter.value === 'upscaled') list = list.filter(m => (m.upscaled || 0) > 0)
  if (filter.value === 'downloaded') list = list.filter(m => !(m.upscaled || 0))
  if (filter.value === 'updates') list = list.filter(m => manga.updatesByTitle[m.name])
  if (statusFilter.value !== 'all') list = list.filter(m => m.status === statusFilter.value)
  const q = search.value.trim().toLowerCase()
  if (q) list = list.filter(m => (m.name || '').toLowerCase().includes(q))

  const upd = (m) => manga.updatesByTitle[m.name]?.new_count || 0
  const cmp = {
    title: (a, b) => (a.name || '').localeCompare(b.name || ''),
    chapters: (a, b) => (b.chapter_count || 0) - (a.chapter_count || 0),
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

const findingCovers = ref(false)

async function load() {
  loading.value = true; error.value = false
  try {
    const [local, mdLib] = await Promise.all([
      api.get('/api/library'),
      api.get('/api/mangadex/local_library').catch(() => []),
    ])
    // Generic tracked-manga lookups (local_library.json): by id (MangaDex uuid, src_*
    // composite, or sanitized local title) and by title (legacy fallback match).
    const trackedById = {}
    const trackedByName = {}
    for (const t of (mdLib || [])) {
      trackedById[t.id] = t
      const key = (t.title || '').toLowerCase().trim()
      if (key) trackedByName[key] = t
    }
    const matchedIds = new Set()
    // Merge local (disk-scanned) manga with their tracked status, if any
    const seen = new Set()
    const merged = []
    for (const m of (local || [])) {
      seen.add((m.name || '').toLowerCase().trim())
      const sm = m.source_meta
      let tracked = (sm?.sourceId && sm?.mangaId) ? trackedById[`src_${sm.sourceId}_${sm.mangaId}`] : null
      if (!tracked) tracked = trackedById[m.id]
      if (!tracked) tracked = trackedByName[(m.name || '').toLowerCase().trim()]
      if (tracked) matchedIds.add(tracked.id)
      const kind = tracked?.kind || 'mangadex'
      merged.push({
        ...m,
        mdId: tracked && kind === 'mangadex' ? tracked.id : null,
        trackedId: tracked?.id || null,
        status: tracked?.status || '',
      })
    }
    // Add tracked-only entries (added to "Mi Biblioteca" but nothing downloaded yet)
    for (const t of (mdLib || [])) {
      const key = (t.title || '').toLowerCase().trim()
      if (!key || seen.has(key) || matchedIds.has(t.id)) continue
      seen.add(key)
      const kind = t.kind || 'mangadex'
      const sourceMeta = kind === 'source' && t.source_id && t.manga_id
        ? { sourceId: t.source_id, mangaId: t.manga_id, sourceName: t.source_name || '', sourceLang: t.source_lang || '' }
        : null
      merged.push({
        id: t.title, name: t.title, chapter_count: 0, upscaled: 0, cover: t.cover || null,
        mdId: kind === 'mangadex' ? t.id : null,
        trackedId: t.id, trackedOnly: true, status: t.status || '',
        source_meta: sourceMeta,
      })
    }
    items.value = merged
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
    <!-- Hero header -->
    <header class="hero stagger">
      <div class="hero__head" style="--i:0">
        <p class="hero__eyebrow"><span class="hero__tick" /> TU COLECCIÓN LOCAL</p>
        <h1 class="hero__title">Biblioteca</h1>
      </div>

      <div class="hero__stats" style="--i:1">
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
    <div class="toolbar stagger">
      <div class="filters" style="--i:2">
        <button v-for="f in FILTERS" :key="f.id" class="pill" :class="{ 'is-active': filter === f.id }"
                @click="filter = f.id" v-show="f.id !== 'updates' || f.count">
          {{ f.label }}<span v-if="f.count" class="pill__n">{{ f.count }}</span>
        </button>
        <span class="filters__sep" />
        <button class="pill" :class="{ 'is-active': statusFilter === 'all' }" @click="statusFilter = 'all'">
          Todo estado
        </button>
        <button v-for="k in MANGA_STATUS_ORDER" :key="k" v-show="statusCounts[k]" class="pill"
                :class="{ 'is-active': statusFilter === k }" @click="statusFilter = k"
                :style="statusFilter === k ? { color: MANGA_STATUS[k].color, borderColor: MANGA_STATUS[k].color } : {}">
          {{ MANGA_STATUS[k].label }} <span class="pill__n">{{ statusCounts[k] }}</span>
        </button>
      </div>
      <div class="tb-right" style="--i:2">
        <button class="covbtn" @click="showHistory = true" title="Historial de lectura">
          <Icon name="clock" :size="14" /> Historial
        </button>
        <button class="covbtn" :disabled="findingCovers" @click="findCovers" title="Buscar portadas faltantes en MangaDex">
          <span v-if="findingCovers" class="covspin" /><Icon v-else name="spark" :size="14" /> Portadas
        </button>
        <button class="covbtn" :disabled="manga.offlineCovers?.running" @click="manga.downloadCoversOffline()" title="Descargar todas las portadas para uso offline">
          <span v-if="manga.offlineCovers?.running" class="covspin" /><Icon v-else name="download" :size="14" />
          <span v-if="manga.offlineCovers?.running">{{ manga.offlineCovers.done }}/{{ manga.offlineCovers.total }}</span><span v-else>Offline</span>
        </button>
        <label class="sortbox" title="Ordenar la biblioteca">
          <Icon name="chevron" :size="13" class="sortbox__ic" />
          <select v-model="sort">
            <option v-for="s in SORTS" :key="s.id" :value="s.id">{{ s.label }}</option>
          </select>
        </label>
        <label class="searchbox">
          <Icon name="search" :size="15" />
          <input v-model="search" type="search" placeholder="Filtrar series…" />
        </label>
      </div>
    </div>

    <!-- Continuar leyendo -->
    <section v-if="!loading && continueItems.length" class="cont">
      <h2 class="cont__title"><Icon name="spark" :size="15" /> Continuar leyendo</h2>
      <div class="cont__rail" @wheel="railWheel">
        <button v-for="m in continueItems" :key="m.id" class="contcard" @click="manga.resumeManga(m)"
                :title="`Reanudar ${m.name} · Cap. ${m._resume.lastChapter}`">
          <div class="contcard__cov">
            <img v-if="imgThumb(m.cover)" :src="imgThumb(m.cover)" class="blurup" aria-hidden="true" alt="" />
            <img v-if="m.cover" :src="imgProxy(m.cover)" loading="lazy" alt="" />
            <div v-else class="contcard__ph"><Icon name="library" :size="20" /></div>
            <span class="contcard__play"><Icon name="spark" :size="18" /></span>
            <span v-if="m._resume.pct" class="contcard__bar"><span :style="{ width: m._resume.pct + '%' }" /></span>
          </div>
          <span class="contcard__name">{{ m.name }}</span>
          <span class="contcard__ch">Cap. {{ m._resume.lastChapter }}<template v-if="m._resume.pct"> · {{ m._resume.pct }}%</template></span>
        </button>
      </div>
    </section>

    <!-- Grid -->
    <div v-if="loading" class="grid">
      <div v-for="n in 12" :key="n" class="skeleton" />
    </div>

    <EmptyState v-else-if="error" icon="globe" title="El backend no responde."
                hint="Comprueba que el servidor esté en marcha e inténtalo de nuevo.">
      <template #action>
        <button class="btn" @click="load"><Icon name="spark" :size="15" /> Reintentar</button>
      </template>
    </EmptyState>

    <EmptyState v-else-if="!filtered.length" icon="library"
                :title="items.length ? 'Sin resultados para ese filtro.' : 'Tu biblioteca está vacía.'"
                :hint="items.length ? '' : 'Descarga capítulos desde MangaDex o tus fuentes para empezar.'" />

    <div v-else class="grid">
      <MangaCard v-for="m in filtered" :key="m.id" :manga="m" :updates="manga.updatesByTitle[m.name]?.new_count || 0" @click="manga.open(m)" />
    </div>

    <!-- Para ti: recomendaciones basadas en tu biblioteca (AniList) — al final del todo -->
    <MangaRecRail v-if="!loading && (manga.forYouLoading || manga.forYou.length)"
                  :items="manga.forYou" :loading="manga.forYouLoading"
                  title="Para ti" subtitle="Descubre mangas afines a tu biblioteca"
                  @select="manga.discoverRec" />

    <HistoryPanel :open="showHistory" @close="showHistory = false" />
  </div>
</template>

<style scoped>
.view { padding: var(--s-4) var(--s-6) var(--s-8); max-width: var(--content-max); margin: 0 auto; }

/* ── Continuar leyendo ────────────────────────────────────────────────── */
.cont { margin: var(--s-2) 0 var(--s-6); }
.cont__title { display: flex; align-items: center; gap: var(--s-2); font-size: var(--fs-md); color: var(--azure-bright); margin-bottom: var(--s-3); }
.cont__rail { display: flex; gap: var(--s-4); overflow-x: auto; padding-bottom: var(--s-2); scroll-snap-type: x proximity; }
.contcard { flex: 0 0 8.5rem; width: 8.5rem; scroll-snap-align: start; display: flex; flex-direction: column; gap: 4px; text-align: left; }
/* Caja de portada de tamaño FIJO (8.5×12.75rem = 2:3) con las imágenes en position
   absolute: así la imagen NUNCA dicta el tamaño de la tarjeta. Antes algunas salían
   apaisadas y otras normales porque la regla global `.blurup + img {position:relative}`
   dejaba la imagen principal en flujo y su aspecto influía en la caja. Escala con rem. */
.contcard__cov { position: relative; width: 8.5rem; height: 12.75rem; border-radius: var(--r-md); overflow: hidden; background: var(--surface-2); border: 1px solid var(--line); }
.contcard__cov img { position: absolute; inset: 0; width: 100%; height: 100%; object-fit: cover; display: block; transition: transform var(--t-fast); }
.contcard:hover .contcard__cov img { transform: scale(1.04); }
.contcard__ph { width: 100%; height: 100%; display: grid; place-items: center; color: var(--ink-faint); }
.contcard__play { position: absolute; inset: 0; display: grid; place-items: center; background: rgba(0,0,0,.35); color: #fff; opacity: 0; transition: opacity var(--t-fast); }
.contcard:hover .contcard__play { opacity: 1; }
.contcard__bar { position: absolute; left: 0; right: 0; bottom: 0; height: 3px; background: rgba(0,0,0,.4); }
.contcard__bar span { display: block; height: 100%; background: var(--azure); box-shadow: 0 0 6px var(--azure-glow); }
.contcard__name { font-size: var(--fs-xs); font-weight: 500; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.contcard__ch { font-size: var(--fs-2xs); color: var(--ink-faint); font-variant-numeric: tabular-nums; }

/* ── Hero ─────────────────────────────────────────────────────────────── */
.hero {
  display: flex; align-items: flex-end; justify-content: space-between;
  flex-wrap: wrap; gap: var(--s-5);
  padding: var(--s-5) 0 var(--s-6);
}
.hero__eyebrow {
  display: flex; align-items: center; gap: var(--s-2);
  font-family: var(--font-mono); font-size: var(--fs-2xs);
  letter-spacing: var(--tracking-caps); color: var(--azure);
  margin-bottom: var(--s-2);
}
.hero__tick { width: 14px; height: 1px; background: var(--azure); box-shadow: 0 0 8px var(--azure-glow); }
.hero__title { font-size: var(--fs-3xl); }

.hero__stats { display: flex; gap: var(--s-6); }
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
.pill__n { margin-left: 5px; font-family: var(--font-mono); font-size: var(--fs-2xs); color: var(--cyan); }

.searchbox {
  display: flex; align-items: center; gap: var(--s-2);
  padding: var(--s-2) var(--s-3); width: min(17.5rem, 50vw);
  background: var(--surface); border: 1px solid var(--line); border-radius: var(--r-md);
  color: var(--ink-faint);
  transition: border-color var(--t-fast), box-shadow var(--t-fast);
}
.searchbox:focus-within { border-color: var(--azure); box-shadow: 0 0 0 3px var(--azure-haze); }
.searchbox input { flex: 1; border: none; outline: none; background: none; color: var(--ink); font-size: var(--fs-sm); }
.sortbox {
  display: flex; align-items: center; gap: 4px;
  padding: var(--s-2) var(--s-2) var(--s-2) var(--s-3);
  background: var(--surface); border: 1px solid var(--line); border-radius: var(--r-md);
  color: var(--ink-faint); transition: border-color var(--t-fast);
}
.sortbox:focus-within { border-color: var(--azure); }
.sortbox__ic { transform: rotate(90deg); flex: none; }
.sortbox select { border: none; outline: none; background: none; color: var(--ink); font-size: var(--fs-sm); cursor: pointer; padding-right: 2px; }
.sortbox select option { background: var(--surface); color: var(--ink); }
.tb-right { display: flex; align-items: center; gap: var(--s-2); }
.covbtn { display: inline-flex; align-items: center; gap: 6px; padding: var(--s-2) var(--s-3); border-radius: var(--r-md); font-size: var(--fs-sm); color: var(--ink-soft); border: 1px solid var(--line); transition: all var(--t-fast); }
.covbtn:hover:not(:disabled) { color: var(--azure-bright); border-color: var(--azure); }
.covbtn:disabled { opacity: .6; }
.covspin { width: 14px; height: 14px; border-radius: 50%; border: 2px solid var(--line-2); border-top-color: var(--azure); animation: spin .7s linear infinite; }

/* ── Grid ─────────────────────────────────────────────────────────────── */
.grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(14.0625rem, 1fr));
  gap: var(--s-6) var(--s-5);
}
.skeleton {
  aspect-ratio: 2 / 3; border-radius: var(--r-md);
  background: linear-gradient(100deg, var(--surface) 30%, var(--surface-2) 50%, var(--surface) 70%);
  background-size: 200% 100%;
  animation: shimmer 1.4s linear infinite;
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
  .hero__stats { gap: var(--s-5); }
}
</style>
