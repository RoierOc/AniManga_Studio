<script setup>
/* Retrospectiva: «tu mes» / «tu año» a pantalla completa.
 *
 * El panel de estadísticas que ya existe es un LIBRO DE CONTABILIDAD en un desplegable de 21 rem:
 * datos buenos presentados como una hoja de cálculo, y mezclando cuánto ocupa el disco con qué has
 * visto tú. Esto es lo contrario: pocas cifras, grandes, con el arte que ya tienes en caché, y
 * pasando de una a otra como páginas.
 *
 * ⚠️ Enseña por separado los episodios VISTOS y los MARCADOS en masa. No es un tecnicismo: medido
 * en el historial real, la mediana del hueco entre dos registros de la misma serie es de 5
 * segundos, o sea que 440 de las 500 entradas son marcados. Un «has visto 500 episodios» sería
 * mentira y se notaría a la primera. Ver `src/api/retrospective.py`.
 */
import { computed, onMounted, onBeforeUnmount, ref, watch } from 'vue'
import { api } from '@/lib/api'
import { imgProxy } from '@/lib/img'
import Icon from '@/components/ui/Icon.vue'
import Spinner from '@/components/ui/Spinner.vue'
import ErrorState from '@/components/ui/ErrorState.vue'

const emit = defineEmits(['close'])

const PERIODOS = [
  { id: 'mes', label: 'Este mes' },
  { id: 'anio', label: 'Este año' },
  { id: 'todo', label: 'Siempre' },
]
const DOW = ['Lunes', 'Martes', 'Miércoles', 'Jueves', 'Viernes', 'Sábado', 'Domingo']

const periodo = ref('anio')
const data = ref(null)
const cargando = ref(true)
const error = ref('')
const pagina = ref(0)

async function cargar() {
  cargando.value = true; error.value = ''
  try {
    data.value = await api.get(`/api/retrospective?periodo=${periodo.value}`)
    pagina.value = 0
  } catch (e) {
    error.value = e?.message || 'No se pudo calcular tu resumen.'
    data.value = null
  } finally {
    cargando.value = false
  }
}

const horas = computed(() => Math.round((data.value?.anime?.seconds || 0) / 3600))
const minutos = computed(() => Math.round((data.value?.anime?.seconds || 0) / 60))
const diaFuerte = computed(() => {
  const w = data.value?.rhythm?.by_weekday || []
  if (!w.length) return null
  const i = w.indexOf(Math.max(...w))
  return w[i] ? DOW[i] : null
})
const horaFuerte = computed(() => {
  const h = data.value?.rhythm?.by_hour || []
  if (!h.length) return null
  const i = h.indexOf(Math.max(...h))
  return h[i] ? i : null
})
/* Barras de las 24 h normalizadas al máximo. Es el único gráfico del resumen y va aquí porque
   ES un dato sobre ti; no tiene nada que ver con que la interfaz cambie según la hora, que es
   una idea distinta y descartada. */
const barrasHora = computed(() => {
  const h = data.value?.rhythm?.by_hour || []
  const max = Math.max(1, ...h)
  return h.map((n, i) => ({ h: i, n, pct: (n / max) * 100 }))
})

// Sólo se muestran las páginas que tienen algo que contar: sin manga leído, no hay página de manga.
const paginas = computed(() => {
  if (!data.value) return []
  const p = ['portada', 'tiempo']
  if (data.value.anime?.top?.length) p.push('top')
  if (data.value.rhythm?.days) p.push('ritmo')
  if (data.value.manga?.chapters) p.push('manga')
  p.push('cierre')
  return p
})
const actual = computed(() => paginas.value[pagina.value] || 'portada')

function ir(d) {
  const n = paginas.value.length
  if (!n) return
  pagina.value = Math.min(n - 1, Math.max(0, pagina.value + d))
}
function onKey(e) {
  if (e.key === 'Escape') emit('close')
  else if (e.key === 'ArrowRight' || e.key === ' ') { e.preventDefault(); ir(1) }
  else if (e.key === 'ArrowLeft') ir(-1)
}

