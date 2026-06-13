<script setup>
import { ref, computed, onUnmounted, watch } from 'vue'
import { useAnimeStore } from '@/stores/anime'
import { animeFormatLabel, SEASON_ES } from '@/lib/anime'
import { relativeTime } from '@/lib/format'
import Icon from '@/components/ui/Icon.vue'

const store = useAnimeStore()
const active = ref(0)
const items = computed(() => store.heroItems)
const c = computed(() => items.value[active.value] || null)

// Wide hero art: prefer the cached banner (TMDB backdrop → AniList banner), then a
// blurred cover as a last resort so it still reads as a cinematic hero.
const bannerUrl = computed(() => c.value?.anime?.banner || '')
const coverUrl = computed(() => c.value?.anime?.cover_xl || c.value?.anime?.cover || '')
const hasBanner = computed(() => !!bannerUrl.value)
const bgUrl = computed(() => bannerUrl.value || coverUrl.value)

const EYEBROW = { new: 'NUEVO EPISODIO', continue: 'SIGUE VIENDO', seasonal: 'TEMPORADA' }
const eyebrow = computed(() => EYEBROW[c.value?.kind] || '')
const genres = computed(() => (Array.isArray(c.value?.anime?.genres) ? c.value.anime.genres.slice(0, 4) : []))

const epLabel = computed(() => {
  if (!c.value) return ''
  if (c.value.kind === 'seasonal') {
    const na = store.nextAiring[c.value.anime.al_id || c.value.anime.id]
    return na ? `Ep ${na.episode} · Próximamente` : 'Próximamente'
  }
  return `Episodio ${c.value.ep?.num}`
})
const seasonLabel = computed(() => {
  const a = c.value?.anime
  if (!a) return ''
  if (a.season && a.season_year) return `${SEASON_ES[a.season] || a.season} ${a.season_year}`
  return a.season_year ? String(a.season_year) : ''
})
const freshLabel = computed(() => {
  if (c.value?.kind === 'new' && c.value.ts) return `Disponible ${relativeTime(c.value.ts)}`
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
  const it = items.value[i]; const u = it?.anime?.banner || it?.anime?.cover_xl || it?.anime?.cover
  if (u) { const img = new Image(); img.src = u }
}
watch([items, active], () => {
  if (active.value >= items.value.length) active.value = 0   // clamp without jumping to 0 on silent reloads
  preload(active.value + 1); preload(active.value - 1)
}, { immediate: true })
watch(() => items.value.length, () => startTimer(), { immediate: true })
onUnmounted(() => clearInterval(timer))

function primary() {
  if (!c.value) return
  if (c.value.kind === 'seasonal') store.openTorrents(c.value.anime)
  else store.play(c.value.anime, c.value.ep)
}
function secondary() {
  if (!c.value) return
  if (c.value.kind === 'seasonal') store.addToLibrary(c.value.anime)
  else store.openDetail(c.value.anime)
}
</script>

<template>
  <section v-if="items.length" class="hero" :class="{ 'is-cover': !hasBanner }">
    <!-- Background art -->
    <div class="hero__bg">
      <Transition name="hero-bg" mode="out-in">
        <div v-if="c" :key="c.anime.id + bgUrl" class="hero__img" :style="{ backgroundImage: `url('${bgUrl}')` }" />
      </Transition>
      <div class="hero__shade" />
    </div>

    <!-- Content -->
    <div class="hero__inner">
      <Transition name="hero-content" mode="out-in" :duration="380">
        <div v-if="c" :key="c.anime.id" class="hero__body">
          <p class="hero__eyebrow"><span class="hero__tick" /> {{ eyebrow }}</p>
          <h1 class="hero__title">{{ c.anime.title }}</h1>

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
              <Icon :name="c.kind === 'seasonal' ? 'search' : 'play'" :size="17" />
              {{ c.kind === 'seasonal' ? 'Buscar torrents' : (progress ? 'Reanudar' : 'Ver episodio') }}
            </button>
            <button class="hbtn" @click="secondary">
              <Icon :name="c.kind === 'seasonal' ? 'spark' : 'spark'" :size="14" />
              {{ c.kind === 'seasonal' ? '+ Mi Anime' : 'Información' }}
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
  height: clamp(320px, 42vw, 460px);
  border-radius: var(--r-xl); overflow: hidden;
  background: var(--surface);
}

