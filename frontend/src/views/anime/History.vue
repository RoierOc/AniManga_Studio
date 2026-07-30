<script setup>
import { computed, onMounted } from 'vue'
import { useAnimeStore } from '@/stores/anime'
import { imgProxy } from '@/lib/img'
import Icon from '@/components/ui/Icon.vue'
import EmptyState from '@/components/ui/EmptyState.vue'

const store = useAnimeStore()
onMounted(() => { if (!store.historyLoaded) store.loadHistory() })

function open(item) {
  const a = store.library.find(x => x.id === item.anime_id)
  if (a) store.openDetail(a)
}

/* El historial eran 500 filas planas: el título de la serie repetido una y otra vez (tres
   «Monster» seguidos), y dos formatos de fecha MEZCLADOS en la misma lista («hace 1 d» junto a
   «21/7/2026»), porque `relativeTime` cambia de forma a los 7 días. Se arregla en dos pasos:
     · la FECHA sube a un encabezado de día, así la fila sólo lleva la hora;
     · los episodios seguidos de la misma serie se funden en UNA fila («Episodios 3-5»).
   Nada se pierde: la sesión de anoche se lee de un vistazo en vez de ocupar seis filas. */
const _dia = (ts) => { const d = new Date(ts * 1000); d.setHours(0, 0, 0, 0); return d.getTime() }

function etiquetaDia(ts) {
  const hoy = _dia(Date.now() / 1000)
  const dias = Math.round((hoy - _dia(ts)) / 86400000)
  if (dias <= 0) return 'Hoy'
  if (dias === 1) return 'Ayer'
  if (dias < 7) return new Date(ts * 1000).toLocaleDateString('es', { weekday: 'long' })
  const d = new Date(ts * 1000)
  const mismoAnio = d.getFullYear() === new Date().getFullYear()
  return d.toLocaleDateString('es', { day: 'numeric', month: 'long', ...(mismoAnio ? {} : { year: 'numeric' }) })
}
const hora = (ts) => new Date(ts * 1000).toLocaleTimeString('es', { hour: '2-digit', minute: '2-digit' })

const dias = computed(() => {
  const out = []
  let dia = null, grupo = null
  for (const h of store.history) {
    const d = _dia(h.watched_at)
    if (!dia || dia.clave !== d) { dia = { clave: d, etiqueta: etiquetaDia(h.watched_at), filas: [] }; out.push(dia); grupo = null }
    // Episodios CONSECUTIVOS de la misma serie = una sola fila. El historial llega ordenado de
    // más reciente a más antiguo, así que los `eps` se guardan al revés y se leen invertidos.
    if (grupo && grupo.anime_id === h.anime_id) { grupo.eps.push(h.episode); grupo.desde = h.watched_at; continue }
    grupo = { ...h, eps: [h.episode], desde: h.watched_at }
    dia.filas.push(grupo)
  }
  return out
})

