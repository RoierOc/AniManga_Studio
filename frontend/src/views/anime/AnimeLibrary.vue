<script setup>
import { computed, onMounted, onUnmounted } from 'vue'
import { useAnimeStore } from '@/stores/anime'
import { ANIME_STATUS, STATUS_ORDER } from '@/lib/anime'
import AnimeCard from '@/components/anime/AnimeCard.vue'
import Icon from '@/components/ui/Icon.vue'

const store = useAnimeStore()

// Background sync (15s) — only refreshes while qBittorrent has active downloads,
// matching the original app's _qbtSyncTimer behaviour.
let sync = null
onMounted(() => {
  sync = setInterval(() => { if (store.hasActiveQbt()) store.loadLibrary(true) }, 15000)
})
onUnmounted(() => { if (sync) clearInterval(sync) })

const SORTS = [
  { id: 'last_added', label: 'Recientes' },
  { id: 'last_watched', label: 'Vistos' },
  { id: 'title', label: 'A–Z' },
  { id: 'progress', label: 'Progreso' },
]

const filtered = computed(() => {
  let list = [...store.library]
  if (store.libFilter !== 'all') list = list.filter(a => a.status === store.libFilter)
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
  const c = { all: store.library.length }
  for (const k of STATUS_ORDER) c[k] = store.library.filter(a => a.status === k).length
  return c
})
</script>

<template>
  <div class="alib">
    <header class="hero stagger">
      <div style="--i:0">
        <p class="hero__eyebrow"><span class="hero__tick" /> TU ANIME</p>
        <h1>Estudio</h1>
      </div>
      <div class="hero__actions" style="--i:1">
        <button class="scanbtn" @click="store.openScan()" title="Carpetas de anime local"><Icon name="folder" :size="15" /> Carpetas</button>
        <label class="searchbox">
          <Icon name="search" :size="15" />
          <input v-model="store.libSearch" type="search" placeholder="Buscar en tu anime…" />
        </label>
      </div>
    </header>

    <!-- Continue watching -->
    <section v-if="store.continueWatching.length" class="cw">
      <h3 class="cw__title">Seguir viendo</h3>
      <div class="cw__row">
        <article v-for="cw in store.continueWatching" :key="cw.anime.id" class="cwc" @click="store.play(cw.anime, cw.ep)">
          <div class="cwc__thumb">
            <img v-if="cw.anime.cover" :src="cw.anime.cover" :alt="cw.anime.title" loading="lazy" />
            <div class="cwc__scrim" />
            <div class="cwc__play"><Icon name="play" :size="22" /></div>
            <span class="cwc__ep">EP {{ cw.ep.num }}</span>
            <div v-if="cw.ep.resume_pos > 0 && cw.ep.duration" class="cwc__bar"><span :style="{ width: Math.min(100, cw.ep.resume_pos / cw.ep.duration * 100) + '%' }" /></div>
          </div>
          <div class="cwc__title">{{ cw.anime.title }}</div>
        </article>
      </div>
    </section>

    <div class="toolbar stagger">
      <div class="filters" style="--i:2">
        <button class="pill" :class="{ 'is-active': store.libFilter === 'all' }" @click="store.libFilter = 'all'">
          Todo <span class="pill__n">{{ counts.all }}</span>
        </button>
        <button v-for="k in STATUS_ORDER" :key="k" v-show="counts[k]" class="pill"
                :class="{ 'is-active': store.libFilter === k }" @click="store.libFilter = k"
                :style="store.libFilter === k ? { color: ANIME_STATUS[k].color, borderColor: ANIME_STATUS[k].color } : {}">
          {{ ANIME_STATUS[k].label }} <span class="pill__n">{{ counts[k] }}</span>
        </button>
      </div>
      <div class="sorts" style="--i:2">
        <button v-for="s in SORTS" :key="s.id" class="sort" :class="{ 'is-active': store.libSort === s.id }"
                @click="store.libSort = s.id">{{ s.label }}</button>
      </div>
    </div>

    <div v-if="store.loading" class="grid">
      <div v-for="n in 10" :key="n" class="skeleton" />
    </div>
    <div v-else-if="!filtered.length" class="empty">
      <Icon name="film" :size="34" />
      <p>{{ store.library.length ? 'Sin resultados.' : 'Aún no has añadido anime.' }}</p>
    </div>
    <div v-else class="grid">
      <AnimeCard v-for="a in filtered" :key="a.id" :anime="a" @open="store.openDetail($event)" />
    </div>
  </div>
</template>

<style scoped>
.alib { max-width: var(--content-max); margin: 0 auto; padding: 0 var(--s-6); }
.hero { display: flex; align-items: flex-end; justify-content: space-between; flex-wrap: wrap; gap: var(--s-4); padding: var(--s-5) 0 var(--s-5); }
.hero__eyebrow { display: flex; align-items: center; gap: var(--s-2); font-family: var(--font-mono); font-size: var(--fs-2xs); letter-spacing: var(--tracking-caps); color: var(--azure); margin-bottom: var(--s-2); }
.hero__tick { width: 14px; height: 1px; background: var(--azure); box-shadow: 0 0 8px var(--azure-glow); }

