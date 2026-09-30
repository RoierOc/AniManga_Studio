<script setup>
import { rememberedRef } from '@/lib/viewMemory'
/* Historial de Cine — dos lentes de la misma pregunta, y por eso dos pills y no una lista mezclada:
 *   · Visto   — lo que hiciste TÚ (nuestro fichero de progreso).
 *   · Bajado  — lo que hizo la máquina (historial de Sonarr/Radarr).
 * Fundirlas ordenando por fecha daría una lista donde «viste el 8» y «se importó el 8» se pisan.
 *
 * Mismo tratamiento del tiempo que el historial de anime: la FECHA sube a un encabezado de día y
 * la fila sólo lleva la hora — así no conviven dos formatos de fecha en la misma columna. Los
 * episodios seguidos de la misma serie se funden en una fila: una sesión de anoche no son seis.
 */
import { computed, onMounted, ref } from 'vue'
import { useMediaStore } from '@/stores/media'
import { dayLabel, hourLabel } from '@/lib/format'
import { imgProxy } from '@/lib/img'
import Icon from '@/components/ui/Icon.vue'
import Spinner from '@/components/ui/Spinner.vue'
import EmptyState from '@/components/ui/EmptyState.vue'
import ErrorState from '@/components/ui/ErrorState.vue'

const store = useMediaStore()
const lente = rememberedRef('media:history:lens', 'watched')

onMounted(() => { if (!store.historyLoaded) store.loadHistory() })

const _dia = (ts) => { const d = new Date(ts * 1000); d.setHours(0, 0, 0, 0); return d.getTime() }

/* Agrupa por día y funde episodios consecutivos de la misma serie. La lista llega de más reciente
   a más antiguo, así que dentro del grupo los números van al revés y se leen invertidos. */
const vistos = computed(() => {
  const out = []
  let dia = null, grupo = null
  for (const w of store.watched) {
    const d = _dia(w.at)
    if (!dia || dia.clave !== d) {
      dia = { clave: d, label: dayLabel(w.at), filas: [] }
      out.push(dia)
      grupo = null
    }
    /* Se funden episodios de la MISMA serie y la MISMA temporada. Sin la segunda condición la
       fila decía «T5 · Episodios 8, 9, 10, 1, 2, 3, 4»: eran el final de la 4 y el principio de
       la 5 de la misma tarde, etiquetados con la temporada del primero. */
    const mismaTanda = grupo && w.kind === 'series' && grupo.kind === 'series' &&
      grupo.series_id === w.series_id && grupo.season === w.season
    if (mismaTanda) { grupo.nums.push(w.num); grupo.desde = w.at; continue }
    grupo = { ...w, nums: [w.num], desde: w.at }
    dia.filas.push(grupo)
  }
  return out
})

const grabs = computed(() => {
  const out = []
  let dia = null
  for (const g of store.grabs) {
    const d = _dia(g.at)
    if (!dia || dia.clave !== d) { dia = { clave: d, label: dayLabel(g.at), filas: [] }; out.push(dia) }
    dia.filas.push(g)
  }
  return out
})

function episodios(g) {
  if (g.kind === 'movie') return 'Película'
  if (g.nums.length === 1) return `${g.season}x${String(g.nums[0] || 0).padStart(2, '0')} · ${g.ep_title || 'Sin título'}`
  const a = g.nums[g.nums.length - 1], b = g.nums[0]
  const seguido = g.nums.length === Math.abs(b - a) + 1
  return seguido
    ? `T${g.season} · Episodios ${Math.min(a, b)}-${Math.max(a, b)}`
    : `T${g.season} · Episodios ${[...g.nums].reverse().join(', ')}`
}
const rango = (g) => (g.desde === g.at ? hourLabel(g.at) : `${hourLabel(g.desde)} – ${hourLabel(g.at)}`)

function abrir(g) {
  store.openById(g.kind, g.kind === 'movie' ? g.movie_id : g.series_id)
}
</script>

