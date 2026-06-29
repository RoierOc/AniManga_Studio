<script setup>
import { computed, ref } from 'vue'
import { useUiStore } from '@/stores/ui'
import { useMangaStore } from '@/stores/manga'
import Icon from '@/components/ui/Icon.vue'
import EmptyState from '@/components/ui/EmptyState.vue'
import { relativeTime } from '@/lib/format'

// Full Activity center: the roomy counterpart to the TopBar drawer. Same store getters
// (processingGroups / historyGroups) → perfectly in sync with the drawer and the modal.
const ui = useUiStore()
const store = useMangaStore()

const KIND = {
  download:  { icon: 'download', color: 'var(--azure)',  label: 'Descarga' },
  upscale:   { icon: 'spark',    color: 'var(--cyan)',   label: '4K' },
  export:    { icon: 'library',  color: 'var(--violet)', label: 'Tomo' },
  translate: { icon: 'globe',    color: 'var(--jade)',   label: 'Traducir' },
}
const kind = (k) => KIND[k] || KIND.download
const monogram = (t) => (t || '?').trim().charAt(0).toUpperCase()

const STATUS_LABEL = { done: 'Completado', error: 'Error', cancelled: 'Cancelado', running: 'En curso', queued: 'En cola' }

const q = ref('')
const tab = computed({ get: () => ui.activityTab, set: (v) => { ui.activityTab = v } })

function match(groups) {
  const term = q.value.trim().toLowerCase()
  if (!term) return groups
  return groups.filter(g => g.title.toLowerCase().includes(term))
}
const activeGroups = computed(() => match(store.processingGroups))
const historyGroups = computed(() => match(store.historyGroups))

function openManga(g) {
  store.open({ id: g.mangaId, name: g.title, cover: g.cover })
}
</script>

<template>
  <div class="act">
    <div class="act__bar">
      <div class="act__tabs">
        <button :class="{ on: tab === 'active' }" @click="tab = 'active'">
          En proceso <span class="act__n">{{ store.processingGroups.length }}</span>
        </button>
        <button :class="{ on: tab === 'history' }" @click="tab = 'history'">
          Historial <span class="act__n">{{ store.historyTasks.length }}</span>
        </button>
      </div>
      <div class="act__tools">
        <label class="act__search">
          <Icon name="search" :size="15" />
          <input v-model="q" type="text" placeholder="Filtrar por título…" />
        </label>
        <button v-if="tab === 'history' && store.historyTasks.length" class="act__clear" @click="store.clearActivityHistory()">
          <Icon name="close" :size="13" /> Vaciar
        </button>
      </div>
    </div>

    <!-- En proceso -->
    <template v-if="tab === 'active'">
      <div v-if="activeGroups.length" class="act__grid">
        <article v-for="g in activeGroups" :key="g.mangaId" class="card">
          <header class="card__head" @click="openManga(g)">
            <span class="card__cover">
              <img v-if="g.cover" :src="g.cover" alt="" loading="lazy" />
              <span v-else class="card__mono">{{ monogram(g.title) }}</span>
            </span>
            <span class="card__title">{{ g.title }}</span>
            <span class="card__pct" :class="{ 'is-err': g.anyError }">{{ g.pct }}%</span>
          </header>
          <div v-for="t in g.tasks" :key="t.id" class="row">
            <span class="row__kind" :style="{ color: kind(t.kind).color }"><Icon :name="kind(t.kind).icon" :size="13" /></span>
            <span class="row__klabel">{{ kind(t.kind).label }}</span>
            <span class="row__label">{{ t.label }}</span>
            <div class="row__bar" :class="{ 'is-err': t.status === 'error' }">
              <span :style="{ width: t.pct + '%', background: kind(t.kind).color }" />
            </div>
            <span class="row__pct">{{ t.status === 'error' ? '—' : t.pct + '%' }}</span>
            <button class="row__act" title="Cancelar" @click="store.cancelAnyTask(t)"><Icon name="close" :size="13" /></button>
          </div>
        </article>
      </div>
      <EmptyState v-else icon="check" title="No hay nada en proceso"
        hint="Descargas, traducciones, escalados 4K y tomos aparecerán aquí en tiempo real." />
    </template>

    <!-- Historial -->
    <template v-else>
      <div v-if="historyGroups.length" class="act__grid">
        <article v-for="g in historyGroups" :key="g.mangaId" class="card">
          <header class="card__head" @click="openManga(g)">
            <span class="card__cover">
              <img v-if="g.cover" :src="g.cover" alt="" loading="lazy" />
              <span v-else class="card__mono">{{ monogram(g.title) }}</span>
            </span>
            <span class="card__title">{{ g.title }}</span>
          </header>
          <div v-for="t in g.tasks" :key="t.id" class="row">
            <span class="row__kind" :style="{ color: kind(t.kind).color }"><Icon :name="kind(t.kind).icon" :size="13" /></span>
            <span class="row__klabel">{{ kind(t.kind).label }}</span>
            <span class="row__label">{{ t.label }}</span>
            <span class="pill" :class="'pill--' + t.status">{{ STATUS_LABEL[t.status] || t.status }}</span>
            <span class="row__ts">{{ relativeTime(Math.floor(t.ts / 1000)) }}</span>
            <button v-if="t.kind === 'export' && t.file" class="row__act row__act--dl" title="Descargar tomo" @click="store.downloadExportFile(t.id)"><Icon name="download" :size="13" /></button>
            <button class="row__act" title="Quitar del historial" @click="store.hideFromHistory(t.id)"><Icon name="close" :size="13" /></button>
          </div>
        </article>
      </div>
      <EmptyState v-else icon="clock" title="Historial vacío"
        hint="Aquí quedará lo que termine en esta sesión." />
    </template>
  </div>
