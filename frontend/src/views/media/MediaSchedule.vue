<script setup>
/* Estrenos de Cine — el equivalente del calendario de anime, con la forma que pide ESTE dominio.
 *
 * En anime la rejilla de siete columnas funciona porque todo se emite semanalmente. Aquí no: un
 * episodio sale el jueves y una película en diciembre. Con columnas de semana, «Dune: Part Three»
 * no cabría en la vista. Por eso es una AGENDA vertical agrupada por día, que aguanta igual de bien
 * lo de mañana y lo de dentro de cinco meses.
 *
 * Cero estado propio: todo vive en el store (`agenda`), así que cambiar de pestaña no lo pierde.
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

/* Ventanas, no un deslizador: son las tres preguntas que se hacen de verdad. «Este mes» va con
   180 días hacia delante porque los estrenos de cine se anuncian con esa antelación — recortarlo
   a 30 escondería justo lo que tiene gracia saber. */
const RANGOS = [
  { id: 'semana', label: 'Esta semana', back: 2, days: 7 },
  { id: 'mes', label: 'Este mes', back: 7, days: 31 },
  { id: 'lejos', label: 'Todo lo anunciado', back: 7, days: 180 },
]
const rango = ref('mes')

function elegir(id) {
  rango.value = id
  const r = RANGOS.find(x => x.id === id)
  store.loadAgenda({ back: r.back, days: r.days })
}

onMounted(() => { if (!store.agendaLoaded) elegir(rango.value) })

const ahora = Math.floor(Date.now() / 1000)

/* Un episodio ocurre en un INSTANTE (se emite a las 22:00) y una película sale en una FECHA. Por
   eso el backend manda `day` en las películas: pasar su medianoche UTC por el reloj local la
   empujaba al día anterior y la agenda contradecía a Radarr. */
const tsDe = (it) => (it.day ? Date.parse(`${it.day}T00:00:00`) / 1000 : it.ts)

/* Agrupado por día de calendario, no por «días desde hoy»: si no, dos episodios de la misma noche
   caen en cubos distintos según la hora a la que abras la app. */
const dias = computed(() => {
  const out = []
  let dia = null
  for (const it of store.agenda) {
    const ts = tsDe(it)
    const clave = new Date(ts * 1000).toDateString()
    if (!dia || dia.clave !== clave) {
      const label = dayLabel(ts, { future: true })
      dia = {
        clave, ts, label, pasado: ts < ahora, items: [],
        // La fecha corta acompaña a «Hoy», «Mañana» o «jueves», que no dicen QUÉ día son. Cuando
        // la etiqueta ya es la fecha («11 de agosto»), repetirla al lado es ruido.
        fecha: /\d/.test(label) ? '' : new Date(ts * 1000).toLocaleDateString('es', { day: 'numeric', month: 'short' }),
      }
      out.push(dia)
    }
    dia.items.push(it)
  }
  return out
})

const pendientes = computed(() => store.agenda.filter(i => tsDe(i) >= ahora).length)

// Un episodio ya emitido que NO tienes es lo único accionable de la lista: se marca.
const falta = (it) => tsDe(it) < ahora && !it.have

function abrir(it) {
  store.openById(it.kind, it.kind === 'movie' ? it.movie_id : it.series_id)
}
</script>

<template>
  <div class="msch">
    <header class="msch__head">
      <div>
        <p class="eyebrow"><span class="tick" /> CALENDARIO</p>
        <h1 class="msch__h1">Estrenos</h1>
      </div>
      <p v-if="store.agendaLoaded" class="msch__sub">{{ pendientes }} por llegar</p>
    </header>

    <div class="toolbar">
      <div class="filters">
        <button v-for="r in RANGOS" :key="r.id" class="pill" :class="{ 'is-active': rango === r.id }"
                @click="elegir(r.id)">{{ r.label }}</button>
      </div>
    </div>

    <!-- Un servicio caído se DICE: si Sonarr no contesta, la lista de películas es válida pero
         está incompleta, y callarlo la convierte en una mentira. -->
    <p v-if="Object.keys(store.agendaErrors).length" class="msch__warn">
      <Icon name="alert" :size="14" />
      {{ Object.keys(store.agendaErrors).join(' y ') }} no respondió: faltan sus estrenos.
    </p>

    <Spinner v-if="store.agendaLoading && !store.agenda.length" />
    <ErrorState v-else-if="store.agendaError" title="No se pudo cargar la agenda."
                :detail="store.agendaError" @retry="elegir(rango)" />

    <EmptyState v-else-if="!dias.length" icon="clock" title="Nada anunciado en este plazo."
                hint="Prueba con un plazo más largo, o añade series en emisión y películas que aún no han salido." />

    <div v-else class="agenda">
      <section v-for="d in dias" :key="d.clave" class="day" :class="{ 'is-past': d.pasado }">
        <div class="day__head">
          <span class="day__label">{{ d.label }}</span>
          <span v-if="d.fecha" class="day__date">{{ d.fecha }}</span>
          <span class="day__line" />
        </div>

        <button v-for="it in d.items" :key="it.id" class="row" :class="{ 'is-missing': falta(it) }"
                @click="abrir(it)">
          <div class="row__cov">
            <img v-if="it.poster" :src="imgProxy(it.poster, 120)" :alt="it.title" loading="lazy" decoding="async"
                 @load="$event.target.classList.add('is-loaded')" />
          </div>
          <div class="row__main">
            <p class="row__title">{{ it.title }}</p>
            <p class="row__sub">
              <template v-if="it.kind === 'series'">
                <b class="row__n">{{ it.season }}x{{ String(it.num || 0).padStart(2, '0') }}</b>
                {{ it.ep_title || 'Sin título' }}
              </template>
              <template v-else>Película</template>
            </p>
          </div>
          <div class="row__right">
            <span class="row__ev" :class="`is-${it.event === 'Episodio' ? 'ep' : 'mv'}`">{{ it.event }}</span>
            <!-- La hora SÓLO en episodios: un estreno en físico es una FECHA sin hora, y al pasarla
                 por el reloj salía un «19:00» inventado (la conversión de zona horaria). -->
            <span v-if="it.kind === 'series'" class="row__time">{{ hourLabel(it.ts) }}</span>
          </div>
          <!-- Tres estados y no dos: «lo tengo», «salió y no lo tengo» y «aún no ha salido».
               Fundir los dos últimos en un hueco vacío es lo que hace que no te enteres. -->
          <span v-if="it.have" class="row__flag is-have"><Icon name="check" :size="13" /> En disco</span>
          <span v-else-if="tsDe(it) < ahora" class="row__flag is-miss">No lo tienes</span>
          <span v-else class="row__flag is-soon">Aún no</span>
        </button>
      </section>
    </div>
  </div>
