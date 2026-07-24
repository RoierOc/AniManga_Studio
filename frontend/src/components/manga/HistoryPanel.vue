<script setup>
import { onMounted } from 'vue'
import { useMangaStore } from '@/stores/manga'
import { relativeTime } from '@/lib/format'
import { imgProxy } from '@/lib/img'
import Icon from '@/components/ui/Icon.vue'
import Spinner from '@/components/ui/Spinner.vue'
import EmptyState from '@/components/ui/EmptyState.vue'

const props = defineProps({ open: { type: Boolean, default: false } })
const emit = defineEmits(['close'])
const store = useMangaStore()

onMounted(() => { if (!store.historyLoaded) store.loadHistory() })

function continueReading(h) {
  store.continueHistory(h)
  emit('close')
}
</script>

<template>
  <Teleport to="body">
    <Transition name="modal">
      <div v-if="props.open" class="ov" @click.self="emit('close')">
        <div class="modal">
          <button class="modal__x" @click="emit('close')"><Icon name="close" :size="18" /></button>
          <header class="modal__head">
            <h2>Historial</h2>
            <button v-if="store.history.length" class="clear" @click="store.clearHistory()">
              <Icon name="close" :size="13" /> Limpiar
            </button>
          </header>

          <div v-if="!store.historyLoaded" class="center"><Spinner /></div>
          <EmptyState v-else-if="!store.history.length" icon="library" title="Aún no has leído nada." />

          <div v-else class="hist__list">
            <button v-for="(h, i) in store.history" :key="i" class="hrow" @click="continueReading(h)">
              <div class="hrow__cover">
                <img v-if="h.cover" :src="imgProxy(h.cover, 60)" :alt="h.title" loading="lazy" />
                <span class="hrow__ch">Cap. {{ h.chapter }}</span>
              </div>
              <div class="hrow__meta">
                <div class="hrow__title">{{ h.title }}</div>
                <div class="hrow__sub">Cap. {{ h.chapter }} · {{ relativeTime(h.read_at) }}</div>
              </div>
              <Icon name="play" :size="16" class="hrow__play" />
            </button>
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.ov { position: fixed; inset: 0; z-index: var(--z-modal); display: grid; place-items: center; padding: var(--s-5); background: rgba(7,10,18,.72); backdrop-filter: blur(8px); }
.modal { position: relative; width: min(34rem, 100%); max-height: 80vh; display: flex; flex-direction: column; background: var(--glass-strong); border: 1px solid var(--line-2); border-radius: var(--r-lg); box-shadow: var(--shadow-xl); overflow: hidden; padding: var(--s-5); gap: var(--s-4); }
.modal__x { position: absolute; top: var(--s-3); right: var(--s-3); z-index: 3; width: 2.125rem; height: 2.125rem; display: grid; place-items: center; border-radius: var(--r-sm); color: var(--ink-soft); background: var(--surface); border: 1px solid var(--line); }
.modal__x:hover { color: var(--ink); }
.modal__head { display: flex; align-items: center; justify-content: space-between; gap: var(--s-4); padding-right: var(--s-7); }
.modal__head h2 { font-size: var(--fs-xl); }
.clear { display: inline-flex; align-items: center; gap: 0.375rem; padding: var(--s-2) var(--s-3); border-radius: var(--r-sm); border: 1px solid var(--line); color: var(--ink-faint); font-size: var(--fs-xs); transition: all var(--t-fast); flex-shrink: 0; }
.clear:hover { color: var(--coral); border-color: color-mix(in srgb, var(--coral) 40%, transparent); }

.center { display: grid; place-items: center; padding: var(--s-8); }

.hist__list { display: flex; flex-direction: column; gap: var(--s-2); overflow-y: auto; }
.hrow { display: flex; align-items: center; gap: var(--s-4); padding: var(--s-2) var(--s-3); border-radius: var(--r-md); border: 1px solid var(--line); background: var(--surface); text-align: left; transition: all var(--t-fast); }
.hrow:hover { border-color: var(--line-strong); background: var(--surface-2); }
.hrow:hover .hrow__play { color: var(--azure-bright); transform: scale(1.15); }
.hrow__cover { position: relative; width: 2.875rem; height: 4rem; flex-shrink: 0; border-radius: var(--r-sm); overflow: hidden; background: var(--surface-3); }
.hrow__cover img { width: 100%; height: 100%; object-fit: cover; }
.hrow__ch { position: absolute; left: 3px; bottom: 2px; font-family: var(--font-display); font-weight: 700; font-size: var(--fs-2xs); color: #fff; text-shadow: 0 1px 4px rgba(0,0,0,.9); }
.hrow__meta { flex: 1; min-width: 0; }
.hrow__title { font-size: var(--fs-sm); font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.hrow__sub { font-size: var(--fs-xs); color: var(--ink-faint); margin-top: 2px; }
.hrow__play { color: var(--ink-faint); transition: all var(--t-fast) var(--ease-snap); flex-shrink: 0; }

.modal-enter-active, .modal-leave-active { transition: opacity var(--t-base); }
.modal-enter-active .modal { transition: transform var(--t-base) var(--ease-snap); }
.modal-enter-from, .modal-leave-to { opacity: 0; }
.modal-enter-from .modal { transform: scale(.95) translateY(12px); }
</style>
