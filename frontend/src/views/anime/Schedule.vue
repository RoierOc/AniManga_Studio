<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useAnimeStore } from '@/stores/anime'
import { fmtCountdown, fmtAgo } from '@/lib/anime'
import { imgProxy } from '@/lib/img'
import Icon from '@/components/ui/Icon.vue'
import EmptyState from '@/components/ui/EmptyState.vue'

const store = useAnimeStore()
const nowTick = ref(Date.now())
let timer = null

onMounted(() => {
  if (!store.seasonal.length) store.loadSeasonal()
  store.loadAiring()
  timer = setInterval(() => { nowTick.value = Date.now() }, 60000)   // countdown vivo
})
onUnmounted(() => { if (timer) clearInterval(timer) })

// Columnas Lun→Dom (JS getDay: 0=Dom … 6=Sáb)
const WEEK = [
  { d: 1, label: 'Lunes' }, { d: 2, label: 'Martes' }, { d: 3, label: 'Miércoles' },
  { d: 4, label: 'Jueves' }, { d: 5, label: 'Viernes' }, { d: 6, label: 'Sábado' }, { d: 0, label: 'Domingo' },
]

// Próximos estrenos (≤ 8 días): biblioteca en emisión + populares de temporada.
const entries = computed(() => {
  const now = nowTick.value / 1000
  const HORIZON = 8 * 86400
  const out = []
  const seen = new Set()
  for (const a of store.library) {
    const inf = store.airing[a.al_id]
    const at = inf?.next_airing_at
    if (!at || at < now || at > now + HORIZON) continue
    seen.add(a.al_id)
    out.push({ anime: a, ep: inf.next_episode, at, mine: true })
  }
  for (const a of store.seasonalPopular) {
    const at = a.airing_at
    if (!at || at < now || at > now + HORIZON || seen.has(a.al_id)) continue
    out.push({ anime: a, ep: a.next_episode, at, mine: false })
  }
  return out
})

const days = computed(() => {
  const todayD = new Date(nowTick.value).getDay()
  return WEEK.map(w => ({
    ...w,
    isToday: w.d === todayD,
    items: entries.value.filter(e => new Date(e.at * 1000).getDay() === w.d).sort((x, y) => x.at - y.at),
  }))
})
const hasAny = computed(() => entries.value.length > 0)

// Misma fecha de calendario local (no "últimas 24h") para casar con la cabecera "HOY".
const sameLocalDay = (aSec, bMs) => {
  const a = new Date(aSec * 1000), b = new Date(bMs)
  return a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate()
}

// "Emitido hoy": episodios que YA SALIERON hoy (emisión anterior, en el pasado), con
// el tiempo transcurrido. Antes se derivaba de `entries` (futuro) filtrando por día de
// la semana → pescaba el episodio de LA SEMANA QUE VIENE (mismo día) y mostraba "en 6d".
// Ahora se usa la emisión previa real: `last_aired_at` del backend, o `next_airing_at − 7d`
// (semanal) como respaldo. Fuente: biblioteca (store.airing) + populares de temporada.
const airedToday = computed(() => {
  const now = nowTick.value / 1000
  const out = []
  const seen = new Set()
  const consider = (anime, at, ep, mine) => {
    if (!at || at > now || !sameLocalDay(at, nowTick.value)) return
    if (seen.has(anime.al_id)) return
    seen.add(anime.al_id)
    out.push({ anime, ep, at, mine })
  }
  for (const a of store.library) {
    const inf = store.airing[a.al_id]
    if (!inf) continue
    let at = inf.last_aired_at, ep = inf.last_episode
    if (!at && inf.next_airing_at) { at = inf.next_airing_at - 7 * 86400; ep = (inf.next_episode || 1) - 1 }
    consider(a, at, ep, true)
  }
  for (const a of store.seasonalPopular) {
    let at = a.last_aired_at, ep = a.last_episode
    if (!at && a.airing_at) { at = a.airing_at - 7 * 86400; ep = (a.next_episode || 1) - 1 }
    consider(a, at, ep, false)
  }
  return out.sort((x, y) => y.at - x.at)   // más reciente primero
})

const todayLabel = computed(() =>
  new Date(nowTick.value).toLocaleDateString([], { weekday: 'long', day: 'numeric', month: 'long' }))