function episodios(g) {
  if (g.eps.length === 1) return `Episodio ${g.eps[0]}`
  const a = g.eps[g.eps.length - 1], b = g.eps[0]
  // Sólo se dice «3-5» si de verdad es un tramo seguido; si hay saltos, se enumeran.
  const seguido = g.eps.length === Math.abs(b - a) + 1
  return seguido ? `Episodios ${Math.min(a, b)}-${Math.max(a, b)}` : `Episodios ${[...g.eps].reverse().join(', ')}`
}
function rango(g) {
  return g.desde === g.watched_at ? hora(g.watched_at) : `${hora(g.desde)} – ${hora(g.watched_at)}`
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

    <div v-if="!store.historyLoaded" class="hist__list">
      <div v-for="n in 8" :key="n" class="hrow hrow--skel">
        <div class="hrow__cover skel" />
        <div class="hrow__meta"><div class="skel skel--t" /><div class="skel skel--s" /></div>
      </div>
    </div>
    <EmptyState v-else-if="!store.history.length" icon="heart" title="Aún no has visto nada." />

    <div v-else class="hist__days">
      <section v-for="d in dias" :key="d.clave" class="hday">
        <h2 class="hday__lbl">{{ d.etiqueta }}</h2>
        <div class="hist__list">
          <button v-for="(g, i) in d.filas" :key="i" class="hrow" @click="open(g)">
            <div class="hrow__cover">
              <img v-if="g.cover" :src="imgProxy(g.cover, 96)" :alt="g.title" loading="lazy" decoding="async" />
              <Icon v-else name="film" :size="14" />
            </div>
            <div class="hrow__meta">
              <div class="hrow__title">{{ g.title }}</div>
              <div class="hrow__sub">{{ episodios(g) }}</div>
            </div>
            <span class="hrow__time">{{ rango(g) }}</span>
            <Icon name="play" :size="16" class="hrow__play" />
          </button>
        </div>
      </section>
    </div>
  </div>
</template>

<style scoped>
.hist { max-width: 57.5rem; margin: 0 auto; padding: 0 var(--s-6) var(--s-8); }
.hist__head { display: flex; align-items: flex-end; justify-content: space-between; gap: var(--s-4); padding: var(--s-5) 0; }
.eyebrow { display: flex; align-items: center; gap: var(--s-2); font-family: var(--font-mono); font-size: var(--fs-2xs); letter-spacing: var(--tracking-caps); color: var(--azure); margin-bottom: var(--s-2); }
.tick { width: 0.875rem; height: 1px; background: var(--azure); box-shadow: 0 0 8px var(--azure-glow); }
.clear { display: inline-flex; align-items: center; gap: 0.375rem; padding: var(--s-2) var(--s-3); border-radius: var(--r-sm); border: 1px solid var(--line); color: var(--ink-faint); font-size: var(--fs-xs); transition: all var(--t-fast); }
.clear:hover { color: var(--coral); border-color: color-mix(in srgb, var(--coral) 40%, transparent); }

.empty { display: flex; flex-direction: column; align-items: center; gap: var(--s-3); padding: var(--s-8) 0; color: var(--ink-faint); }

.hrow--skel { pointer-events: none; }
.skel { background: linear-gradient(100deg, var(--surface-2) 30%, var(--surface-3) 50%, var(--surface-2) 70%);
  background-size: 200% 100%; animation: shimmer 1.4s linear infinite; border-radius: var(--r-sm); }
.skel--t { height: 0.9rem; width: 45%; margin-bottom: 0.375rem; }
.skel--s { height: 0.7rem; width: 28%; }

.hist__days { display: flex; flex-direction: column; gap: var(--s-5); }
/* La fecha sube al encabezado del día: la fila ya sólo lleva la hora, y así no conviven
   «hace 1 d» y «21/7/2026» en la misma lista. */
.hday__lbl {
  font-family: var(--font-mono); font-size: var(--fs-2xs); letter-spacing: var(--tracking-caps);
  text-transform: uppercase; color: var(--ink-faint); margin-bottom: var(--s-2);
}
.hist__list { display: flex; flex-direction: column; gap: var(--s-2); }
.hrow__time { font-family: var(--font-mono); font-size: var(--fs-2xs); color: var(--ink-faint); flex-shrink: 0; }
.hrow { display: flex; align-items: center; gap: var(--s-4); padding: var(--s-2) var(--s-3); border-radius: var(--r-md); border: 1px solid var(--line); background: var(--surface); text-align: left; transition: all var(--t-fast); }
.hrow:hover { border-color: var(--line-strong); background: var(--surface-2); }
.hrow:hover .hrow__play { color: var(--azure-bright); transform: scale(1.15); }
/* La caja era apaisada (64x40) y dentro va un PÓSTER 2:3: `object-fit: cover` recortaba una
   tira de la cara. Ahora la caja tiene la forma del contenido. */
.hrow__cover { position: relative; width: 2.25rem; aspect-ratio: 2 / 3; flex-shrink: 0;
  border-radius: var(--r-sm); overflow: hidden; background: var(--surface-3);
  display: grid; place-items: center; color: var(--ink-ghost); }
.hrow__cover img { width: 100%; height: 100%; object-fit: cover; }
.hrow__meta { flex: 1; min-width: 0; }
.hrow__title { font-size: var(--fs-sm); font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.hrow__sub { font-size: var(--fs-xs); color: var(--ink-faint); margin-top: 2px; }
.hrow__play { color: var(--ink-faint); transition: all var(--t-fast) var(--ease-snap); }

@media (max-width: 640px) { .hist { padding: 0 var(--s-4) var(--s-8); } }
</style>
