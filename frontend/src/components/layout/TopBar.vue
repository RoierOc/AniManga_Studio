<script setup>
import { computed } from 'vue'
import { useUiStore, VIEWS } from '@/stores/ui'
import { useAnimeStore } from '@/stores/anime'
import Icon from '@/components/ui/Icon.vue'

const ui = useUiStore()
const anime = useAnimeStore()
const current = computed(() => {
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

    <div class="topbar__search">
      <Icon name="search" :size="16" />
      <input type="search" placeholder="Buscar en tu universo…" />
      <kbd>/</kbd>
    </div>
  </header>
</template>

<style scoped>
.topbar {
  position: sticky; top: 0;
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

.topbar__search {
  margin-left: auto;
  display: flex; align-items: center; gap: var(--s-2);
  width: min(360px, 38vw);
  padding: var(--s-2) var(--s-3);
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: var(--r-md);
  color: var(--ink-faint);
  transition: border-color var(--t-fast), box-shadow var(--t-fast);
}
.topbar__search:focus-within {
  border-color: var(--azure);
  box-shadow: 0 0 0 3px var(--azure-haze);
}
.topbar__search input {
  flex: 1; border: none; outline: none; background: none;
  color: var(--ink); font-size: var(--fs-sm);
}
.topbar__search input::placeholder { color: var(--ink-ghost); }
.topbar__search kbd {
  font-family: var(--font-mono); font-size: var(--fs-2xs);
  padding: 1px 6px; border-radius: var(--r-xs);
  border: 1px solid var(--line-2); color: var(--ink-faint);
}

@media (max-width: 860px) {
  .topbar { padding: 0 var(--s-4); }
  .topbar__burger { display: grid; }
  .topbar__search { width: 44px; }
  .topbar__search input, .topbar__search kbd { display: none; }
}
</style>
