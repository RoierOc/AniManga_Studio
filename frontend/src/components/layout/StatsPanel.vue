<script setup>
import { ref, computed, onMounted, onBeforeUnmount } from 'vue'
import Icon from '@/components/ui/Icon.vue'

const emit = defineEmits(['close'])
const data = ref(null)
const loading = ref(true)
const error = ref(false)
const rootEl = ref(null)

// Barras diarias normalizadas al máximo de las dos series (14 días).
const chart = computed(() => {
  const d = data.value?.daily
  if (!d) return null
  const max = Math.max(1, ...d.watched, ...d.upscaled)
  const DOW = ['D', 'L', 'M', 'X', 'J', 'V', 'S']
  return d.labels.map((iso, i) => {
    const dt = new Date(iso + 'T00:00:00')
    return {
      iso,
      dow: DOW[dt.getDay()],
      day: dt.getDate(),
      watched: d.watched[i],
      upscaled: d.upscaled[i],
      wPct: (d.watched[i] / max) * 100,
      uPct: (d.upscaled[i] / max) * 100,
    }
  })
})
const chartHasUpscale = computed(() => (data.value?.daily?.upscaled || []).some(v => v > 0))

function fmtGB(bytes) {
  const gb = (bytes || 0) / 1e9
  if (gb >= 100) return gb.toFixed(0) + ' GB'
  if (gb >= 10) return gb.toFixed(1) + ' GB'
  if (gb >= 1) return gb.toFixed(2) + ' GB'
  return ((bytes || 0) / 1e6).toFixed(0) + ' MB'
}

async function load() {
  loading.value = true; error.value = false
  try {
    const r = await fetch('/api/storage/stats')
    if (!r.ok) throw new Error()
    data.value = await r.json()
  } catch { error.value = true }
  finally { loading.value = false }
}

function onKey(e) { if (e.key === 'Escape') emit('close') }
function onDocClick(e) { if (rootEl.value && !rootEl.value.contains(e.target)) emit('close') }

onMounted(() => {
  load()
  document.addEventListener('keydown', onKey)
  // defer para no capturar el mismo clic que abrió el panel
  setTimeout(() => document.addEventListener('click', onDocClick), 0)
})
onBeforeUnmount(() => {
  document.removeEventListener('keydown', onKey)
  document.removeEventListener('click', onDocClick)
})
</script>

<template>
  <div ref="rootEl" class="stats" role="dialog" aria-label="Estadísticas">
    <header class="stats__head">
      <span class="stats__title"><Icon name="chart" :size="16" /> Estadísticas</span>
      <button class="stats__x" title="Cerrar" @click="emit('close')"><Icon name="close" :size="15" /></button>
    </header>

    <div v-if="loading" class="stats__state">Cargando…</div>
    <div v-else-if="error" class="stats__state">No se pudieron cargar las estadísticas.</div>

    <div v-else class="stats__body">
      <!-- Este mes -->
      <section class="stats__block stats__block--hero">
        <span class="stats__eyebrow">Últimos 30 días</span>
        <div class="stats__hero-nums">
          <div class="stats__big"><b>{{ data.activity.episodes_month }}</b><span>episodios vistos</span></div>
          <div class="stats__big"><b>{{ data.activity.series_month }}</b><span>series</span></div>
        </div>
        <p v-if="data.activity.last_watched" class="stats__last">
          Último: <em>{{ data.activity.last_watched.title }}</em> · ep {{ data.activity.last_watched.episode }}
        </p>
      </section>

      <!-- Gráfica de actividad diaria -->
      <section v-if="chart" class="stats__block">
        <span class="stats__eyebrow">Actividad · 14 días</span>
        <div class="stats__chart">
          <div v-for="c in chart" :key="c.iso" class="stats__col" :title="`${c.iso}: ${c.watched} vistos${chartHasUpscale ? ', ' + c.upscaled + ' escalados' : ''}`">
            <div class="stats__bars-stack">
              <div class="stats__bar stats__bar--up" :style="{ height: c.uPct + '%' }"></div>
              <div class="stats__bar stats__bar--watch" :style="{ height: c.wPct + '%' }"></div>
            </div>
            <span class="stats__dow">{{ c.dow }}</span>
          </div>
        </div>
        <div class="stats__legend">
          <span><i class="dot dot--watch"></i>Episodios vistos</span>
          <span><i class="dot dot--up"></i>Caps. escalados</span>
        </div>
      </section>

      <!-- Biblioteca -->
      <section class="stats__block">
        <span class="stats__eyebrow">Biblioteca</span>
        <div class="stats__grid">
          <div class="stats__cell"><b>{{ data.library.manga_series }}</b><span>mangas</span></div>
          <div class="stats__cell"><b>{{ data.library.anime_series }}</b><span>animes</span></div>
          <div class="stats__cell"><b>{{ data.library.chapters }}</b><span>capítulos</span></div>
          <div class="stats__cell"><b>{{ data.library.episodes }}</b><span>episodios</span></div>
        </div>
      </section>

      <!-- Procesado -->
      <section class="stats__block">
        <span class="stats__eyebrow">Procesado</span>
        <div class="stats__grid">
          <div class="stats__cell"><b>{{ data.library.upscaled_chapters }}</b><span>caps. escalados</span></div>
          <div class="stats__cell"><b>{{ data.library.translated_chapters }}</b><span>caps. traducidos</span></div>
        </div>
      </section>

      <!-- Almacenamiento -->
      <section class="stats__block">
        <span class="stats__eyebrow">Almacenamiento</span>
        <ul class="stats__bars">
          <li><span>Anime</span><i>{{ fmtGB(data.storage.anime) }}</i></li>
          <li><span>Manga original</span><i>{{ fmtGB(data.storage.original) }}</i></li>
          <li><span>Escalado 4K</span><i>{{ fmtGB(data.storage.upscaled) }}</i></li>
          <li class="stats__bars-total"><span>Total usado</span><i>{{ fmtGB(data.storage.total) }}</i></li>
          <li v-if="data.storage.disk_free"><span>Libre en disco</span><i>{{ fmtGB(data.storage.disk_free) }}</i></li>
        </ul>
      </section>
    </div>
  </div>
