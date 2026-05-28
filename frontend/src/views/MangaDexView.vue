<script setup>
import { computed, onMounted } from 'vue'
import { useMangadexStore } from '@/stores/mangadex'
import MdCard from '@/components/manga/MdCard.vue'
import MdDetailModal from '@/components/manga/MdDetailModal.vue'
import Icon from '@/components/ui/Icon.vue'

const store = useMangadexStore()

const TABS = [
  { id: 'followed', label: 'Popular' },
  { id: 'latest', label: 'Recientes' },
  { id: 'rating', label: 'Mejor valorados' },
]
const RATINGS = [
  { id: 'safe', label: 'Seguro' },
  { id: 'suggestive', label: '16+' },
  { id: 'erotica', label: '18+' },
]
const canMore = computed(() => store.tab !== 'search' && store.popular.length < store.total)

onMounted(() => {
  store.checkAuth()
  store.loadTags()
  if (!store.popular.length) store.loadPopular('followed', 1)
})
</script>

<template>
  <div class="md">
    <header class="md__head">
      <div>
        <p class="eyebrow"><span class="tick" /> EXPLORA MANGADEX</p>
        <h1>MangaDex</h1>
      </div>
      <label class="searchbox">
        <Icon name="search" :size="16" />
        <input v-model="store.query" @keyup.enter="store.search()" type="search" placeholder="Buscar manga…" />
      </label>
    </header>

    <div class="md__bar">
      <div class="tabs">
        <button v-for="t in TABS" :key="t.id" class="tab" :class="{ 'is-active': store.tab === t.id }" @click="store.setTab(t.id)">{{ t.label }}</button>
        <button v-if="store.tab === 'search'" class="tab is-active">Resultados</button>
      </div>
      <div class="md__filters">
        <button v-for="r in RATINGS" :key="r.id" class="rpill" :class="{ 'is-active': store.ratings.includes(r.id) }" @click="store.toggleRating(r.id)">{{ r.label }}</button>
        <button class="rpill" :class="{ 'is-active': store.showTagFilter }" @click="store.showTagFilter = !store.showTagFilter"><Icon name="spark" :size="13" /> Géneros</button>
      </div>
    </div>

    <div v-if="store.showTagFilter && store.allTags.length" class="tags">
      <button v-for="t in store.allTags" :key="t.id" class="tagchip" :class="{ 'is-active': store.selectedTags.includes(t.id) }" @click="store.toggleTag(t.id)">{{ t.name }}</button>
    </div>

    <div v-if="store.loading && !store.list.length" class="grid">
      <div v-for="n in 12" :key="n" class="skeleton" />
    </div>
    <div v-else-if="!store.list.length" class="empty"><Icon name="search" :size="34" /><p>{{ store.tab === 'search' ? 'Sin resultados.' : 'Nada que mostrar.' }}</p></div>
    <template v-else>
      <div class="grid">
        <MdCard v-for="m in store.list" :key="m.id" :manga="m" :score="store.score(m)" @open="store.openDetail($event)" />
      </div>
      <div v-if="canMore" class="more">
        <button class="morebtn" :disabled="store.loading" @click="store.loadPopular(store.tab, store.page + 1)">
          {{ store.loading ? 'Cargando…' : 'Cargar más' }}
        </button>
      </div>
    </template>

    <MdDetailModal />
  </div>
</template>

<style scoped>
.md { max-width: var(--content-max); margin: 0 auto; padding: 0 var(--s-6) var(--s-8); }
.md__head { display: flex; align-items: flex-end; justify-content: space-between; flex-wrap: wrap; gap: var(--s-4); padding: var(--s-5) 0; }
.eyebrow { display: flex; align-items: center; gap: var(--s-2); font-family: var(--font-mono); font-size: var(--fs-2xs); letter-spacing: var(--tracking-caps); color: var(--azure); margin-bottom: var(--s-2); }
.tick { width: 14px; height: 1px; background: var(--azure); box-shadow: 0 0 8px var(--azure-glow); }
.searchbox { display: flex; align-items: center; gap: var(--s-2); padding: var(--s-2) var(--s-3); width: min(300px, 50vw); background: var(--surface); border: 1px solid var(--line); border-radius: var(--r-md); color: var(--ink-faint); transition: border-color var(--t-fast), box-shadow var(--t-fast); }
.searchbox:focus-within { border-color: var(--azure); box-shadow: 0 0 0 3px var(--azure-haze); }
.searchbox input { flex: 1; border: none; outline: none; background: none; color: var(--ink); font-size: var(--fs-sm); }

.md__bar { display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: var(--s-3); margin-bottom: var(--s-4); }
.tabs { display: flex; gap: 2px; padding: 3px; border-radius: var(--r-md); background: var(--surface); border: 1px solid var(--line); }
.tab { padding: 7px 14px; border-radius: var(--r-sm); font-size: var(--fs-sm); font-weight: 500; color: var(--ink-faint); transition: all var(--t-fast); }
.tab:hover { color: var(--ink); }
.tab.is-active { background: var(--azure-haze); color: var(--azure-bright); }
.md__filters { display: flex; gap: var(--s-2); flex-wrap: wrap; }
.rpill { display: inline-flex; align-items: center; gap: 5px; padding: 6px 12px; border-radius: var(--r-pill); font-size: var(--fs-xs); color: var(--ink-soft); border: 1px solid var(--line); transition: all var(--t-fast); }
.rpill:hover { color: var(--ink); border-color: var(--line-strong); }
.rpill.is-active { background: var(--azure-haze); border-color: var(--azure); color: var(--azure-bright); }

.tags { display: flex; flex-wrap: wrap; gap: var(--s-2); margin-bottom: var(--s-5); max-height: 140px; overflow-y: auto; padding: var(--s-1); }
.tagchip { padding: 4px 10px; border-radius: var(--r-pill); font-size: var(--fs-xs); color: var(--ink-soft); border: 1px solid var(--line); transition: all var(--t-fast); }
.tagchip:hover { color: var(--ink); border-color: var(--line-strong); }
.tagchip.is-active { background: var(--azure); color: #fff; border-color: transparent; }

.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(180px, 1fr)); gap: var(--s-5); }
.skeleton { aspect-ratio: 2/3; border-radius: var(--r-md); background: linear-gradient(100deg, var(--surface) 30%, var(--surface-2) 50%, var(--surface) 70%); background-size: 200% 100%; animation: shimmer 1.4s linear infinite; }
.empty { display: flex; flex-direction: column; align-items: center; gap: var(--s-3); padding: var(--s-8) 0; color: var(--ink-faint); }
.more { display: grid; place-items: center; padding: var(--s-6) 0; }
.morebtn { padding: var(--s-3) var(--s-6); border-radius: var(--r-pill); background: var(--surface); border: 1px solid var(--line-2); color: var(--ink-soft); font-weight: 600; font-size: var(--fs-sm); transition: all var(--t-fast); }
.morebtn:hover { color: var(--ink); border-color: var(--azure); }

@media (max-width: 540px) { .md { padding: 0 var(--s-4) var(--s-8); } .grid { grid-template-columns: repeat(auto-fill, minmax(140px, 1fr)); } }
</style>
