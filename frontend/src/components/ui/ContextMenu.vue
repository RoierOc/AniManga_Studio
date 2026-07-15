<script setup>
// Menú contextual reutilizable (clic derecho). El padre controla apertura y posición:
//   const cm = ref({ open:false, x:0, y:0, items:[] })
//   @contextmenu.prevent="openMenu($event, itemsFor(x))"
// Cada item: { label, icon?, danger?, disabled?, action } o { sep:true }.
// Se cierra al elegir, con Escape, scroll, resize o clic fuera. Se reposiciona para
// no salirse de la ventana.
import { ref, watch, nextTick, onBeforeUnmount } from 'vue'
import Icon from '@/components/ui/Icon.vue'

const props = defineProps({
  open: { type: Boolean, default: false },
  x: { type: Number, default: 0 },
  y: { type: Number, default: 0 },
  items: { type: Array, default: () => [] },
})
const emit = defineEmits(['update:open'])

const el = ref(null)
const pos = ref({ left: 0, top: 0 })

function close() { emit('update:open', false) }
function pick(it) {
  if (it.disabled || it.sep) return
  close()
  it.action?.()
}

// Reposiciona dentro de la ventana tras montar (ya conocemos el tamaño real).
watch(() => props.open, async (o) => {
  if (!o) return
  pos.value = { left: props.x, top: props.y }
  await nextTick()
  const r = el.value?.getBoundingClientRect()
  if (!r) return
  const pad = 8
  let left = props.x, top = props.y
  if (left + r.width + pad > window.innerWidth) left = window.innerWidth - r.width - pad
  if (top + r.height + pad > window.innerHeight) top = window.innerHeight - r.height - pad
  pos.value = { left: Math.max(pad, left), top: Math.max(pad, top) }
})

function onDocDown(e) { if (el.value && !el.value.contains(e.target)) close() }
function onKey(e) { if (e.key === 'Escape') close() }
function onScroll() { if (props.open) close() }
watch(() => props.open, (o) => {
  const m = o ? 'addEventListener' : 'removeEventListener'
  document[m]('mousedown', onDocDown, true)
  document[m]('keydown', onKey, true)
  window[m]('scroll', onScroll, true)
  window[m]('resize', close)
})
onBeforeUnmount(() => {
  document.removeEventListener('mousedown', onDocDown, true)
  document.removeEventListener('keydown', onKey, true)
  window.removeEventListener('scroll', onScroll, true)
  window.removeEventListener('resize', close)
})
</script>

<template>
  <Teleport to="body">
    <Transition name="cm">
      <div v-if="open" ref="el" class="cm" :style="{ left: pos.left + 'px', top: pos.top + 'px' }" @contextmenu.prevent>
        <template v-for="(it, i) in items" :key="i">
          <div v-if="it.sep" class="cm__sep" />
          <button v-else class="cm__it" :class="{ 'is-danger': it.danger, 'is-disabled': it.disabled }"
                  :disabled="it.disabled" @click="pick(it)">
            <Icon v-if="it.icon" :name="it.icon" :size="15" class="cm__ic" />
            <span>{{ it.label }}</span>
          </button>
        </template>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.cm {
  position: fixed; z-index: var(--z-modal, 400); min-width: 12rem; padding: var(--s-1);
  border-radius: var(--r-md); background: rgba(12,16,26,.97); border: 1px solid var(--line-2);
  backdrop-filter: blur(14px); box-shadow: var(--shadow-xl);
}
.cm__it {
  display: flex; align-items: center; gap: var(--s-2); width: 100%; text-align: left;
  padding: var(--s-2) var(--s-3); border-radius: var(--r-sm); font-size: var(--fs-sm);
  color: var(--ink); cursor: pointer; transition: background var(--t-fast), color var(--t-fast);
}
.cm__it:hover { background: rgba(255,255,255,.08); }
.cm__it.is-danger { color: var(--rose, #ff6b81); }
.cm__it.is-danger:hover { background: color-mix(in srgb, var(--rose, #ff6b81) 16%, transparent); }
.cm__it.is-disabled { opacity: .4; cursor: default; }
.cm__it.is-disabled:hover { background: transparent; }
.cm__ic { color: var(--ink-soft); flex-shrink: 0; }
.cm__it.is-danger .cm__ic { color: inherit; }
.cm__sep { height: 1px; margin: var(--s-1) var(--s-2); background: var(--line); }

.cm-enter-active, .cm-leave-active { transition: opacity var(--t-fast), transform var(--t-fast) var(--ease-silk); transform-origin: top left; }
.cm-enter-from, .cm-leave-to { opacity: 0; transform: scale(.96) translateY(-4px); }
</style>