</template>

<style scoped>
.msch { display: block; max-width: var(--content-max); margin: 0 auto; padding: 0 var(--s-6) var(--s-8); }
.msch__head { display: flex; align-items: flex-end; justify-content: space-between; gap: var(--s-4);
  padding: var(--s-5) 0 var(--s-4); }
.msch__h1 { font-family: var(--font-display); font-size: var(--fs-2xl); }
.msch__sub { font-size: var(--fs-sm); color: var(--ink-faint); }
.msch__warn { display: flex; align-items: center; gap: var(--s-2); margin: var(--s-3) 0 0;
  color: var(--warn); font-size: var(--fs-sm); }

.agenda { display: flex; flex-direction: column; gap: var(--s-5); margin-top: var(--s-5); }
.day { display: flex; flex-direction: column; gap: var(--s-2); }
/* Lo que ya pasó se apaga pero NO se esconde: es la parte accionable de la lista (salió y no lo
   tienes). Se distingue por peso, no por presencia. */
.day.is-past .day__label { color: var(--ink-faint); }
.day__head { display: flex; align-items: center; gap: var(--s-3); margin-bottom: var(--s-1); }
.day__label { font-family: var(--font-display); font-size: var(--fs-base); font-weight: 600; }
.day__label::first-letter { text-transform: uppercase; }
.day__date { font-family: var(--font-mono); font-size: var(--fs-2xs); color: var(--ink-faint); }
.day__line { flex: 1; height: 1px; background: var(--line); }

.row { display: flex; align-items: center; gap: var(--s-3); width: 100%; text-align: left;
  padding: var(--s-2) var(--s-3); border-radius: var(--r-md); background: var(--surface);
  border: 1px solid var(--line); cursor: pointer;
  transition: border-color var(--t-fast), transform var(--t-fast); }
.row:hover { border-color: var(--line-strong); transform: translateX(2px); }
.row.is-missing { border-color: color-mix(in srgb, var(--gold) 40%, transparent); }
.row__cov { flex: none; width: 2.5rem; aspect-ratio: 2/3; border-radius: var(--r-xs); overflow: hidden;
  background: var(--surface-3); }
.row__cov img { width: 100%; height: 100%; object-fit: cover; opacity: 0; transition: opacity var(--t-slow); }
.row__cov img.is-loaded { opacity: 1; }
.row__main { min-width: 0; flex: 1; }
.row__title { margin: 0; font-size: var(--fs-sm); font-weight: 600; color: var(--ink);
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.row__sub { margin: 2px 0 0; font-size: var(--fs-xs); color: var(--ink-faint);
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.row__n { font-family: var(--font-mono); color: var(--azure-bright); font-weight: 700; }
.row__right { flex: none; display: flex; flex-direction: column; align-items: flex-end; gap: 2px; }
.row__ev { font-family: var(--font-mono); font-size: var(--fs-2xs); font-weight: 700; }
.row__ev.is-ep { color: var(--azure-bright); }
.row__ev.is-mv { color: var(--violet); }
.row__time { font-size: var(--fs-2xs); color: var(--ink-faint); }
.row__flag { flex: none; display: inline-flex; align-items: center; gap: 3px; min-width: 6.5rem;
  justify-content: flex-end; font-size: var(--fs-2xs); font-weight: 600; }
.row__flag.is-have { color: var(--jade); }
.row__flag.is-miss { color: var(--gold); }
.row__flag.is-soon { color: var(--ink-ghost); }

@media (max-width: 640px) {
  .msch { padding: 0 var(--s-4) var(--s-8); }
  .row__flag { display: none; }   /* el color del borde ya lo dice; el texto sobra en estrecho */
}
</style>