/* Background art */
.hero__bg { position: absolute; inset: 0; }
.hero__img {
  position: absolute; inset: 0; background-size: cover; background-position: center 22%;
}
/* When only a vertical cover is available, blur+scale it so it fills the wide frame. */
.hero.is-cover .hero__img { filter: blur(28px) saturate(1.15) brightness(.85); transform: scale(1.18); }
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
.hero__body { max-width: 620px; }
.hero__eyebrow {
  display: inline-flex; align-items: center; gap: var(--s-2);
  font-family: var(--font-mono); font-size: var(--fs-2xs); letter-spacing: var(--tracking-caps);
  color: var(--cyan); margin-bottom: var(--s-3);
}
.hero__tick { width: 16px; height: 1px; background: var(--cyan); box-shadow: 0 0 8px var(--cyan-glow); }
.hero__title {
  font-family: var(--font-display); font-weight: 700; color: #fff;
  font-size: clamp(1.7rem, 3.4vw, 3rem); line-height: 1.06;
  text-shadow: 0 2px 24px rgba(0,0,0,.6);
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;
}
.hero__meta {
  display: flex; flex-wrap: wrap; align-items: center; gap: var(--s-2);
  margin-top: var(--s-3); font-size: var(--fs-sm); color: var(--ice);
  text-shadow: 0 1px 8px rgba(0,0,0,.7);
}
.hero__ep { font-weight: 600; color: #fff; }
.hero__dot { color: var(--ink-faint); }
.hero__fresh {
  margin-left: var(--s-1); font-family: var(--font-mono); font-size: var(--fs-2xs);
  color: var(--cyan); padding: 2px 8px; border-radius: var(--r-pill); background: var(--cyan-glow);
}
.hero__genres { display: flex; flex-wrap: wrap; gap: var(--s-2); margin-top: var(--s-3); }
.hg {
  font-size: var(--fs-2xs); padding: 3px 11px; border-radius: var(--r-pill);
  color: var(--ink); background: rgba(255,255,255,.10); backdrop-filter: blur(6px);
  border: 1px solid rgba(255,255,255,.12);
}
.hero__bar { margin-top: var(--s-4); width: 220px; max-width: 60%; height: 4px; border-radius: var(--r-pill); background: rgba(255,255,255,.22); overflow: hidden; }
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
  width: 40px; height: 40px; display: grid; place-items: center; border-radius: 50%;
  color: #fff; background: rgba(7,10,18,.45); border: 1px solid rgba(255,255,255,.14);
  backdrop-filter: blur(6px); opacity: 0; transition: all var(--t-fast);
}
.hero:hover .hero__arr { opacity: 1; }
.hero__arr:hover { background: rgba(7,10,18,.7); }
.hero__arr--l { left: var(--s-4); }
.hero__arr--r { right: var(--s-4); }
.hero__dots { position: absolute; bottom: var(--s-4); right: var(--s-6); z-index: 2; display: flex; gap: 6px; }
.hero__dots button {
  width: 7px; height: 7px; border-radius: 50%; border: none; padding: 0; cursor: pointer;
  background: rgba(255,255,255,.35); transition: all var(--t-fast);
}
.hero__dots button:hover { background: rgba(255,255,255,.6); }
.hero__dots button.is-on { background: #fff; width: 22px; border-radius: var(--r-pill); }

/* Transitions */
.hero-bg-enter-active { transition: opacity .6s var(--ease-silk); }
.hero-bg-leave-active { transition: opacity .4s var(--ease-silk); }
.hero-bg-enter-from, .hero-bg-leave-to { opacity: 0; }
.hero-content-enter-active { transition: all .38s var(--ease-silk); }
.hero-content-leave-active { transition: all .22s var(--ease-silk); }
.hero-content-enter-from { opacity: 0; transform: translateY(12px); }
.hero-content-leave-to { opacity: 0; transform: translateY(-8px); }

@media (max-width: 640px) {
  .hero { height: clamp(280px, 56vw, 360px); border-radius: var(--r-lg); }
  .hero__inner { padding: var(--s-5) var(--s-4); }
  .hero__arr { display: none; }
}
</style>
