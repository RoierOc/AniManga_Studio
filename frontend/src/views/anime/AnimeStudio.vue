<script setup>
import { computed, onMounted, onUnmounted, watch } from 'vue'
import { useUiStore } from '@/stores/ui'
import { useAnimeStore } from '@/stores/anime'
import { supportsVT, vtGo } from '@/lib/vt'
import AnimeLibrary from './AnimeLibrary.vue'
import AnimeDetail from './AnimeDetail.vue'
import Downloads from './Downloads.vue'
import History from './History.vue'
import Seasonal from './Seasonal.vue'
import Schedule from './Schedule.vue'
import Search from './Search.vue'
import AnimeExplore from './AnimeExplore.vue'
import AutoplayModal from '@/components/anime/AutoplayModal.vue'
import SubTrackModal from '@/components/anime/SubTrackModal.vue'
import ScanPathsModal from '@/components/anime/ScanPathsModal.vue'
import PlaceholderView from '@/views/PlaceholderView.vue'
import Icon from '@/components/ui/Icon.vue'

const store = useAnimeStore()
const backfillTimers = []
function scheduleBackfillRefresh() {
  backfillTimers.splice(0).forEach(clearTimeout)
  if (!store.needsAnimeMetadataBackfill()) return
  for (const delay of [7000, 20000]) {
    backfillTimers.push(setTimeout(() => {
      if (store.needsAnimeMetadataBackfill()) store.loadLibrary(true, true)
    }, delay))
  }
}
let stopLibraryWatch = null

const TABS = [
  { id: 'library',   label: 'Mi Anime',  icon: 'film' },
  { id: 'search',    label: 'Buscar',    icon: 'search' },
  { id: 'explore',   label: 'Explorar',  icon: 'globe' },
  { id: 'seasonal',  label: 'Temporada', icon: 'sun' },
  { id: 'schedule',  label: 'Estrenos',  icon: 'clock' },
  { id: 'downloads', label: 'Descargas', icon: 'download' },
  { id: 'history',   label: 'Historial', icon: 'heart' },
]

onMounted(() => {
  store.init()
  if (!store.library.length) store.loadLibrary()
  else if (store.needsAnimeMetadataBackfill()) store.loadLibrary(true, true)
  // Vive en el shell del módulo, no en la rejilla: el backfill también debe terminar si la ficha
  // o cualquier otra subvista está abierta cuando se añade una serie.
  stopLibraryWatch = watch(() => store.library.length, scheduleBackfillRefresh)
  scheduleBackfillRefresh()
})
onUnmounted(() => {
  stopLibraryWatch?.()
  backfillTimers.splice(0).forEach(clearTimeout)
})

function selectTab(id) {
  vtGo(() => {
    store._resetDetail()
    store.closeTorrents()
    store.sub = id
    store.persist()
    useUiStore().pushNav()
  })
}

// Con View Transitions el swap lo anima el navegador (crossfade + morph del
// póster); la <Transition out-in> de Vue retrasaría el montaje del DOM nuevo
// y la captura del "después" saldría vacía. Sin soporte, swap Vue como antes.
const VIEW_MAP = { library: AnimeLibrary, search: Search, explore: AnimeExplore, seasonal: Seasonal,
                   schedule: Schedule, downloads: Downloads, history: History }
const current = computed(() => {
  if (store.detail) return { c: AnimeDetail, key: 'detail', props: {} }
  const c = VIEW_MAP[store.sub]
  if (c) return { c, key: store.sub, props: {} }
  const t = TABS.find(x => x.id === store.sub)
  return { c: PlaceholderView, key: store.sub, props: { label: t?.label, icon: t?.icon } }
})
</script>

<template>
  <div class="studio">
    <!-- Sub-nav (hidden while a detail is open) -->
    <nav v-if="!store.detail" class="subnav">
      <button v-for="t in TABS" :key="t.id" class="subnav__tab" :class="{ 'is-active': store.sub === t.id }"
              @click="selectTab(t.id)">
        <Icon :name="t.icon" :size="16" /> {{ t.label }}
      </button>
    </nav>

    <div class="studio__body">
      <component v-if="supportsVT" :is="current.c" :key="current.key" v-bind="current.props" />
      <Transition v-else name="swap" mode="out-in">
        <component :is="current.c" :key="current.key" v-bind="current.props" />
      </Transition>
    </div>

    <AutoplayModal />
    <SubTrackModal />
    <ScanPathsModal />
  </div>
</template>

<style scoped>
.studio { width: 100%; --domain-accent: var(--cyan);
  --domain-accent-soft: color-mix(in srgb, var(--cyan) 12%, transparent); }
/* Barra de sub-navegación a ancho COMPLETO con separador inferior: se lee como una barra
   intencional tanto sobre el hero a sangre (Mi Anime) como sobre las vistas centradas (Buscar,
   Explorar…). Alineada a la izquierda con el mismo padding que el contenido → las pestañas cuadran
   con el hero y las tarjetas en cualquier ancho de pantalla. */
.studio__body { padding-bottom: var(--s-8); }

/* the detail/library/placeholder swap */
.swap-enter-active { transition: all var(--t-slow) var(--ease-silk); }
.swap-leave-active { transition: all var(--t-fast) var(--ease-silk); }
.swap-enter-from { opacity: 0; transform: translateY(10px); }
.swap-leave-to { opacity: 0; transform: translateY(-6px); }
</style>
