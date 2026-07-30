<script setup>
import { computed, onMounted } from 'vue'
import { useAnimeStore } from '@/stores/anime'
import DiscoverCard from '@/components/anime/DiscoverCard.vue'
import Icon from '@/components/ui/Icon.vue'
import EmptyState from '@/components/ui/EmptyState.vue'
import ErrorState from '@/components/ui/ErrorState.vue'
import Select from '@/components/ui/Select.vue'
import Skeleton from '@/components/ui/Skeleton.vue'

const store = useAnimeStore()

const SORTS = [
  { id: 'score', label: 'Mejor puntuados' },
  { id: 'popularity', label: 'Más populares' },
  { id: 'trending', label: 'Tendencia' },
]
const FORMATS = [
  { id: '', label: 'Todo' },
  { id: 'TV', label: 'TV' },
  { id: 'MOVIE', label: 'Película' },
  { id: 'OVA', label: 'OVA' },
  { id: 'ONA', label: 'ONA' },
  { id: 'SPECIAL', label: 'Especial' },
]
const years = computed(() => {
  const cur = new Date().getFullYear(); const arr = []
  for (let y = cur + 1; y >= 1970; y--) arr.push(y)
  return arr
})
// Solo géneros "reales" (no tags) para el chip-rail; los tags saturan la lista.
const genres = computed(() => store.exploreGenreList.filter(g => g.type === 'genre').map(g => g.name))

function reload() { store.loadExplore(false) }
function setSort(id) { store.exploreSort = id; reload() }
function setGenre(g) { store.exploreGenre = store.exploreGenre === g ? '' : g; reload() }

onMounted(() => {
  store.loadExploreGenres()
  if (!store.explore.length) store.loadExplore(false)
})
</script>

<template>
  <div class="expl">
    <header class="expl__head">
      <div>
        <p class="eyebrow"><span class="tick" /> EXPLORAR</p>
        <h1>Descubre anime</h1>
      </div>

      <div class="expl__controls">
        <Select :model-value="store.exploreYear" aria-label="Año"
                :options="[{ value: 0, label: 'Cualquier año' }, ...years.map(y => ({ value: y, label: String(y) }))]"
                @change="store.exploreYear = Number($event); reload()" />
        <Select :model-value="store.exploreFormat" aria-label="Formato"
                :options="FORMATS.map(f => ({ value: f.id, label: f.label }))"
                @change="store.exploreFormat = $event; reload()" />
        <div class="segm">
          <button v-for="s in SORTS" :key="s.id" :class="{ 'is-active': store.exploreSort === s.id }" @click="setSort(s.id)">{{ s.label }}</button>
        </div>
      </div>
    </header>

    <div v-if="genres.length" class="genres">
      <button class="gchip" :class="{ 'is-active': !store.exploreGenre }" @click="setGenre(store.exploreGenre)">Todos</button>
      <button v-for="g in genres" :key="g" class="gchip" :class="{ 'is-active': store.exploreGenre === g }" @click="setGenre(g)">{{ g }}</button>
    </div>

    <div v-if="store.exploreLoading && !store.explore.length" class="grid">
      <Skeleton v-for="n in 12" :key="n" variant="poster" />
    </div>
    <!-- El fallo va ANTES del vacío: con AniList caída, «sin resultados» sería mentira. -->
    <ErrorState v-else-if="store.exploreError && !store.explore.length"
                title="No se pudo cargar el catálogo." :detail="store.exploreError"
                @retry="store.loadExplore()" />
    <EmptyState v-else-if="!store.explore.length" icon="spark" title="Sin resultados con estos filtros."
                hint="Prueba a quitar un filtro: género, año o formato." />
    <template v-else>
      <div class="grid">
        <DiscoverCard v-for="a in store.explore" :key="a.al_id" :anime="a" />
      </div>
      <div v-if="store.exploreHasNext" class="more">
        <button class="morebtn" :disabled="store.exploreLoading" @click="store.loadExploreMore()">
          <Icon name="chevron" :size="15" :style="{ transform: 'rotate(90deg)' }" />
          {{ store.exploreLoading ? 'Cargando…' : 'Cargar más' }}
        </button>
      </div>
    </template>
  </div>
</template>

<style scoped>
.expl { max-width: var(--content-max); margin: 0 auto; padding: 0 var(--s-6) var(--s-8); }
.expl__head { display: flex; align-items: flex-end; justify-content: space-between; flex-wrap: wrap; gap: var(--s-4); padding: var(--s-5) 0; }
.eyebrow { display: flex; align-items: center; gap: var(--s-2); font-family: var(--font-mono); font-size: var(--fs-2xs); letter-spacing: var(--tracking-caps); color: var(--azure); margin-bottom: var(--s-2); }
.tick { width: 0.875rem; height: 1px; background: var(--azure); box-shadow: 0 0 8px var(--azure-glow); }

.expl__controls { display: flex; align-items: center; gap: var(--s-2); flex-wrap: wrap; }
.segm { display: flex; gap: 2px; padding: 3px; border-radius: var(--r-md); background: var(--surface); border: 1px solid var(--line); }
.segm button { padding: 0.375rem 0.75rem; border-radius: var(--r-sm); font-size: var(--fs-xs); font-weight: 500; color: var(--ink-faint); transition: all var(--t-fast); }
.segm button:hover { color: var(--ink); }
.segm button.is-active { background: var(--surface-3); color: var(--ink); }

.genres { display: flex; gap: var(--s-2); flex-wrap: wrap; margin-bottom: var(--s-5); }
.gchip { padding: 0.3125rem 0.75rem; border-radius: var(--r-pill); font-size: var(--fs-xs); color: var(--ink-soft); border: 1px solid var(--line); transition: all var(--t-fast); }
.gchip:hover { color: var(--ink); border-color: var(--line-strong); }
.gchip.is-active { background: var(--azure-haze); border-color: var(--azure); color: var(--azure-bright); }

/* Ancho base propio; el resto de la rejilla (densidad, hueco, móvil) vive en base.css */
.grid { --card-min: 11.875rem; gap: var(--s-5); }

.more { display: flex; justify-content: center; padding: var(--s-6) 0 0; }
.morebtn { display: inline-flex; align-items: center; gap: var(--s-2); padding: var(--s-3) var(--s-5); border-radius: var(--r-pill); border: 1px solid var(--line-2); background: var(--surface); color: var(--ink-soft); font-size: var(--fs-sm); font-weight: 600; transition: all var(--t-fast); }
.morebtn:hover:not(:disabled) { color: var(--ink); border-color: var(--azure); background: var(--azure-haze); }
.morebtn:disabled { opacity: .6; cursor: default; }

@media (max-width: 640px) { .expl { padding: 0 var(--s-4) var(--s-8); } .grid { --card-min: 8.75rem; } }
</style>