</template>

<style scoped>
.stats {
  position: absolute; top: calc(100% + 8px); right: 0;
  width: 340px; max-width: calc(100vw - 24px);
  background: var(--surface, #131a2b);
  border: 1px solid var(--line);
  border-radius: var(--r-lg, 14px);
  box-shadow: 0 18px 50px rgba(0,0,0,.5);
  z-index: var(--z-modal, 900);
  overflow: hidden;
  animation: stats-in .16s ease;
}
@keyframes stats-in { from { opacity: 0; transform: translateY(-6px); } to { opacity: 1; transform: none; } }

.stats__head {
  display: flex; align-items: center; justify-content: space-between;
  padding: var(--s-3) var(--s-4);
  border-bottom: 1px solid var(--line);
}
.stats__title { display: flex; align-items: center; gap: var(--s-2); font-weight: 600; font-size: var(--fs-sm); color: var(--ink); }
.stats__title :deep(svg) { color: var(--azure-bright); }
.stats__x { color: var(--ink-faint); width: 26px; height: 26px; display: grid; place-items: center; border-radius: var(--r-sm); }
.stats__x:hover { color: var(--ink); background: var(--azure-haze); }

.stats__state { padding: var(--s-6); text-align: center; color: var(--ink-soft); font-size: var(--fs-sm); }

.stats__body { padding: var(--s-3) var(--s-4) var(--s-4); display: flex; flex-direction: column; gap: var(--s-4); max-height: 70vh; overflow-y: auto; }
.stats__block { display: flex; flex-direction: column; gap: var(--s-2); }
.stats__eyebrow { font-size: var(--fs-2xs, .68rem); letter-spacing: .09em; text-transform: uppercase; color: var(--ink-faint); font-weight: 600; }

.stats__block--hero {
  background: var(--azure-haze);
  border: 1px solid var(--azure); border-radius: var(--r-md, 10px);
  padding: var(--s-3) var(--s-4);
}
.stats__hero-nums { display: flex; gap: var(--s-6); }
.stats__big { display: flex; flex-direction: column; }
.stats__big b { font-size: 1.7rem; line-height: 1; font-weight: 700; color: var(--azure-bright); font-variant-numeric: tabular-nums; }
.stats__big span { font-size: var(--fs-2xs, .7rem); color: var(--ink-soft); margin-top: 2px; }
.stats__last { font-size: var(--fs-xs); color: var(--ink-soft); margin: 0; }
.stats__last em { color: var(--ink); font-style: normal; font-weight: 500; }

.stats__chart { display: flex; align-items: flex-end; gap: 3px; height: 74px; }
.stats__col { flex: 1; display: flex; flex-direction: column; align-items: center; gap: 3px; height: 100%; }
.stats__bars-stack {
  flex: 1; width: 100%; display: flex; flex-direction: column; justify-content: flex-end;
  gap: 1px; min-height: 0;
}
.stats__bar { width: 100%; border-radius: 2px 2px 0 0; min-height: 0; transition: height var(--t-fast); }
.stats__bar--watch { background: var(--azure-bright); }
.stats__bar--up { background: #7c5cff; }
.stats__dow { font-size: .6rem; color: var(--ink-faint); }
.stats__legend { display: flex; gap: var(--s-4); margin-top: var(--s-1); }
.stats__legend span { display: flex; align-items: center; gap: 5px; font-size: var(--fs-2xs, .7rem); color: var(--ink-soft); }
.stats__legend .dot { width: 8px; height: 8px; border-radius: 2px; display: inline-block; }
.dot--watch { background: var(--azure-bright); }
.dot--up { background: #7c5cff; }

.stats__grid { display: grid; grid-template-columns: 1fr 1fr; gap: var(--s-2); }
.stats__cell {
  display: flex; flex-direction: column; gap: 1px;
  padding: var(--s-2) var(--s-3);
  background: var(--base); border: 1px solid var(--line); border-radius: var(--r-sm);
}
.stats__cell b { font-size: 1.15rem; font-weight: 700; color: var(--ink); font-variant-numeric: tabular-nums; }
.stats__cell span { font-size: var(--fs-2xs, .7rem); color: var(--ink-faint); }

.stats__bars { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 5px; }
.stats__bars li { display: flex; justify-content: space-between; font-size: var(--fs-xs); color: var(--ink-soft); }
.stats__bars li i { font-style: normal; color: var(--ink); font-variant-numeric: tabular-nums; }
.stats__bars-total { border-top: 1px solid var(--line); padding-top: 5px; margin-top: 2px; font-weight: 600; }
.stats__bars-total span, .stats__bars-total i { color: var(--ink); }
</style>
