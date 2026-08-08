<script setup>
import { mediaStatusLabel } from '@/lib/format'
/* Home de Series y Películas — espejo de `views/anime/AnimeLibrary.vue`:
   aura + hero a sangre + riel "Seguir viendo" + toolbar + rejilla de tarjetas. */
import { computed, onMounted, ref } from 'vue'
import { useMediaStore } from '@/stores/media'
import { useUiStore } from '@/stores/ui'
import { imgProxy } from '@/lib/img'
import MediaCard from '@/components/media/MediaCard.vue'
import MediaHero from '@/components/media/MediaHero.vue'
import ContinueRail, { RAIL_W } from '@/components/media/ContinueRail.vue'
import ContextMenu from '@/components/ui/ContextMenu.vue'
import Icon from '@/components/ui/Icon.vue'
import EmptyState from '@/components/ui/EmptyState.vue'
import Skeleton from '@/components/ui/Skeleton.vue'
import ErrorState from '@/components/ui/ErrorState.vue'
import ContentToolbar from '@/components/ui/ContentToolbar.vue'
import DensityToggle from '@/components/ui/DensityToggle.vue'
import Select from '@/components/ui/Select.vue'
import Spinner from '@/components/ui/Spinner.vue'
import { useTagsStore } from '@/stores/tags'

const store = useMediaStore()
const ui = useUiStore()
const tags = useTagsStore()
const heroTint = ref('rgb(77, 141, 255)')
const menu = ref(null)

onMounted(() => {
  store.init()
  // Fuera del camino crítico: son ~1,5 s de TMDB en frío y la rejilla no los espera.
  if (!store.forYouLoaded) store.loadForYou()
})

/* Dos familias de filtro, y el orden lo dice: primero QUÉ es (series/películas), después DÓNDE
   estás tú con ello (viendo/vistas/sin empezar) y al final el estado de los archivos.
   Los tres del medio no son un estado que tengas que marcar a mano — Sonarr no lo tiene y pedirte
   que lo mantengas sería peaje: salen del progreso que ya guardamos al reproducir. */
const FILTERS = [
  { id: 'all', label: 'Todo' },
  { id: 'series', label: 'Series' },
  { id: 'movies', label: 'Películas' },
  { id: 'watching', label: 'Viendo', color: 'var(--jade)' },
  { id: 'unseen', label: 'Sin empezar', color: 'var(--violet)' },
  { id: 'seen', label: 'Vistas', color: 'var(--azure-bright)' },
  { id: 'missing', label: 'Incompletas' },
]
const SORTS = [
  { id: 'recent', label: 'Recientes' },
  { id: 'title', label: 'A–Z' },
  { id: 'progress', label: 'Progreso' },
]

// El hero necesita acciones, y esas ya son de dominio: se montan aquí, no dentro del componente.
const heroItems = computed(() => store.heroItems.map(h => ({
  ...h,
  actions: [
    h.raw.kind === 'movie'
      ? { label: 'Ver película', icon: 'play', primary: true, run: () => store.playMovie(h.raw) }
      // Si el hero dice «sigue viendo», el botón tiene que REANUDAR, no abrir la ficha para que
      // busques tú el episodio. Igual que en Mi Anime.
      : h.cont
        ? { label: `Continuar · T${h.cont.season}E${String(h.cont.num).padStart(2, '0')}`,
            icon: 'play', primary: true, run: () => store.playContinue(h.cont) }
        : { label: 'Ver episodios', icon: 'play', primary: true, run: () => store.openDetail(h.raw) },
    { label: 'Información', icon: 'spark', run: () => store.openDetail(h.raw) },
  ],
  // Mismo gesto que en Inicio y en Mi Anime: el título abre la ficha. Sin esto, Cine sería la
  // única de las tres portadas donde pinchar el logo no hace nada.
  titleAction: () => store.openDetail(h.raw),
})))

// El riel mezcla series y películas: una película no tiene temporada ni episodio, así que su
// subtítulo dice cuánto le queda, que es la información útil para decidir si la retomas ahora.
const rail = computed(() => store.continueItems.map(c => ({
  id: c.kind === 'movie' ? `mv:${c.movie_id}` : `${c.series_id}:${c.episode_id}`,
  raw: c,
  thumb: imgProxy(c.still, RAIL_W),
  title: c.title,
  subtitle: c.kind === 'movie'
    ? (c.duration ? `Te quedan ${Math.max(1, Math.round((c.duration - c.pos) / 60))} min` : 'Película')
    : `T${c.season} · Episodio ${c.num}${c.episode_title ? ` — ${c.episode_title}` : ''}`,
  badge: c.kind === 'movie' ? 'PELÍCULA' : `${c.season}x${String(c.num).padStart(2, '0')}`,
  progress: c.duration ? (c.pos / c.duration) * 100 : 0,
})))