watch(periodo, cargar)
onMounted(() => { cargar(); window.addEventListener('keydown', onKey) })
onBeforeUnmount(() => window.removeEventListener('keydown', onKey))

const fmt = (n) => (n || 0).toLocaleString('es-ES')
</script>

<template>
  <div class="retro" role="dialog" aria-label="Tu resumen">
    <button class="retro__x" data-tip="Cerrar (Esc)" aria-label="Cerrar" @click="emit('close')">
      <Icon name="close" :size="18" />
    </button>

    <div class="retro__periodos">
      <button v-for="p in PERIODOS" :key="p.id" :class="{ on: periodo === p.id }"
              @click="periodo = p.id">{{ p.label }}</button>
    </div>

    <div v-if="cargando" class="retro__estado"><Spinner :size="28" /></div>
    <ErrorState v-else-if="error" class="retro__estado" title="No se pudo calcular tu resumen."
                :detail="error" @retry="cargar" />

    <template v-else-if="data">
      <!-- Fondo: el arte de tu serie más vista, muy atenuado. Decorativo, nunca bajo el texto. -->
      <div v-if="data.anime.top[0]?.cover" class="retro__fondo"
           :style="{ backgroundImage: `url(${imgProxy(data.anime.top[0].cover, 0)})` }" />

      <div class="retro__lienzo" @click="ir(1)">
        <Transition name="pag" mode="out-in">
          <section :key="actual" class="pag">

            <template v-if="actual === 'portada'">
              <span class="pag__over">Tu resumen</span>
              <h1 class="pag__gigante">{{ periodo === 'mes' ? 'Este mes' : periodo === 'anio' ? 'Este año' : 'Todo lo que llevas' }}</h1>
              <p class="pag__pie">Pulsa o usa <kbd>→</kbd> para pasar</p>
            </template>

            <template v-else-if="actual === 'tiempo'">
              <span class="pag__over">Has visto</span>
              <h1 class="pag__gigante">{{ fmt(data.anime.episodes) }}</h1>
              <p class="pag__sub">{{ data.anime.episodes === 1 ? 'episodio' : 'episodios' }}
                 de {{ fmt(data.anime.series) }} {{ data.anime.series === 1 ? 'serie' : 'series' }}</p>
              <p class="pag__grande">
                Son <b>{{ horas >= 1 ? `${fmt(horas)} h` : `${fmt(minutos)} min` }}</b> de anime.
              </p>
              <!-- Honestidad: el marcado masivo NO se cuenta como visto, y se dice. -->
              <p v-if="data.anime.marked" class="pag__nota">
                Aparte marcaste {{ fmt(data.anime.marked) }} episodios como vistos de una vez;
                ésos no cuentan aquí.
              </p>
              <p v-if="data.anime.estimated" class="pag__nota">
                {{ fmt(data.anime.estimated) }} sin duración conocida: contados como ~24 min.
              </p>
            </template>

            <template v-else-if="actual === 'top'">
              <span class="pag__over">Lo que más viste</span>
              <ol class="top">
                <li v-for="(s, i) in data.anime.top" :key="s.id" class="top__fila"
                    :style="{ animationDelay: `${i * 90}ms` }">
                  <b class="top__n">{{ i + 1 }}</b>
                  <img v-if="s.cover" class="top__art" :src="imgProxy(s.cover, 320)" :alt="s.title" />
                  <div class="top__txt">
                    <span class="top__title">{{ s.title }}</span>
                    <span class="top__eps">{{ s.episodes }} {{ s.episodes === 1 ? 'episodio' : 'episodios' }}</span>
                  </div>
                </li>
              </ol>
            </template>

            <template v-else-if="actual === 'ritmo'">
              <span class="pag__over">Tu ritmo</span>
              <div class="ritmo">
                <div class="ritmo__cifra"><b>{{ fmt(data.rhythm.days) }}</b><span>días con algo visto</span></div>
                <div class="ritmo__cifra"><b>{{ fmt(data.rhythm.streak) }}</b><span>días seguidos, tu mejor racha</span></div>
                <div v-if="data.rhythm.best_day.episodes" class="ritmo__cifra">
                  <b>{{ fmt(data.rhythm.best_day.episodes) }}</b><span>en tu día más largo</span>
                </div>
              </div>
              <p v-if="diaFuerte || horaFuerte !== null" class="pag__grande">
                Sobre todo <b v-if="diaFuerte">los {{ diaFuerte.toLowerCase() }}</b><template v-if="diaFuerte && horaFuerte !== null">, </template><b v-if="horaFuerte !== null">a las {{ horaFuerte }}:00</b>.
              </p>
              <div class="reloj" aria-hidden="true">
                <i v-for="b in barrasHora" :key="b.h" :style="{ height: Math.max(2, b.pct) + '%' }"
                   :class="{ on: b.n > 0 }" />
              </div>
              <p class="pag__nota">De 00:00 a 23:00</p>
            </template>

            <template v-else-if="actual === 'manga'">
              <span class="pag__over">Y leyendo</span>
              <h1 class="pag__gigante">{{ fmt(data.manga.chapters) }}</h1>
              <p class="pag__sub">{{ data.manga.chapters === 1 ? 'capítulo' : 'capítulos' }}
                 de {{ fmt(data.manga.works) }} {{ data.manga.works === 1 ? 'obra' : 'obras' }}</p>
            </template>

            <template v-else>
              <span class="pag__over">青</span>
              <h1 class="pag__gigante">Hasta la próxima</h1>
              <p class="pag__sub">Tu historial se guarda entero, mes a mes.</p>
              <button class="pag__btn" @click.stop="pagina = 0">Volver a empezar</button>
            </template>

          </section>
        </Transition>
      </div>

      <div class="retro__puntos">
        <button v-for="(p, i) in paginas" :key="p" :class="{ on: i === pagina }"
                :aria-label="`Página ${i + 1}`" @click.stop="pagina = i" />
      </div>
    </template>
  </div>
