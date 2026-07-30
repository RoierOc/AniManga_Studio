<script setup>
import { computed, ref } from 'vue'
import { useUiStore, VIEWS } from '@/stores/ui'
import { useAnimeStore } from '@/stores/anime'
import Icon from '@/components/ui/Icon.vue'
import ActivityIndicator from '@/components/ui/ActivityIndicator.vue'
import StatsPanel from '@/components/layout/StatsPanel.vue'

const ui = useUiStore()
const anime = useAnimeStore()
const statsOpen = ref(false)
const current = computed(() => {
  if (ui.currentView === 'settings') return { label: 'Ajustes', icon: 'settings' }
  if (ui.currentView === 'workshop') return { label: 'Importar', icon: 'upload' }   // se abre desde Biblioteca
  if (ui.currentView === 'kitchen') return { label: 'Cocina del diseño', icon: 'palette' }   // desde Ajustes
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
      <div class="topbar__stats-wrap">
        <button
          class="topbar__gear"
          :class="{ 'is-active': statsOpen }"
          data-tip="Estadísticas"
          @click.stop="statsOpen = !statsOpen"
        >
          <Icon name="chart" :size="18" />
        </button>
        <StatsPanel v-if="statsOpen" @close="statsOpen = false" />
      </div>
      <!-- Los atajos existían y eran invisibles: nada anunciaba que `?` abre el panel. -->
      <button
        class="topbar__gear topbar__kbd"
        :class="{ 'is-active': ui.showShortcuts }"
        data-tip="Atajos de teclado (?)"
        aria-label="Atajos de teclado"
        @click="ui.showShortcuts = !ui.showShortcuts"
      >?</button>
      <button
        class="topbar__gear"
        :class="{ 'is-active': ui.currentView === 'settings' }"
        data-tip="Ajustes"
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
  /* Opaca hasta abajo: el degradado a `transparent` dejaba el último 40% sin fondo, así que lo que
     scrolleaba por debajo (filas de episodio, hero) se leía ENCIMA de la barra y colisionaba con
     los botones de la ventana. El difuminado se conserva sólo como filo inferior. */
  background: var(--base);
  box-shadow: 0 8px 12px -8px var(--base);
  backdrop-filter: blur(8px);
  -webkit-backdrop-filter: blur(8px);
}
.topbar__burger {
  display: none; color: var(--ink-soft);
  width: 2.375rem; height: 2.375rem; place-items: center;
  border-radius: var(--r-sm); border: 1px solid var(--line);
}
.topbar__title { display: flex; align-items: center; gap: var(--s-3); }
.topbar__title-icon { color: var(--azure); }
.topbar__title h2 { font-size: var(--fs-lg); font-weight: 600; }

.topbar__actions { margin-left: auto; display: flex; align-items: center; gap: var(--s-2); }
.topbar__stats-wrap { position: relative; display: flex; }
.topbar__gear {
  color: var(--ink-faint);
  width: 2.375rem; height: 2.375rem; display: grid; place-items: center;
  border-radius: var(--r-sm); border: 1px solid transparent;
  transition: all var(--t-fast);
}
.topbar__gear:hover { color: var(--ink); border-color: var(--line); }
.topbar__kbd { font-family: var(--font-mono); font-size: var(--fs-md); font-weight: 700; }
.topbar__gear.is-active { color: var(--azure-bright); border-color: var(--azure); background: var(--azure-haze); }

@media (max-width: 860px) {
  .topbar { padding: 0 var(--s-4); }
  .topbar__burger { display: grid; }
}
</style>
