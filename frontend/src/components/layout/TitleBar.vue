<script setup>
// Barra de título PROPIA para la shell nativa de Windows (WebView2 sin marco).
// Sustituye a la barra de título del sistema por una integrada con "Midnight
// Atelier": misma marca (青 AniManga Studio) + controles minimizar/maximizar/
// cerrar al estilo Windows. Solo se monta cuando corremos dentro de la shell
// nativa (App.vue la envuelve en v-if="isNative()"), así el player web no la ve.
import { ref, onMounted, onUnmounted } from 'vue'
import { windowControl, onMessage } from '@/lib/nativeBridge'

const maximized = ref(false)

// Rust nos avisa al maximizar/restaurar (también por doble-clic o Win+↑) para que
// el icono del botón central alterne entre "maximizar" y "restaurar".
let off = null
onMounted(() => {
  off = onMessage((d) => {
    if (d?.event === 'windowState') maximized.value = !!d.maximized
  })
})
onUnmounted(() => off && off())

// Arrastre + doble-clic para (des)maximizar sobre la MISMA zona. WebView2 en modo
// composición no soporta -webkit-app-region, así que lo hacemos por IPC: el mousedown
// pide a Rust iniciar el bucle de arrastre nativo (WM_NCLBUTTONDOWN/HTCAPTION).
let lastDown = 0
function onDragDown(e) {
  if (e.button !== 0) return
  const now = Date.now()
  if (now - lastDown < 380) {        // doble-clic → alternar maximizado
    lastDown = 0
    windowControl('toggleMaximize')
    return
  }
  lastDown = now
  windowControl('drag')
}

function minimize() { windowControl('minimize') }
function toggleMax() { windowControl('toggleMaximize') }
function close() { windowControl('close') }
</script>

<template>
  <div class="tb" :class="{ 'is-max': maximized }">
    <!-- Zona arrastrable: marca + relleno. Los controles quedan fuera de ella. -->
    <div class="tb__drag" @mousedown="onDragDown">
      <div class="tb__brand">
        <span class="tb__mark"><span class="tb__kanji">青</span></span>
        <span class="tb__word">AniManga<b>Studio</b></span>
      </div>
    </div>

    <div class="tb__ctrls">
      <button class="tb__btn" title="Minimizar" @click="minimize">
        <svg viewBox="0 0 12 12" width="12" height="12" aria-hidden="true">
          <path d="M2 6 H10" />
        </svg>
      </button>
      <button class="tb__btn" :title="maximized ? 'Restaurar' : 'Maximizar'" @click="toggleMax">
        <svg v-if="!maximized" viewBox="0 0 12 12" width="12" height="12" aria-hidden="true">
          <rect x="2.2" y="2.2" width="7.6" height="7.6" rx="1" />
        </svg>
        <svg v-else viewBox="0 0 12 12" width="12" height="12" aria-hidden="true">
          <rect x="2.2" y="3.6" width="6.2" height="6.2" rx="1" />
          <path d="M4.4 3.6 V2.2 H10.6 V8.4 H9.2" />
        </svg>
      </button>
      <button class="tb__btn tb__btn--close" title="Cerrar" @click="close">
        <svg viewBox="0 0 12 12" width="12" height="12" aria-hidden="true">
          <path d="M2.4 2.4 L9.6 9.6 M9.6 2.4 L2.4 9.6" />
        </svg>
      </button>
    </div>
  </div>
</template>

<style scoped>
.tb {
  position: fixed; top: 0; left: 0; right: 0;
  height: var(--titlebar-h);
  z-index: var(--z-titlebar);
  display: flex; align-items: stretch;
  background: linear-gradient(180deg, var(--void) 0%, var(--base) 100%);
  border-bottom: 1px solid var(--line);
  user-select: none;
  /* Fina línea de acento azul, coherente con el resto de la UI. */
  box-shadow: inset 0 -1px 0 color-mix(in srgb, var(--azure) 10%, transparent);
}

.tb__drag {
  flex: 1; min-width: 0;
  display: flex; align-items: center;
  padding-left: var(--s-4);
  -webkit-app-region: drag; /* por si algún runtime lo respeta; el arrastre real va por IPC */
  cursor: default;
}

.tb__brand { display: flex; align-items: center; gap: var(--s-2); pointer-events: none; }
.tb__mark {
  width: 1.15rem; height: 1.15rem; flex-shrink: 0;
  display: grid; place-items: center; border-radius: 0.3rem;
  background: radial-gradient(circle at 30% 25%, var(--azure) 0%, var(--azure-ink) 92%);
  box-shadow: 0 0 0 1px var(--azure-glow);
}
.tb__kanji {
  font-family: var(--font-jp);
  font-size: 0.78rem; line-height: 1; color: #fff; font-weight: 700;
}
.tb__word {
  font-family: var(--font-display); font-weight: 500;
  font-size: var(--fs-xs); letter-spacing: 0.02em; color: var(--ink-soft);
  white-space: nowrap;
}
.tb__word b { color: var(--ink); font-weight: 600; margin-left: 0.28em; }

.tb__ctrls { display: flex; align-items: stretch; flex-shrink: 0; }
.tb__btn {
  width: 2.9rem; height: 100%;
  display: grid; place-items: center;
  color: var(--ink-faint);
  transition: background var(--t-fast), color var(--t-fast);
}
.tb__btn svg { fill: none; stroke: currentColor; stroke-width: 1.1; stroke-linecap: round; stroke-linejoin: round; }
.tb__btn:hover { background: color-mix(in srgb, var(--ink) 9%, transparent); color: var(--ink); }
.tb__btn:active { background: color-mix(in srgb, var(--ink) 14%, transparent); }
.tb__btn--close:hover { background: var(--coral); color: #fff; }
.tb__btn--close:active { background: color-mix(in srgb, var(--coral) 82%, #000); color: #fff; }
</style>
