<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useAnimeStore } from '@/stores/anime'
import { ANIME_STATUS, STATUS_ORDER, nextUnwatchedEp, currentSeason, isCurrentSeason, SEASON_ES } from '@/lib/anime'
import { imgProxy } from '@/lib/img'
import AnimeCard from '@/components/anime/AnimeCard.vue'
import ContinueRail from '@/components/media/ContinueRail.vue'
import HeroBanner from '@/components/anime/HeroBanner.vue'
import Icon from '@/components/ui/Icon.vue'
import EmptyState from '@/components/ui/EmptyState.vue'
import Skeleton from '@/components/ui/Skeleton.vue'
import ContextMenu from '@/components/ui/ContextMenu.vue'
import ContentToolbar from '@/components/ui/ContentToolbar.vue'

const store = useAnimeStore()

// Aura del hero: color dominante del banner activo, teñido sutilmente detrás del home.
const heroTint = ref('rgb(77, 141, 255)')

// Background sync (15s) — only refreshes while qBittorrent has active downloads,
// matching the original app's _qbtSyncTimer behaviour.
let sync = null
const reloads = []
onMounted(() => {
  sync = setInterval(() => { if (store.hasActiveQbt()) store.loadLibrary(true) }, 15000)
  if (!store.seasonal.length) store.loadSeasonal()
  store.loadAiring()   // fresh airing schedule → "new episode just aired" hero
  // Hero banners/genres are backfilled server-side after the first library load;
  // refresh silently a couple of times so HD art appears without a manual reload.
  reloads.push(setTimeout(() => store.loadLibrary(true), 7000))
  reloads.push(setTimeout(() => store.loadLibrary(true), 20000))
})
onUnmounted(() => { if (sync) clearInterval(sync); reloads.forEach(clearTimeout) })

// Traduce "seguir viendo" de anime a la forma genérica del riel compartido. La miniatura del
// episodio solo existe si está descargado; si no, se cae a la portada.
const cwItems = computed(() => store.continueWatching.map(cw => ({
  id: cw.anime.id,
  raw: cw,
  thumb: (cw.ep.in_local || (cw.ep.in_qbt && cw.ep.progress >= 100))
    ? `/api/anime/thumb/${cw.anime.id}/${cw.ep.num}`
    : (cw.anime.cover ? imgProxy(cw.anime.cover, 340) : ''),
  title: cw.anime.title,
  subtitle: `Episodio ${cw.ep.num}`,
  badge: `EP ${cw.ep.num}`,
  progress: (cw.ep.resume_pos > 0 && cw.ep.duration) ? (cw.ep.resume_pos / cw.ep.duration) * 100 : 0,
})))

const SORTS = [
  { id: 'last_added', label: 'Recientes' },
  { id: 'last_watched', label: 'Vistos' },
  { id: 'title', label: 'A–Z' },
  { id: 'progress', label: 'Progreso' },
]

// "Todos" muestra solo contenido ACTIVO: las series completadas o abandonadas
// solo aparecen en su propia pestaña (viven ahí, no ensucian la lista principal).
const INACTIVE = ['completed', 'dropped']

// Temporada actual (recalculada por render → rueda sola cada trimestre).
const season = computed(() => currentSeason())
const seasonLabel = computed(() => `${SEASON_ES[season.value.season]} ${season.value.year}`)

const filtered = computed(() => {
  let list = [...store.library]
  if (store.libFilter === 'season') list = list.filter(a => isCurrentSeason(a, season.value))
  else if (store.libFilter !== 'all') list = list.filter(a => a.status === store.libFilter)
  else list = list.filter(a => !INACTIVE.includes(a.status))
  const q = store.libSearch.trim().toLowerCase()
  if (q) list = list.filter(a => (a.title || '').toLowerCase().includes(q))
  const s = store.libSort
  list.sort((a, b) => {
    if (s === 'title') return (a.title || '').localeCompare(b.title || '')
    if (s === 'last_watched') return (b.last_watched_at || 0) - (a.last_watched_at || 0)
    if (s === 'progress') {
      const pa = (a.downloaded_count || 0) / (a.total_episodes || 1)
      const pb = (b.downloaded_count || 0) / (b.total_episodes || 1)
      return pb - pa
    }
    return (b.added_at || 0) - (a.added_at || 0)
  })
  return list
})