</template>

<style scoped>
.retro {
  position: fixed; inset: 0; z-index: var(--z-toast);
  background: var(--void); color: var(--ink);
  display: flex; flex-direction: column; align-items: center; justify-content: center;
  overflow: hidden;
  animation: retro-in var(--t-slow) var(--ease-silk) both;
}
@keyframes retro-in { from { opacity: 0; transform: scale(1.02) } to { opacity: 1; transform: none } }

.retro__fondo {
  position: absolute; inset: 0; background-size: cover; background-position: center;
  opacity: .1; filter: blur(30px) saturate(1.3); transform: scale(1.15); pointer-events: none;
}

.retro__x { position: absolute; top: calc(var(--titlebar-h) + var(--s-4)); right: var(--s-5);
  z-index: 3; color: var(--ink-faint); padding: var(--s-2); border-radius: 50%; }
.retro__x:hover { color: var(--ink); background: var(--surface-2); }

.retro__periodos {
  position: absolute; top: calc(var(--titlebar-h) + var(--s-4)); left: 50%; translate: -50% 0;
  z-index: 3; display: flex; gap: var(--s-1); padding: var(--s-1);
  background: var(--surface); border: 1px solid var(--line); border-radius: var(--r-pill);
}
.retro__periodos button { padding: var(--s-2) var(--s-4); border-radius: var(--r-pill);
  font-size: var(--fs-xs); color: var(--ink-faint); transition: all var(--t-fast); }
.retro__periodos button.on { background: var(--azure-haze); color: var(--azure-bright); }

.retro__estado { position: relative; z-index: 2; }

.retro__lienzo { position: relative; z-index: 2; width: 100%; max-width: 46rem;
  padding: 0 var(--s-6); cursor: pointer; }

