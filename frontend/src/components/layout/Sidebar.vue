<script setup>
import { useUiStore, VIEWS } from '@/stores/ui'
import { useAnimeStore } from '@/stores/anime'
import Icon from '@/components/ui/Icon.vue'
const ui = useUiStore()
const anime = useAnimeStore()

function select(item) {
  if (item.sub) {                       // anime sub-view
    anime.closeDetail(); anime.closeTorrents()
    anime.sub = item.sub
    anime.persist?.()
  }
  ui.goto(item.id)
}
const isActive = (item) =>
  ui.currentView === item.id && (!item.sub || anime.sub === item.sub)
</script>

<template>
  <aside class="sidebar" :class="{ 'is-collapsed': ui.sidebarCollapsed, 'is-mobile-open': ui.sidebarMobileOpen }">
    <!-- Brand -->
    <div class="brand">
      <div class="brand__mark">
        <span class="brand__kanji jp">愛</span>
        <span class="brand__pulse" />
      </div>
      <div class="brand__text">
        <span class="brand__name">AniManga</span>
        <span class="brand__sub">STUDIO</span>
      </div>
      <button class="brand__collapse" @click="ui.toggleSidebar()" :title="ui.sidebarCollapsed ? 'Expandir' : 'Colapsar'">
        <Icon name="chevron" :size="16" :style="{ transform: ui.sidebarCollapsed ? 'none' : 'rotate(180deg)' }" />
      </button>
    </div>

    <!-- Nav -->
    <nav class="nav">
      <div v-for="g in VIEWS" :key="g.group" class="nav__group">
        <span class="nav__label">{{ g.group }}</span>
        <button
          v-for="item in g.items" :key="item.id + (item.sub || '')"
          class="nav__item" :class="{ 'is-active': isActive(item) }"
          @click="select(item)" :title="item.label"
        >
          <span class="nav__rail" />
          <Icon :name="item.icon" :size="19" class="nav__icon" />
          <span class="nav__name">{{ item.label }}</span>
        </button>
      </div>
    </nav>

    <!-- Footer status -->
    <div class="sidebar__foot">
      <div v-if="ui.hiddenModeActive" class="status-chip status-chip--hid" title="Estás en la biblioteca oculta. Vuelve a la principal desde Ajustes.">
        <span class="status-chip__dot" />
        <span class="status-chip__text">Biblioteca oculta</span>
      </div>
      <div v-else class="status-chip">
        <span class="status-chip__dot" />
        <span class="status-chip__text">Conectado</span>
      </div>
    </div>
  </aside>
</template>

<style scoped>
.sidebar {
  position: sticky; top: var(--titlebar-h);
  height: calc(100vh - var(--titlebar-h));
  width: var(--sidebar-w);
  flex-shrink: 0;
  display: flex; flex-direction: column;
  background: linear-gradient(180deg, var(--surface) 0%, var(--base) 100%);
  border-right: 1px solid var(--line);
  z-index: var(--z-sidebar);
  transition: width var(--t-base) var(--ease-silk);
}
.sidebar.is-collapsed { width: var(--sidebar-w-collapsed); }

