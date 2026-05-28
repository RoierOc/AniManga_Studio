<script setup>
import { useUiStore } from '@/stores/ui'
import Icon from './Icon.vue'
const ui = useUiStore()

const READER = [
  { k: ['←', '→'], d: 'Página anterior / siguiente' },
  { k: ['Espacio'], d: 'Siguiente página' },
  { k: ['[', ']'], d: 'Capítulo anterior / siguiente' },
  { k: ['f'], d: 'Cambiar ajuste de imagen' },
  { k: ['w'], d: 'Modo paginado / webtoon' },
  { k: ['d'], d: 'Dirección LTR / RTL' },
  { k: ['c'], d: 'Comparar original / 4K' },
  { k: ['rueda'], d: 'Zoom (arrastra para mover)' },
  { k: ['Esc'], d: 'Cerrar lector' },
]
const GLOBAL = [
  { k: ['?'], d: 'Mostrar / ocultar este panel' },
  { k: ['Esc'], d: 'Cerrar panel activo' },
]
</script>

<template>
  <Teleport to="body">
    <Transition name="modal">
      <div v-if="ui.showShortcuts" class="ov" @click.self="ui.showShortcuts = false">
        <div class="sc">
          <button class="sc__x" @click="ui.showShortcuts = false"><Icon name="close" :size="18" /></button>
          <h2 class="sc__title">Atajos de teclado</h2>
          <div class="sc__cols">
            <div>
              <h3 class="sc__h">Lector de manga</h3>
              <div v-for="(r, i) in READER" :key="i" class="sc__row"><span class="sc__keys"><kbd v-for="k in r.k" :key="k">{{ k }}</kbd></span><span>{{ r.d }}</span></div>
            </div>
            <div>
              <h3 class="sc__h">Global</h3>
              <div v-for="(r, i) in GLOBAL" :key="i" class="sc__row"><span class="sc__keys"><kbd v-for="k in r.k" :key="k">{{ k }}</kbd></span><span>{{ r.d }}</span></div>
            </div>
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.ov { position: fixed; inset: 0; z-index: var(--z-modal); display: grid; place-items: center; padding: var(--s-5); background: rgba(7,10,18,.72); backdrop-filter: blur(8px); }
.sc { position: relative; width: min(640px, 100%); background: var(--glass-strong); border: 1px solid var(--line-2); border-radius: var(--r-lg); box-shadow: var(--shadow-xl); padding: var(--s-6); }
.sc__x { position: absolute; top: var(--s-3); right: var(--s-3); width: 32px; height: 32px; display: grid; place-items: center; border-radius: var(--r-sm); color: var(--ink-soft); border: 1px solid var(--line); }
.sc__title { font-size: var(--fs-xl); margin-bottom: var(--s-4); }
.sc__cols { display: grid; grid-template-columns: 1fr 1fr; gap: var(--s-6); }
.sc__h { font-family: var(--font-mono); font-size: var(--fs-2xs); letter-spacing: var(--tracking-caps); text-transform: uppercase; color: var(--azure); margin-bottom: var(--s-3); }
.sc__row { display: flex; align-items: center; gap: var(--s-3); padding: 5px 0; font-size: var(--fs-sm); color: var(--ink-soft); }
.sc__keys { display: flex; gap: 4px; min-width: 90px; }
kbd { font-family: var(--font-mono); font-size: var(--fs-2xs); padding: 2px 7px; border-radius: var(--r-xs); border: 1px solid var(--line-2); color: var(--ink); background: var(--surface); }
.modal-enter-active, .modal-leave-active { transition: opacity var(--t-base); }
.modal-enter-from, .modal-leave-to { opacity: 0; }
@media (max-width: 540px) { .sc__cols { grid-template-columns: 1fr; } }
</style>
