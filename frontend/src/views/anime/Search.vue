<script setup>
import { computed, onMounted } from 'vue'
import { useAnimeStore } from '@/stores/anime'
import { animeFormatLabel } from '@/lib/anime'
import TorrentPanel from '@/components/anime/TorrentPanel.vue'
import AnimeRail from '@/components/anime/AnimeRail.vue'
import Icon from '@/components/ui/Icon.vue'
import Spinner from '@/components/ui/Spinner.vue'

const store = useAnimeStore()
onMounted(() => {
  store.checkQbt()
  if (!store.seasonal.length) store.loadSeasonal()   // descubrimiento para el estado inicial
})

// Populares de la temporada → envueltos para AnimeRail (poster). Clic = buscar torrents.
const popularItems = computed(() => store.seasonalPopular.map(a => ({ anime: a })))
</script>

<template>
  <TorrentPanel v-if="store.torrentAnime" />

  <div v-else class="search">
    <header class="search__head">
      <p class="eyebrow"><span class="tick" /> ENCUENTRA Y DESCARGA</p>
      <h1>Buscar anime</h1>
      <label class="bigbox">
        <Icon name="search" :size="18" />
        <input v-model="store.searchQuery" @keyup.enter="store.searchAnime()"
               placeholder="Título del anime…" autofocus />
        <button class="bigbox__go" @click="store.searchAnime()">Buscar</button>
      </label>
    </header>

    <div v-if="store.searchLoading" class="grid">
      <div v-for="n in 10" :key="n" class="skeleton" />
    </div>
    <!-- Estado inicial (sin búsqueda): descubre populares de la temporada -->
    <div v-else-if="!store.searchResults.length" class="discover">
      <AnimeRail v-if="popularItems.length" title="Populares de la temporada" variant="poster"
                 :items="popularItems" @select="it => store.openTorrents(it.anime)" />
      <div v-else class="hint">
        <Icon name="film" :size="34" />
        <p>Busca un anime para ver torrents disponibles y enviarlos a qBittorrent.</p>
      </div>
    </div>
    <div v-else class="grid">
      <article v-for="a in store.searchResults" :key="a.al_id || a.title" class="rc" tabindex="0"
               @click="store.openTorrents(a)" @keydown.enter="store.openTorrents(a)">
        <div class="rc__poster">
          <img v-if="a.cover" :src="a.cover" :alt="a.title" loading="lazy" @load="$event.target.classList.add('is-loaded')" class="rc__img" />
          <div class="rc__scrim" />
          <span class="rc__fmt">{{ animeFormatLabel(a.format) }}</span>
          <span v-if="a.score" class="rc__score"><Icon name="spark" :size="10" /> {{ a.score }}</span>
          <div class="rc__hover"><span class="rc__btn"><Icon name="download" :size="16" /> Torrents</span></div>
          <div class="rc__overlay">
            <h3 class="rc__title">{{ a.title }}</h3>
            <span class="rc__sub">{{ a.episodes ? a.episodes + ' ep.' : '? ep.' }}<template v-if="a.season_label"> · {{ a.season_label }}</template></span>
          </div>
        </div>
      </article>
    </div>
  </div>
</template>

