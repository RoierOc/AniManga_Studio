<script setup>
import { computed } from 'vue'
import { useUiStore, VIEWS } from '@/stores/ui'
import { useAnimeStore } from '@/stores/anime'
import Icon from '@/components/ui/Icon.vue'
import ActivityIndicator from '@/components/ui/ActivityIndicator.vue'

const ui = useUiStore()
const anime = useAnimeStore()
const current = computed(() => {
  if (ui.currentView === 'settings') return { label: 'Ajustes', icon: 'settings' }
  if (ui.currentView === 'workshop') return { label: 'Importar', icon: 'upload' }   // se abre desde Biblioteca
  const items = VIEWS.flatMap(g => g.items)
  if (ui.currentView === 'anime') return items.find(i => i.id === 'anime' && i.sub === anime.sub) || items.find(i => i.id === 'anime')
  return items.find(i => i.id === ui.currentView)
})
</script>

<template>
  <header class="topbar">
    <button class="topbar__burger" @click="ui.sidebarMobileOpen = !ui.sidebarMobileOpen">
      <Icon name="menu" :size="20" />
    </button>

    <div class="topbar__title">
      <Icon v-if="current" :name="current.icon" :size="18" class="topbar__title-icon" />
      <h2>{{ current?.label }}</h2>
    </div>

    <div class="topbar__actions">
      <ActivityIndicator />
      <button
        class="topbar__gear"
        :class="{ 'is-active': ui.currentView === 'settings' }"
        title="Ajustes"
        @click="ui.goto('settings')"
      >
        <Icon name="settings" :size="18" />
      </button>
    </div>
  </header>
</template>

<style scoped>
.topbar {
  position: sticky; top: var(--titlebar-h);
  z-index: var(--z-topbar);
  height: var(--topbar-h);
  display: flex; align-items: center; gap: var(--s-4);
  padding: 0 var(--s-6);
  background: linear-gradient(180deg, var(--base) 60%, transparent);
  backdrop-filter: blur(8px);
  -webkit-backdrop-filter: blur(8px);
}
.topbar__burger {
  display: none; color: var(--ink-soft);
  width: 38px; height: 38px; place-items: center;
  border-radius: var(--r-sm); border: 1px solid var(--line);
}
.topbar__title { display: flex; align-items: center; gap: var(--s-3); }
.topbar__title-icon { color: var(--azure); }
.topbar__title h2 { font-size: var(--fs-lg); font-weight: 600; }

.topbar__actions { margin-left: auto; display: flex; align-items: center; gap: var(--s-2); }
.topbar__gear {
  color: var(--ink-faint);
  width: 38px; height: 38px; display: grid; place-items: center;
  border-radius: var(--r-sm); border: 1px solid transparent;
  transition: all var(--t-fast);
}
.topbar__gear:hover { color: var(--ink); border-color: var(--line); }
.topbar__gear.is-active { color: var(--azure-bright); border-color: var(--azure); background: var(--azure-haze); }

@media (max-width: 860px) {
  .topbar { padding: 0 var(--s-4); }
  .topbar__burger { display: grid; }
}
</style>
