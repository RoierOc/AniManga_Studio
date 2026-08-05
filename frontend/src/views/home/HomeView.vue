<script setup>
/* Portada — la puerta de entrada de la app.
 *
 * Antes se aterrizaba en `library` (una rejilla de manga) y ninguna vista cruzaba dominios.
 * Esto es lo aprobado en la maqueta `docs/dev/mockups/b1-portada/`: UNA pieza de arte a sangre
 * con UNA acción, y debajo una sola fila «Continuar» que mezcla anime, manga, cine y novelas.
 *
 * ⚠️ La vista «Hoy» de julio 2026 se rechazó por FEA, y era un panel de secciones apiladas. La
 * diferencia deliberada: esto no resume la app, ofrece lo siguiente que ibas a hacer.
 */
import { computed, onMounted, onBeforeUnmount, ref, watch } from 'vue'
import { useHomeStore, _hace } from '@/stores/home'
import { useUiStore } from '@/stores/ui'
import { useAnimeStore } from '@/stores/anime'
import { imgProxy } from '@/lib/img'
import { pageUrl } from '@/lib/manga'
import ContinueRail, { RAIL_W, RAIL_POSTER_W } from '@/components/media/ContinueRail.vue'
import ContextMenu from '@/components/ui/ContextMenu.vue'
import Icon from '@/components/ui/Icon.vue'
import EmptyState from '@/components/ui/EmptyState.vue'
import ErrorState from '@/components/ui/ErrorState.vue'
import Skeleton from '@/components/ui/Skeleton.vue'
import { generos } from '@/lib/etiquetas'

const store = useHomeStore()
const ui = useUiStore()

const KIND_LABEL = { anime: 'Anime', manga: 'Manga', cine: 'Cine', novela: 'Novela' }
const KIND_VERB = { anime: 'Sigues viendo', manga: 'Sigues leyendo', cine: 'Sigues viendo', novela: 'Sigues leyendo' }

const hero = computed(() => store.heroCurrent)

// Un logo que falla no puede arrastrarse al siguiente item del carrusel.
const logoRoto = ref(false)
watch(() => hero.value?.key, () => { logoRoto.value = false })

/* El arte del hero se pide a SANGRE, así que va por el proxy al mayor tamaño: `imgProxy(url, 0)`
   sirve el original. Pedirlo a 1280 y estirarlo a 2560 es exactamente lo que se ve blando. */
const heroArt = computed(() => (hero.value?.art ? imgProxy(hero.value.art, 0) : ''))

/* Un riel = una lista de items del store en la forma que entiende `ContinueRail`. Se hace UNA
   vez aquí en vez de repetir el mapeo en cada sección: los cinco rieles comparten forma, y lo
   único que cambia entre ellos es el ancho al que se pinta la miniatura. */
function aRiel(items, w) {
  return items.map(it => ({
    id: it.key,
    raw: it,
    thumb: it.poster ? imgProxy(it.poster, w) : '',
    title: it.title,
    subtitle: it.meta ? `${it.label} · ${it.meta}` : it.label,
    badge: KIND_LABEL[it.kind],
    tone: it.kind,
    progress: it.pct,
  }))
}

const rail = computed(() => store.continuarReciente.map(it => ({
  id: it.key,
  raw: it,
  // 240 = el ancho máximo real de la tarjeta en modo póster (`.is-poster .cwc`, 15rem). El
  // proxy ya duplica para pantallas HiDPI y sube al peldaño siguiente, así que pedir más sólo
  // añadiría megas que se decodifican en el hilo principal al hacer scroll.
  thumb: it.poster ? imgProxy(it.poster, RAIL_POSTER_W) : '',
  title: it.title,
  subtitle: `${it.label}${it.ts ? ` · ${_hace(it.ts)}` : ''}`,
  badge: KIND_LABEL[it.kind],
  tone: it.kind,
  progress: it.pct,
})))

/* 640 = tarjeta ancha (16:9); 240 = póster. Ver el porqué del número en `rail`. */
const railHoy = computed(() => aRiel(store.emitidoHoy, RAIL_W))
const railNuevos = computed(() => aRiel(store.capitulosNuevos, RAIL_POSTER_W))

