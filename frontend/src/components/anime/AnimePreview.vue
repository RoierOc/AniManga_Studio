<script setup>
import { computed } from 'vue'
import { useAnimeStore } from '@/stores/anime'
import { animeFormatLabel } from '@/lib/anime'

const store = useAnimeStore()
const a = computed(() => store.preview)
const tier = computed(() => { const s = a.value?.score; return !s ? '' : s >= 75 ? 'is-high' : s >= 60 ? 'is-mid' : 'is-low' })
// library entries store `episodes` as an array; search/discover store it as a number.
const epCount = computed(() => {
  const e = a.value?.episodes
  if (typeof e === 'number') return e
  return a.value?.total_episodes || 0
})
</script>

<template>
  <Teleport to="body">
    <Transition name="prev">
      <div v-if="a" class="prev" :style="{ left: store.previewPos.x + 'px', top: store.previewPos.y + 'px' }">
        <div class="prev__cover">
          <img v-if="a.cover" :src="a.cover" :alt="a.title" />
          <span v-if="a.status === 'RELEASING'" class="prev__airing">EN EMISIÓN</span>
        </div>
        <div class="prev__body">
          <div class="prev__title">{{ a.title }}</div>
          <div class="prev__meta">
            <span class="prev__fmt">{{ animeFormatLabel(a.format) }}</span>
            <span v-if="epCount">{{ epCount }} ep</span>
            <span v-if="a.score" class="prev__score" :class="tier">★ {{ (a.score / 10).toFixed(1) }}</span>
          </div>
          <div v-if="a.genres?.length" class="prev__genres">{{ a.genres.slice(0, 3).join(' · ') }}</div>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.prev { position: fixed; z-index: var(--z-toast); width: 256px; pointer-events: none;
  background: var(--glass-strong); backdrop-filter: blur(16px); border: 1px solid var(--line-2); border-radius: var(--r-md); box-shadow: var(--shadow-xl); overflow: hidden; }
.prev__cover { position: relative; aspect-ratio: 16/10; background: var(--surface-2); }
.prev__cover img { width: 100%; height: 100%; object-fit: cover; }
.prev__airing { position: absolute; top: var(--s-2); left: var(--s-2); font-family: var(--font-mono); font-size: 8px; font-weight: 700; letter-spacing: .1em; padding: 2px 6px; border-radius: var(--r-xs); color: var(--cyan); background: var(--cyan-glow); }
.prev__body { padding: var(--s-3); }
.prev__title { font-weight: 600; font-size: var(--fs-sm); line-height: var(--lh-snug); display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
.prev__meta { display: flex; align-items: center; gap: var(--s-2); margin-top: var(--s-2); font-size: var(--fs-xs); color: var(--ink-faint); }
.prev__fmt { font-family: var(--font-mono); font-size: var(--fs-2xs); color: var(--azure); }
.prev__score.is-high { color: var(--jade); } .prev__score.is-mid { color: var(--gold); } .prev__score.is-low { color: var(--ink-faint); }
.prev__genres { margin-top: 6px; font-size: var(--fs-2xs); color: var(--ink-soft); }
.prev-enter-active, .prev-leave-active { transition: opacity var(--t-fast); }
.prev-enter-from, .prev-leave-to { opacity: 0; }
</style>