const counts = computed(() => {
  // El contador de "Todo" refleja lo que realmente muestra: solo activos.
  const c = { all: store.library.filter(a => !INACTIVE.includes(a.status)).length }
  for (const k of STATUS_ORDER) c[k] = store.library.filter(a => a.status === k).length
  c.season = store.library.filter(a => isCurrentSeason(a, season.value)).length
  return c
})

// Filtros para la barra compartida: "Todo" + la temporada en curso + un pill por estado.
const libFilters = computed(() => [
  { id: 'all', label: 'Todo', n: counts.value.all },
  { id: 'season', label: 'Temporada', n: counts.value.season, icon: 'spark', title: seasonLabel.value },
  ...STATUS_ORDER.map(k => ({ id: k, label: ANIME_STATUS[k].label, n: counts.value[k], color: ANIME_STATUS[k].color })),
])

// Si la temporada rota (o se vacía) mientras el filtro está activo, vuelve a "Todo".
watch(() => counts.value.season, (n) => { if (store.libFilter === 'season' && !n) store.libFilter = 'all' })

// "Ver" desde la tarjeta hover: reproduce el próximo episodio no visto, o abre el detalle.
function playFromCard(a) {
  const ep = nextUnwatchedEp(a)
  if (ep) store.play(a, ep); else store.openDetail(a)
}

// Menú contextual (clic derecho) sobre las tarjetas de anime.
const cm = ref({ open: false, x: 0, y: 0, items: [] })
// Menú del riel "Seguir viendo": ir a la serie sin bajar a buscarla en la biblioteca.
function openCwMenu(e, cw) {
  cm.value = {
    open: true, x: e.clientX, y: e.clientY,
    items: [
      { label: 'Ver serie', icon: 'film', action: () => store.openDetail(cw.anime) },
      { label: `Reproducir ep. ${cw.ep.num}`, icon: 'play', action: () => store.play(cw.anime, cw.ep) },
    ],
  }
}
function openMenu(e, a) {
  cm.value = {
    open: true, x: e.clientX, y: e.clientY,
    items: [
      { label: 'Abrir', icon: 'film', action: () => store.openDetail(a) },
      { label: 'Ver ahora', icon: 'play', action: () => playFromCard(a) },
      { label: 'Buscar torrents', icon: 'download', action: () => store.openTorrents(a) },
      { sep: true },
      { label: 'Borrar episodios', icon: 'trash', action: () => store.clearEpisodes(a) },
      { label: 'Quitar de biblioteca', icon: 'close', danger: true, action: () => store.removeFromLibrary(a.id) },
    ],
  }
}
</script>

