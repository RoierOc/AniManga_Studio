<script setup>
import { useUiStore } from '@/stores/ui'
import Icon from './Icon.vue'
const ui = useUiStore()

const READER = [
  { k: ['←', '→'], d: 'Página anterior / siguiente' },
  { k: ['Espacio'], d: 'Siguiente página' },
  { k: ['[', ']'], d: 'Capítulo anterior / siguiente (o pulsa «Cap. N» para saltar a otro)' },
  { k: ['m'], d: 'Mosaico: todo el capítulo de un vistazo' },
  { k: ['f'], d: 'Cambiar ajuste de imagen' },
  { k: ['w'], d: 'Modo paginado / webtoon' },
  { k: ['s'], d: 'Doble página' },
  { k: ['o'], d: 'Desfase de la doble página (portada suelta)' },
  { k: ['d'], d: 'Dirección LTR / RTL' },
  { k: ['c'], d: 'Comparar original / 4K' },
  { k: ['a'], d: 'Auto-scroll (modo tira)' },
  { k: ['doble clic'], d: 'Ampliar donde pulsas / restablecer' },
  { k: ['rueda'], d: 'Zoom (arrastra para mover)' },
  { k: ['Esc'], d: 'Cerrar lector' },
]
// El reproductor nativo tenía 10 atajos implementados y CERO documentados: función pagada que
// nadie sabía que existía. Ver `onKey` en NativePlayerOverlay.vue.
const PLAYER = [
  { k: ['Espacio', 'k'], d: 'Pausa / reanudar' },
  { k: ['←', '→'], d: 'Retroceder / avanzar 5 s' },
  { k: ['j', 'l'], d: 'Retroceder / avanzar 10 s' },
  { k: ['↑', '↓'], d: 'Subir / bajar volumen' },
  { k: ['rueda'], d: 'Volumen (hasta 150 %)' },
  { k: ['m'], d: 'Silenciar' },
  { k: ['f'], d: 'Pantalla completa' },
]
const GLOBAL = [
  { k: ['Ctrl', 'K'], d: 'Buscar en todo · saltar a una sección' },
  { k: ['?'], d: 'Mostrar / ocultar este panel' },
  { k: ['F11'], d: 'Pantalla completa (en cualquier vista)' },
  { k: ['Esc'], d: 'Cerrar panel activo' },
  { k: ['Alt', '←/→'], d: 'Atrás / adelante en el historial' },
  { k: ['botones laterales'], d: 'Atrás / adelante (ratón)' },
]
const LIBRARY = [
  { k: ['←', '↑', '↓', '→'], d: 'Mover el foco por la rejilla' },
  { k: ['Enter'], d: 'Abrir la tarjeta enfocada' },
  { k: ['Espacio'], d: 'Marcar / desmarcar una tarjeta' },
  { k: ['Esc'], d: 'Quitar las marcas' },
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
            <div v-for="g in [
              { h: 'Lector de manga', rows: READER },
              { h: 'Reproductor', rows: PLAYER },
              { h: 'Biblioteca', rows: LIBRARY },
              { h: 'Global', rows: GLOBAL },
            ]" :key="g.h" class="sc__col">
              <h3 class="sc__h">{{ g.h }}</h3>
              <div v-for="(r, i) in g.rows" :key="i" class="sc__row">
                <span class="sc__keys"><kbd v-for="k in r.k" :key="k">{{ k }}</kbd></span>
                <span>{{ r.d }}</span>
              </div>
            </div>
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.ov { position: fixed; inset: 0; z-index: var(--z-modal); display: grid; place-items: center; padding: var(--s-5); background: rgba(7,10,18,.72); backdrop-filter: blur(8px); }
.sc { position: relative; width: min(52rem, 100%); max-height: 88vh; overflow-y: auto; background: var(--glass-strong); border: 1px solid var(--line-2); border-radius: var(--r-lg); box-shadow: var(--shadow-xl); padding: var(--s-6); }
.sc__x { position: absolute; top: var(--s-3); right: var(--s-3); width: 2rem; height: 2rem; display: grid; place-items: center; border-radius: var(--r-sm); color: var(--ink-soft); border: 1px solid var(--line); }
.sc__title { font-size: var(--fs-xl); margin-bottom: var(--s-4); }
.sc__cols { display: grid; grid-template-columns: repeat(4, 1fr); gap: var(--s-6); }
.sc__keys { flex-wrap: wrap; }
.sc__h { font-family: var(--font-mono); font-size: var(--fs-2xs); letter-spacing: var(--tracking-caps); text-transform: uppercase; color: var(--azure); margin-bottom: var(--s-3); }
.sc__row { display: flex; align-items: center; gap: var(--s-3); padding: 0.3125rem 0; font-size: var(--fs-sm); color: var(--ink-soft); }
.sc__keys { display: flex; gap: 4px; min-width: 5.625rem; }
kbd { font-family: var(--font-mono); font-size: var(--fs-2xs); padding: 2px 0.4375rem; border-radius: var(--r-xs); border: 1px solid var(--line-2); color: var(--ink); background: var(--surface); }
.modal-enter-active, .modal-leave-active { transition: opacity var(--t-base); }
.modal-enter-from, .modal-leave-to { opacity: 0; }
@media (max-width: 900px) { .sc__cols { grid-template-columns: 1fr 1fr; } }
@media (max-width: 540px) { .sc__cols { grid-template-columns: 1fr; } }
</style>
