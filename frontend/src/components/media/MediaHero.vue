<script setup>
/* Hero cinematográfico ÚNICO de la app (carrusel + Ken Burns + aura de color).
 *
 * Durante meses hubo DOS: éste y `components/anime/HeroBanner.vue` (365 líneas), con el mismo
 * markup y el mismo CSS copiado — la historia de `MangaCard`/`MediaCard` otra vez. Cada mejora
 * visual costaba el doble y las dos versiones divergieron (el logo de TMDB y la pastilla «Emitido
 * hace 2h» sólo existían en anime; el botón primario era blanco allí y azul aquí).
 * Ahora `HeroBanner` es un ENVOLTORIO que traduce el store de anime a estos datos, igual que
 * `AnimeCard` envuelve a `MediaCard`. Toda la presentación vive aquí y sólo aquí.
 *
 * Cada item: `{ id, art, artFallback, logo, overline, title, meta[], tags[], progress, actions[],
 * titleAction? }` — `titleAction` hace que el logo/título abra la ficha (opcional).
 * `meta` acepta strings o `{ text, chip?, lead? }`; las acciones son `{label, icon, primary?, run}`.
 */
import { computed, onUnmounted, ref, watch } from 'vue'
import { imgProxy } from '@/lib/img'
import Icon from '@/components/ui/Icon.vue'

const props = defineProps({
  items: { type: Array, default: () => [] },
  bleed: { type: Boolean, default: false },
})
const emit = defineEmits(['tint'])

const active = ref(0)
const c = computed(() => props.items[active.value] || null)

// Cascada de arte: si la capa ancha falla (URL rota o bloqueada), se pasa a la siguiente en vez
// de dejar el hero en blanco. `is-cover` difumina y escala el póster vertical para llenar el marco.
// `art` es SIEMPRE arte ancho (banner/fanart) y puede faltar; `artFallback` admite una URL o una
// lista de portadas verticales (anime encadena cover_xl → cover). De ahí sale `isWide`: sólo la
// capa 0 se muestra sin difuminar, así que una portada vertical nunca se estira a lo ancho.
const bgIdx = ref(0)

/* Las capas NO se piden igual:
 *  · la 0 es arte ancho y se ve NÍTIDO a pantalla completa → sin `w`, tamaño original. Se probó
 *    pedirla al peldaño más alto del proxy (900) y fue un error visible: la caja mide 1341 px CSS
 *    (más en HiDPI), así que 900 se amplía en el navegador y las portadas salen blandas. El hero
 *    es la imagen más grande y más mirada de la app; aquí los bytes no mandan.
 *  · las demás son portadas verticales que `is-cover` pinta con `blur(28px)` y escala 1,18 →
 *    pedirlas a resolución original era bajar 91 KB para desenfocarlas. 480 se ve idéntico.
 * `artUrl` es UNA función para que la precarga pida exactamente la misma url que luego se pinta:
 * sin eso, precargar calentaba una entrada de caché que el render no usaba nunca. */
const BLUR_W = 480
function artUrl(u, tier) { return tier === 0 ? imgProxy(u) : imgProxy(u, BLUR_W) }
const tiers = computed(() => [c.value?.art, ...[].concat(c.value?.artFallback || [])]
  .filter(Boolean).map(artUrl))
const bgUrl = computed(() => tiers.value[bgIdx.value] || '')
const isWide = computed(() => bgIdx.value === 0 && !!c.value?.art)
function onBgError() { if (bgIdx.value < tiers.value.length - 1) bgIdx.value++ }

// Title treatment (el logo PNG de TMDB, estilo Crunchyroll). Si no hay o la URL falla → título.
const logoFailed = ref(false)
/* El logo se pinta como mucho a `min(35rem, 82%)` — medido 716 px en un monitor de 2560. Iba SIN
   proxy: cada visita se lo pedía a TMDB a tamaño original (1,2 MB el peor de la biblioteca) para
   una caja de medio ancho. Ahora va por `/api/img`, que lo cachea en disco y lo sirve reducido
   conservando la transparencia (WebP con alfa; en JPEG el rótulo saldría sobre un cuadro negro). */
