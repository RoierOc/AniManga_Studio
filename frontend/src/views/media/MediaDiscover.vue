<script setup>
/* Descubrir (TMDB). Añadir traduce el id de TMDB al de Sonarr/Radarr vía `/resolve`, que hace un
   lookup EXACTO por `tmdb:<id>` — antes se emparejaba por título y cada obra con nombre no inglés
   abría un «varias coincidencias, elige la correcta» que era puro peaje. */
import { onMounted } from 'vue'
import { useMediaStore } from '@/stores/media'
import MediaCard from '@/components/media/MediaCard.vue'
import Spinner from '@/components/ui/Spinner.vue'
import EmptyState from '@/components/ui/EmptyState.vue'
import ErrorState from '@/components/ui/ErrorState.vue'
import DensityToggle from '@/components/ui/DensityToggle.vue'
import { useInfiniteScroll } from '@/lib/useInfiniteScroll'

const store = useMediaStore()

const LISTS = [
  { id: 'trending', label: 'Tendencias' },
  { id: 'popular', label: 'Populares' },
  { id: 'top', label: 'Mejor valoradas' },
]

onMounted(() => { if (!store.discover.length) store.loadDiscover() })

function pick(list) { store.discoverList = list; store.loadDiscover() }
function pickKind(k) {
  if (store.discoverKind === k) return
  // Los ids de género difieren entre tv y movie: al cambiar de tipo el género elegido deja de
  // ser válido, así que se resetea a "Todos".
  store.discoverKind = k; store.discoverGenre = ''; store.loadDiscover()
}
function pickGenre(id) { store.discoverGenre = store.discoverGenre === id ? '' : id; store.loadDiscover() }

const sentinel = useInfiniteScroll(
  () => store.loadMoreDiscover(),
  () => store.discoverHasMore && !store.discoverLoadingMore && !store.discoverLoading,
)

// El alta vive en el store (`addFromTmdb`): «Para ti» hace exactamente lo mismo desde la portada.
const add = (it) => store.addFromTmdb(it, store.discoverKind)
</script>

<template>
  <div class="mdisc">
    <header class="mdisc__head">
      <p class="eyebrow"><span class="tick" /> QUÉ VER</p>
    </header>

    <div class="toolbar">
      <div class="filters">
        <button v-for="l in LISTS" :key="l.id" class="pill" :class="{ 'is-active': store.discoverList === l.id }"
                @click="pick(l.id)">{{ l.label }}</button>
      </div>
      <div class="toolbar__right">
        <DensityToggle />
        <div class="sorts">
          <button class="sort" :class="{ 'is-active': store.discoverKind === 'series' }" @click="pickKind('series')">Series</button>
          <button class="sort" :class="{ 'is-active': store.discoverKind === 'movie' }" @click="pickKind('movie')">Películas</button>
        </div>
      </div>
    </div>

    <!-- Géneros: la segunda dimensión de descubrimiento. "Todos" respeta la lista de arriba;
         un género la convierte en un /discover ordenado por ese mismo criterio. -->
    <div v-if="store.genres[store.discoverKind]?.length" class="mdisc__genres">
      <button class="gpill" :class="{ 'is-active': !store.discoverGenre }" @click="pickGenre('')">Todos</button>
      <button v-for="g in store.genres[store.discoverKind]" :key="g.id"
              class="gpill" :class="{ 'is-active': store.discoverGenre === String(g.id) }"
              @click="pickGenre(String(g.id))">{{ g.name }}</button>
    </div>

    <p v-if="store.discoverSkipped" class="mdisc__note">
      {{ store.discoverSkipped }} anime{{ store.discoverSkipped > 1 ? 's' : '' }} fuera de la lista —
      tienen su propia sección en <b>Anime</b>.
    </p>

    <Spinner v-if="store.discoverLoading" />
    <ErrorState v-else-if="store.discoverErr" title="No se pudo cargar TMDB."
                hint="Requiere TMDB_API_KEY y conexión a internet." :detail="store.discoverErr"
                @retry="store.loadDiscover()" />

    <template v-else>
      <div class="grid mdisc__grid stagger">
        <MediaCard v-for="d in store.discover" :key="d.tmdb_id"
                   :cover="d.poster" :title="d.title"
                   :kind-label="store.discoverKind === 'movie' ? 'PELÍCULA' : 'SERIE'"
                   :status="d.score ? { label: `★ ${d.score}`, color: 'var(--cyan)' } : null"
                   :flag="d.already ? { tone: 'soft', icon: 'check', label: 'EN TU BIBLIOTECA' } : null"
                   :tags="[d.year ? String(d.year) : ''].filter(Boolean)"
                   :play-label="d.already ? 'Ya la tienes' : (store.adding === d.tmdb_id ? 'Añadiendo…' : 'Añadir')"
                   :play-icon="d.already ? 'check' : (store.adding === d.tmdb_id ? 'refresh' : 'plus')"
                   :play-done="!!d.already" :play-busy="store.adding === d.tmdb_id"
                   alt-label=""
                   @open="add(d)" @play="add(d)" />
      </div>
      <!-- Centinela: al asomar por el borde, pide la página siguiente sola. El botón queda de
           respaldo si no hay IntersectionObserver o si una página falló. -->
      <div ref="sentinel" class="mdisc__more">
        <Spinner v-if="store.discoverLoadingMore" :size="18" />
        <button v-else-if="store.discoverHasMore" class="mdisc__morebtn" @click="store.loadMoreDiscover()">
          Cargar más
        </button>
      </div>
    </template>
  </div>
</template>

<style scoped>
.mdisc { display: block; padding: var(--s-5) var(--s-6) var(--s-8); }
.mdisc__head { margin-bottom: var(--s-4); }
.mdisc__note { font-size: var(--fs-xs); color: var(--ink-faint); margin: 0 0 var(--s-3); }
.mdisc__genres { display: flex; flex-wrap: wrap; gap: var(--s-2); margin: 0 0 var(--s-4); }
.gpill { padding: var(--s-1) var(--s-3); border-radius: var(--r-pill); font-size: var(--fs-xs);
  font-weight: 600; color: var(--ink-soft); background: var(--surface); border: 1px solid var(--line);
  cursor: pointer; transition: all var(--t-fast); }
.gpill:hover { color: var(--ink); border-color: var(--line-strong); }
.gpill.is-active { color: #fff; background: var(--azure); border-color: transparent; }
/* Usa `.grid` de base.css y sólo redefine el ancho base (regla del sistema de diseño). Con eso
   hereda gratis el control de densidad (`--dens`), que es lo que aquí faltaba: la rejilla propia
   metía 7 tarjetas por fila a 1600 px y no había forma de cambiarlo. 14rem ≈ 5 por fila en
   densidad normal, y el conmutador da las otras dos. */
.mdisc__grid { --card-min: 14rem; }
.mdisc__more { display: grid; place-items: center; min-height: 3.5rem; margin-top: var(--s-5); }
.mdisc__morebtn { padding: var(--s-3) var(--s-6); border-radius: var(--r-pill); font-size: var(--fs-sm);
  font-weight: 600; color: var(--ink); background: var(--surface); border: 1px solid var(--line);
  cursor: pointer; transition: all var(--t-fast); }
.mdisc__morebtn:hover { color: #fff; border-color: var(--azure); background: var(--azure-haze); }
</style>
