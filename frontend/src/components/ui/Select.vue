<script setup>
/* Desplegable propio — sustituye al <select> nativo, que en la shell nativa pinta el control
 * y su lista con el estilo del sistema operativo y rompe la estética en cada formulario.
 *
 * El patrón viene de `dstatus` (AnimeDetail), que ya lo había resuelto bien para el estado de
 * una serie: botón con punto de color + popover animado. Aquí generalizado a cualquier lista.
 *
 * El menú se TELETRANSPORTA al body con posición fija: muchos de los sitios donde va (el modal
 * del manga, el panel Gestionar) tienen `overflow:hidden` y recortarían un menú `absolute` —
 * lección ya aprendida con el selector de modelo. Se coloca debajo del botón, o encima si no cabe.
 *
 * Uso:  <Select v-model="lang" :options="[{value:'es',label:'Español',color:'var(--jade)'}]" />
 */
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import Icon from '@/components/ui/Icon.vue'

const props = defineProps({
  modelValue: { type: [String, Number], default: '' },
  // [{ value, label, color?, hint? }] — `color` pinta el punto, `hint` sale a la derecha.
  options: { type: Array, default: () => [] },
  placeholder: { type: String, default: 'Elegir…' },
  // Ancho del botón: por defecto se ajusta al contenido.
  block: { type: Boolean, default: false },
  disabled: { type: Boolean, default: false },
  ariaLabel: { type: String, default: '' },
})
const emit = defineEmits(['update:modelValue', 'change'])

const open = ref(false)
const btn = ref(null)
const menu = ref(null)
const active = ref(-1)          // opción resaltada por teclado
const pos = ref({ top: 0, left: 0, width: 0, up: false })

const selected = computed(() => props.options.find(o => String(o.value) === String(props.modelValue)) || null)

function place() {
  const el = btn.value
  if (!el) return
  const r = el.getBoundingClientRect()
  const estH = Math.min(18 * 16, props.options.length * 40 + 12)
  const below = window.innerHeight - r.bottom
  const up = below < estH + 8 && r.top > below
  pos.value = { left: r.left, width: r.width, top: up ? r.top - 6 : r.bottom + 6, up }
}

/* Instante en que se abrió el menú. `onReflow` cierra al hacer scroll, y el `scrollIntoView` de
   más abajo ES un scroll: con `scroll-behavior:smooth` (base.css) la animación emite eventos
   durante cientos de ms, así que el menú se abría y se cerraba solo — no se podía elegir nada. */
let openedAt = 0

async function toggle() {
  if (props.disabled) return
  if (open.value) return close()
  place()
  openedAt = performance.now()
  open.value = true
  active.value = props.options.findIndex(o => String(o.value) === String(props.modelValue))
  await nextTick()
  // `behavior:'instant'` para no heredar el scroll suave global (ver openedAt).
  menu.value?.querySelector('.usel__opt.is-active')?.scrollIntoView({ block: 'nearest', behavior: 'instant' })
}
function close() { open.value = false; active.value = -1 }

function pick(o) {
  emit('update:modelValue', o.value)
  emit('change', o.value)
  close()
  btn.value?.focus({ preventScroll: true })
}

function onKey(e) {
  if (!open.value) {
    if (['Enter', ' ', 'ArrowDown'].includes(e.key)) { e.preventDefault(); toggle() }
    return
  }
  if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); close(); btn.value?.focus(); return }
  if (e.key === 'Tab') return close()
  if (e.key === 'Enter' || e.key === ' ') {
    e.preventDefault()
    if (active.value >= 0) pick(props.options[active.value])
    return
  }
  if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
    e.preventDefault()
    const n = props.options.length
    if (!n) return
    active.value = (active.value + (e.key === 'ArrowDown' ? 1 : -1) + n) % n
    nextTick(() => menu.value?.querySelector('.usel__opt.is-active')?.scrollIntoView({ block: 'nearest' }))
  }
}

// Cerrar al pulsar fuera, y al hacer scroll/resize (el menú es fijo: seguiría al botón si no).
function onDocDown(e) {
  if (btn.value?.contains(e.target) || menu.value?.contains(e.target)) return
  close()
}
function onReflow(e) {
  if (e?.type === 'scroll') {
    if (menu.value?.contains(e.target)) return          // scroll DENTRO del menú: legítimo
    if (performance.now() - openedAt < 300) return      // el scrollIntoView de la propia apertura
  }
  close()
}
watch(open, (v) => {
  const m = v ? 'addEventListener' : 'removeEventListener'
  document[m]('mousedown', onDocDown)
  window[m]('resize', onReflow)
  window[m]('scroll', onReflow, true)
})
onBeforeUnmount(() => {
  document.removeEventListener('mousedown', onDocDown)
  window.removeEventListener('resize', onReflow)
  window.removeEventListener('scroll', onReflow, true)
})
</script>

