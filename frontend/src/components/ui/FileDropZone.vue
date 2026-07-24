<script setup>
/* Soltar un CBZ/CBR en CUALQUIER parte de la ventana lo lleva al Taller y lo analiza.
 *
 * Además tapa un agujero que ya existía: sin un manejador a nivel de ventana, soltar un archivo
 * fuera del dropzone del Taller hace que el navegador NAVEGUE hasta ese archivo — en la shell
 * nativa eso te saca de la app y pierdes el estado, sin aviso ni forma de volver. El
 * `preventDefault` de aquí lo impide en toda la ventana, aunque el archivo no sea importable.
 *
 * Va montado una sola vez en App.vue.
 */
import { ref, onMounted, onBeforeUnmount } from 'vue'
import { useUiStore } from '@/stores/ui'
import { useWorkshopStore } from '@/stores/workshop'
import Icon from '@/components/ui/Icon.vue'

// Mismo criterio que el Taller: si cambia allí, cambia aquí.
const ARCHIVE_RE = /\.(cbz|cbr|zip|rar)$/i

const ui = useUiStore()
const workshop = useWorkshopStore()
const active = ref(false)

/* `dragenter`/`dragleave` se disparan también al cruzar CADA elemento hijo, así que un booleano
   simple parpadearía sin parar mientras mueves el ratón. Se cuenta la profundidad y sólo el
   regreso a 0 apaga el overlay. */
let depth = 0

const hasFiles = (e) => Array.from(e.dataTransfer?.types || []).includes('Files')
// En el Taller no pintamos overlay: esa vista ya tiene su propio dropzone con su indicación,
// y dos avisos superpuestos para el mismo gesto se ven como un error.
const onWorkshop = () => ui.currentView === 'workshop'

function onEnter(e) {
  if (!hasFiles(e) || onWorkshop()) return   // arrastrar texto o un elemento de la UI no cuenta
  depth++
  active.value = true
}
function onOver(e) {
  // Sin preventDefault en dragover el navegador se queda con el archivo y navega a él.
  // Se hace SIEMPRE, incluso en el Taller: impedir esa navegación no es negociable.
  if (hasFiles(e)) e.preventDefault()
}
function onLeave(e) {
  if (!hasFiles(e) || onWorkshop()) return
  if (--depth <= 0) { depth = 0; active.value = false }
}

function onDrop(e) {
  if (!hasFiles(e)) return
  e.preventDefault()                // impide la navegación SIEMPRE, sea importable o no
  depth = 0
  active.value = false
  /* Escuchamos en CAPTURA, así que esto corre ANTES que el dropzone del Taller. Cortamos la
     propagación para que el archivo no se analice dos veces: lo que hacemos aquí es lo mismo
     que hace él. (Comprobar `e.defaultPrevented` no serviría: acabamos de ponerlo a true.) */
  e.stopPropagation()

  const files = Array.from(e.dataTransfer?.files || [])
  const f = files.find(x => ARCHIVE_RE.test(x.name))
  if (!f) {
    ui.toast(files.length
      ? 'Solo se pueden importar archivos .cbz, .cbr, .zip o .rar'
      : 'No se recibió ningún archivo', 'warn')
    return
  }
  if (files.length > 1) ui.toast(`Se importa «${f.name}»; el Taller procesa uno cada vez`, 'info')
  if (!onWorkshop()) ui.goto('workshop')   // si ya estás allí, no metas una entrada de historial
  workshop.analyze(f)
}

/* En `window` y en fase de captura: así llega aunque una vista intermedia se coma el evento, y
   la navegación queda impedida en toda la app pase lo que pase por debajo. */
const opts = { capture: true }
onMounted(() => {
  window.addEventListener('dragenter', onEnter, opts)
  window.addEventListener('dragover', onOver, opts)
  window.addEventListener('dragleave', onLeave, opts)
  window.addEventListener('drop', onDrop, opts)
})
onBeforeUnmount(() => {
  window.removeEventListener('dragenter', onEnter, opts)
  window.removeEventListener('dragover', onOver, opts)
  window.removeEventListener('dragleave', onLeave, opts)
  window.removeEventListener('drop', onDrop, opts)
})
</script>

<template>
  <Transition name="fdz">
    <div v-if="active" class="fdz" aria-hidden="true">
      <div class="fdz__card">
        <div class="fdz__glyph"><Icon name="upload" :size="34" /></div>
        <p class="fdz__title">Suelta para importar</p>
        <p class="fdz__hint">CBZ · CBR · ZIP · RAR — el Taller detecta los capítulos solo</p>
      </div>
    </div>
  </Transition>
</template>

<style scoped>
.fdz {
  position: fixed; inset: 0; z-index: var(--z-modal);
  display: grid; place-items: center; padding: var(--s-6);
  background: var(--glass-strong); backdrop-filter: blur(8px);
  pointer-events: none;   /* nunca debe robarle el drop a la ventana */
}
.fdz__card {
  display: flex; flex-direction: column; align-items: center; gap: var(--s-3);
  padding: var(--s-8) var(--s-9); text-align: center;
  border-radius: var(--r-xl); background: var(--surface);
  /* Borde discontinuo = "aquí se suelta", el gesto que ya entiende todo el mundo. */
  border: 2px dashed var(--azure); box-shadow: var(--shadow-xl), 0 0 0 1px var(--azure-glow);
}
.fdz__glyph {
  width: 5rem; height: 5rem; display: grid; place-items: center;
  border-radius: var(--r-lg); color: var(--azure); background: var(--azure-haze);
  border: 1px solid color-mix(in srgb, var(--azure) 22%, transparent);
}
.fdz__title { font-family: var(--font-display); font-size: var(--fs-xl); color: var(--ink); }
.fdz__hint { font-size: var(--fs-sm); color: var(--ink-faint); }

.fdz-enter-active, .fdz-leave-active { transition: opacity var(--t-fast) var(--ease-silk); }
.fdz-enter-from, .fdz-leave-to { opacity: 0; }
.fdz-enter-active .fdz__card { transition: transform var(--t-base) var(--ease-snap); }
.fdz-enter-from .fdz__card { transform: scale(0.94); }

@media (prefers-reduced-motion: reduce) {
  .fdz-enter-active, .fdz-leave-active, .fdz-enter-active .fdz__card { transition: none; }
}
</style>
