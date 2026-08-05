<script setup>
import { computed } from 'vue'
import { useUiStore, VIEWS, MODES } from '@/stores/ui'
import { useAnimeStore } from '@/stores/anime'
import { useMediaStore } from '@/stores/media'
import { sseState } from '@/lib/sse'
import Icon from '@/components/ui/Icon.vue'
const ui = useUiStore()

const CONN = {
  live:       { tone: 'ok',   label: 'Conectado',    hint: 'El servidor responde y envía novedades en vivo.' },
  connecting: { tone: 'wait', label: 'Conectando…',  hint: 'Restableciendo la conexión con el servidor.' },
  down:       { tone: 'bad',  label: 'Sin conexión', hint: 'El servidor no responde: lo que veas puede estar desactualizado.' },
}
const conn = computed(() => CONN[sseState.value] || CONN.connecting)
const anime = useAnimeStore()
const media = useMediaStore()

// Solo los grupos del modo activo (+ los transversales `both`): el sidebar entero se reconfigura
// al conmutar, dando la sensación de dos apps dentro de una.
const groups = computed(() => VIEWS.filter((g) => g.mode === 'both' || g.mode === ui.mode))

// Vistas con sub-pestañas propias: el sidebar necesita saber a QUÉ store cambiarle la sub-vista.
// Antes esto era un `if` que solo contemplaba anime, así que añadir otra sección obligaba a
// tocar aquí; con el mapa, la siguiente solo se registra.
const SUBBED = {
  anime: {
    set: (sub) => { anime.closeDetail(); anime.closeTorrents(); anime.sub = sub; anime.persist?.() },
    get: () => anime.sub,
  },
  media: {
    // Se escribe la pestaña a pelo, sin `setSub`: ese empuja entrada de historial y el `ui.goto`
    // de abajo empuja otra, así que UN clic en el sidebar dejaba DOS entradas idénticas y el
    // botón de atrás parecía no hacer nada. El `goto` ya recoge la pestaña en su snapshot.
    set: (sub) => { media.closeDetail(); ui.tabs.media = sub },
    get: () => media.sub,
  },
}

function select(item, group) {
  if (item.sub) SUBBED[item.id]?.set(item.sub)
  // Un ítem de un modo concreto (no transversal) fija el "home" de ese modo: al conmutar vuelves aquí.
  if (group?.mode === 'anime' || group?.mode === 'cine') ui.setModeHome(group.mode, item.id)
  ui.goto(item.id)
}
const isActive = (item) =>
  ui.currentView === item.id && (!item.sub || SUBBED[item.id]?.get() === item.sub)
</script>

<template>
  <aside class="sidebar" :class="{ 'is-collapsed': ui.sidebarCollapsed, 'is-mobile-open': ui.sidebarMobileOpen }">
    <!-- Brand -->
    <div class="brand">
      <!-- La marca es el atajo al Inicio, como el logotipo de cualquier sitio. No abre un menú ni
           despliega nada: un clic, la puerta de la app. -->
      <button class="brand__home" data-tip="Ir al inicio" aria-label="Ir al inicio" @click="ui.goto('home')">
        <span class="brand__mark">
          <span class="brand__kanji jp">青</span>
          <span class="brand__pulse" />
        </span>
        <span class="brand__text">
          <span class="brand__name">AniManga</span>
          <span class="brand__sub">STUDIO</span>
        </span>
      </button>
      <button class="brand__collapse" @click="ui.toggleSidebar()" :data-tip="ui.sidebarCollapsed ? 'Expandir' : 'Colapsar'">
        <Icon name="chevron" :size="16" :style="{ transform: ui.sidebarCollapsed ? 'none' : 'rotate(180deg)' }" />
      </button>
    </div>

    <!-- Conmutador de modo: reconfigura toda la navegación de abajo (アニメ ⇄ Cine). -->
    <div class="modesw" :class="{ 'is-cine': ui.mode === 'cine' }" role="tablist" aria-label="Modo">
      <span class="modesw__thumb" :style="{ transform: ui.mode === 'cine' ? 'translateX(100%)' : 'none' }" />
      <button v-for="m in MODES" :key="m.id" class="modesw__opt" :class="{ 'is-on': ui.mode === m.id }"
              role="tab" :aria-selected="ui.mode === m.id" @click="ui.switchMode(m.id)">{{ m.label }}</button>
    </div>

    <!-- Nav -->
    <nav class="nav">
      <div v-for="g in groups" :key="g.group" class="nav__group">
        <!-- Inicio no lleva epígrafe (`group: ''`): es la puerta de la app, no una sección.
             Sin el `v-if` quedaba una franja de aire vacía encima de ella. -->
        <span v-if="g.group" class="nav__label">{{ g.group }}</span>
        <!-- `data-tip` sólo plegado: con el sidebar abierto la burbuja repetía la etiqueta que
             está ahí al lado Y tapaba el ítem de debajo. Un tooltip que no añade nada es ruido
             que además esconde el siguiente destino. -->
        <button
          v-for="item in g.items" :key="item.id + (item.sub || '')"
          class="nav__item" :class="{ 'is-active': isActive(item) }"
          @click="select(item, g)"
          :data-tip="ui.sidebarCollapsed ? item.label : null"
        >
          <span class="nav__rail" />
          <Icon :name="item.icon" :size="19" class="nav__icon" />
          <span class="nav__name">{{ item.label }}</span>
        </button>
      </div>
    </nav>

    <!-- Footer status -->
    <div class="sidebar__foot">
      <div v-if="ui.hiddenModeActive" class="status-chip status-chip--hid" data-tip="Estás en la biblioteca oculta. Vuelve a la principal desde Ajustes.">
        <span class="status-chip__dot" />
        <span class="status-chip__text">Biblioteca oculta</span>
      </div>
      <!-- Dice la VERDAD: antes se pintaba "Conectado" siempre, aunque el backend estuviera
           muerto, y era el único indicador de salud de la app. Ahora sigue al EventSource. -->
      <div v-else class="status-chip" :class="`status-chip--${conn.tone}`" :data-tip="conn.hint">
        <span class="status-chip__dot" />
        <span class="status-chip__text">{{ conn.label }}</span>
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
/* El botón sólo aporta el gesto: no pinta caja propia para que la marca se vea igual que antes.
   El realce al pasar por encima es del kanji, que es lo que se lee como logotipo. */