.searchbox { display: flex; align-items: center; gap: var(--s-2); padding: var(--s-2) var(--s-3); width: min(280px, 50vw);
  background: var(--surface); border: 1px solid var(--line); border-radius: var(--r-md); color: var(--ink-faint); transition: border-color var(--t-fast), box-shadow var(--t-fast); }
.searchbox:focus-within { border-color: var(--azure); box-shadow: 0 0 0 3px var(--azure-haze); }
.searchbox input { flex: 1; border: none; outline: none; background: none; color: var(--ink); font-size: var(--fs-sm); }
.hero__actions { display: flex; align-items: center; gap: var(--s-2); }
.scanbtn { display: inline-flex; align-items: center; gap: 6px; padding: var(--s-2) var(--s-3); border-radius: var(--r-md); font-size: var(--fs-sm); color: var(--ink-soft); border: 1px solid var(--line); transition: all var(--t-fast); }
.scanbtn:hover { color: var(--azure-bright); border-color: var(--azure); }

/* continue watching */
.cw { margin-bottom: var(--s-6); }
.cw__title { font-family: var(--font-display); font-size: var(--fs-lg); margin-bottom: var(--s-3); }
.cw__row { display: flex; gap: var(--s-4); overflow-x: auto; padding-bottom: var(--s-2); }
.cwc { flex-shrink: 0; width: 220px; cursor: pointer; transition: transform var(--t-base) var(--ease-snap); }
.cwc:hover { transform: translateY(-4px); }
.cwc__thumb { position: relative; aspect-ratio: 16/9; border-radius: var(--r-md); overflow: hidden; background: var(--surface-2); border: 1px solid var(--line); }
.cwc:hover .cwc__thumb { border-color: var(--azure-glow); box-shadow: var(--shadow-md); }
.cwc__thumb img { width: 100%; height: 100%; object-fit: cover; }
.cwc__scrim { position: absolute; inset: 0; background: linear-gradient(180deg, rgba(7,10,18,.1), rgba(5,7,13,.6)); }
.cwc__play { position: absolute; inset: 0; display: grid; place-items: center; color: #fff; opacity: 0; transition: opacity var(--t-base); }
.cwc:hover .cwc__play { opacity: 1; }
.cwc__ep { position: absolute; top: var(--s-2); left: var(--s-2); font-family: var(--font-mono); font-size: var(--fs-2xs); font-weight: 700; padding: 2px 7px; border-radius: var(--r-xs); background: rgba(7,10,18,.7); color: var(--ice); }
.cwc__bar { position: absolute; left: 0; right: 0; bottom: 0; height: 3px; background: rgba(255,255,255,.15); }
.cwc__bar span { display: block; height: 100%; background: var(--azure); }
.cwc__title { margin-top: var(--s-2); font-size: var(--fs-sm); font-weight: 500; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }

.toolbar { display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: var(--s-3); margin-bottom: var(--s-6); }
.filters { display: flex; gap: var(--s-2); flex-wrap: wrap; }
.pill { display: inline-flex; align-items: center; gap: var(--s-2); padding: var(--s-2) var(--s-3); border-radius: var(--r-pill);
  font-size: var(--fs-sm); font-weight: 500; color: var(--ink-soft); border: 1px solid var(--line); transition: all var(--t-fast); }
.pill:hover { color: var(--ink); border-color: var(--line-strong); }
.pill.is-active { background: var(--azure-haze); border-color: var(--azure); color: var(--azure-bright); }
.pill__n { font-family: var(--font-mono); font-size: var(--fs-2xs); color: var(--ink-faint); }

.sorts { display: flex; gap: 2px; padding: 3px; border-radius: var(--r-md); background: var(--surface); border: 1px solid var(--line); }
.sort { padding: 6px 12px; border-radius: var(--r-sm); font-size: var(--fs-xs); font-weight: 500; color: var(--ink-faint); transition: all var(--t-fast); }
.sort:hover { color: var(--ink); }
.sort.is-active { background: var(--surface-3); color: var(--ink); }

.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(210px, 1fr)); gap: var(--s-5) var(--s-5); }
.skeleton { aspect-ratio: 2/3; border-radius: var(--r-md); background: linear-gradient(100deg, var(--surface) 30%, var(--surface-2) 50%, var(--surface) 70%); background-size: 200% 100%; animation: shimmer 1.4s linear infinite; }
.empty { display: flex; flex-direction: column; align-items: center; gap: var(--s-3); padding: var(--s-9) 0; color: var(--ink-faint); }

@media (max-width: 540px) {
  .alib { padding: 0 var(--s-4); }
  .grid { grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: var(--s-5) var(--s-3); }
}
</style>