/* «Lo dejaste a medias» lleva DOS cosas más que el resto: el tiempo (que es lo que te hace
   reconocer la obra) y las últimas páginas leídas, que se piden al pasar el ratón. */
const railMedias = computed(() => aRiel(store.dejadoAMedias, RAIL_POSTER_W).map(it => ({
  ...it,
  subtitle: `${it.raw.meta ? `${it.raw.label} · ${it.raw.meta}` : it.raw.label} · ${_hace(it.raw.ts)}`,
  recap: (store.recaps[it.raw.raw?.title] || []).map(p => pageUrl(p, 96)),
})))

/* El recordatorio se pide al ENTRAR en la tarjeta, no al cargar la portada: con 12 tarjetas
   serían 12 peticiones para algo que quizá no mires. El store memoriza por obra. */
function alEntrarMedias(item) { store.pedirRecap(item.raw) }

/* `goto` no sabe de sub-vistas (y `pushNav` con una sub rompe el atrás — regla del repo): el
   panel de Mi Anime lo elige su propio store, igual que hace `AnimeLibrary`. */
function irAAnime(sub) { useAnimeStore().sub = sub; ui.goto('anime') }

/* Clic derecho en la fila. El riel ya emitía `menu` desde el principio, pero la Portada no lo
   escuchaba: el gesto existía y no pasaba NADA — que es peor que no tenerlo, porque parece roto.
   Aquí está el único sitio donde SÍ se reproduce/lee directamente desde la Portada, y a propósito:
   el clic normal abre la ficha (decisión del usuario), así que el atajo directo necesita vivir en
   algún lado, y el gesto secundario es donde no molesta a nadie. */
const cm = ref({ open: false, x: 0, y: 0, items: [] })
const REANUDAR = { anime: 'Reproducir', cine: 'Reproducir', manga: 'Seguir leyendo', novela: 'Seguir leyendo' }

function abrirMenu({ ev, item }) {
  const it = item.raw
  cm.value = {
    open: true, x: ev.clientX, y: ev.clientY,
    items: [
      { label: `${REANUDAR[it.kind]} · ${it.label}`, icon: 'play', action: () => store.resume(it) },
      { label: 'Ver ficha', icon: 'grid', action: () => store.openDetail(it) },
    ],
  }
}

/* Rotación del hero. Se PARA al pasar el ratón por encima: el carrusel cambiándote la película
   justo cuando ibas a pulsar «Continuar» es de los detalles que hacen que una app moleste. */
const paused = ref(false)
let timer = null
function tick() {
  timer = setInterval(() => { if (!paused.value) store.nextHero(1) }, 9000)
}

onMounted(() => { store.init(); tick() })
onBeforeUnmount(() => clearInterval(timer))

function onKey(e) {
  if (e.key === 'ArrowRight') store.nextHero(1)
  else if (e.key === 'ArrowLeft') store.nextHero(-1)
}
</script>

