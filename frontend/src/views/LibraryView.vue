<script setup>
import { ref, computed, onMounted } from 'vue'
import { api } from '@/lib/api'
import { useUiStore } from '@/stores/ui'
import { useMangaStore } from '@/stores/manga'
import MangaCard from '@/components/manga/MangaCard.vue'
import Spinner from '@/components/ui/Spinner.vue'
import Icon from '@/components/ui/Icon.vue'

const ui = useUiStore()
const manga = useMangaStore()
const items = ref([])
const loading = ref(true)
const error = ref(false)
const search = ref('')
const filter = ref('all')

const FILTERS = computed(() => [
  { id: 'all', label: 'Todo' },
  { id: 'upscaled', label: 'Escalado 4K' },
  { id: 'downloaded', label: 'Solo descargado' },
  { id: 'updates', label: 'Novedades', count: manga.updates.length },
])

const filtered = computed(() => {
  let list = items.value
  if (filter.value === 'upscaled') list = list.filter(m => (m.upscaled || 0) > 0)
  if (filter.value === 'downloaded') list = list.filter(m => !(m.upscaled || 0))
  if (filter.value === 'updates') list = list.filter(m => manga.updatesByTitle[m.name])
  const q = search.value.trim().toLowerCase()
  if (q) list = list.filter(m => (m.name || '').toLowerCase().includes(q))
  return list
})

const totals = computed(() => ({
  series: items.value.length,
  chapters: items.value.reduce((a, m) => a + (m.chapter_count || 0), 0),
  upscaled: items.value.filter(m => (m.upscaled || 0) > 0).length,
}))

const findingCovers = ref(false)

async function load() {
  loading.value = true; error.value = false
  try {
    const [local, mdLib] = await Promise.all([
      api.get('/api/library'),
      api.get('/api/mangadex/local_library').catch(() => []),
    ])
    // Build a name→md lookup from local_library.json
    const mdByName = {}
    for (const m of (mdLib || [])) {
      const key = (m.title || m.name || '').toLowerCase().trim()
      if (key) mdByName[key] = m
    }
    // Merge local manga with MD entries
    const seen = new Set()
    const merged = []
    for (const m of (local || [])) {
      seen.add((m.name || '').toLowerCase().trim())
      const mdKey = (m.name || '').toLowerCase().trim()
      merged.push({ ...m, mdId: mdByName[mdKey]?.id })
    }
    // Add MD-only entries (saved but not downloaded)
    for (const m of (mdLib || [])) {
      const key = (m.title || '').toLowerCase().trim()
      if (key && !seen.has(key)) {
        merged.push({ id: m.title, name: m.title, chapter_count: 0, upscaled: 0, cover: m.cover || null, mdId: m.id, mdOnly: true })
      }
    }
    items.value = merged
  } catch (e) {
    error.value = true
    ui.toast('No se pudo cargar la biblioteca', 'error')
  } finally {
    loading.value = false
  }
}

async function findCovers() {
  findingCovers.value = true
  ui.toast('Buscando portadas faltantes en MangaDex…', 'info')
  try {
    const found = await api.get('/api/library/search-covers')
    const n = Object.keys(found || {}).length
    if (n) {
      items.value = items.value.map(m => found[m.id] && !m.cover ? { ...m, cover: found[m.id] } : m)
      ui.toast(`${n} portadas encontradas`, 'ok')
    } else ui.toast('No se encontraron portadas nuevas', 'warn')
  } catch (_) { ui.toast('Error buscando portadas', 'error') }
  finally { findingCovers.value = false }
}
onMounted(() => { load(); if (!manga.updatesLoaded) manga.loadUpdates() })
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
                @click="filter = f.id" v-show="f.id !== 'updates' || f.count">
          {{ f.label }}<span v-if="f.count" class="pill__n">{{ f.count }}</span>
        </button>
      </div>
      <div class="tb-right" style="--i:2">
        <button class="covbtn" :disabled="findingCovers" @click="findCovers" title="Buscar portadas faltantes en MangaDex">
          <span v-if="findingCovers" class="covspin" /><Icon v-else name="spark" :size="14" /> Portadas
        </button>
        <button class="covbtn" :disabled="manga.offlineCovers?.running" @click="manga.downloadCoversOffline()" title="Descargar todas las portadas para uso offline">
          <span v-if="manga.offlineCovers?.running" class="covspin" /><Icon v-else name="download" :size="14" />
          <span v-if="manga.offlineCovers?.running">{{ manga.offlineCovers.done }}/{{ manga.offlineCovers.total }}</span><span v-else>Offline</span>
        </button>
        <label class="searchbox">
          <Icon name="search" :size="15" />
          <input v-model="search" type="search" placeholder="Filtrar series…" />
        </label>
      </div>
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

    <div v-else class="grid">
      <MangaCard v-for="m in filtered" :key="m.id" :manga="m" :updates="manga.updatesByTitle[m.name]?.new_count || 0" @click="manga.open(m)" />
    </div>
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
.pill.is-active { background: var(--azure-haze); border-color: var(--azure); color: var(--azure-bright); }
.pill__n { margin-left: 5px; font-family: var(--font-mono); font-size: var(--fs-2xs); color: var(--cyan); }

.searchbox {
  display: flex; align-items: center; gap: var(--s-2);
  padding: var(--s-2) var(--s-3); width: min(280px, 50vw);
  background: var(--surface); border: 1px solid var(--line); border-radius: var(--r-md);
  color: var(--ink-faint);
  transition: border-color var(--t-fast), box-shadow var(--t-fast);
}
.searchbox:focus-within { border-color: var(--azure); box-shadow: 0 0 0 3px var(--azure-haze); }
.searchbox input { flex: 1; border: none; outline: none; background: none; color: var(--ink); font-size: var(--fs-sm); }
.tb-right { display: flex; align-items: center; gap: var(--s-2); }
.covbtn { display: inline-flex; align-items: center; gap: 6px; padding: var(--s-2) var(--s-3); border-radius: var(--r-md); font-size: var(--fs-sm); color: var(--ink-soft); border: 1px solid var(--line); transition: all var(--t-fast); }
.covbtn:hover:not(:disabled) { color: var(--azure-bright); border-color: var(--azure); }
.covbtn:disabled { opacity: .6; }
.covspin { width: 14px; height: 14px; border-radius: 50%; border: 2px solid var(--line-2); border-top-color: var(--azure); animation: spin .7s linear infinite; }

/* ── Grid ─────────────────────────────────────────────────────────────── */
.grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(225px, 1fr));
  gap: var(--s-6) var(--s-5);
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

@media (max-width: 540px) {
  .view { padding: var(--s-3) var(--s-4) var(--s-8); }
  .grid { grid-template-columns: repeat(auto-fill, minmax(160px, 1fr)); gap: var(--s-5) var(--s-3); }
  .hero__stats { gap: var(--s-5); }
}
</style>