<template>
  <div class="usel" :class="{ 'is-block': block }">
    <button ref="btn" type="button" class="usel__btn" :class="{ 'is-open': open }"
            :disabled="disabled" :aria-label="ariaLabel || placeholder"
            aria-haspopup="listbox" :aria-expanded="open"
            @click="toggle" @keydown="onKey">
      <span v-if="selected?.color" class="usel__dot" :style="{ background: selected.color, color: selected.color }" />
      <span class="usel__lbl" :style="selected?.color ? { color: selected.color } : {}">
        {{ selected?.label ?? placeholder }}
      </span>
      <Icon name="chevron" :size="13" class="usel__chev" :style="{ transform: open ? 'rotate(-90deg)' : 'rotate(90deg)' }" />
    </button>

    <Teleport to="body">
      <Transition name="usel-pop">
        <ul v-if="open" ref="menu" class="usel__menu" :class="{ 'is-up': pos.up }" role="listbox"
            :style="{ top: pos.top + 'px', left: pos.left + 'px', minWidth: pos.width + 'px' }"
            @keydown="onKey">
          <li v-for="(o, i) in options" :key="o.value" role="option" :aria-selected="String(o.value) === String(modelValue)">
            <button type="button" class="usel__opt"
                    :class="{ 'is-sel': String(o.value) === String(modelValue), 'is-active': i === active }"
                    @click="pick(o)" @mouseenter="active = i">
              <span v-if="o.color" class="usel__dot" :style="{ background: o.color, color: o.color }" />
              <span class="usel__opt-l" :style="o.color ? { color: o.color } : {}">{{ o.label }}</span>
              <span v-if="o.hint" class="usel__opt-h">{{ o.hint }}</span>
              <Icon v-if="String(o.value) === String(modelValue)" name="check" :size="13" class="usel__ck" />
            </button>
          </li>
        </ul>
      </Transition>
    </Teleport>
  </div>
</template>

<style scoped>
.usel { position: relative; display: inline-flex; }
.usel.is-block, .usel.is-block .usel__btn { width: 100%; }

.usel__btn {
  display: inline-flex; align-items: center; gap: var(--s-2);
  padding: var(--s-2) var(--s-3);
  border-radius: var(--r-sm); background: var(--surface); border: 1px solid var(--line-2);
  font-size: var(--fs-sm); font-weight: 600; color: var(--ink);
  transition: border-color var(--t-fast), background var(--t-fast);
}
.usel__btn:hover:not(:disabled), .usel__btn.is-open { border-color: var(--azure); background: var(--surface-2); }
.usel__btn:disabled { opacity: .5; cursor: default; }
.usel__lbl { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.usel.is-block .usel__lbl { flex: 1; text-align: left; }
.usel__chev { flex-shrink: 0; color: var(--ink-faint); transition: transform var(--t-fast); }
.usel__dot { width: 0.5rem; height: 0.5rem; border-radius: 50%; flex-shrink: 0; box-shadow: 0 0 8px currentColor; }
</style>

<style>
/* El menú vive teletransportado en <body>, así que su estilo NO puede ser scoped. */
.usel__menu {
  position: fixed; z-index: calc(var(--z-toast) + 2);
  max-height: 18rem; overflow-y: auto; padding: var(--s-1);
  border-radius: var(--r-md); background: var(--glass-strong); backdrop-filter: blur(14px);
  border: 1px solid var(--line-2); box-shadow: var(--shadow-xl);
}
.usel__menu.is-up { transform: translateY(-100%); }
.usel__opt {
  display: flex; align-items: center; gap: var(--s-2); width: 100%;
  padding: var(--s-2) var(--s-3); border-radius: var(--r-sm);
  font-size: var(--fs-sm); font-weight: 500; color: var(--ink); text-align: left;
  transition: background var(--t-fast);
}
.usel__opt.is-active { background: rgba(255, 255, 255, 0.08); }
.usel__opt.is-sel { background: var(--azure-haze); }
.usel__opt-l { flex: 1; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.usel__opt-h { font-family: var(--font-mono); font-size: var(--fs-2xs); color: var(--ink-faint); }
.usel__ck { flex-shrink: 0; color: var(--azure-bright); }

.usel-pop-enter-active, .usel-pop-leave-active {
  transition: opacity var(--t-fast), transform var(--t-fast) var(--ease-silk);
}
.usel-pop-enter-from, .usel-pop-leave-to { opacity: 0; }
.usel-pop-enter-from:not(.is-up), .usel-pop-leave-to:not(.is-up) { transform: translateY(-4px) scale(.98); }
.usel-pop-enter-from.is-up, .usel-pop-leave-to.is-up { transform: translateY(-100%) translateY(4px) scale(.98); }
</style>