<template>
  <div class="home" @keydown="onKey" tabindex="-1">

    <!-- ── Hero ────────────────────────────────────────────────────────── -->
    <section v-if="hero" class="hero" @mouseenter="paused = true" @mouseleave="paused = false">
      <!-- `:key` fuerza el remontaje al cambiar de item: sin él la imagen se sustituía en seco,
           sin el fundido, y el Ken Burns no volvía a empezar. -->
      <div :key="hero.key" class="hero__art" :style="{ backgroundImage: `url(${heroArt})` }" />
      <div class="hero__aura" />
      <div class="hero__scrim" />

      <div class="hero__body">
        <span class="hero__over">
          <span class="pip" />{{ KIND_VERB[hero.kind] }}
          <em v-if="hero.kind !== 'anime'">· {{ KIND_LABEL[hero.kind] }}</em>
        </span>

        <!-- Title treatment: el PNG de TMDB, igual que en Mi Anime — es lo que le da carácter al
             banner. Si no hay logo o la URL falla, cae al título en texto. `logoRoto` se resetea
             al cambiar de item o un fallo se arrastraría al siguiente.
             Es también un enlace a la ficha: el título es el sitio donde uno pincha por instinto,
             y hasta ahora era lo único del hero que no llevaba a ninguna parte. -->
        <button class="hero__name" @click="store.openDetail(hero)" :data-tip="`Ver ${hero.title}`">
          <img v-if="hero.logo && !logoRoto" class="hero__logo" :src="imgProxy(hero.logo, 0)"
               :alt="hero.title" @error="logoRoto = true" />
          <h1 v-else class="hero__title">{{ hero.title }}</h1>
        </button>

        <div class="hero__meta">
          <span v-if="hero.meta">{{ hero.meta }}</span>
          <span v-if="hero.genres?.length" class="dot" />
          <span v-if="hero.genres?.length">{{ generos(hero.genres, 3).join(' · ') }}</span>
        </div>

        <p v-if="hero.synopsis" class="hero__syn">{{ hero.synopsis }}</p>

        <div v-if="hero.pct" class="hero__resume">
          <div class="hero__bar"><i :style="{ width: hero.pct + '%' }" /></div>
          <span>
            {{ hero.label }}
            <template v-if="hero.restante"> · te quedan {{ hero.restante }} min</template>
          </span>
        </div>

        <!-- «Continuar» abre la FICHA, no reproduce (decisión del usuario, 31-jul). Por eso ya no
             hay un segundo botón «Ver ficha»: haría exactamente lo mismo. -->
        <div class="hero__acts">
          <button class="btn btn--play" @click="store.openDetail(hero)">
            <Icon name="play" :size="17" /> Continuar · {{ hero.label }}
          </button>
        </div>
      </div>

      <div v-if="store.heroItems.length > 1" class="hero__dots">
        <button v-for="(it, i) in store.heroItems" :key="it.key"
                :class="{ on: i === store.heroIndex }"
                :data-tip="it.title" :aria-label="it.title"
                @click="store.heroIndex = i" />
      </div>
    </section>

    <!-- Esqueleto con la FORMA del hero: sin esto la fila saltaba de arriba abajo al llegar. -->
    <Skeleton v-else-if="store.loading" class="hero hero--sk" />

    <!-- ── Fila única ──────────────────────────────────────────────────── -->
    <div class="home__rail">
      <!-- Las tarjetas también abren la ficha, no el lector/reproductor: mismo criterio que el
           hero. Nada arranca solo desde la Portada. -->
      <ContinueRail v-if="rail.length" title="Continuar" :items="rail" poster
                    :hint="rail.length > 1 ? `${rail.length} sin terminar` : ''"
                    @play="({ raw }) => store.openDetail(raw)"
                    @menu="abrirMenu" />

      <!-- ── Rieles ──────────────────────────────────────────────────────────────────────
           Ninguno se pinta vacío: `ContinueRail` ya se oculta solo cuando no hay items, así
           que la Portada de alguien que sólo lee manga sigue siendo hero + una fila.
           Todos abren la FICHA al pulsar, como «Continuar»: en la Portada no arranca nada solo. -->

      <ContinueRail :items="railHoy" title="Emitido hoy"
                    :hint="railHoy.length === 1 ? '1 episodio' : `${railHoy.length} episodios`"
                    :action="{ label: 'Ver calendario', fn: () => irAAnime('schedule') }"
                    @play="({ raw }) => store.openDetail(raw)"
                    @menu="abrirMenu" />

      <ContinueRail :items="railNuevos" title="Capítulos nuevos" poster
                    hint="en obras que ya sigues"
                    @play="({ raw }) => store.openDetail(raw)"
                    @menu="abrirMenu" />

      <!-- Si la consulta a MangaDex falla, se DICE. Un riel que desaparece en silencio se lee
           como «no tienes capítulos nuevos», que es justo lo contrario de lo que pasó. -->
      <ErrorState v-if="store.novedadesError && !railNuevos.length" class="home__err"
                  title="No se pudieron consultar los capítulos nuevos"
                  :detail="store.novedadesError"
                  @retry="store.cargarNovedades()" />

      <ContinueRail :items="railMedias" title="Lo dejaste a medias" poster dim
                    hint="sin tocar desde hace más de tres semanas"
                    @play="({ raw }) => store.openDetail(raw)"
                    @enter="alEntrarMedias"
                    @menu="abrirMenu" />

      <!-- `v-if` propio, ya no `v-else` del riel: entre medias hay cuatro rieles más, y el
           vacío de la Portada es «no has empezado NADA», no «no hay fila de continuar». -->
      <EmptyState v-if="store.isEmpty"
                  icon="home"
                  title="Aún no has empezado nada"
                  body="Cuando veas un episodio o leas un capítulo, aparecerá aquí para que puedas retomarlo de un clic.">
        <button class="btn btn--ghost" @click="ui.goto('library')">Ir a la biblioteca</button>
      </EmptyState>
    </div>

    <ContextMenu v-model:open="cm.open" :x="cm.x" :y="cm.y" :items="cm.items" />
  </div>
