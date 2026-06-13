<script setup>
import { ref, computed, onUnmounted, watch } from 'vue'
import { useAnimeStore } from '@/stores/anime'
import { animeFormatLabel } from '@/lib/anime'
import Icon from '@/components/ui/Icon.vue'

const store = useAnimeStore()
const active = ref(0)
const items = computed(() => store.heroItems)
const c = computed(() => items.value[active.value] || null)

let timer = null
function startTimer() { clearInterval(timer); if (items.value.length > 1) timer = setInterval(() => goTo(active.value + 1), 8000) }
watch(items, () => startTimer(), { immediate: true })
onUnmounted(() => { clearInterval(timer) })
function goTo(i) { active.value = ((i % items.value.length) + items.value.length) % items.value.length }

const genres = computed(() => (Array.isArray(c.value?.anime?.genres) ? c.value.anime.genres.slice(0, 4) : []))
const bgUrl = computed(() => c.value?.anime?.banner || c.value?.anime?.cover || '')
const progress = computed(() => {
  if (!c.value?.ep?.resume_pos || !c.value?.ep?.duration) return 0
  return Math.min(100, (c.value.ep.resume_pos / c.value.ep.duration) * 100)
})
const epLabel = computed(() => {
  if (!c.value) return ''
  if (c.value.kind === 'seasonal') {
    const na = store.nextAiring[c.value.anime.al_id || c.value.anime.id]
    return na ? `Ep ${na.episode} · Próximamente` : 'Próximamente'
  }
  return `Episodio ${c.value.ep.num}`
})
</script>

<template>
  <section v-if="items.length" class="hero">
    <!-- Subtle aura from cover (not full-bleed image — just a glow) -->
    <div class="hero__aura">
      <Transition name="hero-bg" mode="out-in">
        <div v-if="c" :key="c.anime.id" class="hero__aura-img"
             :style="{ backgroundImage: `url('${bgUrl}')` }" />
      </Transition>
    </div>

    <!-- Content -->
    <div class="hero__body">
      <Transition name="hero-content" mode="out-in" :duration="400">
        <div v-if="c" :key="c.anime.id" class="hero__row">
          <!-- Poster -->
          <img :src="c.anime.cover" :alt="c.anime.title" class="hero__poster" />

          <!-- Info -->
          <div class="hero__info">
            <div class="hero__chips">
              <span class="hch hch--air">Temporada</span>
              <span class="hch">{{ epLabel }}</span>
              <span v-if="c.anime.format" class="hch">{{ animeFormatLabel(c.anime.format) }}</span>
            </div>

            <h1 class="hero__title">{{ c.anime.title }}</h1>

            <div v-if="genres.length" class="hero__genres">
              <span v-for="g in genres" :key="g" class="hg">{{ g }}</span>
            </div>

            <div class="hero__btns">
              <button class="hbtn hbtn--play" @click="store.openTorrents(c.anime)">
                <Icon name="search" :size="16" /> Buscar torrents
              </button>
              <button class="hbtn" @click="store.openDetail(c.anime)">
                <Icon name="spark" :size="14" /> Info
              </button>
            </div>
          </div>
        </div>
      </Transition>
    </div>

    <!-- Nav arrows -->
    <button v-if="items.length > 1" class="hero__arr hero__arr--l" @click="goTo(active - 1)">
      <Icon name="chevron" :size="22" :style="{ transform: 'rotate(180deg)' }" />
    </button>
    <button v-if="items.length > 1" class="hero__arr hero__arr--r" @click="goTo(active + 1)">
      <Icon name="chevron" :size="22" />
    </button>

    <!-- Dots -->
    <div v-if="items.length > 1" class="hero__dots">
      <button v-for="(_, i) in items" :key="i" :class="{ 'is-on': i === active }" @click="goTo(i)" />
    </div>
  </section>
</template>

<style scoped>
.hero {
  position: relative;
  padding: var(--s-7) 0 var(--s-5);
  border-radius: var(--r-xl); overflow: hidden;
  background: transparent;
}
/* Top & bottom fades — organic blend with page */
.hero::before,
.hero::after {
  content: ''; position: absolute; left: 0; right: 0; z-index: 2; pointer-events: none;
}
.hero::before {
  top: 0; height: 60px;
  background: linear-gradient(180deg, var(--base) 0%, transparent 100%);
}
.hero::after {
  bottom: 0; height: 80px;
  background: linear-gradient(0deg, var(--base) 0%, transparent 100%);
}

/* Subtle aura */
.hero__aura {
  position: absolute; inset: 0; overflow: hidden;
}
.hero__aura-img {
  position: absolute; inset: -60px;
  background-size: cover; background-position: center 25%;
  filter: blur(40px) brightness(.3) saturate(1.5);
  opacity: .45;
}
.hero__aura::after {
  content: ''; position: absolute; inset: 0;
  background: radial-gradient(ellipse at 65% 40%, transparent 20%, var(--base) 100%);
}

