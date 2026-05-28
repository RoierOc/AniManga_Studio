<script setup>
import { computed } from 'vue'
import { useUiStore, VIEWS } from '@/stores/ui'
import Sidebar from '@/components/layout/Sidebar.vue'
import TopBar from '@/components/layout/TopBar.vue'
import Toaster from '@/components/ui/Toaster.vue'
import LibraryView from '@/views/LibraryView.vue'
import PlaceholderView from '@/views/PlaceholderView.vue'

const ui = useUiStore()

// Map each view id to its component. Only Library is migrated so far;
// the rest render the placeholder until their phase lands.
const meta = computed(() => VIEWS.flatMap(g => g.items).find(i => i.id === ui.currentView))

const VIEW_COMPONENTS = { library: LibraryView }
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
