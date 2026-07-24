<script setup>
import { ref, computed, onUnmounted, watch } from 'vue'
import { useAnimeStore } from '@/stores/anime'
import { animeFormatLabel, SEASON_ES } from '@/lib/anime'
import { relativeTime } from '@/lib/format'
import { imgProxy } from '@/lib/img'
import Icon from '@/components/ui/Icon.vue'

// `bleed`: hero a sangre (borde a borde, sin esquinas redondeadas, funde con el fondo abajo).
defineProps({ bleed: { type: Boolean, default: false } })

const store = useAnimeStore()
const active = ref(0)
const items = computed(() => store.heroItems)
const c = computed(() => items.value[active.value] || null)

// Wide hero art: prefer the cached banner (TMDB backdrop → AniList banner), then a
// blurred cover as a last resort so it still reads as a cinematic hero. If a tier
// fails to load (broken URL, blocked domain) bgIdx advances to the next one instead
// of leaving the hero blank.
const bgIdx = ref(0)
const bgTiers = computed(() => {
  const a = c.value?.anime
  return a ? [a.banner, a.cover_xl, a.cover].filter(Boolean).map(imgProxy) : []
})
const bgUrl = computed(() => bgTiers.value[bgIdx.value] || '')
const hasBanner = computed(() => bgIdx.value === 0 && !!c.value?.anime?.banner)
function onBgError() { if (bgIdx.value < bgTiers.value.length - 1) bgIdx.value++ }

// Muestrea el color dominante del fondo actual y lo emite para que el home lo use como
// aura sutil detrás de los rieles (imgProxy sirve TMDB/AniList/MangaDex en /api/img →
// mismo origen → canvas legible; fuentes externas o error → azul del tema).
const emit = defineEmits(['tint'])
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
    } catch (_) { emit('tint', 'rgb(77, 141, 255)') }
  }
  img.onerror = () => emit('tint', 'rgb(77, 141, 255)')
  img.src = url
}
watch(bgUrl, sampleTint, { immediate: true })

// Crunchyroll-style logo title-treatment (TMDB PNG). Falls back to the text title
// when none is cached, or when the cached logo URL fails to load.
const logoFailed = ref(false)
const logoUrl = computed(() => c.value?.anime?.logo || '')
const hasLogo = computed(() => !!logoUrl.value && !logoFailed.value)
watch(() => c.value?.anime?.id, () => { bgIdx.value = 0; logoFailed.value = false })

const EYEBROW = { new: 'NUEVO EPISODIO', downloaded: 'LISTO PARA VER', continue: 'SIGUE VIENDO', seasonal: 'TEMPORADA', recommendation: 'RECOMENDADO' }
const eyebrow = computed(() => EYEBROW[c.value?.kind] || '')
const genres = computed(() => (Array.isArray(c.value?.anime?.genres) ? c.value.anime.genres.slice(0, 4) : []))

const epLabel = computed(() => {
  if (!c.value) return ''
  if (c.value.kind === 'seasonal' || c.value.kind === 'recommendation') {
    const na = store.nextAiring[c.value.anime.al_id || c.value.anime.id]
    return na ? `Ep ${na.episode} · Próximamente` : 'Próximamente'
  }
  if (c.value.kind === 'new') return `Capítulo nuevo · Ep ${c.value.ep?.num}`
  return `Episodio ${c.value.ep?.num}`
})
const seasonLabel = computed(() => {
  const a = c.value?.anime
  if (!a) return ''
  if (a.season && a.season_year) return `${SEASON_ES[a.season] || a.season} ${a.season_year}`
  return a.season_year ? String(a.season_year) : ''
})
const freshLabel = computed(() => {
  if (c.value?.kind === 'new' && c.value.aired_at) return `Emitido ${relativeTime(c.value.aired_at)}`
  if (c.value?.kind === 'downloaded' && c.value.ts) return `Descargado ${relativeTime(c.value.ts)}`
  return ''
})

