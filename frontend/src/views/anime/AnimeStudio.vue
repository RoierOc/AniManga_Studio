<script setup>
import { onMounted } from 'vue'
import { useAnimeStore } from '@/stores/anime'
import AnimeLibrary from './AnimeLibrary.vue'
import AnimeDetail from './AnimeDetail.vue'
import Downloads from './Downloads.vue'
import History from './History.vue'
import AutoplayModal from '@/components/anime/AutoplayModal.vue'
import PlaceholderView from '@/views/PlaceholderView.vue'
import Icon from '@/components/ui/Icon.vue'

const store = useAnimeStore()

const TABS = [
  { id: 'library',   label: 'Mi Anime',  icon: 'film' },
  { id: 'search',    label: 'Buscar',    icon: 'search' },
  { id: 'seasonal',  label: 'Temporada', icon: 'spark' },
  { id: 'downloads', label: 'Descargas', icon: 'download' },
  { id: 'history',   label: 'Historial', icon: 'heart' },
]

onMounted(() => {
  store.init()
  if (!store.library.length) store.loadLibrary()
})

function selectTab(id) {
  store.closeDetail()
  store.sub = id
}
</script>

<template>
  <div class="studio">
    <!-- Sub-nav (hidden while a detail is open) -->
    <nav v-if="!store.detail" class="subnav">
      <button v-for="t in TABS" :key="t.id" class="subnav__tab" :class="{ 'is-active': store.sub === t.id }"
              @click="selectTab(t.id)">
        <Icon :name="t.icon" :size="16" /> {{ t.label }}
      </button>
    </nav>

    <div class="studio__body">
      <Transition name="swap" mode="out-in">
        <AnimeDetail v-if="store.detail" key="detail" />
        <AnimeLibrary v-else-if="store.sub === 'library'" key="library" />
        <Downloads v-else-if="store.sub === 'downloads'" key="downloads" />
        <History v-else-if="store.sub === 'history'" key="history" />
        <PlaceholderView v-else :key="store.sub"
                         :label="TABS.find(t => t.id === store.sub)?.label"
                         :icon="TABS.find(t => t.id === store.sub)?.icon" />
      </Transition>
    </div>

    <AutoplayModal />
  </div>
</template>

<style scoped>
.studio { width: 100%; }
.subnav {
  display: flex; gap: var(--s-1); flex-wrap: wrap;
  max-width: var(--content-max); margin: 0 auto;
  padding: var(--s-4) var(--s-6) 0;
}
.subnav__tab {
  display: inline-flex; align-items: center; gap: var(--s-2);
  padding: var(--s-2) var(--s-4); border-radius: var(--r-sm);
  font-size: var(--fs-sm); font-weight: 500; color: var(--ink-faint);
  transition: all var(--t-fast) var(--ease-silk);
}
.subnav__tab:hover { color: var(--ink); background: var(--surface); }
.subnav__tab.is-active { color: var(--azure-bright); background: var(--azure-haze); }
.studio__body { padding-bottom: var(--s-8); }

/* the detail/library/placeholder swap */
.swap-enter-active { transition: all var(--t-slow) var(--ease-silk); }
.swap-leave-active { transition: all var(--t-fast) var(--ease-silk); }
.swap-enter-from { opacity: 0; transform: translateY(10px); }
.swap-leave-to { opacity: 0; transform: translateY(-6px); }
</style>