const logoUrl = computed(() => (c.value?.logo ? imgProxy(c.value.logo, 720) : ''))
const hasLogo = computed(() => !!logoUrl.value && !logoFailed.value)
watch(() => c.value?.id, () => { bgIdx.value = 0; logoFailed.value = false })

// `meta` admite string o `{text, chip, lead}`: el primero va destacado y `chip` se pinta como
// pastilla (la de «Emitido hace 2 h» del anime).
const metaItems = computed(() => (c.value?.meta || [])
  .filter(Boolean)
  .map((m, i) => (typeof m === 'string' ? { text: m, lead: i === 0 } : { lead: i === 0, ...m }))
  .map(m => ({ ...m, ...splitNum(m.text) })))

// «51 capítulos · 3 leídos · 1669 en 4K» iba entero en el mismo gris: la cifra, que es el dato,
// pesaba lo mismo que su unidad. Se separa el número inicial para poder darle peso propio.
function splitNum(text) {
  const m = /^(\d[\d.,]*)(\s+)(.+)$/.exec(String(text ?? ''))
  return m ? { num: m[1], rest: m[3] } : { num: '', rest: String(text ?? '') }
}

// Color dominante del fondo → el home lo usa como aura sutil detrás de los rieles.
// imgProxy sirve TMDB/TVDB desde /api/img (mismo origen), así que el canvas es legible.
function sampleTint(url) {
  if (!url) return
  const img = new Image()
  img.onload = () => {
    try {
      const cv = document.createElement('canvas'); cv.width = cv.height = 10
      const ctx = cv.getContext('2d', { willReadFrequently: true })
      ctx.drawImage(img, 0, 0, 10, 10)
      const d = ctx.getImageData(0, 0, 10, 10).data
      let r = 0, g = 0, b = 0, n = 0
      for (let i = 0; i < d.length; i += 4) { r += d[i]; g += d[i + 1]; b += d[i + 2]; n++ }
      emit('tint', `rgb(${r / n | 0}, ${g / n | 0}, ${b / n | 0})`)
    } catch { emit('tint', 'rgb(77, 141, 255)') }
  }
  img.onerror = () => emit('tint', 'rgb(77, 141, 255)')
  img.src = url
}
watch(bgUrl, sampleTint, { immediate: true })

function goTo(i) { const n = props.items.length; if (n) active.value = ((i % n) + n) % n }

// Precargar los vecinos: sin esto el carrusel parpadea en blanco al pasar de diapositiva.
function preload(i) {
  const n = props.items.length
  if (!n) return
  const it = props.items[((i % n) + n) % n]
  const capas = [it?.art, ...[].concat(it?.artFallback || [])].filter(Boolean)
  const urls = capas.length ? [artUrl(capas[0], capas[0] === it?.art ? 0 : 1)] : []
  if (it?.logo) urls.push(it.logo)   // el render lo pinta crudo: precargar otra url no sirve de nada
  for (const u of urls) { const im = new Image(); im.src = u }
}
watch([() => props.items, active], () => {
  if (active.value >= props.items.length) active.value = 0
  preload(active.value + 1); preload(active.value - 1)
}, { immediate: true })

let timer = null
function startTimer() { clearInterval(timer); if (props.items.length > 1) timer = setInterval(() => goTo(active.value + 1), 8000) }
watch(() => props.items.length, () => {
  if (active.value >= props.items.length) active.value = 0
  startTimer()
}, { immediate: true })
onUnmounted(() => clearInterval(timer))
</script>

