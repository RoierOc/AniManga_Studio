<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useAnimeStore } from '@/stores/anime'
import { ANIME_STATUS, STATUS_ORDER, nextUnwatchedEp, currentSeason, isCurrentSeason, shiftSeason, SEASON_ES } from '@/lib/anime'
import { imgProxy } from '@/lib/img'
import { ultimaTarjeta } from '@/lib/vt'
import AnimeCard from '@/components/anime/AnimeCard.vue'
import AnimeRail from '@/components/anime/AnimeRail.vue'
import ContinueRail, { RAIL_W } from '@/components/media/ContinueRail.vue'
import HeroBanner from '@/components/anime/HeroBanner.vue'
import Icon from '@/components/ui/Icon.vue'
import EmptyState from '@/components/ui/EmptyState.vue'
import Skeleton from '@/components/ui/Skeleton.vue'
import ErrorState from '@/components/ui/ErrorState.vue'
import ContextMenu from '@/components/ui/ContextMenu.vue'
import ContentToolbar from '@/components/ui/ContentToolbar.vue'
import Select from '@/components/ui/Select.vue'
import { useTagsStore } from '@/stores/tags'
import { opcionesGenero, generoActivo, conGenero } from '@/lib/generos'
import DensityToggle from '@/components/ui/DensityToggle.vue'
import { useGridKeyboard } from '@/lib/useGridKeyboard'

const store = useAnimeStore()
const tags = useTagsStore()
tags.load()

// Aura del hero: color dominante del banner activo, teñido sutilmente detrás del home.
const heroTint = ref('rgb(77, 141, 255)')

// Background sync (15s) — only refreshes while qBittorrent has active downloads,
// matching the original app's _qbtSyncTimer behaviour.
let sync = null
const reloads = []
onMounted(() => {
  sync = setInterval(() => { if (store.hasActiveQbt()) store.loadLibrary(true, true) }, 15000)
  if (!store.seasonal.length) store.loadSeasonal()
  store.loadForYou()   // recomendaciones sobre tu biblioteca (cacheadas 24 h en el backend)
  store.loadAiring()   // fresh airing schedule → "new episode just aired" hero
  // Hero banners/genres are backfilled server-side after the first library load;
  // refresh silently a couple of times so HD art appears without a manual reload.
  reloads.push(setTimeout(() => store.loadLibrary(true, true), 7000))
  reloads.push(setTimeout(() => store.loadLibrary(true, true), 20000))
})
onUnmounted(() => { if (sync) clearInterval(sync); reloads.forEach(clearTimeout) })

