<script setup>
/* "Cocina" — todos los componentes del sistema de diseño, en todos sus estados, a la vez.
 *
 * Existe para que los estados RAROS (vacío, cargando, error, título kilométrico, sin portada,
 * contador de cuatro cifras) se pulan aquí en vez de descubrirse en producción con un manga de
 * título imposible. No aparece en la barra lateral: se llega por Ajustes → «Cocina del diseño»,
 * o con `__stores.ui.goto('kitchen')` desde la consola.
 *
 * Regla al añadir un componente al sistema: añádelo TAMBIÉN aquí, con su caso límite.
 */
import { ref } from 'vue'
import { useTagsStore } from '@/stores/tags'
import { useUiStore } from '@/stores/ui'
import Icon, { ICON_NAMES } from '@/components/ui/Icon.vue'
import Spinner from '@/components/ui/Spinner.vue'
import ChapterProgress from '@/components/ui/ChapterProgress.vue'
import Skeleton from '@/components/ui/Skeleton.vue'
import EmptyState from '@/components/ui/EmptyState.vue'
import ErrorState from '@/components/ui/ErrorState.vue'
import Select from '@/components/ui/Select.vue'
import ContentToolbar from '@/components/ui/ContentToolbar.vue'
import DensityToggle from '@/components/ui/DensityToggle.vue'
import MediaCard from '@/components/media/MediaCard.vue'
import ContinueRail from '@/components/media/ContinueRail.vue'
import CounterpartRow from '@/components/media/CounterpartRow.vue'

const ui = useUiStore()

/* Caso límite del filtro de género: nombre kilométrico (que tiene que recortarse con puntos
   suspensivos, no ensanchar la barra) y contadores de 1 a 4 cifras. */
const KIT_GEN = [
  { value: '', label: 'Todos los géneros' },
  { value: 'Slice of Life', label: 'Recuentos de la vida y sobremesas larguísimas', hint: '1284' },
  { value: 'Action', label: 'Acción', hint: '24' },
  { value: 'Music', label: 'Música', hint: '1' },
]

const LONG = 'Shuu ni Ichido Classmate wo Kau Hanashi: Futari no Jikan, Iiwake no 5000-en'
const sel1 = ref('leyendo')
const sel2 = ref('')
const tbFilter = ref('all')
const tbSort = ref('recent')
const tbSearch = ref('')

const STATUS_OPTS = [
  { value: '', label: 'Sin estado' },
  { value: 'leyendo', label: 'Leyendo', color: 'var(--azure)' },
  { value: 'completed', label: 'Completado', color: 'var(--jade)' },
  { value: 'dropped', label: 'Abandonado', color: 'var(--coral)' },
]
const MANY = Array.from({ length: 40 }, (_, i) => ({ value: `v${i}`, label: `Opción número ${i + 1}`, hint: `${i * 7}px` }))

const CARDS = [
  { caso: 'Normal', props: { cover: '', title: 'Ao no Hako', kindLabel: 'MANGA', status: { label: 'Leyendo', color: 'var(--azure)' }, count: { done: 24, total: 158 }, tags: ['Romance', 'Deportes'] } },
  { caso: 'Título kilométrico', props: { cover: '', title: LONG, kindLabel: 'MANGA', count: { done: 7, total: 12 }, tags: ['Comedia'] } },
  { caso: 'Sin portada ni datos', props: { cover: '', title: '?' } },
  { caso: 'Con aviso "NUEVO"', props: { cover: '', title: 'Medalist', kindLabel: 'TV', flag: { tone: 'live', label: 'NUEVO' }, count: { done: 12, total: 12 }, dots: ['done', 'done', 'dl', 'missing'] } },
  { caso: 'Cuenta atrás', props: { cover: '', title: 'Serie en emisión', kindLabel: 'TV', flag: { tone: 'soft', icon: 'clock', label: 'Ep 5 · 2d 3h' }, count: { done: 4, total: 24 } } },
  { caso: 'Números grandes', props: { cover: '', title: 'One Piece', kindLabel: 'MANGA', count: { done: 1142, total: 1150 } } },
]

const RAIL = [
  { id: 1, thumb: '', title: 'Ao no Hako', subtitle: 'Episodio 12', badge: 'EP 12', progress: 64 },
  { id: 2, thumb: '', title: LONG, subtitle: 'Capítulo 7 · 12%', badge: 'CAP 7', progress: 12 },
  { id: 3, thumb: '', title: 'Sin progreso', subtitle: 'Episodio 1', badge: 'EP 1', progress: 0 },
]