<template>
  <section v-if="items.length" class="hero" :class="{ 'is-cover': !isWide, 'is-bleed': bleed }">
    <div class="hero__bg">
      <!-- SIN `mode="out-in"`: con él la diapositiva vieja se desvanece ENTERA antes de que entre la
           nueva, así que entre las dos se ve el fondo — un parpadeo oscuro cada 8 s. Superpuestas
           (las dos son `position:absolute`) es un fundido cruzado de verdad. -->
      <Transition name="hero-bg">
        <div v-if="c" :key="String(c.id) + bgUrl" class="hero__img">
          <div class="hero__kb" :style="{ backgroundImage: `url('${bgUrl}')` }" />
          <!-- sonda invisible: detecta una URL rota y avanza de capa -->
          <img v-if="bgUrl" :src="bgUrl" alt="" class="hero__probe" @error="onBgError" />
        </div>
      </Transition>
      <div class="hero__shade" />
      <!-- Grano + viñeta: es lo que separa un fotograma de un `background-image`. El grano rompe
           el banding de los degradados sobre arte oscuro y la viñeta cierra los bordes. -->
      <div class="hero__grain" />
    </div>

    <div class="hero__inner">
      <Transition name="hero-content" mode="out-in" :duration="380">
        <div v-if="c" :key="c.id" class="hero__body">
          <p v-if="c.overline" class="hero__eyebrow"><span class="hero__tick" /> {{ c.overline }}</p>
          <!-- El título es donde uno pincha por instinto, así que abre la ficha — el mismo gesto
               que la Portada. `titleAction` es OPCIONAL a propósito: sin ella se pinta un `div`
               y no un botón muerto, que es peor que no tener el gesto (parece roto). -->
          <component :is="c.titleAction ? 'button' : 'div'" class="hero__name"
                     :data-tip="c.titleAction ? `Ver ${c.title}` : null"
                     @click="c.titleAction && c.titleAction()">
            <img v-if="hasLogo" class="hero__logo" :src="logoUrl" :alt="c.title" @error="logoFailed = true" />
            <h1 v-else class="hero__title">{{ c.title }}</h1>
          </component>

          <div v-if="metaItems.length" class="hero__meta">
            <template v-for="(m, i) in metaItems" :key="i">
              <span v-if="i && !m.chip" class="hero__dot">·</span>
              <span :class="{ 'hero__lead': m.lead && !m.chip, 'hero__chip': m.chip }">
                <b v-if="m.num && !m.chip" class="hero__num">{{ m.num }}</b>{{ m.num && !m.chip ? m.rest : m.text }}
              </span>
            </template>
          </div>

          <div v-if="c.tags?.length" class="hero__tags">
            <span v-for="t in c.tags" :key="t" class="hg">{{ t }}</span>
          </div>

          <div v-if="c.progress" class="hero__bar"><span :style="{ width: c.progress + '%' }" /></div>

          <div v-if="c.actions?.length" class="hero__btns">
            <button v-for="(a, i) in c.actions" :key="i" class="hbtn" :class="{ 'hbtn--play': a.primary }"
                    @click="a.run">
              <Icon :name="a.icon || 'play'" :size="a.primary ? 17 : 14" /> {{ a.label }}
            </button>
          </div>
        </div>
      </Transition>
    </div>

    <button v-if="items.length > 1" class="hero__arr hero__arr--l" @click="goTo(active - 1)" aria-label="Anterior">
      <Icon name="chevron" :size="22" :style="{ transform: 'rotate(180deg)' }" />
    </button>
    <button v-if="items.length > 1" class="hero__arr hero__arr--r" @click="goTo(active + 1)" aria-label="Siguiente">
      <Icon name="chevron" :size="22" />
    </button>
    <div v-if="items.length > 1" class="hero__dots">
      <button v-for="(_, i) in items" :key="i" :class="{ 'is-on': i === active }" @click="goTo(i)"
              :aria-label="`Ir al destacado ${i + 1}`" />
    </div>
  </section>
</template>

<style scoped>
/* El alto va atado al ANCHO (58vw) para que el arte conserve su proporción, pero con tope en
   ALTO de ventana: sin él, una pantalla ancha y baja (portátil 1080p, ultrawide) daba un hero de
   856 px sobre 950 de viewport — el 90 % de la pantalla — y la primera obra de la biblioteca
   empezaba en y=1540, o sea 600 px de scroll para ver un solo título. Con el tope, la primera
   fila asoma por abajo e invita a bajar, que es lo que hace que una portada se sienta portada y
   no pantalla de bienvenida. */
.hero { position: relative; margin: 0 0 var(--s-7);
  height: min(clamp(31.25rem, 58vw, 42.5rem), 72vh);
  border-radius: var(--r-xl); overflow: hidden; background: var(--surface); }
/* El fundido cruzado necesita que la diapositiva SALIENTE siga ocupando su sitio mientras se va. */
.hero-bg-leave-active { position: absolute; inset: 0; }

/* Grano fino (turbulencia SVG, sin pedir ningún archivo) + viñeta. `overlay` mantiene el color del
   arte y sólo altera la luminancia, así que no lava los negros. */