<style scoped>
.search { max-width: var(--content-max); margin: 0 auto; padding: 0 var(--s-6) var(--s-8); }
.search__head { padding: var(--s-5) 0 var(--s-6); }
.eyebrow { display: flex; align-items: center; gap: var(--s-2); font-family: var(--font-mono); font-size: var(--fs-2xs); letter-spacing: var(--tracking-caps); color: var(--azure); margin-bottom: var(--s-2); }
.tick { width: 14px; height: 1px; background: var(--azure); box-shadow: 0 0 8px var(--azure-glow); }
.bigbox { display: flex; align-items: center; gap: var(--s-3); margin-top: var(--s-4); padding: var(--s-3) var(--s-4); max-width: 640px; background: var(--surface); border: 1px solid var(--line-2); border-radius: var(--r-lg); color: var(--ink-faint); transition: border-color var(--t-fast), box-shadow var(--t-fast); }
.bigbox:focus-within { border-color: var(--azure); box-shadow: 0 0 0 4px var(--azure-haze); }
.bigbox input { flex: 1; border: none; outline: none; background: none; color: var(--ink); font-size: var(--fs-lg); }
.bigbox__go { padding: var(--s-2) var(--s-5); border-radius: var(--r-sm); background: var(--azure); color: #fff; font-weight: 600; font-size: var(--fs-sm); transition: background var(--t-fast); }
.bigbox__go:hover { background: var(--azure-bright); }

.hint { display: flex; flex-direction: column; align-items: center; gap: var(--s-3); padding: var(--s-9) 0; color: var(--ink-faint); text-align: center; }

.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(11.875rem, 1fr)); gap: var(--s-5); }
.skeleton { aspect-ratio: 2/3; border-radius: var(--r-md); background: linear-gradient(100deg, var(--surface) 30%, var(--surface-2) 50%, var(--surface) 70%); background-size: 200% 100%; animation: shimmer 1.4s linear infinite; }

.rc { outline: none; transition: transform var(--t-base) var(--ease-snap); }
.rc:hover, .rc:focus-visible { transform: translateY(-6px); }
.rc__poster { position: relative; aspect-ratio: 2/3; border-radius: var(--r-md); overflow: hidden; background: var(--surface-2); border: 1px solid var(--line); cursor: pointer; transition: box-shadow var(--t-base), border-color var(--t-base); }
.rc:hover .rc__poster { border-color: var(--azure-glow); box-shadow: var(--shadow-lg), 0 0 0 1px var(--azure-glow); }
.rc__img { width: 100%; height: 100%; object-fit: cover; opacity: 0; transition: opacity var(--t-slow), transform var(--t-cine) var(--ease-silk); }
.rc__img.is-loaded { opacity: 1; }
.rc:hover .rc__img { transform: scale(1.07); }
.rc__scrim { position: absolute; inset: 0; background: linear-gradient(180deg, rgba(7,10,18,.3) 0%, transparent 28%, transparent 50%, rgba(5,7,13,.95) 100%); }
.rc__fmt { position: absolute; top: var(--s-2); left: var(--s-2); font-family: var(--font-mono); font-size: var(--fs-2xs); font-weight: 600; padding: 2px 7px; border-radius: var(--r-xs); background: rgba(7,10,18,.6); backdrop-filter: blur(6px); }
.rc__score { position: absolute; top: var(--s-2); right: var(--s-2); display: inline-flex; align-items: center; gap: 3px; font-size: var(--fs-2xs); font-weight: 700; padding: 2px 7px; border-radius: var(--r-pill); color: var(--gold); background: rgba(7,10,18,.6); backdrop-filter: blur(6px); }
.rc__hover { position: absolute; inset: 0; display: grid; place-items: center; opacity: 0; transition: opacity var(--t-base); }
.rc:hover .rc__hover, .rc:focus-visible .rc__hover { opacity: 1; }
.rc__btn { display: inline-flex; align-items: center; gap: 6px; padding: var(--s-2) var(--s-4); border-radius: var(--r-pill); background: var(--azure); color: #fff; font-size: var(--fs-xs); font-weight: 600; box-shadow: var(--glow-azure); }
.rc__overlay { position: absolute; left: 0; right: 0; bottom: 0; padding: var(--s-3); }
.rc__title { font-size: var(--fs-sm); font-weight: 600; color: #fff; line-height: var(--lh-snug); text-shadow: 0 1px 6px rgba(0,0,0,.65); display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
.rc__sub { font-size: var(--fs-2xs); color: var(--ink-soft); text-shadow: 0 1px 4px rgba(0,0,0,.6); }

@media (max-width: 640px) { .search { padding: 0 var(--s-4) var(--s-8); } .grid { grid-template-columns: repeat(auto-fill, minmax(8.75rem, 1fr)); } }
</style>