/* Riel «lo dejaste a medias»: el subtítulo lleva el TIEMPO (es lo que te hace reconocer la obra)
   y el recordatorio son las últimas páginas leídas. Aquí sin arte: interesa comprobar que la
   tarjeta aguanta sin miniatura y con un título kilométrico. */
const RAIL_MEDIAS = [
  { id: 1, thumb: '', title: 'Ikoku Nikki', subtitle: 'Cap. 34 · pág. 17 de 34 · hace 22 días',
    badge: 'Manga', tone: 'manga', progress: 47, recap: ['', '', ''] },
  { id: 2, thumb: '', title: LONG, subtitle: 'Episodio 6 · hace 2 meses',
    badge: 'Anime', tone: 'anime', progress: 26, recap: [] },
]

// Etiquetas: el caso límite que importa es la obra CON etiquetas ya puestas y sugerencias
// disponibles (el estado vacío es el fácil). El título largo comprueba que no rompe la cabecera.
const tags = useTagsStore()
function demoTags(vacio) {
  tags.anime = { ...tags.anime, __demo_a: ['releer', 'para el finde'], __demo_b: ['sin prisa'] }
  tags.openPicker('anime', vacio ? '__demo_vacio' : '__demo_a', vacio ? LONG : 'Ao no Hako')
}

async function demoConfirm(danger) {
  const ok = await ui.confirm({
    title: danger ? 'Borrar archivos' : 'Quitar de la biblioteca',
    danger,
    body: danger
      ? 'Se borran 4,2 GB · 137 capítulos.\nEsto no se puede deshacer.'
      : '¿Quitar «Ao no Hako» de la biblioteca?\nLos archivos se conservan en el disco.',
    confirmLabel: danger ? 'Borrar' : 'Quitar',
  })
  ui.toast(ok ? 'Confirmado' : 'Cancelado', ok ? 'ok' : 'info')
}
</script>