// resume progress for continue-watching
const progress = computed(() => {
  const ep = c.value?.ep
  if (!ep?.resume_pos || !ep?.duration) return 0
  return Math.min(100, (ep.resume_pos / ep.duration) * 100)
})

let timer = null
function startTimer() { clearInterval(timer); if (items.value.length > 1) timer = setInterval(() => goTo(active.value + 1), 8000) }
function goTo(i) { active.value = ((i % items.value.length) + items.value.length) % items.value.length }

// Preload neighbouring banners so carousel transitions never flash.
function preload(i) {
  const it = items.value[i]
  const u = it?.anime?.banner || it?.anime?.cover_xl || it?.anime?.cover
  if (u) { const img = new Image(); img.src = u }
  if (it?.anime?.logo) { const lg = new Image(); lg.src = it.anime.logo }
}
watch([items, active], () => {
  if (active.value >= items.value.length) active.value = 0   // clamp without jumping to 0 on silent reloads
  preload(active.value + 1); preload(active.value - 1)
}, { immediate: true })

// Eagerly enrich recommendation items so the hero shows TMDB images/logo
watch(items, (list) => {
  list.forEach(it => {
    if (it.kind === 'recommendation' && it.anime?.al_id) store.enrichPreview(it.anime.al_id)
  })
}, { immediate: true })
watch(() => items.value.length, () => startTimer(), { immediate: true })
onUnmounted(() => clearInterval(timer))

// Can we play right now? (a downloaded file exists). 'new' without a file → fetch it.
const canPlay = computed(() => !['seasonal', 'recommendation'].includes(c.value?.kind) && c.value?.hasFile)
const primaryLabel = computed(() => {
  if (!c.value) return ''
  if (canPlay.value) return progress.value ? 'Reanudar' : 'Ver episodio'
  if (c.value.kind === 'recommendation') return 'Información'
  if (c.value.kind === 'new') return `Buscar Ep ${c.value.ep?.num}`
  return 'Buscar torrents'
})
const primaryIcon = computed(() => {
  if (canPlay.value) return 'play'
  if (c.value?.kind === 'recommendation') return 'spark'
  return 'search'
})

function primary() {
  if (!c.value) return
  if (canPlay.value) store.play(c.value.anime, c.value.ep)
  else if (c.value.kind === 'recommendation') store.openPreview(c.value.anime)
  else store.openTorrents(c.value.anime, c.value.kind === 'new' ? c.value.ep?.num : null)
}
function secondary() {
  if (!c.value) return
  if (c.value.kind === 'seasonal' || c.value.kind === 'recommendation') store.addToLibrary(c.value.anime)
  else store.openDetail(c.value.anime)
}
</script>

