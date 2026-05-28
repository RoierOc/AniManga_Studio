<script setup>
import { ref, computed, onMounted } from 'vue'
import { api } from '@/lib/api'
import { useUiStore } from '@/stores/ui'
import MangaCard from '@/components/manga/MangaCard.vue'
import Spinner from '@/components/ui/Spinner.vue'
import Icon from '@/components/ui/Icon.vue'

const ui = useUiStore()
const items = ref([])
const loading = ref(true)
const error = ref(false)
const search = ref('')
const filter = ref('all')

const FILTERS = [
  { id: 'all', label: 'Todo' },
  { id: 'upscaled', label: 'Escalado 4K' },
  { id: 'downloaded', label: 'Solo descargado' },
]

const filtered = computed(() => {
  let list = items.value
  if (filter.value === 'upscaled') list = list.filter(m => (m.upscaled || 0) > 0)
  if (filter.value === 'downloaded') list = list.filter(m => !(m.upscaled || 0))
  const q = search.value.trim().toLowerCase()
  if (q) list = list.filter(m => (m.name || '').toLowerCase().includes(q))
  return list
})

const totals = computed(() => ({
  series: items.value.length,
  chapters: items.value.reduce((a, m) => a + (m.chapter_count || 0), 0),
  upscaled: items.value.filter(m => (m.upscaled || 0) > 0).length,
}))

async function load() {
  loading.value = true; error.value = false
  try {
    items.value = await api.get('/api/library')
  } catch (e) {
    error.value = true
    ui.toast('No se pudo cargar la biblioteca', 'error')
  } finally {
    loading.value = false
  }
}
onMounted(load)
</script>

<template>
  <div class="view">
    <!-- Hero header -->
    <header class="hero stagger">
      <div class="hero__head" style="--i:0">
        <p class="hero__eyebrow"><span class="hero__tick" /> TU COLECCIÓN LOCAL</p>
        <h1 class="hero__title">Biblioteca</h1>
      </div>

      <div class="hero__stats" style="--i:1">
        <div class="stat">
          <span class="stat__num">{{ totals.series }}</span>
          <span class="stat__label">series</span>
        </div>
        <div class="stat">
          <span class="stat__num">{{ totals.chapters }}</span>
          <span class="stat__label">capítulos</span>
        </div>
        <div class="stat stat--accent">
          <span class="stat__num">{{ totals.upscaled }}</span>
          <span class="stat__label">en 4K</span>
        </div>
      </div>
    </header>

    <!-- Controls -->
    <div class="toolbar stagger">
      <div class="filters" style="--i:2">
        <button v-for="f in FILTERS" :key="f.id" class="pill" :class="{ 'is-active': filter === f.id }"
                @click="filter = f.id">{{ f.label }}</button>
      </div>
      <label class="searchbox" style="--i:2">
        <Icon name="search" :size="15" />
        <input v-model="search" type="search" placeholder="Filtrar series…" />
      </label>
    </div>

    <!-- Grid -->
    <div v-if="loading" class="grid">
      <div v-for="n in 12" :key="n" class="skeleton" />
    </div>

    <div v-else-if="error" class="empty">
      <Icon name="globe" :size="34" />
      <p>El backend no responde.</p>
      <button class="btn" @click="load"><Icon name="spark" :size="15" /> Reintentar</button>
    </div>

    <div v-else-if="!filtered.length" class="empty">
      <Icon name="library" :size="34" />
      <p>{{ items.length ? 'Sin resultados para ese filtro.' : 'Tu biblioteca está vacía.' }}</p>
    </div>

    <TransitionGroup v-else tag="div" name="card" class="grid stagger">
      <MangaCard v-for="(m, i) in filtered" :key="m.id" :manga="m" :style="{ '--i': Math.min(i, 16) }" />
    </TransitionGroup>
  </div>
</template>

<style scoped>
.view { padding: var(--s-4) var(--s-6) var(--s-8); max-width: var(--content-max); margin: 0 auto; }