<template>
  <div class="kit">
    <header class="kit__head">
      <p class="eyebrow"><span class="tick" /> SISTEMA DE DISEÑO</p>
      <h1>Cocina</h1>
      <p class="kit__sub">Cada componente en todos sus estados. Si algo se rompe con un título largo
        o una lista vacía, se ve aquí antes que en tu biblioteca.</p>
    </header>

    <section class="kit__s">
      <h2 class="kit__h">Tipografía</h2>
      <div class="kit__box">
        <h1>Título h1 — display</h1>
        <h2>Título h2</h2>
        <h3>Título h3</h3>
        <p>Cuerpo normal. La lectura larga usa <code>--lh-body</code> para respirar.</p>
        <p class="muted">Texto secundario (<code>--ink-soft</code>) y <span class="kit__ghost">terciario</span>.</p>
        <p class="jp">日本語のテキスト — Noto Sans JP</p>
        <p class="kit__mono">JetBrains Mono · 1234567890</p>
      </div>
    </section>

    <section class="kit__s">
      <h2 class="kit__h">Color</h2>
      <div class="kit__sw">
        <span v-for="c in ['--azure','--cyan','--ice','--violet','--rose','--gold','--jade','--coral','--surface','--surface-2','--surface-3']"
              :key="c" class="sw" :style="{ background: `var(${c})` }"><em>{{ c.slice(2) }}</em></span>
      </div>
    </section>

    <section class="kit__s">
      <h2 class="kit__h">Carga</h2>
      <!-- Se confundió con la página colgada: son MUESTRAS, giran para siempre a propósito. -->
      <p class="kit__note"><Icon name="spark" :size="13" /> Estas ruedas y esqueletos giran
        <strong>siempre</strong>: son las muestras del componente, no es que la página esté cargando.</p>
      <div class="kit__box kit__row">
        <span><Spinner :size="14" /> 14</span>
        <span><Spinner :size="22" /> 22 (por defecto)</span>
        <span><Spinner :size="26" /> 26</span>
        <span class="kit__dark"><Spinner :size="18" tone="light" /> tone="light"</span>
        <span><Spinner :size="18" tone="ok" /> tone="ok"</span>
      </div>
      <div class="kit__box">
        <Skeleton variant="line" width="60%" height="1rem" />
        <Skeleton variant="line" width="35%" height="0.9rem" />
        <div class="kit__skels"><Skeleton v-for="n in 4" :key="n" ratio="2 / 3" /></div>
      </div>
      <!-- Progreso de capítulo. Los casos límite son los dos de abajo: el backend NO sabe cuántas
           páginas hay hasta que resuelve el capítulo, y una fuente puede traer 300. -->
      <div class="kit__box kit__row">
        <ChapterProgress :task="{ status: 'downloading', progress: 7, total: 20 }" />
        <ChapterProgress :task="{ status: 'starting' }" />
        <ChapterProgress :task="{ status: 'downloading', progress: 288, total: 312 }" />
      </div>
    </section>

    <section class="kit__s">
      <h2 class="kit__h">Desplegable (Select)</h2>
      <div class="kit__box kit__row">
        <Select v-model="sel1" :options="STATUS_OPTS" aria-label="Con color" />
        <Select v-model="sel2" :options="[{ value: '', label: 'Vacío' }]" aria-label="Una opción" />
        <Select v-model="sel2" :options="MANY" placeholder="40 opciones (scroll)" aria-label="Muchas" />
        <Select :options="STATUS_OPTS" model-value="leyendo" disabled aria-label="Deshabilitado" />
        <!-- Con icono: dos desplegables seguidos sin él se leen igual y no se sabe cuál hace qué. -->
        <Select v-model="sel2" icon="spark" :options="MANY" aria-label="Con icono" />
      </div>
      <div class="kit__box"><Select block v-model="sel1" :options="STATUS_OPTS" aria-label="Ancho completo" /></div>
    </section>

    <section class="kit__s">
      <h2 class="kit__h">Barra de contenido</h2>
      <div class="kit__box">
        <ContentToolbar
          :filters="[{ id: 'all', label: 'Todo', n: 84 }, { id: 'r', label: 'Leyendo', n: 3, color: 'var(--azure)' },
                     { id: 'c', label: 'Completado', n: 0, color: 'var(--jade)' }]"
          :filter="tbFilter" @update:filter="tbFilter = $event"
          :sorts="[{ id: 'recent', label: 'Recientes' }, { id: 'az', label: 'A–Z' }]"
          :sort="tbSort" @update:sort="tbSort = $event"
          :genres="KIT_GEN"
          :search="tbSearch" @update:search="tbSearch = $event" />
        <p class="muted">«Completado» está oculto porque su contador es 0 — reaparece al seleccionarlo.
          El desplegable de género se pinta como un pill activo al elegir uno: al filtrar por género
          los pills se apagan, y la barra no puede parecer «sin filtro». Caso límite a la vista: un
          género de nombre largo junto a uno de una sola letra.</p>
      </div>
    </section>

    <section class="kit__s">
      <h2 class="kit__h">Etiquetas (TagPicker)</h2>
      <div class="kit__row">
        <button class="kbtn" @click="demoTags(false)">Con etiquetas y sugerencias</button>
        <button class="kbtn" @click="demoTags(true)">Vacía · título kilométrico</button>
      </div>
    </section>

    <section class="kit__s">
      <h2 class="kit__h">Densidad de la rejilla</h2>
      <div class="kit__box">
        <DensityToggle />
        <p class="muted">
          Es GLOBAL: escribe <code>--dens</code> en la raíz y las tres bibliotecas multiplican por
          él su ancho base. Cámbialo aquí y mira abajo las tarjetas — se mueven en vivo.
        </p>
      </div>
    </section>

    <section class="kit__s">
      <h2 class="kit__h">Tooltip (data-tip)</h2>
      <div class="kit__box">
        <div class="kit__row" style="display:flex">
          <button class="btn" data-tip="Texto corto"><Icon name="play" :size="14" /></button>
          <button class="btn" data-tip="Un tooltip largo de verdad, con una frase entera que explica por qué esto hace lo que hace y no cabe en una línea">Texto largo</button>
          <button class="btn" disabled data-tip="Sobre un botón DESHABILITADO: el nativo de Chrome ni se entera">Deshabilitado</button>
          <button class="btn" data-tip="Con el tabulador también sale — el `title` nativo no salía nunca">Prueba con Tab</button>
        </div>
        <p class="muted">
          Sustituye a <code>title</code>. Sale a los 380 ms, o al instante si vienes de otro
          (como en un menú). Los de sólo icono heredan <code>aria-label</code> automáticamente.
        </p>
      </div>
    </section>

    <section class="kit__s">
      <h2 class="kit__h">Tarjeta de contenido</h2>
      <div class="grid kit__cards">
        <figure v-for="c in CARDS" :key="c.caso">
          <MediaCard v-bind="c.props" />
          <figcaption>{{ c.caso }}</figcaption>
        </figure>
      </div>
    </section>

    <section class="kit__s">
      <h2 class="kit__h">Puente manga ⇄ anime</h2>
      <!-- Casos límite reales: una obra con VARIAS temporadas donde unas las tienes y otras no
           (Medalist), y una obra que no tiene contraparte — ahí la fila entera NO se pinta, ni
           título ni hueco. Con AniList caído sale el ErrorState, nunca un «no tiene anime». -->
      <div class="kit__box"><CounterpartRow :al-id="118371" from="manga" /></div>
      <div class="kit__box"><CounterpartRow :al-id="16498" from="anime" /></div>
      <p class="muted">Sin al_id o sin contraparte la sección no existe; `in_library` ausente
        (no se pudo leer la otra biblioteca) no pinta ni «La tienes» ni «Buscar».</p>
    </section>

    <section class="kit__s">
      <h2 class="kit__h">Riel</h2>
      <div class="kit__box"><ContinueRail :items="RAIL" title="Seguir viendo" /></div>
      <!-- Los añadidos de la Portada en rieles: subtítulo, enlace lateral (aparece al hover),
           modo apagado y el recordatorio de páginas. Aquí se ven con títulos kilométricos. -->
      <div class="kit__box">
        <ContinueRail :items="RAIL" title="Con contexto y enlace" poster
                      hint="6 sin terminar" :action="{ label: 'Ver todo', fn: () => {} }" />
      </div>
      <div class="kit__box">
        <ContinueRail :items="RAIL_MEDIAS" title="Apagado, con recordatorio" poster dim
                      hint="sin tocar desde hace más de tres semanas" />
      </div>
      <p class="muted">Con la lista vacía el riel no se pinta (no deja un hueco ni un título huérfano).</p>
    </section>

    <section class="kit__s">
      <h2 class="kit__h">Estados vacíos</h2>
      <div class="kit__two">
        <!-- Un estado vacío debe enseñar la SALIDA: el botón lo pinta el propio EmptyState,
             así ninguna vista se inventa su `.btn`. `is-primary` para la acción principal. -->
        <div class="kit__box"><EmptyState icon="library" title="Tu biblioteca está vacía."
          hint="Descarga capítulos desde MangaDex o tus fuentes para empezar.">
          <template #action>
            <button class="is-primary"><Icon name="spark" :size="15" /> Explorar fuentes</button>
            <button><Icon name="upload" :size="15" /> Importar CBZ</button>
          </template>
        </EmptyState></div>
        <div class="kit__box"><EmptyState icon="search" title="Sin resultados para ese filtro.">
          <template #action><button class="is-primary"><Icon name="close" :size="15" /> Quitar filtros</button></template>
        </EmptyState></div>
      </div>
    </section>

    <section class="kit__s">
      <h2 class="kit__h">Soltar archivos</h2>
      <p class="kit__note"><Icon name="spark" :size="13" /> No se puede mostrar quieto: arrastra un
        <strong>.cbz</strong> sobre la ventana (aquí mismo) y aparecerá el overlay a pantalla
        completa. Funciona en toda la app y lleva el archivo al Taller.</p>
    </section>

    <section class="kit__s">
      <h2 class="kit__h">Estado de error</h2>
      <p class="kit__note"><Icon name="spark" :size="13" /> «Falló» no es «no había nada»: si una
        carga revienta, esto es lo que se pinta — <strong>nunca</strong> el estado vacío, que
        afirmaría que el usuario no tiene contenido.</p>
      <div class="kit__two">
        <div class="kit__box"><ErrorState @retry="ui.toast('Reintentando…', 'info')" /></div>
        <div class="kit__box"><ErrorState title="No se pudo buscar."
          detail="TypeError: Failed to fetch — http://127.0.0.1:5101/api/anime/search?q=frieren"
          @retry="ui.toast('Reintentando…', 'info')" /></div>
      </div>
    </section>

    <section class="kit__s">
      <h2 class="kit__h">Avisos y confirmación</h2>
      <div class="kit__box kit__row">
        <button class="kbtn" @click="ui.toast('Guardado ✓', 'ok')">Toast ok</button>
        <button class="kbtn" @click="ui.toast('Algo ha fallado', 'error')">Toast error</button>
        <button class="kbtn" @click="ui.toast('Cuidado con esto', 'warn')">Toast warn</button>
        <button class="kbtn" @click="ui.toast('Capítulo borrado', 'info', 6000, { label: 'Deshacer', fn: () => ui.toast('Deshecho', 'ok') })">Toast con acción</button>
        <button class="kbtn" @click="demoConfirm(false)">Confirmar</button>
        <button class="kbtn kbtn--danger" @click="demoConfirm(true)">Confirmar destructivo</button>
      </div>
    </section>

    <section class="kit__s">
      <h2 class="kit__h">Iconos <span class="kit__n">{{ ICON_NAMES.length }}</span></h2>
      <div class="kit__icons">
        <span v-for="n in ICON_NAMES" :key="n" class="kicon"><Icon :name="n" :size="18" /><em>{{ n }}</em></span>
      </div>
    </section>
  </div>
