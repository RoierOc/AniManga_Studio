<script setup>
import { onMounted } from 'vue'
import { useAnimeStore } from '@/stores/anime'
import TorrentPanel from '@/components/anime/TorrentPanel.vue'
import DiscoverCard from '@/components/anime/DiscoverCard.vue'
import Icon from '@/components/ui/Icon.vue'
import Spinner from '@/components/ui/Spinner.vue'
import Skeleton from '@/components/ui/Skeleton.vue'
import ErrorState from '@/components/ui/ErrorState.vue'
import EmptyState from '@/components/ui/EmptyState.vue'

const store = useAnimeStore()
onMounted(() => store.checkQbt())
</script>

<template>
  <TorrentPanel v-if="store.torrentAnime" />

  <div v-else class="search">
    <header class="search__head">
      <p class="eyebrow"><span class="tick" /> ENCUENTRA Y DESCARGA</p>
      <h1>Buscar anime</h1>
      <label class="bigbox">
        <Icon name="search" :size="18" />
        <input v-model="store.searchQuery" type="search" enterkeyhint="search"
               @input="store.onSearchInput()" @keyup.enter="store.submitSearch()"
               placeholder="Título del anime…" autofocus />
        <button class="bigbox__go" @click="store.submitSearch()">Buscar</button>
      </label>
    </header>

    <div v-if="store.searchLoading" class="grid">
      <Skeleton v-for="n in 10" :key="n" variant="poster" />
    </div>
    <!-- Si la búsqueda REVENTÓ no podemos caer al estado inicial como si no hubiera resultados. -->
    <ErrorState v-else-if="store.searchError" title="No se pudo buscar."
                :detail="store.searchError" @retry="store.searchAnime()" />
    <!-- Estado inicial: NADA. Aquí hubo rieles de «populares» y «mejor valorados», pero salían
         del mismo `seasonalPopular` reordenado — o sea, las mismas 20 series dos veces — y el
         usuario no los usaba. Una vista de búsqueda que no ha buscado nada no tiene por qué
         inventarse contenido: la caja es la interfaz. Temporada y Explorar ya son las vistas de
         descubrimiento, y están en la misma barra. -->
    <div v-else-if="!store.searchCompleted" class="hint">
      <Icon name="film" :size="34" />
      <p>Busca un anime para ver torrents disponibles y enviarlos a qBittorrent.</p>
    </div>
    <EmptyState v-else-if="!store.searchResults.length" icon="search"
                :title="`No encontramos «${store.searchQuery.trim()}»`"
                hint="Prueba con otro título o limpia la búsqueda para empezar de nuevo.">
      <template #action>
        <button class="search__clear" @click="store.searchQuery = ''; store.submitSearch()">Limpiar búsqueda</button>
      </template>
    </EmptyState>
    <!-- Antes esto era una tarjeta propia (`.rc`) que apuntaba el <img> DIRECTO al CDN de
         AniList y no decía si ya tenías la serie. `DiscoverCard` ya hace las tres cosas bien
         y su clic ES «buscar torrents». -->
    <div v-else class="grid">
      <DiscoverCard v-for="a in store.searchResults" :key="a.al_id || a.title" :anime="a" />
    </div>
  </div>
</template>

<style scoped>
.search { max-width: var(--content-max); margin: 0 auto; padding: 0 var(--s-6) var(--s-8); }
.search__head { padding: var(--s-5) 0 var(--s-6); }
.eyebrow { display: flex; align-items: center; gap: var(--s-2); font-family: var(--font-mono); font-size: var(--fs-2xs); letter-spacing: var(--tracking-caps); color: var(--azure); margin-bottom: var(--s-2); }
.tick { width: 0.875rem; height: 1px; background: var(--azure); box-shadow: 0 0 8px var(--azure-glow); }
.bigbox { display: flex; align-items: center; gap: var(--s-3); margin-top: var(--s-4); padding: var(--s-3) var(--s-4); max-width: 40rem; background: var(--surface); border: 1px solid var(--line-2); border-radius: var(--r-lg); color: var(--ink-faint); transition: border-color var(--t-fast), box-shadow var(--t-fast); }
.bigbox:focus-within { border-color: var(--azure); box-shadow: 0 0 0 4px var(--azure-haze); }
.bigbox input { flex: 1; border: none; outline: none; background: none; color: var(--ink); font-size: var(--fs-lg); }
.bigbox__go { padding: var(--s-2) var(--s-5); border-radius: var(--r-sm); background: var(--azure); color: #fff; font-weight: 600; font-size: var(--fs-sm); transition: background var(--t-fast); }
.bigbox__go:hover { background: var(--azure-bright); }

.hint { display: flex; flex-direction: column; align-items: center; gap: var(--s-3); padding: var(--s-9) 0; color: var(--ink-faint); text-align: center; }
.search__clear { padding: var(--s-3) var(--s-5); border-radius: var(--r-pill); color: var(--ink);
  background: var(--surface); border: 1px solid var(--line); cursor: pointer; }
.search__clear:hover { border-color: var(--azure); background: var(--azure-haze); }

/* Ancho base propio; el resto de la rejilla (densidad, hueco, móvil) vive en base.css */
.grid { --card-min: 11.875rem; gap: var(--s-5); }


@media (max-width: 640px) { .search { padding: 0 var(--s-4) var(--s-8); } .grid { --card-min: 8.75rem; } }
</style>