.brand__home {
  display: flex; align-items: center; gap: var(--s-3); min-width: 0;
  text-align: left; border-radius: var(--r-sm);
  transition: transform var(--t-fast) var(--ease-snap);
}
.brand__home:hover .brand__mark { box-shadow: 0 0 0 1px var(--azure-glow), var(--glow-azure); }
.brand__home:active { transform: scale(.97); }
.is-collapsed .brand__home { gap: 0; }
.brand__mark {
  position: relative;
  width: 2.375rem; height: 2.375rem; flex-shrink: 0;
  display: grid; place-items: center;
  border-radius: var(--r-sm);
  background: radial-gradient(circle at 30% 25%, var(--azure) 0%, var(--azure-ink) 90%);
  box-shadow: var(--glow-azure);
}
.brand__kanji { font-size: 1.25rem; color: #fff; font-weight: 700; }
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
  margin-left: auto; width: 1.75rem; height: 1.75rem; flex-shrink: 0;
  display: grid; place-items: center; color: var(--ink-faint);
  border-radius: var(--r-xs); border: 1px solid var(--line);
  transition: all var(--t-fast) var(--ease-silk);
}
.brand__collapse:hover { color: var(--ink); border-color: var(--line-strong); background: var(--surface-2); }
.is-collapsed .brand__text { display: none; }
.is-collapsed .brand { flex-direction: column; gap: var(--s-2); padding: var(--s-3) var(--s-3); height: auto; align-items: center; }
.is-collapsed .brand__collapse { margin-left: 0; }

/* ── Conmutador de modo ───────────────────────────────────────────────── */
.modesw {
  position: relative; display: flex; margin: 0 var(--s-4) var(--s-2);
  padding: 3px; border-radius: var(--r-pill);
  background: var(--surface-2); border: 1px solid var(--line);
}
.modesw__thumb {
  position: absolute; top: 3px; left: 3px; width: calc(50% - 3px); height: calc(100% - 0.375rem);
  border-radius: var(--r-pill); background: var(--azure-haze);
  border: 1px solid color-mix(in srgb, var(--azure) 45%, transparent);
  box-shadow: 0 0 12px var(--azure-glow);
  transition: transform var(--t-base) var(--ease-snap), background var(--t-base), border-color var(--t-base);
}
.modesw__opt {
  position: relative; z-index: 1; flex: 1; padding: var(--s-2) 0;
  font-size: var(--fs-xs); font-weight: 600; color: var(--ink-faint);
  transition: color var(--t-fast);
}
.modesw__opt.is-on { color: var(--ink); }
.modesw__opt:first-child { font-family: var(--font-display); }
/* Colapsado: el segmentado horizontal no cabe → dos etiquetas minúsculas apiladas; la activa
   resaltada, la otra sigue siendo clicable para conmutar. */
.is-collapsed .modesw { flex-direction: column; gap: 2px; padding: 2px; margin: 0 var(--s-2) var(--s-2); }
.is-collapsed .modesw__thumb { display: none; }
.is-collapsed .modesw__opt { padding: var(--s-1) 0; font-size: 0.62rem; border-radius: var(--r-xs); }
.is-collapsed .modesw__opt.is-on { background: var(--azure-haze); }

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
.is-collapsed .nav__label { opacity: 0; height: 0.5rem; }

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
  width: 3px; height: 1.25rem; border-radius: var(--r-pill);
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
  width: 0.4375rem; height: 0.4375rem; border-radius: 50%;
  background: var(--live); box-shadow: var(--glow-cyan);
  animation: pulse-live 2.4s var(--ease-drift) infinite;
}
.is-collapsed .status-chip__text { display: none; }
.status-chip--hid { color: var(--violet); font-weight: 600; }
.status-chip--hid .status-chip__dot { background: var(--violet); box-shadow: 0 0 8px color-mix(in srgb, var(--violet) 65%, transparent); }
/* Reconectando: ámbar y latido rápido. Caído: coral y QUIETO — un punto que late sugiere
   actividad, y ahí justamente no la hay. */
.status-chip--wait { color: var(--warn); }
.status-chip--wait .status-chip__dot { background: var(--warn); box-shadow: 0 0 8px color-mix(in srgb, var(--warn) 60%, transparent); animation-duration: 0.9s; }
.status-chip--bad { color: var(--coral); font-weight: 600; }
.status-chip--bad .status-chip__dot { background: var(--coral); box-shadow: 0 0 8px color-mix(in srgb, var(--coral) 60%, transparent); animation: none; }

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