</template>

<style scoped>
.kit { max-width: var(--content-max); margin: 0 auto; padding: var(--s-5) var(--s-6) var(--s-9); }
.kit__head { margin-bottom: var(--s-6); }
.kit__head h1 { font-size: var(--fs-3xl); }
.kit__sub { color: var(--ink-soft); font-size: var(--fs-sm); margin-top: var(--s-2); max-width: 56ch; }
.kit__s { margin-bottom: var(--s-7); }
.kit__h { font-family: var(--font-mono); font-size: var(--fs-2xs); letter-spacing: var(--tracking-caps);
  text-transform: uppercase; color: var(--azure); margin-bottom: var(--s-3);
  padding-bottom: var(--s-2); border-bottom: 1px solid var(--line); }
.kit__n { color: var(--ink-ghost); }
.kit__box { padding: var(--s-4); border: 1px solid var(--line); border-radius: var(--r-md);
  background: var(--surface); display: flex; flex-direction: column; gap: var(--s-3); }
.kit__row { flex-direction: row; flex-wrap: wrap; align-items: center; gap: var(--s-4); font-size: var(--fs-sm); color: var(--ink-soft); }
.kit__row > span { display: inline-flex; align-items: center; gap: var(--s-2); }
.kit__dark { padding: var(--s-2) var(--s-3); border-radius: var(--r-sm); background: #05070d; }
.kit__two { display: grid; grid-template-columns: 1fr 1fr; gap: var(--s-4); }
.kit__ghost { color: var(--ink-ghost); }
.kit__note { display: flex; align-items: center; gap: var(--s-2); margin-bottom: var(--s-3);
  padding: var(--s-2) var(--s-3); border-radius: var(--r-sm); font-size: var(--fs-xs);
  color: var(--ice); background: var(--azure-haze); border: 1px solid var(--line-2); }
.kit__note :deep(svg) { flex-shrink: 0; color: var(--azure); }
.kit__mono { font-family: var(--font-mono); }
.muted { color: var(--ink-faint); font-size: var(--fs-xs); margin-top: var(--s-2); }
code { font-family: var(--font-mono); font-size: 0.9em; color: var(--ice); }

.kit__sw { display: flex; flex-wrap: wrap; gap: var(--s-2); }
.sw { width: 6.5rem; height: 3.5rem; border-radius: var(--r-sm); border: 1px solid var(--line-2);
  display: grid; place-items: end center; padding-bottom: 4px; }
.sw em { font-style: normal; font-family: var(--font-mono); font-size: var(--fs-2xs);
  color: #fff; text-shadow: 0 1px 4px rgba(0,0,0,.9); }

.kit__skels { display: grid; grid-template-columns: repeat(4, 1fr); gap: var(--s-3); }
.kit__cards { --card-min: 11rem; gap: var(--s-5); }
.kit__cards figcaption { margin-top: var(--s-2); font-size: var(--fs-2xs); color: var(--ink-faint); text-align: center; }

.kit__icons { display: grid; grid-template-columns: repeat(auto-fill, minmax(6rem, 1fr)); gap: var(--s-3); }
.kicon { display: flex; flex-direction: column; align-items: center; gap: 4px; padding: var(--s-3);
  border: 1px solid var(--line); border-radius: var(--r-sm); color: var(--ink-soft); }
.kicon em { font-style: normal; font-family: var(--font-mono); font-size: var(--fs-2xs); color: var(--ink-ghost); }

.kbtn { padding: var(--s-2) var(--s-4); border-radius: var(--r-md); font-size: var(--fs-sm);
  font-weight: 600; color: var(--ink-soft); border: 1px solid var(--line-2); transition: all var(--t-fast); }
.kbtn:hover { color: var(--ink); border-color: var(--azure); }
.kbtn--danger:hover { color: var(--coral); border-color: var(--coral); }

@media (max-width: 720px) { .kit__two, .kit__skels { grid-template-columns: 1fr 1fr; } }
</style>