</template>

<style scoped>
.home { display: block; }
/* El fallo de un riel no ocupa el sitio de una fila entera: es un aviso, no una sección. */
.home__err { margin: 0 0 var(--s-8); }

/* ── Hero ──────────────────────────────────────────────────────────────── */
/* A sangre a propósito: es lo PRIMERO de la vista, así que no hay ningún «Volver» ni cabecera
   con la que descuadrarse — que fue justo el motivo por el que el hero a sangre se revirtió en
   la ficha de anime, donde vive entre el botón de volver y la rejilla de episodios. */
/* Sin márgenes negativos: MEDIDO, el contenedor de vistas (`.shell__content`) tiene padding 0,
   así que un `margin: -2rem` no "recuperaba" nada — empujaba el hero 32 px fuera del viewport y
   la página entera cogía scroll horizontal. El hero ya ocupa todo el ancho disponible por sí solo;
   quien pone el aire lateral es la fila, no él. */
.hero {
  position: relative; height: calc(100vh - var(--titlebar-h) - var(--topbar-h) - 7rem);
  min-height: 31rem; overflow: hidden;
}
.hero--sk { border-radius: 0; }
.hero__art {
  position: absolute; inset: 0; background-size: cover; background-position: center 30%;
  animation: hero-in var(--t-cine) var(--ease-silk) both, kenburns 26s var(--ease-drift) infinite alternate;
}
@keyframes hero-in { from { opacity: 0 } to { opacity: 1 } }
@keyframes kenburns {
  from { transform: scale(1.04) translate3d(0,0,0) }
  to   { transform: scale(1.13) translate3d(-1.5%,-1.5%,0) }
}
/* Dos velos: el vertical asienta el texto abajo, el horizontal deja respirar el arte a la
   derecha. El de abajo funde con la fila para que no haya un filo duro entre las dos zonas. */
.hero__scrim {
  position: absolute; inset: 0;
  background:
    linear-gradient(to top, var(--void) 2%, rgba(7,10,18,.86) 22%, rgba(7,10,18,.20) 58%, rgba(7,10,18,.42) 100%),
    linear-gradient(to right, rgba(7,10,18,.92) 0%, rgba(7,10,18,.55) 38%, transparent 72%);
}
/* Aura decorativa. NUNCA cae bajo el texto: el color de portada tiñendo la UI ya se rechazó. */
.hero__aura {
  position: absolute; right: -8%; top: -18%; width: 58%; height: 90%;
  background: radial-gradient(circle at 50% 50%, var(--azure-haze), transparent 66%);
  filter: blur(60px); pointer-events: none;
}
.hero__body {
  position: relative; height: 100%; display: flex; flex-direction: column; justify-content: flex-end;
  padding: 0 clamp(var(--s-6), 4vw, var(--s-9)) var(--s-7); max-width: 44rem;
}
.hero__over {
  display: inline-flex; align-items: center; gap: var(--s-2); margin-bottom: var(--s-3);
  font-size: var(--fs-2xs); font-weight: 600; letter-spacing: var(--tracking-caps);
  text-transform: uppercase; color: var(--azure-bright);
}
.hero__over em { font-style: normal; color: var(--ink-faint); }
.hero__over .pip { width: .4rem; height: .4rem; border-radius: 50%; background: var(--cyan); box-shadow: 0 0 10px var(--cyan); }
/* El título es un botón, pero no debe PARECERLO: sin caja, sin subrayado, alineado a la izquierda
   como el texto que sustituye. Sólo se levanta un poco al pasar por encima. */