// Etiquetas propias: mismo mecanismo que manga y anime, con la identidad de ESTE dominio
// (`<kind>:<id>`, porque una serie 5 y una película 5 son cosas distintas).
const tagId = (it) => `${it.kind}:${it.id}`
const tagOptions = computed(() => [
  { value: '', label: 'Todas las etiquetas' },
  ...tags.universe('media').map(t => ({
    value: t, label: t,
    hint: String(store.all.filter(x => tags.forWork('media', tagId(x)).includes(t)).length),
  })),
])
const tagFilter = computed({
  get: () => (store.filter.startsWith('tag:') ? store.filter.slice(4) : ''),
  set: (v) => { store.filter = v ? `tag:${v}` : 'all' },
})

function cardFor(it) {
  const est = store.watchState[tagId(it)]
  return {
    cover: it.poster,
    title: it.title,
    kindLabel: it.kind === 'movie' ? 'PELÍCULA' : 'SERIE',
    // La marca de «vista» va en la tarjeta y no sólo en el filtro: recorrer la rejilla buscando
    // qué te falta por ver era imposible sin abrir cada ficha.
    flag: est === 'seen' ? { tone: 'soft', icon: 'check', label: 'VISTA' }
        : est === 'watching' ? { tone: 'soft', icon: 'play', label: 'VIENDO' } : null,
    // Solo se marca lo que falta: una serie completa no necesita insignia.
    status: it.have < it.total ? { label: `Faltan ${it.total - it.have}`, color: 'var(--cyan)' } : null,
    // Sin `total` la tarjeta pintaba un «0» suelto bajo el título, que no dice nada (pasaba en
    // Rick and Morty y Arcane). Si no hay contra qué contar, no se cuenta.
    count: it.kind === 'series' && it.total ? { done: it.have, total: it.total } : null,
    tags: [it.year ? String(it.year) : '', mediaStatusLabel(it.status)].filter(Boolean),
  }
}

function openMenu(ev, it) {
  menu.value = {
    x: ev.clientX, y: ev.clientY,
    items: [
      { label: 'Abrir', icon: 'film', action: () => store.openDetail(it) },
      ...(it.kind === 'movie' ? [
        { label: 'Ver ahora', icon: 'play', action: () => store.playMovie(it) },
        { label: 'Subtítulos en español', icon: 'globe', action: () => store.movieSubs(it) },
      ] : []),
      { label: 'Elegir torrent', icon: 'download', action: () => store.openPicker(it) },
      { label: 'Etiquetas…', icon: 'spark', action: () => tags.openPicker('media', tagId(it), it.title) },
      { sep: true },
      { label: 'Quitar de la biblioteca', icon: 'close', action: () => remove(it, false) },
      { label: 'Eliminar CON los archivos', icon: 'trash', danger: true, action: () => remove(it, true) },
    ],
  }
}

/* Diálogo sólo para lo IRREVERSIBLE. Quitar de la biblioteca conserva los archivos, así que no
   merece una interrupción: se hace y se ofrece deshacer. Borrar 40 GB del disco, sí. */
async function remove(it, withFiles) {
  if (withFiles) {
    const ok = await ui.confirm({
      title: 'Eliminar con archivos', danger: true, confirmLabel: 'Eliminar todo',
      body: `¿Eliminar «${it.title}» Y SUS ARCHIVOS del disco?\nEsto no se puede deshacer.`,
    })
    if (ok) store.removeFromLibrary(it, { deleteFiles: true })
    return
  }
  await store.removeFromLibrary(it, { deleteFiles: false })
  // Volver a darla de alta necesita el id EXTERNO (TVDB/TMDB); el `id` de la lista es el de
  // Sonarr y ya no existe. Sin `ext_id` no se ofrece un botón que no funcionaría.
  ui.toast(`«${it.title}» quitada de la biblioteca`, 'info', 7000,
    it.ext_id ? { label: 'Deshacer', fn: () => store.add(it.ext_id, it.kind) } : null)
}