<template>
  <div class="mh">
    <header class="mh__head">
      <div>
        <p class="eyebrow"><span class="tick" /> LO QUE HA PASADO</p>
        <h1 class="mh__h1">Historial</h1>
      </div>
    </header>

    <div class="toolbar">
      <div class="filters">
        <button class="pill" :class="{ 'is-active': lente === 'watched' }" @click="lente = 'watched'">
          <Icon name="play" :size="13" /> Visto
          <span class="pill__n">{{ store.watched.length }}</span>
        </button>
        <button class="pill" :class="{ 'is-active': lente === 'grabs' }" @click="lente = 'grabs'">
          <Icon name="download" :size="13" /> Bajado
          <span class="pill__n">{{ store.grabs.length }}</span>
        </button>
      </div>
    </div>

    <Spinner v-if="store.historyLoading && !store.historyLoaded" />
    <ErrorState v-else-if="store.historyError" title="No se pudo cargar el historial."
                :detail="store.historyError" @retry="store.loadHistory()" />

    <template v-else-if="lente === 'watched'">
      <EmptyState v-if="!vistos.length" icon="play" title="Todavía no has visto nada."
                  hint="Lo que reproduzcas desde aquí aparecerá en esta lista." />
      <div v-else class="days">
        <section v-for="d in vistos" :key="d.clave" class="day">
          <div class="day__head"><span class="day__label">{{ d.label }}</span><span class="day__line" /></div>
          <button v-for="g in d.filas" :key="g.id" class="row" @click="abrir(g)">
            <div class="row__still">
              <img v-if="g.still" :src="imgProxy(g.still, 240)" :alt="g.title" loading="lazy" decoding="async" />
            </div>
            <div class="row__main">
              <p class="row__title">{{ g.title }}</p>
              <p class="row__sub">{{ episodios(g) }}</p>
            </div>
            <!-- Lo dejado a medias se distingue de lo terminado: son cosas distintas y la única
                 pista que tenías era la barra del riel, que aquí no está. -->
            <span v-if="!g.watched && g.pos" class="row__tag is-half">a medias</span>
            <span class="row__time">{{ rango(g) }}</span>
          </button>
        </section>
      </div>
    </template>

    <template v-else>
      <EmptyState v-if="!grabs.length" icon="download" title="Sin descargas registradas."
                  hint="Aquí queda lo que Sonarr y Radarr han cogido e importado." />
      <div v-else class="days">
        <section v-for="d in grabs" :key="d.clave" class="day">
          <div class="day__head"><span class="day__label">{{ d.label }}</span><span class="day__line" /></div>
          <div v-for="g in d.filas" :key="g.id" class="row is-static">
            <span class="row__ev" :class="`is-${g.event === 'Cogido' ? 'grab' : 'imp'}`">{{ g.event }}</span>
            <div class="row__main">
              <p class="row__rel">{{ g.title }}</p>
              <p class="row__sub">
                <template v-if="g.quality">{{ g.quality }}</template>
                <template v-if="g.indexer"> · {{ g.indexer }}</template>
              </p>
            </div>
            <span class="row__time">{{ hourLabel(g.at) }}</span>
          </div>
        </section>
      </div>
    </template>
  </div>
</template>

<style scoped>
.mh { display: block; max-width: var(--content-max); margin: 0 auto; padding: 0 var(--s-6) var(--s-8); }
.mh__head { padding: var(--s-5) 0 var(--s-4); }
.mh__h1 { font-family: var(--font-display); font-size: var(--fs-2xl); }

.days { display: flex; flex-direction: column; gap: var(--s-5); margin-top: var(--s-4); }
.day { display: flex; flex-direction: column; gap: var(--s-2); }
.day__head { display: flex; align-items: center; gap: var(--s-3); }
.day__label { font-family: var(--font-display); font-size: var(--fs-base); font-weight: 600; }
.day__label::first-letter { text-transform: uppercase; }
.day__line { flex: 1; height: 1px; background: var(--line); }

.row { display: flex; align-items: center; gap: var(--s-3); width: 100%; text-align: left;
  padding: var(--s-2) var(--s-3); border-radius: var(--r-md); background: var(--surface);
  border: 1px solid var(--line); transition: border-color var(--t-fast), transform var(--t-fast); }
.row:not(.is-static) { cursor: pointer; }
.row:not(.is-static):hover { border-color: var(--line-strong); transform: translateX(2px); }
.row__still { flex: none; width: 5rem; aspect-ratio: 16/9; border-radius: var(--r-xs); overflow: hidden;
  background: var(--surface-3); }
.row__still img { width: 100%; height: 100%; object-fit: cover; }
.row__main { min-width: 0; flex: 1; }
.row__title { margin: 0; font-size: var(--fs-sm); font-weight: 600; color: var(--ink);
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.row__rel { margin: 0; font-family: var(--font-mono); font-size: var(--fs-xs); color: var(--ink-soft);
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.row__sub { margin: 2px 0 0; font-size: var(--fs-xs); color: var(--ink-faint);
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.row__time { flex: none; font-family: var(--font-mono); font-size: var(--fs-2xs); color: var(--ink-faint); }
.row__tag { flex: none; font-size: var(--fs-2xs); font-weight: 700; }
.row__tag.is-half { color: var(--gold); }
.row__ev { flex: none; min-width: 5.5rem; font-family: var(--font-mono); font-size: var(--fs-2xs);
  font-weight: 700; }
.row__ev.is-grab { color: var(--cyan); }
.row__ev.is-imp { color: var(--jade); }

@media (max-width: 640px) {
  .mh { padding: 0 var(--s-4) var(--s-8); }
  .row__still { width: 3.5rem; }
}
</style>
