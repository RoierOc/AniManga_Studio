<script setup>
import { computed } from 'vue'
import { useMangaStore } from '@/stores/manga'
import Icon from './Icon.vue'

const store = useMangaStore()
const tasks = computed(() => store.activeTasks)
const KIND = {
  download: { icon: 'download', color: 'var(--azure)', label: 'Descarga' },
  upscale: { icon: 'spark', color: 'var(--cyan)', label: '4K' },
  export: { icon: 'library', color: 'var(--violet)', label: 'Tomo' },
}
</script>

<template>
  <Teleport to="body">
    <Transition name="q">
      <div v-if="tasks.length" class="q" :class="{ 'is-collapsed': !store.queueOpen }">
        <button class="q__head" @click="store.queueOpen = !store.queueOpen">
          <span class="q__spin" />
          <span class="q__title">{{ tasks.length }} {{ tasks.length === 1 ? 'tarea activa' : 'tareas activas' }}</span>
          <Icon name="chevron" :size="15" :style="{ transform: store.queueOpen ? 'rotate(90deg)' : 'rotate(-90deg)' }" />
        </button>

        <div v-show="store.queueOpen" class="q__body">
          <div v-for="t in tasks" :key="t.kind + t.id" class="qt">
            <div class="qt__top">
              <span class="qt__kind" :style="{ color: KIND[t.kind].color }"><Icon :name="KIND[t.kind].icon" :size="12" /> {{ KIND[t.kind].label }}</span>
              <span class="qt__label">{{ t.label }}</span>
              <a v-if="t.file" class="qt__act" :href="store.exportFileUrl(t.id)" download :title="'Descargar archivo'"><Icon name="download" :size="13" /></a>
              <button v-else-if="t.kind !== 'export'" class="qt__act qt__act--x" @click="store.cancelTask(t)" title="Cancelar"><Icon name="close" :size="13" /></button>
            </div>
            <div class="qt__bar" :class="{ 'is-done': t.done }">
              <span :style="{ width: (t.done ? 100 : t.pct) + '%', background: KIND[t.kind].color }" />
            </div>
            <div class="qt__meta">
              <span>{{ t.done ? 'Listo' : (t.msg || t.status) }}</span>
              <span v-if="!t.done" class="qt__pct">{{ t.pct }}%</span>
            </div>
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.q { position: fixed; left: var(--s-5); bottom: var(--s-5); z-index: var(--z-toast); width: 320px;
  background: var(--glass-strong); backdrop-filter: blur(16px); border: 1px solid var(--line-2); border-radius: var(--r-md); box-shadow: var(--shadow-lg); overflow: hidden; }
.q__head { display: flex; align-items: center; gap: var(--s-2); width: 100%; padding: var(--s-3) var(--s-4); }
.q__spin { width: 14px; height: 14px; border-radius: 50%; border: 2px solid var(--line-2); border-top-color: var(--azure); animation: spin .7s linear infinite; }
.q__title { flex: 1; text-align: left; font-size: var(--fs-sm); font-weight: 600; }
.q__body { max-height: 320px; overflow-y: auto; padding: 0 var(--s-3) var(--s-3); display: flex; flex-direction: column; gap: var(--s-2); }
.qt { padding: var(--s-3); border-radius: var(--r-sm); background: var(--surface); border: 1px solid var(--line); }
.qt__top { display: flex; align-items: center; gap: var(--s-2); }
.qt__kind { display: inline-flex; align-items: center; gap: 4px; font-family: var(--font-mono); font-size: var(--fs-2xs); font-weight: 600; flex-shrink: 0; }
.qt__label { flex: 1; font-size: var(--fs-xs); color: var(--ink-soft); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.qt__act { width: 24px; height: 24px; display: grid; place-items: center; border-radius: var(--r-xs); color: var(--ink-faint); border: 1px solid var(--line); flex-shrink: 0; transition: all var(--t-fast); }
.qt__act:hover { color: var(--azure-bright); border-color: var(--azure); }
.qt__act--x:hover { color: var(--coral); border-color: color-mix(in srgb, var(--coral) 40%, transparent); }
.qt__bar { height: 4px; margin: var(--s-2) 0 6px; border-radius: var(--r-pill); background: var(--surface-3); overflow: hidden; }
.qt__bar span { display: block; height: 100%; transition: width var(--t-base) var(--ease-silk); }
.qt__bar.is-done span { background: var(--jade) !important; }
.qt__meta { display: flex; justify-content: space-between; font-size: var(--fs-2xs); color: var(--ink-faint); font-family: var(--font-mono); }

.q-enter-active, .q-leave-active { transition: all var(--t-base) var(--ease-snap); }
.q-enter-from, .q-leave-to { opacity: 0; transform: translateY(20px); }

@media (max-width: 540px) { .q { left: var(--s-3); right: var(--s-3); width: auto; } }
</style>
