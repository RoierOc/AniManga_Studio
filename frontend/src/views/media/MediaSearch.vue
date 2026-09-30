<script setup>
import { rememberedRef } from '@/lib/viewMemory'
/* Buscar y añadir. La búsqueda va contra el catálogo de Sonarr/Radarr (no TMDB), así que el id
   que vuelve ya es el del alta — sin dos identidades que reconciliar. */
import { computed, ref } from 'vue'
import { useMediaStore } from '@/stores/media'
import MediaCard from '@/components/media/MediaCard.vue'
import Icon from '@/components/ui/Icon.vue'
import Spinner from '@/components/ui/Spinner.vue'
import EmptyState from '@/components/ui/EmptyState.vue'
import ErrorState from '@/components/ui/ErrorState.vue'

const store = useMediaStore()
const term = rememberedRef('media:search:term', '')
const kind = rememberedRef('media:search:kind', 'series')

const results = computed(() => store.results)

async function add(r) {
  if (r.already) return
  const ok = await store.add(r.ext_id, kind.value)
  if (ok) r.already = true
}

async function run() {
  store.filter = kind.value === 'movie' ? 'movies' : 'series'
  await store.submitSearch(term.value)
}

// Buscar mientras se escribe (Enter/botón siguen disparando ya, sin esperar al debounce).
function onType() {
  store.filter = kind.value === 'movie' ? 'movies' : 'series'
  store.onSearchInput(term.value)
}
</script>

<template>
  <div class="msearch">
    <header class="msearch__head">
      <p class="eyebrow"><span class="tick" /> AÑADIR A TU BIBLIOTECA</p>
    </header>

    <form class="msearch__form" @submit.prevent="run">
      <div class="filters">
        <button type="button" class="pill" :class="{ 'is-active': kind === 'series' }" @click="kind = 'series'">Series</button>
        <button type="button" class="pill" :class="{ 'is-active': kind === 'movie' }" @click="kind = 'movie'">Películas</button>
      </div>
      <label class="searchbox">
        <Icon name="search" :size="15" />
        <input v-model="term" type="search" enterkeyhint="search" @input="onType"
               :placeholder="kind === 'series' ? 'Título de la serie…' : 'Título de la película…'" />
      </label>
      <button class="msearch__go" type="submit" :disabled="store.searching || !term.trim()">
        {{ store.searching ? 'Buscando…' : 'Buscar' }}
      </button>
    </form>

    <Spinner v-if="store.searching" />
    <ErrorState v-else-if="store.searchErr" title="No se pudo buscar en el catálogo."
                :detail="store.searchErr" @retry="run" />
    <EmptyState v-else-if="results === null" icon="search"
                title="Busca una serie o película"
                hint="Se busca en el catálogo de Sonarr y Radarr; el título original en inglés suele acertar más." />
    <EmptyState v-else-if="results && !results.length" icon="search" title="Sin resultados"
                hint="Prueba con el título original en inglés." />

    <div v-else-if="results" class="msearch__grid stagger">
      <MediaCard v-for="r in results" :key="r.ext_id"
                 :cover="r.poster" :title="r.title"
                 :kind-label="kind === 'movie' ? 'PELÍCULA' : 'SERIE'"
                 :status="r.already ? { label: 'En tu biblioteca', color: 'var(--jade)' } : null"
                 :tags="[r.year ? String(r.year) : ''].filter(Boolean)"
                 :play-label="r.already ? 'Ya la tienes' : (store.adding === r.ext_id ? 'Añadiendo…' : 'Añadir')"
                 :play-icon="r.already ? 'check' : (store.adding === r.ext_id ? 'refresh' : 'plus')"
                 :play-done="!!r.already" :play-busy="store.adding === r.ext_id"
                 alt-label=""
                 @open="add(r)" @play="add(r)" />
    </div>
  </div>
</template>

<style scoped>
.msearch { display: block; padding: var(--s-5) var(--s-6) var(--s-8); }
.msearch__head { margin-bottom: var(--s-4); }
.msearch__form { display: flex; align-items: center; gap: var(--s-3); flex-wrap: wrap; margin-bottom: var(--s-5); }
.msearch__go { padding: var(--s-2) var(--s-4); border-radius: var(--r-pill); background: var(--azure);
  color: #fff; border: 0; font: inherit; font-weight: 600; font-size: var(--fs-sm); cursor: pointer; }
.msearch__go:disabled { opacity: .5; cursor: default; }
.msearch__grid { display: grid; gap: var(--s-5); grid-template-columns: repeat(auto-fill, minmax(11rem, 1fr)); }
.msearch__add { width: 100%; margin-top: var(--s-2); padding: var(--s-2); border-radius: var(--r-sm);
  background: var(--surface-2); border: 1px solid var(--line); color: var(--ink); font: inherit;
  font-size: var(--fs-xs); font-weight: 600; cursor: pointer; transition: all var(--t-fast); }
.msearch__add:hover:not(:disabled) { border-color: var(--azure); color: var(--azure-bright); }
.msearch__add:disabled { opacity: .55; cursor: default; }
</style>