function play(it) {
  if (it.kind === 'movie') store.playMovie(it)
  else store.openDetail(it)     // una serie no tiene "un" vídeo: se abre para elegir episodio
}
</script>

<template>
  <div class="mlib">
    <div class="mlib__aura" :style="{ '--tint-c': heroTint }" />

    <header class="mlib__head">
      <p class="eyebrow"><span class="tick" /> SERIES Y PELÍCULAS</p>
    </header>

    <!-- Un servicio caído se DICE explícitamente: si no, se lee como "no tienes nada". -->
    <p v-if="store.offline.length" class="mlib__warn">
      <Icon name="alert" :size="14" />
      {{ store.offline.join(' y ') }} no responde. Arráncalo con <code>servarr/start.sh</code>.
    </p>

    <div v-if="heroItems.length" class="mlib__hero">
      <MediaHero :items="heroItems" bleed @tint="c => heroTint = c" />
    </div>

    <ContinueRail :items="rail" @play="({ raw }) => store.playContinue(raw)" />

    <ContentToolbar :filters="FILTERS.map(f => ({ ...f, n: f.id === 'all' ? undefined : store.counts[f.id] }))"
                    :filter="store.filter" @update:filter="store.filter = $event"
                    :genres="store.generos"
                    :sorts="SORTS" :sort="store.sort" @update:sort="store.sort = $event"
                    :search="store.search" @update:search="store.search = $event"
                    search-placeholder="Buscar en tu biblioteca…">
      <template #extra>
        <DensityToggle />
        <!-- Sin etiquetas puestas no aparece: quien no las usa no ve un control de más. -->
        <Select v-if="tagOptions.length > 1" v-model="tagFilter" icon="spark"
                aria-label="Filtrar por etiqueta" :options="tagOptions" />
      </template>
    </ContentToolbar>

    <!-- Mismo esqueleto que Manga y Anime: la rejilla ya tiene forma antes de llegar los datos,
         en vez del parpadeo en blanco con el que el contenido saltaba de golpe. -->
    <div v-if="store.loading && !store.items.length" class="grid mlib__grid">
      <Skeleton v-for="n in 12" :key="n" variant="poster" />
    </div>

    <!-- El fallo va ANTES del vacío: si la carga reventó, no podemos afirmar que no hay nada. -->
    <ErrorState v-else-if="store.loadError && !store.items.length"
                title="No se pudo cargar tu biblioteca." :detail="store.loadError"
                @retry="store.load()" />

    <EmptyState v-else-if="!store.items.length" icon="film"
                :title="store.search ? 'Nada coincide' : 'Tu biblioteca está vacía'"
                :hint="store.search ? 'Prueba con otro título.' : 'Ve a Buscar o Descubrir para añadir series y películas.'">
      <template #action>
        <button v-if="store.search" class="is-primary" @click="store.search = ''">
          <Icon name="close" :size="15" /> Limpiar la búsqueda
        </button>
        <template v-else>
          <button class="is-primary" @click="store.setSub('search')"><Icon name="search" :size="15" /> Buscar</button>
          <button @click="store.setSub('discover')"><Icon name="spark" :size="15" /> Descubrir</button>
        </template>
      </template>
    </EmptyState>

    <TransitionGroup v-else name="grid" tag="div" class="grid mlib__grid">
      <MediaCard v-for="it in store.items" :key="it.kind + it.id" v-bind="cardFor(it)"
                 :play-label="it.kind === 'movie' ? 'Ver' : 'Episodios'"
                 alt-label="Torrents" alt-icon="download"
                 @open="store.openDetail(it)" @play="play(it)"
                 @alt="store.openPicker(it)"
                 @contextmenu.prevent="openMenu($event, it)" />
    </TransitionGroup>

    <!-- «Para ti» va AL FINAL, debajo de la rejilla: es descubrimiento, no biblioteca. Mismo sitio
         y mismo criterio que en Mi Anime, para que las dos secciones se lean igual. -->
    <section v-if="store.forYou.length || store.forYouLoading" class="fy">
      <header class="fy__head">
        <h3 class="fy__title">Para ti</h3>
        <span class="fy__hint">a partir de lo que ya tienes</span>
      </header>
      <Spinner v-if="store.forYouLoading && !store.forYou.length" :size="18" />
      <div v-else class="fy__row">
        <MediaCard v-for="d in store.forYou" :key="d.kind + d.tmdb_id"
                   :cover="d.poster" :title="d.title"
                   :kind-label="d.kind === 'movie' ? 'PELÍCULA' : 'SERIE'"
                   :status="d.score ? { label: `★ ${d.score}`, color: 'var(--cyan)' } : null"
                   :flag="d.already ? { tone: 'soft', icon: 'check', label: 'EN TU BIBLIOTECA' } : null"
                   :tags="[d.year ? String(d.year) : ''].filter(Boolean)"
                   :play-label="d.already ? 'Ya la tienes' : (store.adding === d.tmdb_id ? 'Añadiendo…' : 'Añadir')"
                   :play-icon="d.already ? 'check' : (store.adding === d.tmdb_id ? 'refresh' : 'plus')"
                   :play-done="!!d.already" :play-busy="store.adding === d.tmdb_id"
                   alt-label=""
                   @open="store.addFromTmdb(d, d.kind)" @play="store.addFromTmdb(d, d.kind)" />
      </div>
    </section>

    <ContextMenu v-if="menu" v-bind="menu" @close="menu = null" />
  </div>
