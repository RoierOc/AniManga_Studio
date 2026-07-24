<script setup>
/* Home de Series y Películas — espejo de `views/anime/AnimeLibrary.vue`:
   aura + hero a sangre + riel "Seguir viendo" + toolbar + rejilla de tarjetas. */
import { computed, onMounted, ref } from 'vue'
import { useMediaStore } from '@/stores/media'
import { useUiStore } from '@/stores/ui'
import { imgProxy } from '@/lib/img'
import MediaCard from '@/components/media/MediaCard.vue'
import MediaHero from '@/components/media/MediaHero.vue'
import ContinueRail from '@/components/media/ContinueRail.vue'
import ContextMenu from '@/components/ui/ContextMenu.vue'
import Icon from '@/components/ui/Icon.vue'
import EmptyState from '@/components/ui/EmptyState.vue'
import Skeleton from '@/components/ui/Skeleton.vue'
import ErrorState from '@/components/ui/ErrorState.vue'
import ContentToolbar from '@/components/ui/ContentToolbar.vue'

const store = useMediaStore()
const ui = useUiStore()
const heroTint = ref('rgb(77, 141, 255)')
const menu = ref(null)

onMounted(() => store.init())

const FILTERS = [
  { id: 'all', label: 'Todo' },
  { id: 'series', label: 'Series' },
  { id: 'movies', label: 'Películas' },
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
      : { label: 'Ver episodios', icon: 'play', primary: true, run: () => store.openDetail(h.raw) },
    { label: 'Información', icon: 'spark', run: () => store.openDetail(h.raw) },
  ],
})))

const rail = computed(() => store.continueItems.map(c => ({
  id: `${c.series_id}:${c.episode_id}`,
  raw: c,
  thumb: imgProxy(c.still, 340),
  title: c.title,
  subtitle: `T${c.season} · Episodio ${c.num}${c.episode_title ? ` — ${c.episode_title}` : ''}`,
  badge: `${c.season}x${String(c.num).padStart(2, '0')}`,
  progress: c.duration ? (c.pos / c.duration) * 100 : 0,
})))

function cardFor(it) {
  return {
    cover: it.poster,
    title: it.title,
    kindLabel: it.kind === 'movie' ? 'PELÍCULA' : 'SERIE',
    // Solo se marca lo que falta: una serie completa no necesita insignia.
    status: it.have < it.total ? { label: `Faltan ${it.total - it.have}`, color: 'var(--cyan)' } : null,
    count: it.kind === 'series' ? { done: it.have, total: it.total } : null,
    tags: [it.year ? String(it.year) : '', it.status].filter(Boolean),
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
                    :sorts="SORTS" :sort="store.sort" @update:sort="store.sort = $event"
                    :search="store.search" @update:search="store.search = $event"
                    search-placeholder="Buscar en tu biblioteca…" />

    <!-- Mismo esqueleto que Manga y Anime: la rejilla ya tiene forma antes de llegar los datos,
         en vez del parpadeo en blanco con el que el contenido saltaba de golpe. -->
    <div v-if="store.loading && !store.items.length" class="mlib__grid">
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

    <TransitionGroup v-else name="grid" tag="div" class="mlib__grid">
      <MediaCard v-for="it in store.items" :key="it.kind + it.id" v-bind="cardFor(it)"
                 :play-label="it.kind === 'movie' ? 'Ver' : 'Episodios'"
                 alt-label="Torrents" alt-icon="download"
                 @open="store.openDetail(it)" @play="play(it)"
                 @alt="store.openPicker(it)"
                 @contextmenu.prevent="openMenu($event, it)" />
    </TransitionGroup>

    <ContextMenu v-if="menu" v-bind="menu" @close="menu = null" />
  </div>
</template>

<style scoped>
/* `display:block` a propósito: como hijo de un flex column, los márgenes automáticos harían
   que la vista se encogiera al ancho del contenido y la rejilla colapsara (gotcha del repo). */
.mlib { display: block; position: relative; --alib-pad: var(--s-6); }
.mlib__aura {
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
.mlib__grid {
  display: grid; gap: var(--s-5); padding-bottom: var(--s-8);
  grid-template-columns: repeat(auto-fill, minmax(11rem, 1fr));
}
</style>
