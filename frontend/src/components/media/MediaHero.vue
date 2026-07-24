<script setup>
/* Hero cinematográfico genérico (carrusel + Ken Burns + aura de color).
 *
 * Comparte el lenguaje visual de `components/anime/HeroBanner.vue` pero NO su lógica: aquel está
 * casado con el dominio de anime (emisión, recomendaciones, `enrichPreview`, `openTorrents`…).
 * Envolverlo habría significado pasarle una docena de callbacks — más acoplamiento, no menos.
 * Aquí todo entra como datos: cada item es `{ id, art, artFallback, overline, title, meta[],
 * tags[], progress, actions[] }` y las acciones son `{label, icon, primary?, run}`.
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
const bgIdx = ref(0)
const tiers = computed(() => [c.value?.art, c.value?.artFallback].filter(Boolean).map(imgProxy))
const bgUrl = computed(() => tiers.value[bgIdx.value] || '')
const isWide = computed(() => bgIdx.value === 0 && !!c.value?.art)
function onBgError() { if (bgIdx.value < tiers.value.length - 1) bgIdx.value++ }
watch(() => c.value?.id, () => { bgIdx.value = 0 })

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
      <Transition name="hero-bg" mode="out-in">
        <div v-if="c" :key="String(c.id) + bgUrl" class="hero__img">
          <div class="hero__kb" :style="{ backgroundImage: `url('${bgUrl}')` }" />
          <!-- sonda invisible: detecta una URL rota y avanza de capa -->
          <img v-if="bgUrl" :src="bgUrl" alt="" class="hero__probe" @error="onBgError" />
        </div>
      </Transition>
      <div class="hero__shade" />
    </div>

    <div class="hero__inner">
      <Transition name="hero-content" mode="out-in" :duration="380">
        <div v-if="c" :key="c.id" class="hero__body">
          <p v-if="c.overline" class="hero__eyebrow"><span class="hero__tick" /> {{ c.overline }}</p>
          <h1 class="hero__title">{{ c.title }}</h1>

          <div v-if="c.meta?.length" class="hero__meta">
            <template v-for="(m, i) in c.meta" :key="i">
              <span v-if="i" class="hero__dot">·</span>
              <span :class="{ 'hero__lead': i === 0 }">{{ m }}</span>
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
.hero { position: relative; margin: 0 0 var(--s-7); height: clamp(31.25rem, 58vw, 42.5rem);
  border-radius: var(--r-xl); overflow: hidden; background: var(--surface); }
/* A sangre: la IMAGEN se desvanece por arriba y abajo (máscara alfa) en vez de oscurecerse con
   una capa encima → se disuelve en el fondo de página sin costura. */
.hero.is-bleed { border-radius: 0; margin: 0; background: transparent; height: clamp(35rem, 68vw, 51.25rem); }
.hero.is-bleed .hero__img {
  overflow: hidden;
  -webkit-mask-image: linear-gradient(to bottom, transparent 0%, #000 8%, #000 70%, transparent 100%);
          mask-image: linear-gradient(to bottom, transparent 0%, #000 8%, #000 70%, transparent 100%);
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
.hero__dot { color: var(--ink-faint); }
.hero__tags { display: flex; flex-wrap: wrap; gap: var(--s-2); margin-top: var(--s-3); }
.hg { font-size: var(--fs-2xs); padding: 3px 0.6875rem; border-radius: var(--r-pill); color: var(--ink);
  background: rgba(255,255,255,.10); backdrop-filter: blur(6px); border: 1px solid rgba(255,255,255,.12); }
.hero__bar { margin-top: var(--s-4); height: 3px; width: min(20rem, 70%); border-radius: 2px;
  background: rgba(255,255,255,.20); overflow: hidden; }
.hero__bar span { display: block; height: 100%; background: var(--azure-bright); box-shadow: var(--glow-azure); }

.hero__btns { display: flex; flex-wrap: wrap; gap: var(--s-3); margin-top: var(--s-5); }
.hbtn { display: inline-flex; align-items: center; gap: var(--s-2); padding: var(--s-3) var(--s-5);
  border-radius: var(--r-pill); font-weight: 600; font-size: var(--fs-sm); color: #fff;
  border: 1px solid rgba(255,255,255,.22); background: rgba(255,255,255,.08); backdrop-filter: blur(8px);
  transition: all var(--t-fast); cursor: pointer; }
.hbtn:hover { border-color: rgba(255,255,255,.5); background: rgba(255,255,255,.16); }
.hbtn--play { background: var(--azure); border-color: transparent; }
.hbtn--play:hover { background: var(--azure-bright); box-shadow: var(--glow-azure); }

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
</style>
