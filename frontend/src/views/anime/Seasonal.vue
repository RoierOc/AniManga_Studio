<script setup>
import { computed, onMounted } from 'vue'
import { useAnimeStore } from '@/stores/anime'
import { SEASON_ES } from '@/lib/anime'
import DiscoverCard from '@/components/anime/DiscoverCard.vue'
import Icon from '@/components/ui/Icon.vue'
import EmptyState from '@/components/ui/EmptyState.vue'
import ErrorState from '@/components/ui/ErrorState.vue'
import Select from '@/components/ui/Select.vue'
import Skeleton from '@/components/ui/Skeleton.vue'

const store = useAnimeStore()

const SORTS = [
  { id: 'score', label: 'Puntuación' },
  { id: 'popularity', label: 'Popularidad' },
  { id: 'trending', label: 'Tendencia' },
]
/* UN solo desplegable, no temporada + año por separado.
   Eran cuatro controles para elegir lo mismo (‹, ›, «Verano», «2026»), y encima los dos
   desplegables permitían componer huecos absurdos — se podía dejar «Invierno 2027» a medio
   camino y disparar una carga por cada mitad. La lista cronológica es lo que la gente piensa:
   una temporada es un punto en el tiempo, no dos campos. Las flechas se quedan porque el salto
   de ±1 es el 90 % de los casos y con la lista costaría dos clics. */
const SEASONS = ['WINTER', 'SPRING', 'SUMMER', 'FALL']
const periods = computed(() => {
  const cur = new Date().getFullYear(); const arr = []
  for (let y = cur + 1; y >= 1990; y--) {
    for (const s of [...SEASONS].reverse()) arr.push({ value: `${s}|${y}`, label: `${SEASON_ES[s]} ${y}` })
  }
  return arr
})
const label = computed(() => store.season && store.year ? `${SEASON_ES[store.season] || store.season} ${store.year}` : 'Temporada actual')
const genres = computed(() => {
  const s = new Set(); store.seasonal.forEach(a => (a.genres || []).forEach(g => s.add(g)))
  return [...s].sort()
})
const filtered = computed(() =>
  store.seasonGenre ? store.seasonal.filter(a => (a.genres || []).includes(store.seasonGenre)) : store.seasonal
)

function setSort(id) { store.seasonSort = id; store.loadSeasonal() }
function setPeriod(v) {
  const [s, y] = String(v).split('|')
  store.season = s; store.year = Number(y)
  store.loadSeasonal()          // una sola carga: antes eran dos, una por desplegable
}

onMounted(() => { if (!store.seasonal.length) store.loadSeasonal() })
</script>

<template>
  <div class="season">
    <header class="season__head">
      <div>
        <p class="eyebrow"><span class="tick" /> DESCUBRE</p>
        <div class="season__nav">
          <button class="navbtn" @click="store.seasonNav(-1)"><Icon name="chevron" :size="16" :style="{ transform: 'rotate(180deg)' }" /></button>
          <h1>{{ label }}</h1>
          <button class="navbtn" @click="store.seasonNav(1)"><Icon name="chevron" :size="16" /></button>
        </div>
      </div>

      <div class="season__controls">
        <Select :model-value="`${store.season}|${store.year}`" aria-label="Temporada"
                :options="periods" @change="setPeriod($event)" />
        <div class="segm">
          <button v-for="s in SORTS" :key="s.id" :class="{ 'is-active': store.seasonSort === s.id }" @click="setSort(s.id)">{{ s.label }}</button>
        </div>
      </div>
    </header>

    <div v-if="genres.length" class="genres">
      <button class="gchip" :class="{ 'is-active': !store.seasonGenre }" @click="store.seasonGenre = ''">Todos</button>
      <button v-for="g in genres" :key="g" class="gchip" :class="{ 'is-active': store.seasonGenre === g }"
              @click="store.seasonGenre = store.seasonGenre === g ? '' : g">{{ g }}</button>
    </div>

    <div v-if="store.seasonalLoading" class="grid">
      <Skeleton v-for="n in 12" :key="n" variant="poster" />
    </div>
    <ErrorState v-else-if="store.seasonalError && !store.seasonal.length"
                title="No se pudo cargar la temporada." :detail="store.seasonalError"
                @retry="store.loadSeasonal()" />
    <EmptyState v-else-if="!filtered.length" icon="spark" title="Sin resultados para esta temporada."
                :hint="store.seasonGenre ? `Ningún título de esta temporada es de ${store.seasonGenre}.` : ''" />
    <div v-else class="grid">
      <DiscoverCard v-for="a in filtered" :key="a.al_id" :anime="a" />
    </div>
  </div>
</template>

<style scoped>
.season { max-width: var(--content-max); margin: 0 auto; padding: 0 var(--s-6) var(--s-8); }
.season__head { display: flex; align-items: flex-end; justify-content: space-between; flex-wrap: wrap; gap: var(--s-4); padding: var(--s-5) 0; }
.eyebrow { display: flex; align-items: center; gap: var(--s-2); font-family: var(--font-mono); font-size: var(--fs-2xs); letter-spacing: var(--tracking-caps); color: var(--azure); margin-bottom: var(--s-2); }
.tick { width: 0.875rem; height: 1px; background: var(--azure); box-shadow: 0 0 8px var(--azure-glow); }
.season__nav { display: flex; align-items: center; gap: var(--s-3); }
.navbtn { width: 2.125rem; height: 2.125rem; display: grid; place-items: center; border-radius: var(--r-sm); border: 1px solid var(--line); color: var(--ink-soft); transition: all var(--t-fast); }
.navbtn:hover { color: var(--ink); border-color: var(--azure); background: var(--azure-haze); }

.season__controls { display: flex; align-items: center; gap: var(--s-2); flex-wrap: wrap; }

.genres { display: flex; gap: 0.25rem; flex-wrap: wrap; margin-bottom: var(--s-5); }
/* 19 filtros con contorno = 19 objetos compitiendo con el contenido. En reposo son texto; el
   contorno y el color se los gana el que está activo, que es el único que hay que ver de un vistazo. */
.gchip { padding: 0.3125rem 0.625rem; border-radius: var(--r-pill); font-size: var(--fs-xs); color: var(--ink-faint); border: 1px solid transparent; transition: color var(--t-fast), background var(--t-fast), border-color var(--t-fast); }
.gchip:hover { color: var(--ink); background: var(--surface-2); }
.gchip.is-active { background: var(--azure-haze); border-color: var(--azure); color: var(--azure-bright); }

/* Ancho base propio; el resto de la rejilla (densidad, hueco, móvil) vive en base.css */
.grid { --card-min: 11.875rem; gap: var(--s-5); }
.empty { display: flex; flex-direction: column; align-items: center; gap: var(--s-3); padding: var(--s-8) 0; color: var(--ink-faint); }

@media (max-width: 640px) { .season { padding: 0 var(--s-4) var(--s-8); } .grid { --card-min: 8.75rem; } }
</style>