<template>
  <section v-if="items.length" class="hero" :class="{ 'is-cover': !hasBanner, 'is-bleed': bleed }">
    <!-- Background art -->
    <div class="hero__bg">
      <Transition name="hero-bg" mode="out-in">
        <div v-if="c" :key="c.anime.id + bgUrl" class="hero__img">
          <div class="hero__kb" :style="{ backgroundImage: `url('${bgUrl}')` }" />
          <!-- invisible probe: detects a broken/blocked URL and advances to the next tier -->
          <img v-if="bgUrl" :src="bgUrl" alt="" class="hero__probe" @error="onBgError" />
        </div>
      </Transition>
      <div class="hero__shade" />
    </div>

    <!-- Content -->
    <div class="hero__inner">
      <Transition name="hero-content" mode="out-in" :duration="380">
        <div v-if="c" :key="c.anime.id" class="hero__body">
          <p class="hero__eyebrow"><span class="hero__tick" /> {{ eyebrow }}</p>
          <img v-if="hasLogo" class="hero__logo" :src="logoUrl" :alt="c.anime.title" @error="logoFailed = true" />
          <h1 v-else class="hero__title">{{ c.anime.title }}</h1>

          <div class="hero__meta">
            <span class="hero__ep">{{ epLabel }}</span>
            <span v-if="seasonLabel" class="hero__dot">·</span>
            <span v-if="seasonLabel">{{ seasonLabel }}</span>
            <span class="hero__dot">·</span>
            <span>{{ animeFormatLabel(c.anime.format) }}</span>
            <span v-if="freshLabel" class="hero__fresh">{{ freshLabel }}</span>
          </div>

          <div v-if="genres.length" class="hero__genres">
            <span v-for="g in genres" :key="g" class="hg">{{ g }}</span>
          </div>

          <!-- resume bar (continue watching) -->
          <div v-if="progress" class="hero__bar"><span :style="{ width: progress + '%' }" /></div>

          <div class="hero__btns">
            <button class="hbtn hbtn--play" @click="primary">
              <Icon :name="primaryIcon" :size="17" /> {{ primaryLabel }}
            </button>
            <button class="hbtn" @click="secondary">
              <Icon name="spark" :size="14" />
              {{ ['seasonal', 'recommendation'].includes(c.kind) ? '+ Mi Anime' : 'Información' }}
            </button>
          </div>
        </div>
      </Transition>
    </div>

    <!-- Nav -->
    <button v-if="items.length > 1" class="hero__arr hero__arr--l" @click="goTo(active - 1)" aria-label="Anterior">
      <Icon name="chevron" :size="22" :style="{ transform: 'rotate(180deg)' }" />
    </button>
    <button v-if="items.length > 1" class="hero__arr hero__arr--r" @click="goTo(active + 1)" aria-label="Siguiente">
      <Icon name="chevron" :size="22" />
    </button>
    <div v-if="items.length > 1" class="hero__dots">
      <button v-for="(_, i) in items" :key="i" :class="{ 'is-on': i === active }" @click="goTo(i)" />
    </div>
  </section>
</template>

<style scoped>
.hero {
  position: relative; margin: 0 0 var(--s-7);
  height: clamp(31.25rem, 58vw, 42.5rem);
  border-radius: var(--r-xl); overflow: hidden;
  background: var(--surface);
}

/* Variante a sangre (home de anime): borde a borde, más alto, sin esquinas ni margen (lo pone
   el contenedor), y difuminado inferior FUERTE que llega al fondo de página → se funde sin costura. */
.hero.is-bleed {
  border-radius: 0; margin: 0; background: transparent;
  height: clamp(35rem, 68vw, 51.25rem);
}
/* La IMAGEN misma se desvanece (alfa) en sus bordes arriba y abajo → se disuelve en el fondo de
   página en vez de oscurecerse con una capa encima. Así "deja de ser imagen y pasa a ser el color
   de abajo". `overflow:hidden` fija el recorte para que el Ken Burns no arrastre el borde del
   degradado de la máscara. El fondo transparente deja ver el `--void` real de la página. */