/* Body */
.hero__body {
  position: relative; z-index: 1;
  max-width: var(--content-max); margin: 0 auto;
  padding: 0 var(--s-6);
}
.hero__row { display: flex; gap: var(--s-6); align-items: center; width: 100%; }

/* Poster */
.hero__poster {
  width: clamp(140px, 16vw, 200px); aspect-ratio: 2/3; object-fit: cover;
  border-radius: var(--r-md); box-shadow: var(--shadow-lg);
  border: 1px solid var(--line-2); flex-shrink: 0;
  transition: transform var(--t-base) var(--ease-silk);
}
.hero__row:hover .hero__poster { transform: translateY(-3px); }

/* Info */
.hero__info { min-width: 0; }
.hero__chips { display: flex; gap: var(--s-2); flex-wrap: wrap; margin-bottom: var(--s-3); }
.hch {
  font-family: var(--font-mono); font-size: var(--fs-2xs); font-weight: 600; letter-spacing: .04em;
  padding: 3px 10px; border-radius: var(--r-pill);
  color: var(--ink-soft); background: var(--surface-2); border: 1px solid var(--line-2);
}
.hch--new { background: var(--azure); color: #fff; border-color: transparent; }
.hch--air { color: var(--cyan); background: var(--cyan-glow); border-color: transparent; }

.hero__title {
  font-family: var(--font-display); font-size: clamp(1.5rem, 2.8vw, 2.4rem);
  font-weight: 700; line-height: 1.1; color: var(--ink);
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;
}

.hero__bar {
  margin-top: var(--s-3); width: 160px; height: 3px; border-radius: var(--r-pill);
  background: var(--surface-3); overflow: hidden;
}
.hero__bar span {
  display: block; height: 100%; background: var(--azure);
  box-shadow: 0 0 6px var(--azure-glow); transition: width .6s var(--ease-silk);
}

.hero__genres { display: flex; gap: var(--s-1); flex-wrap: wrap; margin-top: var(--s-3); }
.hg {
  font-size: var(--fs-2xs); padding: 2px 10px; border-radius: var(--r-pill);
  color: var(--ink-faint); background: var(--surface); border: 1px solid var(--line);
}

.hero__btns { display: flex; gap: var(--s-3); margin-top: var(--s-5); }
.hbtn {
  display: inline-flex; align-items: center; gap: var(--s-2);
  padding: var(--s-3) var(--s-5); border-radius: var(--r-sm);
  font-size: var(--fs-sm); font-weight: 600; color: var(--ink-soft);
  background: var(--surface-2); border: 1px solid var(--line-2); transition: all var(--t-fast);
}
.hbtn:hover { color: var(--ink); border-color: var(--line-strong); background: var(--surface-3); }
.hbtn--play { background: var(--azure); color: #fff; border-color: transparent; box-shadow: var(--shadow-sm); }
.hbtn--play:hover { background: var(--azure-bright); color: #fff; }

/* Left/Right arrows */
.hero__arr {
  position: absolute; top: 50%; transform: translateY(-50%); z-index: 2;
  width: 36px; height: 36px; display: grid; place-items: center; border-radius: 50%;
  color: var(--ink-faint); background: var(--surface); border: 1px solid var(--line);
  transition: all var(--t-fast); opacity: 0;
}
.hero:hover .hero__arr { opacity: 1; }
.hero__arr:hover { color: var(--ink); border-color: var(--line-strong); background: var(--surface-2); }
.hero__arr--l { left: var(--s-4); }
.hero__arr--r { right: var(--s-4); }

/* Dots — bottom right */
.hero__dots {
  position: absolute; bottom: var(--s-4); right: var(--s-6); z-index: 2;
  display: flex; gap: 6px;
}
.hero__dots button {
  width: 7px; height: 7px; border-radius: 50%; border: none; padding: 0; cursor: pointer;
  background: var(--line-strong); transition: all var(--t-fast);
}
.hero__dots button:hover { background: var(--ink-faint); }
.hero__dots button.is-on { background: var(--azure); box-shadow: 0 0 6px var(--azure-glow); width: 20px; border-radius: var(--r-pill); }

/* Transitions */
.hero-bg-enter-active { transition: opacity .6s var(--ease-silk); }
.hero-bg-leave-active { transition: opacity .4s var(--ease-silk); }
.hero-bg-enter-from, .hero-bg-leave-to { opacity: 0; }
.hero-content-enter-active { transition: all .4s var(--ease-silk); }
.hero-content-leave-active { transition: all .25s var(--ease-silk); }
.hero-content-enter-from { opacity: 0; transform: translateY(8px); }
.hero-content-leave-to { opacity: 0; transform: translateY(-4px); }

@media (max-width: 640px) {
  .hero__body { padding: 0 var(--s-4); }
  .hero__poster { width: 100px; }
  .hero__row { gap: var(--s-4); }
  .hero__arr { display: none; }
}
</style>