// Traduce "seguir viendo" de anime a la forma genérica del riel compartido. La miniatura del
// episodio solo existe si está descargado; si no, se cae a la portada.
const cwItems = computed(() => store.continueWatching.map(cw => ({
  id: cw.anime.id,
  raw: cw,
  thumb: (cw.ep.in_local || (cw.ep.in_qbt && cw.ep.progress >= 100))
    ? `/api/anime/thumb/${cw.anime.id}/${cw.ep.num}`
    : (cw.anime.cover ? imgProxy(cw.anime.cover, RAIL_W) : ''),
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

// Temporada actual (recalculada por render → rueda sola cada trimestre) desplazada por el paso
// que hayas dado con las flechas. El desplazamiento es LOCAL a la vista y no se persiste: es una
// consulta ("¿qué vi el otoño pasado?"), no una preferencia — al salir del filtro vuelve a hoy.
const seasonStep = ref(0)
const season = computed(() => shiftSeason(currentSeason(), seasonStep.value))
const seasonLabel = computed(() => `${SEASON_ES[season.value.season]} ${season.value.year}`)

/* AnimeRail espera `{ anime, ep? }`; el endpoint devuelve series planas de AniList. `episodes: []`
   es necesario: la tarjeta lee esa lista y sin ella revienta al pintar el progreso. */
const forYouItems = computed(() => store.forYou.map(a => ({
  anime: { ...a, id: a.al_id, episodes: [], total_episodes: a.episodes || 0, last_watched_at: 0 },
})))

/* Géneros de TU anime. `gen_tags` son los específicos (Yuri, Isekai, Escolar…) que el backend
   guarda aparte para no cambiar lo que pintan las tarjetas; para FILTRAR son una lista sola. */
const conTags = (a) => [...(a.genres || []), ...(a.gen_tags || [])]

const filtered = computed(() => {
  let list = [...store.library]
  if (generoActivo(store.libFilter)) {
    // Igual que las etiquetas: buscar «algo de acción» no puede esconderte lo ya terminado.
    list = conGenero(list, generoActivo(store.libFilter), conTags)
  }
  else if (store.libFilter.startsWith('tag:')) {
    const t = store.libFilter.slice(4)
    list = list.filter(a => tags.forWork('anime', a.id).includes(t))
  }
  else if (store.libFilter === 'season') list = list.filter(a => isCurrentSeason(a, season.value))
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

const { selected: gridSelected, count: gridSelectionCount, clear: clearGridSelection, onKey: onGridKey } =
  useGridKeyboard(() => filtered.value.map(a => a.id))
const selectedAnime = computed(() => filtered.value.filter(a => gridSelected.has(String(a.id))))

async function clearSelectedAnime() {
  const accepted = await store.clearEpisodesBatch(selectedAnime.value)
  if (accepted) clearGridSelection()
}

// Lo que responde la pregunta de verdad: de esa temporada, qué terminaste y qué quedó a medias.
// Se calcula sobre la lista ya filtrada, así que respeta también la búsqueda.
const seasonSummary = computed(() => {
  if (store.libFilter !== 'season') return ''
  const n = (...st) => filtered.value.filter(a => st.includes(a.status)).length
  const partes = [
    n('completed') && `${n('completed')} vistas`,
    n('watching', 'on_hold') && `${n('watching', 'on_hold')} a medias`,
    n('plan_to_watch') && `${n('plan_to_watch')} pendientes`,
  ].filter(Boolean)
  return partes.join(' · ')
})

const seasonEmpty = computed(() => store.libFilter === 'season' && !store.libSearch.trim())

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

const generos = computed(() => opcionesGenero(store.library, conTags))

// Las etiquetas en UN desplegable, no en pills: no tienen tope y la barra crecía sin control.
// Mismo criterio y misma forma que en la biblioteca de manga (ver LibraryView).
const tagOptions = computed(() => [
  { value: '', label: 'Todas las etiquetas' },
  ...tags.universe('anime').map(t => ({
    value: t, label: t,
    hint: String(store.library.filter(a => tags.forWork('anime', a.id).includes(t)).length),
  })),
])
const tagFilter = computed({
  get: () => (store.libFilter.startsWith('tag:') ? store.libFilter.slice(4) : ''),
  set: (v) => { store.libFilter = v ? `tag:${v}` : 'all' },
})

// Si la temporada rota (o se vacía) mientras el filtro está activo, vuelve a "Todo".
// Sólo en la temporada EN CURSO: una temporada pasada vacía es una respuesta legítima
// («no viste nada ese trimestre»), y expulsar del filtro haría imposible atravesarla.
watch(() => counts.value.season, (n) => {
  if (store.libFilter === 'season' && !n && !seasonStep.value) store.libFilter = 'all'
})
// Salir del filtro vuelve a hoy: si no, el pill reaparecería en una temporada de hace dos años.
watch(() => store.libFilter, (f) => { if (f !== 'season') seasonStep.value = 0 })

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
      { label: 'Etiquetas…', icon: 'spark', action: () => tags.openPicker('anime', a.id, a.title) },
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
                    :genres="generos"
                    :sorts="SORTS" :sort="store.libSort" @update:sort="store.setLibSort($event)"
                    :search="store.libSearch" @update:search="store.libSearch = $event"
                    search-placeholder="Buscar en tu anime…">
      <template #extra>
        <!-- Sólo existe mientras miras una temporada: fuera de ese filtro no significa nada. -->
        <div v-if="store.libFilter === 'season'" class="snav">
          <button class="snav__arrow" @click="seasonStep--"
                  data-tip="Temporada anterior" aria-label="Temporada anterior">
            <Icon name="chevron" :size="15" class="snav__prev" />
          </button>
          <span class="snav__lbl">{{ seasonLabel }}</span>
          <button class="snav__arrow" @click="seasonStep++"
                  data-tip="Temporada siguiente" aria-label="Temporada siguiente">
            <Icon name="chevron" :size="15" />
          </button>
          <button v-if="seasonStep" class="snav__now" @click="seasonStep = 0">Hoy</button>
        </div>
        <DensityToggle />
        <!-- Sin etiquetas puestas no aparece: quien no las usa no ve un control de más. -->
        <Select v-if="tagOptions.length > 1" v-model="tagFilter" icon="spark"
                aria-label="Filtrar por etiqueta" :options="tagOptions" />
        <button class="iconbtn" @click="store.openScan()" data-tip="Carpetas de anime local"
                aria-label="Carpetas de anime local"><Icon name="folder" :size="16" /></button>
      </template>
    </ContentToolbar>

    <p v-if="seasonSummary" class="snav__sum">{{ seasonSummary }}</p>

    <div v-if="store.loading" class="grid">
      <Skeleton v-for="n in 10" :key="n" variant="poster" />
    </div>
    <!-- El fallo va ANTES del vacío: si la carga reventó, no podemos afirmar que no hay nada. -->
    <ErrorState v-else-if="store.loadError && !store.library.length"
                title="No se pudo cargar tu anime." :detail="store.loadError"
                @retry="store.loadLibrary()" />

    <!-- Una temporada pasada sin nada no es «sin resultados»: es la respuesta a la pregunta que
         hiciste, y decirlo con su nombre evita que parezca que el filtro se rompió. -->
    <EmptyState v-else-if="!filtered.length" icon="film"
                :title="seasonEmpty ? `No tienes nada de ${seasonLabel}.`
                        : store.library.length ? 'Sin resultados.' : 'Aún no has añadido anime.'"
                :hint="seasonEmpty ? 'Usa las flechas para ver otra temporada.'
                       : store.library.length ? '' : 'Busca una serie y añádela para seguir sus episodios aquí.'">
      <template #action>
        <button v-if="store.library.length" class="is-primary"
                @click="store.libSearch = ''; store.libFilter = 'all'">
          <Icon name="close" :size="15" /> Quitar filtros
        </button>
        <template v-else>
          <button class="is-primary" @click="store.sub = 'search'"><Icon name="search" :size="15" /> Buscar anime</button>
          <button @click="store.sub = 'seasonal'"><Icon name="spark" :size="15" /> Ver la temporada</button>
        </template>
      </template>
    </EmptyState>
    <p v-if="gridSelectionCount && !store.loading && !(store.loadError && !store.library.length) && filtered.length"
       class="grid__selection" aria-live="polite">
      <span>{{ gridSelectionCount }} marcada(s)</span>
      <button type="button" class="grid__selection-action" @click="clearSelectedAnime">
        Liberar espacio
      </button>
      <button type="button" data-tip="Quitar marcas" @click="clearGridSelection">Limpiar</button>
    </p>

    <TransitionGroup v-if="!store.loading && !(store.loadError && !store.library.length) && filtered.length"
                     name="grid" tag="div" class="grid" role="grid"
                     aria-label="Biblioteca de anime" aria-multiselectable="true"
                     @keydown="onGridKey">
      <AnimeCard v-for="a in filtered" :key="a.id" :anime="a" @open="store.openDetail($event)" @play="playFromCard"
                 data-grid-item :data-grid-key="a.id" role="gridcell"
                 :aria-selected="gridSelected.has(String(a.id))"
                 :class="{ 'is-key-selected': gridSelected.has(String(a.id)), 'is-returned': String(a.id) === ultimaTarjeta }"
                 @contextmenu.prevent="openMenu($event, a)" />
    </TransitionGroup>

    <!-- «Para ti» va AL FINAL, debajo de la rejilla: es descubrimiento, no biblioteca. Ponerlo
         entre «Seguir viendo» y los filtros metía cosas que no tienes en medio de las que sí. -->
    <AnimeRail v-if="forYouItems.length" class="alib__foryou" title="Para ti" :items="forYouItems"
               @select="store.openPreview($event.anime)" />
    <ContextMenu v-model:open="cm.open" :x="cm.x" :y="cm.y" :items="cm.items" />
  </div>
</template>

<style scoped>
/* Navegador de temporada. Va en la barra, pegado a los demás controles, y no en un bloque
   propio: es un ajuste del filtro activo, no una sección. */
.snav { display: flex; align-items: center; gap: var(--s-1); padding: 0 var(--s-1);
  border: 1px solid var(--line); border-radius: var(--r-md); background: var(--surface-2); }
.snav__arrow { display: grid; place-items: center; padding: var(--s-2) var(--s-1);
  color: var(--ink-faint); transition: color var(--t-fast); }
.snav__arrow:hover { color: var(--ink); }
.snav__prev { transform: rotate(180deg); }
/* Ancho mínimo: sin él la barra entera se movía de sitio al pasar de «Otoño 2025» a «Verano 2026»,
   y las flechas huían del cursor cuando encadenas varios saltos. */
.snav__lbl { min-width: 8.5rem; text-align: center; font-size: var(--fs-xs); font-weight: 600;
  color: var(--ink); white-space: nowrap; }
.snav__now { font-size: var(--fs-2xs); font-weight: 700; color: var(--azure-bright);
  padding: 0 var(--s-2); }
.snav__now:hover { color: #fff; }
.snav__sum { margin: calc(var(--s-3) * -1) var(--alib-pad) var(--s-4);
  font-size: var(--fs-xs); color: var(--ink-faint); }

/* Ancho completo (de borde a borde del área de contenido): antes un max-width centrado
   dejaba los extremos vacíos. `--alib-pad` es el único margen lateral del grid/toolbar y lo
   reutiliza el hero (en negativo) para sangrar a los bordes sin descuadrarse. */
/* Al volver a la rejilla, un pulso en la tarjeta de la que saliste. Dura poco y se va: es un
   guiño para reencontrar el sitio, no un estado «seleccionado». `forwards` NO: si se quedara
   fijo, la marca competiría con el hover y con la tarjeta que abras después. */
.is-returned { animation: vuelta 1.6s var(--ease-silk) 1; border-radius: var(--r-md); }
@keyframes vuelta {
  0%   { box-shadow: 0 0 0 0 var(--azure-glow); }
  25%  { box-shadow: 0 0 0 3px var(--azure-glow), 0 8px 28px var(--azure-glow); }
  100% { box-shadow: 0 0 0 0 rgba(0,0,0,0); }
}
@media (prefers-reduced-motion: reduce) { .is-returned { animation: none; } }

/* Separación de la rejilla: cerrada la biblioteca, empieza el descubrimiento. */
.alib__foryou { margin-top: var(--s-8); padding-top: var(--s-6); border-top: 1px solid var(--line); }

.alib { position: relative; --alib-pad: var(--s-6); padding: 0 var(--alib-pad); }
.grid__selection { display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: var(--s-3); margin: calc(var(--s-4) * -1) 0 var(--s-4); padding: var(--s-2) var(--s-3); border: 1px solid color-mix(in srgb, var(--azure) 34%, transparent); border-radius: var(--r-md); background: var(--azure-haze); color: var(--azure-bright); font-size: var(--fs-xs); }
.grid__selection-action { margin-left: auto; padding: var(--s-1) var(--s-2); border: 1px solid var(--amber); border-radius: var(--r-sm); color: var(--amber); font-size: var(--fs-xs); }
.grid__selection-action:hover { background: color-mix(in srgb, var(--amber) 14%, transparent); }
.grid__selection button { color: var(--ink-soft); font-size: var(--fs-xs); text-decoration: underline; text-underline-offset: 2px; }
.grid__selection button:hover { color: var(--ink); }
.grid__selection .grid__selection-action { color: var(--amber); text-decoration: none; }
.grid :deep(.mcard.is-key-selected .mcard__poster) { border-color: var(--azure); box-shadow: 0 0 0 2px var(--azure), 0 0 24px -8px var(--azure-glow); }
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
  /* El aura es un degradado radial anclado ARRIBA, así que su primera fila es la más intensa y
     el borde superior del contenedor la corta en seco: medido, +20 de luminancia en una fila —
     una línea horizontal justo donde empieza el hero, más marcada en el lado donde el radial está
     centrado. Se le da su propia rampa vertical para que entre desde cero. */
  -webkit-mask-image: linear-gradient(to bottom, transparent 0%, #000 16%);
          mask-image: linear-gradient(to bottom, transparent 0%, #000 16%);
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

/* Ancho base propio; el resto de la rejilla (densidad, hueco, móvil) vive en base.css */
.grid { --card-min: 14.0625rem; }
.empty { display: flex; flex-direction: column; align-items: center; gap: var(--s-3); padding: var(--s-9) 0; color: var(--ink-faint); }

@media (max-width: 540px) {
  .alib { --alib-pad: var(--s-4); }
}
</style>