.hero__name {
  align-self: flex-start; max-width: 100%; text-align: left;
  transition: transform var(--t-base) var(--ease-snap), filter var(--t-base) var(--ease-silk);
}
.hero__name:hover { transform: translateY(-2px); filter: brightness(1.12); }
.hero__name:active { transform: translateY(0) scale(.99); }

/* Title treatment de TMDB, con las mismas medidas que `MediaHero` en modo `bleed`: los dos heroes
   de la app tienen que verse iguales, no parecidos. La sombra lo despega del arte. */
.hero__logo {
  max-width: min(42.5rem, 88%); max-height: clamp(8.75rem, 19vw, 17.5rem);
  object-fit: contain; object-position: left bottom;
  filter: drop-shadow(0 4px 20px rgba(0,0,0,.65));
  animation: logorise .6s var(--ease-silk) both;
}
@keyframes logorise { from { opacity: 0; transform: translateY(14px) scale(.98) } to { opacity: 1; transform: none } }

.hero__title {
  font-family: var(--font-display); font-size: var(--fs-3xl); font-weight: 600;
  line-height: var(--lh-tight); letter-spacing: var(--tracking-display);
  text-shadow: 0 2px 24px rgba(0,0,0,.6);
}
.hero__meta {
  display: flex; align-items: center; flex-wrap: wrap; gap: var(--s-3);
  margin-top: var(--s-3); font-size: var(--fs-sm); color: var(--ink-soft);
}
.hero__meta .dot { width: 3px; height: 3px; border-radius: 50%; background: var(--ink-ghost); }
.hero__syn {
  margin-top: var(--s-4); font-size: var(--fs-md); line-height: 1.55; color: var(--ink-soft);
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;
}
.hero__resume { margin-top: var(--s-5); max-width: 22rem; }
.hero__bar { height: 3px; border-radius: 2px; background: rgba(255,255,255,.16); overflow: hidden; }
.hero__bar i { display: block; height: 100%; background: var(--azure); box-shadow: 0 0 10px var(--azure-glow);
  transition: width var(--t-slow) var(--ease-silk); }
.hero__resume span { display: block; margin-top: var(--s-2); font-size: var(--fs-xs); color: var(--ink-faint); }
.hero__acts { display: flex; align-items: center; gap: var(--s-3); margin-top: var(--s-5); }

.btn {
  display: inline-flex; align-items: center; gap: var(--s-2);
  padding: var(--s-3) var(--s-5); border-radius: var(--r-pill);
  font-size: var(--fs-sm); font-weight: 600; transition: all var(--t-base) var(--ease-silk);
}
.btn--play { background: var(--ink); color: var(--void); box-shadow: var(--shadow-md); }
.btn--play:hover { transform: translateY(-2px) scale(1.02); box-shadow: 0 12px 32px rgba(234,240,251,.22); }
.btn--ghost { background: rgba(255,255,255,.08); color: var(--ink); border: 1px solid var(--line-2);
  backdrop-filter: blur(10px); }
.btn--ghost:hover { background: rgba(255,255,255,.14); border-color: var(--line-strong); }

.hero__dots { position: absolute; right: clamp(var(--s-6), 4vw, var(--s-9)); bottom: var(--s-7);
  display: flex; gap: var(--s-2); }
.hero__dots button { width: 1.5rem; height: 2px; border-radius: 2px; background: rgba(255,255,255,.22);
  transition: all var(--t-base) var(--ease-silk); }
.hero__dots button:hover { background: rgba(255,255,255,.5); }
.hero__dots button.on { background: var(--ink); width: 2.5rem; }

/* La fila asoma bajo el hero e invita a bajar sin que haya que descubrirlo. El aire lateral lo
   pone ella (el hero va a sangre), con el mismo `clamp` que usa el cuerpo del hero para que
   título y tarjetas queden alineados en la misma vertical. */
.home__rail {
  position: relative; z-index: 2;
  padding: var(--s-5) clamp(var(--s-6), 4vw, var(--s-9)) var(--s-8);
}

@media (prefers-reduced-motion: reduce) {
  .hero__art, .hero__logo { animation: none; }
}
</style>
