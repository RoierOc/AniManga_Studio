<script setup>
import { computed, defineAsyncComponent, onErrorCaptured, ref, watch, onMounted, onUnmounted } from 'vue'
import { useUiStore, VIEWS } from '@/stores/ui'
import { useMangaStore } from '@/stores/manga'
import Icon from '@/components/ui/Icon.vue'
import Sidebar from '@/components/layout/Sidebar.vue'
import TopBar from '@/components/layout/TopBar.vue'
import Toaster from '@/components/ui/Toaster.vue'
import TaskQueue from '@/components/ui/TaskQueue.vue'
import ShortcutsModal from '@/components/ui/ShortcutsModal.vue'
import MangaModal from '@/components/manga/MangaModal.vue'
import Reader from '@/components/manga/Reader.vue'
import PlaceholderView from '@/views/PlaceholderView.vue'

// Views are code-split into their own chunks (loaded on demand) to shrink the initial
// bundle — the Anime Studio especially pulls in a lot. LibraryView stays eager since it
// is the most common landing view.
import LibraryView from '@/views/LibraryView.vue'
const MangaDexView = defineAsyncComponent(() => import('@/views/MangaDexView.vue'))
const SourcesView  = defineAsyncComponent(() => import('@/views/SourcesView.vue'))
const LocalView    = defineAsyncComponent(() => import('@/views/LocalView.vue'))
const AnimeStudio  = defineAsyncComponent(() => import('@/views/anime/AnimeStudio.vue'))
const SettingsView = defineAsyncComponent(() => import('@/views/SettingsView.vue'))

const ui = useUiStore()
const manga = useMangaStore()

function onGlobalKey(e) {
  const tag = (e.target?.tagName || '').toLowerCase()
  if (tag === 'input' || tag === 'textarea' || tag === 'select') return
  if (e.key === '?') { e.preventDefault(); ui.showShortcuts = !ui.showShortcuts }
  else if (e.key === 'Escape' && ui.showShortcuts) ui.showShortcuts = false
}
onMounted(() => {
  ui.initNav()   // capture the landing view as the first history entry (before any nav),
                 // so browser back/forward traverses the whole app, not just details.
  manga.init()
  window.addEventListener('keydown', onGlobalKey)
})
onUnmounted(() => window.removeEventListener('keydown', onGlobalKey))

// Every sidebar view has a real component below; the PlaceholderView is only a
// defensive fallback and should never render in normal use.
const meta = computed(() => VIEWS.flatMap(g => g.items).find(i => i.id === ui.currentView))

const VIEW_COMPONENTS = { library: LibraryView, mangadex: MangaDexView, sources: SourcesView, local: LocalView, anime: AnimeStudio, settings: SettingsView }
const activeComponent = computed(() => VIEW_COMPONENTS[ui.currentView] || null)

// Error boundary: a render error in any view/modal shows a recoverable panel instead of
// a blank white screen. "Reintentar" remounts the view; "Recargar" does a hard reload.
const crash = ref(null)
const crashKey = ref(0)
onErrorCaptured((err, _inst, info) => {
  console.error('[boundary]', info, err)
  crash.value = { message: String(err?.message || err), info }
  return false   // stop propagation
})
function retry() {
  crash.value = null
  crashKey.value++   // force a fresh remount of the content
}
function reload() { window.location.reload() }
// Navigating away from a broken view clears the error.
watch(() => ui.currentView, () => { crash.value = null })
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
        <div v-if="crash" class="crash">
          <div class="crash__glyph"><Icon name="spark" :size="38" /></div>
          <h2>Algo salió mal en esta vista</h2>
          <p class="crash__msg">{{ crash.message }}</p>
          <div class="crash__actions">
            <button class="crash__btn crash__btn--accent" @click="retry"><Icon name="spark" :size="14" /> Reintentar</button>
            <button class="crash__btn" @click="reload">Recargar la app</button>
          </div>
          <p class="crash__hint">O elige otra sección en la barra lateral.</p>
        </div>
        <Transition v-else name="view" mode="out-in">
          <component :is="activeComponent" v-if="activeComponent" :key="ui.currentView + '_' + crashKey" />
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

/* error boundary fallback */
.crash { display: flex; flex-direction: column; align-items: center; justify-content: center; min-height: 70vh; text-align: center; gap: var(--s-3); padding: var(--s-6); }
.crash__glyph { width: 84px; height: 84px; display: grid; place-items: center; border-radius: var(--r-lg); color: var(--coral); background: color-mix(in srgb, var(--coral) 12%, transparent); border: 1px solid color-mix(in srgb, var(--coral) 30%, transparent); }
.crash h2 { font-size: var(--fs-2xl); }
.crash__msg { color: var(--ink-faint); font-family: var(--font-mono); font-size: var(--fs-xs); max-width: 560px; word-break: break-word; }
.crash__actions { display: flex; gap: var(--s-3); margin-top: var(--s-2); }
.crash__btn { display: inline-flex; align-items: center; gap: 6px; padding: var(--s-3) var(--s-5); border-radius: var(--r-md); font-size: var(--fs-sm); font-weight: 600; color: var(--ink-soft); border: 1px solid var(--line-2); transition: all var(--t-fast); }
.crash__btn:hover { color: var(--ink); border-color: var(--line-strong); }
.crash__btn--accent { background: var(--azure); color: #fff; border-color: transparent; }
.crash__btn--accent:hover { background: var(--azure-bright); color: #fff; }
.crash__hint { color: var(--ink-ghost); font-size: var(--fs-xs); margin-top: var(--s-2); }

/* view crossfade + lift */
.view-enter-active { transition: all var(--t-slow) var(--ease-silk); }
.view-leave-active { transition: all var(--t-fast) var(--ease-silk); }
.view-enter-from { opacity: 0; transform: translateY(10px); }
.view-leave-to   { opacity: 0; transform: translateY(-6px); }
</style>
