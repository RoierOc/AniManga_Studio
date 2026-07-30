<script setup>
import { computed } from 'vue'
import { useUiStore } from '@/stores/ui'
import { useMangaStore } from '@/stores/manga'
import Icon from './Icon.vue'

// Live activity affordance in the TopBar: a progress ring + count while anything is
// processing, falling back to a quiet history glyph once idle (so the drawer — and its
// recent history — stays reachable). Single source of truth = the manga store getters.
const ui = useUiStore()
const manga = useMangaStore()
const count = computed(() => manga.activeCount)
const pct = computed(() => manga.aggregatePct)
const hasHistory = computed(() => manga.historyTasks.length > 0)
const anyError = computed(() => manga.liveTasks.some(t => t.status === 'error'))

const C = 56.5   // 2·π·9 — matches the per-chapter rings in the modal
const offset = computed(() => C - C * (pct.value / 100))
</script>

<template>
  <button
    v-if="count || hasHistory"
    class="ai"
    :class="{ 'is-live': count, 'is-error': anyError }"
    :data-tip="count ? `${count} tarea(s) en proceso · ${pct}%` : 'Actividad reciente'"
    @click="ui.activityOpen = !ui.activityOpen"
  >
    <svg class="ai__ring" viewBox="0 0 24 24" width="34" height="34">
      <circle class="ai__track" cx="12" cy="12" r="9" />
      <circle
        v-if="count"
        class="ai__fill"
        cx="12" cy="12" r="9"
        :style="{ strokeDasharray: C, strokeDashoffset: offset }"
      />
    </svg>
    <span class="ai__center">
      <span v-if="count" class="ai__count">{{ count }}</span>
      <Icon v-else name="clock" :size="15" />
    </span>
  </button>
</template>

<style scoped>
.ai {
  position: relative; width: 2.375rem; height: 2.375rem;
  display: grid; place-items: center;
  color: var(--ink-faint); border-radius: var(--r-sm);
  border: 1px solid transparent; transition: all var(--t-fast);
}
.ai:hover { color: var(--ink); border-color: var(--line); }
.ai.is-live { color: var(--azure-bright); }
.ai.is-error { color: var(--coral); }
.ai__ring { position: absolute; inset: 0; margin: auto; transform: rotate(-90deg); }
.ai__track { fill: none; stroke: var(--line-2); stroke-width: 2.4; }
.ai__fill {
  fill: none; stroke: currentColor; stroke-width: 2.4; stroke-linecap: round;
  transition: stroke-dashoffset var(--t-base) var(--ease-silk);
}
.ai__center { position: relative; display: grid; place-items: center; }
.ai__count { font-size: var(--fs-xs); font-weight: 700; font-family: var(--font-mono); }
.ai.is-live::after {
  content: ''; position: absolute; top: 0.3125rem; right: 0.3125rem;
  width: 0.375rem; height: 0.375rem; border-radius: 50%; background: var(--cyan);
  box-shadow: var(--glow-cyan); animation: ai-pulse 1.4s ease-in-out infinite;
}
@keyframes ai-pulse { 0%, 100% { opacity: 1; } 50% { opacity: .35; } }
@media (prefers-reduced-motion: reduce) {
  .ai.is-live::after { animation: none; }
  .ai__fill { transition: none; }
}
</style>