/* ── Hero ─────────────────────────────────────────────────────────────── */
.hero {
  display: flex; align-items: flex-end; justify-content: space-between;
  flex-wrap: wrap; gap: var(--s-5);
  padding: var(--s-5) 0 var(--s-6);
}
.hero__eyebrow {
  display: flex; align-items: center; gap: var(--s-2);
  font-family: var(--font-mono); font-size: var(--fs-2xs);
  letter-spacing: var(--tracking-caps); color: var(--azure);
  margin-bottom: var(--s-2);
}
.hero__tick { width: 14px; height: 1px; background: var(--azure); box-shadow: 0 0 8px var(--azure-glow); }
.hero__title { font-size: var(--fs-3xl); }

.hero__stats { display: flex; gap: var(--s-6); }
.stat { display: flex; flex-direction: column; }
.stat__num { font-family: var(--font-display); font-size: var(--fs-2xl); font-weight: 600; line-height: 1; }
.stat__label { font-size: var(--fs-xs); color: var(--ink-faint); margin-top: 4px; text-transform: lowercase; }
.stat--accent .stat__num { color: var(--cyan); }

/* ── Toolbar ──────────────────────────────────────────────────────────── */
.toolbar {
  display: flex; align-items: center; justify-content: space-between;
  flex-wrap: wrap; gap: var(--s-3);
  margin-bottom: var(--s-6);
}
.filters { display: flex; gap: var(--s-2); }
.pill {
  padding: var(--s-2) var(--s-4);
  border-radius: var(--r-pill);
  font-size: var(--fs-sm); font-weight: 500; color: var(--ink-soft);
  border: 1px solid var(--line);
  transition: all var(--t-fast) var(--ease-silk);
}
.pill:hover { color: var(--ink); border-color: var(--line-strong); }
.pill.is-active { color: #fff; background: var(--azure-haze); border-color: var(--azure); color: var(--azure-bright); }

.searchbox {
  display: flex; align-items: center; gap: var(--s-2);
  padding: var(--s-2) var(--s-3); width: min(280px, 50vw);
  background: var(--surface); border: 1px solid var(--line); border-radius: var(--r-md);
  color: var(--ink-faint);
  transition: border-color var(--t-fast), box-shadow var(--t-fast);
}
.searchbox:focus-within { border-color: var(--azure); box-shadow: 0 0 0 3px var(--azure-haze); }
.searchbox input { flex: 1; border: none; outline: none; background: none; color: var(--ink); font-size: var(--fs-sm); }

/* ── Grid ─────────────────────────────────────────────────────────────── */
.grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
  gap: var(--s-5) var(--s-4);
}
.skeleton {
  aspect-ratio: 2 / 3; border-radius: var(--r-md);
  background: linear-gradient(100deg, var(--surface) 30%, var(--surface-2) 50%, var(--surface) 70%);
  background-size: 200% 100%;
  animation: shimmer 1.4s linear infinite;
}

/* ── Empty / error ────────────────────────────────────────────────────── */
.empty {
  display: flex; flex-direction: column; align-items: center; gap: var(--s-3);
  padding: var(--s-9) 0; color: var(--ink-faint); text-align: center;
}
.btn {
  display: inline-flex; align-items: center; gap: var(--s-2);
  margin-top: var(--s-2); padding: var(--s-2) var(--s-4);
  border-radius: var(--r-sm); background: var(--azure-haze); color: var(--azure-bright);
  border: 1px solid var(--azure); font-size: var(--fs-sm); font-weight: 500;
  transition: background var(--t-fast);
}
.btn:hover { background: var(--azure); color: #fff; }

/* card list transitions */
.card-enter-active { transition: all var(--t-slow) var(--ease-silk); }
.card-enter-from { opacity: 0; transform: translateY(12px); }
.card-move { transition: transform var(--t-slow) var(--ease-silk); }

@media (max-width: 540px) {
  .view { padding: var(--s-3) var(--s-4) var(--s-8); }
  .grid { grid-template-columns: repeat(auto-fill, minmax(120px, 1fr)); gap: var(--s-4) var(--s-3); }
  .hero__stats { gap: var(--s-5); }
}
</style>