.pag { text-align: center; display: flex; flex-direction: column; align-items: center; gap: var(--s-3); }
.pag-enter-active, .pag-leave-active { transition: opacity var(--t-base) var(--ease-silk), transform var(--t-base) var(--ease-silk); }
.pag-enter-from { opacity: 0; transform: translateY(14px); }
.pag-leave-to { opacity: 0; transform: translateY(-14px); }

.pag__over { font-size: var(--fs-2xs); font-weight: 600; letter-spacing: var(--tracking-caps);
  text-transform: uppercase; color: var(--azure-bright); }
.pag__gigante { font-family: var(--font-display); font-weight: 600; line-height: .95;
  letter-spacing: var(--tracking-display); font-size: clamp(3.5rem, 11vw, 8rem); }
.pag__sub { font-size: var(--fs-xl); color: var(--ink-soft); }
.pag__grande { margin-top: var(--s-4); font-size: var(--fs-lg); color: var(--ink-soft); }
.pag__grande b { color: var(--ink); }
.pag__nota { font-size: var(--fs-xs); color: var(--ink-ghost); max-width: 30rem; }
.pag__pie { margin-top: var(--s-6); font-size: var(--fs-xs); color: var(--ink-ghost); }
.pag__pie kbd { font-family: var(--font-mono); border: 1px solid var(--line-2); border-radius: var(--r-xs);
  padding: 1px 5px; }
.pag__btn { margin-top: var(--s-5); padding: var(--s-3) var(--s-5); border-radius: var(--r-pill);
  background: var(--ink); color: var(--void); font-weight: 600; font-size: var(--fs-sm); }

/* ── Top ── */
.top { list-style: none; width: 100%; display: flex; flex-direction: column; gap: var(--s-3); margin-top: var(--s-4); }
.top__fila { display: flex; align-items: center; gap: var(--s-4); text-align: left;
  animation: sube .45s var(--ease-out) both; }
@keyframes sube { from { opacity: 0; transform: translateX(-14px) } to { opacity: 1; transform: none } }
.top__n { font-family: var(--font-display); font-size: var(--fs-2xl); color: var(--ink-ghost); width: 1.6rem; }
.top__art { width: 3.25rem; height: 4.75rem; object-fit: cover; border-radius: var(--r-sm);
  border: 1px solid var(--line); flex-shrink: 0; }
.top__txt { min-width: 0; }
.top__title { display: block; font-size: var(--fs-lg); font-weight: 600;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.top__eps { font-size: var(--fs-sm); color: var(--ink-faint); }

/* ── Ritmo ── */
.ritmo { display: flex; flex-wrap: wrap; justify-content: center; gap: var(--s-7); margin-top: var(--s-4); }
.ritmo__cifra { display: flex; flex-direction: column; gap: var(--s-1); }
.ritmo__cifra b { font-family: var(--font-display); font-size: var(--fs-3xl); line-height: 1; }
.ritmo__cifra span { font-size: var(--fs-xs); color: var(--ink-faint); max-width: 9rem; }

.reloj { display: flex; align-items: flex-end; gap: 3px; height: 3.5rem; margin-top: var(--s-6); width: 100%; }
.reloj i { flex: 1; background: var(--surface-3); border-radius: 2px; transition: height var(--t-slow) var(--ease-silk); }
.reloj i.on { background: var(--azure); box-shadow: 0 0 8px var(--azure-glow); }

.retro__puntos { position: absolute; bottom: var(--s-7); left: 50%; translate: -50% 0; z-index: 3;
  display: flex; gap: var(--s-2); }
.retro__puntos button { width: 1.75rem; height: 2px; border-radius: 2px; background: var(--surface-3);
  transition: all var(--t-base) var(--ease-silk); }
.retro__puntos button.on { background: var(--ink); width: 2.75rem; }

@media (prefers-reduced-motion: reduce) {
  .retro, .top__fila { animation: none; }
  .pag-enter-active, .pag-leave-active { transition: none; }
}
</style>