const timeLabel = (at) => new Date(at * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
function countdownLabel(at) {
  const c = fmtCountdown(at, nowTick.value / 1000)
  if (!c) return 'ahora'
  if (c.d > 0) return `en ${c.d}d ${c.h}h`
  if (c.h > 0) return `en ${c.h}h ${c.m}m`
  return `en ${c.m}m`
}
function agoLabel(at) {
  const c = fmtAgo(at, nowTick.value / 1000)
  if (!c || c.diff < 60) return 'recién emitido'
  if (c.d > 0) return `hace ${c.d}d ${c.h}h`
  if (c.h > 0) return `hace ${c.h}h ${c.m}m`
  return `hace ${c.m}m`
}
const openEntry = (e) => e.mine ? store.openDetail(e.anime) : store.openPreview(e.anime)
</script>

<template>
  <div class="sched">
    <header class="hero">
      <div>
        <p class="hero__eyebrow"><span class="hero__tick" /> CALENDARIO</p>
        <h1>Estrenos</h1>
      </div>
    </header>

    <EmptyState v-if="!hasAny && !airedToday.length && !store.seasonalLoading" icon="clock" title="No hay estrenos próximos."
                hint="Añade series en emisión o revisa la pestaña Temporada." />

    <!-- Emitido hoy: episodios que YA salieron hoy, con el tiempo transcurrido -->
    <section v-if="airedToday.length" class="today">
      <div class="today__head">
        <span class="today__badge today__badge--aired"><span class="today__dot" /> EMITIDO HOY</span>
        <h2 class="today__date">{{ todayLabel }}</h2>
        <span class="today__n">{{ airedToday.length }} {{ airedToday.length === 1 ? 'episodio' : 'episodios' }}</span>
      </div>
      <div class="today__row">
        <button v-for="e in airedToday" :key="(e.anime.al_id || e.anime.id) + '-a' + e.ep"
                class="tcard tcard--aired" @click="openEntry(e)">
          <div class="tcard__cov">
            <img v-if="e.anime.cover" :src="imgProxy(e.anime.cover, 160)" loading="lazy" decoding="async" alt=""
                 @load="$event.target.classList.add('is-loaded')" />
            <span v-if="e.mine" class="tcard__mine" title="En tu biblioteca">★</span>
          </div>
          <div class="tcard__info">
            <span class="tcard__title">{{ e.anime.title }}</span>
            <span class="tcard__ep">Episodio {{ e.ep || '?' }}</span>
            <div class="tcard__time">
              <Icon name="check" :size="13" /><strong>{{ timeLabel(e.at) }}</strong>
              <span class="tcard__cd tcard__cd--aired">{{ agoLabel(e.at) }}</span>
            </div>
          </div>
        </button>
      </div>
    </section>

    <h3 v-if="hasAny && airedToday.length" class="week__title">Próximos esta semana</h3>
    <div v-if="hasAny" class="week">
      <section v-for="day in days" :key="day.d" class="col" :class="{ 'is-today': day.isToday }">
        <div class="col__head">
          <span class="col__day">{{ day.label }}</span>
          <span v-if="day.isToday" class="col__today">HOY</span>
        </div>
        <div v-if="!day.items.length" class="col__empty">—</div>
        <button v-for="e in day.items" :key="(e.anime.al_id || e.anime.id) + '-' + e.ep" class="ent" @click="openEntry(e)">
          <div class="ent__cov">
            <img v-if="e.anime.cover" :src="imgProxy(e.anime.cover, 160)" loading="lazy" decoding="async" alt=""
                 @load="$event.target.classList.add('is-loaded')" />
            <span v-if="e.mine" class="ent__mine" title="En tu biblioteca">★</span>
          </div>
          <div class="ent__info">
            <span class="ent__title">{{ e.anime.title }}</span>
            <span class="ent__ep">EP {{ e.ep || '?' }} · {{ timeLabel(e.at) }}</span>
            <span class="ent__cd">{{ countdownLabel(e.at) }}</span>
          </div>
        </button>
      </section>
    </div>
  </div>
</template>

<style scoped>
.sched { max-width: var(--content-max); margin: 0 auto; padding: 0 var(--s-6) var(--s-8); }
.hero { padding: var(--s-5) 0; }
.hero__eyebrow { display: flex; align-items: center; gap: var(--s-2); font-family: var(--font-mono); font-size: var(--fs-2xs); letter-spacing: var(--tracking-caps); color: var(--azure); margin-bottom: var(--s-2); }
.hero__tick { width: 0.875rem; height: 1px; background: var(--azure); box-shadow: 0 0 8px var(--azure-glow); }

/* Hoy — estrenos del día destacados con hora exacta */
.today { margin-bottom: var(--s-7); }
.today__head { display: flex; align-items: center; gap: var(--s-3); margin-bottom: var(--s-4); flex-wrap: wrap; }
.today__badge { display: inline-flex; align-items: center; gap: 0.375rem; font-family: var(--font-mono); font-size: var(--fs-2xs);
  font-weight: 700; letter-spacing: var(--tracking-caps); color: var(--azure-bright); padding: 3px 0.625rem;
  border-radius: var(--r-pill); background: var(--azure-haze); border: 1px solid var(--azure-glow); }
.today__dot { width: 0.4375rem; height: 0.4375rem; border-radius: 50%; background: var(--azure-bright); box-shadow: 0 0 6px var(--azure-glow); animation: pulse-live 2s var(--ease-drift) infinite; }
.today__date { font-family: var(--font-display); font-size: var(--fs-lg); text-transform: capitalize; }
.today__n { font-size: var(--fs-xs); color: var(--ink-faint); }
.today__row { display: flex; gap: var(--s-3); overflow-x: auto; padding-bottom: var(--s-2); }
.tcard { flex-shrink: 0; width: 19rem; display: flex; gap: var(--s-3); text-align: left; padding: var(--s-3);
  border-radius: var(--r-md); background: var(--surface); border: 1px solid var(--line);
  transition: border-color var(--t-base), box-shadow var(--t-base), transform var(--t-base) var(--ease-snap); }
.tcard:hover { border-color: var(--azure-glow); box-shadow: var(--shadow-md); transform: translateY(-3px); }
.tcard.is-soon { border-color: color-mix(in srgb, var(--cyan) 45%, transparent); box-shadow: inset 0 0 0 1px var(--cyan-glow); }
.tcard__cov { position: relative; flex-shrink: 0; width: 3.5rem; aspect-ratio: 2/3; border-radius: var(--r-sm); overflow: hidden; background: var(--surface-3); }
.tcard__cov img { width: 100%; height: 100%; object-fit: cover; opacity: 0; transition: opacity var(--t-slow); }
.tcard__cov img.is-loaded { opacity: 1; }
.tcard__mine { position: absolute; top: 2px; right: 3px; font-size: var(--fs-xs); color: var(--gold); text-shadow: 0 1px 3px #000; }
.tcard__info { min-width: 0; display: flex; flex-direction: column; gap: 3px; justify-content: center; }
.tcard__title { font-size: var(--fs-sm); font-weight: 600; color: var(--ink); line-height: var(--lh-snug);
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
.tcard__ep { font-family: var(--font-mono); font-size: var(--fs-2xs); color: var(--ink-faint); }
.tcard__time { display: flex; align-items: center; gap: 0.375rem; margin-top: 2px; font-size: var(--fs-sm); color: var(--ink-soft); }
.tcard__time :deep(svg) { color: var(--cyan); }
.tcard__time strong { color: var(--ink); font-family: var(--font-display); }
.tcard__cd { font-family: var(--font-mono); font-size: var(--fs-2xs); color: var(--cyan); }
.tcard.is-soon .tcard__cd { color: var(--azure-bright); font-weight: 700; }

/* Variante "emitido hoy": ya salió → acento jade (éxito) en vez de cian (cuenta atrás) */
.today__badge--aired { color: var(--jade); background: color-mix(in srgb, var(--jade) 12%, transparent); border-color: color-mix(in srgb, var(--jade) 40%, transparent); }
.today__badge--aired .today__dot { background: var(--jade); box-shadow: 0 0 6px color-mix(in srgb, var(--jade) 60%, transparent); animation: none; }
.tcard--aired .tcard__time :deep(svg) { color: var(--jade); }
.tcard__cd--aired { color: var(--jade); }

.week__title { font-family: var(--font-display); font-size: var(--fs-lg); margin-bottom: var(--s-3); color: var(--ink-soft); }
.week { display: grid; grid-template-columns: repeat(7, minmax(0, 1fr)); gap: var(--s-3); }
.col { min-width: 0; background: var(--surface); border: 1px solid var(--line); border-radius: var(--r-md); padding: var(--s-3); display: flex; flex-direction: column; gap: var(--s-2); }
.col.is-today { border-color: var(--azure); box-shadow: inset 0 0 0 1px var(--azure-glow); }
.col__head { display: flex; align-items: center; justify-content: space-between; padding-bottom: var(--s-2); border-bottom: 1px solid var(--line); }
.col__day { font-family: var(--font-display); font-size: var(--fs-sm); font-weight: 600; color: var(--ink); }
.col__today { font-family: var(--font-mono); font-size: var(--fs-2xs); font-weight: 700; color: var(--azure-bright); padding: 1px 0.375rem; border-radius: var(--r-pill); background: var(--azure-haze); }
.col__empty { color: var(--ink-ghost); font-size: var(--fs-sm); text-align: center; padding: var(--s-4) 0; }

.ent { display: flex; gap: var(--s-2); text-align: left; padding: var(--s-2); border-radius: var(--r-sm); transition: background var(--t-fast); }
.ent:hover { background: var(--surface-2); }
.ent__cov { position: relative; flex-shrink: 0; width: 2.5rem; aspect-ratio: 2/3; border-radius: var(--r-xs); overflow: hidden; background: var(--surface-3); }
.ent__cov img { width: 100%; height: 100%; object-fit: cover; opacity: 0; transition: opacity var(--t-slow); }
.ent__cov img.is-loaded { opacity: 1; }
.ent__mine { position: absolute; top: 1px; right: 2px; font-size: var(--fs-2xs); color: var(--gold); text-shadow: 0 1px 3px #000; }
.ent__info { min-width: 0; display: flex; flex-direction: column; gap: 1px; }
.ent__title { font-size: var(--fs-xs); font-weight: 600; color: var(--ink); line-height: var(--lh-snug); display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
.ent__ep { font-family: var(--font-mono); font-size: var(--fs-2xs); color: var(--ink-faint); }
.ent__cd { font-family: var(--font-mono); font-size: var(--fs-2xs); color: var(--cyan); }

@media (max-width: 900px) {
  .week { grid-auto-flow: column; grid-template-columns: none; grid-auto-columns: 14rem; overflow-x: auto; padding-bottom: var(--s-3); }
  .col { scroll-snap-align: start; }
}
@media (max-width: 540px) { .sched { padding: 0 var(--s-4) var(--s-8); } }
</style>