.hero.is-bleed .hero__img {
  overflow: hidden;
  -webkit-mask-image: linear-gradient(to bottom, transparent 0%, #000 8%, #000 70%, transparent 100%);
          mask-image: linear-gradient(to bottom, transparent 0%, #000 8%, #000 70%, transparent 100%);
}
/* Solo un velo IZQUIERDO suave para la legibilidad del texto; el fundido arriba/abajo lo hace la
   máscara de la imagen, NO una capa oscura (que teñía toda la imagen). */
.hero.is-bleed .hero__shade {
  background: linear-gradient(90deg, rgba(7,10,18,.82) 0%, rgba(7,10,18,.34) 34%, transparent 64%);
}
/* Texto del hero alineado con el borde izquierdo de las tarjetas (mismo padding que el grid),
   en vez de centrado en un max-width — así todo cuadra a la izquierda en pantallas anchas.
   Más padding inferior para que el logo/título caigan más abajo, sobre la zona oscura. */
.hero.is-bleed .hero__inner { max-width: none; margin: 0; padding: var(--s-6) var(--alib-pad, var(--s-6)) var(--s-8); }
/* Logo del anime más grande y bajo (se extiende más hacia abajo). */
.hero.is-bleed .hero__logo { max-width: min(42.5rem, 88%); max-height: clamp(8.75rem, 19vw, 17.5rem); }

/* Background art */
.hero__bg { position: absolute; inset: 0; }
.hero__img { position: absolute; inset: 0; }
/* The Ken Burns animation lives on this inner layer, never on the <Transition> target
   (.hero__img). An infinite animation on the transition element makes Vue wait for its
   animationend — which never fires — so the opacity fade-in stays stuck at 0 and the
   banner never appears. Keeping it on a separate child lets the fade run normally. */
.hero__kb {
  position: absolute; inset: 0; background-size: cover; background-position: center 22%;
  animation: kenburns 26s ease-in-out infinite alternate;
  will-change: transform;
}
/* When only a vertical cover is available, blur+scale it so it fills the wide frame. */
.hero.is-cover .hero__kb { filter: blur(28px) saturate(1.15) brightness(.85); transform: scale(1.18); animation: none; }
@keyframes kenburns {
  from { transform: scale(1.04) translate3d(0, 0, 0); }
  to   { transform: scale(1.13) translate3d(-1.5%, -1.5%, 0); }
}
@media (prefers-reduced-motion: reduce) { .hero__kb { animation: none; } }
.hero__probe { position: absolute; width: 1px; height: 1px; opacity: 0; pointer-events: none; }
.hero__shade {
  position: absolute; inset: 0;
  background:
    linear-gradient(90deg, rgba(7,10,18,.92) 0%, rgba(7,10,18,.62) 38%, rgba(7,10,18,.15) 70%, transparent 100%),
    linear-gradient(0deg, rgba(7,10,18,.95) 0%, rgba(7,10,18,.30) 32%, transparent 60%);
}

/* Content */
.hero__inner {
  position: absolute; inset: 0; z-index: 1;
  max-width: var(--content-max); margin: 0 auto;
  display: flex; align-items: flex-end;
  padding: var(--s-6) var(--s-7) var(--s-6);
}
.hero__body { max-width: 38.75rem; }
.hero__eyebrow {
  display: inline-flex; align-items: center; gap: var(--s-2);
  font-family: var(--font-mono); font-size: var(--fs-2xs); letter-spacing: var(--tracking-caps);
  color: var(--cyan); margin-bottom: var(--s-3);
}
.hero__tick { width: 1rem; height: 1px; background: var(--cyan); box-shadow: 0 0 8px var(--cyan-glow); }
.hero__title {
  font-family: var(--font-display); font-weight: 700; color: #fff;
  font-size: clamp(2.64rem, 5.3vw, 4.8rem); line-height: 1.04;
  text-shadow: 0 2px 24px rgba(0,0,0,.6);
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;
}
/* TMDB logo title-treatment — sized like a big hero wordmark (Crunchyroll-style), shadow lifts it off the art. */
.hero__logo {
  max-width: min(35rem, 82%); max-height: clamp(6.875rem, 15vw, 12.5rem);
  width: auto; height: auto; object-fit: contain; object-position: left bottom;
  filter: drop-shadow(0 4px 20px rgba(0,0,0,.65));
  animation: logorise .6s var(--ease-silk) both;
}
@keyframes logorise { from { opacity: 0; transform: translateY(14px) scale(.98); } to { opacity: 1; transform: none; } }
.hero__meta {
  display: flex; flex-wrap: wrap; align-items: center; gap: var(--s-2);
  margin-top: var(--s-3); font-size: var(--fs-sm); color: var(--ice);
  text-shadow: 0 1px 8px rgba(0,0,0,.7);
}
.hero__ep { font-weight: 600; color: #fff; }
.hero__dot { color: var(--ink-faint); }
.hero__fresh {
  margin-left: var(--s-1); font-family: var(--font-mono); font-size: var(--fs-2xs);
  color: var(--cyan); padding: 2px 0.5rem; border-radius: var(--r-pill); background: var(--cyan-glow);
}
.hero__genres { display: flex; flex-wrap: wrap; gap: var(--s-2); margin-top: var(--s-3); }
.hg {
  font-size: var(--fs-2xs); padding: 3px 0.6875rem; border-radius: var(--r-pill);
  color: var(--ink); background: rgba(255,255,255,.10); backdrop-filter: blur(6px);
  border: 1px solid rgba(255,255,255,.12);
}
.hero__bar { margin-top: var(--s-4); width: 13.75rem; max-width: 60%; height: 4px; border-radius: var(--r-pill); background: rgba(255,255,255,.22); overflow: hidden; }
.hero__bar span { display: block; height: 100%; background: var(--azure-bright); box-shadow: 0 0 8px var(--azure-glow); }

.hero__btns { display: flex; gap: var(--s-3); margin-top: var(--s-5); flex-wrap: wrap; }
.hbtn {
  display: inline-flex; align-items: center; gap: var(--s-2);
  padding: var(--s-3) var(--s-5); border-radius: var(--r-md);
  font-size: var(--fs-sm); font-weight: 600; color: var(--ink);
  background: rgba(255,255,255,.12); border: 1px solid rgba(255,255,255,.16);
  backdrop-filter: blur(8px); transition: all var(--t-fast) var(--ease-silk);
}
.hbtn:hover { background: rgba(255,255,255,.2); transform: translateY(-1px); }
.hbtn--play { background: #fff; color: #0b0f1a; border-color: transparent; box-shadow: var(--shadow-md); }
.hbtn--play:hover { background: #fff; color: #0b0f1a; box-shadow: var(--glow-azure); }

/* Nav */
.hero__arr {
  position: absolute; top: 50%; transform: translateY(-50%); z-index: 2;
  width: 2.5rem; height: 2.5rem; display: grid; place-items: center; border-radius: 50%;
  color: #fff; background: rgba(7,10,18,.45); border: 1px solid rgba(255,255,255,.14);
  backdrop-filter: blur(6px); opacity: 0; transition: all var(--t-fast);
}
.hero:hover .hero__arr { opacity: 1; }
.hero__arr:hover { background: rgba(7,10,18,.7); }
.hero__arr--l { left: var(--s-4); }
.hero__arr--r { right: var(--s-4); }
.hero__dots { position: absolute; bottom: var(--s-4); right: var(--s-6); z-index: 2; display: flex; gap: 0.375rem; }
.hero__dots button {
  width: 0.4375rem; height: 0.4375rem; border-radius: 50%; border: none; padding: 0; cursor: pointer;
  background: rgba(255,255,255,.35); transition: all var(--t-fast);
}
.hero__dots button:hover { background: rgba(255,255,255,.6); }
.hero__dots button.is-on { background: #fff; width: 1.375rem; border-radius: var(--r-pill); }

/* Transitions */
.hero-bg-enter-active { transition: opacity .6s var(--ease-silk); }
.hero-bg-leave-active { transition: opacity .4s var(--ease-silk); }
.hero-bg-enter-from, .hero-bg-leave-to { opacity: 0; }
.hero-content-enter-active { transition: all .38s var(--ease-silk); }
.hero-content-leave-active { transition: all .22s var(--ease-silk); }
.hero-content-enter-from { opacity: 0; transform: translateY(12px); }
.hero-content-leave-to { opacity: 0; transform: translateY(-8px); }

@media (max-width: 640px) {
  .hero { height: clamp(23.75rem, 72vw, 30rem); border-radius: var(--r-lg); }
  .hero__inner { padding: var(--s-5) var(--s-4); }
  .hero__arr { display: none; }
  .hero__logo { max-width: 64%; max-height: 6rem; }
}
</style>
