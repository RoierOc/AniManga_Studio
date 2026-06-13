<script setup>
import { computed, onMounted, onUnmounted } from 'vue'
import { useUiStore, VIEWS } from '@/stores/ui'
import { useMangaStore } from '@/stores/manga'
import Sidebar from '@/components/layout/Sidebar.vue'
import TopBar from '@/components/layout/TopBar.vue'
import Toaster from '@/components/ui/Toaster.vue'
import TaskQueue from '@/components/ui/TaskQueue.vue'
import ShortcutsModal from '@/components/ui/ShortcutsModal.vue'
import MangaModal from '@/components/manga/MangaModal.vue'
import Reader from '@/components/manga/Reader.vue'
import LibraryView from '@/views/LibraryView.vue'
import MangaDexView from '@/views/MangaDexView.vue'
import FollowedView from '@/views/FollowedView.vue'
import SourcesView from '@/views/SourcesView.vue'
import LocalView from '@/views/LocalView.vue'
import AnimeStudio from '@/views/anime/AnimeStudio.vue'
import PlaceholderView from '@/views/PlaceholderView.vue'

const ui = useUiStore()
const manga = useMangaStore()

function onGlobalKey(e) {
  const tag = (e.target?.tagName || '').toLowerCase()
  if (tag === 'input' || tag === 'textarea' || tag === 'select') return
  if (e.key === '?') { e.preventDefault(); ui.showShortcuts = !ui.showShortcuts }
  else if (e.key === 'Escape' && ui.showShortcuts) ui.showShortcuts = false
}
onMounted(() => { manga.init(); window.addEventListener('keydown', onGlobalKey) })
onUnmounted(() => window.removeEventListener('keydown', onGlobalKey))

// Every sidebar view has a real component below; the PlaceholderView is only a
// defensive fallback and should never render in normal use.
const meta = computed(() => VIEWS.flatMap(g => g.items).find(i => i.id === ui.currentView))

const VIEW_COMPONENTS = { library: LibraryView, mangadex: MangaDexView, followed: FollowedView, sources: SourcesView, local: LocalView, anime: AnimeStudio }
const activeComponent = computed(() => VIEW_COMPONENTS[ui.currentView] || null)
</script>

<template>
  <div class="shell">
    <Sidebar />
    <div
      v-if="ui.sidebarMobileOpen"
      class="scrim-mobile"
      @click="ui.sidebarMobileOpen = false"
    />

    <div class="shell__main">
      <TopBar />
      <main class="shell__content">
        <Transition name="view" mode="out-in">
          <component :is="activeComponent" v-if="activeComponent" :key="ui.currentView" />
          <PlaceholderView v-else :key="ui.currentView" :label="meta?.label" :icon="meta?.icon" />
        </Transition>
      </main>
    </div>

    <MangaModal />
    <Reader />
    <TaskQueue />
    <ShortcutsModal />
    <Toaster />
  </div>
</template>

<style scoped>
.shell { display: flex; min-height: 100vh; }
.shell__main { flex: 1; min-width: 0; display: flex; flex-direction: column; }
.shell__content { flex: 1; }

.scrim-mobile {
  position: fixed; inset: 0; z-index: 35;
  background: rgba(7, 10, 18, 0.6); backdrop-filter: blur(2px);
  animation: fade var(--t-base);
}

/* view crossfade + lift */
.view-enter-active { transition: all var(--t-slow) var(--ease-silk); }
.view-leave-active { transition: all var(--t-fast) var(--ease-silk); }
.view-enter-from { opacity: 0; transform: translateY(10px); }
.view-leave-to   { opacity: 0; transform: translateY(-6px); }
</style>
