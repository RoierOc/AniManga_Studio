<script setup>
import { onMounted } from 'vue'
import { useAnimeStore } from '@/stores/anime'
import { relativeTime } from '@/lib/format'
import Icon from '@/components/ui/Icon.vue'
import Spinner from '@/components/ui/Spinner.vue'
import EmptyState from '@/components/ui/EmptyState.vue'

const store = useAnimeStore()
onMounted(() => { if (!store.historyLoaded) store.loadHistory() })

function open(item) {
  const a = store.library.find(x => x.id === item.anime_id)
  if (a) store.openDetail(a)
}
</script>

<template>
  <div class="hist">
    <header class="hist__head">
      <div>
        <p class="eyebrow"><span class="tick" /> CONTINÚA DONDE LO DEJASTE</p>
        <h1>Historial</h1>
      </div>
      <button v-if="store.history.length" class="clear" @click="store.clearHistory()">
        <Icon name="close" :size="13" /> Limpiar
      </button>
    </header>

    <div v-if="!store.historyLoaded" class="center"><Spinner /></div>
    <EmptyState v-else-if="!store.history.length" icon="heart" title="Aún no has visto nada." />

    <div v-else class="hist__list">
      <button v-for="(h, i) in store.history" :key="i" class="hrow" @click="open(h)">
        <div class="hrow__cover">
          <img v-if="h.cover" :src="h.cover" :alt="h.title" loading="lazy" />
          <span class="hrow__ep">{{ String(h.episode).padStart(2, '0') }}</span>
        </div>
        <div class="hrow__meta">
          <div class="hrow__title">{{ h.title }}</div>
          <div class="hrow__sub">Episodio {{ h.episode }} · {{ relativeTime(h.watched_at) }}</div>
        </div>
        <Icon name="play" :size="16" class="hrow__play" />
      </button>
    </div>
  </div>
</template>

<style scoped>
.hist { max-width: 920px; margin: 0 auto; padding: 0 var(--s-6) var(--s-8); }
.hist__head { display: flex; align-items: flex-end; justify-content: space-between; gap: var(--s-4); padding: var(--s-5) 0; }
.eyebrow { display: flex; align-items: center; gap: var(--s-2); font-family: var(--font-mono); font-size: var(--fs-2xs); letter-spacing: var(--tracking-caps); color: var(--azure); margin-bottom: var(--s-2); }
.tick { width: 14px; height: 1px; background: var(--azure); box-shadow: 0 0 8px var(--azure-glow); }
.clear { display: inline-flex; align-items: center; gap: 6px; padding: var(--s-2) var(--s-3); border-radius: var(--r-sm); border: 1px solid var(--line); color: var(--ink-faint); font-size: var(--fs-xs); transition: all var(--t-fast); }
.clear:hover { color: var(--coral); border-color: color-mix(in srgb, var(--coral) 40%, transparent); }

.center { display: grid; place-items: center; padding: var(--s-8); }
.empty { display: flex; flex-direction: column; align-items: center; gap: var(--s-3); padding: var(--s-8) 0; color: var(--ink-faint); }

.hist__list { display: flex; flex-direction: column; gap: var(--s-2); }
.hrow { display: flex; align-items: center; gap: var(--s-4); padding: var(--s-2) var(--s-3); border-radius: var(--r-md); border: 1px solid var(--line); background: var(--surface); text-align: left; transition: all var(--t-fast); }
.hrow:hover { border-color: var(--line-strong); background: var(--surface-2); }
.hrow:hover .hrow__play { color: var(--azure-bright); transform: scale(1.15); }
.hrow__cover { position: relative; width: 64px; height: 40px; flex-shrink: 0; border-radius: var(--r-sm); overflow: hidden; background: var(--surface-3); }
.hrow__cover img { width: 100%; height: 100%; object-fit: cover; }
.hrow__ep { position: absolute; left: 4px; bottom: 2px; font-family: var(--font-display); font-weight: 700; font-size: var(--fs-xs); color: #fff; text-shadow: 0 1px 4px rgba(0,0,0,.9); }
.hrow__meta { flex: 1; min-width: 0; }
.hrow__title { font-size: var(--fs-sm); font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.hrow__sub { font-size: var(--fs-xs); color: var(--ink-faint); margin-top: 2px; }
.hrow__play { color: var(--ink-faint); transition: all var(--t-fast) var(--ease-snap); }

@media (max-width: 640px) { .hist { padding: 0 var(--s-4) var(--s-8); } }
</style>