<template>
  <div class="alib">
    <div class="alib__aura" :style="{ '--tint-c': heroTint }" />

    <header class="alib__head">
      <p class="eyebrow"><span class="tick" /> TU ANIME</p>
    </header>

    <div class="alib__hero">
      <HeroBanner bleed @tint="c => heroTint = c" />
    </div>

    <ContinueRail :items="cwItems" @play="({ raw }) => store.play(raw.anime, raw.ep)"
                  @menu="({ ev, item }) => openCwMenu(ev, item.raw)" />

    <ContentToolbar :filters="libFilters" :filter="store.libFilter" @update:filter="store.libFilter = $event"
                    :sorts="SORTS" :sort="store.libSort" @update:sort="store.setLibSort($event)"
                    :search="store.libSearch" @update:search="store.libSearch = $event"
                    search-placeholder="Buscar en tu anime…">
      <template #extra>
        <button class="iconbtn" @click="store.openScan()" title="Carpetas de anime local"
                aria-label="Carpetas de anime local"><Icon name="folder" :size="16" /></button>
      </template>
    </ContentToolbar>

    <div v-if="store.loading" class="grid">
      <Skeleton v-for="n in 10" :key="n" variant="poster" />
    </div>
    <EmptyState v-else-if="!filtered.length" icon="film"
                :title="store.library.length ? 'Sin resultados.' : 'Aún no has añadido anime.'"
                :hint="store.library.length ? '' : 'Busca una serie y añádela para seguir sus episodios aquí.'">
      <template #action>
        <button v-if="store.library.length" class="is-primary" @click="store.libSearch = ''; store.libFilter = 'all'">
          <Icon name="close" :size="15" /> Quitar filtros
        </button>
        <template v-else>
          <button class="is-primary" @click="store.sub = 'search'"><Icon name="search" :size="15" /> Buscar anime</button>
          <button @click="store.sub = 'seasonal'"><Icon name="spark" :size="15" /> Ver la temporada</button>
        </template>
      </template>
    </EmptyState>
    <TransitionGroup v-else name="grid" tag="div" class="grid">
      <AnimeCard v-for="a in filtered" :key="a.id" :anime="a" @open="store.openDetail($event)" @play="playFromCard"
                 @contextmenu.prevent="openMenu($event, a)" />
    </TransitionGroup>
    <ContextMenu v-model:open="cm.open" :x="cm.x" :y="cm.y" :items="cm.items" />
  </div>
</template>

<style scoped>
/* Ancho completo (de borde a borde del área de contenido): antes un max-width centrado
   dejaba los extremos vacíos. `--alib-pad` es el único margen lateral del grid/toolbar y lo
   reutiliza el hero (en negativo) para sangrar a los bordes sin descuadrarse. */
.alib { position: relative; --alib-pad: var(--s-6); padding: 0 var(--alib-pad); }
/* El contenido va por encima del aura */
.alib > * { position: relative; z-index: 1; }

/* Encabezado eyebrow (mismo estilo que las demás vistas de anime: Buscar, Explorar…). */
.alib__head { padding: var(--s-2) 0 var(--s-4); }

/* Hero a sangre: rompe el padding lateral del contenedor y ocupa todo el ancho, borde a borde.
   Margen inferior corto: el borde de la imagen ya se disuelve en el fondo, así que "Seguir viendo"
   sube y queda cerca sin dejar un hueco muerto. */
.alib__hero { margin: 0 calc(-1 * var(--alib-pad)) var(--s-1); }

/* Aura del hero — halo del color dominante del banner activo, detrás de todo el home.
   @property permite que el color tween suavemente al rotar el carrusel (si no hay soporte,
   cambia al instante). Muy sutil para no competir con la lectura. */
.alib__aura {
  position: absolute; z-index: 0; top: 0; left: 50%; transform: translateX(-50%);
  width: 100%; height: 34rem; pointer-events: none;
  background: radial-gradient(75% 60% at 50% 0%, color-mix(in srgb, var(--tint-c) 15%, transparent), transparent 72%);
  transition: --tint-c var(--t-cine) var(--ease-silk);
}

/* Botón-icono Carpetas: pequeño, junto a los ordenadores (Recientes/Vistos/A–Z), mismo lenguaje
   visual que la caja de búsqueda para que se localice sin romper la estética. */
.iconbtn { display: grid; place-items: center; width: 2.25rem; height: 2.25rem; flex-shrink: 0;
  border-radius: var(--r-md); color: var(--ink-soft); background: var(--surface); border: 1px solid var(--line);
  transition: all var(--t-fast); }
.iconbtn:hover { color: var(--azure-bright); border-color: var(--azure); }

.grid { position: relative; display: grid; grid-template-columns: repeat(auto-fill, minmax(14.0625rem, 1fr)); gap: var(--s-6) var(--s-5); }
.empty { display: flex; flex-direction: column; align-items: center; gap: var(--s-3); padding: var(--s-9) 0; color: var(--ink-faint); }

@media (max-width: 540px) {
  .alib { --alib-pad: var(--s-4); }
  .grid { grid-template-columns: repeat(auto-fill, minmax(10rem, 1fr)); gap: var(--s-5) var(--s-3); }
}
</style>