.hero__grain { position: absolute; inset: 0; pointer-events: none;
  background-image:
    radial-gradient(120% 90% at 50% 45%, transparent 52%, rgba(0,0,0,.42) 100%),
    url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='140' height='140'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='.82' numOctaves='3' stitchTiles='stitch'/%3E%3CfeColorMatrix type='saturate' values='0'/%3E%3C/filter%3E%3Crect width='140' height='140' filter='url(%23n)' opacity='.5'/%3E%3C/svg%3E");
  background-size: cover, 140px 140px;
  mix-blend-mode: overlay; opacity: .5; }

/* A sangre: la IMAGEN se desvanece por arriba y por abajo (máscara alfa) en vez de oscurecerse
   con una capa encima → se disuelve en el fondo de página sin costura.

El desvanecido de ARRIBA es una curva, no una rampa recta, y ocupa el 20 % en vez del 8 %.
   Antes subía de alfa 0 a 1 en 59 px de 741: tan corto y tan recto que no se leía como un
   difuminado sino como una FRANJA sucia cruzando el banner. Los cuatro puntos de abajo muestrean
   una `smoothstep` (0 · .16 · .5 · .84 · 1), así que el medio no llega de golpe y no queda un
   borde definido en ningún sitio. Probado a 18/26/34 % sobre el mismo fotograma antes de fijar 20.
   Y la máscara va en LAS TRES capas, no sólo en la imagen. El velo lateral y el grano son
   hermanos suyos, no hijos, así que cortaban en seco en el borde de la caja: medido, un escalón de
   +21 de luminancia en UNA fila arriba y de -9 abajo. Como el velo oscurece el lado izquierdo,
   allí el escalón quedaba tapado y a la derecha no — por eso el corte parecía «ir de izquierda a
   derecha».
   Ponerla en el padre `.hero__bg` NO vale: `.hero__kb` tiene capa de composición propia
   (`will-change` + `animation`) y se escapa de la máscara del padre, igual que se escapa del
   `border-radius` (ver la nota de `.hero__bg`). Medido: con la máscara en el padre el escalón se
   arreglaba abajo pero arriba seguía, ahora en el lado izquierdo. Una regla, tres selectores. */
.hero.is-bleed { border-radius: 0; margin: 0; background: transparent;
  height: min(clamp(35rem, 68vw, 51.25rem), 78vh); }