</template>

<style scoped>
.act { padding: var(--s-5) var(--s-6); max-width: 64rem; margin: 0 auto; }
.act__bar { display: flex; align-items: center; gap: var(--s-4); flex-wrap: wrap; margin-bottom: var(--s-5); }
.act__tabs { display: flex; gap: var(--s-2); }
.act__tabs button { display: inline-flex; align-items: center; gap: var(--s-2); padding: var(--s-2) var(--s-4); border-radius: var(--r-pill); font-size: var(--fs-sm); font-weight: 600; color: var(--ink-faint); border: 1px solid var(--line); transition: all var(--t-fast); }
.act__tabs button:hover { color: var(--ink-soft); border-color: var(--line-2); }
.act__tabs button.on { color: #fff; background: var(--azure); border-color: transparent; }
.act__n { font-family: var(--font-mono); font-size: var(--fs-2xs); opacity: .8; }
.act__tools { margin-left: auto; display: flex; align-items: center; gap: var(--s-2); }
.act__search { display: inline-flex; align-items: center; gap: var(--s-2); padding: var(--s-2) var(--s-3); border-radius: var(--r-md); border: 1px solid var(--line); color: var(--ink-faint); background: var(--surface); }
.act__search input { background: none; border: none; outline: none; color: var(--ink); font-size: var(--fs-sm); width: 12rem; }
.act__clear { display: inline-flex; align-items: center; gap: 4px; font-size: var(--fs-xs); color: var(--ink-faint); padding: var(--s-2) var(--s-3); border-radius: var(--r-sm); border: 1px solid var(--line); }
.act__clear:hover { color: var(--coral); border-color: color-mix(in srgb, var(--coral) 40%, transparent); }

.act__grid { display: flex; flex-direction: column; gap: var(--s-3); }
.card { border: 1px solid var(--line); border-radius: var(--r-lg); background: var(--surface); padding: var(--s-4); }
.card__head { display: flex; align-items: center; gap: var(--s-3); margin-bottom: var(--s-3); cursor: pointer; }
.card__cover { width: 34px; height: 34px; border-radius: var(--r-sm); overflow: hidden; flex-shrink: 0; background: var(--surface-3); display: grid; place-items: center; }
.card__cover img { width: 100%; height: 100%; object-fit: cover; }
.card__mono { font-size: var(--fs-sm); font-weight: 700; color: var(--ink-faint); }
.card__title { flex: 1; font-size: var(--fs-md); font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.card__head:hover .card__title { color: var(--azure-bright); }
.card__pct { font-family: var(--font-mono); font-size: var(--fs-xs); color: var(--ink-faint); }
.card__pct.is-err { color: var(--coral); }

.row { display: flex; align-items: center; gap: var(--s-3); padding: var(--s-2) 0; }
.row + .row { border-top: 1px solid var(--line); }
.row__kind { flex-shrink: 0; display: grid; place-items: center; }
.row__klabel { font-family: var(--font-mono); font-size: var(--fs-2xs); font-weight: 600; color: var(--ink-faint); width: 4.5rem; flex-shrink: 0; }
.row__label { flex: 1; min-width: 0; font-size: var(--fs-sm); color: var(--ink-soft); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.row__bar { width: 8rem; height: 4px; border-radius: var(--r-pill); background: var(--surface-3); overflow: hidden; flex-shrink: 0; }
.row__bar span { display: block; height: 100%; transition: width var(--t-base) var(--ease-silk); }
.row__bar.is-err span { background: var(--coral) !important; width: 100% !important; }
.row__pct { font-family: var(--font-mono); font-size: var(--fs-2xs); color: var(--ink-faint); width: 2.5rem; text-align: right; flex-shrink: 0; }
.row__ts { font-size: var(--fs-2xs); color: var(--ink-ghost); flex-shrink: 0; }
.row__act { width: 26px; height: 26px; display: grid; place-items: center; border-radius: var(--r-xs); color: var(--ink-faint); border: 1px solid var(--line); flex-shrink: 0; transition: all var(--t-fast); }
.row__act:hover { color: var(--coral); border-color: color-mix(in srgb, var(--coral) 40%, transparent); }
.row__act--dl:hover { color: var(--azure-bright); border-color: color-mix(in srgb, var(--azure) 45%, transparent); }

.pill { font-size: var(--fs-2xs); font-weight: 700; padding: 2px 8px; border-radius: var(--r-pill); flex-shrink: 0; }
.pill--done { color: var(--jade); background: color-mix(in srgb, var(--jade) 14%, transparent); }
.pill--error { color: var(--coral); background: color-mix(in srgb, var(--coral) 14%, transparent); }
.pill--cancelled { color: var(--ink-faint); background: var(--surface-3); }

@media (max-width: 640px) {
  .row__klabel, .row__bar { display: none; }
  .act__search input { width: 8rem; }
}
</style>