/* ── Brand ────────────────────────────────────────────────────────────── */
.brand {
  display: flex; align-items: center; gap: var(--s-3);
  padding: var(--s-5) var(--s-4);
  height: var(--topbar-h);
}
.brand__mark {
  position: relative;
  width: 38px; height: 38px; flex-shrink: 0;
  display: grid; place-items: center;
  border-radius: var(--r-sm);
  background: radial-gradient(circle at 30% 25%, var(--azure) 0%, var(--azure-ink) 90%);
  box-shadow: var(--glow-azure);
}
.brand__kanji { font-size: 20px; color: #fff; font-weight: 700; }
.brand__pulse {
  position: absolute; inset: -3px;
  border-radius: inherit;
  border: 1px solid var(--azure-glow);
  animation: pulse-live 3s var(--ease-drift) infinite;
}
.brand__text { display: flex; flex-direction: column; line-height: 1; overflow: hidden; }
.brand__name { font-family: var(--font-display); font-weight: 600; font-size: 1.05rem; letter-spacing: -0.01em; white-space: nowrap; }
.brand__sub  { font-family: var(--font-mono); font-size: var(--fs-2xs); color: var(--azure); letter-spacing: var(--tracking-caps); }
.brand__collapse {
  margin-left: auto; width: 28px; height: 28px; flex-shrink: 0;
  display: grid; place-items: center; color: var(--ink-faint);
  border-radius: var(--r-xs); border: 1px solid var(--line);
  transition: all var(--t-fast) var(--ease-silk);
}
.brand__collapse:hover { color: var(--ink); border-color: var(--line-strong); background: var(--surface-2); }
.is-collapsed .brand__text { display: none; }
.is-collapsed .brand { flex-direction: column; gap: var(--s-2); padding: var(--s-3) var(--s-3); height: auto; align-items: center; }
.is-collapsed .brand__collapse { margin-left: 0; }

/* ── Nav ──────────────────────────────────────────────────────────────── */
.nav { flex: 1; padding: var(--s-4) var(--s-3); overflow-y: auto; }
.nav__group { margin-bottom: var(--s-5); }
.nav__label {
  display: block;
  padding: 0 var(--s-3) var(--s-2);
  font-family: var(--font-mono);
  font-size: var(--fs-2xs);
  letter-spacing: var(--tracking-caps);
  text-transform: uppercase;
  color: var(--ink-ghost);
}
.is-collapsed .nav__label { opacity: 0; height: 8px; }

.nav__item {
  position: relative;
  display: flex; align-items: center; gap: var(--s-3);
  width: 100%;
  padding: var(--s-3);
  margin-bottom: 2px;
  border-radius: var(--r-sm);
  color: var(--ink-soft);
  transition: color var(--t-fast) var(--ease-silk), background var(--t-fast);
}
.nav__rail {
  position: absolute; left: -3px; top: 50%; transform: translateY(-50%) scaleY(0);
  width: 3px; height: 20px; border-radius: var(--r-pill);
  background: var(--azure); box-shadow: 0 0 12px var(--azure-glow);
  transition: transform var(--t-base) var(--ease-snap);
}
.nav__icon { flex-shrink: 0; transition: transform var(--t-base) var(--ease-snap); }
.nav__name { font-size: var(--fs-sm); font-weight: 500; white-space: nowrap; }
.is-collapsed .nav__name { display: none; }

.nav__item:hover { color: var(--ink); background: var(--surface-2); }
.nav__item:hover .nav__icon { transform: translateX(1px) scale(1.06); }
.nav__item.is-active { color: var(--ink); background: var(--azure-haze); }
.nav__item.is-active .nav__rail { transform: translateY(-50%) scaleY(1); }
.nav__item.is-active .nav__icon { color: var(--azure-bright); }

/* ── Footer ───────────────────────────────────────────────────────────── */
.sidebar__foot { padding: var(--s-4); border-top: 1px solid var(--line); }
.status-chip {
  display: flex; align-items: center; gap: var(--s-2);
  font-size: var(--fs-xs); color: var(--ink-faint);
}
.status-chip__dot {
  width: 7px; height: 7px; border-radius: 50%;
  background: var(--live); box-shadow: var(--glow-cyan);
  animation: pulse-live 2.4s var(--ease-drift) infinite;
}
.is-collapsed .status-chip__text { display: none; }
.status-chip--hid { color: var(--violet); font-weight: 600; }
.status-chip--hid .status-chip__dot { background: var(--violet); box-shadow: 0 0 8px color-mix(in srgb, var(--violet) 65%, transparent); }

/* ── Mobile ───────────────────────────────────────────────────────────── */
@media (max-width: 860px) {
  .sidebar {
    position: fixed; left: 0; top: 0;
    transform: translateX(-100%);
    transition: transform var(--t-base) var(--ease-silk);
    box-shadow: var(--shadow-xl);
  }
  .sidebar.is-mobile-open { transform: translateX(0); }
}
</style>