.hero.is-bleed .hero__img,
.hero.is-bleed .hero__shade,
.hero.is-bleed .hero__grain {
  overflow: hidden;
  -webkit-mask-image: linear-gradient(to bottom, rgba(0,0,0,0) 0%, rgba(0,0,0,.16) 5%, rgba(0,0,0,.5) 10%,
      rgba(0,0,0,.84) 15%, #000 20%, #000 70%, transparent 100%);
          mask-image: linear-gradient(to bottom, rgba(0,0,0,0) 0%, rgba(0,0,0,.16) 5%, rgba(0,0,0,.5) 10%,
      rgba(0,0,0,.84) 15%, #000 20%, #000 70%, transparent 100%);
}
.hero.is-bleed .hero__shade { background: linear-gradient(90deg, rgba(7,10,18,.82) 0%, rgba(7,10,18,.34) 34%, transparent 64%); }
.hero.is-bleed .hero__inner { max-width: none; margin: 0; padding: var(--s-6) var(--alib-pad, var(--s-6)) var(--s-8); }

/* Recorte PROPIO: `overflow:hidden`+`border-radius` en `.hero` NO recorta a `.hero__kb` porque
   éste tiene su propia capa de composición (`will-change`+`animation`), y Chrome no aplica el
   radio del padre a las capas compuestas → la imagen escalada se salía por las esquinas. Que la
   capa que CONTIENE al elemento animado herede el radio y recorte ella misma. */
.hero__bg { position: absolute; inset: 0; border-radius: inherit; overflow: hidden; }
.hero__img { position: absolute; inset: 0; }
/* El Ken Burns vive en esta capa interior, NUNCA en el elemento de la <Transition>: una animación
   infinita ahí hace que Vue espere un animationend que no llega y el fundido se queda en 0. */
.hero__kb { position: absolute; inset: 0; background-size: cover; background-position: center 22%;
  animation: kenburns 26s ease-in-out infinite alternate; will-change: transform; }
.hero.is-cover .hero__kb { filter: blur(28px) saturate(1.15) brightness(.85); transform: scale(1.18); animation: none; }
@keyframes kenburns { from { transform: scale(1.04) translate3d(0,0,0); } to { transform: scale(1.13) translate3d(-1.5%,-1.5%,0); } }
@media (prefers-reduced-motion: reduce) { .hero__kb { animation: none; } }
.hero__probe { position: absolute; width: 1px; height: 1px; opacity: 0; pointer-events: none; }
.hero__shade { position: absolute; inset: 0;
  background:
    linear-gradient(90deg, rgba(7,10,18,.92) 0%, rgba(7,10,18,.62) 38%, rgba(7,10,18,.15) 70%, transparent 100%),
    linear-gradient(0deg, rgba(7,10,18,.95) 0%, rgba(7,10,18,.30) 32%, transparent 60%); }

.hero__inner { position: absolute; inset: 0; z-index: 1; max-width: var(--content-max); margin: 0 auto;
  display: flex; align-items: flex-end; padding: var(--s-6) var(--s-7); }
.hero__body { max-width: 38.75rem; }
.hero__eyebrow { display: inline-flex; align-items: center; gap: var(--s-2); font-family: var(--font-mono);
  font-size: var(--fs-2xs); letter-spacing: var(--tracking-caps); color: var(--cyan); margin-bottom: var(--s-3); }
.hero__tick { width: 1rem; height: 1px; background: var(--cyan); box-shadow: 0 0 8px var(--cyan-glow); }
.hero__title { font-family: var(--font-display); font-weight: 700; color: #fff;
  font-size: clamp(2.4rem, 4.6vw, 4.2rem); line-height: 1.04; text-shadow: 0 2px 24px rgba(0,0,0,.6);
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
.hero__meta { display: flex; flex-wrap: wrap; align-items: center; gap: var(--s-2); margin-top: var(--s-3);
  font-size: var(--fs-sm); color: var(--ice); text-shadow: 0 1px 8px rgba(0,0,0,.7); }
.hero__lead { font-weight: 600; color: #fff; }
/* La cifra en la display, tabular y un punto más grande: se lee de un vistazo sin negritas por
   todas partes. `tabular-nums` evita que el ancho baile cuando el número cambia. */
.hero__num { font-family: var(--font-display); font-weight: 700; font-size: 1.12em; color: #fff;
  font-variant-numeric: tabular-nums; letter-spacing: -.01em; margin-right: .3em; }
.hero__dot { color: var(--ink-faint); }
/* Pastilla de frescura («Emitido hace 2 h», «Descargado ayer»): dato con caducidad, por eso se
   separa del resto de la meta en vez de encadenarse con un punto. */
.hero__chip { margin-left: var(--s-1); font-family: var(--font-mono); font-size: var(--fs-2xs);
  color: var(--cyan); padding: 2px 0.5rem; border-radius: var(--r-pill); background: var(--cyan-glow); }
/* Mismas medidas que el hero de Inicio: los dos se leen como la misma pieza. */
/* Bloque a lo ANCHO del cuerpo, no `width: fit-content`: el logo se dimensiona con
   `max-width: min(35rem, 82%)`, y ese 82 % se resuelve contra ESTA caja — si encogiera al
   contenido, el porcentaje se mediría contra el propio logo y saldría un 18 % más pequeño. */
.hero__name { display: block; width: 100%; text-align: left;
  transition: transform var(--t-base) var(--ease-snap), filter var(--t-base) var(--ease-silk); }
/* El transform se aplica sobre el logo, no sobre la caja vacía de al lado. */
button.hero__name { transform-origin: left center; }
button.hero__name { cursor: pointer; }
button.hero__name:hover { transform: translateY(-2px); filter: brightness(1.12); }
button.hero__name:active { transform: translateY(0) scale(.99); }

/* Title treatment de TMDB. La sombra lo despega del arte; el `logorise` evita que aparezca seco. */
.hero__logo { max-width: min(35rem, 82%); max-height: clamp(6.875rem, 15vw, 12.5rem);
  width: auto; height: auto; object-fit: contain; object-position: left bottom;
  filter: drop-shadow(0 4px 20px rgba(0,0,0,.65)); animation: logorise .6s var(--ease-silk) both; }
@keyframes logorise { from { opacity: 0; transform: translateY(14px) scale(.98); } to { opacity: 1; transform: none; } }
.hero.is-bleed .hero__logo { max-width: min(42.5rem, 88%); max-height: clamp(8.75rem, 19vw, 17.5rem); }
.hero__tags { display: flex; flex-wrap: wrap; gap: var(--s-2); margin-top: var(--s-3); }
.hg { font-size: var(--fs-2xs); padding: 3px 0.6875rem; border-radius: var(--r-pill); color: var(--ink);
  background: rgba(255,255,255,.10); backdrop-filter: blur(6px); border: 1px solid rgba(255,255,255,.12); }
.hero__bar { margin-top: var(--s-4); height: 3px; width: min(20rem, 70%); border-radius: 2px;
  background: rgba(255,255,255,.20); overflow: hidden; }
.hero__bar span { display: block; height: 100%; background: var(--azure-bright); box-shadow: var(--glow-azure); }

/* Botonera: gana la versión de anime (primario BLANCO sobre el arte). Era la divergencia más
   visible entre los dos heroes — aquí el primario era azul y se peleaba con el aura de color de
   fondo; el blanco funciona sobre cualquier arte, que es justo lo que un hero no controla. */
.hero__btns { display: flex; flex-wrap: wrap; gap: var(--s-3); margin-top: var(--s-5); }
.hbtn { display: inline-flex; align-items: center; gap: var(--s-2); padding: var(--s-3) var(--s-5);
  border-radius: var(--r-md); font-weight: 600; font-size: var(--fs-sm); color: var(--ink);
  border: 1px solid rgba(255,255,255,.16); background: rgba(255,255,255,.12); backdrop-filter: blur(8px);
  transition: all var(--t-fast) var(--ease-silk); cursor: pointer; }
.hbtn:hover { background: rgba(255,255,255,.2); transform: translateY(-1px); }
.hbtn--play { background: #fff; color: #0b0f1a; border-color: transparent; box-shadow: var(--shadow-md); }
.hbtn--play:hover { background: #fff; color: #0b0f1a; box-shadow: var(--glow-azure); }

.hero__arr { position: absolute; top: 50%; transform: translateY(-50%); z-index: 2;
  width: 2.6rem; height: 2.6rem; display: grid; place-items: center; border-radius: 50%;
  background: rgba(7,10,18,.55); border: 1px solid rgba(255,255,255,.14); color: #fff;
  backdrop-filter: blur(6px); cursor: pointer; opacity: 0; transition: opacity var(--t-base); }
.hero:hover .hero__arr { opacity: 1; }
.hero__arr--l { left: var(--s-4); }
.hero__arr--r { right: var(--s-4); }
.hero__dots { position: absolute; bottom: var(--s-4); right: var(--s-6); z-index: 2; display: flex; gap: 0.375rem; }
.hero__dots button { width: 0.4375rem; height: 0.4375rem; border-radius: 50%; border: 0; cursor: pointer;
  background: rgba(255,255,255,.28); transition: all var(--t-fast); }
.hero__dots button.is-on { background: var(--azure-bright); box-shadow: var(--glow-azure); width: 1.25rem; border-radius: var(--r-pill); }

.hero-bg-enter-active, .hero-bg-leave-active { transition: opacity var(--t-cine) var(--ease-silk); }
.hero-bg-enter-from, .hero-bg-leave-to { opacity: 0; }
.hero-content-enter-active { transition: opacity .38s var(--ease-silk), transform .38s var(--ease-silk); }
.hero-content-leave-active { transition: opacity .2s var(--ease-silk); }
.hero-content-enter-from { opacity: 0; transform: translateY(12px); }
.hero-content-leave-to { opacity: 0; }

/* En móvil el hero no puede comerse la pantalla entera antes de ver una sola tarjeta. */
@media (max-width: 640px) {
  .hero { height: clamp(23.75rem, 72vw, 30rem); border-radius: var(--r-lg); }
  .hero__inner { padding: var(--s-5) var(--s-4); }
  .hero__arr { display: none; }
  .hero__logo { max-width: 64%; max-height: 6rem; }
}
</style>