</template>

<style scoped>
/* `display:block` a propósito: como hijo de un flex column, los márgenes automáticos harían
   que la vista se encogiera al ancho del contenido y la rejilla colapsara (gotcha del repo). */
.mlib { display: block; position: relative; --alib-pad: var(--s-6); }
.mlib__aura {
  /* El aura es un degradado radial anclado ARRIBA, así que su primera fila es la más intensa y
     el borde superior del contenedor la corta en seco: medido, +20 de luminancia en una fila —
     una línea horizontal justo donde empieza el hero, más marcada en el lado donde el radial está
     centrado. Se le da su propia rampa vertical para que entre desde cero. */
  -webkit-mask-image: linear-gradient(to bottom, transparent 0%, #000 16%);
          mask-image: linear-gradient(to bottom, transparent 0%, #000 16%);
  position: absolute; inset: 0 0 auto 0; height: 60vh; pointer-events: none; z-index: 0;
  background: radial-gradient(80% 60% at 20% 0%, color-mix(in srgb, var(--tint-c) 22%, transparent) 0%, transparent 70%);
  transition: background 1.2s var(--ease-silk);
}
.mlib__head, .mlib__hero, .toolbar, .mlib__grid, .mlib__warn { position: relative; z-index: 1; }
.mlib__head { padding: var(--s-5) var(--alib-pad) 0; }
.mlib__warn { display: flex; align-items: center; gap: var(--s-2); margin: var(--s-3) var(--alib-pad) 0;
  color: var(--warn); font-size: var(--fs-sm); }
.mlib__hero { margin-bottom: var(--s-6); }
.toolbar, .mlib__grid { position: relative; padding-inline: var(--alib-pad); }
/* La rejilla es la compartida (`.grid` en base.css); aquí sólo su ancho base y el respiro. */
/* El MISMO ancho base que Mi Anime y Biblioteca (`14.0625rem`). Con 11rem la tarjeta medía
   241×362 y las de anime/manga 332×498: la misma `MediaCard`, un 28 % más pequeña sólo aquí, y al
   cambiar de modo se notaba que era otra app. El resto de la rejilla (densidad, hueco, móvil)
   vive en base.css. */
.mlib__grid { --card-min: 14.0625rem; gap: var(--s-5); padding-bottom: var(--s-8); }

/* «Para ti»: fila que se recorre, no rejilla. Es descubrimiento — no debe competir en peso con tu
   biblioteca, que es lo que has venido a ver. */
.fy { position: relative; z-index: 1; padding: 0 var(--alib-pad) var(--s-8); }
.fy__head { display: flex; align-items: baseline; gap: var(--s-3); margin-bottom: var(--s-4); }
.fy__title { font-family: var(--font-display); font-size: var(--fs-xl); }
.fy__hint { font-size: var(--fs-xs); color: var(--ink-faint); }
.fy__row { display: flex; gap: var(--s-4); overflow-x: auto; padding-bottom: var(--s-3); }
/* `flex: 0 0 auto` con ancho fijo: dentro de un flex horizontal la tarjeta no tiene rejilla que
   la mida, y sin base se encogería hasta el ancho de su título. */
.fy__row > * { flex: 0 0 11rem; }
</style>
